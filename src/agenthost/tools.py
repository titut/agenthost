"""Tool discovery and execution."""
from __future__ import annotations

import asyncio
import contextvars
import importlib.util
import inspect
import json
import sys
from abc import ABC, abstractmethod
from pathlib import Path
from types import ModuleType
from typing import TYPE_CHECKING, Any, Callable, get_type_hints

if TYPE_CHECKING:
    from agenthost.config import AgentConfig


# Exposed so agent tools can access the current conversation thread.
current_thread_id: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "agenthost_current_thread_id", default=None
)

# Exposed so agent tools can read agent configuration (e.g. for summarization).
current_agent_config: contextvars.ContextVar["AgentConfig | None"] = contextvars.ContextVar(
    "agenthost_current_agent_config", default=None
)


class ToolRunner(ABC):
    """Abstract interface for executing tools."""

    @abstractmethod
    async def run(
        self, tool_name: str, arguments: dict[str, Any], thread_id: str | None = None
    ) -> str:
        ...


class InProcessToolRunner(ToolRunner):
    """Runs tools as Python functions in the same process."""

    def __init__(
        self,
        functions: dict[str, Callable[..., Any]],
        config: "AgentConfig | None" = None,
    ):
        self.functions = functions
        self.config = config

    async def run(
        self, tool_name: str, arguments: dict[str, Any], thread_id: str | None = None
    ) -> str:
        tokens: list[contextvars.Token[Any]] = []
        if thread_id is not None:
            tokens.append(current_thread_id.set(thread_id))
        if self.config is not None:
            tokens.append(current_agent_config.set(self.config))
        try:
            fn = self.functions.get(tool_name)
            if fn is None:
                return json.dumps({"error": f"Tool not found: {tool_name}"})

            missing = _missing_required_args(fn, arguments)
            if missing:
                return json.dumps(
                    {
                        "error": f"Missing required arguments for tool '{tool_name}': {', '.join(missing)}. "
                        f"Please provide all required arguments and try again."
                    }
                )

            try:
                coerced = _coerce_arguments(fn, arguments)
                if inspect.iscoroutinefunction(fn):
                    result = await fn(**coerced)
                else:
                    # Run sync functions in threadpool to avoid blocking event loop.
                    # asyncio.to_thread copies the current context so tools can
                    # access contextvars such as current_thread_id.
                    result = await asyncio.to_thread(fn, **coerced)

                if isinstance(result, str):
                    return result
                return json.dumps(result, default=str)
            except Exception as exc:  # noqa: BLE001
                return json.dumps({"error": f"{type(exc).__name__}: {exc}"})
        finally:
            for token in reversed(tokens):
                current_agent_config.reset(token) if token.var is current_agent_config else current_thread_id.reset(token)


def _resolve_type_hints(fn: Callable[..., Any]) -> dict[str, Any]:
    """Resolve string annotations (from __future__ import annotations) to real types."""
    try:
        return get_type_hints(fn)
    except Exception:  # noqa: BLE001
        return {}


def _python_type_to_json_type(py_type: Any) -> str:
    origin = getattr(py_type, "__origin__", None)
    if origin is list or py_type is list:
        return "array"
    if origin is dict or py_type is dict:
        return "object"
    if py_type is bool:
        return "boolean"
    if py_type is int:
        return "integer"
    if py_type is float:
        return "number"
    if py_type is str:
        return "string"
    return "string"


def _build_tool_schema(fn: Callable[..., Any]) -> dict[str, Any]:
    sig = inspect.signature(fn)
    doc = inspect.getdoc(fn) or f"Call {fn.__name__}."
    params: dict[str, Any] = {}
    required: list[str] = []
    hints = _resolve_type_hints(fn)

    for name, param in sig.parameters.items():
        if param.default is inspect.Parameter.empty:
            required.append(name)
        annotation = hints.get(name, str)
        params[name] = {
            "type": _python_type_to_json_type(annotation),
            "description": f"Parameter '{name}'.",
        }

    return {
        "type": "function",
        "function": {
            "name": fn.__name__,
            "description": doc,
            "parameters": {
                "type": "object",
                "properties": params,
                "required": required,
            },
        },
    }


def _missing_required_args(fn: Callable[..., Any], arguments: dict[str, Any]) -> list[str]:
    """Return the names of required parameters that are missing from arguments."""
    sig = inspect.signature(fn)
    missing: list[str] = []
    for name, param in sig.parameters.items():
        if param.default is inspect.Parameter.empty and name not in arguments:
            missing.append(name)
    return missing


def _coerce_arguments(fn: Callable[..., Any], arguments: dict[str, Any]) -> dict[str, Any]:
    """Coerce string arguments from the LLM into the function's annotated types."""
    hints = _resolve_type_hints(fn)
    coerced: dict[str, Any] = {}

    for name, value in arguments.items():
        target = hints.get(name)
        if target is None or not isinstance(value, str):
            coerced[name] = value
            continue

        origin = getattr(target, "__origin__", None)

        if target is int:
            coerced[name] = int(value)
        elif target is float:
            coerced[name] = float(value)
        elif target is bool:
            coerced[name] = value.lower() in ("true", "1", "yes", "on")
        elif target is list or origin is list:
            try:
                coerced[name] = json.loads(value)
            except json.JSONDecodeError:
                coerced[name] = [value]
        elif target is dict or origin is dict:
            try:
                coerced[name] = json.loads(value)
            except json.JSONDecodeError:
                coerced[name] = {"value": value}
        else:
            coerced[name] = value

    return coerced


def _load_module(path: Path) -> ModuleType:
    module_name = f"agenthost.tools.{path.stem}"
    spec = importlib.util.spec_from_file_location(module_name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Could not load tool module: {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


def discover_tools(
    tools_dir: Path,
    builtin_functions: dict[str, Callable[..., Any]] | None = None,
    config: "AgentConfig | None" = None,
) -> tuple[list[dict[str, Any]], ToolRunner]:
    """Discover tool functions from Python files in tools_dir.

    Only includes functions **defined** in each module (checked via
    ``fn.__module__``), not imported references.  This prevents the same
    shared helper (e.g. ``get_gmail_service`` imported from ``_gmail_base``
    into every tool module) from being registered as a duplicate tool.

    ``builtin_functions`` are merged in after discovery. Agent-defined tools
    with the same name take precedence over built-ins.
    """
    functions: dict[str, Callable[..., Any]] = {}
    schemas: list[dict[str, Any]] = []

    if tools_dir.exists():
        for file in sorted(tools_dir.glob("*.py")):
            if file.name.startswith("_"):
                continue
            module = _load_module(file)
            module_name = module.__name__
            for name, obj in inspect.getmembers(module, inspect.isfunction):
                if name.startswith("_"):
                    continue
                # Only accept functions defined in this module, not imported ones.
                if getattr(obj, "__module__", None) != module_name:
                    continue
                functions[name] = obj
                schemas.append(_build_tool_schema(obj))

    if builtin_functions:
        for name, fn in builtin_functions.items():
            if name not in functions:
                functions[name] = fn
                schemas.append(_build_tool_schema(fn))

    return schemas, InProcessToolRunner(functions, config=config)
