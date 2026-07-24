# agenthost

A minimal, modular Python host for folder-based LLM agents. Each agent is a self-contained directory with its own system prompt, configuration, tools, skills, scheduled events, and persistent memory. Expose agents through a FastAPI+SSE server, a Textual TUI chat client, a small CLI, and optional chat bridges (Discord, Telegram, WhatsApp).

## Features

- **Folder-based agents** — every agent is a directory containing `WHOAMI.md` (system prompt), `agent.yaml` (config), `tools/` (Python functions), `skills/` (Markdown knowledge snippets), `events.yaml` (scheduled triggers), and optional `CRITICAL.md` (high-priority instructions).
- **Toolbox switching** — agents can swap their active tool and skill set at runtime via the `toolbox` built-in tool, keeping conversation state and memory intact. Only one toolbox is active at a time.
- **Persistent memory with RAG** — SQLite-backed per-agent memory stores recent messages verbatim and uses embedding-based retrieval (RAG) to recover relevant older context.
- **Built-in tools** — opt-in capabilities enabled per-agent in `agent.yaml`:
  - `get_current_datetime` — return current date/time in ISO 8601 (always enabled).
  - `get_skill` — load full Markdown content of a skill on demand (always enabled).
  - `event_tool` — CRUD for scheduled events (`list`, `add`, `update`, `delete`). Supports daily, weekday, weekend, day-of-week, interval, and one-shot triggers with multiple times.
  - `send_discord` / `edit_discord_message` — push messages to Discord via the bridge, with optional file attachments.
  - `get_current_thread_id` — expose the current conversation thread ID.
  - `read_file` / `list_uploads` — read files inside the project directory; transparently extracts text from `.docx`, `.pdf`, `.xlsx`, `.csv`, `.md`, and `.txt`.
  - `skill_crud` — create, update, or delete Markdown skill files.
  - `toolbox` — list available toolboxes, inspect a toolbox's tools/skills, or switch the active toolbox.
- **Scheduled events** — agents can manage their own cron-like triggers via `events.yaml` and the `event_tool`. Schedules use APScheduler and are reloaded live when modified.
- **Secrets management** — KeePass `.kdbx` database stores API keys and secrets, loaded into environment variables at runtime.
- **Multi-interface** — interact with agents through:
  - **TUI chat client** — Textual-based terminal UI with streaming, tool call/result cards, thread picker, `@path` context attachment, and chatless monitor mode.
  - **CLI** — one-shot queries (`--once`), plain-text loop (`--no-tui`), and thread monitoring.
  - **SSE HTTP API** — FastAPI server with server-sent events for streaming responses.
- **Chat bridges** — optional integrations forward messages from messaging platforms to a running agent:
  - **Discord** — bot with mention/prefix triggers, DM support, access control, file attachments, outbound send/edit endpoints.
  - **Telegram** — polling bot with username-based access control, proxy support, Markdown replies.
  - **WhatsApp** — Node.js/Baileys bridge with QR pairing, self-chat, outbound send endpoint.
- **Document processing** — transparent text extraction from Word, PDF, Excel, and CSV files via `python-docx`, `pypdf`, `pandas`, `openpyxl`, and `tabulate`.
- **Orchestrator mode** — agents can be configured as orchestrators that route tasks to other running agents.
- **OpenAI-compatible** — uses the `openai` SDK with any OpenAI-compatible API endpoint (default: DeepInfra). Supports reasoning effort control, frequency/presence penalties, and configurable embedding models.

## Installation

```bash
# Clone the repository
git clone git@github.com:titut/agenthost.git
cd agenthost

# Use the bundled virtual environment (Python 3.10+)
venv/bin/python -m agenthost --help

# Or install the package
pip install -e .
agenthost --help
```

### Optional Dependencies

```bash
# Web research agent
pip install -e ".[researcher]"

# Gmail agent
pip install -e ".[gmail]"

# Enhanced memory (RAG with numpy)
pip install -e ".[memory]"

# LinkedIn jobs scraper
pip install -e ".[jobhunter]"

# Everything
pip install -e ".[full]"
```

## Quick Start

```bash
# Serve the built-in RESEARCHER agent
agenthost serve RESEARCHER

# In another terminal, start a chat
agenthost chat --agent RESEARCHER

# One-shot query
agenthost chat --agent RESEARCHER --once "What is the weather in Tokyo?"

# Plain-text loop (no TUI)
agenthost chat --agent RESEARCHER --no-tui
```

