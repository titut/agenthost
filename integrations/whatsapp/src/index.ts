import 'dotenv/config';
import fs from 'fs/promises';
import http from 'http';
import makeWASocket, {
  DisconnectReason,
  fetchLatestBaileysVersion,
  useMultiFileAuthState,
  WASocket,
  type WAMessage,
} from '@whiskeysockets/baileys';
import pino from 'pino';
import qrcode from 'qrcode-terminal';

const AGENT_CHAT_URL = process.env.AGENT_CHAT_URL ?? 'http://127.0.0.1:8000/chat';
const AGENT_NAME = process.env.AGENT_NAME ?? 'agent';
const AUTH_STATE_DIR = process.env.AUTH_STATE_DIR ?? './auth_state';
const LOG_LEVEL = (process.env.LOG_LEVEL ?? 'info').toLowerCase();
const RESPOND_TO_OTHERS = (process.env.RESPOND_TO_OTHERS ?? 'true').toLowerCase() === 'true';
const RESPOND_TO_FROM_ME = (process.env.RESPOND_TO_FROM_ME ?? 'false').toLowerCase() === 'true';
const AI_PREFIX = process.env.AI_PREFIX ?? '/ai';
const SYSTEM_PREFIX = process.env.SYSTEM_PREFIX ?? '/system';
const BUSY_MESSAGE = process.env.BUSY_MESSAGE ?? 'agent is busy, wait for response before sending another message';
const WHATSAPP_TARGET_JID = process.env.WHATSAPP_TARGET_JID;
const BRIDGE_HTTP_PORT = parseInt(process.env.BRIDGE_HTTP_PORT ?? '9001', 10);

// Track threads that currently have an in-flight agent request.
const busyThreads = new Set<string>();

// The authenticated user's own WhatsApp JID. Populated once connected.
let ownJid: string | null = null;

const LEVELS = ['silent', 'error', 'warn', 'info', 'debug', 'trace'] as const;
type LogLevel = (typeof LEVELS)[number];

function isLogLevel(value: string): value is LogLevel {
  return LEVELS.includes(value as LogLevel);
}

const CURRENT_LEVEL: LogLevel = isLogLevel(LOG_LEVEL) ? LOG_LEVEL : 'info';
const CURRENT_INDEX = LEVELS.indexOf(CURRENT_LEVEL);

function log(level: LogLevel, ...args: unknown[]): void {
  if (LEVELS.indexOf(level) <= CURRENT_INDEX) {
    const ts = new Date().toISOString();
    const prefix = `[${ts}] [${level.toUpperCase()}] [whatsapp-bridge]`;
    if (args.length > 0 && typeof args[0] === 'string') {
      // eslint-disable-next-line no-console
      console.log(`${prefix} ${args[0]}`, ...args.slice(1));
    } else {
      // eslint-disable-next-line no-console
      console.log(prefix, ...args);
    }
  }
}

function mask(s: string | undefined): string {
  if (!s) return '<not set>';
  if (s.length <= 8) return '*'.repeat(s.length);
  return `${s.slice(0, 4)}...${s.slice(-4)}`;
}

async function ensureDir(dir: string): Promise<void> {
  log('debug', `Ensuring directory exists: ${dir}`);
  await fs.mkdir(dir, { recursive: true });
  log('debug', `Directory ready: ${dir}`);
}

