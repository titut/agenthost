"""Simple Discord bridge for agenthost agents.

Forwards incoming Discord messages (DMs or guild mentions) to a running
agenthost agent's /chat endpoint and sends the agent's replies back via the
Discord bot user.

Also exposes an outbound /send endpoint so the agent can push messages and
optional file attachments to Discord channels or users via the send_discord
built-in tool.
"""
from __future__ import annotations

import asyncio
import json
import os
import re
import subprocess
import sys
import tempfile
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import aiohttp
import aiohttp.web
import discord
import httpx
from discord.ext import commands
from dotenv import load_dotenv

# Make the agenthost package importable when running this script directly.
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from agenthost.filesystem_tools import save_upload_with_text
from agenthost.home import get_agenthost_home

load_dotenv()

BOT_TOKEN = os.environ.get("DISCORD_BOT_TOKEN", "").strip()
AGENT_CHAT_URL = os.environ.get("AGENT_CHAT_URL", "http://127.0.0.1:8000/chat").strip()
AGENT_HEALTH_URL = os.environ.get(
    "AGENT_HEALTH_URL", AGENT_CHAT_URL.replace("/chat", "/health")
).strip()
AGENT_RETRIES = int(os.environ.get("AGENT_RETRIES", "3"))
AGENT_RETRY_DELAY = float(os.environ.get("AGENT_RETRY_DELAY", "2"))
LOG_LEVEL = os.environ.get("LOG_LEVEL", "info").lower()
MAX_MESSAGE_LENGTH = 2000

# Outbound HTTP server settings. The agent calls this via the
# send_discord built-in tool.
BRIDGE_HTTP_HOST = os.environ.get("BRIDGE_HTTP_HOST", "127.0.0.1").strip()
BRIDGE_HTTP_PORT = int(os.environ.get("BRIDGE_HTTP_PORT", "9002"), 10)

# Comma-separated list of Discord user IDs allowed to use this bot.
# If empty, all users are allowed. User IDs are numeric and stable.
ALLOWED_USER_IDS = {
    u.strip()
    for u in os.environ.get("ALLOWED_USER_IDS", "").split(",")
    if u.strip()
}

# Optional: restrict guild responses to specific guilds and/or channels.
# If empty, the bot will respond in any guild channel it can access.
ALLOWED_GUILD_IDS = {
    int(g.strip())
    for g in os.environ.get("ALLOWED_GUILD_IDS", "").split(",")
    if g.strip()
}
ALLOWED_CHANNEL_IDS = {
    int(c.strip())
    for c in os.environ.get("ALLOWED_CHANNEL_IDS", "").split(",")
    if c.strip()
}

# Guild trigger settings. The bot responds in a guild channel when either:
#   - it is mentioned, OR
#   - the message starts with this prefix (leave empty to disable prefix trigger)
GUILD_PREFIX = os.environ.get("DISCORD_GUILD_PREFIX", "").strip()

# File attachment types the bridge will extract text from and save for agents.
SUPPORTED_ATTACHMENT_TYPES = {
    ".docx",
    ".pdf",
    ".xlsx",
    ".csv",
    ".md",
    ".txt",
}

LEVELS = ["silent", "error", "warn", "info", "debug", "trace"]


def log(level: str, message: str, *args: Any) -> None:
    if LEVELS.index(level) <= LEVELS.index(LOG_LEVEL):
        import time

        prefix = f"[{time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())}] [{level.upper()}] [discord-bridge]"
        print(f"{prefix} {message}", *args, flush=True)


async def check_agent_health() -> dict[str, Any]:
    """Verify the agent is reachable before starting the bot."""
    log("info", f"Checking agent health at {AGENT_HEALTH_URL}")
    async with httpx.AsyncClient(timeout=httpx.Timeout(10.0)) as client:
        try:
            response = await client.get(AGENT_HEALTH_URL)
            response.raise_for_status()
            data = response.json()
            log("info", f"Agent healthy: {data.get('agent')} @ {data.get('model')}")
            return data
        except Exception as exc:
            log("error", f"Agent health check failed: {exc}")
            log("error", f"Make sure the agent is running and reachable at {AGENT_CHAT_URL}")
            raise


