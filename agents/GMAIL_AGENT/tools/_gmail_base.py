"""
Shared base module for GMAIL_AGENT tools.

Provides the following public functions (all return plain dicts):

    get_gmail_service()
        Authenticate, build, and cache a Gmail API v1 service resource.

    build_mime_message(to, subject, body_text, cc=, bcc=)
        Construct a base64url-encoded MIME message (text-only).

    extract_body(payload)
        Extract the plain-text body from a Gmail message payload dict.

And internal helpers (prefixed with ``_``) used by tool modules:

    _decode_body(data: str) -> str
        Base64url-decode with automatic padding correction.

    _get_snippet(service, message_id) -> dict
        Fetch a short snippet for confirmation flows (1 quota unit).
"""

from __future__ import annotations

import base64
import os
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from typing import Optional

import google.auth.exceptions
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import Resource, build
from googleapiclient.errors import HttpError

# ---------------------------------------------------------------------------
# Module-level cache for the Gmail API service
# ---------------------------------------------------------------------------
_service: Optional[Resource] = None

# Required environment-variable names
_REQUIRED_ENV_VARS = ("GMAIL_CLIENT_ID", "GMAIL_CLIENT_SECRET", "GMAIL_REFRESH_TOKEN")

# ---------------------------------------------------------------------------
# Public: get_gmail_service
# ---------------------------------------------------------------------------


def _read_gmail_credentials() -> dict:
    """Read and validate Gmail OAuth credentials from the environment.

    Returns {"client_id": ..., "client_secret": ..., "refresh_token": ...}
    on success, or {"error": ..., "missing_vars": [...]} on failure.
    """
    client_id = os.environ.get("GMAIL_CLIENT_ID", "").strip()
    client_secret = os.environ.get("GMAIL_CLIENT_SECRET", "").strip()
    refresh_token = os.environ.get("GMAIL_REFRESH_TOKEN", "").strip()

    missing = []
    if not client_id:
        missing.append("GMAIL_CLIENT_ID")
    if not client_secret:
        missing.append("GMAIL_CLIENT_SECRET")
    if not refresh_token:
        missing.append("GMAIL_REFRESH_TOKEN")

    if missing:
        return {
            "error": f"Missing required env var(s): {', '.join(missing)}",
            "missing_vars": missing,
        }

    return {
        "client_id": client_id,
        "client_secret": client_secret,
        "refresh_token": refresh_token,
    }


def build_gmail_service() -> dict:
    """Build a fresh Gmail API v1 service.

    Unlike ``get_gmail_service()``, this always creates a new ``Resource``
    instance. Use this in worker threads where the shared cached service would
    not be thread-safe.

    Returns:
        ``{"success": True, "service": <Resource>}`` on success, **or**
        ``{"error": "<message>", "missing_vars": [...], "detail": "..."}`` on
        failure.
    """
    creds_result = _read_gmail_credentials()
    if "error" in creds_result:
        return creds_result

    client_id = creds_result["client_id"]
    client_secret = creds_result["client_secret"]
    refresh_token = creds_result["refresh_token"]

    # ---- Build Credentials object ----------------------------------------
    try:
        creds = Credentials(
            token=None,
            refresh_token=refresh_token,
            token_uri="https://oauth2.googleapis.com/token",
            client_id=client_id,
            client_secret=client_secret,
            scopes=["https://www.googleapis.com/auth/gmail.modify"],
        )
    except Exception as exc:
        return {
            "error": "Failed to create OAuth credentials object.",
            "detail": str(exc),
        }

    # ---- Perform initial token refresh -----------------------------------
    try:
        creds.refresh(Request())
    except google.auth.exceptions.RefreshError:
        return {
            "error": (
                "Gmail auth failed: refresh token expired or revoked. "
                "Re-authenticate at "
                "https://developers.google.com/gmail/api/quickstart/python"
            ),
        }
    except Exception as exc:
        return {
            "error": "Failed to refresh Gmail credentials.",
            "detail": str(exc),
        }

    # ---- Build the API service resource ----------------------------------
    try:
        service = build("gmail", "v1", credentials=creds)
        return {"success": True, "service": service}
    except Exception as exc:
        return {
            "error": "Failed to initialize Gmail service.",
            "detail": str(exc),
        }