## CLI Reference

```
agenthost serve <alias-or-path>     Start an agent's FastAPI server
agenthost chat [message]            Interactive chat session
  --agent <name>                    Agent alias or path
  --port <port>                     Server port (default: 8000)
  --url <url>                       Direct URL to /chat endpoint
  --thread <id>                     Conversation thread ID
  --once <message>                  One-shot query, print response, exit
  --no-tui                          Plain text loop (no Textual TUI)
  --chatless                        Monitor an ongoing turn
agenthost list                      Show currently running agents
agenthost key                       Manage API keys in KeePass database
  list                              List all stored keys
  add --name <KEY>                  Add a new key (prompts for value)
  edit --name <KEY>                 Edit an existing key
agenthost agent                     Manage agent alias mappings
  list                              List all aliases
  add <alias> <path>                Register a new alias
  remove <alias>                    Remove an alias
```

## Agent Package Layout

```
my-agent/
├── WHOAMI.md          # System prompt (required). Must contain '# Description'
├── CRITICAL.md        # Instructions appended at end of every payload (optional)
├── agent.yaml         # Model, temperature, memory, built-in tools config
├── events.yaml        # Scheduled cron-like triggers
├── tools/
│   ├── __init__.py    # Can re-export public functions
│   └── *.py           # Each file: top-level async/def functions become tools
└── skills/
    └── *.md           # Markdown knowledge snippets with '# Description' section
```

### Creating a New Agent

```bash
# Copy the template
cp -r templates/agent agents/MY_AGENT

# Edit the system prompt
vim agents/MY_AGENT/WHOAMI.md

# Configure the agent
vim agents/MY_AGENT/agent.yaml

# Register an alias
agenthost agent add my-agent agents/MY_AGENT

# Serve it
agenthost serve my-agent
```

## Configuration (`agent.yaml`)

```yaml
model: "moonshotai/Kimi-K2.5"       # LLM model
host: "127.0.0.1"                    # Server bind address
temperature: 0.7                     # LLM temperature
base_url: "https://api.deepinfra.com/v1"  # API base URL
max_tokens: 12000                    # Max response tokens
thinking: "low"                      # Reasoning effort: low, medium, high
orchestrator: false                  # Enable orchestrator mode
frequency_penalty: 0.7               # Token frequency penalty
presence_penalty: 0.7                # Token presence penalty

embedding:
  model: "BAAI/bge-m3"              # Embedding model for RAG
  base_url: "https://api.deepinfra.com/v1"

memory:
  mode: "rag"                        # Memory mode: rag or off
  recent_messages: 8                 # Recent messages included verbatim
  chunk_size: 512                    # Tokens per RAG chunk
  budget_tokens: 6000                # Max tokens for retrieved context
  max_chunks: 12                     # Max chunks per retrieval
  max_pool_chunks: 5000              # Max chunks stored in memory
  similarity_weight: 0.6             # RAG scoring weight
  recency_weight: 0.3                # Recency scoring weight
  role_weight: 0.1                   # Role scoring weight
  mmr_lambda: 0.7                    # MMR diversity parameter

extra:
  builtin_tools:                     # Opt-in built-in tools
    - events                         # event_tool for scheduled events
    - discord                        # send_discord, edit_discord_message
    - thread                         # get_current_thread_id
    - filesystem                     # read_file, list_uploads
    - skill_crud                     # skill_crud for skill management
    - toolbox                        # toolbox for switching toolboxes
  whatsapp_bridge_url: "http://127.0.0.1:9001/send"
  discord_bridge_url: "http://127.0.0.1:9002/send"
```

Config merging order: `config.py DEFAULT_CONFIG` → `~/.agenthost/default_agent.yaml` → `agent.yaml`.

## Toolboxes

A toolbox is a reusable package that bundles tools and skills. It has the same internal structure as an agent's `tools/` and `skills/` directories.

```
toolboxes/
└── <name>/
    ├── tools/
    │   └── *.py          # Top-level functions become tools
    └── skills/
        └── *.md          # Markdown skills
```

An agent loads one toolbox at a time via the `toolbox` built-in tool (requires `builtin_tools: [toolbox]` in `agent.yaml`):