def _run_agenthost_list() -> list[dict[str, object]]:
    """Run `agenthost list` and parse its output into active agent records."""
    try:
        result = subprocess.run(
            [sys.executable, "-m", "agenthost", "list"],
            capture_output=True,
            text=True,
            check=False,
            timeout=10.0,
        )
    except Exception as exc:
        log("warn", f"`agenthost list` failed: {exc}")
        return []

    lines = result.stdout.strip().splitlines()
    agents: list[dict[str, object]] = []
    for line in lines[2:]:
        parts = line.split()
        if len(parts) >= 5:
            try:
                agents.append(
                    {
                        "name": parts[0],
                        "host": parts[1],
                        "port": int(parts[2]),
                        "pid": int(parts[3]),
                        "path": " ".join(parts[4:]),
                    }
                )
            except ValueError:
                continue
    return agents


async def clear_agents_for_thread(thread_id: str) -> tuple[int, list[str], list[str]]:
    """Call /clear on every active agent for the given thread_id."""
    active = _run_agenthost_list()
    cleared: list[str] = []
    failed: list[str] = []
    for entry in active:
        host = str(entry.get("host", "127.0.0.1"))
        port = int(entry["port"])
        name = str(entry["name"])
        try:
            async with httpx.AsyncClient() as client:
                resp = await client.post(
                    f"http://{host}:{port}/clear",
                    json={"message": "", "thread_id": thread_id},
                    timeout=5.0,
                )
                resp.raise_for_status()
                cleared.append(name)
        except Exception as exc:
            log("warn", f"Failed to clear agent '{name}' for thread '{thread_id}': {exc}")
            failed.append(name)
    return len(active), cleared, failed


async def stream_agent_events(
    thread_id: str, message: str
) -> AsyncIterator[dict[str, Any]]:
    """Send a message to the agent and yield streaming events as they arrive.

    Yields dicts with keys:
      - {"type": "content", "data": str}
      - {"type": "tool_start", "name": str, "arguments": dict}
      - {"type": "tool_result", "name": str, "result": str}
      - {"type": "tool_error", "name": str, "error": str}
      - {"type": "done"}
      - {"type": "error", "data": str}
    """
    log("debug", f"-> agent POST {AGENT_CHAT_URL}")
    log("debug", f"   thread_id={thread_id}")
    log("debug", f"   message={message}")

    last_error: Exception | None = None
    for attempt in range(1, AGENT_RETRIES + 1):
        try:
            async with httpx.AsyncClient() as client:
                async with client.stream(
                    "POST",
                    AGENT_CHAT_URL,
                    json={"message": message, "thread_id": thread_id},
                    headers={"Accept": "text/event-stream"},
                    timeout=300.0,
                ) as response:
                    log("debug", f"<- agent HTTP {response.status_code} (attempt {attempt})")
                    response.raise_for_status()

                    current_event: str | None = None
                    event_count = 0

                    async for line in response.aiter_lines():
                        line = line.strip()
                        if not line:
                            current_event = None
                            continue

                        if line.startswith("event:"):
                            current_event = line.split(":", 1)[1].strip()
                            log("trace", f"SSE event: {current_event}")
                            continue

                        if line.startswith("data:") and current_event:
                            event_count += 1
                            data = line.split(":", 1)[1].strip()
                            log("trace", f"SSE data [{current_event}]: {data}")
                            if current_event == "message":
                                try:
                                    event = json.loads(data)
                                except json.JSONDecodeError:
                                    log("debug", f"Failed to parse SSE data: {data}")
                                    continue
                                event_type = event.get("type")
                                if event_type == "content" and isinstance(event.get("data"), str):
                                    yield {"type": "content", "data": event["data"]}
                                elif event_type == "tool_start" and isinstance(event.get("data"), dict):
                                    payload = event["data"]
                                    yield {
                                        "type": "tool_start",
                                        "name": payload.get("name", "tool"),
                                        "arguments": payload.get("arguments", {}),
                                    }
                                elif event_type == "tool_result" and isinstance(event.get("data"), dict):
                                    payload = event["data"]
                                    yield {
                                        "type": "tool_result",
                                        "name": payload.get("name", "tool"),
                                        "result": payload.get("result", ""),
                                    }
                                elif event_type == "tool_error" and isinstance(event.get("data"), dict):
                                    payload = event["data"]
                                    yield {
                                        "type": "tool_error",
                                        "name": payload.get("name", "tool"),
                                        "error": payload.get("error", ""),
                                    }
                            elif current_event == "heartbeat":
                                log("trace", "Agent heartbeat received")
                            elif current_event == "done":
                                yield {"type": "done"}
                            elif current_event == "error":
                                yield {"type": "error", "data": data}

                    log("debug", f"SSE events: {event_count}")
                    return
        except Exception as exc:
            last_error = exc
            log("warn", f"Agent request attempt {attempt}/{AGENT_RETRIES} failed: {exc}")
            if attempt < AGENT_RETRIES:
                log("info", f"Retrying in {AGENT_RETRY_DELAY}s...")
                await asyncio.sleep(AGENT_RETRY_DELAY)

    raise last_error or RuntimeError("Agent request failed after retries")


