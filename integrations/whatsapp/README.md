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

```env
# URL of the running agent's chat endpoint
AGENT_CHAT_URL=http://127.0.0.1:8000/chat

# Optional: shown in logs
AGENT_NAME=RESEARCHER

# Optional: log verbosity
#   info    - normal flow (default)
#   debug   - verbose bridge logs (recommended for troubleshooting)
#   trace   - debug + very noisy Baileys internal logs
LOG_LEVEL=info
```

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

### System messages

When something goes wrong, the bridge sends a `/system` message to the chat, e.g.:

```
/system error: could not reach agent at http://127.0.0.1:8000/chat
```

If you send a new message while the agent is still responding to your previous one, you'll get:

```
/system agent is busy, wait for response before sending another message
```

## Notes

- Only direct (1:1) text messages are handled. Group messages, status broadcasts, and media messages are ignored.
- The sender's WhatsApp JID is hashed with SHA-256 to create the agent `thread_id`, so conversation history persists across restarts.
- If you log out from WhatsApp's Linked Devices, delete `./auth_state` and scan the QR code again.

## Production / VPC

Run this service on your VPC alongside the agent. Your phone only needs to reach WhatsApp's servers; the Baileys library maintains the connection from the VPC.
