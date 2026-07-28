"""Dependency management for evolved toolboxes."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

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
# Tools
# ===================================================================


def add_requirements(toolbox: str, requirements: str) -> str:
    """Add entries to a toolbox's ``requirements.txt``.

    Appends the given requirements to the file, creating it if it doesn't
    exist. Duplicate lines already present in the file are skipped so the
    same package is never listed twice.

    After calling this, call ``install_requirements(toolbox)`` to install
    the packages into the evolve venv.

    Args:
        toolbox: Name of the toolbox.
        requirements: One or more pip-compatible requirement lines (e.g.
            ``"requests>=2.28"`` or ``"beautifulsoup4\\nhttpx>=0.27"``).
            Multiple entries should be separated by newlines.
    """
    try:
        toolbox_dir = _require(toolbox)
    except ValueError as exc:
        return json.dumps({"error": str(exc)})

    if not requirements.strip():
        return json.dumps({"error": "requirements string cannot be empty."})

    req_path = toolbox_dir / "requirements.txt"

    existing: set[str] = set()
    if req_path.exists():
        for line in req_path.read_text(encoding="utf-8").splitlines():
            stripped = line.strip()
            if stripped and not stripped.startswith("#"):
                existing.add(stripped.lower())

    new_lines: list[str] = []
    skipped: list[str] = []
    for line in requirements.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if stripped.lower() in existing:
            skipped.append(stripped)
        else:
            new_lines.append(stripped)
            existing.add(stripped.lower())

    if not new_lines and skipped:
        return json.dumps(
            {
                "status": "unchanged",
                "toolbox": toolbox,
                "message": "All requirements already present.",
                "skipped": skipped,
            },
            indent=2,
        )

    if not new_lines:
        return json.dumps(
            {"status": "unchanged", "toolbox": toolbox, "message": "Nothing to add."},
            indent=2,
        )

    try:
        req_path.parent.mkdir(parents=True, exist_ok=True)
        with req_path.open("a", encoding="utf-8") as fh:
            for line in new_lines:
                fh.write(line + "\n")
    except Exception as exc:
        return json.dumps({"error": f"Failed to write requirements.txt: {exc}"})

    result: dict[str, Any] = {
        "status": "added",
        "toolbox": toolbox,
        "added": new_lines,
    }
    if skipped:
        result["skipped"] = skipped
    return json.dumps(result, indent=2)


async def install_requirements(toolbox: str) -> str:
    """Install a toolbox's ``requirements.txt`` into the evolve venv.

    Runs ``pip install -r <toolbox>/requirements.txt`` using the isolated
    ``_evolve_venv`` Python environment under ``toolboxes-evolve/``.
    Always run this after calling ``add_requirements()`` and before
    ``test_tool()`` or switching to the toolbox so its tools can import
    the new packages.

    Args:
        toolbox: Name of the toolbox whose requirements to install.
    """
    try:
        toolbox_dir = _require(toolbox)
    except ValueError as exc:
        return json.dumps({"error": str(exc)})

    req_path = toolbox_dir / "requirements.txt"
    if not req_path.exists():
        return json.dumps(
            {
                "error": f"Toolbox '{toolbox}' has no requirements.txt. "
                f"Use add_requirements() first."
            }
        )

    venv_pip = _EVOLVE_ROOT / "_evolve_venv" / "bin" / "pip"

    if not venv_pip.exists():
        return json.dumps(
            {
                "error": f"Evolve venv pip not found at {venv_pip}. "
                f"Run: python -m venv {_EVOLVE_ROOT / '_evolve_venv'}"
            }
        )

    import subprocess

    try:
        proc = await __import__("asyncio").create_subprocess_exec(
            str(venv_pip),
            "install",
            "-r",
            str(req_path),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
        )
        stdout, _ = await proc.communicate()
        output = stdout.decode("utf-8", errors="replace")
    except Exception as exc:
        return json.dumps({"error": f"Failed to run pip: {exc}"})

    return json.dumps(
        {
            "toolbox": toolbox,
            "requirements_file": str(req_path.relative_to(_EVOLVE_ROOT)),
            "exit_code": proc.returncode,
            "output": output,
        },
        indent=2,
    )
