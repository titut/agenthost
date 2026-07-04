"""Core agent: loads config, tools, skills, memory, and runs the LLM loop."""
from __future__ import annotations

import asyncio
import json
from typing import Any, AsyncIterator

from openai import AsyncOpenAI, BadRequestError

from agenthost.builtin_tools import build_builtin_tools_prompt, make_builtin_tools
from agenthost.config import AgentConfig
from agenthost.logger import setup_logging
from agenthost.memory import AgentMemory
from agenthost.tools import ToolRunner, discover_tools


logger = setup_logging("agenthost.agent")


class Agent:
    def __init__(
        self,
        config: AgentConfig,
        client: AsyncOpenAI | None = None,
        scheduler: Any | None = None,
    ):
        self.config = config
        if client is not None:
            self.client = client
        else:
            kwargs = {}
            if config.base_url:
                kwargs["base_url"] = config.base_url
            self.client = AsyncOpenAI(**kwargs)
        self.memory = AgentMemory(config)
        builtins = make_builtin_tools(config, scheduler, agent_provider=lambda: self)
        self.tool_schemas, self.tool_runner = discover_tools(config.tools_dir, builtins)
        self.system_prompt = config.system_prompt + build_builtin_tools_prompt(config)

    async def chat(self, thread_id: str, user_message: str) -> AsyncIterator[str]:
        # Expose the current thread_id to built-in tools that need it.
        self._current_thread_id = thread_id
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

        attempt = 0
        max_attempts = 5
        while True:
            completion_kwargs: dict[str, Any] = {
                "model": self.config.model,
                "messages": messages,
                "tools": self.tool_schemas or None,
                "tool_choice": "auto" if self.tool_schemas else None,
                "temperature": self.config.temperature,
                "stream": True,
            }
            if self.config.max_tokens is not None:
                completion_kwargs["max_tokens"] = self.config.max_tokens
            if self.config.thinking is not None:
                completion_kwargs["reasoning_effort"] = self.config.thinking

            logger.debug(
                "LLM request for thread '%s': %s",
                thread_id,
                json.dumps(completion_kwargs, default=str),
            )

            try:
                stream = await self.client.chat.completions.create(**completion_kwargs)
            except Exception as exc:
                attempt += 1
                if attempt >= max_attempts:
                    logger.error(
                        "LLM request failed for thread '%s' after %d attempts: %s",
                        thread_id,
                        attempt,
                        exc,
                    )
                    raise

                logger.warning(
                    "LLM request failed for thread '%s' (attempt %d/%d): %s; repairing and retrying in 1s",
                    thread_id,
                    attempt,
                    max_attempts,
                    exc,
                )
                try:
                    removed = self.memory.repair_thread(thread_id)
                    if removed:
                        logger.warning("Repaired %d messages in thread '%s'", removed, thread_id)
                except Exception as repair_err:
                    logger.warning("Repair failed for thread '%s': %s", thread_id, repair_err)

                messages = self._build_messages(thread_id)
                await asyncio.sleep(1)
                continue

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
                            tool_calls.append({"id": f"call_{len(tool_calls)}", "type": "function", "function": {"name": "", "arguments": ""}})
                        if tc.id:
                            tool_calls[index]["id"] = tc.id
                        if tc.function and tc.function.name:
                            tool_calls[index]["function"]["name"] = tc.function.name
                        if tc.function and tc.function.arguments:
                            tool_calls[index]["function"]["arguments"] += tc.function.arguments

                        # Preserve provider-specific fields (e.g. Gemini's
                        # thought_signature) that the OpenAI SDK does not model.
                        extra = getattr(tc, "model_extra", None) or {}
                        for key, value in extra.items():
                            if key not in ("id", "index", "type", "function") and value is not None:
                                tool_calls[index][key] = value
                                logger.debug("Preserved tool-call extra field: %s", key)

            if tool_calls:
                logger.debug(
                    "LLM response for thread '%s' produced tool_calls: %s",
                    thread_id,
                    json.dumps(tool_calls, default=str),
                )
                # Persist assistant's tool call request.
                self.memory.append_message(thread_id, {"role": "assistant", "tool_calls": tool_calls})
                messages.append({"role": "assistant", "tool_calls": tool_calls})

                for tc in tool_calls:
                    name = tc["function"]["name"]
                    args = self._parse_tool_arguments(tc["function"]["arguments"])

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
                        "content": result,
                    }
                    self.memory.append_message(thread_id, tool_msg)
                    messages.append(tool_msg)

                # Loop back to model with tool results.
                continue

            if assistant_content:
                logger.debug(
                    "LLM response for thread '%s' produced content: %s",
                    thread_id,
                    assistant_content,
                )
                self.memory.append_message(thread_id, {"role": "assistant", "content": assistant_content})
            logger.info(
                "Agent '%s' thread '%s' finished turn with %d total messages",
                self.config.name,
                thread_id,
                len(messages),
            )
            break

    def _build_messages(self, thread_id: str) -> list[dict[str, Any]]:
        messages: list[dict[str, Any]] = [{"role": "system", "content": self.system_prompt}]

        # Prepend the thread_id to the latest user message so the agent always
        # knows which conversation it is in. This is needed for tools like
        # send_discord_message that must target the same channel/thread.
        # Memory stays clean because we only modify the copy sent to the LLM.
        history = self.memory.get_messages(thread_id)
        if history and history[-1]["role"] == "user":
            history = list(history)
            history[-1] = {
                **history[-1],
                "content": f"[thread_id: {thread_id}] {history[-1]['content']}",
            }

        cleaned_history = self._clean_tool_calls_for_api(history)
        messages.extend(self._merge_consecutive_messages(cleaned_history))
        return messages

    @staticmethod
    def _clean_tool_calls_for_api(
        history: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        """Normalize persisted tool_calls for the API.

        Ensures ``function.arguments`` is valid JSON. Some models emit
        concatenated JSON objects when trying to batch multiple calls; the API
        rejects those if sent back verbatim. Provider-specific extra fields
        (e.g. Gemini's ``extra_content.google.thought_signature``) are preserved
        because Gemini requires them on subsequent turns.
        """
        cleaned: list[dict[str, Any]] = []
        for msg in history:
            msg_copy = dict(msg)
            tool_calls = msg_copy.get("tool_calls")
            if tool_calls:
                cleaned_tool_calls: list[dict[str, Any]] = []
                for tc in tool_calls:
                    tc_copy = dict(tc)
                    function = dict(tc_copy.get("function", {}))
                    args = Agent._parse_tool_arguments(function.get("arguments", ""))
                    function["arguments"] = json.dumps(args)
                    tc_copy["function"] = function
                    cleaned_tool_calls.append(tc_copy)
                msg_copy["tool_calls"] = cleaned_tool_calls
            cleaned.append(msg_copy)
        return cleaned

    @staticmethod
    def _parse_tool_arguments(arguments: str) -> dict[str, Any]:
        """Parse tool arguments from the LLM.

        Handles the common failure mode where the model concatenates multiple
        JSON objects (e.g. when trying to call the same tool repeatedly in one
        turn). In that case, the first complete object is returned.
        """
        if not arguments:
            return {}

        try:
            parsed = json.loads(arguments)
            if isinstance(parsed, dict):
                return parsed
            return {}
        except json.JSONDecodeError:
            pass

        # Try to extract the first top-level JSON object.
        decoder = json.JSONDecoder()
        try:
            obj, _ = decoder.raw_decode(arguments)
            if isinstance(obj, dict):
                logger.warning(
                    "Tool arguments contained concatenated JSON; using first object: %s",
                    obj,
                )
                return obj
        except json.JSONDecodeError:
            pass

        return {}

    @staticmethod
    def _merge_consecutive_messages(
        history: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        """Merge consecutive content-only messages of the same role.

        Providers such as Gemini require strictly alternating user/assistant
        turns. Tool messages and assistant messages that declare tool_calls are
        left untouched so tool-call/tool-response pairing stays intact.
        """
        merged: list[dict[str, Any]] = []
        for msg in history:
            if not merged:
                merged.append(dict(msg))
                continue

            last = merged[-1]
            both_plain = (
                last["role"] == msg["role"]
                and "tool_calls" not in last
                and "tool_calls" not in msg
                and "tool_call_id" not in last
                and "tool_call_id" not in msg
            )
            if both_plain:
                last_content = last.get("content") or ""
                msg_content = msg.get("content") or ""
                separator = "\n\n" if last_content and msg_content else ""
                last["content"] = f"{last_content}{separator}{msg_content}".strip()
                continue

            merged.append(dict(msg))

        return merged
