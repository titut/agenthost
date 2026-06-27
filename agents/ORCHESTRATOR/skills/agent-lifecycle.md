# Agent Lifecycle Guidance

## When to Read Agent Folders

- Read multiple agents before choosing one. E.g., if the user asks for
  research, read RESEARCHER and any other relevant agents.
- Re-read an agent folder if you're unsure of its capabilities.
- You can read agent folders without spawning them.

## How to Choose an Agent

Match the user's task to the agent's persona and tools:
- Research tasks → RESEARCHER
- Coding tasks → SW_DEV
- Testing tasks → TEST_ENG

If no existing agent fits, explain the gap to the user.

## Managing the 3-Agent Limit

- Before spawning, always check `list_agents()`.
- If at the limit, offer to despawn an idle agent.
- Despawn agents as soon as their task is complete.

## Conversation Memory

- `send_message` reuses the same `thread_id` for the lifetime of a spawned agent.
- The spawned agent remembers all messages exchanged while it is running.
- When you despawn an agent, its conversation memory is gone.
- A newly spawned agent (even with the same name) starts with a fresh thread.
- When spawning a replacement, include a concise summary of prior progress.

## Error Handling

- If an agent does not respond, despawn it and report to the user.
- If `spawn_agent` fails, check `agenthost list` to see if the agent started anyway.
- If an agent task fails, try re-spawning or use a different agent.
- Never read `.agenthost-registry.json` directly — use `agenthost list`.
