"""Add or remove labels from multiple Gmail messages in batches."""

from __future__ import annotations

import importlib.util
from pathlib import Path
from typing import Any

from googleapiclient.errors import HttpError

_tools_dir = Path(__file__).resolve().parent
_spec = importlib.util.spec_from_file_location(
    "_gmail_base", str(_tools_dir / "_gmail_base.py")
)
_gmail_base = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_gmail_base)

_get_gmail_service = _gmail_base.get_gmail_service

_SYSTEM_LABELS_FROZEN = frozenset({"INBOX", "SENT", "STARRED", "IMPORTANT"})


def _batch_modify(
    service,
    message_ids: list[str],
    add_label_ids: list[str],
    remove_label_ids: list[str],
) -> dict[str, Any]:
    """Use Gmail's batchModify endpoint to update labels on many messages."""
    try:
        body: dict[str, Any] = {"ids": message_ids}
        if add_label_ids:
            body["addLabelIds"] = add_label_ids
        if remove_label_ids:
            body["removeLabelIds"] = remove_label_ids

        service.users().messages().batchModify(userId="me", body=body).execute()
        return {"success": True, "message_ids": message_ids}
    except HttpError as exc:
        status = exc.resp.status if exc.resp is not None else 0
        return {
            "success": False,
            "error": f"Gmail API error ({status}): {exc}",
        }
    except Exception as exc:  # noqa: BLE001
        return {"success": False, "error": str(exc)}


def add_labels_to_messages(
    message_ids: list[str],
    label_ids: list[str],
) -> dict[str, Any]:
    """Add labels to multiple messages in a single batch API call.

    Parameters
    ----------
    message_ids : list[str]
        Gmail message IDs to label.
    label_ids : list[str]
        Label IDs to add.

    Returns
    -------
    dict
        ``{"success": True, "message_ids": [...]}`` on success, **or**
        ``{"success": False, "error": "..."}`` on failure.
    """
    if not message_ids:
        return {"success": False, "error": "message_ids is required"}
    if not label_ids or not isinstance(label_ids, list) or len(label_ids) == 0:
        return {"success": False, "error": "label_ids must be a non-empty list"}

    auth_result = _get_gmail_service()
    if "error" in auth_result:
        return auth_result
    service = auth_result["service"]

    return _batch_modify(service, message_ids, add_label_ids=label_ids, remove_label_ids=[])


def remove_labels_from_messages(
    message_ids: list[str],
    label_ids: list[str],
) -> dict[str, Any]:
    """Remove labels from multiple messages in a single batch API call.

    System labels ``INBOX``, ``SENT``, ``STARRED``, and ``IMPORTANT`` cannot be
    manually removed and will be ignored.

    Parameters
    ----------
    message_ids : list[str]
        Gmail message IDs to update.
    label_ids : list[str]
        Label IDs to remove.

    Returns
    -------
    dict
        ``{"success": True, "message_ids": [...], "ignored_labels": [...]}`` on
        success, **or** ``{"success": False, "error": "..."}`` on failure.
    """
    if not message_ids:
        return {"success": False, "error": "message_ids is required"}
    if not label_ids or not isinstance(label_ids, list) or len(label_ids) == 0:
        return {"success": False, "error": "label_ids must be a non-empty list"}

    removable = [lid for lid in label_ids if lid not in _SYSTEM_LABELS_FROZEN]
    ignored = [lid for lid in label_ids if lid in _SYSTEM_LABELS_FROZEN]
    if not removable:
        return {
            "success": False,
            "error": "No removable labels provided (system labels cannot be removed).",
            "ignored_labels": ignored,
        }

    auth_result = _get_gmail_service()
    if "error" in auth_result:
        return auth_result
    service = auth_result["service"]

    result = _batch_modify(service, message_ids, add_label_ids=[], remove_label_ids=removable)
    if result.get("success"):
        result["ignored_labels"] = ignored
    return result
