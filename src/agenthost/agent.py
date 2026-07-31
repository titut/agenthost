"""Core agent: loads config, tools, skills, memory, and runs the LLM loop."""

from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, AsyncIterator

from openai import AsyncOpenAI, BadRequestError

from agenthost.builtin_tools import build_builtin_tools_prompt, make_builtin_tools
from agenthost.config import AgentConfig, _estimate_tokens
from agenthost.embeddings import EmbeddingClient
from agenthost.logger import setup_logging
from agenthost.memory import AgentMemory
from agenthost.skills import load_skills
from agenthost.toolbox import get_toolboxes_root, list_toolboxes, validate_toolbox_name
from agenthost.tools import ToolRunner, discover_tools


def _current_datetime_message() -> str:
    """Return a concise current date and time message for the system prompt."""
    now = datetime.now(timezone.utc).astimezone()
    return (
        f"Current date and time: {now.strftime('%Y-%m-%d %H:%M:%S')} "
        f"({now.strftime('%Z')}, UTC offset {now.strftime('%z')}). "
        f"Use this when answering questions about 'today', 'now', 'current', or any time-sensitive topic."
    )


logger = setup_logging("agenthost.agent")


class ThreadState:
    """Per-thread toolbox state, tracked separately for each conversation.

    Every thread starts with the agent's base tools and no active toolbox.
    Calling ``toolbox(action='switch', target='<name>')`` updates only that
    thread's state.
    """

    def __init__(
        self,
        tool_schemas: list[dict[str, Any]],
        tool_runner: ToolRunner,
    ):
        self.tool_schemas = tool_schemas
        self.tool_runner = tool_runner
        self.active_toolbox: str | None = None
        self.active_toolbox_path: Path | None = None
        self.active_toolbox_skills: dict[str, str] = {}


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
        self.embedding_client = EmbeddingClient(config.embedding)
        self.memory = AgentMemory(config, self.embedding_client)
        self.builtin_functions = make_builtin_tools(
            config, scheduler, agent_provider=lambda: self
        )
        self.default_tool_schemas, self.default_tool_runner = discover_tools(
            config.tools_dir, self.builtin_functions, config=config
        )
        # Per-thread toolbox state. Each thread can independently switch
        # toolboxes; threads that have never switched use the agent's default
        # tools and skills. Memory and conversation state persist across
        # toolbox switches within a thread.
        self.thread_states: dict[str, ThreadState] = {}
        # Buffer the entire assistant response for orchestrator agents so we can
        # apply a post-processing sanitizer before streaming it to clients.
        self._buffer_content = config.orchestrator
        # Threads in planning mode (activated by /plan prefix).  Planning-mode
        # threads see only built-in tools (no toolbox tools) plus the planning
        # tool itself.  Normal threads see full toolbox tools and no plan tool.
        self._planning_threads: set[str] = set()
        # Pre-built schemas + runner for planning mode — only built-in tools.
        planning_fns: dict[str, Any] = {
            "get_current_datetime": self.builtin_functions["get_current_datetime"],
            "get_skill": self.builtin_functions["get_skill"],
        }
        if "plan" in self.builtin_functions:
            planning_fns["plan"] = self.builtin_functions["plan"]
        from agenthost.tools import InProcessToolRunner, _build_tool_schema

        self._planning_tool_schemas = [
            _build_tool_schema(fn) for fn in planning_fns.values()
        ]
        self._planning_tool_runner = InProcessToolRunner(planning_fns, config=config)

    def enter_planning_mode(self, thread_id: str) -> None:
        """Restrict *thread_id* to built-in tools only (plus the plan tool).

        Called when the user sends a ``/plan`` prefixed message.
        """
        self._planning_threads.add(thread_id)
        # Replace the thread's state with planning-mode schemas so the next
        # LLM call sees only built-in tools.
        self.thread_states[thread_id] = ThreadState(
            tool_schemas=self._planning_tool_schemas,
            tool_runner=self._planning_tool_runner,
        )
        logger.info("Thread '%s' entered planning mode", thread_id)

    def exit_planning_mode(self, thread_id: str) -> None:
        """Restore full toolbox tools for *thread_id*.

        Called when the user sends a ``/noplan`` prefixed message.
        """
        self._planning_threads.discard(thread_id)
        # Restore default tools and skills.
        self.thread_states[thread_id] = ThreadState(
            tool_schemas=self.default_tool_schemas,
            tool_runner=self.default_tool_runner,
        )
        logger.info("Thread '%s' exited planning mode", thread_id)

    def _get_thread_state(self, thread_id: str) -> ThreadState:
        """Return the toolbox state for *thread_id*, creating it if needed.

        A new state starts with the agent's default tools and no active toolbox.
        If the thread is in planning mode, only built-in tools are exposed.
        """
        if thread_id not in self.thread_states:
            if thread_id in self._planning_threads:
                self.thread_states[thread_id] = ThreadState(
                    tool_schemas=self._planning_tool_schemas,
                    tool_runner=self._planning_tool_runner,
                )
            else:
                self.thread_states[thread_id] = ThreadState(
                    tool_schemas=self.default_tool_schemas,
                    tool_runner=self.default_tool_runner,
                )
        return self.thread_states[thread_id]

    def _system_prompt_initial(self, thread_id: str) -> str:
        """Return the initial system prompt for a specific thread.

        Rebuilt from disk each request so skill additions/deletions become
        visible on the next turn without restarting. Toolbox-specific skills
        and tools are taken from the thread's current state.
        """
        state = self._get_thread_state(thread_id)
        skills_dir = (
            state.active_toolbox_path / "skills"
            if state.active_toolbox_path is not None
            else None
        )
        prompt = self.config.build_system_prompt(skills_dir)
        if state.active_toolbox is not None:
            prompt += (
                f"\n\n# Active Toolbox\n\n"
                f"You are currently using the `{state.active_toolbox}` toolbox. "
                f"The tools and skills shown above belong to this toolbox. "
                f"To change toolboxes, call `toolbox(action='switch', target='<name>')`. "
                f"Pass an empty target to revert to the agent's default tools and skills."
            )
        return prompt + build_builtin_tools_prompt(self.config)

    def switch_toolbox(self, name: str) -> str:
        """Switch the active toolbox for the current thread.

        The thread is determined from ``self._current_thread_id`` (set during
        ``chat()``).  Passing an empty name reverts the thread to the agent's
        base tools and skills.  Built-in tools are preserved across switches.

        Other threads are unaffected — they keep whatever toolbox they had
        before (or the agent default if they never switched).
        """
        thread_id: str = getattr(self, "_current_thread_id", "")

        if not name:
            state = self._get_thread_state(thread_id)
            state.tool_schemas = self.default_tool_schemas
            state.tool_runner = self.default_tool_runner
            state.active_toolbox = None
            state.active_toolbox_path = None
            state.active_toolbox_skills = {}
            return json.dumps(
                {
                    "status": "reverted",
                    "toolbox": None,
                    "message": "Switched back to the agent's default tools and skills.",
                }
            )

        if not validate_toolbox_name(name):
            return json.dumps({"error": f"Invalid toolbox name '{name}'."})

        toolbox_path = get_toolboxes_root(self.config.toolboxes_dir) / name
        if not toolbox_path.exists():
            available = list_toolboxes(self.config.toolboxes_dir)
            return json.dumps(
                {
                    "error": f"Toolbox '{name}' not found.",
                    "available": available,
                }
            )

        if (
            not (toolbox_path / "tools").is_dir()
            and not (toolbox_path / "skills").is_dir()
        ):
            return json.dumps(
                {
                    "error": (
                        f"Toolbox '{name}' has no tools/ or skills/ directory; "
                        "nothing to load."
                    ),
                }
            )

        state = self._get_thread_state(thread_id)
        state.tool_schemas, state.tool_runner = discover_tools(
            toolbox_path / "tools", self.builtin_functions, config=self.config
        )
        state.active_toolbox = name
        state.active_toolbox_path = toolbox_path
        state.active_toolbox_skills = load_skills(toolbox_path / "skills")

        available_tools = [schema["function"]["name"] for schema in state.tool_schemas]
        available_skills = list(state.active_toolbox_skills.keys())

        return json.dumps(
            {
                "status": "switched",
                "toolbox": name,
                "tools": available_tools,
                "skills": available_skills,
            },
            indent=2,
        )

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
        await self.memory.append_message(
            thread_id, {"role": "user", "content": user_message}
        )
        messages = await self._build_messages(thread_id)

        attempt = 0
        max_attempts = 5
        tool_round = 0
        max_tool_rounds = 15
        recent_tool_errors: list[str] = []
        pending_tool_messages: list[dict[str, Any]] = []

        async def _graceful_error(message: str) -> str:
            logger.error(message)
            await self.memory.append_message(
                thread_id, {"role": "assistant", "content": message}
            )
            return json.dumps({"type": "error", "data": message}) + "\n"

        while True:
            state = self._get_thread_state(thread_id)
            completion_kwargs: dict[str, Any] = {
                "model": self.config.model,
                "messages": messages,
                "tools": state.tool_schemas or None,
                "tool_choice": "auto" if state.tool_schemas else None,
                "temperature": self.config.temperature,
                "stream": True,
            }
            if self.config.max_tokens is not None:
                completion_kwargs["max_tokens"] = self.config.max_tokens
            # Continuation calls skip penalties (prior output is in context, so
            # every word is already penalized) and disable reasoning (the model
            # already deliberated in call 1; continuation calls focus on visible
            # text).
            if not getattr(self, "_in_continuation", False):
                if self.config.frequency_penalty is not None:
                    completion_kwargs["frequency_penalty"] = (
                        self.config.frequency_penalty
                    )
                if self.config.presence_penalty is not None:
                    completion_kwargs["presence_penalty"] = self.config.presence_penalty
                if self.config.thinking is not None:
                    completion_kwargs["reasoning_effort"] = self.config.thinking
            else:
                completion_kwargs["reasoning_effort"] = "none"

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
                    yield await _graceful_error(error_summary)
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
                        logger.warning(
                            "Repaired %d messages in thread '%s'", removed, thread_id
                        )
                except Exception as repair_err:
                    logger.warning(
                        "Repair failed for thread '%s': %s", thread_id, repair_err
                    )

                messages = await self._build_messages(thread_id, pending_tool_messages)
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
                                    yield json.dumps(
                                        {"type": "thinking", "data": think_buffer}
                                    ) + "\n"
                                think_buffer = ""
                                in_think_tag = False
                                raw = raw[end + len("</think>") :]
                                continue

                            start = raw.find("<think>")
                            if start == -1:
                                break
                            before = raw[:start]
                            if before:
                                assistant_content += before
                                if not self._buffer_content:
                                    yield json.dumps(
                                        {"type": "content", "data": before}
                                    ) + "\n"
                            raw = raw[start + len("<think>") :]
                            end = raw.find("</think>")
                            if end == -1:
                                think_buffer = raw
                                in_think_tag = True
                                raw = ""
                            else:
                                thinking = raw[:end]
                                if thinking:
                                    thinking_content += thinking
                                    yield json.dumps(
                                        {"type": "thinking", "data": thinking}
                                    ) + "\n"
                                raw = raw[end + len("</think>") :]

                        if raw:
                            assistant_content += raw
                            if not self._buffer_content:
                                yield json.dumps(
                                    {"type": "content", "data": raw}
                                ) + "\n"

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
                                tool_calls.append(
                                    {
                                        "id": f"call_{len(tool_calls)}",
                                        "type": "function",
                                        "function": {"name": "", "arguments": ""},
                                    }
                                )
                            if tc.id:
                                tool_calls[index]["id"] = tc.id
                            if tc.function and tc.function.name:
                                tool_calls[index]["function"]["name"] = tc.function.name
                            if tc.function and tc.function.arguments:
                                tool_calls[index]["function"][
                                    "arguments"
                                ] += tc.function.arguments

                            # Preserve provider-specific fields (e.g. Gemini's
                            # thought_signature) that the OpenAI SDK does not model.
                            extra = getattr(tc, "model_extra", None) or {}
                            for key, value in extra.items():
                                if (
                                    key not in ("id", "index", "type", "function")
                                    and value is not None
                                ):
                                    tool_calls[index][key] = value
                                    logger.debug(
                                        "Preserved tool-call extra field: %s", key
                                    )
            except asyncio.CancelledError:
                logger.warning(
                    "LLM stream cancelled for thread '%s' (client disconnected?)",
                    thread_id,
                )
                raise

            logger.info(
                "Agent '%s' thread '%s' stream ended: finish_reason=%s, "
                "content_len=%d, tool_calls=%d, in_continuation=%s",
                self.config.name,
                thread_id,
                finish_reason,
                len(assistant_content),
                len(tool_calls),
                getattr(self, "_in_continuation", False),
            )

            if assistant_content and self._buffer_content:
                sanitized = self._sanitize_output(assistant_content)
                if sanitized != assistant_content:
                    logger.warning(
                        "Sanitized degenerate output tail for agent '%s' thread '%s' "
                        "(%d -> %d chars)",
                        self.config.name,
                        thread_id,
                        len(assistant_content),
                        len(sanitized),
                    )
                assistant_content = sanitized
                for chunk in self._chunk_text(assistant_content, chunk_size=1000):
                    yield json.dumps({"type": "content", "data": chunk}) + "\n"

            # If the model hit max_tokens mid-generation (no tool_calls, just a
            # partial response), inject the partial output and a continuation
            # prompt so the model can pick up where it left off with a fresh
            # attention window. This prevents the model from losing the plot
            # during long-running single-generation tasks.
            if finish_reason == "length" and not tool_calls:
                logger.info(
                    "Agent '%s' thread '%s' hit max_tokens mid-generation; "
                    "continuing with fresh attention window (call_1_len=%d, "
                    "call_1_tail='%s')",
                    self.config.name,
                    thread_id,
                    len(assistant_content),
                    assistant_content[-200:].replace("\n", "\\n"),
                )
                self._in_continuation = True
                yield json.dumps({"type": "continuation"}) + "\n"
                # Append the partial assistant output and continuation prompt
                # to the existing pending_tool_messages (which already contain
                # this turn's tool_calls and tool results). The DB history
                # only contains the user message at this point, so the tool
                # interactions must flow through pending_tool_messages.
                if assistant_content:
                    pending_tool_messages.append(
                        {"role": "assistant", "content": assistant_content}
                    )
                if thinking_content:
                    pending_tool_messages.append(
                        {
                            "role": "system",
                            "content": f"[Previous reasoning:\n{thinking_content}\n]",
                            "ephemeral": True,
                        }
                    )
                pending_tool_messages.append(
                    {
                        "role": "system",
                        "content": (
                            "You were cut off mid-response. Continue exactly where you "
                            "left off. Do NOT repeat or summarize what was already written."
                        ),
                    }
                )
                assistant_content = ""
                thinking_content = ""
                messages = await self._build_messages(thread_id, pending_tool_messages)
                logger.info(
                    "Agent '%s' thread '%s' continuation: rebuilt messages=%d, "
                    "pending_tool_msgs=%d, last_5_roles=%s",
                    self.config.name,
                    thread_id,
                    len(messages),
                    len(pending_tool_messages),
                    [m.get("role") for m in messages[-5:]],
                )
                continue

            if tool_calls:
                tool_round += 1
                if tool_round > max_tool_rounds:
                    error_lines = "\n".join(f"- {e}" for e in recent_tool_errors[-5:])
                    error_summary = (
                        f"I tried using tools {tool_round - 1} times but kept running into issues. "
                        "Recent tool errors:\n"
                        + (error_lines or "(no specific errors recorded)")
                        + "\n\nI stopped to avoid an API error. Try rephrasing your request, "
                        "or specify one simple action at a time."
                    )
                    yield await _graceful_error(error_summary)
                    break

                logger.debug(
                    "LLM response for thread '%s' produced tool_calls: %s",
                    thread_id,
                    json.dumps(tool_calls, default=str),
                )
                # Keep the assistant tool-call request and matching tool results in
                # memory only for the current turn; they are passed to the next LLM
                # request via pending_tool_messages and discarded afterward.
                pending_tool_messages.append(
                    {"role": "assistant", "tool_calls": tool_calls}
                )

                for tc in tool_calls:
                    name = tc["function"]["name"]
                    args = self._parse_tool_arguments(tc["function"]["arguments"])

                    logger.info(
                        "Agent '%s' thread '%s' calling tool '%s'",
                        self.config.name,
                        thread_id,
                        name,
                    )
                    yield json.dumps(
                        {
                            "type": "tool_start",
                            "data": {"name": name, "arguments": args},
                        }
                    ) + "\n"
                    try:
                        result = await state.tool_runner.run(
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
                            {
                                "error": f"Tool '{name}' failed: {type(exc).__name__}: {exc}"
                            }
                        )
                        yield json.dumps(
                            {
                                "type": "tool_error",
                                "data": {"name": name, "error": result},
                            }
                        ) + "\n"
                    yield json.dumps(
                        {
                            "type": "tool_result",
                            "data": {"name": name, "result": result},
                        }
                    ) + "\n"

                    # Tool results may be plain strings (e.g. "FILE_PATH: ...") or JSON.
                    # Only treat a parsed dict containing "error" as a tool error.
                    try:
                        parsed_result = json.loads(result)
                        if isinstance(parsed_result, dict) and "error" in parsed_result:
                            recent_tool_errors.append(f"{name}: {result}")
                    except json.JSONDecodeError:
                        pass

                    tool_msg = {
                        "role": "tool",
                        "tool_call_id": tc["id"],
                        "name": name,
                        "content": result,
                    }
                    pending_tool_messages.append(tool_msg)

                # Rebuild the message list with the pending tool messages so the
                # final system prompt (critical instructions) is placed after them.
                messages = await self._build_messages(thread_id, pending_tool_messages)
                continue

            if assistant_content:
                logger.debug(
                    "LLM response for thread '%s' produced content: %s",
                    thread_id,
                    assistant_content,
                )
                await self.memory.append_message(
                    thread_id, {"role": "assistant", "content": assistant_content}
                )

            # Persist this turn's tool interactions (assistant tool_calls + tool
            # results) to memory so they are available for RAG retrieval in future
            # turns. They are NOT included in the recent-message window — only
            # user and final assistant text messages count toward that limit.
            for pmsg in pending_tool_messages:
                if not pmsg.get("ephemeral"):
                    await self.memory.append_message(thread_id, pmsg)

            logger.info(
                "Agent '%s' thread '%s' finished turn with %d total messages",
                self.config.name,
                thread_id,
                len(messages),
            )
            break

    async def _build_messages(
        self,
        thread_id: str,
        pending_tool_messages: list[dict[str, Any]] | None = None,
    ) -> list[dict[str, Any]]:
        # Build the prompt for the next generation. The initial system prompt
        # (WHOAMI / role) goes at the start, optional RAG context from older
        # messages follows, then the recent conversation history, the current
        # user message, any in-progress tool calls and results for this turn,
        # the current date/time, and finally the critical instructions as the
        # freshest context.
        #
        # Tool call and tool result messages are passed in pending_tool_messages
        # instead of being persisted to memory, so they don't consume slots in
        # the recent-message window after this turn ends.
        messages: list[dict[str, Any]] = [
            {"role": "system", "content": self._system_prompt_initial(thread_id)}
        ]

        history = self.memory.get_messages(thread_id)

        # Normalize and persist the cleaned history back to memory.db so that
        # corrupted threads are repaired on disk, not just in the request payload.
        normalized_history, was_modified = Agent._normalize_messages(history)
        if was_modified:
            await self.memory.rewrite_thread(thread_id, normalized_history)

        # Split history into the recent window (verbatim) and older messages
        # (retrieved via RAG). Tool interactions (assistant tool_calls and tool
        # results) are excluded from the recent window so they don't consume
        # slots, but they remain in the full history for RAG retrieval.
        def _is_tool_interaction(msg: dict[str, Any]) -> bool:
            if msg.get("role") == "tool":
                return True
            if msg.get("role") == "assistant" and msg.get("tool_calls"):
                return True
            return False

        conversation_history = [
            m for m in normalized_history if not _is_tool_interaction(m)
        ]

        cfg = self.config.memory
        recent_count = max(cfg.recent_messages, 1)
        # Use the conversation-only list (no tool interactions) for the recent
        # window. Fall back to the full history if filtering left nothing.
        if conversation_history:
            recent_history = conversation_history[-recent_count:]
            older_history = (
                conversation_history[:-recent_count]
                if len(conversation_history) > recent_count
                else []
            )
        else:
            recent_history = normalized_history[-recent_count:]
            older_history = []

        # Build a compound RAG query from the recent conversation window so
        # the embedding captures the full semantic arc, not just the latest
        # user message. This improves retrieval of relevant older chunks
        # (including tool interactions) when the user asks a short follow-up.
        def _role_prefix(role: str | None) -> str:
            """Return a short label prefix for a message role."""
            if role == "user":
                return "User:"
            if role == "assistant":
                return "Assistant:"
            return f"{role or 'unknown'}:"

        query_parts: list[str] = []
        for msg in recent_history:
            content = msg.get("content")
            if content:
                query_parts.append(f"{_role_prefix(msg.get('role'))} {content}")
        query_text = "\n".join(query_parts)

        # Retrieve relevant chunks from the full history. Tool interactions
        # (stored in the DB but excluded from the recent window) are included
        # in the candidate pool. Messages already in the recent window are
        # excluded via exclude_message_ids so we don't duplicate context.
        if normalized_history and query_text:
            recent_ids = {msg.get("id") for msg in recent_history if msg.get("id")}
            rag_chunks = await self.memory.retrieve_relevant_chunks(
                thread_id,
                query_text,
                exclude_message_ids=recent_ids,
            )
            if rag_chunks:
                rag_text = "\n\n".join(
                    f"[{i + 1}] {chunk['chunk_text']}"
                    for i, chunk in enumerate(rag_chunks)
                )
                messages.append(
                    {
                        "role": "system",
                        "content": (
                            "Relevant context from earlier in the conversation:\n\n"
                            f"{rag_text}"
                        ),
                    }
                )

        # Separate the current user turn from earlier history. The final system
        # prompt reminder is inserted immediately before the current user message
        # so the model sees it right before it must respond.
        current_user: dict[str, Any] | None = None
        if recent_history and recent_history[-1]["role"] == "user":
            recent_history = list(recent_history)
            current_user = recent_history.pop()
            # Prepend the thread_id to the latest user message so the agent always
            # knows which conversation it is in. This is needed for tools like
            # send_discord that must target the same channel/thread.
            # Memory stays clean because we only modify the copy sent to the LLM.
            current_user = {
                **current_user,
                "content": f"[thread_id: {thread_id}] {current_user['content']}",
            }

        for msg in recent_history:
            msg_copy = dict(msg)
            msg_copy.pop("id", None)
            msg_copy.pop("created_at", None)
            messages.append(msg_copy)

        if current_user is not None:
            current_user_copy = dict(current_user)
            current_user_copy.pop("id", None)
            current_user_copy.pop("created_at", None)
            messages.append(current_user_copy)

        # Inject tool calls and results for the current turn. These are kept out
        # of memory so they don't crowd the recent-message window; they are only
        # needed to let the model respond to the current turn's tools.
        if pending_tool_messages:
            messages.extend(pending_tool_messages)

        # The current date and time are injected on every request so the agent
        # always has a fresh temporal reference, even when previous tool results
        # in the conversation history are stale.
        messages.append({"role": "system", "content": _current_datetime_message()})

        # Place the critical-instruction reminder at the very end of the payload.
        # This ensures it is the freshest context for the next assistant generation,
        # even when we rebuild the list after tool results mid-turn.
        if self.config.critical_instructions:
            messages.append(
                {"role": "system", "content": self.config.critical_instructions}
            )

        return messages

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
                        merged_content = (
                            f"{merged_content}{separator}{next_content}".strip()
                        )
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
                            prev["content"] = (
                                f"{prev_content}{separator}{msg_content}".strip()
                            )
                        msg_created_at = msg.get("created_at")
                        if msg_created_at and (
                            prev.get("created_at") is None
                            or msg_created_at < prev["created_at"]
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
                    expected_ids = {
                        tc.get("id") for tc in msg["tool_calls"] if tc.get("id")
                    }
                    j = i + 1
                    following_tools: list[dict[str, Any]] = []
                    while j < n and cleaned[j].get("role") == "tool":
                        following_tools.append(cleaned[j])
                        j += 1

                    found_ids = {t.get("tool_call_id") for t in following_tools}
                    had_missing_responses = (
                        expected_ids and not expected_ids <= found_ids
                    )
                    if had_missing_responses:
                        # Dangling tool_call after a stop/interrupt. Preserve the
                        # assistant message and any real tool responses, then add
                        # synthetic results for missing tool_call_ids so the
                        # conversation remains API-valid and resumable.
                        modified = True

                    # Valid or repaired group: assistant tool_call + tool responses.
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
                        else:
                            synthetic_tool = {
                                "role": "tool",
                                "tool_call_id": tc["id"],
                                "name": tc["function"].get("name", ""),
                                "content": "[Tool call interrupted before a result was received. The user may want to continue from here.]",
                            }
                            result.append(synthetic_tool)
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

    @staticmethod
    def _sanitize_output(text: str, max_tail_words_without_period: int = 8) -> str:
        """Trim degenerate trailing text such as synonym chains.

        If the final run of words after the last sentence-ending punctuation is
        longer than the threshold, truncate back to that punctuation. This is a
        safety net for models that keep generating related words instead of stopping.

        Code blocks and very short tails are left untouched.
        """
        text = text.rstrip()
        if not text:
            return text

        # Preserve complete code blocks and other structured endings.
        if text.endswith("```"):
            return text

        punct = ".!?。！？"
        last_punct_idx = max((text.rfind(c) for c in punct), default=-1)
        if last_punct_idx <= 0:
            return text

        tail = text[last_punct_idx + 1 :]
        words = tail.split()
        if len(words) > max_tail_words_without_period:
            # Include trailing quote/parenthesis/bracket characters after the
            # punctuation, then strip any trailing whitespace.
            end = last_punct_idx + 1
            while end < len(text) and text[end] in "'\"\u201d\u2019)]} ":
                end += 1
            return text[:end].rstrip()

        return text

    @staticmethod
    def _chunk_text(text: str, chunk_size: int = 1000) -> list[str]:
        """Split text into chunks without breaking paragraphs or code blocks.

        Prefers breaks at paragraph boundaries, then line boundaries, then
        falls back to the requested size. This keeps streamed output readable
        while avoiding single enormous SSE events.
        """
        if not text:
            return []
        if len(text) <= chunk_size:
            return [text]

        chunks: list[str] = []
        i = 0
        while i < len(text):
            end = min(i + chunk_size, len(text))
            if end < len(text):
                search_start = i + int(chunk_size * 0.8)
                paragraph_break = text.rfind("\n\n", search_start, end)
                if paragraph_break != -1:
                    end = paragraph_break + 2
                else:
                    line_break = text.rfind("\n", search_start, end)
                    if line_break != -1:
                        end = line_break + 1
                    else:
                        space_break = text.rfind(" ", search_start, end)
                        if space_break != -1:
                            end = space_break + 1
            chunks.append(text[i:end])
            i = end
        return chunks
