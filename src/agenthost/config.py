"""Agent package configuration.

Philosophy: everything needed to run the agent lives in the agent folder.
The only exception is secrets (e.g. OPENAI_API_KEY), which are read from the environment.
"""

from __future__ import annotations

import ast
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from agenthost.agents_config import AgentsConfig
from agenthost.home import get_agenthost_home
from agenthost.skills import load_skills


def _deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    """Recursively merge override into base. Nested dicts are merged, not replaced."""
    result = dict(base)
    for key, value in override.items():
        if key in result and isinstance(result[key], dict) and isinstance(value, dict):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = value
    return result


def _find_default_agent_config(agent_path: Path) -> Path | None:
    """Locate .agenthost/default_agent.yaml by walking up from the agent path."""
    current = agent_path.resolve()
    while current != current.parent:
        candidate = current / ".agenthost" / "default_agent.yaml"
        if candidate.exists():
            return candidate
        current = current.parent
    return None


DEFAULT_CONFIG = {
    "model": "moonshotai/Kimi-K2.5",
    "host": "127.0.0.1",
    "temperature": 0.7,
    "base_url": "https://api.deepinfra.com/v1",
    "max_tokens": 12000,
    "thinking": "low",
    "orchestrator": False,
    "frequency_penalty": 0.7,
    "presence_penalty": 0.7,
    "embedding": {
        "model": "BAAI/bge-m3",
        "base_url": "https://api.deepinfra.com/v1",
    },
    "memory": {
        "mode": "rag",
        "recent_messages": 8,
        "chunk_size": 512,
        "budget_tokens": 6000,
        "max_chunks": 12,
        "max_pool_chunks": 500,
        "similarity_weight": 0.6,
        "recency_weight": 0.3,
        "role_weight": 0.1,
        "mmr_lambda": 0.7,
    },
}


def _estimate_tokens(messages: list[dict[str, Any]], model: str) -> int:
    """Return a rough token estimate for the messages payload.

    Uses tiktoken if available, otherwise falls back to a characters-per-token
    heuristic. This is intentionally approximate — it is only for logging and
    debugging context-length issues.
    """
    text = json.dumps(messages, default=str)

    try:
        import tiktoken

        try:
            encoding = tiktoken.encoding_for_model(model)
        except KeyError:
            # Unknown model name; use the cl100k_base encoding as a reasonable
            # fallback for modern OpenAI-compatible models.
            encoding = tiktoken.get_encoding("cl100k_base")
        return len(encoding.encode(text))
    except Exception:  # noqa: BLE001
        # tiktoken not installed or unusable; fall back to a rough heuristic.
        # English averages ~4 characters per token; add a small overhead factor.
        return int(len(text) / 4) + len(messages) * 2


@dataclass
class EmbeddingConfig:
    """Embedding provider used for memory RAG."""

    model: str = "BAAI/bge-m3"
    base_url: str | None = "https://api.deepinfra.com/v1"


@dataclass
class MemoryConfig:
    """Hybrid memory configuration: recent messages + RAG chunks."""

    mode: str = "rag"
    recent_messages: int = 8
    chunk_size: int = 512
    budget_tokens: int = 6000
    max_chunks: int = 12
    max_pool_chunks: int = 5000
    similarity_weight: float = 0.6
    recency_weight: float = 0.3
    role_weight: float = 0.1
    mmr_lambda: float = 0.7


