"""FastAPI server exposing an agent via HTTP + SSE."""
from __future__ import annotations

import socket
from contextlib import asynccontextmanager, closing
from typing import AsyncIterator

from fastapi import FastAPI, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from agenthost.agent import Agent
from agenthost.config import AgentConfig
from agenthost.logger import setup_logging
from agenthost.registry import register_agent, unregister_agent


logger = setup_logging("agenthost.server")

DEFAULT_THREAD_ID = "default"
DEFAULT_PORT_START = 8000
DEFAULT_PORT_END = 9000


class ChatRequest(BaseModel):
    message: str
    thread_id: str | None = Field(default=None, description="Conversation thread ID; omitted uses the process default thread.")


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


def build_app(agent: Agent) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        yield

    app = FastAPI(title=f"Agent: {agent.config.name}", lifespan=lifespan)

    @app.get("/health")
    async def health() -> dict:
        return {
            "status": "ok",
            "agent": agent.config.name,
            "model": agent.config.model,
            "tools": [t["function"]["name"] for t in agent.tool_schemas],
        }

    @app.post("/chat")
    async def chat(request: ChatRequest) -> StreamingResponse:
        thread_id = request.thread_id or DEFAULT_THREAD_ID
        logger.info(
            "Chat request for agent '%s' thread '%s'", agent.config.name, thread_id
        )

        async def event_stream() -> AsyncIterator[str]:
            # First event gives the thread_id so the client can continue the conversation.
            yield f"event: meta\ndata: {{\"thread_id\": \"{thread_id}\"}}\n\n"
            try:
                async for chunk in agent.chat(thread_id, request.message):
                    yield f"event: message\ndata: {chunk}\n\n"
                yield f"event: done\ndata: {{}}\n\n"
                logger.info(
                    "Chat completed for agent '%s' thread '%s'",
                    agent.config.name,
                    thread_id,
                )
            except Exception as exc:  # noqa: BLE001
                logger.exception(
                    "Chat failed for agent '%s' thread '%s': %s",
                    agent.config.name,
                    thread_id,
                    exc,
                )
                import json
                yield f"event: error\ndata: {json.dumps({'error': str(exc)})}\n\n"

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
    agent = Agent(config)
    agent.memory.clear_messages()
    app = build_app(agent)

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