async function fetchAgentReply(threadId: string, message: string): Promise<string> {
  log('debug', `-> agent POST ${AGENT_CHAT_URL}`);
  log('debug', `   thread_id=${threadId}`);
  log('debug', `   message=${message}`);

  const requestStart = Date.now();
  let response: Response;
  try {
    response = await fetch(AGENT_CHAT_URL, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        Accept: 'text/event-stream',
      },
      body: JSON.stringify({ message, thread_id: threadId }),
    });
  } catch (err) {
    log('error', `Failed to reach agent at ${AGENT_CHAT_URL}:`, err);
    throw err;
  }

  log('debug', `<- agent HTTP ${response.status} (${Date.now() - requestStart}ms)`);

  if (!response.ok) {
    const text = await response.text().catch((err) => {
      log('debug', 'Could not read error response body:', err);
      return '';
    });
    log('error', `Agent returned ${response.status}: ${text}`);
    throw new Error(`Agent returned ${response.status}: ${text}`);
  }

  if (!response.body) {
    throw new Error('Agent response has no body');
  }

  const reader = response.body.getReader();
  const decoder = new TextDecoder();

  let buffer = '';
  let currentEvent: string | null = null;
  const contentParts: string[] = [];
  let metaReceived = false;
  let eventCount = 0;

  function processLine(line: string): void {
    const trimmed = line.trim();
    if (!trimmed) {
      currentEvent = null;
      return;
    }

    if (trimmed.startsWith('event:')) {
      currentEvent = trimmed.slice('event:'.length).trim();
      log('trace', `SSE event type: ${currentEvent}`);
      return;
    }

    if (trimmed.startsWith('data:') && currentEvent) {
      eventCount++;
      const data = trimmed.slice('data:'.length).trim();
      log('trace', `SSE data [${currentEvent}]:`, data);

      if (currentEvent === 'meta') {
        try {
          const meta = JSON.parse(data);
          log('debug', `Agent assigned thread_id: ${meta.thread_id ?? '<none>'}`);
          metaReceived = true;
        } catch (err) {
          log('debug', 'Failed to parse meta event:', data, err);
        }
      } else if (currentEvent === 'message') {
        try {
          const event = JSON.parse(data);
          if (event.type === 'content' && typeof event.data === 'string') {
            contentParts.push(event.data);
            log('trace', `Content chunk (${event.data.length} chars): ${event.data.slice(0, 80)}`);
          } else if (event.type === 'tool_start') {
            log('debug', `Agent started tool: ${event.data?.name}`, event.data?.arguments);
          } else if (event.type === 'tool_result') {
            log('debug', `Agent got tool result: ${event.data?.name}`, event.data?.result);
          } else if (event.type === 'tool_error') {
            log('warn', `Agent tool error: ${event.data?.name}`, event.data?.error);
          } else {
            log('trace', 'Unknown SSE message event type:', event.type);
          }
        } catch (err) {
          log('debug', 'Failed to parse SSE message data:', data, err);
        }
      } else if (currentEvent === 'done') {
        log('debug', 'Agent stream done');
      } else if (currentEvent === 'error') {
        log('error', `Agent reported stream error: ${data}`);
        throw new Error(`Agent error: ${data}`);
      }
    }
  }

  while (true) {
    const { done, value } = await reader.read();
    if (done) {
      buffer += decoder.decode();
      if (buffer) {
        log('trace', 'Processing final SSE buffer');
        buffer.split('\n').forEach(processLine);
      }
      break;
    }

    buffer += decoder.decode(value, { stream: true });
    const lines = buffer.split('\n');
    buffer = lines.pop() ?? '';
    lines.forEach(processLine);
  }

  const fullReply = contentParts.join('');
  log('debug', `SSE events processed: ${eventCount}, meta received: ${metaReceived}, content length: ${fullReply.length}`);
  return fullReply;
}

function describeMessageType(msg: WAMessage): string {
  if (!msg.message) return 'empty';
  const keys = Object.keys(msg.message);
  return keys.join(', ');
}

async function sendSystemMessage(sock: WASocket, jid: string, text: string): Promise<void> {
  const message = `${SYSTEM_PREFIX} ${text}`;
  log('debug', `Sending system message to ${jid}: ${message}`);
  await sock.sendMessage(jid, { text: message });
}

const IGNORED_STATUSES = new Set(['DELIVERY_ACK', 'SERVER_ACK', 'READ', 'PLAYED']);

