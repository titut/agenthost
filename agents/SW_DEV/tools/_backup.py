"""Backup utility for SW_DEV file tools.

Every overwrite of an existing file is preceded by a timestamped backup under
.agenthost/backups/ so accidental data loss is recoverable.
"""
from __future__ import annotations

import datetime
from pathlib import Path


# Repository root is three levels above this file: agents/SW_DEV/tools/_backup.py
_REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent
_BACKUP_ROOT = _REPO_ROOT / ".agenthost" / "backups"


def backup_file(target: Path) -> Path:
    """Write a timestamped backup of *target* and return the backup path.

    Backups are stored under .agenthost/backups/<iso-timestamp>/<rel-path>.
    The backup preserves the exact bytes of the original file. If multiple
    backups of the same file occur in the same second, a counter is appended.
    """
    timestamp = datetime.datetime.now().isoformat(timespec="seconds")
    rel_path = target.relative_to(_REPO_ROOT.resolve())
    backup_dir = _BACKUP_ROOT / timestamp
    backup_path = backup_dir / rel_path

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
