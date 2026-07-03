"""Simple Telegram polling bridge for agenthost agents.

Forwards incoming Telegram messages to a running agenthost agent's /chat endpoint
and sends the agent's replies back via the Telegram Bot API.
"""
from __future__ import annotations

import atexit
import json
import os
import sys
import time
from typing import Any

import httpx
from dotenv import load_dotenv

load_dotenv()

BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
AGENT_CHAT_URL = os.environ.get("AGENT_CHAT_URL", "http://127.0.0.1:8000/chat").strip()
AGENT_HEALTH_URL = os.environ.get(
    "AGENT_HEALTH_URL", AGENT_CHAT_URL.replace("/chat", "/health")
).strip()
POLL_TIMEOUT = int(os.environ.get("TELEGRAM_POLL_TIMEOUT", "30"))
AGENT_RETRIES = int(os.environ.get("AGENT_RETRIES", "3"))
AGENT_RETRY_DELAY = int(os.environ.get("AGENT_RETRY_DELAY", "2"))
LOG_LEVEL = os.environ.get("LOG_LEVEL", "info").lower()

# Comma-separated list of Telegram usernames allowed to use this bot.
# If empty, all users are allowed. Usernames are case-insensitive and should
# not include the leading @.
ALLOWED_USERNAMES = {
    u.strip().lstrip("@").lower()
    for u in os.environ.get("ALLOWED_USERNAMES", "").split(",")
    if u.strip()
}

# Optional proxy for Telegram API traffic. Useful when the host network has
# poor routing to api.telegram.org (common on some VPS providers).
# Examples:
#   TELEGRAM_PROXY=http://proxy.example.com:8080
#   TELEGRAM_PROXY=socks5://user:pass@proxy.example.com:1080
# For SOCKS5 support, install httpx with socks: pip install "httpx[socks]"
TELEGRAM_PROXY = os.environ.get("TELEGRAM_PROXY", "").strip() or None

TELEGRAM_CONNECT_TIMEOUT = float(os.environ.get("TELEGRAM_CONNECT_TIMEOUT", "30"))
TELEGRAM_READ_TIMEOUT = float(
    os.environ.get("TELEGRAM_READ_TIMEOUT", str(POLL_TIMEOUT + 30))
)

LEVELS = ["silent", "error", "warn", "info", "debug", "trace"]

# Shared client for Telegram API calls. Uses an explicit proxy/timeout so it
# doesn't pick up ambient HTTP_PROXY variables intended for other traffic.
TELEGRAM_TIMEOUT = httpx.Timeout(
    connect=TELEGRAM_CONNECT_TIMEOUT, read=TELEGRAM_READ_TIMEOUT
)
TELEGRAM_CLIENT = httpx.Client(
    proxies=TELEGRAM_PROXY,
    timeout=TELEGRAM_TIMEOUT,
    trust_env=False,
)
atexit.register(TELEGRAM_CLIENT.close)


def log(level: str, message: str, *args: Any) -> None:
    if LEVELS.index(level) <= LEVELS.index(LOG_LEVEL):
        prefix = f"[{time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())}] [{level.upper()}] [telegram-bridge]"
        print(f"{prefix} {message}", *args, flush=True)


def check_agent_health() -> dict[str, Any]:
    """Verify the agent is reachable before starting the poll loop."""
    log("info", f"Checking agent health at {AGENT_HEALTH_URL}")
    try:
        response = httpx.get(AGENT_HEALTH_URL, timeout=httpx.Timeout(connect=10.0, read=10.0))
        response.raise_for_status()
        data = response.json()
        log("info", f"Agent healthy: {data.get('agent')} @ {data.get('model')}")
        return data
    except Exception as exc:
        log("error", f"Agent health check failed: {exc}")
        log("error", f"Make sure the agent is running and reachable at {AGENT_CHAT_URL}")
        raise


def fetch_agent_reply(thread_id: str, message: str) -> str:
    """Send a message to the agent and collect the streaming text reply."""
    log("debug", f"-> agent POST {AGENT_CHAT_URL}")
    log("debug", f"   thread_id={thread_id}")
    log("debug", f"   message={message}")

    last_error: Exception | None = None
    for attempt in range(1, AGENT_RETRIES + 1):
        try:
            with httpx.stream(
                "POST",
                AGENT_CHAT_URL,
                json={"message": message, "thread_id": thread_id},
                headers={"Accept": "text/event-stream"},
                timeout=300.0,
            ) as response:
                log("debug", f"<- agent HTTP {response.status_code} (attempt {attempt})")
                response.raise_for_status()
                content_parts: list[str] = []
                current_event: str | None = None
                event_count = 0

                for line in response.iter_lines():
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
                                if event.get("type") == "content" and isinstance(event.get("data"), str):
                                    content_parts.append(event["data"])
                            except json.JSONDecodeError:
                                log("debug", f"Failed to parse SSE data: {data}")
                        elif current_event == "error":
                            raise RuntimeError(f"Agent error: {data}")

                reply = "".join(content_parts)
                log("debug", f"SSE events: {event_count}, reply length: {len(reply)}")
                return reply
        except Exception as exc:
            last_error = exc
            log("warn", f"Agent request attempt {attempt}/{AGENT_RETRIES} failed: {exc}")
            if attempt < AGENT_RETRIES:
                log("info", f"Retrying in {AGENT_RETRY_DELAY}s...")
                time.sleep(AGENT_RETRY_DELAY)

    raise last_error or RuntimeError("Agent request failed after retries")


