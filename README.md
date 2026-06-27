# agenthost

A minimal, folder-based multi-agent host.

Each agent is just a folder containing its identity, configuration, tools, skills, and memory — making agents portable, shareable, and trivially reproducible.

```text
RESEARCHER/
├── WHOAMI.md          # System prompt / agent identity
├── agent.yaml         # Configuration (model, host, temperature, etc.)
├── tools/             # Python tool files (auto-discovered)
├── skills/            # Markdown skill instructions (injected into system prompt)
└── memory/            # SQLite database (auto-created on first run)
```

---

## Architecture

### Folder-as-Agent Pattern

Every agent is a self-contained directory under `agents/`. The framework reads the folder at startup and assembles the agent from its parts:

| Component  | File                     | Purpose                                                          |
|------------|--------------------------|------------------------------------------------------------------|
| Identity   | `WHOAMI.md`              | The system prompt — describes the agent's role, personality, rules |
| Config     | `agent.yaml`             | Model, host, temperature, base URL, memory limits, optional port |
| Tools      | `tools/*.py`             | Runnable Python functions exposed to the LLM as tools            |
| Skills     | `skills/*.md`            | Markdown documents appended to the system prompt as guidance     |
| Memory     | `memory/memory.db`       | SQLite database with conversation history and KV store           |

### Multi-Agent Orchestration

agenthost supports **coordinated multi-agent workflows** via the **ORCHESTRATOR** agent. A typical pipeline:

1. A user gives a high-level task to the ORCHESTRATOR
2. The ORCHESTRATOR decomposes it into phases and steps
3. It reads agent folders to match capabilities to each step
4. It presents the plan for user approval
5. It spawns agents, sends them messages, gathers results, and despawns them
6. It adapts on failure, retries when appropriate, and synthesizes final output

Running agents are tracked in a process registry at `~/.agenthost-registry.json` with PID-based health filtering. Zombie entries are automatically cleaned up on `list`.

### Design → Build → Test Pipeline

The five agents form a complete design-build-test lifecycle:

```
SYS_ENG ──produces──► architecture spec ──► SW_DEV ──implements──► code ──► TEST_ENG ──verifies──► test report
                                         ▲                                                        │
                                         └──────────── ORCHESTRATOR coordinates ───────────────────┘
```

1. **SYS_ENG** produces architecture documents in `output/architecture/`
2. **SW_DEV** implements from architecture specs using file tools with automatic backups
3. **TEST_ENG** writes test plans in `output/test-plans/` and executable tests
4. **ORCHESTRATOR** coordinates the entire lifecycle, from request to delivery

---

## Quick Start

### Install

```bash
pip install -e .
```

Requires Python ≥ 3.10. See `pyproject.toml` for full dependency list.

### Serve an Agent

```bash
agenthost serve agents/RESEARCHER
```

Ports are auto-assigned starting at `8000`. The CLI prints the URL on startup.

### Chat with an Agent

```bash
agenthost chat --agent RESEARCHER
```

This starts an interactive streaming REPL. Type messages, see streamed responses, and continue the thread until you run `/quit`.

Or with curl:

```bash
curl -N -H "Accept: text/event-stream" \
  -H "Content-Type: application/json" \
  -d '{"message": "What is 12 * 12?"}' \
  http://localhost:8000/chat
```

The `-H "Content-Type: application/json"` is required; without it `curl -d` sends form-encoded data and FastAPI rejects the request.

---

## CLI Commands

### `agenthost serve <agent_folder>`

Serve an agent on an HTTP server with a streaming chat endpoint.

```bash
agenthost serve agents/RESEARCHER
```

- Port auto-assigns from 8000 if not specified in `agent.yaml`
- Registers the agent in the process registry
- Prints the URL and model info on startup
- Clears conversation history on each serve (fresh start)

### `agenthost chat --agent <name>`

Interactive streaming REPL with a running agent.

```bash
agenthost chat --agent RESEARCHER
agenthost chat --agent RESEARCHER "What is the capital of France?"
```

Options:

| Flag          | Description                                               |
|---------------|-----------------------------------------------------------|
| `--agent`     | Agent name; looks up the running port automatically       |
| `--port`      | Agent port (alternative to `--agent`)                     |
| `--host`      | Agent host (default: `127.0.0.1`)                         |
| `--url`       | Full chat endpoint URL; overrides `--host`/`--port`       |
| `--thread`    | Continue an existing conversation thread                  |
| `--once`      | Send a single message and exit (requires `--message`)     |

