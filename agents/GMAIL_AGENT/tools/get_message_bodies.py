"""Fetch the bodies of multiple Gmail messages in parallel."""

from __future__ import annotations

import importlib.util
from concurrent.futures import ThreadPoolExecutor, as_completed
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
_extract_body = _gmail_base.extract_body
_safe_reason = getattr(_gmail_base, "_safe_reason", None)
if _safe_reason is None:
    def _safe_reason(exc: HttpError) -> str:
        try:
            return exc._get_reason() or "Unknown error"
        except Exception:  # noqa: BLE001
            return str(exc)


def _get_one_body(service, message_id: str) -> dict[str, Any]:
    """Fetch and extract body for a single message."""
    try:
        msg = service.users().messages().get(userId="me", id=message_id, format="full").execute()
        payload = msg.get("payload", {})
        headers = {h["name"].lower(): h["value"] for h in payload.get("headers", []) if "name" in h and "value" in h}
        return {
            "success": True,
            "message_id": message_id,
            "thread_id": msg.get("threadId"),
            "snippet": msg.get("snippet", ""),
            "label_ids": msg.get("labelIds", []),
            "from": headers.get("from", ""),
            "to": headers.get("to", ""),
            "subject": headers.get("subject", ""),
            "date": headers.get("date", ""),
            "body": _extract_body(payload),
        }
    except HttpError as exc:
        status = exc.resp.status if exc.resp is not None else 0
        reason = _safe_reason(exc)
        if status == 404:
            return {"success": False, "message_id": message_id, "error": "Message not found."}
        return {"success": False, "message_id": message_id, "error": f"Gmail API error ({status}): {reason}"}
    except Exception as exc:  # noqa: BLE001
        return {"success": False, "message_id": message_id, "error": str(exc)}


def get_message_bodies(
    message_ids: list[str],
    max_workers: int = 5,
) -> dict[str, Any]:
    """Fetch the plain-text bodies and key headers for multiple messages in parallel.

    This is faster than calling ``get_message_body`` repeatedly when you need to
    read several messages at once.

    Parameters
    ----------
    message_ids : list[str]
        Gmail message IDs to fetch.
    max_workers : int, optional
        Maximum parallel API calls (default 5).

    Returns
    -------
    dict
        ``{"success": True, "messages": [...]}`` where each item contains
        ``message_id``, ``thread_id``, ``snippet``, ``label_ids``, ``from``,
        ``to``, ``subject``, ``date``, and ``body``.
    """
    if not message_ids:
        return {"success": True, "messages": []}

    auth_result = _get_gmail_service()
    if "error" in auth_result:
        return auth_result
    service = auth_result["service"]

    results: list[dict[str, Any]] = []
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        future_to_id = {
            executor.submit(_get_one_body, service, mid): mid
            for mid in message_ids
        }
        for future in as_completed(future_to_id):
            results.append(future.result())

    return {"success": True, "messages": results}
