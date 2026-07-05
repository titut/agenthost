# Description

Operational basics for managing agent processes: spawn, message, despawn, and reuse.

# Agent Lifecycle Guidance

This skill covers the operational basics of managing agent processes.
For planning, decomposition, and execution strategy, see **planning.md**.

---

## Reading Agent Folders

- Read multiple agents before choosing one. Call `read_agent_folder("<name>")`
  to inspect an agent's persona, tools, and skills.
- Re-read an agent folder if you are unsure of its capabilities.
- You can read agent folders without spawning them — this is free.

## Choosing an Agent

Match the user's task to the agent's **actual documented capabilities** — do
not assume from the agent name alone. Read the persona (WHOAMI.md), the tools
(definitions + docstrings), and the skills to decide.

If no existing agent fits the task, explain the gap to the user.

## Managing the 3-Agent Limit

- Before spawning, always call `list_agents()` to check current usage.
- If at the limit, finish and despawn an idle agent before spawning a new one.
- Despawn agents as soon as their final step is complete.

## Conversation Memory Per Agent

- `send_message` reuses the same `thread_id` for the lifetime of a spawned
  agent. The agent remembers all messages exchanged while it is running.
- When you despawn an agent, its conversation memory is gone.
- A newly spawned agent (even with the same name) starts with a fresh thread.
- When sending a task to a newly spawned replacement, include a concise summary
  of prior progress — it has no memory of the previous session.

## Agent Reuse

If the same agent type is needed for consecutive steps, **keep the process
alive**. Do not despawn and re-spawn. Just send a new message to the same
`agent_id`. The agent retains context from earlier messages in the same thread.

## Error Handling

- If `send_message` reports an agent is not responding, check its health via
  health endpoint, then despawn and either re-spawn or use an alternative agent.
- If `spawn_agent` fails, run `agenthost list` manually to see if the agent
  started despite the error.
- Never read `.agenthost-registry.json` directly — use the provided tools.

## Domain Independence

This agent lifecycle system is domain-agnostic. The agents you manage could be
researchers, writers, analysts, designers, verifiers, or any role. Judge each
agent by its documented capabilities, not its folder name.