def get_gmail_service() -> dict:
    """Build and return a cached Gmail API v1 service.

    Reads ``GMAIL_CLIENT_ID``, ``GMAIL_CLIENT_SECRET``, and
    ``GMAIL_REFRESH_TOKEN`` from the process environment, constructs OAuth2
    credentials, and returns a ``Resource`` via
    ``googleapiclient.discovery.build("gmail", "v1", ...)``.

    The service is cached in a module-level variable so subsequent calls in
    the same session are nearly free.

    Returns (per §8.2 of the architecture spec):
        ``{"success": True, "service": <Resource>}`` on success, **or**
        ``{"error": "<message>", "missing_vars": [...], "detail": "..."}`` on
        failure.
    """
    global _service

    # ---- Return cached service if available -------------------------------
    if _service is not None:
        return {"success": True, "service": _service}

    result = build_gmail_service()
    if "service" in result:
        _service = result["service"]
    return result


# ---------------------------------------------------------------------------
# Public: build_mime_message
# ---------------------------------------------------------------------------


def build_mime_message(
    to: str,
    subject: str,
    body_text: str,
    cc: Optional[str] = None,
    bcc: Optional[str] = None,
) -> dict:
    """Construct a base64url-encoded MIME message ready for ``messages.send``.

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
        ``{"raw": "<base64url-encoded-string>"}`` on success, **or**
        ``{"error": "...", "detail": "..."}`` on failure.
    """
    # ---- Validate required parameters ------------------------------------
    missing_params = []
    if not to:
        missing_params.append("to")
    if not subject:
        missing_params.append("subject")
    if not body_text:
        missing_params.append("body_text")
    if missing_params:
        return {
            "error": f"Missing required parameter(s): {', '.join(missing_params)}",
        }

    # ---- Build MIME message ----------------------------------------------
    try:
        msg = MIMEText(body_text, "plain", "utf-8")
        msg["To"] = to
        msg["Subject"] = subject
        if cc:
            msg["Cc"] = cc
        if bcc:
            msg["Bcc"] = bcc

        raw = base64.urlsafe_b64encode(msg.as_bytes()).rstrip(b"=").decode("utf-8")
        return {"raw": raw}
    except Exception as exc:
        return {
            "error": "Failed to build MIME message.",
            "detail": str(exc),
        }


# ---------------------------------------------------------------------------
# Public: extract_body
# ---------------------------------------------------------------------------


def extract_body(payload: dict) -> dict:
    """Extract the plain-text body string from a Gmail message ``payload``.

    Handles:
    - Single-part messages (``payload.body.data``).
    - Multipart messages (recursively searches parts for ``text/plain``).
    - Falls back to ``text/html`` decoded as text if no plain part exists.
    - Base64url decoding with automatic padding correction.

    Returns
    -------
    dict
        ``{"body_plain_text": "..."}`` on success, **or**
        ``{"body_plain_text": "(no plain text body available)"}`` when no
        body data can be extracted, **or**
        ``{"error": "...", "detail": "..."}`` on unexpected failure.
    """
    try:
        text = _extract_body_recursive(payload)
        return {"body_plain_text": text}
    except Exception as exc:
        return {
            "error": "Failed to extract message body.",
            "detail": str(exc),
        }


# ---------------------------------------------------------------------------
# Internal: _decode_body
# ---------------------------------------------------------------------------


def _decode_body(data: str) -> str:
    """Base64url-decode *data* with automatic padding correction.

    Gmail uses the URL-safe alphabet (RFC 4648 §5) and frequently omits
    trailing ``=`` padding characters.  This function restores padding,
    decodes, and returns the UTF-8 string, substituting replacement
    characters for undecodable bytes.
    """
    if not data:
        return ""
    # Restore padding — base64 length must be a multiple of 4
    remainder = len(data) % 4
    if remainder:
        data += "=" * (4 - remainder)
    try:
        decoded = base64.urlsafe_b64decode(data)
        return decoded.decode("utf-8", errors="replace")
    except Exception:
        return "(unable to decode body data)"


