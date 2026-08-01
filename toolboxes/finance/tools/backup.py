"""Git backup tool for personal finance entries.

Public functions:

    backup_finance_entries(message=None)
        Stage all changed files, commit with a timestamp, and push to origin.
"""

from __future__ import annotations

import os
import stat
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from git import Repo
from git.exc import GitCommandError

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_REPO_PATH = Path.home() / ".agenthost" / "entries"
_BRANCH = "main"
_REMOTE = "origin"

# ---------------------------------------------------------------------------
# Public: backup_finance_entries
# ---------------------------------------------------------------------------


def backup_finance_entries(message: str | None = None) -> dict[str, Any]:
    """Stage all changed files, commit, and push the finance entries repo to GitHub.

    The repository at ``~/.agenthost/entries`` must already be configured
    with a ``git@github.com:...`` remote.  The SSH passphrase is read from
    the ``SSH_PASS`` environment variable (typically loaded from the KeePass
    database when the agent starts).

    Parameters
    ----------
    message : str | None
        Optional custom commit message.  If omitted, a timestamp-based
        message is generated automatically (``backup: 2026-08-01 16:15``).

    Returns
    -------
    dict
        ``{"success": True, "timestamp": "...", "commit": "...",
           "files_changed": N, "pushed": True}`` on success, or
        ``{"error": "..."}`` on failure.
    """
    # --- validate repo ---------------------------------------------------
    if not (_REPO_PATH / ".git").exists():
        return {
            "error": (
                f"No git repository found at {_REPO_PATH}. "
                "Initialize one with: cd ~/.agenthost/entries && git init && "
                "git remote add origin git@github.com:..."
            )
        }

    ssh_pass = os.environ.get("SSH_PASS", "")
    if not ssh_pass:
        return {
            "error": (
                "SSH_PASS environment variable is not set. "
                "Ensure the KeePass database contains an entry named 'SSH_PASS' "
                "and that the agent was started with 'serve' or 'chat'."
            )
        }

    # --- open repo -------------------------------------------------------
    try:
        repo = Repo(str(_REPO_PATH))
    except Exception as exc:  # noqa: BLE001
        return {"error": f"Failed to open git repository: {exc}"}

    # --- check for uncommitted changes -----------------------------------
    if not repo.is_dirty(untracked_files=True):
        return {
            "success": True,
            "message": "Nothing to commit — working tree is clean.",
            "pushed": False,
        }

    # --- stage all files (git add .) -------------------------------------
    try:
        repo.git.add(".")
    except GitCommandError as exc:
        return {"error": f"git add failed: {exc.stderr.strip()}"}

    # --- commit ----------------------------------------------------------
    if message is None:
        utc_now = datetime.now(timezone.utc)
        local_now = utc_now.astimezone()
        message = f"backup: {local_now.strftime('%Y-%m-%d %H:%M')}"

    try:
        commit = repo.index.commit(message)
    except GitCommandError as exc:
        return {"error": f"git commit failed: {exc.stderr.strip()}"}

    commit_hash = commit.hexsha[:7]
    files_changed = len(commit.stats.files) if commit.stats else 0

    # --- push via SSH_ASKPASS trick --------------------------------------
    helper_path = _write_askpass_helper(ssh_pass)
    try:
        with repo.git.custom_environment(
            SSH_ASKPASS=str(helper_path),
            SSH_ASKPASS_REQUIRE="force",
            DISPLAY=":0",
        ):
            push_result = repo.remotes[_REMOTE].push(_BRANCH)
    except GitCommandError as exc:
        return {
            "error": (
                f"git push failed: {exc.stderr.strip()}. "
                "Check that the SSH key is unlocked and the remote is reachable."
            )
        }
    finally:
        _cleanup_askpass_helper(helper_path)

    # Check if any ref was actually updated.
    pushed = False
    if push_result:
        for push_info in push_result:
            if push_info.flags & push_info.ERROR:
                return {"error": f"Push failed: {push_info.summary.strip()}"}
            if (
                push_info.flags & push_info.FAST_FORWARD
                or push_info.flags & push_info.NEW_HEAD
                or push_info.flags & push_info.FORCED_UPDATE
            ):
                pushed = True

    return {
        "success": True,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "commit": commit_hash,
        "message": message,
        "files_changed": files_changed,
        "pushed": pushed,
    }


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _write_askpass_helper(ssh_pass: str) -> Path:
    """Write a temporary executable script that prints ``ssh_pass``.

    Returns the path to the helper script.
    """
    script = f"#!/usr/bin/env python3\nprint({ssh_pass!r})\n"
    fd, path = tempfile.mkstemp(suffix=".py", prefix="ssh_askpass_")
    with os.fdopen(fd, "w") as fh:
        fh.write(script)
    os.chmod(path, os.stat(path).st_mode | stat.S_IEXEC)
    return Path(path)


def _cleanup_askpass_helper(helper_path: Path) -> None:
    """Remove the temporary askpass helper script."""
    try:
        helper_path.unlink(missing_ok=True)
    except Exception:  # noqa: BLE001
        pass