def split_message(text: str, limit: int = MAX_MESSAGE_LENGTH) -> list[str]:
    """Split a long message into Discord-friendly non-empty chunks."""
    text = text.strip()
    if not text:
        return []
    if len(text) <= limit:
        return [text]

    chunks: list[str] = []
    while text:
        if len(text) <= limit:
            chunks.append(text)
            break

        # Try to split at a newline near the limit to keep formatting clean.
        cut = text.rfind("\n", 0, limit)
        if cut == -1:
            cut = limit
        chunk = text[:cut].strip()
        if chunk:
            chunks.append(chunk)
        text = text[cut:].lstrip("\n")
    return chunks


def _format_tool_args(args: dict[str, Any], max_value_len: int = 1000) -> str:
    """Format tool arguments for display, truncating long values.

    Keeps tool-start notifications readable in Discord by capping each
    argument value and the total args string.
    """
    if not args:
        return ""
    parts: list[str] = []
    for key, value in args.items():
        s = repr(value)
        if len(s) > max_value_len:
            s = s[:max_value_len].rstrip() + "..."
        parts.append(f"{key}={s}")
    args_str = ", ".join(parts)
    # Cap the full args string as well so the backticks don't explode.
    if len(args_str) > 1500:
        args_str = args_str[:1500].rstrip() + "..."
    return args_str


def truncate_message(text: str, limit: int = MAX_MESSAGE_LENGTH) -> str:
    """Truncate a message to fit in a single Discord message block.

    Keeps the text under Discord's character limit and appends a truncation
    marker when cutting. Use this for outbound tool sends where the user wants
    one message rather than a split thread.
    """
    text = text.strip()
    if not text:
        return ""
    if len(text) <= limit:
        return text

    marker = "\n\n[... truncated]"
    # Leave room for the marker so the final message stays under the limit.
    available = limit - len(marker)
    if available <= 0:
        return text[:limit]
    # Try to cut at a newline to keep formatting clean.
    cut = text.rfind("\n", 0, available)
    if cut == -1 or cut < available * 0.8:
        cut = available
    return text[:cut].rstrip() + marker


async def safe_send(channel: discord.abc.Messageable, text: str) -> bool:
    """Send a message, splitting if long, stripping whitespace, and skipping empties.

    Returns True if at least one chunk was actually sent.
    """
    chunks = split_message(text, limit=MAX_MESSAGE_LENGTH - 100)
    for chunk in chunks:
        await channel.send(chunk)
    return bool(chunks)


def clean_mentions(text: str, bot_user: discord.ClientUser) -> str:
    """Remove bot mentions from the message text before sending it to the agent."""
    # Matches both <@id> and <@!id> (nickname mention).
    pattern = re.compile(rf"<@!?{bot_user.id}>\s*")
    return pattern.sub("", text).strip()


intents = discord.Intents.default()
# Required to receive message content in DMs and guilds.
intents.message_content = True
# Required to receive direct messages.
intents.dm_messages = True
intents.messages = True

# Use '!' prefix for commands so they don't clash with Discord slash commands in guilds.
bot = commands.Bot(command_prefix="!", intents=intents)

