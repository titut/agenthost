"""Scheduled events for agenthost agents.

Each agent may define an events.yaml file describing messages to send to the
agent on a schedule. Schedules use a structured, human-readable YAML format
instead of cron expressions.
"""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal
from zoneinfo import ZoneInfo

import yaml
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.date import DateTrigger
from apscheduler.triggers.interval import IntervalTrigger
from pydantic import BaseModel, field_validator, model_validator

from agenthost.logger import setup_logging

if TYPE_CHECKING:
    from agenthost.agent import Agent


logger = setup_logging("agenthost.events")


DAY_MAP = {
    "monday": "mon",
    "tuesday": "tue",
    "wednesday": "wed",
    "thursday": "thu",
    "friday": "fri",
    "saturday": "sat",
    "sunday": "sun",
}

VALID_TYPES = set(DAY_MAP) | {"daily", "weekdays", "weekends", "interval", "once"}


def _parse_time(time_str: str) -> tuple[int, int, int]:
    """Parse 'HH:MM' or 'HH:MM:SS' into hour, minute, second."""
    parts = time_str.strip().split(":")
    if len(parts) == 2:
        hour, minute = parts
        second = "0"
    elif len(parts) == 3:
        hour, minute, second = parts
    else:
        raise ValueError(f"Invalid time format: {time_str!r}. Use HH:MM or HH:MM:SS.")
    return int(hour), int(minute), int(second)


def _build_cron_trigger(
    day_type: str, hour: int, minute: int, second: int, tz: ZoneInfo | None
) -> CronTrigger:
    """Build an APScheduler CronTrigger for a day type and time."""
    kwargs = {"hour": hour, "minute": minute, "second": second}
    if tz is not None:
        kwargs["timezone"] = tz

    if day_type == "daily":
        return CronTrigger(**kwargs)
    if day_type == "weekdays":
        return CronTrigger(day_of_week="mon-fri", **kwargs)
    if day_type == "weekends":
        return CronTrigger(day_of_week="sat,sun", **kwargs)
    cron_day = DAY_MAP[day_type]
    return CronTrigger(day_of_week=cron_day, **kwargs)


def _to_timezone(timezone_name: str | None) -> ZoneInfo | None:
    """Return a ZoneInfo for a timezone name, or None."""
    if timezone_name is None:
        return None
    return ZoneInfo(timezone_name)


def _localize_datetime(dt: datetime, tz: ZoneInfo | None) -> datetime:
    """Attach or convert a datetime to the given timezone."""
    if tz is None:
        return dt
    if dt.tzinfo is None:
        return dt.replace(tzinfo=tz)
    return dt.astimezone(tz)