The CLI streams content, tool calls, and tool results in real time. REPL commands:

- `/quit`, `/exit`, `/q` — End the session
- `/help` — Show available commands
- `/thread` — Show the current thread ID

### `agenthost list`

Show all currently running agents.

```bash
agenthost list
```

Outputs a table of `Name`, `Host`, `Port`, `PID`, and `Path`. Stale entries (dead PIDs) are automatically pruned.

### `agenthost key list|add|edit`

Manage secrets in the KeePass `.kdbx` database.

```bash
agenthost key list                          # List all key names
agenthost key add --name SERVICE --value sk-...    # Add a new key
agenthost key edit --name SERVICE           # Edit an existing key (prompts for new value)
```

All subcommands support:

| Flag          | Description                                                |
|---------------|------------------------------------------------------------|
| `--db`        | Path to `.kdbx` file (default: `keys.kdbx`)                |
| `--password`  | Master password (prompts securely if omitted)              |

The database is created automatically on first `add`. On `edit`, if `--name` is omitted, an interactive selection menu is shown.

---

## Available Agents

### ORCHESTRATOR — Generalist Planning & Lifecycle Manager

The ORCHESTRATOR takes any request, decomposes it into ordered steps, assigns the right agents, gets user approval, and executes the plan while tracking progress and adapting to failures.

**Personality:** Domain-agnostic, methodical, user-in-the-loop.

**Tools:**
- `spawn_agent(name)` — Start an agent process (max 3 concurrent)
- `send_message(agent_id, message)` — Give a task to a running agent
- `despawn_agent(agent_id)` — Stop an agent gracefully
- `list_agents()` — Check which agents are currently running
- `list_available_agents()` — Discover what agent folders exist
- `read_agent_folder(name)` — Inspect an agent's persona, tools, and skills
- `plan_save(key, json)` — Store a plan in the KV store
- `plan_load(key)` — Retrieve a stored plan
- `plan_delete(key)` — Remove a plan from storage

**Skills:** `planning.md` — coarse-to-fine decomposition, agent matching, execution tracking; `agent-lifecycle.md` — spawn/monitor/despawn patterns.

**Config:** Model `deepseek-v4-flash`, temperature `0.3`, max 3 concurrent agents.

### RESEARCHER — Research Assistant

A curious research assistant that finds information, summarizes topics, and thinks step by step.

**Tools:**
- `search_web(query)` — DuckDuckGo web search (no API key required)
- `calculate(expression)` — Evaluate mathematical expressions

**Skills:** `web-search.md` — guidance on when and how to use search tools.

### SYS_ENG — Systems Engineer

Designs, builds, and operates production infrastructure and platforms. Produces architecture documents.

**Personality:** Pragmatic, paranoid about failure, automation-obsessed, cost-aware.

**Tools:**
- `read_directory_tree(path, max_depth)` — Explore repo structure
- `read_file(path, max_lines)` — Read source and config files
- `write_file(path, content, output_dir)` — Write architecture documents to `output/architecture/`

**Output:** Architecture documents in `output/architecture/`.

### SW_DEV — Senior Developer

Turns architecture specs into clean, working, production-grade code. Reads before writing, follows existing conventions, verifies every deliverable.

**Personality:** Methodical, convention-respecting, quality-obsessed, pragmatically ambitious.

**Tools:**
- `read_directory_tree(path, max_depth)` — Explore repository structure
- `read_file(path, max_lines)` — Read source, specs, and configs
- `write_file(path, content)` — Create new files (with automatic backups)
- `edit_file(path, old_string, new_string)` — Targeted snippet replacement (with automatic backups)

**Backup system:** Every `write_file` and `edit_file` operation creates a timestamped backup under `.agenthost/backups/<timestamp>/<path>` before overwriting, enabling easy rollback.

**Skills:** `development-patterns.md` — when to create vs. extend, conventions, quality standards.

**Output:** Implementation code in `src/agenthost/` and agent tool files.

### TEST_ENG — Test Engineer

Rigorous, comprehensive test engineer who produces exhaustive test plans and executable tests from architecture specs and source code.

**Personality:** Adversarial, exhaustive, literal, evidence-driven. "Trust nothing, verify everything."

**Tools:**
- `read_directory_tree(path, max_depth)` — Explore repo structure
- `read_file(path, max_lines)` — Read source, specs, and configs
- `write_file(path, content, output_dir)` — Write test plans to `output/test-plans/`

**Skills:** `test-coverage.md` — how to identify coverage gaps and adversarial test cases.