# Track active agent request tasks per thread_id so !stop can cancel them.
active_tasks: dict[str, asyncio.Task] = {}


@bot.event
async def on_ready() -> None:
    log("info", f"Logged in as {bot.user} (id={bot.user.id})")
    log("info", f"Connected to {len(bot.guilds)} guild(s)")


@bot.command(name="id")
async def id_command(ctx: commands.Context) -> None:
    """Tell the user their Discord ID."""
    await ctx.reply(f"Your Discord user ID is `{ctx.author.id}`.")


@bot.command(name="start")
async def start_command(ctx: commands.Context) -> None:
    """Welcome / help command."""
    await ctx.reply(
        "Mention me in a server or send me a DM and I'll forward it to the agent.\n"
        f"Your user ID is `{ctx.author.id}`.\n"
        "Use `!clear` to clear the conversation memory in this channel."
    )


@bot.command(name="clear")
async def clear_command(ctx: commands.Context) -> None:
    """Clear conversation memory for all active agents in this channel."""
    thread_id = str(ctx.channel.id)
    total, cleared, failed = await clear_agents_for_thread(thread_id)
    msg = f"Cleared memory for {len(cleared)}/{total} agents in this channel."
    if failed:
        msg += f" Failed: {', '.join(failed)}."
    await ctx.reply(msg)


@bot.command(name="stop")
async def stop_command(ctx: commands.Context) -> None:
    """Cancel an in-progress agent response in this channel."""
    thread_id = str(ctx.channel.id)
    task = active_tasks.get(thread_id)
    if task is None:
        await ctx.reply("No active agent request to stop in this channel.")
        return
    task.cancel()
    await ctx.reply("⏹️ Stopping the agent...")


@bot.event
async def on_message(message: discord.Message) -> None:
    if message.author.bot:
        return

    # Let command handlers run first.
    await bot.process_commands(message)

    # If this message was a command (starts with the command prefix), do not
    # forward it to the agent.
    if message.content.startswith(bot.command_prefix):
        return

    # Access control by user ID.
    if ALLOWED_USER_IDS and str(message.author.id) not in ALLOWED_USER_IDS:
        log("warn", f"Ignoring message from unauthorized user {message.author} (id={message.author.id})")
        return

    if isinstance(message.channel, discord.DMChannel):
        await handle_dm(message)
        return

    # Guild channel handling.
    await handle_guild_message(message)


async def handle_dm(message: discord.Message) -> None:
    """Handle a direct message."""
    # Ignore messages that are just commands.
    if message.content.startswith(bot.command_prefix):
        return

    thread_id = str(message.channel.id)
    prompt = await build_prompt_with_attachments(message)
    await run_agent_request_task(
        channel=message.channel,
        thread_id=thread_id,
        prompt=prompt,
        author=message.author,
    )


async def handle_guild_message(message: discord.Message) -> None:
    """Handle a guild/server message. Responds only when mentioned or prefix is used."""
    if not message.guild:
        return

    if ALLOWED_GUILD_IDS and message.guild.id not in ALLOWED_GUILD_IDS:
        log("debug", f"Ignoring message from guild {message.guild.id} (not in allowlist)")
        return

    if ALLOWED_CHANNEL_IDS and message.channel.id not in ALLOWED_CHANNEL_IDS:
        log("debug", f"Ignoring message from channel {message.channel.id} (not in allowlist)")
        return

    mentioned = bot.user in message.mentions
    prefix_used = bool(GUILD_PREFIX and message.content.startswith(GUILD_PREFIX))

    if not mentioned and not prefix_used:
        return

    prompt = message.content
    if mentioned:
        prompt = clean_mentions(prompt, bot.user)
    elif prefix_used:
        prompt = prompt[len(GUILD_PREFIX) :].strip()

    if not prompt:
        return

    thread_id = str(message.channel.id)
    prompt = await build_prompt_with_attachments(message, base_prompt=prompt)
    await run_agent_request_task(
        channel=message.channel,
        thread_id=thread_id,
        prompt=prompt,
        author=message.author,
    )


