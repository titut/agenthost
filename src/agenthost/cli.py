"""CLI entry point: agenthost serve | agenthost chat | agenthost list."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from agenthost.config import AgentConfig
from agenthost.registry import list_agents
from agenthost.server import serve
from agenthost.secure_key import load_keepass_env


def _add_serve_parser(
    subparsers: argparse._SubParsersAction,
) -> argparse.ArgumentParser:
    parser = subparsers.add_parser("serve", help="Serve an agent package.")
    parser.add_argument("agent", help="Path to the agent folder containing WHOAMI.md.")
    return parser


def _add_chat_parser(subparsers: argparse._SubParsersAction) -> argparse.ArgumentParser:
    parser = subparsers.add_parser(
        "chat",
        help="Have a continuous streaming conversation with a running agent.",
    )
    parser.add_argument(
        "message",
        nargs="?",
        help="Optional first message. If omitted, an interactive session starts.",
    )
    parser.add_argument(
        "--agent",
        help="Agent name to chat with. Looks up the running port automatically.",
    )
    parser.add_argument(
        "--host", default="127.0.0.1", help="Agent host (default: 127.0.0.1)."
    )
    parser.add_argument(
        "--port", type=int, help="Agent port. If omitted, --agent must be provided."
    )
    parser.add_argument(
        "--url", help="Full chat endpoint URL. Overrides --host and --port."
    )
    parser.add_argument("--thread", help="Existing thread ID to continue.")
    parser.add_argument(
        "--once",
        action="store_true",
        help="Send a single message and exit. Ignored if no message is given.",
    )
    return parser


def _add_list_parser(subparsers: argparse._SubParsersAction) -> argparse.ArgumentParser:
    parser = subparsers.add_parser("list", help="List currently running agents.")
    return parser


def _do_serve(args: argparse.Namespace) -> int:
    config = AgentConfig.from_path(args.agent)
    serve(config)
    return 0


def _resolve_chat_url(args: argparse.Namespace) -> str:
    if args.url:
        return args.url

    if args.port is not None:
        return f"http://{args.host}:{args.port}/chat"

    if args.agent:
        agents = list_agents()
        for agent in agents:
            if agent.name == args.agent:
                return f"http://{agent.host}:{agent.port}/chat"
        raise ValueError(
            f"No running agent named '{args.agent}'. Run `agenthost list` to see active agents."
        )

    raise ValueError("Must provide --port, --url, or --agent.")


def _stream_turn(
    url: str, message: str, thread_id: str | None
) -> tuple[str | None, int]:
    """Send one message and stream the response. Returns (thread_id, exit_code)."""
    try:
        import httpx
    except ImportError as exc:
        print(f"httpx is required for chat: {exc}", file=sys.stderr)
        return thread_id, 1

    payload: dict[str, object] = {"message": message}
    if thread_id:
        payload["thread_id"] = thread_id

    new_thread_id: str | None = thread_id
    try:
        with httpx.stream("POST", url, json=payload, timeout=120.0) as response:
            response.raise_for_status()
            current_event: str | None = None
            for line in response.iter_lines():
                line = line.strip()
                if not line:
                    current_event = None
                    continue

                if line.startswith("event:"):
                    current_event = line.split(":", 1)[1].strip()
                    continue

                if line.startswith("data:") and current_event is not None:
                    data_part = line.split(":", 1)[1].strip()

                    if current_event == "meta":
                        meta = json.loads(data_part)
                        new_thread_id = meta.get("thread_id")
                        print(f"[thread_id: {new_thread_id}]")
                    elif current_event == "message":
                        event = json.loads(data_part)
                        event_type = event.get("type")
                        event_data = event.get("data")
                        if event_type == "content":
                            print(event_data, end="", flush=True)
                        elif event_type == "tool_start":
                            print(
                                f"\n[tool: {event_data['name']}({event_data['arguments']})]"
                            )
                        elif event_type == "tool_result":
                            print(f"[tool result: {event_data['result']}]")
                    elif current_event == "done":
                        print()
                    elif current_event == "error":
                        print(f"\n[error: {data_part}]")
    except httpx.RequestError as exc:
        print(f"\n[error: could not reach agent at {url}: {exc}]", file=sys.stderr)
        return new_thread_id, 1
    except httpx.HTTPStatusError as exc:
        print(
            f"\n[error: agent returned {exc.response.status_code}: {exc.response.text}]",
            file=sys.stderr,
        )
        return new_thread_id, 1

    return new_thread_id, 0


def _do_chat(args: argparse.Namespace) -> int:
    try:
        url = _resolve_chat_url(args)
    except ValueError as exc:
        print(f"[error: {exc}]", file=sys.stderr)
        return 1

    thread_id: str | None = args.thread

    if args.message and args.once:
        _, code = _stream_turn(url, args.message, thread_id)
        return code

    print("Starting chat. Type /help for commands, /quit to exit.")

    if args.message:
        new_thread, code = _stream_turn(url, args.message, thread_id)
        if code != 0:
            return code
        thread_id = new_thread

    while True:
        try:
            user_input = input("\n> ")
        except EOFError:
            print()
            break
        except KeyboardInterrupt:
            print()
            break

        user_input = user_input.strip()
        if not user_input:
            continue

        if user_input in ("/quit", "/exit", "/q"):
            break

        if user_input == "/help":
            print(
                "Commands:\n"
                "  /help      Show this help\n"
                "  /quit      Exit the chat\n"
                "  /thread    Show the current thread ID"
            )
            continue

        if user_input == "/thread":
            print(f"Current thread: {thread_id or '<none>'}")
            continue

        new_thread, code = _stream_turn(url, user_input, thread_id)
        if code != 0:
            return code
        thread_id = new_thread

    print("Goodbye.")
    return 0


def _do_list(_args: argparse.Namespace) -> int:
    agents = list_agents()
    if not agents:
        print("No active agents.")
        return 0

    print(f"{'Name':<15} {'Host':<15} {'Port':<6} {'PID':<8} {'Path'}")
    print("-" * 80)
    for agent in agents:
        print(
            f"{agent.name:<15} {agent.host:<15} {agent.port:<6} {agent.pid:<8} {agent.path}"
        )
    return 0


def main(argv: list[str] | None = None) -> int:
    # MUST KEEP FOR API KEY TO LOAD FROM ENVIRONMENT VARIABLES
    load_keepass_env("keys.kdbx", "OPENAI_API_KEY", "c1bc0bgq")
    parser = argparse.ArgumentParser(
        prog="agenthost",
        description="Host and chat with folder-based agents.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    _add_serve_parser(subparsers)
    _add_chat_parser(subparsers)
    _add_list_parser(subparsers)

    args = parser.parse_args(argv)

    if args.command == "serve":
        return _do_serve(args)
    if args.command == "chat":
        return _do_chat(args)
    if args.command == "list":
        return _do_list(args)

    return 0


if __name__ == "__main__":
    sys.exit(main())
