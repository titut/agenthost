"""Terminal execution tool for the SW_DEV agent."""
from __future__ import annotations

import subprocess
from pathlib import Path


# Operations are scoped to the directory from which agenthost was invoked.
_REPO_ROOT = Path.cwd()


def run_terminal(command: str, timeout: int = 60) -> dict:
    """Run a shell command in the repository root.

    The command executes with the current working directory set to the
    repository root. Output is captured and returned; interactive programs
    are not supported.

    Args:
        command: The shell command to execute.
        timeout: Maximum seconds to wait for the command (default 60).

    Returns:
        A dict with command, returncode, stdout, and stderr.
    """
    if not command or not command.strip():
        return {"error": "Command cannot be empty."}

    safe_command = command.strip()

    try:
        result = subprocess.run(
            safe_command,
            shell=True,
            cwd=_REPO_ROOT,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        return {
            "error": f"Command timed out after {timeout} seconds.",
            "command": safe_command,
        }
    except Exception as exc:  # noqa: BLE001
        return {
            "error": f"Failed to run command: {type(exc).__name__}: {exc}",
            "command": safe_command,
        }

    return {
        "command": safe_command,
        "returncode": result.returncode,
        "stdout": result.stdout,
        "stderr": result.stderr,
    }
