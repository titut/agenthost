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


class WhatsAppTools:
    """Tool for sending outbound WhatsApp messages via the bridge's /send endpoint."""

    def __init__(self, config: AgentConfig):
        self.config = config

    def send_whatsapp_message(self, text: str) -> str:
        """Send a WhatsApp message to the configured target JID.

        The agent can call this from scheduled events or any other workflow to
        push a message to WhatsApp without waiting for an incoming message.
        """
        bridge_url = (
            self.config.extra.get("whatsapp_bridge_url")
            or os.environ.get("WHATSAPP_BRIDGE_URL")
            or "http://127.0.0.1:9001/send"
        )
        try:
            response = httpx.post(bridge_url, json={"text": text}, timeout=30.0)
            response.raise_for_status()
            return json.dumps({"status": "sent", "bridge": bridge_url})
        except Exception as exc:  # noqa: BLE001
            return json.dumps({"error": f"Failed to send WhatsApp message: {exc}"})


def build_builtin_tools_prompt(config: AgentConfig) -> str:
    """Return a markdown description of enabled built-in tools for the system prompt."""
    enabled = config.extra.get("builtin_tools") or []
    if isinstance(enabled, str):
        enabled = [enabled]

    descriptions: list[str] = []
    if "events" in enabled or "event_tool" in enabled:
        descriptions.append(
            "- `event_tool(action, action_name, schedule_type, prompt, time, every, unit, at, enabled)`: "
            "Manage scheduled events in this agent's events.yaml. "
            "Actions: list, add, update, delete. "
            "Use this when the user asks to schedule, list, modify, or remove recurring tasks."
        )
    if "datetime" in enabled or "get_current_datetime" in enabled:
        descriptions.append(
            "- `get_current_datetime()`: Return the current date and time. "
            "Use this whenever the user asks about the current date, time, day, or year."
        )
    if "whatsapp" in enabled or "send_whatsapp_message" in enabled:
        descriptions.append(
            "- `send_whatsapp_message(text)`: Push a message to WhatsApp via the bridge. "
            "Use this when the user asks you to send something to WhatsApp, "
            "or when a scheduled event prompt instructs you to deliver results to WhatsApp."
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
          builtin_tools: [events, whatsapp]

        extra:
          builtin_tools: [add_event, send_whatsapp_message]
    """
    enabled = config.extra.get("builtin_tools") or []
    if isinstance(enabled, str):
        enabled = [enabled]

    functions: dict[str, Callable[..., Any]] = {}

    # Build the full tool registry first.
    event_tools = EventTools(config, scheduler, agent_provider)
    datetime_tools = DateTimeTools()
    whatsapp_tools = WhatsAppTools(config)
    available = {
        "event_tool": event_tools.event_tool,
        "get_current_datetime": datetime_tools.get_current_datetime,
        "send_whatsapp_message": whatsapp_tools.send_whatsapp_message,
    }

    for item in enabled:
        if item == "events":
            functions["event_tool"] = available["event_tool"]
        elif item == "datetime":
            functions["get_current_datetime"] = available["get_current_datetime"]
        elif item == "whatsapp":
            functions["send_whatsapp_message"] = available["send_whatsapp_message"]
        elif item in available:
            functions[item] = available[item]
        else:
            logger.warning("Unknown builtin tool '%s' requested for agent '%s'", item, config.name)

    return functions
