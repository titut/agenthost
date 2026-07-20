# agenthost Discord Bridge

A simple Discord bot bridge for agenthost agents.

## Why Discord?

Discord is reliable, bot-first, and doesn't have the phone-number / LID complexity of WhatsApp or the regional network issues some VPS providers have with Telegram.

Trade-offs:

- You need a Discord account and a bot application.
- The bot must be invited to a server with the right permissions.

## Setup

1. Go to the [Discord Developer Portal](https://discord.com/developers/applications) and create a new application.

2. In **Bot** settings:
   - Click **Reset Token** and copy the token.
   - Scroll down to **Privileged Gateway Intents** and enable:
     - **Message Content Intent**
     - (Direct Message intent is enabled by default for bots; no extra toggle needed)

3. In **OAuth2 → URL Generator**:
   - Select scope `bot`.
   - Select permissions:
     - Send Messages
     - Attach Files
     - Read Message History
     - Read Messages / View Channels
   - Copy the generated URL and open it in your browser to invite the bot to your server.

4. Copy and edit the environment file:

```bash
cd integrations/discord
cp .env.example .env
# edit .env and set DISCORD_BOT_TOKEN
```

5. Make sure the agent is running:

```bash
# from the repo root
agenthost serve RESEARCHER
```

6. Run the bridge inside the agenthost virtual environment:

```bash
../../venv/bin/python main.py
```

Or, if you installed the requirements globally:

```bash
python main.py
```

## Usage

### In a server (guild)

Mention the bot in a message, e.g.:

```
@MyBot explain recursion like I'm five
```

The bot strips the mention, forwards the rest to the agent, and replies in the same channel.

You can also set a prefix trigger:

```env
DISCORD_GUILD_PREFIX=!ai
```

Then:

```
!ai explain recursion like I'm five
```

### In DMs

Send the bot a direct message. It forwards every DM to the agent and replies.

### Commands

- `!id` — the bot replies with your Discord user ID (useful for `ALLOWED_USER_IDS`).
- `!start` — shows help.
- `!stop` — cancels an in-progress agent response in the current channel.
- `!clear` — clears conversation memory for all agents in the current channel.

## Access control

To restrict the bot to specific users, set their Discord user IDs (numeric):

```env
ALLOWED_USER_IDS=123456789012345678,987654321098765432
```

Messages from anyone else are ignored.

To restrict the bot to specific servers or channels:

```env
ALLOWED_GUILD_IDS=123456789012345678
ALLOWED_CHANNEL_IDS=123456789012345678
```

## Outbound Discord messages (scheduled events)

The bridge exposes a small HTTP server at `http://127.0.0.1:9002/send`. The agent can call the built-in `send_discord(text, thread_id, file_path='')` tool (enabled via `agent.yaml` `builtin_tools: [discord]`) to POST messages and optional file attachments to that endpoint.

The `thread_id` is a Discord channel ID (or DM channel ID). For example:

```json
POST /send
{
  "text": "hello",
  "thread_id": "123456789012345678"
}
```

This is useful for scheduled events: the event prompt tells the agent to run a task and then call `send_discord` with the result.

In the agent's `agent.yaml`:

```yaml
extra:
  builtin_tools: [events, discord]
  discord_bridge_url: http://127.0.0.1:9002/send
```

The agent uses the unified `event_tool(action="add", action_name="...", ...)` tool to manage events.

## File Attachments

You can upload files directly to the bot. The bridge downloads the file and saves it to the shared `uploads/` folder. `read_file` can then read the file directly, transparently extracting text from supported formats.

Supported formats:

- `.docx` — Word documents
- `.pdf` — PDF files
- `.xlsx` — Excel spreadsheets (converted to Markdown tables)
- `.csv` — CSV files (converted to Markdown tables)
- `.md` / `.txt` — plain text

Example:

```
@MyBot summarize this report
[attach report.pdf]
```

The bot will reply with a confirmation like:

> 📎 Saved 1 attachment(s) to the uploads folder.

and the agent sees a preamble in its prompt:

```markdown
User uploaded: uploads/report.pdf
Extracted text: uploads/report.pdf.txt

summarize this report
```

Agents with the `filesystem` built-in tool (e.g. ORCHESTRATOR) can call `read_file("uploads/report.pdf.txt")` to read the extracted text.

## Notes

- Each Discord channel gets its own `thread_id`:
  - DMs: the DM channel ID
  - Guild channels: the channel ID
- Replies longer than 2000 characters are split into multiple Discord messages.
- If you send a new message while the agent is still responding, you'll get a busy notice.