**Output:** Test plans in `output/test-plans/`.

---

## Agent Pipeline: Design → Build → Test

The five agents work together in a structured pipeline coordinated by the ORCHESTRATOR:

```
User Request
     │
     ▼
┌─────────────────────────────────────────────────────┐
│                   ORCHESTRATOR                      │
│  • Decomposes request into phases and steps          │
│  • Reads agent folders to match capabilities         │
│  • Presents plan to user for approval                │
│  • Spawns agents, tracks progress, handles failures  │
│  • Max 3 concurrent agents                           │
│  • Uses SQLite KV store for plan persistence         │
└─────────────────────────────────────────────────────┘
     │
     ├──► SYS_ENG: Produces architecture specs ──────► output/architecture/
     ├──► SW_DEV: Implements from specs ─────────────► src/agenthost/
     └──► TEST_ENG: Writes test plans & tests ───────► output/test-plans/
```

Each agent can also be used independently. The pipeline is a convention, not a constraint.

---

## Writing Custom Tools

Drop a Python file in an agent's `tools/` directory. Any top-level function with a docstring (that does not start with `_`) becomes a tool automatically:

```python
def weather(city: str) -> str:
    """Get the weather for a city."""
    return f"It's sunny in {city}."
```

**Rules:**
- Files starting with `_` are ignored (use `_helpers.py` for internal modules)
- Functions starting with `_` are ignored
- Type annotations are used for JSON schema generation and argument coercion
- Both sync and async functions are supported (sync functions run in a thread pool)
- Return strings or JSON-serializable objects

The RESEARCHER agent ships with a free web search tool (`search_web`) powered by DuckDuckGo via the `ddgs` package — no API key required.

---

## Writing Custom Skills

Drop markdown files in an agent's `skills/` directory. agenthost loads them and injects them into the agent's system prompt under a **"Skills"** section:

```markdown
# my-skill.md

When using the search tool, always verify the domain name before trusting the result.
```

The agent sees this guidance in every conversation. Skills are loaded alphabetically by filename.

---

## Agent Configuration

Each agent has an `agent.yaml`:

```yaml
name: RESEARCHER
model: gpt-4o-mini
host: 127.0.0.1          # 127.0.0.1 = local only; 0.0.0.0 = accessible from network
temperature: 0.7
base_url: https://api.openai.com/v1
max_memory_turns: 50
port: 8000               # Optional: pin a specific port
```

| Field               | Default              | Description                                        |
|---------------------|----------------------|----------------------------------------------------|
| `name`              | Folder name          | Agent identity                                     |
| `model`             | `gpt-4o-mini`        | LLM model identifier                               |
| `host`              | `127.0.0.1`          | Bind address                                       |
| `temperature`       | `0.7`                | LLM temperature (0.0 – 1.0)                        |
| `base_url`          | `https://api.openai.com/v1` | OpenAI-compatible API endpoint            |
| `max_memory_turns`  | `50`                 | Max conversation turns kept in memory (0 = unlimited) |
| `port`              | `auto`               | Explicit TCP port (auto-assigned from 8000 if omitted) |

Any extra fields in `agent.yaml` are stored in `config.extra` and can be read by custom tool code.

---

## Memory System

Each agent gets an auto-created SQLite database at `memory/memory.db` with two tables:

### `messages` — Conversation History

| Column        | Type    | Purpose                                       |
|---------------|---------|-----------------------------------------------|
| `id`          | INTEGER | Auto-incrementing primary key                 |
| `thread_id`   | TEXT    | Conversation thread identifier                |
| `role`        | TEXT    | `system`, `user`, `assistant`, or `tool`      |
| `content`     | TEXT    | Message content (nullable for tool calls)     |
| `tool_calls`  | TEXT    | JSON-serialized tool call requests            |
| `tool_call_id`| TEXT    | Matches tool call to tool result              |
| `name`        | TEXT    | Tool name (for tool role messages)            |
| `created_at`  | DATETIME| Auto-set timestamp                            |

Messages are automatically trimmed to `max_memory_turns` (configurable in `agent.yaml`) by removing the oldest entries when the limit is exceeded.

### `kv` — Key/Value Store

| Column       | Type    | Purpose                      |
|--------------|---------|------------------------------|
| `key`        | TEXT    | Primary key                  |
| `value`      | TEXT    | JSON-serialized value        |
| `updated_at` | DATETIME| Auto-set timestamp           |

The ORCHESTRATOR agent uses the KV store for plan persistence (`plan_save`/`plan_load`/`plan_delete`).