async def build_prompt_with_attachments(
    message: discord.Message, base_prompt: str = ""
) -> str:
    """Download attachments, extract text, save to uploads/, and build a preamble.

    Returns the base_prompt prefixed with metadata about any saved uploads.
    Sends a short confirmation to Discord if attachments were processed.
    """
    if not message.attachments:
        return base_prompt

    preamble_lines: list[str] = []
    saved_count = 0

    for attachment in message.attachments:
        filename = attachment.filename
        suffix = Path(filename).suffix.lower()

        if suffix not in SUPPORTED_ATTACHMENT_TYPES:
            preamble_lines.append(f"User uploaded unsupported file: {filename}")
            continue

        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(attachment.url) as resp:
                    if resp.status != 200:
                        log(
                            "warn",
                            f"Failed to download attachment {filename}: HTTP {resp.status}",
                        )
                        preamble_lines.append(
                            f"Failed to download uploaded file: {filename}"
                        )
                        continue
                    data = await resp.read()

            with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
                tmp.write(data)
                tmp_path = Path(tmp.name)

            original_rel, text_rel = await asyncio.to_thread(
                save_upload_with_text, tmp_path, filename
            )
            tmp_path.unlink(missing_ok=True)

            preamble_lines.append(f"User uploaded: {original_rel}")
            preamble_lines.append(f"Extracted text: {text_rel}")
            saved_count += 1
            log(
                "info",
                f"Saved upload {filename} -> {original_rel}, text -> {text_rel}",
            )
        except Exception as exc:  # noqa: BLE001
            log("error", f"Failed to process attachment {filename}: {exc}")
            preamble_lines.append(f"Failed to process uploaded file {filename}: {exc}")

    if saved_count and isinstance(message.channel, discord.abc.Messageable):
        await safe_send(
            message.channel,
            f"📎 Saved {saved_count} attachment(s) to the uploads folder.",
        )

    preamble = "\n".join(preamble_lines)
    full_prompt = f"{preamble}\n\n{base_prompt}" if base_prompt else preamble
    return full_prompt


async def run_agent_request_task(
    channel: discord.abc.Messageable,
    thread_id: str,
    prompt: str,
    author: discord.User | discord.Member,
) -> None:
    """Create and track an agent request task so !stop can cancel it."""
    if thread_id in active_tasks:
        log("warn", f"Thread {thread_id} is busy; rejecting new message")
        await safe_send(channel, "I'm still working on your last message. Please wait.")
        return

    task = asyncio.create_task(
        process_agent_request(
            channel=channel,
            thread_id=thread_id,
            prompt=prompt,
            author=author,
        )
    )
    active_tasks[thread_id] = task
    try:
        await task
    except asyncio.CancelledError:
        # The !stop command cancelled the task. process_agent_request handles
        # sending the stopped confirmation.
        pass
    finally:
        active_tasks.pop(thread_id, None)


