# agenthost — Agent Development Guide

This guide is written for AI coding agents who need to understand, modify, or extend the `agenthost` project. It is derived from the actual codebase, configuration files, and runtime behavior.

## Project Overview

`agenthost` is a minimal, modular Python host for folder-based LLM agents. Each agent is a self-contained directory that includes a system prompt, configuration, tools, skills, scheduled events, and persistent memory. The host exposes agents through a FastAPI+SSE server, a Textual TUI chat client, and a small CLI. Optional bridges connect agents to Discord, Telegram, and WhatsApp.

Key concepts:

- **Agent package**: a directory with at least `WHOAMI.md` and optionally `agent.yaml`, `tools/`, `skills/`, `events.yaml`, `CRITICAL.md`.
- **Toolbox**: a folder-based package under `toolboxes/` that contains a `tools/` directory and/or a `skills/` directory. An agent can switch toolboxes at runtime while keeping the same memory and conversation state; only one toolbox is active at a time.
- **Runtime data directory**: `~/.agenthost` by default (override with `AGENTHOST_HOME`). Stores the registry, KeePass key database, log file, agent aliases, uploads, output, and per-agent memory databases.
- **Built-in tools**: opt-in capabilities (events, Discord, file reading, skill CRUD, toolbox management, etc.) enabled in `agent.yaml` under `extra.builtin_tools`. Enable the `toolbox` group to allow the `toolbox(action, target)` tool.
- **Memory**: per-agent SQLite database with recent-message verbatim recall plus RAG retrieval over embedded older messages.
- **Secrets**: stored in a KeePass `.kdbx` file and loaded into environment variables on `serve`/`chat`.

## Technology Stack

- **Language**: Python 3.10+ (project currently uses 3.10.12 in the bundled venv).
- **Build system**: `hatchling` (declared in `pyproject.toml`).
- **Web server**: FastAPI + uvicorn, server-sent events (SSE) for streaming responses.
- **LLM client**: `openai` SDK; OpenAI-compatible endpoints (default is DeepInfra).
- **Embeddings**: OpenAI-compatible `embeddings.create` (default model `BAAI/bge-m3` via DeepInfra).
- **Memory/RAG**: SQLite (`sqlite3`) plus optional `numpy` and `tiktoken` (if installed).
- **Scheduling**: APScheduler for cron-like agent events.
- **Secrets**: `pykeepass` for `.kdbx` management.
- **TUI chat**: `textual` + `httpx`.
- **Bridges**:
  - Discord: Python with `aiohttp`, `discord.py`, `httpx`.
  - Telegram: Python polling with `httpx`.
  - WhatsApp: Node.js + TypeScript, Baileys (`@whiskeysockets/baileys`), requires Node >= 20.
- **Document processing**: `python-docx`, `pypdf`, `pandas`, `openpyxl`, `tabulate`.

## Project Structure

```
agent_toolbox/
├── pyproject.toml              # Package metadata, deps, entry point
├── DEV_GUIDE.md                # Detailed architecture map (read this next)
├── README.md                   # ⚠️ Currently corrupted: contains JSON payload, not a real README
├── venv/                       # Bundled Python 3.10 virtual environment
├── src/agenthost/              # Core package
│   ├── __main__.py             # `python -m agenthost` entry point
│   ├── __init__.py             # Version metadata
│   ├── cli.py                  # CLI dispatcher
│   ├── server.py               # FastAPI + uvicorn server
│   ├── agent.py                # Core Agent class and LLM loop
│   ├── config.py               # AgentConfig loader and defaults
│   ├── agents_config.py        # agents.yaml alias management
│   ├── tools.py                # Tool discovery and execution
│   ├── builtin_tools.py        # Built-in tools (events, Discord, files, etc.)
│   ├── events.py               # Scheduled-event models and APScheduler triggers
│   ├── skills.py               # Markdown skill loader
│   ├── toolbox.py              # Toolbox discovery and helpers
│   ├── memory.py               # SQLite memory + RAG retrieval
│   ├── embeddings.py           # Async embedding client
│   ├── filesystem_tools.py     # Read-only file tools and text extraction
│   ├── registry.py             # Running-agent registry
│   ├── home.py                 # Runtime data directory helpers
│   ├── logger.py               # Logging setup
│   ├── secure_key.py           # KeePass .kdbx management
│   └── chat_tui.py             # Textual chat client
├── agents/                     # Built-in/public agents
│   ├── DOCUMENT_WRITER/
│   ├── GMAIL_AGENT/
│   ├── ORCHESTRATOR/
│   ├── RESEARCHER/
│   └── TODO/
├── agents_personal/              # Git-ignored personal agents (do not commit)
│   ├── BEVABAN_MAIL/
│   ├── FINANCE/
│   └── JOB_HUNTER/
├── templates/agent/              # New-agent template
│   ├── WHOAMI.md
│   ├── agent.yaml
│   └── events.yaml
├── toolboxes/                    # Reusable toolbox packages
│   └── example/                  # Sample toolbox: tools/ + skills/
└── integrations/                 # Optional chat bridges
    ├── discord/                  # Python aiohttp/discord.py bot
    ├── telegram/                 # Python polling bot
    └── whatsapp/                 # Node.js Baileys bridge
```

