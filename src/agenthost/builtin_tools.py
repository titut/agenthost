"""Built-in tools available to every agent.

These tools are injected into every agent's tool runner. They give agents the
ability to manage their own configuration, such as scheduled events.
"""

from __future__ import annotations

import json
import os
import re
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx

import yaml
from apscheduler.schedulers.asyncio import AsyncIOScheduler

from agenthost.config import AgentConfig
from agenthost.events import (
    EventsConfig,
    ScheduledEvent,
    load_events,
    run_scheduled_event,
)
from agenthost.filesystem_tools import FileSystemTools
from agenthost.home import get_agenthost_home
from agenthost.logger import setup_logging

if False:
    # Imported only for type checking; avoid circular import at runtime.
    from agenthost.agent import Agent


logger = setup_logging("agenthost.builtin_tools")


def _is_valid_skill_name(name: str) -> bool:
    """Return True if name is a safe skill file stem."""
    if not name or len(name) > 100:
        return False
    return bool(re.fullmatch(r"[a-zA-Z0-9_-]+", name))


def _has_description_section(content: str) -> bool:
    """Return True if the markdown contains a '# Description' section."""
    for line in content.splitlines():
        if line.strip().lower() in ("# description", "## description"):
            return True
    return False


def _backup_skill(path: Path) -> Path:
    """Back up an existing skill file before overwriting or deleting it."""
    backup_path = path.with_suffix(".md.bak")
    if backup_path.exists():
        backup_path.unlink()
    path.rename(backup_path)
    return backup_path


def _normalize_string_or_list(value: str | list[str] | None) -> list[str] | None:
    """Normalize a string argument to a list, or return a list as-is."""
    if value is None:
        return None
    if isinstance(value, list):
        return value
    text = value.strip()
    if text.startswith("[") and text.endswith("]"):
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            pass
    return [item.strip() for item in text.split(",") if item.strip()]


