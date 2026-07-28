"""Tool testing: run a tool in the evolve venv before switching toolboxes."""

from __future__ import annotations

import json
import re
from pathlib import Path

# ---------------------------------------------------------------------------
# Sandbox root and validation (inlined — each tool file is standalone)
# ---------------------------------------------------------------------------
_EVOLVE_ROOT = Path("/home/koroko/Workspace/agenthost/toolboxes-evolve")
_VALID_NAME = re.compile(r"[a-zA-Z0-9][a-zA-Z0-9_-]*$")


def _ok(name: str) -> bool:
    return bool(name) and len(name) <= 100 and bool(_VALID_NAME.fullmatch(name))


def _require(toolbox: str) -> Path:
    """Validate toolbox name and return its directory."""
    if not _ok(toolbox):
        raise ValueError(
            f"Invalid toolbox name '{toolbox}'. "
            f"Use only letters, numbers, hyphens, and underscores (max 100 chars)."
        )
    d = _EVOLVE_ROOT / toolbox
    if not d.is_dir():
        raise ValueError(
            f"Toolbox '{toolbox}' does not exist. Use create_toolbox() first."
        )
    return d


# ===================================================================
# Tool
# ===================================================================


async def test_tool(toolbox: str, name: str, arguments: str = "{}") -> str:
    """Test-run a tool inside its toolbox using the evolve venv.

    Runs the tool in a subprocess using ``_evolve_venv/bin/python`` so
    that any packages installed via ``install_requirements()`` are
    available.  The tool's output (or error) is captured and returned.

    Use this to verify a tool works correctly **before** switching to its
    toolbox.  Call ``install_requirements(toolbox)`` first if the tool
    needs third-party packages.

    Args:
        toolbox: Name of the toolbox containing the tool.
        name: Tool name (file stem and function name).
        arguments: JSON object string with the tool's arguments.
            Defaults to ``"{}"`` for tools that take no required args.
    """
    try:
        toolbox_dir = _require(toolbox)
    except ValueError as exc:
        return json.dumps({"error": str(exc)})

    if not _ok(name):
        return json.dumps({"error": f"Invalid tool name '{name}'."})

    tool_path = toolbox_dir / "tools" / f"{name}.py"
    if not tool_path.exists():
        return json.dumps({"error": f"Tool '{name}' not found in toolbox '{toolbox}'."})

    # Validate arguments are valid JSON.
    try:
        args = json.loads(arguments)
        if not isinstance(args, dict):
            return json.dumps({"error": "arguments must be a JSON object (dict)."})
    except json.JSONDecodeError as exc:
        return json.dumps({"error": f"Invalid JSON arguments: {exc}"})

    venv_python = _EVOLVE_ROOT / "_evolve_venv" / "bin" / "python"
    if not venv_python.exists():
        return json.dumps(
            {
                "error": f"Evolve venv python not found at {venv_python}. "
                f"Run: python -m venv {_EVOLVE_ROOT / '_evolve_venv'}"
            }
        )

    # Build a small test harness that imports the tool module, calls the
    # function, and prints the result as a JSON envelope to stdout.
    harness = (
        f"import json, sys, importlib.util, asyncio, inspect\n"
        f"sys.path.insert(0, {json.dumps(str(toolbox_dir.resolve()))})\n"
        f"spec = importlib.util.spec_from_file_location("
        f"{json.dumps(name)}, {json.dumps(str(tool_path.resolve()))})\n"
        f"mod = importlib.util.module_from_spec(spec)\n"
        f"spec.loader.exec_module(mod)\n"
        f"fn = getattr(mod, {json.dumps(name)})\n"
        f"args = {json.dumps(args)}\n"
        f"if inspect.iscoroutinefunction(fn):\n"
        f"    result = asyncio.run(fn(**args))\n"
        f"else:\n"
        f"    result = fn(**args)\n"
        f"if isinstance(result, str):\n"
        f"    print(json.dumps({{'ok': True, 'result': result}}))\n"
        f"else:\n"
        f"    print(json.dumps({{'ok': True, 'result': json.dumps(result, default=str)}}))\n"
    )

    import subprocess

    try:
        proc = await __import__("asyncio").create_subprocess_exec(
            str(venv_python),
            "-c",
            harness,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
        )
        stdout, _ = await proc.communicate()
        output = stdout.decode("utf-8", errors="replace").strip()
    except Exception as exc:
        return json.dumps({"error": f"Failed to run test subprocess: {exc}"})

    if proc.returncode != 0:
        return json.dumps(
            {
                "status": "error",
                "toolbox": toolbox,
                "tool": name,
                "exit_code": proc.returncode,
                "error": output or "(no output)",
            },
            indent=2,
        )

    # The harness prints a JSON envelope on success.  Try to parse it.
    try:
        envelope = json.loads(output)
        return json.dumps(
            {
                "status": "ok",
                "toolbox": toolbox,
                "tool": name,
                "result": envelope.get("result", output),
            },
            indent=2,
        )
    except json.JSONDecodeError:
        # Harness produced non-JSON output -- return it raw.
        return json.dumps(
            {
                "status": "ok",
                "toolbox": toolbox,
                "tool": name,
                "result": output,
            },
            indent=2,
        )