- `toolbox(action="list")` — list all available toolboxes.
- `toolbox(action="list", target="<name>")` — inspect a specific toolbox's tools and skills.
- `toolbox(action="switch", target="<name>")` — activate a toolbox, replacing current agent tools/skills.
- `toolbox(action="switch", target="")` — revert to the agent's default tools and skills.

Built-in tools are always preserved during toolbox switches. Memory and conversation state persist across switches.

### Built-in Toolboxes

| Toolbox | Description |
|---------|-------------|
| `example` | Sample toolbox with a `greet` tool and `greeting` skill. |
| `researcher` | Web search tools and skills (DuckDuckGo + Crawl4AI). |
| `document_writer` | Document generation tools: CSV, XLSX, DOCX, and Markdown output. |

## Built-in Agents

| Agent | Description |
|-------|-------------|
| `RESEARCHER` | Web research agent with DuckDuckGo search and web page crawling. |
| `DOCUMENT_WRITER` | Document creation agent for CSV, Excel, Word, and Markdown files. |
| `ORCHESTRATOR` | Multi-agent task router that delegates to other running agents. |
| `GMAIL_AGENT` | Gmail inbox automation: search, read, send, label, and manage email. |
| `GENERAL` | General-purpose conversational agent with no extra tools. |
| `TODO` | Task and to-do list manager with persistent SQLite storage. |

## Integrations

### Discord

```bash
cd integrations/discord
cp .env.example .env   # Set DISCORD_BOT_TOKEN
../../venv/bin/python main.py
```

Supports mention triggers, prefix commands (`!ai`), DMs, file attachments with text extraction, access control (user/guild/channel IDs), and outbound message endpoints for scheduled events (see `integrations/discord/README.md`).

### Telegram

```bash
cd integrations/telegram
cp .env.example .env   # Set TELEGRAM_BOT_TOKEN
../../venv/bin/python main.py
```

Long-polling bot with username-based access control, proxy support, and Markdown replies (see `integrations/telegram/README.md`).

### WhatsApp

```bash
cd integrations/whatsapp
npm install
cp .env.example .env
# Requires Node.js >= 20
npm run dev
```

QR-paired Baileys bridge; scan the QR code once, then chat with agents from your WhatsApp self-chat. Includes outbound send endpoint for scheduled events (see `integrations/whatsapp/README.md`).

## Runtime Data Directory (`~/.agenthost`)

| Path | Description |
|------|-------------|
| `agents.yaml` | Alias → agent folder mapping |
| `.agenthost-registry.json` | Running-agent registry (PID, host, port) |
| `keys.kdbx` | KeePass database of API keys and secrets |
| `agenthost.log` | Persistent debug log |
| `default_agent.yaml` | Global default agent configuration |
| `uploads/` | Files uploaded by users (e.g. via Discord) |
| `output/` | Agent-generated output |
| `memory/<agent>/memory.db` | Per-agent SQLite memory + RAG embeddings |

Override the home directory with `AGENTHOST_HOME`.

## Technology Stack

- **Language**: Python 3.10+
- **Web server**: FastAPI + uvicorn with SSE streaming
- **LLM client**: `openai` SDK (OpenAI-compatible)
- **Embeddings**: OpenAI-compatible API (default: `BAAI/bge-m3` via DeepInfra)
- **Memory/RAG**: SQLite (`sqlite3`) + optional `numpy`/`tiktoken`
- **Scheduling**: APScheduler
- **Secrets**: `pykeepass` for `.kdbx` management
- **TUI**: `textual` + `httpx`
- **Bridges**: Discord (`discord.py`), Telegram (`httpx`), WhatsApp (Node.js + Baileys)
- **Document processing**: `python-docx`, `pypdf`, `pandas`, `openpyxl`, `tabulate`

## Development

```bash
# Verify imports
venv/bin/python -c "import agenthost; import agenthost.cli; import agenthost.server; import agenthost.agent; print('imports ok')"

# Verify CLI
venv/bin/python -m agenthost --help

# Enable debug logging (default: on)
export AGENTHOST_DEBUG=1
# Logs written to ~/.agenthost/agenthost.log

# Run a local agent for testing
agenthost serve RESEARCHER
agenthost chat --agent RESEARCHER --no-tui
```

See [DEV_GUIDE.md](DEV_GUIDE.md) for a detailed architecture map, import graph, and module-by-module breakdown. See [AGENTS.md](AGENTS.md) for coding conventions and common development tasks.