class EventTools:
    """CRUD operations for an agent's events.yaml, bound to a live scheduler."""

    def __init__(
        self,
        config: AgentConfig,
        scheduler: AsyncIOScheduler | None,
        agent_provider: Callable[[], "Agent"] | None = None,
    ):
        self.config = config
        self.scheduler = scheduler
        self._agent_provider = agent_provider

    def _events_file(self) -> Path:
        return self.config.path / "events.yaml"

    def _load(self) -> EventsConfig:
        path = self._events_file()
        if not path.exists():
            return EventsConfig()
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        return EventsConfig.model_validate(data)

    def _save(self, config: EventsConfig) -> None:
        path = self._events_file()
        path.write_text(
            yaml.safe_dump(
                config.model_dump(mode="json", exclude_none=True), sort_keys=False
            ),
            encoding="utf-8",
        )

    def _reload_scheduler(self) -> None:
        """Remove existing event jobs and re-add them from the updated file."""
        if self.scheduler is None:
            logger.warning("No scheduler available; events will load on next restart")
            return

        # Remove all jobs for this agent.
        for job in self.scheduler.get_jobs():
            if job.id.startswith(f"{self.config.name}-"):
                self.scheduler.remove_job(job.id)

        events, file_timezone = load_events(self.config.path)
        scheduled_count = 0
        for event in events:
            if not event.enabled:
                continue
            try:
                triggers = event.schedule.to_triggers(file_timezone)
            except Exception as exc:
                logger.error("Invalid schedule for event '%s': %s", event.name, exc)
                continue
            for idx, trigger in enumerate(triggers):
                job_id = f"{self.config.name}-{event.name}-{idx}"
                agent = self._agent_provider() if self._agent_provider else None
                if agent is None:
                    logger.warning(
                        "Cannot reschedule event '%s': agent not available",
                        event.name,
                    )
                    continue
                self.scheduler.add_job(
                    run_scheduled_event,
                    trigger=trigger,
                    args=(agent, event),
                    id=job_id,
                    replace_existing=True,
                )
                scheduled_count += 1

        logger.info(
            "Reloaded %d event trigger jobs for agent '%s'",
            scheduled_count,
            self.config.name,
        )

    def list_events(self) -> str:
        """List all scheduled events in this agent's events.yaml."""
        cfg = self._load()
        events = [json.loads(event.model_dump_json()) for event in cfg.events]
        return json.dumps({"timezone": cfg.timezone, "events": events}, indent=2)

    def add_event(
        self,
        name: str,
        schedule_type: list[str],
        prompt: str | list[str],
        time: list[str] | None = None,
        every: int | None = None,
        unit: str | None = None,
        at: str | None = None,
        enabled: bool = True,
        fresh: bool = True,
    ) -> str:
        """Add a new scheduled event to this agent's events.yaml.

        Args:
            name: Unique name for the event.
            schedule_type: List of schedule types. Each one of: daily, weekdays,
                weekends, monday-sunday, interval, once. For multiple days, pass
                e.g. ['monday', 'wednesday'].
            prompt: Message(s) sent to the agent when the event fires. Pass a
                single string for one step, or a list of strings to run multiple
                prompts one after another in the same thread.
            time: List of times in HH:MM or HH:MM:SS format. Required for day-based
                schedules. For multiple times, pass e.g. ['09:00', '23:00'].
            every: Required for interval schedules.
            unit: Required for interval schedules: seconds, minutes, hours, days.
            at: Required for once schedules, ISO 8601 datetime.
            enabled: Whether the event is active.
            fresh: When True (default), clears thread history before each run.
        """
        cfg = self._load()
        if any(e.name == name for e in cfg.events):
            return json.dumps({"error": f"Event '{name}' already exists"})

        schedule: dict[str, Any] = {"type": schedule_type}
        if time is not None:
            schedule["time"] = time
        if every is not None:
            schedule["every"] = every
        if unit is not None:
            schedule["unit"] = unit
        if at is not None:
            schedule["at"] = at

        try:
            event = ScheduledEvent(
                name=name,
                schedule=schedule,
                prompt=prompt,
                enabled=enabled,
                fresh=fresh,
            )
        except Exception as exc:  # noqa: BLE001
            return json.dumps({"error": f"Invalid event: {exc}"})

        cfg.events.append(event)
        self._save(cfg)
        self._reload_scheduler()
        return json.dumps({"status": "added", "name": name})

    def update_event(
        self,
        name: str,
        schedule_type: list[str] | None = None,
        prompt: str | list[str] | None = None,
        time: list[str] | None = None,
        every: int | None = None,
        unit: str | None = None,
        at: str | None = None,
        enabled: bool | None = None,
        fresh: bool | None = None,
    ) -> str:
        """Update an existing scheduled event. Only provided fields are changed.

        Args:
            name: Name of the event to update.
            schedule_type: List of schedule types. Each one of: daily, weekdays,
                weekends, monday-sunday, interval, once. For multiple days, pass
                e.g. ['monday', 'wednesday'].
            prompt: Message(s) sent to the agent when the event fires. Pass a
                single string for one step, or a list of strings to run multiple
                prompts one after another in the same thread.
            time: List of times in HH:MM or HH:MM:SS format. For multiple times,
                pass e.g. ['09:00', '23:00'].
            fresh: When True, clears thread history before each run.
        """
        cfg = self._load()
        event = next((e for e in cfg.events if e.name == name), None)
        if event is None:
            return json.dumps({"error": f"Event '{name}' not found"})

        schedule = event.schedule.model_dump(mode="json")
        if schedule_type is not None:
            schedule["type"] = schedule_type
        if time is not None:
            schedule["time"] = time
        if every is not None:
            schedule["every"] = every
        if unit is not None:
            schedule["unit"] = unit
        if at is not None:
            schedule["at"] = at

        try:
            new_schedule = event.schedule.__class__.model_validate(schedule)
        except Exception as exc:  # noqa: BLE001
            return json.dumps({"error": f"Invalid schedule: {exc}"})

        event.schedule = new_schedule
        if prompt is not None:
            event.prompt = prompt
        if enabled is not None:
            event.enabled = enabled
        if fresh is not None:
            event.fresh = fresh

        self._save(cfg)
        self._reload_scheduler()
        return json.dumps({"status": "updated", "name": name})

    def delete_event(self, name: str) -> str:
        """Delete a scheduled event by name."""
        cfg = self._load()
        original_len = len(cfg.events)
        cfg.events = [e for e in cfg.events if e.name != name]
        if len(cfg.events) == original_len:
            return json.dumps({"error": f"Event '{name}' not found"})

        self._save(cfg)
        self._reload_scheduler()
        return json.dumps({"status": "deleted", "name": name})

    def event_tool(
        self,
        action: str,
        action_name: str | None = None,
        schedule_type: list[str] | None = None,
        prompt: str | list[str] | None = None,
        time: list[str] | None = None,
        every: int | None = None,
        unit: str | None = None,
        at: str | None = None,
        enabled: bool | None = None,
    ) -> str:
        """Unified CRUD tool for this agent's scheduled events.

        Args:
            action: One of "list", "add", "update", "delete".
            action_name: Name of the event. Required for add/update/delete.
            schedule_type: List of schedule types. Each one of: daily, weekdays,
                weekends, monday-sunday, interval, once. For multiple days, pass
                e.g. ['monday', 'wednesday'].
            prompt: Message(s) sent to the agent when the event fires. Pass a
                single string for one step, or a list of strings to run multiple
                prompts one after another in the same thread.
            time: List of times in HH:MM or HH:MM:SS format. Required for day-based
                schedules. For multiple times, pass e.g. ['09:00', '23:00'].
            every: Required for interval schedules.
            unit: Required for interval schedules: seconds, minutes, hours, days.
            at: Required for once schedules, ISO 8601 datetime.
            enabled: Whether the event is active.
        """
        action = action.lower().strip()
        schedule_type = _normalize_string_or_list(schedule_type)
        time = _normalize_string_or_list(time)
        if action == "list":
            return self.list_events()
        if action == "add":
            if not action_name:
                return json.dumps({"error": "action_name is required for add"})
            if not schedule_type:
                return json.dumps({"error": "schedule_type is required for add"})
            if prompt is None:
                return json.dumps({"error": "prompt is required for add"})
            return self.add_event(
                name=action_name,
                schedule_type=schedule_type,
                prompt=prompt,
                time=time,
                every=every,
                unit=unit,
                at=at,
                enabled=enabled if enabled is not None else True,
            )
        if action == "update":
            if not action_name:
                return json.dumps({"error": "action_name is required for update"})
            return self.update_event(
                name=action_name,
                schedule_type=schedule_type,
                prompt=prompt,
                time=time,
                every=every,
                unit=unit,
                at=at,
                enabled=enabled,
            )
        if action == "delete":
            if not action_name:
                return json.dumps({"error": "action_name is required for delete"})
            return self.delete_event(name=action_name)
        return json.dumps(
            {"error": f"Unknown action '{action}'. Use list/add/update/delete."}
        )


class DateTimeTools:
    """Tool for getting the current date and time."""

    def get_current_datetime(self) -> str:
        """Return the current date and time in ISO 8601 format with timezone."""
        now = datetime.now(timezone.utc).astimezone()
        return json.dumps(
            {
                "iso": now.isoformat(),
                "utc": datetime.now(timezone.utc).isoformat(),
                "local": now.strftime("%Y-%m-%d %H:%M:%S %Z"),
                "date": now.strftime("%Y-%m-%d"),
                "time": now.strftime("%H:%M:%S"),
                "timezone": now.strftime("%Z"),
                "offset": now.strftime("%z"),
            }
        )