async function handleIncomingMessage(sock: WASocket, msg: WAMessage): Promise<void> {
  log('debug', 'Received raw message:', JSON.stringify(msg, null, 2));

  const key = msg.key;
  const remoteJid = key.remoteJid;

  const msgStatus = msg.status?.toString();
  if (msgStatus && IGNORED_STATUSES.has(msgStatus)) {
    log('debug', `Ignoring status update: ${msgStatus}`);
    return;
  }

  if (!remoteJid) {
    log('debug', 'Ignoring message without remoteJid');
    return;
  }

  if (remoteJid === 'status@broadcast') {
    log('debug', 'Ignoring status broadcast');
    return;
  }

  if (remoteJid.endsWith('@g.us')) {
    log('debug', `Ignoring group message (jid=${remoteJid})`);
    return;
  }

  log('debug', `Message type(s): ${describeMessageType(msg)}`);

  const rawText =
    msg.message?.conversation ||
    msg.message?.extendedTextMessage?.text;

  if (!rawText) {
    log('debug', 'Ignoring non-text message');
    return;
  }

  // Ignore messages generated by the bridge itself (AI replies and system messages).
  if (rawText.startsWith(AI_PREFIX) || rawText.startsWith(SYSTEM_PREFIX)) {
    log('debug', 'Ignoring bridge-generated message');
    return;
  }

  if (key.fromMe) {
    if (!RESPOND_TO_FROM_ME) {
      log('debug', `Ignoring message from self (jid=${remoteJid})`);
      return;
    }

    // Only respond to messages the user sent to themselves.
    if (ownJid && remoteJid !== ownJid) {
      log('debug', `Ignoring message from self to another contact (to=${remoteJid}, own=${ownJid})`);
      return;
    }
  } else {
    if (!RESPOND_TO_OTHERS) {
      log('debug', `Ignoring message from others (jid=${remoteJid})`);
      return;
    }
  }

  const threadId = remoteJid;
  log('info', `[${AGENT_NAME}] ${remoteJid} -> thread_id=${threadId}: ${rawText.slice(0, 80)}`);

  if (busyThreads.has(threadId)) {
    log('warn', `Thread ${threadId} is busy; rejecting new message`);
    await sendSystemMessage(sock, remoteJid, BUSY_MESSAGE);
    return;
  }

  busyThreads.add(threadId);
  log('debug', `Thread ${threadId} marked busy`);

  try {
    const reply = await fetchAgentReply(threadId, rawText);
    if (!reply.trim()) {
      log('warn', 'Agent returned empty reply; nothing to send');
      await sendSystemMessage(sock, remoteJid, 'agent returned an empty response');
      return;
    }

    log('info', `[${AGENT_NAME}] reply (${reply.length} chars): ${reply.slice(0, 80)}`);
    log('debug', 'Full reply:', reply);

    const sendStart = Date.now();
    const aiMessage = `${AI_PREFIX} ${reply}`;
    const result = await sock.sendMessage(remoteJid, { text: aiMessage });
    log('debug', `WhatsApp send took ${Date.now() - sendStart}ms, result:`, JSON.stringify(result, null, 2));
  } catch (err) {
    const errorText = err instanceof Error ? err.message : String(err);
    log('error', 'Failed to get or send reply:', err);
    await sendSystemMessage(sock, remoteJid, `error: ${errorText}`);
  } finally {
    busyThreads.delete(threadId);
    log('debug', `Thread ${threadId} no longer busy`);
  }
}

function startOutboundServer(sock: WASocket): void {
  if (!WHATSAPP_TARGET_JID) {
    log('info', 'WHATSAPP_TARGET_JID not set; outbound /send endpoint disabled');
    return;
  }

  const server = http.createServer(async (req, res) => {
    if (req.method !== 'POST' || req.url !== '/send') {
      res.writeHead(404, { 'Content-Type': 'application/json' });
      res.end(JSON.stringify({ error: 'not found' }));
      return;
    }

    let body = '';
    req.on('data', (chunk) => { body += chunk; });
    req.on('end', async () => {
      try {
        const data = JSON.parse(body);
        const text = data.text;
        if (!text || typeof text !== 'string') {
          res.writeHead(400, { 'Content-Type': 'application/json' });
          res.end(JSON.stringify({ error: 'missing text' }));
          return;
        }

        const taggedText = `${AI_PREFIX} ${text}`;
        log('info', `Outbound /send: ${taggedText.slice(0, 80)}`);
        await sock.sendMessage(WHATSAPP_TARGET_JID, { text: taggedText });
        res.writeHead(200, { 'Content-Type': 'application/json' });
        res.end(JSON.stringify({ ok: true }));
      } catch (err) {
        log('error', 'Outbound /send failed:', err);
        res.writeHead(500, { 'Content-Type': 'application/json' });
        res.end(JSON.stringify({ error: String(err) }));
      }
    });
  });

  server.listen(BRIDGE_HTTP_PORT, () => {
    log('info', `Outbound server listening on http://127.0.0.1:${BRIDGE_HTTP_PORT}/send`);
  });
}

