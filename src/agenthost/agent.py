"""Core agent: loads config, tools, skills, memory, and runs the LLM loop."""
from __future__ import annotations

import asyncio
import json
from typing import Any, AsyncIterator

from openai import AsyncOpenAI, BadRequestError

from agenthost.builtin_tools import build_builtin_tools_prompt, make_builtin_tools
from agenthost.config import AgentConfig, _estimate_tokens
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
        self.tool_schemas, self.tool_runner = discover_tools(config.tools_dir, builtins, config=config)
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
        tool_round = 0
        max_tool_rounds = 15
        recent_tool_errors: list[str] = []

        def _graceful_error(message: str) -> str:
            logger.error(message)
            self.memory.append_message(thread_id, {"role": "assistant", "content": message})
            return json.dumps({"type": "error", "data": message}) + "\n"

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
            if self.config.frequency_penalty is not None:
                completion_kwargs["frequency_penalty"] = self.config.frequency_penalty
            if self.config.presence_penalty is not None:
                completion_kwargs["presence_penalty"] = self.config.presence_penalty

            estimated_tokens = _estimate_tokens(
                completion_kwargs["messages"], completion_kwargs["model"]
            )
            logger.debug(
                "LLM request for thread '%s' (~%d tokens): %s",
                thread_id,
                estimated_tokens,
                json.dumps(completion_kwargs, default=str),
            )

            try:
                stream = await self.client.chat.completions.create(**completion_kwargs)
                attempt = 0
            except asyncio.CancelledError:
                logger.warning(
                    "LLM request cancelled for thread '%s' (client disconnected?)",
                    thread_id,
                )
                raise
            except Exception as exc:
                attempt += 1
                if attempt >= max_attempts:
                    error_summary = (
                        f"Request to the language model failed after {max_attempts} attempts "
                        f"({type(exc).__name__}). The conversation state may be invalid. "
                        "Try clearing the thread or rephrasing your request."
                    )
                    yield _graceful_error(error_summary)
                    break

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
            thinking_content = ""
            tool_calls: list[dict[str, Any]] = []
            finish_reason: str | None = None
            in_think_tag = False
            think_buffer = ""

            try:
                async for chunk in stream:
                    # Some providers (e.g. DeepSeek via DeepInfra) emit chunks with an
                    # empty choices list as keep-alives or final markers. Skip them.
                    if not chunk.choices:
                        continue
                    delta = chunk.choices[0].delta
                    finish_reason = chunk.choices[0].finish_reason or finish_reason

                    # Emit reasoning/thinking content from provider-specific fields.
                    reasoning = getattr(delta, "reasoning_content", None)
                    if not reasoning:
                        extra = getattr(delta, "model_extra", None) or {}
                        reasoning = extra.get("reasoning_content")
                    if reasoning:
                        thinking_content += reasoning
                        yield json.dumps({"type": "thinking", "data": reasoning}) + "\n"

                    if delta.content:
                        raw = delta.content
                        # Handle <think>...</think> reasoning blocks that may span chunks.
                        while raw:
                            if in_think_tag:
                                end = raw.find("</think>")
                                if end == -1:
                                    think_buffer += raw
                                    raw = ""
                                    continue
                                think_buffer += raw[:end]
                                if think_buffer:
                                    thinking_content += think_buffer
                                    yield json.dumps({"type": "thinking", "data": think_buffer}) + "\n"
                                think_buffer = ""
                                in_think_tag = False
                                raw = raw[end + len("</think>"):]
                                continue

                            start = raw.find("<think>")
                            if start == -1:
                                break
                            before = raw[:start]
                            if before:
                                assistant_content += before
                                yield json.dumps({"type": "content", "data": before}) + "\n"
                            raw = raw[start + len("<think>"):]
                            end = raw.find("</think>")
                            if end == -1:
                                think_buffer = raw
                                in_think_tag = True
                                raw = ""
                            else:
                                thinking = raw[:end]
                                if thinking:
                                    thinking_content += thinking
                                    yield json.dumps({"type": "thinking", "data": thinking}) + "\n"
                                raw = raw[end + len("</think>"):]

                        if raw:
                            assistant_content += raw
                            yield json.dumps({"type": "content", "data": raw}) + "\n"

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
            except asyncio.CancelledError:
                logger.warning(
                    "LLM stream cancelled for thread '%s' (client disconnected?)",
                    thread_id,
                )
                raise

            if tool_calls:
                tool_round += 1
                if tool_round > max_tool_rounds:
                    error_lines = "\n".join(f"- {e}" for e in recent_tool_errors[-5:])
                    error_summary = (
                        f"I tried using tools {tool_round - 1} times but kept running into issues. "
                        "Recent tool errors:\n" + (error_lines or "(no specific errors recorded)") +
                        "\n\nI stopped to avoid an API error. Try rephrasing your request, "
                        "or specify one simple action at a time."
                    )
                    yield _graceful_error(error_summary)
                    break

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
                        result = await self.tool_runner.run(
                            name, args, thread_id=self._current_thread_id
                        )
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

                    if "error" in json.loads(result):
                        recent_tool_errors.append(f"{name}: {result}")

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

        history = self.memory.get_messages(thread_id)

        # Normalize and persist the cleaned history back to memory.db so that
        # corrupted threads are repaired on disk, not just in the request payload.
        normalized_history, was_modified = Agent._normalize_messages(history)
        if was_modified:
            self.memory.rewrite_thread(thread_id, normalized_history)

        # Trim from the latest message backward so the loaded conversation stays
        # within the configured token budget. Cuts happen only at user-message
        # boundaries so assistant tool_call + tool response groups stay intact.
        normalized_history = self._trim_history_by_tokens(normalized_history, thread_id)

        # Prepend the thread_id to the latest user message so the agent always
        # knows which conversation it is in. This is needed for tools like
        # send_discord_message that must target the same channel/thread.
        # Memory stays clean because we only modify the copy sent to the LLM.
        if normalized_history and normalized_history[-1]["role"] == "user":
            normalized_history = list(normalized_history)
            normalized_history[-1] = {
                **normalized_history[-1],
                "content": f"[thread_id: {thread_id}] {normalized_history[-1]['content']}",
            }

        for msg in normalized_history:
            msg_copy = dict(msg)
            msg_copy.pop("created_at", None)
            messages.append(msg_copy)
        return messages

    def _trim_history_by_tokens(
        self, history: list[dict[str, Any]], thread_id: str
    ) -> list[dict[str, Any]]:
        """Return the most recent suffix of history that fits in the token budget.

        Walks backward from the latest message and accumulates token estimates
        until ``max_memory_tokens`` would be exceeded. Cuts are only made at
        user-message boundaries so that an assistant ``tool_calls`` message and
        its matching tool responses are never split.

        If ``max_memory_turns`` is also set, the result is further capped to that
        many messages.
        """
        max_tokens = self.config.max_memory_tokens
        max_turns = self.config.max_memory_turns
        if max_tokens <= 0 and max_turns <= 0:
            return history

        n = len(history)
        token_counts = [_estimate_tokens([msg], self.config.model) for msg in history]
        prefix = [0]
        for count in token_counts:
            prefix.append(prefix[-1] + count)

        # Turn-based cut (keep the most recent max_turns messages).
        turn_cut = 0
        if max_turns > 0:
            turn_cut = max(0, n - max_turns)

        if max_tokens <= 0:
            return history[turn_cut:]

        # Token-based cut: find the earliest user-message index whose suffix
        # fits within the budget. Iterating backward and updating the cut index
        # whenever a suffix fits keeps the maximum number of recent messages.
        token_cut = n
        for i in range(n - 1, -1, -1):
            if history[i].get("role") != "user":
                continue
            suffix_tokens = prefix[n] - prefix[i]
            if suffix_tokens <= max_tokens:
                token_cut = i
        if token_cut == n:
            # No user-message suffix fits the budget; keep at least the latest
            # user message so the conversation isn't completely empty.
            for i in range(n - 1, -1, -1):
                if history[i].get("role") == "user":
                    token_cut = i
                    break

        cut = max(turn_cut, token_cut)
        if cut > 0:
            logger.debug(
                "Trimmed thread '%s' history: kept %d/%d messages (~%d/%d tokens)",
                thread_id,
                n - cut,
                n,
                prefix[n] - prefix[cut],
                prefix[n],
            )
        return history[cut:]

    @staticmethod
    def _normalize_messages(
        messages: list[dict[str, Any]],
    ) -> tuple[list[dict[str, Any]], bool]:
        """Fix common message-history corruptions before sending to the LLM.

        Returns the cleaned message list and a boolean indicating whether any
        changes were made. This avoids rewriting ``memory.db`` when the history
        is already clean.

        Repairs issues that providers such as Gemini's OpenAI-compatible
        endpoint reject, including:

        - Missing ``name`` on tool messages
        - Concatenated/invalid JSON in tool-call ``function.arguments``
        - Empty/missing tool-call IDs
        - Consecutive user or assistant content messages
        - Dangling assistant tool_calls without matching tool responses
        - Orphan tool messages without a matching assistant tool_call
        """
        if not messages:
            return messages, False

        modified = False

        # --- Pass 1: clean individual tool_calls and tool messages -----------
        cleaned: list[dict[str, Any]] = []
        for msg in messages:
            msg_copy = dict(msg)
            role = msg_copy.get("role")

            if role == "assistant" and msg_copy.get("tool_calls"):
                cleaned_tcs: list[dict[str, Any]] = []
                for idx, tc in enumerate(msg_copy["tool_calls"]):
                    tc_copy = dict(tc)
                    function = dict(tc_copy.get("function", {}))
                    original_args = function.get("arguments", "")
                    args = Agent._parse_tool_arguments(original_args)
                    new_args = json.dumps(args)
                    if new_args != original_args:
                        modified = True
                    function["arguments"] = new_args
                    tc_copy["function"] = function
                    if not tc_copy.get("id"):
                        tc_copy["id"] = f"call_{idx}"
                        modified = True
                    cleaned_tcs.append(tc_copy)
                msg_copy["tool_calls"] = cleaned_tcs

            if role == "tool":
                if not msg_copy.get("name"):
                    msg_copy["name"] = ""
                    modified = True

            cleaned.append(msg_copy)

        # --- Pass 2: rebuild a valid alternating sequence --------------------
        result: list[dict[str, Any]] = []
        i = 0
        n = len(cleaned)
        seen_user = False
        while i < n:
            msg = cleaned[i]
            role = msg.get("role")

            if role == "system":
                result.append(msg)
                i += 1
                continue

            # Drop any messages before the first user turn; a valid conversation
            # must start with a user message after the system prompt.
            if not seen_user:
                if role != "user":
                    modified = True
                    i += 1
                    continue
                seen_user = True

            if role == "user":
                # Merge consecutive user messages.
                merged_content = msg.get("content") or ""
                merged_created_at = msg.get("created_at")
                j = i + 1
                while j < n and cleaned[j].get("role") == "user":
                    modified = True
                    next_content = cleaned[j].get("content") or ""
                    if next_content:
                        separator = "\n\n" if merged_content else ""
                        merged_content = f"{merged_content}{separator}{next_content}".strip()
                    next_created_at = cleaned[j].get("created_at")
                    if next_created_at and (
                        merged_created_at is None or next_created_at < merged_created_at
                    ):
                        merged_created_at = next_created_at
                    j += 1
                merged_msg = {"role": "user", "content": merged_content}
                if merged_created_at:
                    merged_msg["created_at"] = merged_created_at
                result.append(merged_msg)
                i = j
                continue

            if role == "assistant":
                # If previous result message is also assistant, we have a problem.
                # If both are plain content, merge them.
                if result and result[-1].get("role") == "assistant":
                    prev = result[-1]
                    if not msg.get("tool_calls") and not prev.get("tool_calls"):
                        modified = True
                        prev_content = prev.get("content") or ""
                        msg_content = msg.get("content") or ""
                        if msg_content:
                            separator = "\n\n" if prev_content else ""
                            prev["content"] = f"{prev_content}{separator}{msg_content}".strip()
                        msg_created_at = msg.get("created_at")
                        if msg_created_at and (
                            prev.get("created_at") is None or msg_created_at < prev["created_at"]
                        ):
                            prev["created_at"] = msg_created_at
                        i += 1
                        continue
                    # Otherwise skip this assistant message to preserve ordering.
                    modified = True
                    i += 1
                    continue

                if msg.get("tool_calls"):
                    # Collect immediately following tool messages.
                    expected_ids = {tc.get("id") for tc in msg["tool_calls"] if tc.get("id")}
                    j = i + 1
                    following_tools: list[dict[str, Any]] = []
                    while j < n and cleaned[j].get("role") == "tool":
                        following_tools.append(cleaned[j])
                        j += 1

                    found_ids = {t.get("tool_call_id") for t in following_tools}
                    if expected_ids and not expected_ids <= found_ids:
                        # Dangling tool_call without all responses: discard it
                        # and the partial tool responses that followed.
                        modified = True
                        i = j
                        continue

                    # Valid group: assistant tool_call + tool responses.
                    result.append(msg)
                    for tc in msg["tool_calls"]:
                        matching = None
                        for t in following_tools:
                            if t.get("tool_call_id") == tc.get("id"):
                                matching = t
                                break
                        if matching:
                            tool_copy = dict(matching)
                            if not tool_copy.get("name"):
                                tool_copy["name"] = tc["function"].get("name", "")
                                modified = True
                            result.append(tool_copy)
                    i = j
                    continue
                else:
                    # Plain assistant content.
                    result.append(msg)
                    i += 1
                    continue

            if role == "tool":
                # Orphan tool message without a preceding assistant tool_call.
                modified = True
                i += 1
                continue

            # Unknown role: drop it.
            modified = True
            i += 1

        # If the overall structure changed (drops/merges), mark modified.
        if len(result) != len(messages):
            modified = True

        return result, modified

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