class SkillTools:
    """Tool for loading the full content of an agent skill on demand."""

    def __init__(
        self,
        config: AgentConfig,
        agent_provider: Callable[[], "Agent"] | None = None,
    ):
        self.config = config
        self._agent_provider = agent_provider

    def _skills_dir(self) -> Path:
        """Return the skills directory to use for get_skill.

        When the current thread has an active toolbox, its skills directory
        takes precedence so the agent can retrieve the skills shown in the
        system prompt.
        """
        agent = self._agent_provider() if self._agent_provider else None
        if agent is not None:
            thread_id = getattr(agent, "_current_thread_id", "")
            if thread_id:
                state = agent.thread_states.get(thread_id)
                if state is not None and state.active_toolbox_path is not None:
                    return state.active_toolbox_path / "skills"
        return self.config.skills_dir

    def get_skill(self, name: str) -> str:
        """Return the full markdown content of a skill by name.

        Use this when you need the detailed instructions for a skill listed in
        the Available Skills section. The name must match the file stem shown in
        the list (e.g. 'web-search').

        Args:
            name: The skill name, matching the file stem in the skills folder.
        """
        skill_path = self._skills_dir() / f"{name}.md"
        if not skill_path.exists():
            return json.dumps({"error": f"Skill '{name}' not found"})
        return json.dumps(
            {
                "name": name,
                "content": skill_path.read_text(encoding="utf-8"),
            }
        )

    def skill_crud(self, action: str, name: str, content: str | None = None) -> str:
        """Create, update, or delete a skill file in the agent's base skills folder.

        Use this to manage the agent's own skills. Creating or updating a skill
        requires a '# Description' section so it appears correctly in the
        Available Skills list. This tool always targets the agent's base skills
        folder, not the active toolbox.

        Args:
            action: One of "create", "update", "delete".
            name: The skill name, matching the file stem in the skills folder.
            content: Full markdown content for the skill. Required for create and update.
        """
        action = action.lower().strip()
        if action not in ("create", "update", "delete"):
            return json.dumps(
                {"error": f"Unknown action '{action}'. Use create/update/delete."}
            )

        if not _is_valid_skill_name(name):
            return json.dumps(
                {
                    "error": f"Invalid skill name '{name}'. Use only letters, numbers, hyphens, and underscores."
                }
            )

        skill_path = self.config.skills_dir / f"{name}.md"

        if action == "create":
            if skill_path.exists():
                return json.dumps(
                    {
                        "error": f"Skill '{name}' already exists. Use update to modify it."
                    }
                )
            if content is None:
                return json.dumps({"error": "content is required for create"})
            if not _has_description_section(content):
                return json.dumps(
                    {"error": "Skill must contain a '# Description' section"}
                )
            self.config.skills_dir.mkdir(parents=True, exist_ok=True)
            skill_path.write_text(content, encoding="utf-8")
            return json.dumps({"status": "created", "name": name})

        if action == "update":
            if not skill_path.exists():
                return json.dumps(
                    {"error": f"Skill '{name}' not found. Use create to add it."}
                )
            if content is None:
                return json.dumps({"error": "content is required for update"})
            if not _has_description_section(content):
                return json.dumps(
                    {"error": "Skill must contain a '# Description' section"}
                )
            _backup_skill(skill_path)
            skill_path.write_text(content, encoding="utf-8")
            return json.dumps({"status": "updated", "name": name})

        # action == "delete"
        if not skill_path.exists():
            return json.dumps({"error": f"Skill '{name}' not found"})
        backup_path = _backup_skill(skill_path)
        return json.dumps(
            {"status": "deleted", "name": name, "backup": str(backup_path)}
        )


class ThreadTools:
    """Tool for exposing the current conversation thread ID to the agent."""

    def __init__(self, agent_provider: Callable[[], "Agent"] | None):
        self._agent_provider = agent_provider

    def get_current_thread_id(self) -> str:
        """Return the current conversation thread_id.

        Use this when you need to know the current thread_id, for example to
        call send_discord with the correct channel ID.
        """
        agent = self._agent_provider() if self._agent_provider else None
        thread_id = getattr(agent, "_current_thread_id", None) if agent else None
        return json.dumps({"thread_id": thread_id or "unknown"})


class ToolboxTools:
    """Unified tool for listing and switching the agent's active toolbox."""

    def __init__(
        self,
        config: AgentConfig,
        agent_provider: Callable[[], "Agent"] | None,
    ):
        self.config = config
        self._agent_provider = agent_provider

    def toolbox(self, action: str, target: str = "") -> str:
        """List or switch toolboxes.

        Args:
            action: One of "list" or "switch".
            target: Toolbox name. Required for "switch". Optional for "list";
                if omitted, all available toolboxes are returned.
        """
        from agenthost.toolbox import (
            get_toolboxes_root,
            list_toolboxes,
            validate_toolbox_name,
        )

        action = action.lower().strip()

        if action == "switch":
            if not target:
                return json.dumps({"error": "target is required for action='switch'."})
            agent = self._agent_provider() if self._agent_provider else None
            if agent is None:
                return json.dumps({"error": "Agent not available"})
            return agent.switch_toolbox(target)

        if action == "list":
            if not target:
                return json.dumps(
                    {"toolboxes": list_toolboxes(self.config.toolboxes_dir)}
                )

            if not validate_toolbox_name(target):
                return json.dumps({"error": f"Invalid toolbox name '{target}'."})

            toolbox_path = get_toolboxes_root(self.config.toolboxes_dir) / target
            if not toolbox_path.exists():
                return json.dumps(
                    {
                        "error": f"Toolbox '{target}' not found.",
                        "available": list_toolboxes(self.config.toolboxes_dir),
                    }
                )

            tools = self.config._agent_tools(toolbox_path / "tools")
            skills = self.config._agent_skills(toolbox_path / "skills")
            return json.dumps(
                {
                    "toolbox": target,
                    "tools": [
                        {"name": name, "description": desc} for name, desc in tools
                    ],
                    "skills": [
                        {"name": name, "description": desc} for name, desc in skills
                    ],
                },
                indent=2,
            )

        return json.dumps(
            {"error": f"Unknown action '{action}'. Use 'list' or 'switch'."}
        )


