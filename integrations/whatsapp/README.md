# agenthost WhatsApp Bridge

QR-paired WhatsApp bridge for agenthost agents, powered by [Baileys](https://github.com/WhiskeySockets/Baileys).

This lets you chat with a served agent from your personal WhatsApp number. No Meta Business API, no webhooks, no public HTTPS endpoint required.

## How it works

1. This Node.js service pairs with your personal WhatsApp via QR code (Linked Devices).
2. It listens for incoming WhatsApp messages.
3. Messages you send to yourself are forwarded to a running agenthost agent's `/chat` endpoint.
4. The agent's reply is sent back to your WhatsApp self-chat.

The bridge reads your own WhatsApp identity (`state.creds.me`) from Baileys, so you do **not** need to configure your JID or LID manually.

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

Message yourself on WhatsApp. The bridge forwards it to the agent and replies in your self-chat with an `/ai` prefix so you can tell which messages came from the AI.

The bridge ignores any message that starts with `/ai` or `/system`, so it never replies to its own messages.

Messages you send to **other** contacts are ignored by default. If you want the bridge to also respond to incoming messages from other people, set:

```env
RESPOND_TO_OTHERS=true
```

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

The bridge exposes a small HTTP server at `http://127.0.0.1:9001/send`. The agent can call the built-in `send_whatsapp_message(text)` tool (enabled via `agent.yaml` `builtin_tools: [whatsapp]`) to POST messages to that endpoint.

By default, outbound messages are sent to your WhatsApp self-chat. You can also specify a target:

```json
POST /send
{
  "text": "hello",
  "to": "1234567890@s.whatsapp.net"
}
```

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
- Self-chat uses a stable phone-number `thread_id`, so conversation history persists even if WhatsApp changes your LID.
- If you log out from WhatsApp's Linked Devices, delete `./auth_state` and scan the QR code again.

## Production / VPC

Run this service on your VPC alongside the agent. Your phone only needs to reach WhatsApp's servers; the Baileys library maintains the connection from the VPC.
