"""Core agent: loads config, tools, skills, memory, and runs the LLM loop."""
from __future__ import annotations

import json
from typing import Any, AsyncIterator

from openai import AsyncOpenAI

from agenthost.config import AgentConfig
from agenthost.logger import setup_logging
from agenthost.memory import AgentMemory
from agenthost.tools import ToolRunner, discover_tools


logger = setup_logging("agenthost.agent")


class Agent:
    def __init__(self, config: AgentConfig, client: AsyncOpenAI | None = None):
        self.config = config
        if client is not None:
            self.client = client
        else:
            kwargs = {}
            if config.base_url:
                kwargs["base_url"] = config.base_url
            self.client = AsyncOpenAI(**kwargs)
        self.memory = AgentMemory(config)
        self.tool_schemas, self.tool_runner = discover_tools(config.tools_dir)

    async def chat(self, thread_id: str, user_message: str) -> AsyncIterator[str]:
        logger.info(
            "Agent '%s' thread '%s' received user message", self.config.name, thread_id
        )
        # Clean up any dangling assistant tool_calls from previous interrupted turns.
        removed = self.memory.repair_thread(thread_id)
        if removed:
            logger.warning(
                "Repaired %d dangling tool_calls in thread '%s'", removed, thread_id
            )
        self.memory.append_message(thread_id, {"role": "user", "content": user_message})
        messages = self._build_messages(thread_id)

        while True:
            stream = await self.client.chat.completions.create(
                model=self.config.model,
                messages=messages,
                tools=self.tool_schemas or None,
                tool_choice="auto" if self.tool_schemas else None,
                temperature=self.config.temperature,
                stream=True,
            )

            assistant_content = ""
            tool_calls: list[dict[str, Any]] = []
            finish_reason: str | None = None

            async for chunk in stream:
                delta = chunk.choices[0].delta
                finish_reason = chunk.choices[0].finish_reason or finish_reason

                if delta.content:
                    assistant_content += delta.content
                    yield json.dumps({"type": "content", "data": delta.content}) + "\n"

                if delta.tool_calls:
                    for tc in delta.tool_calls:
                        # Some providers (e.g. Gemini) omit the index on tool-call
                        # deltas. Default to the current/last tool call, or 0.
                        index = tc.index
                        if index is None:
                            index = len(tool_calls) - 1 if tool_calls else 0
                            logger.warning(
                                "Tool-call delta missing index; defaulting to %s",
                                index,
                            )
                        while len(tool_calls) <= index:
                            tool_calls.append({"id": "", "type": "function", "function": {"name": "", "arguments": ""}})
                        if tc.id:
                            tool_calls[index]["id"] = tc.id
                        if tc.function and tc.function.name:
                            tool_calls[index]["function"]["name"] = tc.function.name
                        if tc.function and tc.function.arguments:
                            tool_calls[index]["function"]["arguments"] += tc.function.arguments

            if tool_calls:
                # Persist assistant's tool call request.
                self.memory.append_message(thread_id, {"role": "assistant", "tool_calls": tool_calls})
                messages.append({"role": "assistant", "tool_calls": tool_calls})

                for tc in tool_calls:
                    name = tc["function"]["name"]
                    try:
                        args = json.loads(tc["function"]["arguments"]) if tc["function"]["arguments"] else {}
                    except json.JSONDecodeError:
                        args = {}

                    logger.info(
                        "Agent '%s' thread '%s' calling tool '%s'",
                        self.config.name,
                        thread_id,
                        name,
                    )
                    yield json.dumps({"type": "tool_start", "data": {"name": name, "arguments": args}}) + "\n"
                    try:
                        result = await self.tool_runner.run(name, args)
                    except Exception as exc:  # noqa: BLE001
                        logger.exception(
                            "Tool '%s' failed for agent '%s' thread '%s': %s",
                            name,
                            self.config.name,
                            thread_id,
                            exc,
                        )
                        result = json.dumps(
                            {"error": f"Tool '{name}' failed: {type(exc).__name__}: {exc}"}
                        )
                        yield json.dumps({"type": "tool_error", "data": {"name": name, "error": result}}) + "\n"
                    yield json.dumps({"type": "tool_result", "data": {"name": name, "result": result}}) + "\n"

                    tool_msg = {
                        "role": "tool",
                        "tool_call_id": tc["id"],
                        "name": name,
                        "content": result,
                    }
                    self.memory.append_message(thread_id, tool_msg)
                    messages.append(tool_msg)

                # Loop back to model with tool results.
                continue

            if assistant_content:
                self.memory.append_message(thread_id, {"role": "assistant", "content": assistant_content})
            logger.info(
                "Agent '%s' thread '%s' finished turn with %d total messages",
                self.config.name,
                thread_id,
                len(messages),
            )
            break

    def _build_messages(self, thread_id: str) -> list[dict[str, Any]]:
        messages: list[dict[str, Any]] = [{"role": "system", "content": self.config.system_prompt}]
        messages.extend(self.memory.get_messages(thread_id))
        return messages
