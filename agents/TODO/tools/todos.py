"""Todo task management tools.

Public tool functions:
    add_todo(title, description, labels, due_date, due_time)
    list_todos(status, label, due_before, due_after)
    get_todo(todo_id)
    update_todo(todo_id, title, description, labels, due_date, due_time, status)
    complete_todo(todo_id)
    delete_todo(todo_id)
"""

from __future__ import annotations

import importlib.util
import logging
from pathlib import Path
from typing import Any

# Load sibling _db module by absolute path to avoid sys.path pollution.
_tools_dir = Path(__file__).resolve().parent
_spec = importlib.util.spec_from_file_location("_db", str(_tools_dir / "_db.py"))
_db_module = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_db_module)
get_db = _db_module.get_db
init_schema = _db_module.init_schema
row_to_dict = _db_module.row_to_dict

logger = logging.getLogger(__name__)


def _normalize_labels(labels: str) -> str:
    """Convert a comma-separated label string into a sorted, cleaned string."""
    if not labels:
        return ""
    parts = [label.strip().lower() for label in labels.split(",")]
    return ", ".join(sorted({p for p in parts if p}))


def add_todo(
    title: str,
    description: str = "",
    labels: str = "",
    due_date: str = "",
    due_time: str = "",
) -> dict[str, Any]:
    """Add a new todo task.

    Args:
        title: Short task title.
        description: Optional details.
        labels: Comma-separated labels, e.g. "work, urgent".
        due_date: Due date in YYYY-MM-DD format (optional).
        due_time: Due time in HH:MM format (optional).

    Returns:
        A dict with the created todo, or an error description.
    """
    if not title or not title.strip():
        return {"error": "Title is required."}

    try:
        db = get_db()
        init_schema()
        cursor = db.execute(
            """
            INSERT INTO todos (title, description, labels, due_date, due_time)
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                title.strip(),
                description.strip(),
                _normalize_labels(labels),
                due_date.strip() or None,
                due_time.strip() or None,
            ),
        )
        db.commit()
        return get_todo(cursor.lastrowid)
    except Exception as exc:  # noqa: BLE001
        logger.exception("Error adding todo")
        return {"error": f"Failed to add todo: {type(exc).__name__}: {exc}"}


def list_todos(
    status: str = "",
    label: str = "",
    due_before: str = "",
    due_after: str = "",
) -> dict[str, Any]:
    """List todo tasks with optional filters.

    Args:
        status: Filter by "pending" or "completed".
        label: Filter by a single label.
        due_before: Only include tasks due on or before this date (YYYY-MM-DD).
        due_after: Only include tasks due on or after this date (YYYY-MM-DD).

    Returns:
        A dict with a list of matching todos under the "todos" key.
    """
    try:
        db = get_db()
        init_schema()

        conditions: list[str] = []
        params: list[Any] = []

        if status:
            conditions.append("status = ?")
            params.append(status)
        if label:
            conditions.append("labels LIKE ?")
            params.append(f"%{label.strip().lower()}%")
        if due_before:
            conditions.append("due_date <= ?")
            params.append(due_before)
        if due_after:
            conditions.append("due_date >= ?")
            params.append(due_after)

        where_clause = " AND ".join(conditions) if conditions else "1=1"
        rows = db.execute(
            f"""
            SELECT * FROM todos
            WHERE {where_clause}
            ORDER BY
                CASE WHEN due_date IS NULL THEN 1 ELSE 0 END,
                due_date ASC,
                due_time ASC,
                created_at DESC
            """,
            params,
        ).fetchall()

        return {"todos": [row_to_dict(row) for row in rows]}
    except Exception as exc:  # noqa: BLE001
        logger.exception("Error listing todos")
        return {"error": f"Failed to list todos: {type(exc).__name__}: {exc}"}


def get_todo(todo_id: int) -> dict[str, Any]:
    """Get a single todo by ID.

    Args:
        todo_id: The numeric todo ID.

    Returns:
        The todo dict, or an error description.
    """
    try:
        db = get_db()
        init_schema()
        row = db.execute("SELECT * FROM todos WHERE id = ?", (todo_id,)).fetchone()
        if row is None:
            return {"error": f"Todo {todo_id} not found."}
        return row_to_dict(row)
    except Exception as exc:  # noqa: BLE001
        logger.exception("Error getting todo")
        return {"error": f"Failed to get todo: {type(exc).__name__}: {exc}"}


def update_todo(
    todo_id: int,
    title: str = "",
    description: str = "",
    labels: str = "",
    due_date: str = "",
    due_time: str = "",
    status: str = "",
) -> dict[str, Any]:
    """Update one or more fields of an existing todo.

    Args:
        todo_id: The numeric todo ID.
        title: New title (optional).
        description: New description (optional).
        labels: New comma-separated labels (optional).
        due_date: New due date YYYY-MM-DD (optional).
        due_time: New due time HH:MM (optional).
        status: "pending" or "completed" (optional).

    Returns:
        The updated todo dict, or an error description.
    """
    try:
        db = get_db()
        init_schema()

        updates: dict[str, Any] = {"updated_at": "datetime('now')"}
        params: list[Any] = []

        if title:
            updates["title"] = "?"
            params.append(title.strip())
        if description:
            updates["description"] = "?"
            params.append(description.strip())
        if labels:
            updates["labels"] = "?"
            params.append(_normalize_labels(labels))
        if due_date:
            updates["due_date"] = "?"
            params.append(due_date.strip())
        elif due_date == "":
            pass  # Keep existing value when not provided.
        if due_time:
            updates["due_time"] = "?"
            params.append(due_time.strip())
        elif due_time == "":
            pass
        if status:
            updates["status"] = "?"
            params.append(status)

        if not updates:
            return {"error": "No fields provided to update."}

        set_clause = ", ".join(f"{k} = {v}" for k, v in updates.items())
        params.append(todo_id)

        cursor = db.execute(
            f"UPDATE todos SET {set_clause} WHERE id = ?",
            params,
        )
        db.commit()

        if cursor.rowcount == 0:
            return {"error": f"Todo {todo_id} not found."}
        return get_todo(todo_id)
    except Exception as exc:  # noqa: BLE001
        logger.exception("Error updating todo")
        return {"error": f"Failed to update todo: {type(exc).__name__}: {exc}"}


def complete_todo(todo_id: int) -> dict[str, Any]:
    """Mark a todo as completed.

    Args:
        todo_id: The numeric todo ID.

    Returns:
        The updated todo dict, or an error description.
    """
    return update_todo(todo_id, status="completed")


def delete_todo(todo_id: int) -> dict[str, Any]:
    """Delete a todo by ID.

    Args:
        todo_id: The numeric todo ID.

    Returns:
        A success message, or an error description.
    """
    try:
        db = get_db()
        init_schema()
        cursor = db.execute("DELETE FROM todos WHERE id = ?", (todo_id,))
        db.commit()
        if cursor.rowcount == 0:
            return {"error": f"Todo {todo_id} not found."}
        return {"success": True, "deleted_id": todo_id}
    except Exception as exc:  # noqa: BLE001
        logger.exception("Error deleting todo")
        return {"error": f"Failed to delete todo: {type(exc).__name__}: {exc}"}
