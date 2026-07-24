"""
Send messages and manage drafts.

Public functions (all return plain dicts):

    send_message(to, subject, body_text, cc, bcc)
        Compose and send a plain-text email.

    create_draft(to, subject, body_text, cc, bcc)
        Save a message as a draft without sending.

    send_draft(draft_id)
        Send a previously saved draft.
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
_build_mime_message = _gmail_base.build_mime_message

# ---------------------------------------------------------------------------
# Public: send_message
# ---------------------------------------------------------------------------

# NOTE: messages.send costs ~100 quota units — use sparingly.


def send_message(
    to: str,
    subject: str,
    body_text: str,
    cc: Optional[str] = None,
    bcc: Optional[str] = None,
) -> dict[str, Any]:
    """Compose and send a plain-text email.

    .. warning:: This operation costs **~100 quota units** — about 20× more
        than a ``messages.get``.  Avoid re-sending unless necessary.

    Parameters
    ----------
    to : str
        Recipient email address(es), comma-separated for multiple.
    subject : str
        Subject line.
    body_text : str
        Plain-text body content.
    cc : str, optional
        CC recipients, comma-separated.
    bcc : str, optional
        BCC recipients, comma-separated.

    Returns
    -------
    dict
        ``{"success": True, "message_id": "...", "thread_id": "...",
        "label_ids": [...]}`` on success, **or**
        ``{"error": "...", "detail": "..."}`` on failure.
    """
    # --- Validate required params -----------------------------------------
    missing = _check_required(to, subject, body_text)
    if missing:
        return {"error": f"Missing required parameter(s): {', '.join(missing)}"}

    # --- Build MIME message -----------------------------------------------
    mime_result = _build_mime_message(
        to=to, subject=subject, body_text=body_text, cc=cc, bcc=bcc
    )
    if "error" in mime_result:
        return mime_result

    # --- Authenticate -----------------------------------------------------
    auth_result = _get_gmail_service()
    if "error" in auth_result:
        return auth_result
    service = auth_result["service"]

    # --- Fetch sender email (for From header) -----------------------------
    try:
        profile = service.users().getProfile(userId="me").execute()
        sender_email = profile.get("emailAddress", "")
    except Exception as exc:
        return {"error": "Failed to fetch user profile for sender address.", "detail": str(exc)}

    # Rebuild MIME message with From header set to the authenticated user
    try:
        from email.mime.text import MIMEText
        import base64

        msg = MIMEText(body_text, "plain", "utf-8")
        msg["From"] = sender_email
        msg["To"] = to
        msg["Subject"] = subject
        if cc:
            msg["Cc"] = cc
        if bcc:
            msg["Bcc"] = bcc

        raw = base64.urlsafe_b64encode(msg.as_bytes()).rstrip(b"=").decode("utf-8")
        body = {"raw": raw}
    except Exception as exc:
        return {"error": "Failed to build MIME message with From header.", "detail": str(exc)}

    # --- Send -------------------------------------------------------------
    try:
        sent = service.users().messages().send(userId="me", body=body).execute()
        return {
            "success": True,
            "message_id": sent.get("id", ""),
            "thread_id": sent.get("threadId", ""),
            "label_ids": sent.get("labelIds", []),
        }
    except HttpError as exc:
        return _http_send_error(exc)
    except Exception as exc:
        return {"error": "Failed to send message.", "detail": str(exc)}


# ---------------------------------------------------------------------------
# Public: create_draft
# ---------------------------------------------------------------------------


def create_draft(
    to: str,
    subject: str,
    body_text: str,
    cc: Optional[str] = None,
    bcc: Optional[str] = None,
) -> dict[str, Any]:
    """Save a message as a draft without sending.

    Parameters
    ----------
    to : str
        Recipient email address(es), comma-separated for multiple.
    subject : str
        Subject line.
    body_text : str
        Plain-text body content.
    cc : str, optional
        CC recipients, comma-separated.
    bcc : str, optional
        BCC recipients, comma-separated.

    Returns
    -------
    dict
        ``{"success": True, "draft_id": "...", "message_id": "..."}`` on
        success, **or** ``{"error": "...", "detail": "..."}`` on failure.
    """
    # --- Validate required params -----------------------------------------
    missing = _check_required(to, subject, body_text)
    if missing:
        return {"error": f"Missing required parameter(s): {', '.join(missing)}"}

    # --- Build MIME message (no From header needed for draft) -------------
    mime_result = _build_mime_message(
        to=to, subject=subject, body_text=body_text, cc=cc, bcc=bcc
    )
    if "error" in mime_result:
        return mime_result
    body = {"message": mime_result}

    # --- Authenticate -----------------------------------------------------
    auth_result = _get_gmail_service()
    if "error" in auth_result:
        return auth_result
    service = auth_result["service"]

    # --- Create draft -----------------------------------------------------
    try:
        draft = service.users().drafts().create(userId="me", body=body).execute()
        return {
            "success": True,
            "draft_id": draft.get("id", ""),
            "message_id": draft.get("message", {}).get("id", ""),
        }
    except HttpError as exc:
        return _http_send_error(exc)
    except Exception as exc:
        return {"error": "Failed to create draft.", "detail": str(exc)}


# ---------------------------------------------------------------------------
# Public: send_draft
# ---------------------------------------------------------------------------


def send_draft(draft_id: str) -> dict[str, Any]:
    """Send a previously saved draft.

    Parameters
    ----------
    draft_id : str
        The draft ID (from ``create_draft`` or ``list_drafts``).

    Returns
    -------
    dict
        ``{"success": True, "message_id": "...", "thread_id": "..."}`` on
        success, **or** ``{"error": "...", "detail": "..."}`` on failure.
    """
    # --- Validate params --------------------------------------------------
    if not draft_id or not draft_id.strip():
        return {"error": "draft_id is required"}

    # --- Authenticate -----------------------------------------------------
    auth_result = _get_gmail_service()
    if "error" in auth_result:
        return auth_result
    service = auth_result["service"]

    # --- Send draft -------------------------------------------------------
    try:
        sent = (
            service.users()
            .drafts()
            .send(userId="me", body={"id": draft_id.strip()})
            .execute()
        )
        return {
            "success": True,
            "message_id": sent.get("id", ""),
            "thread_id": sent.get("threadId", ""),
        }
    except HttpError as exc:
        status = exc.resp.status if exc.resp is not None else 0
        reason = _safe_reason(exc)
        if status == 404:
            return {
                "error": f"Draft not found: {draft_id}. It may have already been sent or deleted.",
            }
        if status == 400:
            return {
                "error": f"Draft {draft_id} has already been sent or no longer exists.",
            }
        return {"error": f"Gmail API error ({status}): {reason}"}
    except Exception as exc:
        return {"error": "Failed to send draft.", "detail": str(exc)}


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _check_required(to: str, subject: str, body_text: str) -> list[str]:
    """Return a list of missing required parameter names."""
    missing = []
    if not to:
        missing.append("to")
    if not subject:
        missing.append("subject")
    if not body_text:
        missing.append("body_text")
    return missing


def _safe_reason(exc: HttpError) -> str:
    try:
        return exc._get_reason() or str(exc)
    except Exception:
        return str(exc)


def _http_send_error(exc: HttpError) -> dict[str, Any]:
    """Translate send/draft HttpError into standard error dict."""
    status = exc.resp.status if exc.resp is not None else 0
    reason = _safe_reason(exc)

    if status == 400:
        return {
            "error": f"Invalid request: {reason}",
        }
    if status == 403:
        return {
            "error": f"Insufficient permissions: {reason}.  Check that the OAuth scope includes gmail.modify.",
        }
    if status == 429:
        return {
            "error": "Rate limit exceeded. Retry after a few seconds.",
            "retry_after": 5,
        }
    return {"error": f"Gmail API error ({status}): {reason}"}