@dataclass
class AgentConfig:
    path: Path
    name: str
    model: str = DEFAULT_CONFIG["model"]
    host: str = DEFAULT_CONFIG["host"]
    port: int | None = None
    temperature: float = DEFAULT_CONFIG["temperature"]
    base_url: str | None = DEFAULT_CONFIG["base_url"]
    max_tokens: int | None = DEFAULT_CONFIG["max_tokens"]
    thinking: str | None = DEFAULT_CONFIG["thinking"]
    frequency_penalty: float | None = DEFAULT_CONFIG["frequency_penalty"]
    presence_penalty: float | None = DEFAULT_CONFIG["presence_penalty"]
    orchestrator: bool = DEFAULT_CONFIG["orchestrator"]
    toolboxes_dir: Path | None = None
    embedding: EmbeddingConfig = field(default_factory=EmbeddingConfig)
    memory: MemoryConfig = field(default_factory=MemoryConfig)
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
        return get_agenthost_home() / "memory" / self.name

    @property
    def skills(self) -> dict[str, str]:
        return load_skills(self.skills_dir)

    @property
    def critical_instructions_path(self) -> Path:
        return self.path / "CRITICAL.md"

    @property
    def critical_instructions(self) -> str:
        """Return the contents of CRITICAL.md, or empty string if absent."""
        if self.critical_instructions_path.exists():
            return self.critical_instructions_path.read_text(encoding="utf-8").strip()
        return ""

    @property
    def system_prompt(self) -> str:
        """Build the base system prompt using the agent's own skills folder."""
        return self.build_system_prompt(self.skills_dir)

    def build_system_prompt(self, skills_dir: Path | None = None) -> str:
        """Build the system prompt, optionally using a different skills directory.

        ``skills_dir`` is used by the toolbox feature so that the active
        toolbox's skills can be shown in the Available Skills section instead of
        the agent's base skills.
        """
        parts: list[str] = []
        if self.whoami_path.exists():
            parts.append(self.whoami_path.read_text(encoding="utf-8"))
        else:
            parts.append("You are a helpful assistant.")

        skills = self._agent_skills(skills_dir or self.skills_dir)
        if skills:
            skill_lines = [
                f"- `{name}`: {description}" if description else f"- `{name}`"
                for name, description in skills
            ]
            parts.append(
                "\n\n# Available Skills\n\n"
                + "\n".join(skill_lines)
                + "\n\nTo load the full content of a skill, call `get_skill(name)`."
            )

        parts.append(
            "\n\n# Tool Usage Instructions\n\n"
            "Carefully read each tool's description to understand whether it operates on a "
            "single item or supports batch operations. Unless a tool is explicitly described "
            "as accepting multiple items in one call, make exactly one tool call per item. "
            "Provide all required arguments as a single valid JSON object. Do not concatenate "
            "multiple JSON objects or multiple tool calls into one argument string.\n\n"
            "Whenever a user question is time-sensitive (for example, it refers to 'now', "
            "'today', 'current', 'latest', 'recent', a specific date, or a deadline), call "
            "the `get_current_datetime` tool to obtain the current date and time before "
            "answering. Do not guess the current date or time.\n\n"
            "Do not repeat previous paragraphs, section headers, tables, or bullet lists. "
            "If you have already stated a fact, metric, or recommendation, do not restate it. "
            "Move forward to the next point instead of summarizing what you just wrote.\n\n"
            "When writing a report or multi-section answer, each section may appear exactly once. "
            "Do not write a section as bullets and then rewrite the same section as a table. "
            "Do not add a recap, summary, or 'in conclusion' section after the final section. "
            "Stop writing after the last section is complete."
        )

        if self.orchestrator:
            roster = self._build_agent_roster()
            if roster:
                parts.append(roster)

        return "\n".join(parts)

    def _agent_description(self, agent_path: Path) -> str:
        """Return a one-line description for an agent folder.

        Reads the `# Description` section from WHOAMI.md. The description should
        be a single concise sentence (150 characters max).
        """
        whoami = agent_path / "WHOAMI.md"
        if not whoami.exists():
            return "No description available."

        text = whoami.read_text(encoding="utf-8")
        in_description = False
        lines: list[str] = []
        for line in text.splitlines():
            stripped = line.strip()
            if stripped.lower() in ("# description", "## description"):
                in_description = True
                continue
            if in_description:
                if stripped.startswith("#"):
                    break
                if stripped:
                    lines.append(stripped)

        description = " ".join(lines).strip()
        if description:
            sentence = description.split(". ")[0]
            if len(sentence) > 150:
                sentence = sentence[:147] + "..."
            return sentence

        return "No description available."

    def _skill_description(self, skill_path: Path) -> str:
        """Return the first sentence of the `# Description` section of a skill file."""
        if not skill_path.exists():
            return ""
        text = skill_path.read_text(encoding="utf-8")
        in_description = False
        lines: list[str] = []
        for line in text.splitlines():
            stripped = line.strip()
            if stripped.lower() in ("# description", "## description"):
                in_description = True
                continue
            if in_description:
                if stripped.startswith("#"):
                    break
                if stripped:
                    lines.append(stripped)
        description = " ".join(lines).strip()
        if description:
            sentence = description.split(". ")[0]
            if len(sentence) > 100:
                sentence = sentence[:97] + "..."
            return sentence
        return ""

    def _agent_skills(self, skills_dir: Path) -> list[tuple[str, str]]:
        """Return a list of (skill_name, description) for an agent's skills."""
        skills: list[tuple[str, str]] = []
        if not skills_dir.is_dir():
            return skills
        for skill_path in sorted(skills_dir.glob("*.md")):
            description = self._skill_description(skill_path)
            skills.append((skill_path.stem, description))
        return skills

    def _agent_tools(self, tools_dir: Path) -> list[tuple[str, str]]:
        """Return top-level tool function names and first-line docstrings.

        Uses AST so tool modules are not imported and no side effects occur.
        Methods inside classes are ignored.
        """
        tools: list[tuple[str, str]] = []
        if not tools_dir.is_dir():
            return tools
        for file in sorted(tools_dir.glob("*.py")):
            if file.name.startswith("_"):
                continue
            try:
                source = file.read_text(encoding="utf-8")
                tree = ast.parse(source)
            except Exception:  # noqa: BLE001
                continue
            for node in tree.body:
                if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    continue
                name = node.name
                if name.startswith("_"):
                    continue
                doc = ast.get_docstring(node) or ""
                first_line = doc.split("\n")[0].strip()
                if len(first_line) > 100:
                    first_line = first_line[:97] + "..."
                tools.append((name, first_line))
        return tools

    def _build_agent_roster(self) -> str:
        """Build a roster of available agents for orchestrator agents.

        Uses the agenthost agent list (agents.yaml aliases) rather than scanning
        the parent directory, so agents registered anywhere on disk are included.
        """
        agents_config = AgentsConfig()
        aliases = agents_config.list()
        if not aliases:
            return ""

        entries: list[str] = []
        for alias, agent_path_str in sorted(aliases.items()):
            agent_path = Path(agent_path_str).expanduser().resolve()
            if not agent_path.is_dir():
                continue
            if agent_path == self.path:
                continue
            if not (agent_path / "WHOAMI.md").exists():
                continue

            description = self._agent_description(agent_path)
            tools = self._agent_tools(agent_path / "tools")[:10]
            skills = self._agent_skills(agent_path / "skills")

            entry_lines: list[str] = [f"- `{alias}`: {description}"]

            yaml_path = agent_path / "agent.yaml"
            if yaml_path.exists():
                try:
                    cfg = yaml.safe_load(yaml_path.read_text(encoding="utf-8")) or {}
                    model = cfg.get("model")
                    if model:
                        entry_lines.append(f"  - Model: `{model}`")
                except Exception:  # noqa: BLE001
                    pass

            if tools:
                tool_parts = [
                    f"`{name}`" + (f" — {desc}" if desc else "") for name, desc in tools
                ]
                entry_lines.append("  - Tools: " + ", ".join(tool_parts))

            if skills:
                skill_parts = [
                    f"`{name}`" + (f" — {desc}" if desc else "")
                    for name, desc in skills
                ]
                entry_lines.append("  - Skills: " + ", ".join(skill_parts))

            entries.append("\n".join(entry_lines))

        if not entries:
            return ""

        return (
            "\n\n# Available Agents\n\n"
            "You can delegate work to the following agents. "
            "Use `list_agents()` for their current status and full capabilities.\n\n"
            + "\n\n".join(entries)
        )

    @classmethod
    def from_path(cls, path: str | Path) -> "AgentConfig":
        p = Path(path).expanduser().resolve()
        if not p.is_dir():
            raise ValueError(f"Agent path is not a directory: {p}")
        if not (p / "WHOAMI.md").exists():
            raise ValueError(f"Agent package missing WHOAMI.md: {p}")

        config = dict(DEFAULT_CONFIG)

        # Load project-wide defaults from .agenthost/default_agent.yaml if found.
        default_config_path = _find_default_agent_config(p)
        if default_config_path is not None:
            loaded_default = (
                yaml.safe_load(default_config_path.read_text(encoding="utf-8")) or {}
            )
            config = _deep_merge(config, loaded_default)

        # Load agent-specific overrides.
        config_path = p / "agent.yaml"
        if config_path.exists():
            loaded = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
            config = _deep_merge(config, loaded)

        name = config.pop("name", p.name)

        # Optional custom toolboxes directory. Relative paths are resolved against
        # the agent package directory so agent.yaml stays portable.
        toolboxes_dir_raw = config.pop("toolboxes_dir", None)
        toolboxes_dir = None
        if toolboxes_dir_raw:
            toolboxes_dir_path = Path(toolboxes_dir_raw).expanduser()
            if not toolboxes_dir_path.is_absolute():
                toolboxes_dir_path = p / toolboxes_dir_path
            toolboxes_dir = toolboxes_dir_path.resolve()

        # Parse nested embedding/memory config blocks. Embedding base_url defaults
        # to the agent's base_url so the same provider can be used out of the box.
        embedding_cfg = config.pop("embedding", None) or {}
        embedding = EmbeddingConfig(
            model=embedding_cfg.get("model", EmbeddingConfig().model),
            base_url=embedding_cfg.get("base_url", config.get("base_url")),
        )

        memory_cfg = config.pop("memory", None) or {}
        memory = MemoryConfig(
            mode=memory_cfg.get("mode", MemoryConfig().mode),
            recent_messages=int(
                memory_cfg.get("recent_messages", MemoryConfig().recent_messages)
            ),
            chunk_size=int(memory_cfg.get("chunk_size", MemoryConfig().chunk_size)),
            budget_tokens=int(
                memory_cfg.get("budget_tokens", MemoryConfig().budget_tokens)
            ),
            max_chunks=int(memory_cfg.get("max_chunks", MemoryConfig().max_chunks)),
            max_pool_chunks=int(
                memory_cfg.get("max_pool_chunks", MemoryConfig().max_pool_chunks)
            ),
            similarity_weight=float(
                memory_cfg.get("similarity_weight", MemoryConfig().similarity_weight)
            ),
            recency_weight=float(
                memory_cfg.get("recency_weight", MemoryConfig().recency_weight)
            ),
            role_weight=float(
                memory_cfg.get("role_weight", MemoryConfig().role_weight)
            ),
            mmr_lambda=float(memory_cfg.get("mmr_lambda", MemoryConfig().mmr_lambda)),
        )

        # The 'extra' block in agent.yaml is merged into the extra dict.
        explicit_extra = config.pop("extra", None) or {}
        extra = {
            k: v
            for k, v in config.items()
            if k
            not in {
                "model",
                "host",
                "port",
                "temperature",
                "base_url",
                "max_tokens",
                "thinking",
                "frequency_penalty",
                "presence_penalty",
                "orchestrator",
                "toolboxes_dir",
            }
        }
        extra.update(explicit_extra)

        # Port is now optional in config; keep backward compat with explicit values.
        port_value = config.get("port")
        port = int(port_value) if port_value is not None else None

        max_tokens = config.get("max_tokens")
        thinking = config.get("thinking")
        frequency_penalty = config.get("frequency_penalty")
        presence_penalty = config.get("presence_penalty")
        orchestrator = bool(config.get("orchestrator", False))

        return cls(
            path=p,
            name=name,
            model=config.get("model"),
            host=config.get("host"),
            port=port,
            temperature=float(config.get("temperature")),
            base_url=config.get("base_url"),
            max_tokens=int(max_tokens) if max_tokens is not None else None,
            thinking=str(thinking) if thinking is not None else None,
            frequency_penalty=(
                float(frequency_penalty) if frequency_penalty is not None else None
            ),
            presence_penalty=(
                float(presence_penalty) if presence_penalty is not None else None
            ),
            orchestrator=orchestrator,
            toolboxes_dir=toolboxes_dir,
            embedding=embedding,
            memory=memory,
            extra=extra,
        )