# ---------------------------------------------------------------------------
# Internal: _extract_body_recursive
# ---------------------------------------------------------------------------


def _extract_body_recursive(payload: dict) -> str:
    """Walk MIME parts recursively to find the first ``text/plain`` body."""
    mime_type = payload.get("mimeType", "")
    parts = payload.get("parts") or []
    body_data = payload.get("body", {}).get("data", "")

    # --- Leaf node: text/plain ---
    if mime_type == "text/plain" and body_data:
        return _decode_body(body_data)

    # --- Multipart — recurse into children ---
    if parts:
        for part in parts:
            result = _extract_body_recursive(part)
            if result and result != "(no plain text body available)":
                return result

    # --- Fallback: text/html leaf ---
    if mime_type == "text/html" and body_data:
        return _decode_body(body_data)

    # --- Single-part message with direct body data (no mimeType match) ---
    if body_data and not parts:
        return _decode_body(body_data)

    return "(no plain text body available)"


# ---------------------------------------------------------------------------
# Internal: _get_snippet
# ---------------------------------------------------------------------------


def _get_snippet(service: Resource, message_id: str) -> dict:
    """Fetch a short snippet of a message for confirmation flows.

    Uses ``users.messages.get`` with ``format=metadata`` (1 quota unit) so
    it is cheap to call repeatedly.

    Returns
    -------
    dict
        ``{"snippet": "..."}`` on success, **or**
        ``{"error": "...", "detail": "..."}`` on failure.
    """
    if not message_id:
        return {"error": "message_id is required"}

    try:
        msg = (
            service.users()
            .messages()
            .get(userId="me", id=message_id, format="metadata")
            .execute()
        )
        return {"snippet": msg.get("snippet", "(no snippet)")}
    except HttpError as exc:
        status = exc.resp.status if exc.resp is not None else 0
        if status == 404:
            return {
                "error": f"Message not found: {message_id}. It may have been deleted.",
            }
        return {
            "error": f"Gmail API error ({status}): {exc._get_reason() or str(exc)}",
        }
    except Exception as exc:
        return {"error": f"Failed to fetch message snippet: {exc}"}


_HEADER_NAME_MAP = {
    "From": "from",
    "To": "to",
    "Subject": "subject",
    "Date": "date",
    "Message-ID": "message_id_header",
    "References": "references",
    "In-Reply-To": "in_reply_to",
}


def _extract_headers(payload: dict) -> dict[str, str]:
    """Extract known headers from the payload's ``headers`` list."""
    result: dict[str, str] = {}
    for h in payload.get("headers") or []:
        key = _HEADER_NAME_MAP.get(h.get("name", ""))
        if key:
            result[key] = h.get("value", "")
    return result


def get_message_body_dict(service: Resource, message_id: str) -> dict:
    """Fetch a message and return a normalized dict of headers + plain-text body.

    Returns
    -------
    dict
        ``{"success": True, "id": ..., "threadId": ..., "from": ..., ...}``
        on success, **or** ``{"error": "..."}`` on failure.
    """
    if not message_id:
        return {"error": "message_id is required"}

    try:
        msg = (
            service.users()
            .messages()
            .get(userId="me", id=message_id, format="full")
            .execute()
        )
    except HttpError as exc:
        status = exc.resp.status if exc.resp is not None else 0
        reason = ""
        try:
            reason = exc._get_reason() or ""
        except Exception:
            reason = str(exc)
        if status == 404:
            return {
                "error": f"Message not found: {message_id}. It may have been deleted.",
            }
        return {"error": f"Gmail API error ({status}): {reason}"}
    except Exception as exc:
        return {"error": "Failed to fetch message.", "detail": str(exc)}

    payload = msg.get("payload", {})
    headers = _extract_headers(payload)
    body_result = extract_body(payload)

    result = {
        "success": True,
        "id": msg.get("id", ""),
        "threadId": msg.get("threadId", ""),
        "snippet": msg.get("snippet", ""),
        "labelIds": msg.get("labelIds", []),
        "internalDate": msg.get("internalDate", ""),
        "body_plain_text": body_result.get("body_plain_text", "(no plain text body available)"),
    }
    result.update(headers)
    return result
