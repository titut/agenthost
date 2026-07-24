# Description

Gmail search query syntax and best practices.

# Gmail Search Syntax (gmail-search)

## Overview

Gmail search uses the same query syntax as the Gmail web interface. The `search_messages()` and `list_inbox_messages()` tools pass the query string directly to the Gmail API's `q` parameter.

## Basic Operators

| Operator | Example | Description |
|----------|---------|-------------|
| `from:` | `from:alice@example.com` | Messages from a specific sender |
| `to:` | `to:bob@example.com` | Messages to a specific recipient |
| `subject:` | `subject:meeting` | Messages with "meeting" in the subject |
| `after:` | `after:2025/01/01` | Messages after a date (YYYY/MM/DD) |
| `before:` | `before:2025/03/01` | Messages before a date (YYYY/MM/DD) |
| `newer_than:` | `newer_than:1d` | Messages newer than a relative time (e.g. `1d` = 1d) can only use d,m,y. And they must be integers |
| `has:` | `has:attachment` | Messages with attachments |
| `is:` | `is:unread`, `is:read`, `is:starred`, `is:important` | Messages by state |
| `label:` | `label:inbox`, `label:my-label` | Messages with a specific label |
| `in:` | `in:inbox`, `in:spam`, `in:trash`, `in:drafts`, `in:sent` | Messages in a specific folder |
| `-` (NOT) | `-from:newsletter@example.com` | Exclude matching messages |
| `OR` | `from:alice OR from:bob` | Match either condition |
| `{ }` (OR group) | `{from:alice from:bob}` | Alternative OR syntax |

## Combining Operators

By default, multiple terms are joined with **AND**:

```
from:alice@example.com is:unread has:attachment
```

This finds unread messages from Alice that have attachments.

## Exact Phrase Matching

Use double quotes for exact phrase matching:

```
subject:"project update"
body:"meeting at 3pm"
```

## Common Search Patterns

| Intent | Query |
|--------|-------|
| Unread from a specific person | `from:alice@example.com is:unread` |
| Recent messages with attachments | `after:2025/01/01 has:attachment` |
| Messages from the last 12 hours | `newer_than:0.5d` |
| Everything except newsletters | `-from:newsletter@example.com in:inbox` |
| Messages about a topic | `subject:"quarterly review" after:2025/02/01` |
| All drafts | `in:drafts` |
| Messages in a date range | `after:2025/01/01 before:2025/03/01` |

## Limitations

- Gmail search only covers the last ~1M messages unless using a Google Workspace account with full mail search enabled
- Search is case-insensitive
- The `body:` operator searches the full message body but may be slower
- Pagination: use `next_page_token` from the response to fetch additional results beyond the first page

## Pagination

Both `list_inbox_messages()` and `search_messages()` accept an optional `page_token` parameter:

```python
# First page
result = search_messages("from:alice@example.com", max_results=10)
# result["next_page_token"] = "006271f..."

# Second page
next_result = search_messages("from:alice@example.com", max_results=10, page_token=result["next_page_token"])
```

The `result_size_estimate` field gives an approximate total count of matching messages.