async function start(): Promise<void> {
  log('info', '=== agenthost WhatsApp bridge starting ===');
  log('info', `AGENT_CHAT_URL=${AGENT_CHAT_URL}`);
  log('info', `AGENT_NAME=${AGENT_NAME}`);
  log('info', `AUTH_STATE_DIR=${AUTH_STATE_DIR}`);
  log('info', `LOG_LEVEL=${CURRENT_LEVEL}`);
  log('info', `RESPOND_TO_OTHERS=${RESPOND_TO_OTHERS}`);
  log('info', `RESPOND_TO_FROM_ME=${RESPOND_TO_FROM_ME}`);
  log('info', `AI_PREFIX=${AI_PREFIX}`);
  log('info', `SYSTEM_PREFIX=${SYSTEM_PREFIX}`);
  log('info', `WHATSAPP_TARGET_JID=${WHATSAPP_TARGET_JID ?? '<not set>'}`);
  log('info', `BRIDGE_HTTP_PORT=${BRIDGE_HTTP_PORT}`);

  await ensureDir(AUTH_STATE_DIR);

  log('debug', 'Loading multi-file auth state...');
  let { state, saveCreds } = await useMultiFileAuthState(AUTH_STATE_DIR);
  log('debug', 'Auth state loaded');

  // Baileys may create an empty auth folder with no credentials. If creds.me
  // is missing, the session is unusable and will fail with 405. Wipe it and
  // start fresh so the user gets a QR code.
  if (!state.creds.me) {
    log('warn', `Auth state at ${AUTH_STATE_DIR} is empty or invalid. Clearing for fresh QR pairing.`);
    try {
      await fs.rm(AUTH_STATE_DIR, { recursive: true, force: true });
      await fs.mkdir(AUTH_STATE_DIR, { recursive: true });
      const fresh = await useMultiFileAuthState(AUTH_STATE_DIR);
      state = fresh.state;
      saveCreds = fresh.saveCreds;
      log('info', 'Auth state cleared. A QR code will be printed on first connection.');
    } catch (err) {
      log('error', 'Failed to clear empty auth state:', err);
      throw err;
    }
  } else {
    log('debug', `Existing session found for ${state.creds.me.id}`);
  }

  // Baileys internal logger: trace only when explicitly requested, otherwise silent.
  const baileysPinoLevel: pino.LevelWithSilent = CURRENT_LEVEL === 'trace' ? 'trace' : 'silent';
  const baileysLogger = pino({ level: baileysPinoLevel });
  log('debug', `Baileys internal log level: ${baileysPinoLevel}`);

  log('debug', 'Fetching latest Baileys/WhatsApp Web version...');
  const { version, isLatest } = await fetchLatestBaileysVersion();
  log('info', `Using WhatsApp Web version ${version} (latest=${isLatest})`);

  const sock = makeWASocket({
    auth: state,
    printQRInTerminal: false,
    logger: baileysLogger,
    version,
  });
  log('debug', 'WASocket created');

  startOutboundServer(sock);

  sock.ev.on('connection.update', (update) => {
    log('debug', 'connection.update:', JSON.stringify(update, null, 2));

    const { connection, lastDisconnect, qr } = update;

    if (qr) {
      log('info', 'Scan this QR code with WhatsApp (Linked Devices):');
      qrcode.generate(qr, { small: true });
    }

    if (connection === 'close') {
      const statusCode = (lastDisconnect?.error as any)?.output?.statusCode;
      const reason = Object.keys(DisconnectReason).find(
        (k) => (DisconnectReason as any)[k] === statusCode
      ) ?? `unknown(${statusCode})`;
      const shouldReconnect = statusCode !== DisconnectReason.loggedOut;

      log('warn', `WhatsApp connection closed. reason=${reason}, reconnect=${shouldReconnect}`);

      if (shouldReconnect) {
        log('info', 'Reconnecting in 3s...');
        setTimeout(() => {
          start().catch((err) => {
            log('error', 'Reconnection failed:', err);
          });
        }, 3000);
      } else {
        log('info', 'Logged out. Delete auth_state and restart to scan QR again.');
      }
    } else if (connection === 'open') {
      log('info', 'WhatsApp connection ready.');
      if (sock.user?.id) {
        ownJid = sock.user.id;
        log('info', `Authenticated as ${ownJid}`);
      }
    } else if (connection === 'connecting') {
      log('debug', 'WhatsApp connecting...');
    }
  });

  sock.ev.on('creds.update', () => {
    log('debug', 'Credentials updated');
    saveCreds().catch((err) => log('error', 'Failed to save credentials:', err));
  });

  sock.ev.on('messages.upsert', async (m) => {
    log('debug', `messages.upsert: ${m.messages.length} message(s), type=${m.type}`);
    for (const msg of m.messages) {
      await handleIncomingMessage(sock, msg);
    }
  });
}

start().catch((err) => {
  log('error', 'Fatal error starting WhatsApp bridge:', err);
  process.exit(1);
});
