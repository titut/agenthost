"""
Trash, untrash, and permanently delete messages.

Public functions (all return plain dicts):

    trash_message(message_id)
        Move a message to the trash (reversible).

    untrash_message(message_id)
        Restore a trashed message to the inbox (reversible).

    delete_message_permanently(message_id, confirmed=False)
        Permanently delete a message (⚠️ IRREVERSIBLE — requires
        ``confirmed=True``).
"""

from __future__ import annotations

import importlib.util
from pathlib import Path
from typing import Any

from googleapiclient.errors import HttpError

# Load sibling module by absolute path to avoid sys.path pollution.
_tools_dir = Path(__file__).resolve().parent
_spec = importlib.util.spec_from_file_location(
    "_gmail_base", str(_tools_dir / "_gmail_base.py")
)
_gmail_base = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_gmail_base)

_get_gmail_service = _gmail_base.get_gmail_service

# ---------------------------------------------------------------------------
# Public: trash_message
# ---------------------------------------------------------------------------


def trash_message(message_id: str) -> dict[str, Any]:
    """Move a single message to the trash.

    This is **reversible** — the message can be restored via
    ``untrash_message`` or manually in the Gmail web interface.

    Parameters
    ----------
    message_id : str
        The Gmail message ID.

    Returns
    -------
    dict
        ``{"success": True, "message_id": "...", "action": "trashed"}`` on
        success, **or** ``{"error": "...", "detail": "..."}`` on failure.
    """
    if not message_id:
        return {"error": "message_id is required"}

    auth_result = _get_gmail_service()
    if "error" in auth_result:
        return auth_result
    service = auth_result["service"]

    try:
        service.users().messages().trash(userId="me", id=message_id).execute()
        return {
            "success": True,
            "message_id": message_id,
            "action": "trashed",
        }
    except HttpError as exc:
        return _http_trash_error(exc, message_id)
    except Exception as exc:
        return {"error": "Failed to trash message.", "detail": str(exc)}


# ---------------------------------------------------------------------------
# Public: untrash_message
# ---------------------------------------------------------------------------


def untrash_message(message_id: str) -> dict[str, Any]:
    """Restore a single trashed message to the inbox.

    This reverses a ``trash_message`` call.

    Parameters
    ----------
    message_id : str
        The Gmail message ID.

    Returns
    -------
    dict
        ``{"success": True, "message_id": "...", "action": "untrashed"}`` on
        success, **or** ``{"error": "...", "detail": "..."}`` on failure.
    """
    if not message_id:
        return {"error": "message_id is required"}

    auth_result = _get_gmail_service()
    if "error" in auth_result:
        return auth_result
    service = auth_result["service"]

    try:
        service.users().messages().untrash(userId="me", id=message_id).execute()
        return {
            "success": True,
            "message_id": message_id,
            "action": "untrashed",
        }
    except HttpError as exc:
        return _http_trash_error(exc, message_id)
    except Exception as exc:
        return {"error": "Failed to untrash message.", "detail": str(exc)}


# ---------------------------------------------------------------------------
# Public: delete_message_permanently
# ---------------------------------------------------------------------------


def delete_message_permanently(
    message_id: str,
    confirmed: bool = False,
) -> dict[str, Any]:
    """Permanently delete a single message.

    ⚠️ **This operation is IRREVERSIBLE.** The message will be immediately
    and permanently removed and cannot be recovered.

    As a safety guard, the caller must pass ``confirmed=True`` to proceed.
    Without it, the function returns an error explaining the requirement.

    Parameters
    ----------
    message_id : str
        The Gmail message ID.
    confirmed : bool, optional
        Must be ``True`` to proceed with permanent deletion.  Default
        ``False``.

    Returns
    -------
    dict
        ``{"success": True, "message_id": "...",
        "action": "permanently_deleted"}`` on success, **or**
        ``{"error": "Permanent deletion requires confirmed=True. ...",
        "requires_confirmation": True}`` if not confirmed, **or**
        ``{"error": "...", "detail": "..."}`` on API failure.
    """
    if not message_id:
        return {"error": "message_id is required"}

    # --- Safety guard -----------------------------------------------------
    if not confirmed:
        return {
            "error": (
                "Permanent deletion requires confirmed=True. "
                "This action CANNOT be undone. "
                "Use trash_message() instead, which is reversible."
            ),
            "requires_confirmation": True,
            "message_id": message_id,
        }

    # --- Authenticate -----------------------------------------------------
    auth_result = _get_gmail_service()
    if "error" in auth_result:
        return auth_result
    service = auth_result["service"]

    # --- Execute ----------------------------------------------------------
    try:
        service.users().messages().delete(userId="me", id=message_id).execute()
        return {
            "success": True,
            "message_id": message_id,
            "action": "permanently_deleted",
        }
    except HttpError as exc:
        return _http_trash_error(exc, message_id)
    except Exception as exc:
        return {"error": "Failed to delete message.", "detail": str(exc)}


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _safe_reason(exc: HttpError) -> str:
    try:
        return exc._get_reason() or str(exc)
    except Exception:
        return str(exc)


def _http_trash_error(exc: HttpError, message_id: str) -> dict[str, Any]:
    """Translate trash/untrash/delete HttpError into standard error dict."""
    status = exc.resp.status if exc.resp is not None else 0
    reason = _safe_reason(exc)

    if status == 404:
        return {
            "error": f"Message not found: {message_id}. It may have been deleted.",
        }
    if status == 403:
        return {
            "error": f"Insufficient permissions: {reason}. Check the OAuth scope.",
        }
    if status == 429:
        return {
            "error": "Rate limit exceeded. Retry after a few seconds.",
            "retry_after": 5,
        }
    return {"error": f"Gmail API error ({status}): {reason}"}
