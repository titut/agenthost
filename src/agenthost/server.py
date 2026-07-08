"""FastAPI server exposing an agent via HTTP + SSE."""
from __future__ import annotations

import asyncio
import socket
import uuid
from contextlib import asynccontextmanager, closing
from typing import AsyncIterator

from fastapi import FastAPI, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from apscheduler.schedulers.asyncio import AsyncIOScheduler

from agenthost.agent import Agent
from agenthost.config import AgentConfig
from agenthost.events import load_events, run_scheduled_event
from agenthost.logger import setup_logging
from agenthost.registry import register_agent, unregister_agent


logger = setup_logging("agenthost.server")

DEFAULT_THREAD_ID = "default"
DEFAULT_PORT_START = 8000
DEFAULT_PORT_END = 9000

# Per-thread SSE subscriber queues. Allows multiple clients (e.g., Discord + TUI
# chatless monitor) to observe the same agent turn in real time.
_THREAD_SUBSCRIBERS: dict[str, list[asyncio.Queue[tuple[str, str] | None]]] = {}


def _subscribe_thread(thread_id: str) -> asyncio.Queue[tuple[str, str] | None]:
    """Create and register a subscriber queue for the given thread."""
    queue: asyncio.Queue[tuple[str, str] | None] = asyncio.Queue(maxsize=1000)
    _THREAD_SUBSCRIBERS.setdefault(thread_id, []).append(queue)
    return queue


def _unsubscribe_thread(
    thread_id: str, queue: asyncio.Queue[tuple[str, str] | None]
) -> None:
    """Remove a subscriber queue for the given thread."""
    subscribers = _THREAD_SUBSCRIBERS.get(thread_id, [])
    if queue in subscribers:
        subscribers.remove(queue)
    if not subscribers:
        _THREAD_SUBSCRIBERS.pop(thread_id, None)


def _publish_thread_event(thread_id: str, event_type: str, data: str) -> None:
    """Publish an event to all subscribers of the thread."""
    for queue in _THREAD_SUBSCRIBERS.get(thread_id, []):
        try:
            queue.put_nowait((event_type, data))
        except asyncio.QueueFull:
            pass


class ChatRequest(BaseModel):
    message: str
    thread_id: str | None = Field(default=None, description="Conversation thread ID; omitted starts a new thread.")


class ChatResponse(BaseModel):
    thread_id: str


def _find_free_port(host: str, start: int = DEFAULT_PORT_START, end: int = DEFAULT_PORT_END) -> int:
    """Return the first available TCP port in the given range."""
    for port in range(start, end):
        with closing(socket.socket(socket.AF_INET, socket.SOCK_STREAM)) as sock:
            try:
                sock.bind((host, port))
                return port
            except OSError:
                continue
    raise RuntimeError(f"No free port found between {start} and {end}")


def _resolve_port(config: AgentConfig) -> int:
    """Use the configured port if given, otherwise find a free one dynamically."""
    if config.port is not None:
        return config.port
    return _find_free_port(config.host)


