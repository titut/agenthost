"""
Read Gmail messages and attachments.

Public functions (all return plain dicts):

    get_message(message_id, format)
        Fetch the full Gmail message resource.

    get_message_body(message_id)
        Convenience wrapper that extracts plain-text body + key headers.

    get_message_attachment(message_id, attachment_id)
        Fetch raw attachment data (base64-encoded).
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
_extract_body = _gmail_base.extract_body

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
_VALID_FORMATS = ("minimal", "full", "raw", "metadata")


# ---------------------------------------------------------------------------
# Public: get_message
# ---------------------------------------------------------------------------


def get_message(
    message_id: str,
    format: str = "full",
) -> dict[str, Any]:
    """Fetch a single message by its Gmail message ID.

    Parameters
    ----------
    message_id : str
        The Gmail message ID (from a ``list_inbox_messages`` or
        ``search_messages`` result).
    format : str, optional
        One of ``"minimal"``, ``"full"`` (default), ``"raw"``, or
        ``"metadata"``.

    Returns
    -------
    dict
        ``{"success": True, "message": {...}}`` with the full Gmail API
        response, **or** ``{"error": "...", "detail": "..."}`` on failure.
    """
    # --- Validate inputs --------------------------------------------------
    if not message_id:
        return {"error": "message_id is required"}

    format = format.lower()
    if format not in _VALID_FORMATS:
        return {
            "error": f"Invalid format '{format}'. Choose from: {', '.join(_VALID_FORMATS)}",
        }

    # --- Authenticate -----------------------------------------------------
    auth_result = _get_gmail_service()
    if "error" in auth_result:
        return auth_result
    service = auth_result["service"]

    # --- Execute ----------------------------------------------------------
    try:
        msg = (
            service.users()
            .messages()
            .get(userId="me", id=message_id, format=format)
            .execute()
        )
        return {"success": True, "message": msg}
    except HttpError as exc:
        status = exc.resp.status if exc.resp is not None else 0
        reason = _safe_reason(exc)
        if status == 404:
            return {
                "error": f"Message not found: {message_id}. It may have been deleted.",
            }
        return {"error": f"Gmail API error ({status}): {reason}"}
    except Exception as exc:
        return {"error": "Failed to fetch message.", "detail": str(exc)}


# ---------------------------------------------------------------------------
# Public: get_message_body
# ---------------------------------------------------------------------------


def get_message_body(message_id: str) -> dict[str, Any]:
    """Fetch a single message and extract its plain-text body and key headers.

    This is a convenience wrapper around ``get_message(..., format="full")``
    that parses out the body text, snippet, and common headers.

    Parameters
    ----------
    message_id : str
        The Gmail message ID.

    Returns
    -------
    dict
        ``{"success": True, "id": "...", "threadId": "...", "from": "...",
        "to": "...", "subject": "...", "date": "...", "snippet": "...",
        "body_plain_text": "..."}`` on success, **or**
        ``{"error": "...", "detail": "..."}`` on failure.
    """
    # --- Fetch full message -----------------------------------------------
    result = get_message(message_id, format="full")
    if "error" in result:
        return result
    msg = result["message"]

    # --- Extract headers --------------------------------------------------
    headers = _extract_headers(msg.get("payload", {}))
    headers["id"] = msg.get("id", "")
    headers["threadId"] = msg.get("threadId", "")
    headers["snippet"] = msg.get("snippet", "")
    headers["labelIds"] = msg.get("labelIds", [])
    headers["internalDate"] = msg.get("internalDate", "")

    # --- Extract body -----------------------------------------------------
    body_result = _extract_body(msg.get("payload", {}))
    headers["body_plain_text"] = body_result.get(
        "body_plain_text", "(no plain text body available)"
    )
    headers["success"] = True

    return headers


# ---------------------------------------------------------------------------
# Public: get_message_attachment
# ---------------------------------------------------------------------------


def get_message_attachment(
    message_id: str,
    attachment_id: str,
) -> dict[str, Any]:
    """Fetch an attachment's raw data from a message.

    **Note:** To get the ``filename`` and ``mimeType`` of an attachment,
    call ``get_message(message_id)`` first and inspect the message payload
    parts.  Only the ``attachment_id`` and ``size`` are required here; the
    caller must supply them from a previous ``get_message`` call.

    Parameters
    ----------
    message_id : str
        The Gmail message ID that contains the attachment.
    attachment_id : str
        The attachment part ID (found in the message payload parts).

    Returns
    -------
    dict
        ``{"success": True, "data": "<base64-encoded>", "size": N,
        "attachment_id": "...", "message_id": "..."}`` on success, **or**
        ``{"error": "...", "detail": "..."}`` on failure.
    """
    # --- Validate inputs --------------------------------------------------
    if not message_id:
        return {"error": "message_id is required"}
    if not attachment_id:
        return {"error": "attachment_id is required"}

    # --- Authenticate -----------------------------------------------------
    auth_result = _get_gmail_service()
    if "error" in auth_result:
        return auth_result
    service = auth_result["service"]

    # --- Execute ----------------------------------------------------------
    try:
        attachment = (
            service.users()
            .messages()
            .attachments()
            .get(userId="me", messageId=message_id, id=attachment_id)
            .execute()
        )
        return {
            "success": True,
            "message_id": message_id,
            "attachment_id": attachment_id,
            "data": attachment.get("data", ""),
            "size": attachment.get("size", 0),
        }
    except HttpError as exc:
        status = exc.resp.status if exc.resp is not None else 0
        reason = _safe_reason(exc)
        if status == 404:
            return {
                "error": f"Attachment not found: {attachment_id} in message {message_id}.",
            }
        return {"error": f"Gmail API error ({status}): {reason}"}
    except Exception as exc:
        return {"error": "Failed to fetch attachment.", "detail": str(exc)}


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

_HEADER_NAMES = {
    "From": "from",
    "To": "to",
    "Subject": "subject",
    "Date": "date",
    "Message-ID": "message_id_header",
    "References": "references",
    "In-Reply-To": "in_reply_to",
}


def _extract_headers(payload: dict) -> dict[str, Any]:
    """Extract known headers from the payload's ``headers`` list."""
    result: dict[str, Any] = {}
    headers_raw = payload.get("headers") or []
    for h in headers_raw:
        name = h.get("name", "")
        value = h.get("value", "")
        key = _HEADER_NAMES.get(name)
        if key:
            result[key] = value
    return result


def _safe_reason(exc: HttpError) -> str:
    """Safely extract the error reason from an HttpError."""
    try:
        return exc._get_reason() or str(exc)
    except Exception:
        return str(exc)
