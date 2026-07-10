"""Built-in tools available to every agent.

These tools are injected into every agent's tool runner. They give agents the
ability to manage their own configuration, such as scheduled events.
"""

from __future__ import annotations

import json
import os
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
from agenthost.logger import setup_logging

if False:
    # Imported only for type checking; avoid circular import at runtime.
    from agenthost.agent import Agent


logger = setup_logging("agenthost.builtin_tools")


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
        schedule_type: str,
        prompt: str,
        time: str | None = None,
        every: int | None = None,
        unit: str | None = None,
        at: str | None = None,
        enabled: bool = True,
    ) -> str:
        """Add a new scheduled event to this agent's events.yaml.

        Args:
            name: Unique name for the event.
            schedule_type: One of daily, weekdays, weekends, monday-sunday, interval, once.
            prompt: Message/prompt sent to the agent when the event fires.
            time: Required for day-based schedules, e.g. "09:00".
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
        schedule_type: str | None = None,
        prompt: str | None = None,
        time: str | None = None,
        every: int | None = None,
        unit: str | None = None,
        at: str | None = None,
        enabled: bool | None = None,
    ) -> str:
        """Update an existing scheduled event. Only provided fields are changed."""
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
        schedule_type: str | None = None,
        prompt: str | None = None,
        time: str | None = None,
        every: int | None = None,
        unit: str | None = None,
        at: str | None = None,
        enabled: bool | None = None,
    ) -> str:
        """Unified CRUD tool for this agent's scheduled events.

        Args:
            action: One of "list", "add", "update", "delete".
            action_name: Name of the event. Required for add/update/delete.
            schedule_type: One of daily, weekdays, weekends, monday-sunday, interval, once.
            prompt: Message/prompt sent to the agent when the event fires.
            time: Required for day-based schedules, e.g. "09:00".
            every: Required for interval schedules.
            unit: Required for interval schedules: seconds, minutes, hours, days.
            at: Required for once schedules, ISO 8601 datetime.
            enabled: Whether the event is active.
        """
        action = action.lower().strip()
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
        return json.dumps({"error": f"Unknown action '{action}'. Use list/add/update/delete."})


class DateTimeTools:
    """Tool for getting the current date and time."""

    def get_current_datetime(self) -> str:
        """Return the current date and time in ISO 8601 format with timezone."""
        now = datetime.now(timezone.utc).astimezone()
        return json.dumps({
            "iso": now.isoformat(),
            "utc": datetime.now(timezone.utc).isoformat(),
            "local": now.strftime("%Y-%m-%d %H:%M:%S %Z"),
            "date": now.strftime("%Y-%m-%d"),
            "time": now.strftime("%H:%M:%S"),
            "timezone": now.strftime("%Z"),
            "offset": now.strftime("%z"),
        })


class ThreadTools:
    """Tool for exposing the current conversation thread ID to the agent."""

    def __init__(self, agent_provider: Callable[[], "Agent"] | None):
        self._agent_provider = agent_provider

    def get_current_thread_id(self) -> str:
        """Return the current conversation thread_id.

        Use this when you need to know the current thread_id, for example to
        call send_discord_message with the correct channel ID.
        """
        agent = self._agent_provider() if self._agent_provider else None
        thread_id = getattr(agent, "_current_thread_id", None) if agent else None
        return json.dumps({"thread_id": thread_id or "unknown"})


class DiscordTools:
    """Tool for sending outbound Discord messages via the bridge's /send endpoint."""

    def __init__(
        self,
        config: AgentConfig,
        agent_provider: Callable[[], "Agent"] | None = None,
    ):
        self.config = config
        self._agent_provider = agent_provider

    def send_discord_message(self, message: str, thread_id: str) -> str:
        """Send a Discord message to the channel identified by thread_id.

        The agent can call this from scheduled events or any other workflow to
        push a message to Discord without waiting for an incoming message.
        The sent message is also recorded in the thread's memory so the agent
        remembers it.

        Args:
            message: The text to send.
            thread_id: The Discord channel ID (or DM channel ID) to send to.
        """
        bridge_url = (
            self.config.extra.get("discord_bridge_url")
            or os.environ.get("DISCORD_BRIDGE_URL")
            or "http://127.0.0.1:9002/send"
        )
        try:
            response = httpx.post(
                bridge_url,
                json={"text": message, "thread_id": thread_id},
                timeout=30.0,
            )
            response.raise_for_status()

            # Record the outbound message in memory so the agent remembers sending it.
            agent = self._agent_provider() if self._agent_provider else None
            if agent is not None:
                try:
                    agent.memory.append_message(
                        thread_id,
                        {"role": "assistant", "content": message},
                    )
                except Exception as exc:  # noqa: BLE001
                    logger.warning("Failed to record Discord message in memory: %s", exc)

            return json.dumps({"status": "sent", "thread_id": thread_id, "bridge": bridge_url})
        except Exception as exc:  # noqa: BLE001
            return json.dumps({"error": f"Failed to send Discord message: {exc}"})


