"""Tool discovery and execution."""
from __future__ import annotations

import asyncio
import importlib.util
import inspect
import json
import sys
from abc import ABC, abstractmethod
from pathlib import Path
from types import ModuleType
from typing import Any, Callable, get_type_hints


class ToolRunner(ABC):
    """Abstract interface for executing tools."""

    @abstractmethod
    async def run(self, tool_name: str, arguments: dict[str, Any]) -> str:
        ...


class InProcessToolRunner(ToolRunner):
    """Runs tools as Python functions in the same process."""

    def __init__(self, functions: dict[str, Callable[..., Any]]):
        self.functions = functions

    async def run(self, tool_name: str, arguments: dict[str, Any]) -> str:
        fn = self.functions.get(tool_name)
        if fn is None:
            return json.dumps({"error": f"Tool not found: {tool_name}"})

        try:
            coerced = _coerce_arguments(fn, arguments)
            if inspect.iscoroutinefunction(fn):
                result = await fn(**coerced)
            else:
                # Run sync functions in threadpool to avoid blocking event loop.
                loop = asyncio.get_event_loop()
                result = await loop.run_in_executor(None, lambda: fn(**coerced))

            if isinstance(result, str):
                return result
            return json.dumps(result, default=str)
        except Exception as exc:  # noqa: BLE001
            return json.dumps({"error": f"{type(exc).__name__}: {exc}"})


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

    return schemas, InProcessToolRunner(functions)