class DiscordTools:
    """Tools for outbound Discord messages via the bridge's /send and /edit endpoints."""

    def __init__(
        self,
        config: AgentConfig,
        agent_provider: Callable[[], "Agent"] | None = None,
    ):
        self.config = config
        self._agent_provider = agent_provider

    async def send_discord(self, text: str, thread_id: str, file_path: str = "") -> str:
        """Send a Discord message, with an optional file attachment.

        The agent can call this from scheduled events or any other workflow to
        push a message to Discord without waiting for an incoming message.
        The sent message is also recorded in the thread's memory so the agent
        remembers it.

        Args:
            text: The text message to send (required).
            thread_id: The Discord channel ID (or DM channel ID) to send to.
            file_path: Optional path to a file attachment, relative to the
                agenthost home directory or absolute.
        """
        bridge_url = self._bridge_url()
        payload: dict[str, object] = {"text": text, "thread_id": thread_id}

        if file_path:
            resolved = self._resolve_agenthost_path(file_path)
            if isinstance(resolved, str):
                return json.dumps({"error": resolved})
            if not resolved.exists():
                return json.dumps({"error": f"File not found: {file_path}"})
            if not resolved.is_file():
                return json.dumps({"error": f"Path is not a file: {file_path}"})
            payload["file_path"] = str(resolved)

        try:
            response = httpx.post(
                bridge_url,
                json=payload,
                timeout=60.0 if file_path else 30.0,
            )
            response.raise_for_status()

            await self._record_in_memory(
                thread_id, text or (f"Sent file: {file_path}" if file_path else "")
            )

            result: dict[str, object] = {
                "status": "sent",
                "thread_id": thread_id,
                "bridge": bridge_url,
            }
            if file_path:
                result["file_path"] = str(resolved)
            return json.dumps(result)
        except Exception as exc:  # noqa: BLE001
            return json.dumps({"error": f"Failed to send Discord message: {exc}"})

    async def edit_discord_message(
        self, text: str, thread_id: str, message_id: str
    ) -> str:
        """Edit an existing Discord message.

        Args:
            text: The new text content for the message.
            thread_id: The Discord channel ID (or DM channel ID) containing the message.
            message_id: The Discord message ID to edit.
        """
        edit_url = self._edit_bridge_url()
        payload: dict[str, object] = {
            "text": text,
            "thread_id": thread_id,
            "message_id": message_id,
        }

        try:
            response = httpx.post(
                edit_url,
                json=payload,
                timeout=30.0,
            )
            response.raise_for_status()
            return json.dumps(
                {
                    "status": "edited",
                    "thread_id": thread_id,
                    "message_id": message_id,
                    "bridge": edit_url,
                }
            )
        except Exception as exc:  # noqa: BLE001
            return json.dumps({"error": f"Failed to edit Discord message: {exc}"})

    def _bridge_url(self) -> str:
        return (
            self.config.extra.get("discord_bridge_url")
            or os.environ.get("DISCORD_BRIDGE_URL")
            or "http://127.0.0.1:9002/send"
        )

    def _edit_bridge_url(self) -> str:
        """Return the edit endpoint URL by replacing '/send' with '/edit' in the bridge URL."""
        url = self._bridge_url()
        if url.endswith("/send"):
            return url[:-5] + "/edit"
        return url.rstrip("/") + "/edit"

    def _resolve_agenthost_path(self, path: str) -> Path | str:
        """Resolve a path relative to the agenthost home directory.

        Returns the resolved Path, or an error string if the path is invalid or
        escapes the agenthost home directory.
        """
        if not path:
            return "Path cannot be empty"

        home = get_agenthost_home()
        target = Path(path)
        if target.is_absolute():
            resolved = target.resolve()
        else:
            resolved = (home / target).resolve()

        try:
            resolved.relative_to(home)
        except ValueError:
            return f"Access denied: '{path}' resolves outside the agenthost home directory '{home}'."

        return resolved

    async def _record_in_memory(self, thread_id: str, message: str) -> None:
        """Record an outbound Discord interaction in the agent's memory."""
        agent = self._agent_provider() if self._agent_provider else None
        if agent is not None:
            try:
                await agent.memory.append_message(
                    thread_id,
                    {"role": "assistant", "content": message},
                )
            except Exception as exc:  # noqa: BLE001
                logger.warning("Failed to record Discord message in memory: %s", exc)