def send_telegram_message(chat_id: int, text: str, reply_to_message_id: int | None = None) -> None:
    """Send a text message via the Telegram Bot API."""
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    payload: dict[str, Any] = {
        "chat_id": chat_id,
        "text": text,
        "parse_mode": "Markdown",
    }
    if reply_to_message_id is not None:
        payload["reply_to_message_id"] = reply_to_message_id

    log("debug", f"<- telegram sendMessage chat_id={chat_id}")
    try:
        response = TELEGRAM_CLIENT.post(url, json=payload)
        log("debug", f"-> telegram HTTP {response.status_code}")
        response.raise_for_status()
    except Exception as exc:
        log("error", f"Failed to send Telegram message: {exc}")
        raise


def is_user_allowed(message: dict[str, Any]) -> tuple[bool, str | None]:
    """Check if the sender is in the allowed username list."""
    if not ALLOWED_USERNAMES:
        return True, None

    sender = message.get("from", {})
    username = (sender.get("username") or "").lower()
    if username and username in ALLOWED_USERNAMES:
        return True, username
    return False, username


def process_update(update: dict[str, Any]) -> None:
    """Handle a single Telegram update."""
    log("debug", f"Received update: {json.dumps(update, default=str)}")

    message = update.get("message")
    if not message:
        log("debug", "Ignoring update without message")
        return

    chat = message.get("chat", {})
    chat_id = chat.get("id")
    text = message.get("text") or message.get("caption")
    message_id = message.get("message_id")

    if not chat_id or not text:
        log("debug", "Ignoring non-text message")
        return

    allowed, username = is_user_allowed(message)
    if not allowed:
        log("warn", f"Ignoring message from unauthorized user @{username or '<no username>'}")
        return

    # Handle helper commands directly.
    command = text.strip().lower()
    if command in ("/start", "/id"):
        reply = f"Your username is @{username or '<not set>'}.\nchat_id={chat_id}"
        send_telegram_message(chat_id, reply, reply_to_message_id=message_id)
        return

    thread_id = str(chat_id)
    log("info", f"chat_id={chat_id} user=@{username or '?'} -> thread_id={thread_id}: {text[:80]}")

    try:
        reply = fetch_agent_reply(thread_id, text)
        if reply.strip():
            log("info", f"reply ({len(reply)} chars): {reply[:80]}")
            send_telegram_message(chat_id, reply, reply_to_message_id=message_id)
        else:
            log("warn", "Agent returned empty reply")
            send_telegram_message(chat_id, "I didn't get a response from the agent.")
    except Exception as exc:
        log("error", f"Failed to handle message: {exc}")
        send_telegram_message(chat_id, f"Sorry, I couldn't process that: {exc}")


def run() -> None:
    if not BOT_TOKEN:
        log("error", "TELEGRAM_BOT_TOKEN is not set")
        sys.exit(1)

    log("info", "=== agenthost Telegram bridge starting ===")
    log("info", f"AGENT_CHAT_URL={AGENT_CHAT_URL}")
    log("info", f"AGENT_HEALTH_URL={AGENT_HEALTH_URL}")
    log("info", f"POLL_TIMEOUT={POLL_TIMEOUT}")
    log("info", f"AGENT_RETRIES={AGENT_RETRIES}")
    log("info", f"AGENT_RETRY_DELAY={AGENT_RETRY_DELAY}")
    log("info", f"LOG_LEVEL={LOG_LEVEL}")
    if TELEGRAM_PROXY:
        log("info", f"TELEGRAM_PROXY={TELEGRAM_PROXY}")
    else:
        log("info", "TELEGRAM_PROXY=<none>")
    log("info", f"TELEGRAM_CONNECT_TIMEOUT={TELEGRAM_CONNECT_TIMEOUT}")
    log("info", f"TELEGRAM_READ_TIMEOUT={TELEGRAM_READ_TIMEOUT}")
    if ALLOWED_USERNAMES:
        log("info", f"ALLOWED_USERNAMES={sorted(ALLOWED_USERNAMES)}")
    else:
        log("info", "ALLOWED_USERNAMES=<none> (open to all users)")

    check_agent_health()

    offset: int | None = None
    base_url = f"https://api.telegram.org/bot{BOT_TOKEN}"

    while True:
        try:
            params: dict[str, Any] = {
                "offset": offset,
                "limit": 100,
                "timeout": POLL_TIMEOUT,
            }
            log("debug", f"Polling getUpdates offset={offset}")
            response = TELEGRAM_CLIENT.get(f"{base_url}/getUpdates", params=params)
            response.raise_for_status()
            data = response.json()

            if not data.get("ok"):
                log("error", f"Telegram API error: {data}")
                time.sleep(5)
                continue

            updates = data.get("result", [])
            log("debug", f"Received {len(updates)} update(s)")
            for update in updates:
                offset = max(offset or 0, update.get("update_id", 0) + 1)
                process_update(update)

        except Exception as exc:
            log("error", f"Poll loop error: {exc}")
            time.sleep(5)


if __name__ == "__main__":
    run()
