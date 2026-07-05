# Description

Safety rules for reading, sending, and modifying Gmail messages.

# Gmail Safety Rules (gmail-safety)

## Golden Rules

1. **Never retry blindly on HTTP 429.** Log the error, parse `Retry-After` if present, and return the error to the caller. Let the caller decide the retry strategy.
2. **Prefer trash over permanent delete.** Always use `trash_message()` instead of `delete_message_permanently()` unless the user explicitly requests permanent deletion.
3. **`delete_message_permanently` requires `confirmed=True`.** The function first returns a preview (message snippet) and asks for confirmation. Only when `confirmed=True` is passed does it proceed with deletion.
4. **Never strip system-managed labels.** The following labels are managed automatically by Gmail and must not be manually removed via `remove_labels`:
   - `INBOX`
   - `SENT`
   - `STARRED`
   - `IMPORTANT`
5. **Confirm sends.** After sending a message, verify the returned `id` and `threadId` and report them to the caller as confirmation.
6. **Validate email addresses before sending.** At minimum, check that the recipient's address contains an `@` symbol. Gmail will perform full server-side validation.

## Quota Awareness

The Gmail API has a quota of **250 quota units per second per user**. Each operation costs:

| Operation | Cost (units) |
|-----------|-------------|
| `messages.list` | 1 |
| `messages.get` (full) | 5 |
| `messages.get` (metadata) | 1 |
| `messages.send` | 100 |
| `messages.modify` (labels) | 5 |
| `messages.trash` / `untrash` | 10 |
| `messages.delete` | 10 |
| `labels.list` | 1 |
| `drafts.create` / `send` | 10 |
| `attachments.get` | 5 |

**Key takeaway:** Sending is 20× more expensive than listing. Don't re-send unless necessary.

## Destructive Operation Protocol

For any operation that modifies or deletes data:

1. **List first** — When a user asks to delete or modify a specific email, confirm you have the right one by referencing its `id` and snippet.
2. **Confirm intent** — For permanent deletion, always return a preview snippet first and require `confirmed=True`.
3. **Report results** — After any destructive operation, confirm what was done and include the message ID.

## Error Response Format

All safety violations return a structured error dict:

```json
{
  "error": "Cannot manually remove system label: INBOX. This label is managed by Gmail."
}
```

```json
{
  "requires_confirmation": true,
  "message_id": "191c3d8...",
  "snippet": "Hey, just confirming...",
  "instruction": "Call again with confirmed=True to permanently delete this message."
}
```

## Rate Limit Handling

When a 429 response is received:

1. Parse the `Retry-After` header from the response (value is seconds to wait)
2. **Do not sleep and retry internally** — return the error to the calling agent
3. Return format:

```json
{
  "error": "Rate limit exceeded. Retry after 5 seconds.",
  "retry_after": 5
}
```

The calling agent (or user) decides whether and when to retry.
