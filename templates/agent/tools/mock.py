"""Mock tools for the agent template.

These functions demonstrate how tools are discovered. Only functions defined in
this module and not starting with an underscore are registered as callable tools.
"""

from __future__ import annotations


def mock_tool(message: str = "hello") -> dict:
    """A mock tool that echoes back its input.

    Use this as a starting point when adding new tools to an agent.
    """
    return {"tool": "mock_tool", "message": message}


def normal_tool(value: int) -> dict:
    """A normal tool that doubles an integer.

    This function is registered as a tool because its name does not start with
    an underscore.
    """
    return {"tool": "normal_tool", "value": value, "doubled": value * 2}


def _hidden_helper(name: str) -> dict:
    """A helper function that is NOT registered as a tool.

    Tool discovery ignores functions whose names start with an underscore.
    """
    return {"tool": "_hidden_helper", "name": name}
