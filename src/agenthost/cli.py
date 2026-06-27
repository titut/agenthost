"""CLI entry point: agenthost serve | agenthost chat | agenthost list | agenthost key | agenthost agent."""

from __future__ import annotations

import argparse
import getpass
import json
import sys
from pathlib import Path

from agenthost.agents_config import AgentsConfig
from agenthost.config import AgentConfig
from agenthost.home import get_keys_db_path
from agenthost.logger import setup_logging
from agenthost.registry import list_agents
from agenthost.server import serve
from agenthost.secure_key import (
    KeePassDB,
    KeePassDBCorruptedError,
    KeePassEntryNotFoundError,
    KeePassNotFoundError,
    KeePassWrongPasswordError,
    load_keepass_env,
)


try:
    from agenthost.chat_tui import ChatApp
except Exception:  # noqa: BLE001
    ChatApp = None  # type: ignore[misc, assignment]


logger = setup_logging("agenthost.cli")


def _add_serve_parser(
    subparsers: argparse._SubParsersAction,
) -> argparse.ArgumentParser:
    parser = subparsers.add_parser("serve", help="Serve an agent package.")
    parser.add_argument(
        "agent",
        help="Agent alias (from agents.yaml) or path to the agent folder containing WHOAMI.md.",
    )
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
    parser.add_argument(
        "--no-tui",
        dest="no_tui",
        action="store_true",
        help="Use the simple text chat loop instead of the TUI.",
    )
    return parser


def _add_list_parser(subparsers: argparse._SubParsersAction) -> argparse.ArgumentParser:
    parser = subparsers.add_parser("list", help="List currently running agents.")
    return parser


def _resolve_agent_path(alias_or_path: str) -> Path:
    """Resolve an agent alias or folder path to an absolute Path."""
    agents_config = AgentsConfig()
    resolved = agents_config.resolve(alias_or_path)
    if resolved is not None:
        return resolved
    return Path(alias_or_path).expanduser().resolve()


def _do_serve(args: argparse.Namespace) -> int:
    config = AgentConfig.from_path(_resolve_agent_path(args.agent))
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


def _fetch_agent_name(url: str) -> str:
    """Best-effort lookup of the agent name from the server's /health endpoint."""
    try:
        import httpx

        health_url = url.replace("/chat", "/health")
        response = httpx.get(health_url, timeout=5.0)
        response.raise_for_status()
        return response.json().get("agent", "agent")
    except Exception:  # noqa: BLE001
        return "agent"


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
        with httpx.stream("POST", url, json=payload, timeout=600.0) as response:
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
        logger.error("Could not reach agent at %s: %s", url, exc)
        print(f"\n[error: could not reach agent at {url}: {exc}]", file=sys.stderr)
        return new_thread_id, 1
    except httpx.HTTPStatusError as exc:
        logger.error(
            "Agent returned %s: %s",
            exc.response.status_code,
            exc.response.text,
        )
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

    use_tui = (
        not args.no_tui
        and not args.once
        and sys.stdin.isatty()
        and ChatApp is not None
    )

    if not use_tui:
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

    agent_name = _fetch_agent_name(url)
    app = ChatApp(url=url, agent_name=agent_name, thread_id=thread_id)
    app.run()
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