On serve startup, the conversation history is cleared (fresh start).

---

## Backup System

SW_DEV's `write_file` and `edit_file` tools automatically create timestamped backups before any write operation:

```
.agenthost/backups/20250405-143022/agents/SW_DEV/tools/edit.py
.agenthost/backups/20250405-143022/README.md
```

This enables easy rollback. Backups are stored under `.agenthost/backups/<timestamp>/<relative-path>`.

---

## Secrets Management

API keys and other secrets are stored in a KeePass `.kdbx` file (`keys.kdbx` at the project root) using the pure-Python `pykeepass` library.

### CLI Management

```bash
agenthost key list                    # List all stored keys
agenthost key add --name SERVICE      # Add a new key (prompts for value)
agenthost key edit --name SERVICE     # Update an existing key
```

### Environment Loading

On `serve` and `chat` startup, the CLI automatically loads the `OPENAI_API_KEY` from the KeePass database into the environment:

```python
load_keepass_env("keys.kdbx", "OPENAI_API_KEY", master_password)
```

The master password is hardcoded in `cli.py` (`"c1bc0bgq"`) for the auto-load. For manual operations, the `key` subcommand prompts securely via `getpass`.

---

## Health Check

Every agent server exposes a `/health` endpoint:

```bash
curl http://localhost:8000/health
```

Response:

```json
{
  "status": "ok",
  "agent": "RESEARCHER",
  "model": "gpt-4o-mini",
  "tools": ["search_web", "calculate"]
}
```

---

## Process Registry

Running agents are tracked in `.agenthost-registry.json` at the project root:

```json
[
  {
    "pid": 12345,
    "name": "RESEARCHER",
    "path": "/home/user/project/agents/RESEARCHER",
    "host": "127.0.0.1",
    "port": 8000,
    "started_at": 1743864000.0
  }
]
```

- Agents register on startup (`register_agent`) and unregister on shutdown (`unregister_agent`)
- `list_agents()` filters out entries whose PID is no longer alive, and automatically cleans stale entries
- Zombie processes are detected via `/proc/<pid>/status` on Linux

---

## Source Structure

```
src/agenthost/
├── __init__.py      # Package metadata (__version__ = "0.1.0")
├── __main__.py      # `python -m agenthost` entry point
├── cli.py           # CLI: serve, chat, list, key subcommands
├── config.py        # AgentConfig: loads agent.yaml, builds system prompt
├── agent.py         # Agent class: LLM loop, tool invocation, memory integration
├── server.py        # FastAPI server: /health, /chat (SSE streaming)
├── memory.py        # AgentMemory: SQLite-backed conversation + KV store
├── tools.py         # Tool discovery and in-process execution
├── skills.py        # Skill loading from markdown files
├── registry.py      # Process registry (.agenthost-registry.json)
└── secure_key.py    # KeePassDB: list, add, get, update keys in .kdbx
```

### Module Responsibilities

| Module        | Lines | Purpose                                           |
|---------------|-------|---------------------------------------------------|
| `cli.py`      | 421   | Argument parsing and dispatch for all subcommands |
| `agent.py`    | 100   | Core agent loop: LLM streaming + tool execution   |
| `server.py`   | 110   | FastAPI app with SSE streaming chat endpoint      |
| `config.py`   | 107   | Agent folder parsing, system prompt assembly      |
| `tools.py`    | 169   | Auto-discovery of Python tool files, JSON schema generation, argument coercion |
| `memory.py`   | 135   | SQLite-backed messages + KV store with auto-trim  |
| `registry.py` | 116   | JSON process registry with PID health checks      |
| `secure_key.py`| 178  | KeePass database management via pykeepass         |
| `skills.py`   | 19    | Markdown skill file loader                        |

---

## Dependencies

From `pyproject.toml`:

| Package     | Minimum Version | Purpose                             |
|-------------|-----------------|-------------------------------------|
| `fastapi`   | 0.110.0         | HTTP server framework               |
| `uvicorn`   | 0.29.0          | ASGI server                         |
| `openai`    | 1.30.0          | OpenAI-compatible LLM API client    |
| `pydantic`  | 2.7.0           | Request/response models             |
| `pyyaml`    | 6.0.1           | YAML config parsing                 |
| `httpx`     | 0.27.0          | HTTP client (chat streaming)        |
| `ddgs`      | 9.0             | DuckDuckGo search (no API key)      |
| `pykeepass` | 4.1.0           | KeePass .kdbx file access           |
