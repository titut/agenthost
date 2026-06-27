# agenthost

A minimal, folder-based agent host.

Each agent is just a folder:

```
RESEARCHER/
├── WHOAMI.md          # System prompt
├── agent.yaml         # Config (model, host, temperature, etc.)
├── tools/             # Python tool files
├── skills/            # Markdown skill instructions
└── memory/            # SQLite memory (auto-created)
```

## Install

```bash
pip install -e .
```

## Run an agent

```bash
agenthost serve agents/RESEARCHER
```

Ports are assigned automatically starting at `8000`. The CLI prints the URL on startup.

Configure the agent in `agents/RESEARCHER/agent.yaml`:

```yaml
name: RESEARCHER
model: gpt-4o-mini
host: 127.0.0.1          # 127.0.0.1 = local only; 0.0.0.0 = accessible from network
temperature: 0.7
base_url: https://api.openai.com/v1
max_memory_turns: 50
```

To pin a specific port, add `port: 8000` to `agent.yaml`.

## List running agents

```bash
agenthost list
```

Shows every active agent, its dynamically assigned port, PID, and path.

## Chat with the agent

Use the built-in streaming CLI for a continuous conversation:

```bash
agenthost chat --agent RESEARCHER
```

`--agent` looks up the running port automatically. You can also use `--port` or `--url` directly:

```bash
agenthost chat --port 8000
agenthost chat --url http://localhost:8000/chat
```

This starts an interactive REPL. Type messages, see streamed responses, and continue the thread until you run `/quit`.

You can also pass an optional first message on the command line:

```bash
agenthost chat --agent RESEARCHER "What is 12 * 12?"
```

Options:

- `--agent` — agent name; looks up the running port automatically
- `--port` — agent port
- `--host` — agent host (default: 127.0.0.1)
- `--url` — full chat endpoint URL, overrides `--host`/`--port`
- `--thread` — continue an existing thread
- `--once` — send a single message and exit

The CLI streams content, tool calls, and tool results in real time. REPL messages share the same conversation thread. Pass `--thread` to isolate or continue conversations.

Or with curl:

```bash
curl -N -H "Accept: text/event-stream" \
  -H "Content-Type: application/json" \
  -d '{"message": "What is 12 * 12?"}' \
  http://localhost:8000/chat
```

The `-H "Content-Type: application/json"` is required; without it `curl -d` sends form-encoded data and FastAPI rejects the request.

## Health check

```bash
curl http://localhost:8000/health
```

## Writing tools

Drop a Python file in `tools/`. Any top-level function with a docstring becomes a tool:

```python
def weather(city: str) -> str:
    """Get the weather for a city."""
    return f"It's sunny in {city}."
```

The example `RESEARCHER` agent includes a free web search tool powered by DuckDuckGo (via the `ddgs` package). No API key is required.

## Writing skills

Drop markdown files in `skills/`. agenthost reads them and injects them into the agent's system prompt under a "Skills" section.

For example, `skills/web-search.md` could explain when and how to use the `search_web` tool. The LLM will see this guidance in every conversation.

## Secrets

The only thing kept outside the agent folder is the API key:

- `OPENAI_API_KEY` — required

Everything else (model, host, temperature, base_url, memory) lives in the agent's `agent.yaml`.
