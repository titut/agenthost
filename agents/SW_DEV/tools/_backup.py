"""Backup utility for SW_DEV file tools.

Every overwrite of an existing file is preceded by a timestamped backup under
<SW_DEV agent directory>/.backup/ so accidental data loss is recoverable.
"""
from __future__ import annotations

import datetime
from pathlib import Path


# The SW_DEV agent directory is two levels above this file:
# agents/SW_DEV/tools/_backup.py -> agents/SW_DEV
_AGENT_DIR = Path(__file__).resolve().parent.parent
_BACKUP_ROOT = _AGENT_DIR / ".backup"


def _backup_subpath(target: Path) -> Path:
    """Build a unique subpath under .backup/ for an arbitrary absolute target."""
    resolved = target.resolve()
    parts = resolved.parts
    if parts and parts[0] == "/":
        parts = parts[1:]
    return Path(*parts)


def backup_file(target: Path) -> Path:
    """Write a timestamped backup of *target* and return the backup path.

    Backups are stored under <agent-dir>/.backup/<iso-timestamp>/<target-path>.
    The backup preserves the exact bytes of the original file. If multiple
    backups of the same file occur in the same second, a counter is appended.
    """
    timestamp = datetime.datetime.now().isoformat(timespec="seconds")
    sub_path = _backup_subpath(target)
    backup_dir = _BACKUP_ROOT / timestamp
    backup_path = backup_dir / sub_path

    # Ensure uniqueness if multiple operations happen in the same second.
    counter = 1
    original_backup_path = backup_path
    while backup_path.exists():
        suffix = f".{counter}"
        backup_path = original_backup_path.with_name(original_backup_path.name + suffix)
        counter += 1

    backup_path.parent.mkdir(parents=True, exist_ok=True)
    backup_path.write_bytes(target.read_bytes())
    return backup_path