## Agent Package Layout

Every agent must be a directory with at least `WHOAMI.md`. The host loads these files in order:

1. `WHOAMI.md` — the agent’s system prompt (role, capabilities, rules). Must contain a `# Description` section.
2. `CRITICAL.md` — instructions appended at the end of every LLM payload as the freshest context.
3. `agent.yaml` — overrides for model, temperature, memory, embeddings, orchestrator mode, and `extra` settings. Defaults are loaded from `~/.agenthost/default_agent.yaml` if present, then merged.
4. `tools/*.py` — Python functions exposed as LLM tools. Functions starting with `_` or imported from other modules are ignored.
5. `skills/*.md` — markdown knowledge snippets loaded on demand via `get_skill(name)`.
6. `events.yaml` — scheduled prompts for background tasks (APScheduler).

Template files live in `templates/agent/`.

## Toolbox Layout

A toolbox is a directory under `toolboxes/` that bundles a reusable set of tools
and skills. An agent loads one toolbox at a time via the `toolbox(action="switch", target="<name>")`
built-in tool. Memory and conversation state persist across toolbox switches;
only the available tools and skills change.

Toolbox package structure:

```
toolboxes/<name>/
├── tools/          # Python tool files (same rules as agent tools/)
└── skills/         # Markdown skill files
```

When active, a toolbox's tools and skills replace the agent's base tools and
skills. Built-in tools are always preserved. Pass an empty target to
`toolbox(action="switch", target="")` to revert to the agent's default tools and skills.

See `toolboxes/README.md` and `toolboxes/example/` for a working sample.

## Build and Run Commands

The project is packaged as `agenthost` and can be run from the bundled venv or installed as an editable package.

From the repo root:

```bash
# Run from the bundled venv (recommended for this checkout)
venv/bin/python -m agenthost --help
venv/bin/python -m agenthost serve agents/RESEARCHER
venv/bin/python -m agenthost chat --agent RESEARCHER --once "hello"

# Or, after installing the package (creates the `agenthost` console script)
pip install -e .
agenthost serve RESEARCHER
agenthost chat --agent RESEARCHER

# List running agents
agenthost list

# Manage agent aliases
agenthost agent list
agenthost agent add myagent /path/to/agent
agenthost agent remove myagent

# Manage API keys in the KeePass database
agenthost key list
agenthost key add --name OPENAI_API_KEY
agenthost key edit --name OPENAI_API_KEY
```

The KeePass master password is hardcoded in `cli.py` for the current workflow (search for `load_all_keepass_env`). When running `serve` or `chat`, the CLI loads every entry in `~/.agenthost/keys.kdbx` into environment variables.

## CLI Subcommands

- `serve <alias-or-path>` — start an agent’s FastAPI server.
- `chat [message]` — start an interactive chat session. Supports `--agent`, `--port`, `--url`, `--thread`, `--once`, `--no-tui`, `--chatless`.
- `list` — show currently running agents from the registry.
- `key` — manage secrets in the KeePass database (`list`, `add`, `edit`).
- `agent` — manage alias-to-folder mappings in `agents.yaml` (`list`, `add`, `remove`).

