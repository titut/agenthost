"""Simple Discord bridge for agenthost agents.

Forwards incoming Discord messages (DMs or guild mentions) to a running
agenthost agent's /chat endpoint and sends the agent's replies back via the
Discord bot user.

Also exposes an outbound /send endpoint so the agent can push messages to
Discord channels or users via the send_discord_message built-in tool.
"""
from __future__ import annotations

import asyncio
import json
import os
import re
import sys
from collections.abc import AsyncIterator
from typing import Any

import aiohttp
import aiohttp.web
import discord
import httpx
from discord.ext import commands
from dotenv import load_dotenv

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
# send_discord_message built-in tool.
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


async def safe_send(channel: discord.abc.Messageable, text: str) -> bool:
    """Send a message after stripping whitespace; skip if empty.

    Returns True if a message was actually sent.
    """
    text = text.strip()
    if not text:
        return False
    await channel.send(text)
    return True


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

# Track threads with in-flight agent requests.
busy_threads: set[str | int] = set()


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
        f"Your user ID is `{ctx.author.id}`."
    )


@bot.event
async def on_message(message: discord.Message) -> None:
    if message.author.bot:
        return

    # Let command handlers run first.
    await bot.process_commands(message)

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
    await process_agent_request(
        channel=message.channel,
        thread_id=thread_id,
        prompt=message.content,
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
    await process_agent_request(
        channel=message.channel,
        thread_id=thread_id,
        prompt=prompt,
        author=message.author,
    )


async def process_agent_request(
    channel: discord.abc.Messageable,
    thread_id: str,
    prompt: str,
    author: discord.User | discord.Member,
) -> None:
    """Forward a prompt to the agent and stream the reply back to Discord."""
    log("info", f"user={author} thread_id={thread_id}: {prompt[:80]}")

    if thread_id in busy_threads:
        log("warn", f"Thread {thread_id} is busy; rejecting new message")
        await channel.send("I'm still working on your last message. Please wait.")
        return

    busy_threads.add(thread_id)
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
                    args_str = ", ".join(f"{k}={v!r}" for k, v in args.items()) if args else ""
                    if await safe_send(channel, f"🔧 **Using tool:** `{name}({args_str})`"):
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
            await channel.send("I didn't get a response from the agent.")
    except Exception as exc:
        log("error", f"Failed to handle message: {exc}")
        await channel.send(f"Sorry, I couldn't process that: {exc}")
    finally:
        busy_threads.discard(thread_id)


def start_outbound_server() -> aiohttp.web.Application:
    """Create an aiohttp app that exposes POST /send for outbound messages."""
    routes = aiohttp.web.RouteTableDef()

    @routes.post("/send")
    async def send_handler(request: aiohttp.web.Request) -> aiohttp.web.Response:
        try:
            data = await request.json()
        except json.JSONDecodeError:
            return aiohttp.web.json_response({"error": "invalid json"}, status=400)

        text = data.get("text")
        thread_id = data.get("thread_id")
        if not text or not isinstance(text, str):
            return aiohttp.web.json_response({"error": "missing text"}, status=400)
        if not thread_id or not isinstance(thread_id, str):
            return aiohttp.web.json_response({"error": "missing thread_id"}, status=400)

        try:
            channel_id = int(thread_id)
        except ValueError:
            return aiohttp.web.json_response({"error": "thread_id must be a channel ID"}, status=400)

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
            chunks = split_message(text)
            sent = 0
            for chunk in chunks:
                if await safe_send(channel, chunk):
                    sent += 1
            if sent == 0:
                return aiohttp.web.json_response({"error": "empty message"}, status=400)
            log("info", f"Outbound /send to channel {channel_id}: {text[:80]}")
            return aiohttp.web.json_response({"ok": True, "thread_id": thread_id})
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
