"""
Python-native KeePass .kdbx management.

Replaces the previous keepassxc-cli subprocess approach with pykeepass.
Provides both a KeePassDB class for programmatic access and legacy-compatible
helper functions.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import List

from pykeepass import PyKeePass
from pykeepass.exceptions import CredentialsError

# ---------------------------------------------------------------------------
# Typed exception hierarchy
# ---------------------------------------------------------------------------


class KeePassError(Exception):
    """Base exception for KeePass-related errors."""


class KeePassNotFoundError(KeePassError):
    """The .kdbx file does not exist."""


class KeePassWrongPasswordError(KeePassError):
    """The master password is incorrect."""


class KeePassEntryNotFoundError(KeePassError):
    """No entry with the given title exists."""


class KeePassDBCorruptedError(KeePassError):
    """The .kdbx file exists but cannot be parsed."""


# ---------------------------------------------------------------------------
# KeePassDB class
# ---------------------------------------------------------------------------


class KeePassDB:
    """Opens and manages a KeePass .kdbx database.

    Usage:
        db = KeePassDB("keys.kdbx", "master-password")
        db.list_keys()         # -> ["OPENAI_API_KEY", ...]
        db.get_key("SERVICE")  # -> "sk-..."
        db.add_key("SERVICE", "new-value")
        db.update_key("SERVICE", "updated-value")
    """

    def __init__(self, db_path: str | Path, master_password: str) -> None:
        """Open an existing .kdbx file or prompt to create it."""
        self._path = Path(db_path).resolve()
        if not self._path.exists():
            print(f"KeePass database not found: {self._path}")
            response = input("Do you want to create it? (y/n): ").strip().lower()
            if response != "y":
                raise KeePassNotFoundError(
                    f"KeePass database not found: {self._path}\n"
                    f"Run `agenthost key add` to create it."
                )
            created = KeePassDB.create(self._path, master_password)
            self._kp = created._kp
            return
        try:
            self._kp = PyKeePass(str(self._path), password=master_password)
        except CredentialsError:
            raise KeePassWrongPasswordError(
                "Invalid master password for KeePass database."
            ) from None
        except Exception as exc:
            raise KeePassDBCorruptedError(
                f"Failed to open KeePass database: {exc}"
            ) from exc

    @classmethod
    def create(cls, db_path: str | Path, master_password: str) -> KeePassDB:
        """Create a new empty .kdbx database.

        If the file already exists, it will be opened (not overwritten).
        """
        path = db_path
        if path.exists():
            return cls(path, master_password)

        from pykeepass import create_database

        kp = create_database(str(path), password=master_password)
        kp.save()
        instance = cls.__new__(cls)
        instance._path = path
        instance._kp = kp
        return instance

    def list_keys(self) -> List[str]:
        """Return sorted list of all entry titles in the database."""
        entries = self._kp.entries
        return sorted(e.title for e in entries if e.title)

    def get_key(self, title: str) -> str:
        """Return the password for a given entry title.

        Raises KeePassEntryNotFoundError if no entry matches.
        """
        entry = self._kp.find_entries_by_title(title, first=True)
        if entry is None:
            raise KeePassEntryNotFoundError(
                f"No entry found with title '{title}' in {self._path}"
            )
        return entry.password

    def add_key(self, title: str, password: str) -> None:
        """Add a new entry with the given title and password.

        Raises ValueError if an entry with this title already exists.
        """
        existing = self._kp.find_entries_by_title(title, first=True)
        if existing is not None:
            raise ValueError(
                f"Entry '{title}' already exists. "
                f"Use `agenthost key edit` to update it."
            )
        self._kp.add_entry(
            self._kp.root_group,
            title=title,
            username=title,
            password=password,
        )
        self._save()

    def update_key(self, title: str, new_password: str) -> None:
        """Update the password for an existing entry.

        Raises KeePassEntryNotFoundError if no entry matches.
        """
        entry = self._kp.find_entries_by_title(title, first=True)
        if entry is None:
            raise KeePassEntryNotFoundError(
                f"No entry found with title '{title}' in {self._path}"
            )
        entry.password = new_password
        self._save()

    def _save(self) -> None:
        """Persist changes to the .kdbx file."""
        self._kp.save()


# ---------------------------------------------------------------------------
# Legacy compatibility: load_keepass_env() keeps its exact signature so that
# existing callers elsewhere continue to work unchanged.
# ---------------------------------------------------------------------------


def load_keepass_env(db_path: str, entry_path: str, master_password: str) -> None:
    """Extract a key from the KeePass database and set it as an environment variable.

    This is the legacy entry point used by cli.py:main(). It preserves the original
    signature and behavior: loading the value into os.environ.

    Args:
        db_path: Path to the .kdbx file (relative to repo root or absolute).
        entry_path: Title of the KeePass entry (also used as env var name).
        master_password: The master password for the database.
    """
    # Resolve relative path from repo root (matching legacy behavior)
    db = KeePassDB(db_path, master_password)
    secret_value = db.get_key(entry_path)
    os.environ[entry_path] = secret_value
    print(f"\u2705 Successfully loaded {entry_path} into environment.")


def load_all_keepass_env(db_path: str | Path, master_password: str) -> None:
    """Load every entry in the KeePass database into the process environment.

    Each entry's title becomes the environment-variable name and its password
    becomes the value. Skips entries with empty titles. Does not overwrite
    variables that are already set.

    Args:
        db_path: Path to the .kdbx file.
        master_password: The master password for the database.
    """
    db = KeePassDB(db_path, master_password)
    entries = db._kp.entries
    loaded = 0
    skipped = 0
    for entry in entries:
        title = entry.title
        if not title:
            skipped += 1
            continue
        if title in os.environ:
            skipped += 1
            continue
        os.environ[title] = entry.password
        loaded += 1

    print(
        f"\u2705 Loaded {loaded} key(s) from KeePass into environment"
        f"{' (' + str(skipped) + ' skipped)' if skipped else '.'}"
    )