def _add_key_parser(
    subparsers: argparse._SubParsersAction,
) -> argparse.ArgumentParser:
    """Add key subcommand group: list, add, edit."""
    parser = subparsers.add_parser("key", help="Manage API keys in KeePass database.")
    key_sub = parser.add_subparsers(dest="key_command", required=True)

    # key list
    list_p = key_sub.add_parser("list", help="List all key names.")
    list_p.add_argument(
        "--db", default=str(get_keys_db_path()), help=f"Path to .kdbx file (default: {get_keys_db_path()})"
    )
    list_p.add_argument(
        "--password", help="Master password (will prompt securely if omitted)"
    )

    # key add
    add_p = key_sub.add_parser("add", help="Add a new API key.")
    add_p.add_argument(
        "--db", default=str(get_keys_db_path()), help=f"Path to .kdbx file (default: {get_keys_db_path()})"
    )
    add_p.add_argument(
        "--password", help="Master password (will prompt securely if omitted)"
    )
    add_p.add_argument("--name", help="Key name / entry title (will prompt if omitted)")
    add_p.add_argument(
        "--value", help="Key value (will prompt with masked input if omitted)"
    )

    # key edit
    edit_p = key_sub.add_parser("edit", help="Edit an existing API key.")
    edit_p.add_argument(
        "--db", default=str(get_keys_db_path()), help=f"Path to .kdbx file (default: {get_keys_db_path()})"
    )
    edit_p.add_argument(
        "--password", help="Master password (will prompt securely if omitted)"
    )
    edit_p.add_argument(
        "--name", help="Key name to edit (will prompt with selection if omitted)"
    )
    edit_p.add_argument(
        "--value", help="New key value (will prompt with masked input if omitted)"
    )

    return parser


def _do_key(args: argparse.Namespace) -> int:
    """Dispatch key subcommands to KeePassDB operations."""
    db_path: str = args.db
    master_password: str | None = args.password

    if master_password is None:
        master_password = getpass.getpass("Master password: ")

    try:
        if args.key_command == "list":
            db = KeePassDB(db_path, master_password)
            keys = db.list_keys()
            if keys:
                for key in keys:
                    print(key)
            else:
                print("No keys found.")
            return 0

        elif args.key_command == "add":
            # Prompt for name if omitted
            name: str | None = args.name
            if name is None:
                name = input("KEY_NAME: ").strip()
                if not name:
                    print("\u274c Key name cannot be empty.", file=sys.stderr)
                    return 1

            # Prompt for value if omitted
            value: str | None = args.value
            if value is None:
                value = getpass.getpass("KEY_VALUE: ").strip()
                if not value:
                    print("\u274c Key value cannot be empty.", file=sys.stderr)
                    return 1

            db_existed = Path(db_path).exists()
            db = KeePassDB(db_path, master_password)
            if not db_existed:
                print(f"\u2705 Created new KeePass database at {db_path}.")

            try:
                db.add_key(name, value)
            except ValueError as exc:
                print(f"\u274c {exc}", file=sys.stderr)
                return 1

            print(f"\u2705 Added key '{name}' to {db_path}.")
            return 0

        elif args.key_command == "edit":
            db = KeePassDB(db_path, master_password)

            name = args.name
            if name is None:
                keys = db.list_keys()
                if not keys:
                    print("\u274c No keys to edit.", file=sys.stderr)
                    return 1
                print("Select a key to edit:")
                for i, key in enumerate(keys, start=1):
                    print(f"  {i}) {key}")
                choice = input("Enter number or key name: ").strip()
                if choice.isdigit():
                    idx = int(choice) - 1
                    if 0 <= idx < len(keys):
                        name = keys[idx]
                    else:
                        print(
                            f"\u274c Invalid selection: {choice}",
                            file=sys.stderr,
                        )
                        return 1
                elif choice:
                    name = choice
                else:
                    print("\u274c No key selected.", file=sys.stderr)
                    return 1

            value = args.value
            if value is None:
                value = getpass.getpass("New value: ").strip()
                if not value:
                    print("\u274c Key value cannot be empty.", file=sys.stderr)
                    return 1

            db.update_key(name, value)
            print(f"\u2705 Updated key '{name}' in {db_path}.")
            return 0

    except KeePassNotFoundError as exc:
        print(f"\u274c {exc}", file=sys.stderr)
        return 1
    except KeePassWrongPasswordError:
        print(
            "\u274c Invalid master password for KeePass database.",
            file=sys.stderr,
        )
        return 1
    except KeePassEntryNotFoundError as exc:
        print(f"\u274c {exc}", file=sys.stderr)
        return 1
    except KeePassDBCorruptedError as exc:
        print(f"\u274c {exc}", file=sys.stderr)
        return 1

    return 0


