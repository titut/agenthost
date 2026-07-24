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
        prompt: str,
        time: list[str] | None = None,
        every: int | None = None,
        unit: str | None = None,
        at: str | None = None,
        enabled: bool = True,
    ) -> str:
        """Add a new scheduled event to this agent's events.yaml.

        Args:
            name: Unique name for the event.
            schedule_type: List of schedule types. Each one of: daily, weekdays,
                weekends, monday-sunday, interval, once. For multiple days, pass
                e.g. ['monday', 'wednesday'].
            prompt: Message/prompt sent to the agent when the event fires.
            time: List of times in HH:MM or HH:MM:SS format. Required for day-based
                schedules. For multiple times, pass e.g. ['09:00', '23:00'].
            every: Required for interval schedules.
            unit: Required for interval schedules: seconds, minutes, hours, days.
            at: Required for once schedules, ISO 8601 datetime.
            enabled: Whether the event is active.
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
                name=name, schedule=schedule, prompt=prompt, enabled=enabled
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
        prompt: str | None = None,
        time: list[str] | None = None,
        every: int | None = None,
        unit: str | None = None,
        at: str | None = None,
        enabled: bool | None = None,
    ) -> str:
        """Update an existing scheduled event. Only provided fields are changed.

        Args:
            name: Name of the event to update.
            schedule_type: List of schedule types. Each one of: daily, weekdays,
                weekends, monday-sunday, interval, once. For multiple days, pass
                e.g. ['monday', 'wednesday'].
            time: List of times in HH:MM or HH:MM:SS format. For multiple times,
                pass e.g. ['09:00', '23:00'].
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
        prompt: str | None = None,
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
            prompt: Message/prompt sent to the agent when the event fires.
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

        When an active toolbox is loaded, its skills directory takes precedence so
        the agent can retrieve the skills currently shown in the system prompt.
        """
        agent = self._agent_provider() if self._agent_provider else None
        if (
            agent is not None
            and getattr(agent, "active_toolbox_path", None) is not None
        ):
            return agent.active_toolbox_path / "skills"
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
                return json.dumps({"toolboxes": list_toolboxes()})

            if not validate_toolbox_name(target):
                return json.dumps({"error": f"Invalid toolbox name '{target}'."})

            toolbox_path = get_toolboxes_root() / target
            if not toolbox_path.exists():
                return json.dumps(
                    {
                        "error": f"Toolbox '{target}' not found.",
                        "available": list_toolboxes(),
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
    }

    # get_current_datetime and get_skill are enabled by default for every
    # agent. Toolbox management is opt-in via the "toolbox" group or the
    # individual tool name in extra.builtin_tools.
    functions: dict[str, Callable[..., Any]] = {
        "get_current_datetime": available["get_current_datetime"],
        "get_skill": available["get_skill"],
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
        elif item in available:
            functions[item] = available[item]
        else:
            logger.warning(
                "Unknown builtin tool '%s' requested for agent '%s'", item, config.name
            )

    return functions