class PlanningTools:
    """Unified tool for creating, reading, and stepping through task plans.

    The agent drives execution by repeatedly calling ``plan(action="next")``.
    Each call executes one step via a focused ``agent.chat()`` call and returns
    the assistant's response.  The agent sees the output and calls ``next``
    again for the following step — like a cursor walking through the plan.

    Provides a single ``plan(action, ...)`` entry point so the LLM sees
    exactly one planning tool in the schema.
    """

    def __init__(
        self,
        config: AgentConfig,
        agent_provider: Callable[[], "Agent"] | None = None,
    ):
        self.config = config
        self._agent_provider = agent_provider
        self._store: Any = None

    def _get_store(self) -> Any:
        if self._store is None:
            from agenthost.planning import TaskStateStore

            agent = self._agent_provider() if self._agent_provider else None
            db_path = (
                agent.memory.db_path
                if agent is not None
                else self.config.memory_dir / "memory.db"
            )
            self._store = TaskStateStore(db_path)
        return self._store

    def _get_agent(self) -> "Agent | None":
        return self._agent_provider() if self._agent_provider else None

    # ------------------------------------------------------------------
    # Unified tool entry-point
    # ------------------------------------------------------------------

    async def plan(
        self,
        action: str,
        goal: str | None = None,
        steps: list[dict[str, Any]] | None = None,
        plan_id: str | None = None,
    ) -> str:
        """Create, read, and step through multi-step task execution plans.

        Call ``action="create"`` to decompose a complex request into steps.
        Call ``action="next"`` to execute the next pending step — the tool
        runs a focused ``agent.chat()`` call and returns the response.
        Call ``action="read"`` to inspect the plan's current state.
        Call ``action="skip"`` to skip a failed step.

        Args:
            action: One of "create", "next", "read", "skip".
            goal: High-level goal description. Required for "create".
            steps: List of step dicts. Required for "create".  Each dict has:
                step_number (int), description (str), depends_on (list[int],
                optional), assigned_toolbox (str, optional).
            plan_id: Plan identifier. Required for "next", "read", "skip".
                Optional for "read" (defaults to active thread plan).
        """
        action = action.lower().strip()

        if action == "create":
            return self._action_create(goal, steps)
        if action == "next":
            return await self._action_next(plan_id)
        if action == "read":
            return self._action_read(plan_id)
        if action == "skip":
            return self._action_skip(plan_id)
        if action == "delete":
            return self._action_delete(plan_id)
        if action == "reset":
            return self._action_reset(plan_id)
        if action == "list":
            return self._action_list()

        return json.dumps(
            {
                "error": (
                    f"Unknown action '{action}'. "
                    "Valid actions: create, next, read, skip, delete, reset, list."
                )
            }
        )

    # ------------------------------------------------------------------
    # Action implementations
    # ------------------------------------------------------------------

    def _action_create(
        self,
        goal: str | None,
        steps: list[dict[str, Any]] | None,
    ) -> str:
        if not goal:
            return json.dumps({"error": "goal is required for action='create'."})
        if not steps:
            return json.dumps({"error": "steps is required for action='create'."})

        from agenthost.planning import TaskPlan, TaskStep, _validate_dag

        err = _validate_dag(steps)
        if err is not None:
            return json.dumps({"error": err})

        step_number_to_id: dict[int, str] = {}
        task_steps: list[TaskStep] = []
        for s in steps:
            step_num = int(s["step_number"])
            deps = s.get("depends_on") or []
            dep_ids: list[str] = []
            missing: list[int] = []
            for dn in deps:
                sid = step_number_to_id.get(dn)
                if sid is not None:
                    dep_ids.append(sid)
                else:
                    missing.append(dn)
            if missing:
                return json.dumps(
                    {
                        "error": (
                            f"Step {step_num} depends on step(s) {missing} "
                            f"which have not been defined in the steps list."
                        )
                    }
                )
            step = TaskStep(
                step_number=step_num,
                description=s["description"],
                assigned_toolbox=s.get("assigned_toolbox"),
                depends_on=dep_ids,
            )
            step_number_to_id[step_num] = step.id
            task_steps.append(step)

        agent = self._get_agent()
        if agent is None:
            return json.dumps({"error": "Agent not available"})
        thread_id: str = getattr(agent, "_current_thread_id", "")

        plan = TaskPlan(thread_id=thread_id, goal=goal, steps=task_steps)
        try:
            self._get_store().create_plan(plan)
        except ValueError as exc:
            return json.dumps({"error": str(exc)})

        # Plan is created as draft. Agent presents it; user approves textually.
        step_lines = [
            f"{s.step_number}. {s.description}"
            + (f" (→ {s.assigned_toolbox})" if s.assigned_toolbox else "")
            for s in task_steps
        ]
        formatted = (
            f"📋 **Plan: {goal}**\n\n"
            + "\n".join(step_lines)
            + (
                "\n\nDependencies: "
                + ", ".join(
                    f"Step {s.step_number} depends on step(s) "
                    + ", ".join(
                        str(cs.step_number)
                        for cs in task_steps
                        if cs.id in s.depends_on
                    )
                    for s in task_steps
                    if s.depends_on
                )
                if any(s.depends_on for s in task_steps)
                else ""
            )
        )

        return json.dumps(
            {
                "status": "draft",
                "plan_id": plan.id,
                "step_count": len(task_steps),
                "formatted_plan": formatted,
            },
            indent=2,
        )

    async def _action_next(self, plan_id: str | None) -> str:
        """Execute the next pending step.  Returns the assistant's response.

        If the previous step failed, calling ``next`` again retries it.
        If all steps are completed, returns a completion message.
        """
        if not plan_id:
            return json.dumps({"error": "plan_id is required for action='next'."})

        agent = self._get_agent()
        if agent is None:
            return json.dumps({"error": "Agent not available"})

        store = self._get_store()
        plan = store.get_plan(plan_id)
        if plan is None:
            return json.dumps({"error": f"Plan '{plan_id}' not found."})

        # All done?
        all_done = all(s.status in ("completed", "skipped") for s in plan.steps)
        if all_done:
            return json.dumps(
                {
                    "status": "completed",
                    "plan_id": plan_id,
                    "message": "All steps are finished.",
                }
            )

        # Find the next ready/pending step in DAG order.
        completed_ids = {s.id for s in plan.steps if s.status == "completed"}
        next_step: Any = None
        for s in plan.steps:
            if s.status == "running":
                # Step was left mid-execution (unlikely with the new model).
                # Treat it as pending and re-run.
                next_step = s
                break
            if s.status in ("failed", "pending"):
                # Check if its dependencies are satisfied.
                if not s.depends_on or all(d in completed_ids for d in s.depends_on):
                    next_step = s
                    break
                # Otherwise show what's blocking it.
                missing = [d for d in s.depends_on if d not in completed_ids]
                missing_nums = [
                    str(cs.step_number) for cs in plan.steps if cs.id in missing
                ]
                return json.dumps(
                    {
                        "status": "blocked",
                        "step_number": s.step_number,
                        "step_description": s.description,
                        "waiting_on": missing_nums,
                        "hint": (
                            f"Step {s.step_number} cannot run yet. "
                            f"Steps {missing_nums} must complete first."
                        ),
                    }
                )

        if next_step is None:
            return json.dumps(
                {
                    "status": "error",
                    "message": "No runnable step found in the plan.",
                }
            )

        # Mark as running.
        store.update_step_status(next_step.id, "running")

        # Switch toolbox if needed.
        if next_step.assigned_toolbox:
            agent.switch_toolbox(next_step.assigned_toolbox)

        # Build the full context prompt and execute via agent.chat().
        prompt = self._build_step_prompt(plan, next_step)
        result_text, error = await self._call_agent(agent, plan, next_step, prompt)

        if error is not None:
            # Step failed — mark it and return the error.
            store.update_step_status(next_step.id, "failed", error_message=error)
            return json.dumps(
                {
                    "status": "failed",
                    "plan_id": plan_id,
                    "step_number": next_step.step_number,
                    "step_description": next_step.description,
                    "error": error,
                    "hint": (
                        "Call plan(action='next', plan_id=...) to retry this step, "
                        "or plan(action='skip', plan_id=...) to skip it."
                    ),
                }
            )

        # Step completed — store the result.
        store.update_step_status(next_step.id, "completed", result_summary=result_text)

        # Return the step result to the calling LLM.
        done = all(s.status in ("completed", "skipped") for s in plan.steps)
        return json.dumps(
            {
                "status": "step_completed",
                "plan_id": plan_id,
                "step_number": next_step.step_number,
                "step_description": next_step.description,
                "result": result_text,
                "plan_finished": done,
                "hint": (
                    "Call plan(action='next', plan_id=...) for the next step."
                    if not done
                    else "Plan is complete."
                ),
            }
        )

    def _action_read(self, plan_id: str | None) -> str:
        agent = self._get_agent()
        store = self._get_store()

        if plan_id is not None:
            plan = store.get_plan(plan_id)
            if plan is None:
                return json.dumps({"error": f"Plan '{plan_id}' not found."})
        else:
            if agent is None:
                return json.dumps(
                    {"error": "Agent not available and no plan_id provided."}
                )
            thread_id: str = getattr(agent, "_current_thread_id", "")
            plan = store.get_active_plan_for_thread(thread_id)
            if plan is None:
                return json.dumps({"active_plan": None})

        steps_out = [
            {
                "step_number": s.step_number,
                "description": s.description,
                "status": s.status,
                "result_preview": (
                    (s.result_summary or "")[:300] if s.result_summary else None
                ),
                "error_message": s.error_message,
            }
            for s in plan.steps
        ]
        return json.dumps(
            {
                "plan_id": plan.id,
                "goal": plan.goal,
                "status": plan.status,
                "steps": steps_out,
            },
            indent=2,
        )

    def _action_skip(self, plan_id: str | None) -> str:
        """Skip the currently failed step.  The next ``next`` call picks up
        the following step."""
        if not plan_id:
            return json.dumps({"error": "plan_id is required for action='skip'."})

        store = self._get_store()
        plan = store.get_plan(plan_id)
        if plan is None:
            return json.dumps({"error": f"Plan '{plan_id}' not found."})

        # Find the first failed step and skip it.
        skipped = None
        for s in plan.steps:
            if s.status == "failed":
                store.update_step_status(s.id, "skipped")
                skipped = s
                break

        if skipped is None:
            return json.dumps(
                {"error": "No failed step to skip. Call plan(action='next')."}
            )

        # Unblock dependents that can now proceed.
        plan = store.get_plan(plan_id)
        if plan is not None:
            for s in plan.steps:
                if s.status == "blocked":
                    completed_or_skipped = {
                        cs.id
                        for cs in plan.steps
                        if cs.status in ("completed", "skipped")
                    }
                    if all(d in completed_or_skipped for d in s.depends_on):
                        store.update_step_status(s.id, "pending")

        return json.dumps(
            {
                "status": "skipped",
                "plan_id": plan_id,
                "step_number": skipped.step_number,
                "hint": "Call plan(action='next', plan_id=...) to continue.",
            }
        )

    def _action_delete(self, plan_id: str | None) -> str:
        """Delete a plan and all its steps permanently."""
        if not plan_id:
            return json.dumps({"error": "plan_id is required for action='delete'."})

        store = self._get_store()
        plan = store.get_plan(plan_id)
        if plan is None:
            return json.dumps({"error": f"Plan '{plan_id}' not found."})

        store.delete_plan(plan_id)
        return json.dumps(
            {
                "status": "deleted",
                "plan_id": plan_id,
                "message": f"Plan '{plan_id}' permanently deleted.",
            }
        )

    def _action_reset(self, plan_id: str | None) -> str:
        """Reset all steps in a plan back to pending so the user can re-run it.
        Clears all step results, error messages, and completion timestamps."""
        if not plan_id:
            return json.dumps({"error": "plan_id is required for action='reset'."})

        store = self._get_store()
        plan = store.get_plan(plan_id)
        if plan is None:
            return json.dumps({"error": f"Plan '{plan_id}' not found."})

        for s in plan.steps:
            store.update_step_status(
                s.id, "pending", result_summary=None, error_message=None
            )

        return json.dumps(
            {
                "status": "reset",
                "plan_id": plan_id,
                "step_count": plan.step_count,
                "hint": "Call plan(action='next', plan_id=...) to re-run the plan.",
            }
        )

    def _action_list(self) -> str:
        """List all plans for the current thread."""
        agent = self._get_agent()
        if agent is None:
            return json.dumps({"error": "Agent not available"})
        thread_id: str = getattr(agent, "_current_thread_id", "")

        store = self._get_store()
        import sqlite3

        plans_data: list[dict[str, Any]] = []
        with store._connect() as conn:
            rows = conn.execute(
                """SELECT p.id, p.goal, p.status, p.created_at, p.completed_at,
                   COUNT(s.id) as step_count
                   FROM task_plans p
                   LEFT JOIN task_steps s ON s.plan_id = p.id
                   WHERE p.thread_id = ?
                   GROUP BY p.id
                   ORDER BY p.created_at DESC""",
                (thread_id,),
            ).fetchall()
            for row in rows:
                plans_data.append(
                    {
                        "plan_id": row["id"],
                        "goal": row["goal"],
                        "status": row["status"],
                        "step_count": row["step_count"],
                        "created_at": row["created_at"],
                        "completed_at": row["completed_at"],
                    }
                )

        return json.dumps({"thread_id": thread_id, "plans": plans_data}, indent=2)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _build_step_prompt(plan: Any, step: Any) -> str:
        """Construct a focused prompt for *step*, including prior step outputs."""
        total = len(plan.steps)
        parts: list[str] = []

        parts.append(
            f"[Plan Progress: Step {step.step_number} of {total}"
            f" — {step.description}]"
        )
        parts.append("")
        parts.append(f"Overall goal: {plan.goal}")
        parts.append("")

        # Inject outputs from completed steps.
        completed = [
            s for s in plan.steps if s.status == "completed" and s.id in step.depends_on
        ]
        if completed:
            parts.append("Completed work so far:")
            for cs in completed:
                preview = (cs.result_summary or "(no output)")[:500]
                parts.append(f"  Step {cs.step_number} ({cs.description}): {preview}")
            parts.append("")

        parts.append(f"Your task now: {step.description}")
        parts.append("")
        parts.append(
            "Focus only on this step.  Produce a complete, synthesized output."
        )

        return "\n".join(parts)

    async def _call_agent(
        self,
        agent: "Agent",
        plan: Any,
        step: Any,
        prompt: str,
    ) -> tuple[str | None, str | None]:
        """Call ``agent.chat()`` on the plan's thread and capture the response.

        Returns ``(result_text, error_string)``.  Exactly one will be non-None.
        """
        plan_thread = plan.id  # plan_id doubles as the dedicated thread_id

        try:
            result_parts: list[str] = []
            async for chunk in agent.chat(plan_thread, prompt):
                try:
                    event = json.loads(chunk)
                except json.JSONDecodeError:
                    continue
                if event.get("type") == "content":
                    result_parts.append(event.get("data", ""))
            result_text = "".join(result_parts).strip()
            if not result_text:
                return None, "The agent returned an empty response."
            return result_text, None
        except Exception as exc:  # noqa: BLE001
            return None, f"{type(exc).__name__}: {exc}"


