"""Trash, untrash, or permanently delete multiple Gmail messages in parallel."""

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
_build_gmail_service = _gmail_base.build_gmail_service


def _trash_one(message_id: str) -> dict[str, Any]:
    auth_result = _build_gmail_service()
    if "error" in auth_result:
        return {
            "success": False,
            "message_id": message_id,
            "error": auth_result.get("error"),
        }
    service = auth_result["service"]
    try:
        service.users().messages().trash(userId="me", id=message_id).execute()
        return {"success": True, "message_id": message_id, "action": "trashed"}
    except HttpError as exc:
        status = exc.resp.status if exc.resp is not None else 0
        return {
            "success": False,
            "message_id": message_id,
            "error": f"Gmail API error ({status}): {exc}",
        }
    except Exception as exc:  # noqa: BLE001
        return {"success": False, "message_id": message_id, "error": str(exc)}


def _untrash_one(message_id: str) -> dict[str, Any]:
    auth_result = _build_gmail_service()
    if "error" in auth_result:
        return {
            "success": False,
            "message_id": message_id,
            "error": auth_result.get("error"),
        }
    service = auth_result["service"]
    try:
        service.users().messages().untrash(userId="me", id=message_id).execute()
        return {"success": True, "message_id": message_id, "action": "untrashed"}
    except HttpError as exc:
        status = exc.resp.status if exc.resp is not None else 0
        return {
            "success": False,
            "message_id": message_id,
            "error": f"Gmail API error ({status}): {exc}",
        }
    except Exception as exc:  # noqa: BLE001
        return {"success": False, "message_id": message_id, "error": str(exc)}


def _delete_one(message_id: str) -> dict[str, Any]:
    auth_result = _build_gmail_service()
    if "error" in auth_result:
        return {
            "success": False,
            "message_id": message_id,
            "error": auth_result.get("error"),
        }
    service = auth_result["service"]
    try:
        service.users().messages().delete(userId="me", id=message_id).execute()
        return {
            "success": True,
            "message_id": message_id,
            "action": "deleted_permanently",
        }
    except HttpError as exc:
        status = exc.resp.status if exc.resp is not None else 0
        return {
            "success": False,
            "message_id": message_id,
            "error": f"Gmail API error ({status}): {exc}",
        }
    except Exception as exc:  # noqa: BLE001
        return {"success": False, "message_id": message_id, "error": str(exc)}


def _run_parallel(
    message_ids: list[str],
    worker,
    max_workers: int = 5,
) -> dict[str, Any]:
    if not message_ids:
        return {"success": True, "results": []}

    # Validate auth once up front so we fail fast instead of in every worker.
    auth_result = _get_gmail_service()
    if "error" in auth_result:
        return auth_result

    results: list[dict[str, Any]] = []
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        future_to_id = {executor.submit(worker, mid): mid for mid in message_ids}
        for future in as_completed(future_to_id):
            results.append(future.result())

    return {"success": True, "results": results}


def trash_messages(
    message_ids: list[str],
    max_workers: int = 5,
) -> dict[str, Any]:
    """Move multiple messages to trash in parallel.

    Each worker thread builds its own Gmail API service to avoid thread-safety
    issues with googleapiclient.

    Parameters
    ----------
    message_ids : list[str]
        Gmail message IDs to trash.
    max_workers : int, optional
        Maximum parallel API calls (default 5).

    Returns
    -------
    dict
        ``{"success": True, "results": [...]}`` with per-message outcomes.
    """
    return _run_parallel(message_ids, _trash_one, max_workers)


def untrash_messages(
    message_ids: list[str],
    max_workers: int = 5,
) -> dict[str, Any]:
    """Restore multiple trashed messages in parallel.

    Each worker thread builds its own Gmail API service to avoid thread-safety
    issues with googleapiclient.

    Parameters
    ----------
    message_ids : list[str]
        Gmail message IDs to restore.
    max_workers : int, optional
        Maximum parallel API calls (default 5).

    Returns
    -------
    dict
        ``{"success": True, "results": [...]}`` with per-message outcomes.
    """
    return _run_parallel(message_ids, _untrash_one, max_workers)