## Integrations

Each bridge is a separate process that forwards messages to a running agent’s `/chat` endpoint and sends replies back.

### Discord Bridge (`integrations/discord/`)

- Requires `DISCORD_BOT_TOKEN` in `.env`.
- Also exposes an outbound HTTP server at `http://127.0.0.1:9002/send` (and `/edit`) so agents can push messages via `send_discord()`.
- Supports access control via `ALLOWED_USER_IDS`, `ALLOWED_GUILD_IDS`, `ALLOWED_CHANNEL_IDS`, and a prefix trigger `DISCORD_GUILD_PREFIX`.
- Commands: `!id`, `!start`, `!stop`, `!clear`.
- Run: `venv/bin/python integrations/discord/main.py` from the repo root (or from inside `integrations/discord/` with `../../venv/bin/python main.py`).

### Telegram Bridge (`integrations/telegram/`)

- Requires `TELEGRAM_BOT_TOKEN` in `.env`.
- Long-polling bot; forwards Telegram `chat_id` as the agent `thread_id`.
- Run: `venv/bin/python integrations/telegram/main.py`.

### WhatsApp Bridge (`integrations/whatsapp/`)

- Node.js service using Baileys; requires Node >= 20.
- Outbound server at `http://127.0.0.1:9001/send` for `send_whatsapp_message()`.
- Session state saved to `./auth_state`; scan QR on first run.
- Run: `npm install && npm run dev`.

## Code Style Guidelines

- Use Python 3.10+ syntax. The codebase uses `from __future__ import annotations` and `str | None` style unions.
- Keep files under the existing architecture; do not introduce new package structures without updating the import graph in `DEV_GUIDE.md`.
- Add module docstrings summarizing the file’s purpose.
- For agent tools, use top-level `async def` or `def` functions. Functions starting with `_` or imported from another module are not registered as tools. Document parameters with type hints and docstrings; the schema generator uses the first docstring line as the tool description and parameter names as descriptions.
- Skills are plain markdown files in `skills/`; they must contain a `# Description` section to appear correctly in the Available Skills list.
- Agent YAML values override `~/.agenthost/default_agent.yaml`, which overrides `DEFAULT_CONFIG` in `src/agenthost/config.py`.
- Avoid broad imports that would be mis-detected as tools; use underscore-prefixed helper modules or `__init__.py` to share helpers between tools.
- Do not modify `agents_personal/` or `agents/*/memory/`, `agents/*/output/`, or `uploads/` in normal code changes — these are runtime/personal data and are git-ignored.

## Testing Instructions

There is currently no test suite in the project (no `tests/` directory, no `pytest.ini`, no `tox.ini`, no CI/CD configuration). The existing validation is manual and import-based.

Recommended verification before committing changes:

```bash
# Verify all core modules compile
cd /home/koroko/Workspace/agent_toolbox
venv/bin/python -m py_compile src/agenthost/*.py

# Verify the package imports
cd /home/koroko/Workspace/agent_toolbox
venv/bin/python -c "import agenthost; import agenthost.cli; import agenthost.server; import agenthost.agent; import agenthost.config; import agenthost.tools; import agenthost.toolbox; print('imports ok')"

# Verify the CLI can print help
venv/bin/python -m agenthost --help
```

If you add new agent tools, test them by running the agent locally and invoking the tool through the chat interface. Check `agenthost.log` in the runtime home directory for full request/response payloads.

## Security Considerations