class EventSchedule(BaseModel):
    """Structured schedule definition for a scheduled event."""

    type: str | list[str]
    time: str | list[str] | None = None
    every: int | None = None
    unit: Literal["seconds", "minutes", "hours", "days"] | None = None
    at: datetime | None = None

    @field_validator("type", mode="before")
    @classmethod
    def _normalize_type(cls, value: Any) -> list[str]:
        if isinstance(value, str):
            return [value]
        return list(value)

    @field_validator("time", mode="before")
    @classmethod
    def _normalize_time(cls, value: Any) -> list[str] | None:
        if value is None:
            return None
        if isinstance(value, str):
            return [value]
        return list(value)

    @model_validator(mode="after")
    def _validate_schedule(self) -> "EventSchedule":
        for t in self.type:
            if t not in VALID_TYPES:
                raise ValueError(
                    f"Invalid schedule type {t!r}. "
                    f"Valid types: {', '.join(sorted(VALID_TYPES))}"
                )

        special = {"interval", "once"} & set(self.type)
        if special:
            if len(self.type) != 1:
                raise ValueError(
                    f"Schedule type {special.pop()!r} cannot be combined with other types"
                )

        if "interval" in self.type:
            if self.every is None or self.unit is None:
                raise ValueError("Interval schedule requires 'every' and 'unit'")
            if self.time is not None:
                raise ValueError("Interval schedule does not use 'time'")
            if self.at is not None:
                raise ValueError("Interval schedule does not use 'at'")

        if "once" in self.type:
            if self.at is None:
                raise ValueError("Once schedule requires 'at'")
            if self.time is not None:
                raise ValueError("Once schedule does not use 'time'")
            if self.every is not None or self.unit is not None:
                raise ValueError("Once schedule does not use 'every' or 'unit'")

        # Day-based schedules need at least one time.
        if "interval" not in self.type and "once" not in self.type:
            if not self.time:
                raise ValueError("Day-based schedule requires at least one 'time'")

        return self

    def to_triggers(
        self, file_timezone: str | None = None
    ) -> list[CronTrigger | IntervalTrigger | DateTrigger]:
        """Convert this schedule into one or more APScheduler triggers."""
        triggers: list[CronTrigger | IntervalTrigger | DateTrigger] = []
        tz = _to_timezone(file_timezone)

        for t in self.type:
            if t == "interval":
                kwargs = {self.unit: self.every}
                if tz is not None:
                    kwargs["timezone"] = tz
                triggers.append(IntervalTrigger(**kwargs))
                continue

            if t == "once":
                dt = _localize_datetime(self.at, tz)
                triggers.append(DateTrigger(run_date=dt, timezone=tz))
                continue

            times = self.time or ["00:00"]
            for time_str in times:
                hour, minute, second = _parse_time(time_str)
                triggers.append(_build_cron_trigger(t, hour, minute, second, tz))

        return triggers


class ScheduledEvent(BaseModel):
    """A single scheduled event defined in an agent's events.yaml."""

    name: str
    schedule: EventSchedule
    enabled: bool = True
    prompt: str
    # Optional thread_id inherited from the conversation where the event was
    # created. Used by scheduled runs so the agent keeps the right context
    # (e.g. the Discord channel to send messages back to).
    thread_id: str | None = None


class EventsConfig(BaseModel):
    """Root model for an agent's events.yaml file."""

    timezone: str | None = None
    events: list[ScheduledEvent] = []

    @field_validator("timezone")
    @classmethod
    def _validate_timezone(cls, value: str | None) -> str | None:
        if value is None:
            return None
        try:
            ZoneInfo(value)
        except Exception as exc:
            raise ValueError(
                f"Invalid timezone: {value!r}. "
                f"Use IANA timezone names like 'America/New_York' or 'UTC'."
            ) from exc
        return value


def load_events(agent_path: str | Path) -> tuple[list[ScheduledEvent], str | None]:
    """Load scheduled events and timezone from an agent's events.yaml."""
    events_file = Path(agent_path) / "events.yaml"
    if not events_file.exists():
        return [], None

    data = yaml.safe_load(events_file.read_text(encoding="utf-8")) or {}
    config = EventsConfig.model_validate(data)
    return config.events, config.timezone


async def run_scheduled_event(agent: "Agent", event: ScheduledEvent) -> None:
    """Execute a scheduled event by sending its prompt to the agent."""
    logger.info(
        "Running scheduled event '%s' for agent '%s'",
        event.name,
        agent.config.name,
    )
    # Allow events to run in the same thread as an ongoing chat (e.g. WhatsApp)
    # by configuring event_thread_id in agent.yaml extra fields.
    thread_id = event.thread_id or agent.config.extra.get("event_thread_id") or f"scheduled:{event.name}"
    response_parts: list[str] = []

    try:
        async for chunk in agent.chat(thread_id, event.prompt):
            data = json.loads(chunk)
            if data.get("type") == "content":
                response_parts.append(data.get("data", ""))
        logger.info(
            "Scheduled event '%s' completed (%d response chars)",
            event.name,
            len("".join(response_parts)),
        )
    except Exception:
        logger.exception("Scheduled event '%s' failed", event.name)