def build_builtin_tools_prompt(config: AgentConfig) -> str:
    """Return a markdown description of enabled built-in tools for the system prompt."""
    enabled = config.extra.get("builtin_tools") or []
    if isinstance(enabled, str):
        enabled = [enabled]

    descriptions: list[str] = [
        "- `get_current_datetime()`: Return the current date and time. "
        "The current date and time are also included in the system prompt on every request, "
        "so you usually do not need this tool. Use it only when you need to confirm the exact "
        "current time in a structured format or when the system prompt timestamp seems "
        "inconsistent with the user's question.",
        "- `get_skill(name)`: Return the full markdown content of a skill by name. "
        "Use this when a task matches one of the skills listed in the Available Skills section. "
        "The name must match the file stem shown in the list.",
    ]
    if "toolbox" in enabled:
        descriptions.append(
            "- `toolbox(action, target='')`: Manage the agent's active toolbox. "
            "action is one of 'list' or 'switch'. "
            "For 'list', omit target to see all toolboxes, or pass a target to see its tools and skills. "
            "For 'switch', target is required and activates the named toolbox, replacing the current agent tools and skills. "
            "Built-in tools are preserved. Pass an empty target to revert to the agent's default tools and skills."
        )
    if "events" in enabled or "event_tool" in enabled:
        descriptions.append(
            "- `event_tool(action, action_name, schedule_type, prompt, time, every, unit, at, enabled)`: "
            "Manage scheduled events in this agent's events.yaml. "
            "Actions: list, add, update, delete. "
            "schedule_type and time can be lists to create multiple triggers (e.g. "
            "['monday', 'wednesday'] with ['09:00', '23:00']). "
            "Use this when the user asks to schedule, list, modify, or remove recurring tasks."
        )
    if "discord" in enabled or "send_discord" in enabled:
        descriptions.append(
            "- `send_discord(text, thread_id, file_path='')`: Push a message to Discord via the bridge, "
            "with an optional file attachment. Use this when the user asks you to send something to Discord, "
            "or when a scheduled event prompt instructs you to deliver results to Discord. "
            "The thread_id must be the current conversation thread_id, which is shown at the top of the system prompt. "
            "If you are unsure of the current thread_id, call `get_current_thread_id()` first. "
            "file_path, when provided, should be a relative path from the agenthost home directory (e.g. 'output/markdown_writer/file.md')."
        )
    if "discord" in enabled or "edit_discord_message" in enabled:
        descriptions.append(
            "- `edit_discord_message(text, thread_id, message_id)`: Edit an existing Discord message. "
            "Use this when the user asks you to update a previously sent Discord message. "
            "thread_id is the Discord channel ID, and message_id is the Discord message ID to edit."
        )
    if "thread" in enabled or "get_current_thread_id" in enabled:
        descriptions.append(
            "- `get_current_thread_id()`: Return the current conversation thread_id. "
            "Use this when a tool like send_discord needs a thread_id and you are not certain of it."
        )
    if "filesystem" in enabled or "read_file" in enabled:
        descriptions.append(
            "- `read_file(path, max_lines, offset)`: Read a file inside the project directory. "
            "Text files are read directly; `.docx`, `.pdf`, `.xlsx`, and `.csv` are converted to text automatically."
        )
    if "filesystem" in enabled or "list_uploads" in enabled:
        descriptions.append(
            "- `list_uploads()`: List files in the shared uploads directory."
        )
    if "skill_crud" in enabled:
        descriptions.append(
            "- `skill_crud(action, name, content='')`: Create, update, or delete skills in this agent's skills folder. "
            "Actions: create, update, delete. "
            "content is required for create and update and must include a '# Description' section. "
            "Use this when the user asks to add, change, or remove one of the agent's skills."
        )
    if "planning" in enabled or "plan" in enabled:
        descriptions.append(
            "- `plan(action, goal, steps, plan_id)`: "
            "Manage multi-step task execution plans. "
            "Actions: create (build from goal+steps), next (execute next step), "
            "read (inspect plan), skip (skip failed step), "
            "delete (permanently remove plan), reset (clear results for re-run), "
            "list (list all plans in current thread). "
            "After creating a plan, call plan(action='next', plan_id=...) "
            "repeatedly until the plan returns plan_finished=true or 'status':'completed'. "
            "Call plan(action='list') to see all plans with their IDs."
        )

    if not descriptions:
        return ""
    return "\n\n# Built-in Tools\n\n" + "\n".join(descriptions)


