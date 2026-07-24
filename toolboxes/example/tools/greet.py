"""Example toolbox tool: returns a friendly greeting."""

from __future__ import annotations


async def greet(name: str = "World") -> str:
    """Return a greeting for the given name.

    Args:
        name: The name to greet.
    """
    return f"Hello, {name}! This message came from the example toolbox."