- Secrets are stored in `~/.agenthost/keys.kdbx` and loaded into environment variables at runtime. The `.gitignore` already excludes `keys.kdbx`, `.env`, `.env.local`, `*.key`, `*.pem`, `*.cert`.
- The `agents_personal/` directory is git-ignored and contains personal data; do not commit it.
- Agent memory databases (`memory.db`) and generated output are git-ignored.
- File-system tools resolve paths relative to the agenthost home directory and block `..` traversal that escapes it. Maintain that boundary in any new file tools.
- The Discord bridge can restrict access by user/guild/channel IDs; do not disable these in production.
- The current `cli.py` hardcodes a KeePass password in a function call. If you refactor this, switch to a prompt or environment variable and do not leave hardcoded credentials in committed code.
- `README.md` at the project root is currently corrupted and contains what appears to be a captured LLM API request. Treat it as a bug to fix rather than a source of truth; do not expose any private data that may be inside it.

## Common Development Tasks

### Add a new agent

1. Copy `templates/agent/` to a new directory.
2. Edit `WHOAMI.md` with the agent’s role, description, capabilities, and rules.
3. Edit `agent.yaml` to set the desired model, temperature, and enabled built-in tools.
4. Add Python tools under `tools/` (top-level functions only).
5. Add markdown skills under `skills/` if needed.
6. Register an alias: `agenthost agent add <alias> <path>`.
7. Serve it: `agenthost serve <alias>`.

### Add a new built-in tool

1. Implement the function in `src/agenthost/builtin_tools.py` (or a helper class).
2. Register it in `make_builtin_tools()` and `build_builtin_tools_prompt()`.
3. Enable it via `agent.yaml` `extra.builtin_tools` using the group or tool name.

### Add a new toolbox

1. Create a directory under `toolboxes/<name>/`.
2. Add Python tools under `tools/` (top-level `async def` or `def` functions only).
3. Add markdown skills under `skills/` if needed; each skill should contain a `# Description` section.
4. Restart the agent server if it is already running (toolbox discovery is dynamic, but the list of valid toolbox names is checked at switch time).
5. Enable the `toolbox` group in the agent's `agent.yaml` under `extra.builtin_tools` so the agent can switch toolboxes:

   ```yaml
   extra:
     builtin_tools: [toolbox]
   ```

6. From a chat session, call `toolbox(action="switch", target="<name>")` to activate it, `toolbox(action="list")` to see available toolboxes, or `toolbox(action="list", target="<name>")` to inspect a specific toolbox's tools and skills.

### Change the default model or provider

- Edit `DEFAULT_CONFIG` in `src/agenthost/config.py` for global defaults, or create/edit `~/.agenthost/default_agent.yaml` for project-wide defaults without changing code.

### Debug an agent

- Set `AGENTHOST_DEBUG=1` (it is enabled by default) to capture full LLM payloads in `~/.agenthost/agenthost.log`.
- Use `agenthost chat --no-tui` for a simple text loop if the TUI is not needed.
- Use `agenthost chat --chatless --thread <id> --port <port>` to monitor an ongoing turn from another client.

## Known Issues and Caveats

- `README.md` is corrupted (contains JSON, not documentation). It should be rewritten to match the project.
- No automated tests or CI/CD pipeline exists.
- No linting or type-checking configuration is present (e.g., `ruff.toml`, `mypy.ini`).
- The WhatsApp bridge stores session state in `integrations/whatsapp/auth_state/`. That directory is git-ignored but may contain sensitive auth data; be careful not to leak it.
- The `ORCHESTRATOR` agent depends on other agents being already running; it does not start them itself.
- The default provider is DeepInfra (`https://api.deepinfra.com/v1`) with the `moonshotai/Kimi-K2.5` model. Ensure the relevant API key is present in `keys.kdbx` (usually `OPENAI_API_KEY` or `DEEPINFRA_API_KEY`).

## Useful Files to Read First

- `DEV_GUIDE.md` — detailed module-by-module breakdown, import graph, and `~/.agenthost` layout.
- `templates/agent/agent.yaml` — full configuration reference.
- `templates/agent/WHOAMI.md` — system prompt template.
- `templates/agent/events.yaml` — scheduled-event template.
- `src/agenthost/toolbox.py` — toolbox discovery helpers used by the agent.
- `src/agenthost/config.py` — defaults and config merging logic.
- `src/agenthost/builtin_tools.py` — how built-in tools are enabled and described.
- `src/agenthost/agent.py` — the LLM chat/tool loop, message normalization, and toolbox switching.
