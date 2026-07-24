# Description

Create and maintain a dynamic Discord TODO board that lists tasks in the format the user prefers.

# When to Use

Use this skill **every time** a todo is added, updated, completed, or deleted. The board keeps the Discord message in sync with the current state of the TODO list.

Target message:

- Thread ID: `1528625353139159090`
- Message ID: `1528648315191955457`

# Default Template

If the user has not specified a format, use this default:

```markdown
## TODO Board

| Task | Labels | Due | Status |
|------|--------|-----|--------|
| Task title | label1, label2 | YYYY-MM-DD HH:MM | pending |
| Another task | label1 | YYYY-MM-DD HH:MM | completed |
```

Rules for the default template:

- Fetch all tasks with `list_todos()`.
- Sort: pending first, overdue at the top, then by due date; completed tasks last.
- Hide the `description` column by default.
- If a pending task is past due, mark its status as `overdue` and bold the row.
- Do not include completed tasks older than 30 days unless the user asks for them.
- If the list is empty, post "No tasks yet."

# Customization

The user may ask for a different board layout. Adapt the board to their request. Examples:

## Segmentation by label

If the user asks to group by label, create one table per label:

```markdown
## TODO Board

### work

| Task | Due | Status |
|------|-----|--------|
| Finish report | 2026-07-21 17:00 | pending |

### personal

| Task | Due | Status |
|------|-----|--------|
| Buy groceries | 2026-07-20 18:00 | completed |
```

## Custom columns

If the user asks for specific columns, include only those columns. Valid columns include: `Task`, `Description`, `Labels`, `Due`, `Status`, `Created`, `Updated`.

Example:

```markdown
## TODO Board

| Task | Description | Due | Status |
|------|-------------|-----|--------|
| Finish report | Finalize Q3 report | 2026-07-21 17:00 | pending |
```

## Remembering the user's preference

If the user explicitly states a preferred format (e.g. "group by label", "show descriptions", or "use columns X, Y, Z"), remember that preference and use it for all future board updates in this thread. Apply the preference automatically until the user asks to change it.

# How to Update the Board

1. Fetch tasks with `list_todos()`.
2. Apply the user's preferred format if known; otherwise use the default template.
3. Call `edit_discord_message(text=<board>, thread_id="1528625353139159090", message_id="1528648315191955457")`.

Do not explain the format to the user unless they ask. Just generate the board and update the message.
