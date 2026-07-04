"""Agent package configuration.

Philosophy: everything needed to run the agent lives in the agent folder.
The only exception is secrets (e.g. OPENAI_API_KEY), which are read from the environment.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from agenthost.skills import load_skills


DEFAULT_CONFIG = {
    "model": "gpt-4o-mini",
    "host": "127.0.0.1",
    "temperature": 0.7,
    "max_memory_turns": 50,
    "base_url": None,
    "max_tokens": None,
    "thinking": None,
}


@dataclass
class AgentConfig:
    path: Path
    name: str
    model: str = DEFAULT_CONFIG["model"]
    host: str = DEFAULT_CONFIG["host"]
    port: int | None = None
    temperature: float = DEFAULT_CONFIG["temperature"]
    max_memory_turns: int = DEFAULT_CONFIG["max_memory_turns"]
    base_url: str | None = DEFAULT_CONFIG["base_url"]
    max_tokens: int | None = DEFAULT_CONFIG["max_tokens"]
    thinking: str | None = DEFAULT_CONFIG["thinking"]
    extra: dict[str, Any] = field(default_factory=dict)

    @property
    def whoami_path(self) -> Path:
        return self.path / "WHOAMI.md"

    @property
    def tools_dir(self) -> Path:
        return self.path / "tools"

    @property
    def skills_dir(self) -> Path:
        return self.path / "skills"

    @property
    def memory_dir(self) -> Path:
        return self.path / "memory"

    @property
    def skills(self) -> dict[str, str]:
        return load_skills(self.skills_dir)

    @property
    def system_prompt(self) -> str:
        parts: list[str] = []
        if self.whoami_path.exists():
            parts.append(self.whoami_path.read_text(encoding="utf-8"))
        else:
            parts.append("You are a helpful assistant.")

        skills = self.skills
        if skills:
            parts.append("\n\n# Skills\n")
            for name, content in skills.items():
                parts.append(f"\n## {name}\n\n{content}")

        parts.append(
            "\n\n# Tool Usage Instructions\n\n"
            "Carefully read each tool's description to understand whether it operates on a "
            "single item or supports batch operations. Unless a tool is explicitly described "
            "as accepting multiple items in one call, make exactly one tool call per item. "
            "Provide all required arguments as a single valid JSON object. Do not concatenate "
            "multiple JSON objects or multiple tool calls into one argument string."
        )

        return "\n".join(parts)

    @classmethod
    def from_path(cls, path: str | Path) -> "AgentConfig":
        p = Path(path).expanduser().resolve()
        if not p.is_dir():
            raise ValueError(f"Agent path is not a directory: {p}")
        if not (p / "WHOAMI.md").exists():
            raise ValueError(f"Agent package missing WHOAMI.md: {p}")

        config = dict(DEFAULT_CONFIG)
        config_path = p / "agent.yaml"
        if config_path.exists():
            loaded = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
            config.update(loaded)

        name = config.pop("name", p.name)
        # The 'extra' block in agent.yaml is merged into the extra dict.
        explicit_extra = config.pop("extra", None) or {}
        extra = {k: v for k, v in config.items() if k not in {
            "model", "host", "port", "temperature", "max_memory_turns", "base_url",
            "max_tokens", "thinking",
        }}
        extra.update(explicit_extra)

        # Port is now optional in config; keep backward compat with explicit values.
        port_value = config.get("port")
        port = int(port_value) if port_value is not None else None

        max_tokens = config.get("max_tokens")
        thinking = config.get("thinking")

        return cls(
            path=p,
            name=name,
            model=config.get("model"),
            host=config.get("host"),
            port=port,
            temperature=float(config.get("temperature")),
            max_memory_turns=int(config.get("max_memory_turns")),
            base_url=config.get("base_url"),
            max_tokens=int(max_tokens) if max_tokens is not None else None,
            thinking=str(thinking) if thinking is not None else None,
            extra=extra,
        )
