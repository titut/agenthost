# Description

Background memory curator. Reads conversation threads from monitored agents, summarizes them into dated daily logs, and maintains a living master memory document about the user.

# Capabilities

- Read other agents' `memory.db` files using the monitored agent list.
- Summarize recent conversation activity into daily logs.
- Extract and update long-term facts about the user in the master memory document.
- Track a high-water mark so the same messages are not summarized twice.

# How to Use This Agent

1. **Discover** — identify monitored agents with recent messages since the last run.
2. **Read** — fetch those messages grouped by date and thread.
3. **Summarize** — produce concise daily log entries and user fact updates.
4. **Persist** — append to the daily log and rewrite the master memory document.
5. **Advance** — update the `last_summarized_at` watermark.

# Key Rules

- Only read agents listed in `agent.yaml` under `extra.monitored_agents`.
- Never read the USER_MEMORY agent's own database.
- Treat `created_at` timestamps as authoritative UTC values.
- Keep master memory deduplicated; merge new facts with existing ones.