def build_builtin_tools_prompt(config: AgentConfig) -> str:
    """Return a markdown description of enabled built-in tools for the system prompt."""
    enabled = config.extra.get("builtin_tools") or []
    if isinstance(enabled, str):
        enabled = [enabled]

    descriptions: list[str] = [
        "- `get_current_datetime()`: Return the current date and time. "
        "Use this whenever the user asks about the current date, time, day, year, or any "
        "time-sensitive question (e.g. involving 'now', 'today', 'latest', 'recent', or a deadline)."
    ]
    if "events" in enabled or "event_tool" in enabled:
        descriptions.append(
            "- `event_tool(action, action_name, schedule_type, prompt, time, every, unit, at, enabled)`: "
            "Manage scheduled events in this agent's events.yaml. "
            "Actions: list, add, update, delete. "
            "Use this when the user asks to schedule, list, modify, or remove recurring tasks."
        )
    if "discord" in enabled or "send_discord_message" in enabled:
        descriptions.append(
            "- `send_discord_message(message, thread_id)`: Push a message to Discord via the bridge. "
            "Use this when the user asks you to send something to Discord, "
            "or when a scheduled event prompt instructs you to deliver results to Discord. "
            "The thread_id must be the current conversation thread_id, which is shown at the top of the system prompt. "
            "If you are unsure of the current thread_id, call `get_current_thread_id()` first."
        )
    if "thread" in enabled or "get_current_thread_id" in enabled:
        descriptions.append(
            "- `get_current_thread_id()`: Return the current conversation thread_id. "
            "Use this when a tool like send_discord_message needs a thread_id and you are not certain of it."
        )
    if "filesystem" in enabled or "read_file" in enabled or "list_uploads" in enabled:
        descriptions.append(
            "- `read_file(path, max_lines, offset)`: Read a text file inside the project directory. "
            "Use this to read uploaded documents that have been extracted to `.txt` sidecars."
        )
        descriptions.append(
            "- `list_uploads()`: List files in the shared uploads directory, including extracted text sidecars."
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
          builtin_tools: [add_event, send_discord_message, get_current_thread_id]
    """
    enabled = config.extra.get("builtin_tools") or []
    if isinstance(enabled, str):
        enabled = [enabled]

    # Build the full tool registry first.
    event_tools = EventTools(config, scheduler, agent_provider)
    datetime_tools = DateTimeTools()
    discord_tools = DiscordTools(config, agent_provider)
    thread_tools = ThreadTools(agent_provider)
    filesystem_tools = FileSystemTools()
    available = {
        "event_tool": event_tools.event_tool,
        "get_current_datetime": datetime_tools.get_current_datetime,
        "send_discord_message": discord_tools.send_discord_message,
        "get_current_thread_id": thread_tools.get_current_thread_id,
        "read_file": filesystem_tools.read_file,
        "list_uploads": filesystem_tools.list_uploads,
    }

    # get_current_datetime is enabled by default for every agent.
    functions: dict[str, Callable[..., Any]] = {
        "get_current_datetime": available["get_current_datetime"],
    }

    for item in enabled:
        if item == "events":
            functions["event_tool"] = available["event_tool"]
        elif item == "datetime":
            functions["get_current_datetime"] = available["get_current_datetime"]
        elif item == "discord":
            functions["send_discord_message"] = available["send_discord_message"]
        elif item == "thread":
            functions["get_current_thread_id"] = available["get_current_thread_id"]
        elif item == "filesystem":
            functions["read_file"] = available["read_file"]
            functions["list_uploads"] = available["list_uploads"]
        elif item in available:
            functions[item] = available[item]
        else:
            logger.warning("Unknown builtin tool '%s' requested for agent '%s'", item, config.name)

    return functions
