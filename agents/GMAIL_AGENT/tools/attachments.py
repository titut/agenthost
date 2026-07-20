"""
Download and manage Gmail attachments.

Public functions (all return plain dicts):

    list_attachments(message_id)
        List all attachments in a Gmail message.

    download_all_attachments(message_id, output_dir)
        Download every attachment from a message to disk.
"""

from __future__ import annotations

import base64
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

DEFAULT_OUTPUT_DIR = Path.home() / ".agenthost" / "gmail_attachments"


# ---------------------------------------------------------------------------
# Public: list_attachments
# ---------------------------------------------------------------------------


def list_attachments(message_id: str) -> dict[str, Any]:
    """List all attachments in a Gmail message.

    Parameters
    ----------
    message_id : str
        The Gmail message ID.

    Returns
    -------
    dict
        ``{"success": True, "message_id": "...", "attachments": [...]}`` on
        success, **or** ``{"error": "..."}`` on failure.

        Each attachment dict contains ``attachment_id``, ``filename``,
        ``mime_type``, and ``size``.
    """
    if not message_id:
        return {"error": "message_id is required"}

    auth_result = _get_gmail_service()
    if "error" in auth_result:
        return auth_result
    service = auth_result["service"]

    try:
        msg = (
            service.users()
            .messages()
            .get(userId="me", id=message_id, format="full")
            .execute()
        )
    except HttpError as exc:
        return _http_error(exc)
    except Exception as exc:
        return {"error": "Failed to fetch message.", "detail": str(exc)}

    payload = msg.get("payload", {})
    parts = _find_attachment_parts(payload)

    attachments: list[dict[str, Any]] = []
    for part in parts:
        body = part.get("body", {})
        attachments.append(
            {
                "attachment_id": body.get("attachmentId", ""),
                "filename": part.get("filename", ""),
                "mime_type": part.get("mimeType", ""),
                "size": body.get("size", 0),
            }
        )

    return {
        "success": True,
        "message_id": message_id,
        "attachments": attachments,
    }


# ---------------------------------------------------------------------------
# Public: download_all_attachments
# ---------------------------------------------------------------------------


