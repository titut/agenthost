# agenthost WhatsApp Bridge

QR-paired WhatsApp bridge for agenthost agents, powered by [Baileys](https://github.com/WhiskeySockets/Baileys).

This lets you chat with a served agent from your personal WhatsApp number. No Meta Business API, no webhooks, no public HTTPS endpoint required.

## How it works

1. This Node.js service pairs with your personal WhatsApp via QR code (Linked Devices).
2. It listens for incoming WhatsApp messages.
3. Each message is forwarded to a running agenthost agent's `/chat` endpoint.
4. The agent's reply is sent back to the same WhatsApp chat.

## Prerequisites

- Node.js >= 20
- An agenthost agent already running locally or on your VPC

## Setup

```bash
cd integrations/whatsapp
npm install
```

Copy the example environment file and edit it:

```bash
cp .env.example .env
```

See `.env.example` for all available options.

## Run

Make sure the agent is running first:

```bash
# from the repo root
agenthost serve RESEARCHER
```

Then start the bridge:

```bash
npm run dev
```

On first run, a QR code appears in the terminal. Open WhatsApp on your phone, go to **Settings → Linked Devices → Link a Device**, and scan the code.

The session is saved to `./auth_state`, so you normally only need to scan once.

## Usage

Send a text message to your own WhatsApp number from any other WhatsApp account. The bridge forwards it to the agent and replies in the same chat.

You can also message **yourself** from your own phone by setting:

```env
RESPOND_TO_FROM_ME=true
```

The bridge forwards your messages to the agent and replies with an `/ai` prefix so you can tell which messages came from the AI. The bridge ignores any message that starts with `/ai` or `/system`, so it never replies to its own messages.

If you only want the agent to respond when you message **yourself** (not when you message other contacts), set:

```env
WHATSAPP_SELF_JID=84623824551941@lid
```

Your self-chat JID is often a privacy LID. Check the bridge logs when you message yourself; it will print the `remoteJid`.

### System messages

When something goes wrong, the bridge sends a `/system` message to the chat, e.g.:

```
/system error: could not reach agent at http://127.0.0.1:8000/chat
```

If you send a new message while the agent is still responding to your previous one, you'll get:

```
/system agent is busy, wait for response before sending another message
```

## Outbound WhatsApp messages (scheduled events)

The bridge can also push messages to WhatsApp without an incoming message. Set:

```env
WHATSAPP_TARGET_JID=1234567890@s.whatsapp.net
BRIDGE_HTTP_PORT=9001
```

The bridge starts a small HTTP server at `http://127.0.0.1:9001/send`. The agent can call the built-in `send_whatsapp_message(text)` tool (enabled via `agent.yaml` `builtin_tools: [whatsapp]`) to POST messages to that endpoint.

This is useful for scheduled events: the event prompt tells the agent to run a task and then call `send_whatsapp_message` with the result.

In the agent's `agent.yaml`:

```yaml
extra:
  builtin_tools: [events, whatsapp]
  whatsapp_bridge_url: http://127.0.0.1:9001/send
```

The agent uses the unified `event_tool(action="add", action_name="...", ...)` tool to manage events.

## Notes

- Only direct (1:1) text messages are handled. Group messages, status broadcasts, and media messages are ignored.
- The sender's WhatsApp JID is used directly as the agent `thread_id`, so conversation history persists across restarts.
- If you log out from WhatsApp's Linked Devices, delete `./auth_state` and scan the QR code again.

## Production / VPC

Run this service on your VPC alongside the agent. Your phone only needs to reach WhatsApp's servers; the Baileys library maintains the connection from the VPC.
