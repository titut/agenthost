"""
Search and list Gmail messages.

Public functions (all return plain dicts):

    list_inbox_messages(max_results, label_ids, include_spam_trash, page_token)
        List messages in the inbox (or a filtered view of it).

    search_messages(query, max_results, include_spam_trash, page_token)
        List messages matching a Gmail search query.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path
from typing import Any, Optional

from googleapiclient.errors import HttpError

# Load sibling module by absolute path to avoid sys.path pollution.
# This works regardless of the calling directory.
_tools_dir = Path(__file__).resolve().parent
_spec = importlib.util.spec_from_file_location(
    "_gmail_base", str(_tools_dir / "_gmail_base.py")
)
_gmail_base = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_gmail_base)

_get_gmail_service = _gmail_base.get_gmail_service
_get_message_body_dict = _gmail_base.get_message_body_dict

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
_MAX_RESULTS_MIN = 1
_MAX_RESULTS_MAX = 500
_DEFAULT_MAX_RESULTS = 20


# ---------------------------------------------------------------------------
# Public: list_inbox_messages
# ---------------------------------------------------------------------------


def list_inbox_messages(
    max_results: int = _DEFAULT_MAX_RESULTS,
    label_ids: Optional[list[str]] = None,
    include_spam_trash: bool = False,
    page_token: Optional[str] = None,
) -> dict[str, Any]:
    """List messages in the inbox.

    Parameters
    ----------
    max_results : int, optional
        Maximum number of messages to return (1-500). Default 20.
    label_ids : list[str], optional
        Only return messages with these labels.  If ``None`` (default), the
        API returns messages from all labels the user has access to — to
        restrict to inbox-only, pass ``["INBOX"]``.
    include_spam_trash : bool, optional
        Include messages from Spam and Trash. Default ``False``.
    page_token : str, optional
        Token for fetching the next page of results.

    Returns
    -------
    dict
        ``{"success": True, "messages": [...], "result_size_estimate": N,
        "next_page_token": "..."}`` on success, **or**
        ``{"error": "...", "detail": "..."}`` on failure.
    """
    # --- Validate inputs --------------------------------------------------
    max_results = _clamp_max_results(max_results)

    # --- Authenticate -----------------------------------------------------
    auth_result = _get_gmail_service()
    if "error" in auth_result:
        return auth_result
    service = auth_result["service"]

    # --- Build request params ---------------------------------------------
    params: dict[str, Any] = {
        "userId": "me",
        "maxResults": max_results,
        "includeSpamTrash": include_spam_trash,
    }
    if label_ids:
        params["labelIds"] = label_ids
    if page_token:
        params["pageToken"] = page_token

    # --- Execute ----------------------------------------------------------
    try:
        response = service.users().messages().list(**params).execute()
    except HttpError as exc:
        return _http_error(exc)
    except Exception as exc:
        return {"error": "Failed to list inbox messages.", "detail": str(exc)}

    return _build_list_response(response)


# ---------------------------------------------------------------------------
# Public: search_messages
# ---------------------------------------------------------------------------


def search_messages(
    query: str,
    max_results: int = _DEFAULT_MAX_RESULTS,
    include_spam_trash: bool = False,
    page_token: Optional[str] = None,
) -> dict[str, Any]:
    """Search messages using Gmail query syntax.

    Parameters
    ----------
    query : str
        Gmail search query (e.g. ``"from:alice@example.com is:unread"``).
        See ``skills/gmail-search.md`` for the full syntax reference.
        To search for emails within a specific time in Gmail, use the after:, before:, older_than:, or newer_than: operators in the search bar. You can specify exact dates (YYYY/MM/DD format) or use Unix timestamps for second-level precision.Date and Time OperatorsUse these standard commands directly in the Gmail search box:Specific Date Ranges: Type after:2026/01/01 before:2026/02/01 to find emails between January 1 and February 1, 2026.Time Relatives: Use older_than:7d or newer_than:30d for rolling windows using d (days), m (months), and y (years).Exact Time: For exact timestamps (e.g., to narrow down a 15-minute window), convert your dates to Unix Epoch time and query using after:TIMESTAMP before:TIMESTAMP.
    max_results : int, optional
        Maximum number of messages to return (1-500). Default 20.
    include_spam_trash : bool, optional
        Include messages from Spam and Trash. Default ``False``.
    page_token : str, optional
        Token for fetching the next page of results.

    Returns
    -------
    dict
        ``{"success": True, "query": "...", "messages": [...],
        "result_size_estimate": N, "next_page_token": "..."}`` on success,
        **or** ``{"error": "...", "detail": "..."}`` on failure.
    """
    # --- Validate inputs --------------------------------------------------
    if not query or not query.strip():
        return {
            "error": "Query cannot be empty. Use list_inbox_messages() to list inbox.",
        }
    max_results = _clamp_max_results(max_results)

    # --- Authenticate -----------------------------------------------------
    auth_result = _get_gmail_service()
    if "error" in auth_result:
        return auth_result
    service = auth_result["service"]

    # --- Build request params ---------------------------------------------
    params: dict[str, Any] = {
        "userId": "me",
        "maxResults": max_results,
        "q": query.strip(),
        "includeSpamTrash": include_spam_trash,
    }
    if page_token:
        params["pageToken"] = page_token

    # --- Execute ----------------------------------------------------------
    try:
        response = service.users().messages().list(**params).execute()
    except HttpError as exc:
        return _http_error(exc)
    except Exception as exc:
        return {"error": "Failed to search messages.", "detail": str(exc)}

    result = _build_list_response(response)
    result["query"] = query.strip()
    return result


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def search_and_read_messages(
    query: str,
    max_results: int = _DEFAULT_MAX_RESULTS,
    include_spam_trash: bool = False,
    page_token: Optional[str] = None,
) -> dict[str, Any]:
    """Search Gmail and return the full message content for each match.

    This is a convenience combination of ``search_messages`` followed by
    ``get_message_body`` for every result, using a single service connection.

    Parameters
    ----------
    query : str
        Gmail search query (e.g. ``"from:alice@example.com is:unread"``).
    max_results : int, optional
        Maximum number of messages to return (1-500). Default 20.
    include_spam_trash : bool, optional
        Include messages from Spam and Trash. Default ``False``.
    page_token : str, optional
        Token for fetching the next page of results.

    Returns
    -------
    dict
        ``{"success": True, "query": "...", "messages": [...],
        "result_size_estimate": N, "next_page_token": "..."}`` on success,
        **or** ``{"error": "...", "detail": "..."}`` on failure.
    """
    if not query or not query.strip():
        return {
            "error": "Query cannot be empty. Use list_inbox_and_read() to list inbox.",
        }
    max_results = _clamp_max_results(max_results)

    auth_result = _get_gmail_service()
    if "error" in auth_result:
        return auth_result
    service = auth_result["service"]

    params: dict[str, Any] = {
        "userId": "me",
        "maxResults": max_results,
        "q": query.strip(),
        "includeSpamTrash": include_spam_trash,
    }
    if page_token:
        params["pageToken"] = page_token

    try:
        response = service.users().messages().list(**params).execute()
    except HttpError as exc:
        return _http_error(exc)
    except Exception as exc:
        return {"error": "Failed to search messages.", "detail": str(exc)}

    messages: list[dict[str, Any]] = []
    raw_messages = response.get("messages") or []
    for msg in raw_messages:
        message_id = msg.get("id", "")
        if not message_id:
            continue
        body_result = _get_message_body_dict(service, message_id)
        if "error" in body_result:
            messages.append({
                "id": message_id,
                "threadId": msg.get("threadId", ""),
                "error": body_result["error"],
            })
        else:
            messages.append(body_result)

    result: dict[str, Any] = {
        "success": True,
        "query": query.strip(),
        "messages": messages,
        "result_size_estimate": response.get("resultSizeEstimate", 0),
    }
    next_token = response.get("nextPageToken")
    if next_token:
        result["next_page_token"] = next_token
    return result


def list_inbox_and_read(
    max_results: int = _DEFAULT_MAX_RESULTS,
    label_ids: Optional[list[str]] = None,
    include_spam_trash: bool = False,
    page_token: Optional[str] = None,
) -> dict[str, Any]:
    """List inbox messages and return the full message content for each one.

    This is the read counterpart of ``list_inbox_messages``.

    Parameters
    ----------
    max_results : int, optional
        Maximum number of messages to return (1-500). Default 20.
    label_ids : list[str], optional
        Only return messages with these labels. Pass ``["INBOX"]`` for inbox only.
    include_spam_trash : bool, optional
        Include messages from Spam and Trash. Default ``False``.
    page_token : str, optional
        Token for fetching the next page of results.

    Returns
    -------
    dict
        ``{"success": True, "messages": [...], "result_size_estimate": N,
        "next_page_token": "..."}`` on success, **or**
        ``{"error": "...", "detail": "..."}`` on failure.
    """
    max_results = _clamp_max_results(max_results)

    auth_result = _get_gmail_service()
    if "error" in auth_result:
        return auth_result
    service = auth_result["service"]

    params: dict[str, Any] = {
        "userId": "me",
        "maxResults": max_results,
        "includeSpamTrash": include_spam_trash,
    }
    if label_ids:
        params["labelIds"] = label_ids
    if page_token:
        params["pageToken"] = page_token

    try:
        response = service.users().messages().list(**params).execute()
    except HttpError as exc:
        return _http_error(exc)
    except Exception as exc:
        return {"error": "Failed to list inbox messages.", "detail": str(exc)}

    messages: list[dict[str, Any]] = []
    raw_messages = response.get("messages") or []
    for msg in raw_messages:
        message_id = msg.get("id", "")
        if not message_id:
            continue
        body_result = _get_message_body_dict(service, message_id)
        if "error" in body_result:
            messages.append({
                "id": message_id,
                "threadId": msg.get("threadId", ""),
                "error": body_result["error"],
            })
        else:
            messages.append(body_result)

    result: dict[str, Any] = {
        "success": True,
        "messages": messages,
        "result_size_estimate": response.get("resultSizeEstimate", 0),
    }
    next_token = response.get("nextPageToken")
    if next_token:
        result["next_page_token"] = next_token
    return result


def _clamp_max_results(max_results: int) -> int:
    """Clamp *max_results* to the valid range [1, 500]."""
    if not isinstance(max_results, int) or max_results < _MAX_RESULTS_MIN:
        return _DEFAULT_MAX_RESULTS
    if max_results > _MAX_RESULTS_MAX:
        return _MAX_RESULTS_MAX
    return max_results


def _build_list_response(response: dict) -> dict[str, Any]:
    """Normalise the Gmail API list response into our common shape.

    Handles the case of an empty inbox/result set gracefully.
    """
    messages = response.get("messages") or []
    # Normalise each message to include only id + threadId
    normalised = []
    for msg in messages:
        normalised.append(
            {
                "id": msg.get("id", ""),
                "threadId": msg.get("threadId", ""),
                "snippet": msg.get("snippet", ""),
            }
        )

    result: dict[str, Any] = {
        "success": True,
        "messages": normalised,
        "result_size_estimate": response.get("resultSizeEstimate", 0),
    }
    next_token = response.get("nextPageToken")
    if next_token:
        result["next_page_token"] = next_token

    return result


def _http_error(exc: HttpError) -> dict[str, Any]:
    """Translate an HttpError into a standard error dict."""
    status = exc.resp.status if exc.resp is not None else 0
    reason = ""
    try:
        reason = exc._get_reason() or ""
    except Exception:
        reason = str(exc)

    if status == 400 and "invalid" in reason.lower():
        return {
            "error": f"Invalid Gmail query: {reason}",
        }
    if status == 429:
        return {
            "error": f"Rate limit exceeded. Retry after a few seconds.",
            "retry_after": 5,
        }
    return {
        "error": f"Gmail API error ({status}): {reason}",
    }