def _add_agent_parser(
    subparsers: argparse._SubParsersAction,
) -> argparse.ArgumentParser:
    """Add agent subcommand group: list, add, remove."""
    parser = subparsers.add_parser(
        "agent", help="Manage registered agent aliases."
    )
    agent_sub = parser.add_subparsers(dest="agent_command", required=True)

    # agent list
    agent_sub.add_parser("list", help="List registered agent aliases.")

    # agent add
    add_p = agent_sub.add_parser("add", help="Register an agent alias.")
    add_p.add_argument("alias", help="Short name for the agent.")
    add_p.add_argument("path", help="Path to the agent folder.")

    # agent remove
    rm_p = agent_sub.add_parser("remove", help="Remove an agent alias.")
    rm_p.add_argument("alias", help="Short name for the agent.")

    return parser


def _do_agent(args: argparse.Namespace) -> int:
    """Dispatch agent subcommands."""
    agents_config = AgentsConfig()

    if args.agent_command == "list":
        aliases = agents_config.list()
        if not aliases:
            print("No agents registered.")
            return 0
        for alias, path in sorted(aliases.items()):
            print(f"{alias:<20} {path}")
        return 0

    if args.agent_command == "add":
        agent_path = Path(args.path).expanduser().resolve()
        if not agent_path.is_dir():
            print(f"\u274c Not a directory: {agent_path}", file=sys.stderr)
            return 1
        if not (agent_path / "WHOAMI.md").exists():
            print(
                f"\u274c Agent folder missing WHOAMI.md: {agent_path}",
                file=sys.stderr,
            )
            return 1
        agents_config.add(args.alias, agent_path)
        print(f"\u2705 Registered '{args.alias}' -> {agent_path}")
        return 0

    if args.agent_command == "remove":
        if agents_config.remove(args.alias):
            print(f"\u2705 Removed alias '{args.alias}'.")
            return 0
        print(f"\u274c Alias not found: {args.alias}", file=sys.stderr)
        return 1

    return 0


def _format_commands(parser: argparse.ArgumentParser, indent: int = 0) -> list[str]:
    """Recursively collect command names and help text for --help output."""
    lines: list[str] = []
    if parser._subparsers is None:
        return lines
    for action in parser._subparsers._actions:
        if not isinstance(action, argparse._SubParsersAction):
            continue
        for choice in action._choices_actions:
            name = choice.dest
            help_text = choice.help or ""
            lines.append("  " * indent + f"{name:<15} {help_text}")
            subparser = action._name_parser_map.get(name)
            if subparser is not None:
                lines.extend(_format_commands(subparser, indent + 1))
    return lines


def main(argv: list[str] | None = None) -> int:
    logger.info("agenthost CLI starting: %s", argv if argv else sys.argv)
    parser = argparse.ArgumentParser(
        prog="agenthost",
        description="Host and chat with folder-based agents.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    _add_serve_parser(subparsers)
    _add_chat_parser(subparsers)
    _add_list_parser(subparsers)
    _add_key_parser(subparsers)
    _add_agent_parser(subparsers)

    parser.epilog = "\n".join(
        ["commands:", ""] + _format_commands(parser)
    )

    args = parser.parse_args(argv)

    if args.command == "key":
        return _do_key(args)
    if args.command == "agent":
        return _do_agent(args)

    # MUST KEEP FOR API KEY TO LOAD FROM ENVIRONMENT VARIABLES
    load_keepass_env(str(get_keys_db_path()), "OPENAI_API_KEY", "c1bc0bgq")

    if args.command == "serve":
        logger.info("Dispatching command: serve")
        return _do_serve(args)
    if args.command == "chat":
        logger.info("Dispatching command: chat")
        return _do_chat(args)
    if args.command == "list":
        logger.info("Dispatching command: list")
        return _do_list(args)

    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as exc:
        logger.exception("Unhandled exception in agenthost CLI: %s", exc)
        raise
