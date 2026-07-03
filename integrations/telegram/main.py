"""Simple Telegram polling bridge for agenthost agents.

Forwards incoming Telegram messages to a running agenthost agent's /chat endpoint
and sends the agent's replies back via the Telegram Bot API.
"""
from __future__ import annotations

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

LEVELS = ["silent", "error", "warn", "info", "debug", "trace"]


def log(level: str, message: str, *args: Any) -> None:
    if LEVELS.index(level) <= LEVELS.index(LOG_LEVEL):
        prefix = f"[{time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())}] [{level.upper()}] [telegram-bridge]"
        print(f"{prefix} {message}", *args, flush=True)


def check_agent_health() -> dict[str, Any]:
    """Verify the agent is reachable before starting the poll loop."""
    log("info", f"Checking agent health at {AGENT_HEALTH_URL}")
    try:
        response = httpx.get(AGENT_HEALTH_URL, timeout=10.0)
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
        response = httpx.post(url, json=payload, timeout=30.0)
        log("debug", f"-> telegram HTTP {response.status_code}")
        response.raise_for_status()
    except Exception as exc:
        log("error", f"Failed to send Telegram message: {exc}")
        raise


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

    thread_id = str(chat_id)
    log("info", f"chat_id={chat_id} -> thread_id={thread_id}: {text[:80]}")

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
            response = httpx.get(
                f"{base_url}/getUpdates",
                params=params,
                timeout=POLL_TIMEOUT + 10,
            )
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
