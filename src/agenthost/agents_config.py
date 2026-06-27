"""Agent alias configuration stored in the agenthost home directory.

agents.yaml maps short aliases to agent folder paths, so agents can live
anywhere on disk while still being referenced by name.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from agenthost.home import get_agents_yaml_path


class AgentsConfig:
    """Load and manage the agents.yaml alias map."""

    def __init__(self, path: Path | None = None) -> None:
        self.path = path or get_agents_yaml_path()
        self.aliases: dict[str, str] = {}
        self._load()

    def _load(self) -> None:
        """Load aliases from disk, or start empty if the file does not exist."""
        if not self.path.exists():
            return
        try:
            data = yaml.safe_load(self.path.read_text(encoding="utf-8")) or {}
        except (yaml.YAMLError, OSError):
            data = {}
        aliases = data.get("agents") if isinstance(data, dict) else None
        if isinstance(aliases, dict):
            self.aliases = {str(k): str(v) for k, v in aliases.items()}

    def save(self) -> None:
        """Persist aliases to disk."""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload: dict[str, Any] = {"agents": dict(sorted(self.aliases.items()))}
        self.path.write_text(yaml.safe_dump(payload, sort_keys=True), encoding="utf-8")

    def resolve(self, alias: str) -> Path | None:
        """Resolve a registered alias to an agent folder path.

        Returns None if the alias is not registered. Explicit folder paths are
        handled by the caller; this method only looks at agents.yaml.
        """
        if alias in self.aliases:
            return Path(self.aliases[alias]).expanduser().resolve()
        return None

    def add(self, alias: str, path: str | Path) -> None:
        """Register or update an alias."""
        self.aliases[alias] = str(Path(path).expanduser().resolve())
        self.save()

    def remove(self, alias: str) -> bool:
        """Remove an alias. Returns True if it existed."""
        if alias in self.aliases:
            del self.aliases[alias]
            self.save()
            return True
        return False

    def list(self) -> dict[str, str]:
        """Return a read-only view of alias → path mappings."""
        return dict(self.aliases)