def make_builtin_tools(
    config: AgentConfig,
    scheduler: AsyncIOScheduler | None,
    agent_provider: Callable[[], "Agent"] | None = None,
) -> dict[str, Callable[..., Any]]:
    """Return a dict of built-in tool functions for the given agent.

    Built-in tools are opt-in. Enable them in agent.yaml under ``extra`` using
    group names or individual tool names:

        extra:
          builtin_tools: [events, discord]

        extra:
          builtin_tools: [add_event, send_discord, get_current_thread_id]
    """
    enabled = config.extra.get("builtin_tools") or []
    if isinstance(enabled, str):
        enabled = [enabled]

    # Build the full tool registry first.
    event_tools = EventTools(config, scheduler, agent_provider)
    datetime_tools = DateTimeTools()
    skill_tools = SkillTools(config, agent_provider)
    discord_tools = DiscordTools(config, agent_provider)
    thread_tools = ThreadTools(agent_provider)
    filesystem_tools = FileSystemTools()
    toolbox_tools = ToolboxTools(config, agent_provider)
    planning_tools = PlanningTools(config, agent_provider)
    available = {
        "event_tool": event_tools.event_tool,
        "get_current_datetime": datetime_tools.get_current_datetime,
        "get_skill": skill_tools.get_skill,
        "skill_crud": skill_tools.skill_crud,
        "send_discord": discord_tools.send_discord,
        "edit_discord_message": discord_tools.edit_discord_message,
        "get_current_thread_id": thread_tools.get_current_thread_id,
        "read_file": filesystem_tools.read_file,
        "list_uploads": filesystem_tools.list_uploads,
        "toolbox": toolbox_tools.toolbox,
        "plan": planning_tools.plan,
    }

    # get_current_datetime, get_skill, and plan are always available.
    # Planning-mode threads only see these three tools (no toolbox tools).
    # Normal-mode threads see plan + all toolbox tools.
    # Toolbox management is opt-in via extra.builtin_tools.
    functions: dict[str, Callable[..., Any]] = {
        "get_current_datetime": available["get_current_datetime"],
        "get_skill": available["get_skill"],
        "plan": available["plan"],
    }

    for item in enabled:
        if item == "events":
            functions["event_tool"] = available["event_tool"]
        elif item == "datetime":
            functions["get_current_datetime"] = available["get_current_datetime"]
        elif item == "discord":
            functions["send_discord"] = available["send_discord"]
            functions["edit_discord_message"] = available["edit_discord_message"]
        elif item == "thread":
            functions["get_current_thread_id"] = available["get_current_thread_id"]
        elif item == "filesystem":
            functions["read_file"] = available["read_file"]
            functions["list_uploads"] = available["list_uploads"]
        elif item == "toolbox":
            functions["toolbox"] = available["toolbox"]
        elif item == "planning":
            functions["plan"] = available["plan"]
        elif item in available:
            functions[item] = available[item]
        else:
            logger.warning(
                "Unknown builtin tool '%s' requested for agent '%s'", item, config.name
            )

    return functions