def build_app(agent: Agent, scheduler: AsyncIOScheduler | None = None) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        if scheduler is not None:
            scheduler.start()
            logger.info("Scheduler started for agent '%s'", agent.config.name)
        yield
        if scheduler is not None:
            scheduler.shutdown()
            logger.info("Scheduler stopped for agent '%s'", agent.config.name)

    app = FastAPI(title=f"Agent: {agent.config.name}", lifespan=lifespan)

    @app.get("/health")
    async def health() -> dict:
        return {
            "status": "ok",
            "agent": agent.config.name,
            "model": agent.config.model,
            "tools": [t["function"]["name"] for t in agent.tool_schemas],
        }

    @app.post("/clear")
    async def clear(request: ChatRequest) -> dict:
        """Clear the conversation history for the requested thread."""
        thread_id = request.thread_id or DEFAULT_THREAD_ID
        logger.info(
            "Clear request for agent '%s' thread '%s'", agent.config.name, thread_id
        )
        agent.memory.clear_thread(thread_id)
        return {"status": "cleared", "thread_id": thread_id}

    @app.get("/threads")
    async def threads() -> dict:
        """List all conversation threads with a preview of the latest message."""
        logger.info("Threads request for agent '%s'", agent.config.name)
        return {"threads": agent.memory.list_threads()}

    @app.get("/history")
    async def history(thread_id: str = DEFAULT_THREAD_ID) -> dict:
        """Return the full message history for a thread."""
        logger.info(
            "History request for agent '%s' thread '%s'", agent.config.name, thread_id
        )
        messages = agent.memory.get_messages(thread_id)
        return {"thread_id": thread_id, "messages": messages}

    @app.post("/chat")
    async def chat(request: ChatRequest) -> StreamingResponse:
        thread_id = request.thread_id or uuid.uuid4().hex
        logger.info(
            "Chat request for agent '%s' thread '%s'", agent.config.name, thread_id
        )

        async def event_stream() -> AsyncIterator[str]:
            # First event gives the thread_id so the client can continue the conversation.
            meta_event = f"event: meta\ndata: {{\"thread_id\": \"{thread_id}\"}}\n\n"
            yield meta_event
            _publish_thread_event(thread_id, "meta", f"{{\"thread_id\": \"{thread_id}\"}}")

            # Send periodic heartbeats while the agent is thinking or running tools.
            # This keeps long-lived SSE connections open for clients with short
            # read timeouts (e.g., the Discord bridge).
            heartbeat_interval = 60.0
            queue: asyncio.Queue[tuple[str, str] | None] = asyncio.Queue()

            async def consume_chat() -> None:
                try:
                    async for chunk in agent.chat(thread_id, request.message):
                        await queue.put(("message", chunk))
                finally:
                    await queue.put(None)

            async def heartbeat() -> None:
                while True:
                    await asyncio.sleep(heartbeat_interval)
                    await queue.put(("heartbeat", "{}"))

            chat_task = asyncio.create_task(consume_chat())
            heartbeat_task = asyncio.create_task(heartbeat())

            try:
                while True:
                    item = await queue.get()
                    if item is None:
                        break
                    event_type, data = item
                    _publish_thread_event(thread_id, event_type, data)
                    yield f"event: {event_type}\ndata: {data}\n\n"
                _publish_thread_event(thread_id, "done", "{}")
                yield f"event: done\ndata: {{}}\n\n"
                logger.info(
                    "Chat completed for agent '%s' thread '%s'",
                    agent.config.name,
                    thread_id,
                )
            except asyncio.CancelledError:
                logger.warning(
                    "Client disconnected for agent '%s' thread '%s'",
                    agent.config.name,
                    thread_id,
                )
                raise
            except Exception as exc:  # noqa: BLE001
                logger.exception(
                    "Chat failed for agent '%s' thread '%s': %s",
                    agent.config.name,
                    thread_id,
                    exc,
                )
                import json
                error_data = json.dumps({'error': str(exc)})
                _publish_thread_event(thread_id, "error", error_data)
                yield f"event: error\ndata: {error_data}\n\n"
            finally:
                chat_task.cancel()
                heartbeat_task.cancel()
                await asyncio.gather(chat_task, heartbeat_task, return_exceptions=True)

        return StreamingResponse(
            event_stream(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",
            },
        )

    @app.get("/events/{thread_id}")
    async def events(thread_id: str) -> StreamingResponse:
        """Subscribe to real-time SSE events for an existing thread.

        This allows a second client (e.g., the TUI in chatless mode) to observe
        an agent turn that was initiated by another client (e.g., Discord).
        """
        logger.info(
            "Events subscription for agent '%s' thread '%s'",
            agent.config.name,
            thread_id,
        )

        async def event_stream() -> AsyncIterator[str]:
            queue = _subscribe_thread(thread_id)
            logger.debug(
                "Subscriber connected to thread '%s' (%d total subscribers)",
                thread_id,
                len(_THREAD_SUBSCRIBERS.get(thread_id, [])),
            )
            try:
                while True:
                    item = await queue.get()
                    if item is None:
                        # None is used as a sentinel to keep the stream alive
                        # when the producer wants to drop stale subscribers.
                        continue
                    event_type, data = item
                    yield f"event: {event_type}\ndata: {data}\n\n"
            except asyncio.CancelledError:
                logger.warning(
                    "Events subscriber disconnected for agent '%s' thread '%s'",
                    agent.config.name,
                    thread_id,
                )
                raise
            finally:
                _unsubscribe_thread(thread_id, queue)
                logger.debug(
                    "Subscriber disconnected from thread '%s' (%d remaining)",
                    thread_id,
                    len(_THREAD_SUBSCRIBERS.get(thread_id, [])),
                )

        return StreamingResponse(
            event_stream(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",
            },
        )

    return app


def serve(config: AgentConfig) -> None:
    import uvicorn

    logger.info("Starting agent '%s' from %s", config.name, config.path)

    # Create the scheduler before the agent so built-in tools can manage it.
    scheduler = AsyncIOScheduler()

    agent = Agent(config, scheduler=scheduler)

    # Load events and schedule jobs now that the agent exists.
    events, file_timezone = load_events(config.path)
    scheduled_count = 0
    for event in events:
        if not event.enabled:
            logger.info("Skipping disabled event '%s'", event.name)
            continue
        try:
            triggers = event.schedule.to_triggers(file_timezone)
        except Exception as exc:
            logger.error("Invalid schedule for event '%s': %s", event.name, exc)
            continue
        for trigger in triggers:
            job_id = f"{config.name}-{event.name}-{scheduled_count}"
            scheduler.add_job(
                run_scheduled_event,
                trigger=trigger,
                args=(agent, event),
                id=job_id,
                replace_existing=True,
            )
            scheduled_count += 1
    logger.info(
        "Loaded %d enabled events (%d trigger jobs) for agent '%s' (timezone=%s)",
        sum(1 for e in events if e.enabled),
        scheduled_count,
        config.name,
        file_timezone or "local",
    )

    app = build_app(agent, scheduler)

    port = _resolve_port(config)
    register_agent(config.name, config.path, config.host, port)
    logger.info(
        "Registered agent '%s' on http://%s:%d (pid %s)",
        config.name,
        config.host,
        port,
        __import__("os").getpid(),
    )
    try:
        print(f"Serving agent '{config.name}' on http://{config.host}:{port}")
        print(f"Model: {config.model}")
        if config.base_url:
            print(f"Base URL: {config.base_url}")
        print("Tools: discovered at runtime from", config.tools_dir)
        uvicorn.run(app, host=config.host, port=port, log_level="warning")
    except Exception as exc:
        logger.exception("Agent server '%s' crashed: %s", config.name, exc)
        raise
    finally:
        logger.info("Shutting down agent '%s'", config.name)
        unregister_agent()
