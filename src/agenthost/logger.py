"""Centralized logging configuration for agenthost.

Logs go to a fixed file in the agenthost home directory and optionally to
stderr. This makes post-crash diagnosis possible even when the agent server
was running in the background.
"""

from __future__ import annotations

import logging
import os
import sys
from pathlib import Path

from agenthost.home import ensure_agenthost_home


LOG_FILE_NAME = "agenthost.log"

# Debug output (including full LLM request/response payloads) is now enabled
# permanently. Set AGENTHOST_DEBUG=0 to disable it.
DEBUG_MODE = os.environ.get("AGENTHOST_DEBUG", "1").lower() not in ("0", "false", "no", "off")


def get_log_file_path() -> Path:
    """Return the path to the persistent agenthost log file."""
    return ensure_agenthost_home() / LOG_FILE_NAME


def setup_logging(name: str = "agenthost", level: int = logging.INFO) -> logging.Logger:
    """Configure and return the agenthost logger.

    The logger writes to a rotating file in the agenthost home directory and
    mirrors messages to stderr. Safe to call multiple times; existing handlers
    are not duplicated.
    """
    if DEBUG_MODE:
        level = logging.DEBUG
    logger = logging.getLogger(name)
    logger.setLevel(level)

    if logger.handlers:
        return logger

    formatter = logging.Formatter(
        "%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    # Persistent log file in the agenthost home directory.
    log_path = get_log_file_path()
    try:
        file_handler = logging.FileHandler(log_path, mode="a", encoding="utf-8")
        file_handler.setFormatter(formatter)
        logger.addHandler(file_handler)
    except OSError as exc:
        # If we can't write to the log file, fall back to stderr-only logging
        # and emit a warning there.
        sys.stderr.write(f"Could not open log file {log_path}: {exc}\n")

    # Mirror to stderr so foreground runs still show diagnostics.
    stream_handler = logging.StreamHandler(sys.stderr)
    stream_handler.setFormatter(formatter)
    logger.addHandler(stream_handler)

    return logger