def download_all_attachments(
    message_id: str,
    output_dir: Optional[str] = None,
) -> dict[str, Any]:
    """Download every attachment from a Gmail message.

    Parameters
    ----------
    message_id : str
        The Gmail message ID.
    output_dir : str, optional
        Directory where the files should be saved. Defaults to
        ``~/.agenthost/gmail_attachments/``.

    Returns
    -------
    dict
        ``{"success": True, "message_id": "...", "output_dir": "...",
        "saved": [...], "failed": [...]}`` on success, **or**
        ``{"error": "..."}`` on failure.
    """
    if not message_id:
        return {"error": "message_id is required"}

    listed = list_attachments(message_id)
    if "error" in listed:
        return listed

    attachments = listed.get("attachments", [])
    if not attachments:
        return {
            "success": True,
            "message_id": message_id,
            "output_dir": str(_resolve_output_dir(output_dir)),
            "saved": [],
            "failed": [],
            "message": "No attachments found in message.",
        }

    output_path = _resolve_output_dir(output_dir)
    output_path.mkdir(parents=True, exist_ok=True)

    saved: list[dict[str, Any]] = []
    failed: list[dict[str, Any]] = []
    for attachment in attachments:
        attachment_id = attachment.get("attachment_id", "")
        filename = attachment.get("filename") or f"{message_id}_{attachment_id}"

        result = _download_attachment(
            message_id=message_id,
            attachment_id=attachment_id,
            filename=filename,
            output_dir=str(output_path),
        )

        if result.get("success"):
            saved.append(result)
        else:
            failed.append(
                {
                    "attachment_id": attachment_id,
                    "filename": filename,
                    "error": result.get("error"),
                    "detail": result.get("detail"),
                }
            )

    return {
        "success": True,
        "message_id": message_id,
        "output_dir": str(output_path),
        "saved": saved,
        "failed": failed,
    }


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _download_attachment(
    message_id: str,
    attachment_id: str,
    filename: Optional[str] = None,
    output_dir: Optional[str] = None,
) -> dict[str, Any]:
    """Download a single Gmail attachment and save it to disk.

    Parameters
    ----------
    message_id : str
        The Gmail message ID that owns the attachment.
    attachment_id : str
        The attachment ID (from ``list_attachments`` or the message payload).
    filename : str, optional
        Name to use when saving the file. If omitted, a fallback name is
        built from the message and attachment IDs.
    output_dir : str, optional
        Directory where the file should be saved. Defaults to
        ``~/.agenthost/gmail_attachments/``.

    Returns
    -------
    dict
        ``{"success": True, "path": "...", "filename": "...", "size": N}``
        on success, **or** ``{"error": "..."}`` on failure.
    """
    if not message_id:
        return {"error": "message_id is required"}
    if not attachment_id:
        return {"error": "attachment_id is required"}

    auth_result = _get_gmail_service()
    if "error" in auth_result:
        return auth_result
    service = auth_result["service"]

    try:
        attachment = (
            service.users()
            .messages()
            .attachments()
            .get(userId="me", messageId=message_id, id=attachment_id)
            .execute()
        )
    except HttpError as exc:
        return _http_error(exc)
    except Exception as exc:
        return {"error": "Failed to download attachment.", "detail": str(exc)}

    data = attachment.get("data", "")
    if not data:
        return {"error": "Attachment contains no data."}

    try:
        decoded_bytes = _decode_attachment_data(data)
    except Exception as exc:
        return {"error": "Failed to decode attachment data.", "detail": str(exc)}

    if not filename:
        filename = f"{message_id}_{attachment_id}"

    output_path = _resolve_output_dir(output_dir) / filename
    output_path.parent.mkdir(parents=True, exist_ok=True)

    try:
        output_path.write_bytes(decoded_bytes)
    except Exception as exc:
        return {"error": "Failed to write attachment to disk.", "detail": str(exc)}

    return {
        "success": True,
        "message_id": message_id,
        "attachment_id": attachment_id,
        "filename": output_path.name,
        "path": str(output_path),
        "size": len(decoded_bytes),
    }


def _find_attachment_parts(payload: dict) -> list[dict]:
    """Recursively find all MIME parts that carry an ``attachmentId``."""
    attachments: list[dict] = []
    parts = payload.get("parts") or []
    body = payload.get("body", {})

    if body.get("attachmentId"):
        attachments.append(payload)

    for part in parts:
        attachments.extend(_find_attachment_parts(part))

    return attachments


def _decode_attachment_data(data: str) -> bytes:
    """Base64url-decode attachment data to raw bytes.

    Gmail uses the URL-safe alphabet and may omit trailing ``=`` padding.
    """
    if not data:
        return b""

    remainder = len(data) % 4
    if remainder:
        data += "=" * (4 - remainder)

    return base64.urlsafe_b64decode(data)


def _resolve_output_dir(output_dir: Optional[str] = None) -> Path:
    """Resolve output directory, defaulting to ~/.agenthost/gmail_attachments."""
    if output_dir:
        return Path(output_dir).expanduser().resolve()
    return DEFAULT_OUTPUT_DIR


def _http_error(exc: HttpError) -> dict[str, Any]:
    """Translate an HttpError into a standard error dict."""
    status = exc.resp.status if exc.resp is not None else 0
    reason = ""
    try:
        reason = exc._get_reason() or ""
    except Exception:
        reason = str(exc)

    if status == 400:
        return {"error": f"Invalid request: {reason}"}
    if status == 404:
        return {"error": "Message or attachment not found."}
    if status == 403:
        return {"error": f"Insufficient permissions: {reason}"}
    if status == 429:
        return {
            "error": "Rate limit exceeded. Retry after a few seconds.",
            "retry_after": 5,
        }
    return {"error": f"Gmail API error ({status}): {reason}"}