async def process_agent_request(
    channel: discord.abc.Messageable,
    thread_id: str,
    prompt: str,
    author: discord.User | discord.Member,
) -> None:
    """Forward a prompt to the agent and stream the reply back to Discord."""
    log("info", f"user={author} thread_id={thread_id}: {prompt[:80]}")
    content_buffer = ""
    sent_something = False

    async def flush_buffer(force: bool = False) -> None:
        nonlocal content_buffer, sent_something
        content_buffer = content_buffer.strip()
        if not content_buffer:
            return
        # Keep messages well under Discord's 2000-char limit to leave room for
        # any formatting the agent may produce.
        limit = MAX_MESSAGE_LENGTH - 100
        if len(content_buffer) >= limit or force:
            chunks = split_message(content_buffer, limit=limit)
            for chunk in chunks[:-1]:
                if await safe_send(channel, chunk):
                    sent_something = True
            content_buffer = chunks[-1] if chunks else ""
            if force and content_buffer:
                if await safe_send(channel, content_buffer):
                    sent_something = True
                content_buffer = ""

    try:
        async with channel.typing():
            async for event in stream_agent_events(thread_id, prompt):
                event_type = event.get("type")

                if event_type == "content":
                    content_buffer += event.get("data", "")
                    await flush_buffer()

                elif event_type == "tool_start":
                    await flush_buffer(force=True)
                    name = event.get("name", "tool")
                    args = event.get("arguments", {})
                    args_str = _format_tool_args(args)
                    tool_msg = f"🔧 **Using tool:** `{name}({args_str})`"
                    truncated = truncate_message(tool_msg, limit=MAX_MESSAGE_LENGTH - 100)
                    if truncated and await safe_send(channel, truncated):
                        sent_something = True

                elif event_type == "tool_result":
                    name = event.get("name", "tool")
                    result = str(event.get("result", ""))
                    # Only notify completion; the actual result is left for the
                    # agent's content message so it doesn't spam long tool output.
                    summary = result[:80].replace("\n", " ")
                    if len(result) > 80:
                        summary += "..."
                    if await safe_send(channel, f"✅ **Tool `{name}` finished:** {summary}"):
                        sent_something = True

                elif event_type == "tool_error":
                    await flush_buffer(force=True)
                    name = event.get("name", "tool")
                    error = str(event.get("error", ""))[:200]
                    if await safe_send(channel, f"❌ **Tool `{name}` failed:** {error}"):
                        sent_something = True

                elif event_type == "error":
                    await flush_buffer(force=True)
                    if await safe_send(channel, f"❌ **Agent error:** {event.get('data', 'unknown error')}"):
                        sent_something = True

                elif event_type == "done":
                    await flush_buffer(force=True)

        if content_buffer.strip():
            await flush_buffer(force=True)

        if not sent_something:
            log("warn", "Agent returned empty reply")
            await safe_send(channel, "I didn't get a response from the agent.")
    except asyncio.CancelledError:
        log("info", f"Agent request for thread {thread_id} was cancelled")
        await flush_buffer(force=True)
        await safe_send(channel, "⏹️ Agent stopped.")
    except Exception as exc:
        log("error", f"Failed to handle message: {exc}")
        await safe_send(channel, f"Sorry, I couldn't process that: {exc}")
    finally:
        # Nothing to clean up here; run_agent_request_task manages active_tasks.
        pass


def start_outbound_server() -> aiohttp.web.Application:
    """Create an aiohttp app that exposes POST /send for outbound messages."""
    routes = aiohttp.web.RouteTableDef()

    @routes.post("/send")
    async def send_handler(request: aiohttp.web.Request) -> aiohttp.web.Response:
        try:
            data = await request.json()
        except json.JSONDecodeError:
            return aiohttp.web.json_response({"error": "invalid json"}, status=400)

        text = data.get("text") or ""
        thread_id = data.get("thread_id")
        file_path = data.get("file_path")

        if not isinstance(text, str):
            return aiohttp.web.json_response({"error": "text must be a string"}, status=400)
        if thread_id is None:
            return aiohttp.web.json_response({"error": "missing thread_id"}, status=400)
        if isinstance(thread_id, int):
            thread_id = str(thread_id)
        if not isinstance(thread_id, str):
            return aiohttp.web.json_response({"error": "thread_id must be a string or integer"}, status=400)
        if not text and not file_path:
            return aiohttp.web.json_response(
                {"error": "must provide text or file_path"}, status=400
            )

        try:
            channel_id = int(thread_id)
        except ValueError:
            return aiohttp.web.json_response({"error": "thread_id must be a channel ID"}, status=400)

        resolved_file: Path | None = None
        if file_path:
            if not isinstance(file_path, str):
                return aiohttp.web.json_response({"error": "file_path must be a string"}, status=400)
            home = get_agenthost_home()
            target = Path(file_path)
            resolved = target.resolve() if target.is_absolute() else (home / target).resolve()
            try:
                resolved.relative_to(home)
            except ValueError:
                return aiohttp.web.json_response(
                    {"error": f"Access denied: '{file_path}' resolves outside the agenthost home directory."},
                    status=403,
                )
            if not resolved.exists():
                return aiohttp.web.json_response({"error": f"file not found: {file_path}"}, status=404)
            if not resolved.is_file():
                return aiohttp.web.json_response({"error": f"path is not a file: {file_path}"}, status=400)
            resolved_file = resolved

        try:
            channel = await bot.fetch_channel(channel_id)
        except discord.NotFound:
            return aiohttp.web.json_response({"error": "channel not found"}, status=404)
        except discord.Forbidden:
            return aiohttp.web.json_response({"error": "cannot access channel"}, status=403)
        except Exception as exc:
            log("error", f"Failed to fetch channel {channel_id}: {exc}")
            return aiohttp.web.json_response({"error": str(exc)}, status=500)

        try:
            if resolved_file is not None:
                file_obj = discord.File(str(resolved_file), filename=resolved_file.name)
                truncated = truncate_message(text, limit=MAX_MESSAGE_LENGTH - 100)
                if truncated:
                    await channel.send(truncated, file=file_obj)
                else:
                    await channel.send(file=file_obj)
                log("info", f"Outbound /send file to channel {channel_id}: {resolved_file}")
            else:
                truncated = truncate_message(text, limit=MAX_MESSAGE_LENGTH - 100)
                if not truncated:
                    return aiohttp.web.json_response({"error": "empty message"}, status=400)
                await channel.send(truncated)
                log("info", f"Outbound /send to channel {channel_id}: {text[:80]}")
            return aiohttp.web.json_response({"ok": True, "thread_id": thread_id})
        except discord.HTTPException as exc:
            detail = getattr(exc, "text", str(exc))
            log(
                "error",
                f"Failed to send Discord message to {channel_id}: HTTP {exc.status} code {exc.code}: {detail}",
            )
            return aiohttp.web.json_response(
                {"error": f"Discord API error {exc.status}: {detail}"},
                status=500,
            )
        except Exception as exc:
            log("error", f"Failed to send Discord message to {channel_id}: {exc}")
            return aiohttp.web.json_response({"error": str(exc)}, status=500)

    app = aiohttp.web.Application()
    app.add_routes(routes)
    return app


