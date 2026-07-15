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


def _resolve_label_names(names: list[str]) -> dict[str, Any]:
    """Resolve human-friendly label names to their Gmail label IDs.

    Parameters
    ----------
    names : list[str]
        Label names to look up (case-insensitive match).

    Returns
    -------
    dict
        ``{"resolved": {name: id, ...}, "unmatched": [...]}`` on success, **or**
        ``{"error": "...", "detail": "..."}`` if label listing fails.
    """
    result = list_labels()
    if result.get("error"):
        return result

    labels = result.get("labels", [])
    name_map: dict[str, str] = {}
    for label in labels:
        name_map[label["name"].lower()] = label["id"]

    resolved: dict[str, str] = {}
    unmatched: list[str] = []
    for name in names:
        gmail_id = name_map.get(name.lower())
        if gmail_id:
            resolved[name] = gmail_id
        else:
            unmatched.append(name)

    return {"resolved": resolved, "unmatched": unmatched}


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
    label_names: list[str],
) -> dict[str, Any]:
    """Add labels to multiple messages in a single batch API call.

    Labels are specified by **name** (e.g. ``"Work"`` or ``"INBOX"``). The
    function resolves names to their Gmail IDs for you.

    Parameters
    ----------
    message_ids : list[str]
        Gmail message IDs to label.
    label_names : list[str]
        Label names to add (case-insensitive).  Unknown names are ignored.

    Returns
    -------
    dict
        ``{"success": True, "message_ids": [...], "unmatched_names": [...]}`` on
        success, **or** ``{"success": False, "error": "..."}`` on failure.
    """
    if not message_ids:
        return {"success": False, "error": "message_ids is required"}
    if not label_names or not isinstance(label_names, list) or len(label_names) == 0:
        return {"success": False, "error": "label_names must be a non-empty list"}

    resolved = _resolve_label_names(label_names)
    if resolved.get("error"):
        return resolved

    label_id_list = list(resolved["resolved"].values())
    if not label_id_list:
        available = resolved.get("available_label_names", [])
        return {
            "success": False,
            "error": f"None of the requested labels were found on this account.",
            "unmatched_names": resolved["unmatched"],
        }

    auth_result = _get_gmail_service()
    if "error" in auth_result:
        return auth_result
    service = auth_result["service"]

    result = _batch_modify(
        service, message_ids, add_label_ids=label_id_list, remove_label_ids=[]
    )
    if result.get("success"):
        result["unmatched_names"] = resolved["unmatched"]
    return result


def remove_labels_from_messages(
    message_ids: list[str],
    label_names: list[str],
) -> dict[str, Any]:
    """Remove labels from multiple messages in a single batch API call.

    Labels are specified by **name** (e.g. ``"Work"``). The function resolves
    names to their Gmail IDs for you.

    System labels ``INBOX``, ``SENT``, ``STARRED``, and ``IMPORTANT`` cannot be
    manually removed and will be ignored.

    Parameters
    ----------
    message_ids : list[str]
        Gmail message IDs to update.
    label_names : list[str]
        Label names to remove (case-insensitive). Unknown names are ignored.

    Returns
    -------
    dict
        ``{"success": True, "message_ids": [...], "ignored_labels": [...],
        "unmatched_names": [...]}`` on success, **or**
        ``{"success": False, "error": "..."}`` on failure.
    """
    if not message_ids:
        return {"success": False, "error": "message_ids is required"}
    if not label_names or not isinstance(label_names, list) or len(label_names) == 0:
        return {"success": False, "error": "label_names must be a non-empty list"}

    resolved = _resolve_label_names(label_names)
    if resolved.get("error"):
        return resolved

    # Filter out system labels by matching resolved IDs against the frozen set.
    removable_ids: list[str] = []
    ignored: list[str] = []
    for name, lid in resolved["resolved"].items():
        if lid in _SYSTEM_LABELS_FROZEN:
            ignored.append(name)
        else:
            removable_ids.append(lid)

    if not removable_ids:
        return {
            "success": False,
            "error": "No removable labels provided (system labels cannot be removed).",
            "ignored_labels": ignored,
            "unmatched_names": resolved["unmatched"],
        }

    auth_result = _get_gmail_service()
    if "error" in auth_result:
        return auth_result
    service = auth_result["service"]

    result = _batch_modify(
        service, message_ids, add_label_ids=[], remove_label_ids=removable_ids
    )
    if result.get("success"):
        result["ignored_labels"] = ignored
        result["unmatched_names"] = resolved["unmatched"]
    return result


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
