# agenthost Telegram Bridge

A simple polling-based Telegram bridge for agenthost agents.

## Why Telegram?

Telegram has an official Bot API, so this bridge is much simpler than the WhatsApp bridge:

- No QR pairing
- No phone number linking
- No device/session limits
- Bot has its own identity

## Setup

1. Create a Telegram bot via [@BotFather](https://t.me/botfather) and copy the bot token.

2. Copy and edit the environment file:

```bash
cd integrations/telegram
cp .env.example .env
# edit .env and set TELEGRAM_BOT_TOKEN
```

3. Make sure the agent is running:

```bash
# from the repo root
agenthost serve RESEARCHER
```

4. Run the bridge inside the agenthost virtual environment:

```bash
../../venv/bin/python main.py
```

Or, if you have the dependencies installed globally:

```bash
python main.py
```

## Usage

Send a text message to your bot on Telegram. The bridge forwards it to the agent's `/chat` endpoint (using the Telegram `chat_id` as the `thread_id`) and sends the agent's reply back.

## Notes

- The bridge uses long polling. It will keep running until you stop it.
- Each Telegram chat gets its own thread ID, so conversation history is isolated per chat.
- Replies are sent as Markdown. If the agent returns invalid Markdown, Telegram may reject the message.
