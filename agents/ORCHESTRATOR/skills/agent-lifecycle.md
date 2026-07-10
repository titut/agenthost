# Description

Operational basics for working with pre-running agents: discovery, messaging, reuse, and graceful failure handling.

# Agent Lifecycle Guidance

This skill covers the operational basics of delegating work to specialist agents.
For planning, decomposition, and execution strategy, see **planning.md**.

---

## Reading Agent Folders

- Read multiple agents before choosing one. Call `read_agent_folder("<name>")`
  to inspect an agent's persona, tools, and skills.
- Re-read an agent folder if you are unsure of its capabilities.
- You can read agent folders without messaging them — this is free.

## Choosing an Agent

Match the user's task to the agent's **actual documented capabilities** — do
not assume from the agent name alone. Read the persona (WHOAMI.md), the tools
(definitions + docstrings), and the skills to decide.

If no existing agent fits the task, explain the gap to the user.

## Discovering Running Agents

- Agents are started outside the ORCHESTRATOR (e.g., by a supervisor or manually).
- Call `list_agents()` to see which agents are currently running, their host, and
  their port.
- If an agent you need is not running, tell the user which agent is missing and
  that it needs to be started. Do not try to start it yourself.

## Conversation Memory Per Agent

- `send_message(agent_name, message)` uses the same `thread_id` as the current
  ORCHESTRATOR conversation. The specialist agent shares the thread context with
  this conversation.
- Agents remember all messages exchanged in the same thread.
- If you need to reset an agent's memory for the current thread, ask the user to
  run `!clear` (Discord) or `/clear` (TUI).

## Agent Reuse

If the same agent type is needed for consecutive steps, just call
`send_message(agent_name, message)` again. The agent retains context from earlier
messages in the same thread.

## Error Handling

- If `send_message` reports an agent is not responding, inform the user briefly
  and offer to retry, try a different agent, or continue without that agent.
- If `list_agents()` does not show the agent you need, tell the user it needs to
  be started.
- Never read `.agenthost-registry.json` directly — use the provided tools.

## Domain Independence

This agent lifecycle system is domain-agnostic. The agents you manage could be
researchers, writers, analysts, designers, verifiers, or any role. Judge each
agent by its documented capabilities, not its folder name.
