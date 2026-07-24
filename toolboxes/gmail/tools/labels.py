"""
Manage Gmail labels.

Public functions (all return plain dicts):

    list_labels()
        List all labels (system and user-defined).

    add_labels(message_id, label_ids)
        Attach one or more labels to a message.

    remove_labels(message_id, label_ids)
        Detach one or more labels from a message.

**Safety:** System labels ``INBOX``, ``SENT``, ``STARRED``, and ``IMPORTANT``
are managed automatically by Gmail.  ``remove_labels`` will refuse to strip
them.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path
from typing import Any, Optional

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
# Constants
# ---------------------------------------------------------------------------

# Labels managed automatically by Gmail — never manually removable.
_SYSTEM_LABELS_FROZEN = frozenset({"INBOX", "SENT", "STARRED", "IMPORTANT"})


# ---------------------------------------------------------------------------
# Public: list_labels
# ---------------------------------------------------------------------------


def list_labels() -> dict[str, Any]:
    """List all labels on the authenticated account.

    Returns
    -------
    dict
        ``{"success": True, "labels": [...]}`` on success, **or**
        ``{"error": "...", "detail": "..."}`` on failure.

        Each label dict contains ``id``, ``name``, ``type`` (``system`` or
        ``user``), ``messageListVisibility``, ``labelListVisibility``,
        ``color``, ``messagesTotal``, ``messagesUnread``, and
        ``threadsTotal`` when available from the API.
    """
    auth_result = _get_gmail_service()
    if "error" in auth_result:
        return auth_result
    service = auth_result["service"]

    try:
        response = service.users().labels().list(userId="me").execute()
        labels = response.get("labels", [])

        # Enrich with per-label details (color, counts, visibility)
        enriched = []
        for label in labels:
            enriched.append(_enrich_label(label))

        return {"success": True, "labels": enriched}
    except HttpError as exc:
        return _http_label_error(exc)
    except Exception as exc:
        return {"error": "Failed to list labels.", "detail": str(exc)}


# ---------------------------------------------------------------------------
# Public: add_labels
# ---------------------------------------------------------------------------


def add_labels(
    message_id: str,
    label_ids: list[str],
) -> dict[str, Any]:
    """Attach labels to a single message.

    Parameters
    ----------
    message_id : str
        The Gmail message ID.
    label_ids : list[str]
        One or more label IDs to add.

    Returns
    -------
    dict
        ``{"success": True, "message": {"id": "...", "label_ids": [...]}}``
        on success, **or** ``{"error": "...", "detail": "..."}`` on failure.
    """
    # --- Validate ---------------------------------------------------------
    if not message_id:
        return {"error": "message_id is required"}
    if not label_ids or not isinstance(label_ids, list) or len(label_ids) == 0:
        return {"error": "label_ids must be a non-empty list"}

    # --- Execute ----------------------------------------------------------
    return _modify_labels(message_id, add_label_ids=label_ids)


# ---------------------------------------------------------------------------
# Public: remove_labels
# ---------------------------------------------------------------------------


def remove_labels(
    message_id: str,
    label_ids: list[str],
) -> dict[str, Any]:
    """Detach labels from a single message.

    .. warning:: System labels ``INBOX``, ``SENT``, ``STARRED``, and
        ``IMPORTANT`` are managed by Gmail and **cannot** be manually
        removed.  This function will refuse if any of them are present in
        *label_ids*.

    Parameters
    ----------
    message_id : str
        The Gmail message ID.
    label_ids : list[str]
        One or more label IDs to remove.

    Returns
    -------
    dict
        ``{"success": True, "message": {"id": "...", "label_ids": [...]}}``
        on success, **or** ``{"error": "...", "detail": "..."}`` on failure.
    """
    # --- Validate ---------------------------------------------------------
    if not message_id:
        return {"error": "message_id is required"}
    if not label_ids or not isinstance(label_ids, list) or len(label_ids) == 0:
        return {"error": "label_ids must be a non-empty list"}

    # --- Safety: refuse to strip system-managed labels --------------------
    for lid in label_ids:
        if lid.upper() in _SYSTEM_LABELS_FROZEN:
            return {
                "error": (
                    f"Cannot manually remove system label: {lid}. "
                    "This label is managed by Gmail."
                ),
            }

    return _modify_labels(message_id, remove_label_ids=label_ids)


# ---------------------------------------------------------------------------
# Internal: shared modify call
# ---------------------------------------------------------------------------


def _modify_labels(
    message_id: str,
    add_label_ids: Optional[list[str]] = None,
    remove_label_ids: Optional[list[str]] = None,
) -> dict[str, Any]:
    """Shared implementation for ``add_labels`` and ``remove_labels``."""
    auth_result = _get_gmail_service()
    if "error" in auth_result:
        return auth_result
    service = auth_result["service"]

    body: dict[str, Any] = {}
    if add_label_ids:
        body["addLabelIds"] = add_label_ids
    if remove_label_ids:
        body["removeLabelIds"] = remove_label_ids

    try:
        modified = (
            service.users()
            .messages()
            .modify(userId="me", id=message_id, body=body)
            .execute()
        )
        return {
            "success": True,
            "message": {
                "id": modified.get("id", ""),
                "label_ids": modified.get("labelIds", []),
            },
        }
    except HttpError as exc:
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
    except Exception as exc:
        return {"error": "Failed to modify labels.", "detail": str(exc)}


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _enrich_label(label: dict) -> dict[str, Any]:
    """Build a rich label dict with all available fields."""
    return {
        "id": label.get("id", ""),
        "name": label.get("name", ""),
        "type": label.get("type", ""),  # "system" or "user"
        "messageListVisibility": label.get("messageListVisibility", ""),
        "labelListVisibility": label.get("labelListVisibility", ""),
        "color": label.get("color", {}),
        "messagesTotal": label.get("messagesTotal", 0),
        "messagesUnread": label.get("messagesUnread", 0),
        "threadsTotal": label.get("threadsTotal", 0),
    }


def _safe_reason(exc: HttpError) -> str:
    try:
        return exc._get_reason() or str(exc)
    except Exception:
        return str(exc)


def _http_label_error(exc: HttpError) -> dict[str, Any]:
    status = exc.resp.status if exc.resp is not None else 0
    reason = _safe_reason(exc)
    if status == 429:
        return {
            "error": "Rate limit exceeded. Retry after a few seconds.",
            "retry_after": 5,
        }
    return {"error": f"Gmail API error ({status}): {reason}"}