async def main() -> None:
    if not BOT_TOKEN:
        log("error", "DISCORD_BOT_TOKEN is not set")
        sys.exit(1)

    log("info", "=== agenthost Discord bridge starting ===")
    log("info", f"AGENT_CHAT_URL={AGENT_CHAT_URL}")
    log("info", f"AGENT_HEALTH_URL={AGENT_HEALTH_URL}")
    log("info", f"AGENT_RETRIES={AGENT_RETRIES}")
    log("info", f"AGENT_RETRY_DELAY={AGENT_RETRY_DELAY}")
    log("info", f"LOG_LEVEL={LOG_LEVEL}")
    log("info", f"BRIDGE_HTTP_HOST={BRIDGE_HTTP_HOST}")
    log("info", f"BRIDGE_HTTP_PORT={BRIDGE_HTTP_PORT}")
    if ALLOWED_USER_IDS:
        log("info", f"ALLOWED_USER_IDS={sorted(ALLOWED_USER_IDS)}")
    else:
        log("info", "ALLOWED_USER_IDS=<none> (open to all users)")
    if ALLOWED_GUILD_IDS:
        log("info", f"ALLOWED_GUILD_IDS={sorted(ALLOWED_GUILD_IDS)}")
    else:
        log("info", "ALLOWED_GUILD_IDS=<none> (all guilds)")
    if ALLOWED_CHANNEL_IDS:
        log("info", f"ALLOWED_CHANNEL_IDS={sorted(ALLOWED_CHANNEL_IDS)}")
    else:
        log("info", "ALLOWED_CHANNEL_IDS=<none> (all channels)")
    log("info", f"GUILD_PREFIX={GUILD_PREFIX or '<none>'}")

    await check_agent_health()

    app = start_outbound_server()
    runner = aiohttp.web.AppRunner(app)
    await runner.setup()
    site = aiohttp.web.TCPSite(runner, BRIDGE_HTTP_HOST, BRIDGE_HTTP_PORT)
    await site.start()
    log("info", f"Outbound server listening on http://{BRIDGE_HTTP_HOST}:{BRIDGE_HTTP_PORT}/send")

    try:
        await bot.start(BOT_TOKEN)
    finally:
        log("info", "Cleaning up outbound server...")
        await runner.cleanup()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        log("info", "Shutting down...")
