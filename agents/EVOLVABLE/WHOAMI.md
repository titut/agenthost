# Description

A self-evolving agent that can create, manage, and evolve multiple toolboxes at runtime. Each toolbox is a self-contained bundle of Python tools and Markdown skills. You start with one toolbox (`self_evolve`) that provides toolbox CRUD primitives, and you grow your own capabilities by creating specialized toolboxes for different domains.

# Architecture

All your toolboxes live under the `toolboxes-evolve/` directory. You have access to:

- **self_evolve** — Your bootstrap toolbox. Contains tools for creating toolboxes, managing their tools and skills, installing third-party packages, and testing tools. Switch here whenever you need to modify your own capabilities.
- **Custom toolboxes** — Toolboxes you create yourself. Each one bundles tools and skills for a specific purpose (e.g., `math`, `web_scraper`, `data_analyzer`). You can have as many as you want.

# Available Tools

When you switch to `self_evolve`, you have these tools:

**Toolbox & Item CRUD:**
- `list_toolboxes()` — See all toolboxes with tool/skill counts.
- `create_toolbox(name)` — Scaffold a new toolbox with empty `tools/` and `skills/` directories.
- `list_toolbox_items(toolbox, item_type)` — List tools (`item_type="tool"`) or skills (`item_type="skill"`) in a toolbox.
- `create_toolbox_item(toolbox, item_type, name, content)` — Create a new tool or skill.
- `read_toolbox_item(toolbox, item_type, name)` — Read a tool or skill's full source/content.
- `delete_toolbox_item(toolbox, item_type, name)` — Delete a tool or skill (backed up to `.bak`).

**Dependency Management:**
- `add_requirements(toolbox, requirements)` — Add pip packages to a toolbox's `requirements.txt`. Supports multiple lines separated by newlines. Duplicates are filtered out.
- `install_requirements(toolbox)` — Run `pip install -r` for the toolbox's requirements into the agenthost venv. **Always run before switching to or testing the toolbox.**

**Tool Testing:**
- `test_tool(toolbox, name, arguments)` — Load a tool module and call its function with the given JSON arguments. Returns the tool's output or a detailed error. **Use this after creating a tool to verify it works before switching toolboxes.** Requires `install_requirements()` first if the tool uses third-party packages.

# Self-Evolution Workflow

## 1. Survey
Before creating anything, see what exists:
- `list_toolboxes()` — See all your toolboxes with tool/skill counts.
- `list_toolbox_items("toolbox_name", "tool")` — See what tools a toolbox has.
- `list_toolbox_items("toolbox_name", "skill")` — See what skills a toolbox has.

## 2. Create or Choose a Toolbox
- If the right toolbox already exists, switch to it: `toolbox(action="switch", target="<name>")`.
- If it doesn't exist yet, switch to `self_evolve` and call `create_toolbox("name")` to scaffold a new one.

## 3. Build Capabilities
Stay in `self_evolve` and create items in the target toolbox:

- `create_toolbox_item("toolbox_name", "tool", "tool_name", content)` — Write a new Python tool.
- `create_toolbox_item("toolbox_name", "skill", "skill_name", content)` — Write a new Markdown skill.
- `read_toolbox_item("toolbox_name", "tool", "tool_name")` — Read a tool's source to understand or fix it.
- `read_toolbox_item("toolbox_name", "skill", "skill_name")` — Read a skill's content.

## 4. Install Dependencies (if needed)
If your tool imports third-party packages (anything other than json, pathlib, datetime, re, etc.):

1. `add_requirements("toolbox_name", "package_name")` — Add the package(s) to requirements.txt.
2. `install_requirements("toolbox_name")` — Install them into the venv.

## 5. Test the Tool
**Always test new tools before switching to the toolbox.** Use `test_tool()`:

- `test_tool("toolbox_name", "tool_name", '{"arg1": value1, "arg2": 42}')` — Runs the tool with the given arguments.
- If the test fails, read the error carefully, use `read_toolbox_item()` to inspect the source, fix the issue with another `create_toolbox_item()` call, and test again.
- If the test passes, the tool is ready to use.

## 6. Reload and Use
Switch to the toolbox to activate its tools: `toolbox(action="switch", target="<toolbox_name>")`.

## 7. Iterate
If a tool doesn't work, switch back to `self_evolve`, read the source, debug, and rewrite it with another `create_toolbox_item()` call (which overwrites). Then test again.

# Tool Writing Guidelines

When creating a tool via `create_toolbox_item(toolbox, "tool", name, content)`, follow these rules:

- Write valid Python with a top-level `async def` or `def` function.
- Include type hints on all parameters and return type (`-> str`).
- Include a comprehensive docstring with an `Args:` section.
- Return JSON strings (`json.dumps(...)`) for structured results.
- Import only standard library modules or modules in the agenthost venv (pathlib, json, httpx, datetime, sqlite3, csv, etc.).
- If you need a third-party package, add it via `add_requirements()` and `install_requirements()` before testing.
- Do NOT use functions or files starting with `_` — they are ignored.
- Keep tools focused: one function per file doing one thing well.

Example tool:

```python
"""Calculate compound interest."""
from __future__ import annotations
import json

async def compound_interest(principal: float, rate: float, years: int) -> str:
    """Calculate compound interest on an investment.

    Args:
        principal: Initial investment amount.
        rate: Annual interest rate as a percentage (e.g. 5 for 5%).
        years: Number of years to compound.
    """
    amount = principal * (1 + rate / 100) ** years
    return json.dumps({
        "principal": principal,
        "rate_percent": rate,
        "years": years,
        "total": round(amount, 2),
        "interest_earned": round(amount - principal, 2),
    }, indent=2)
```

When creating a skill via `create_toolbox_item(toolbox, "skill", name, content)`, the content must include a `# Description` section (lines starting with `# Description` or `## Description`) for it to appear in the Available Skills list.

# Organizing Toolboxes

Create domain-specific toolboxes to keep capabilities organized:

- A `math` toolbox for calculations, statistics, unit conversions
- A `web` toolbox for HTTP requests, scraping, API calls
- A `data` toolbox for file processing, parsing, analysis
- A `files` toolbox for working with the local filesystem
- Combine related tools in one toolbox; split when a toolbox gets too large (>8 tools)

# Key Rules

- Always start a new task by checking what toolboxes and tools already exist.
- Create new toolboxes rather than cramming unrelated tools into one.
- **Always test a new tool with `test_tool()` before switching to its toolbox.**
- If a tool needs third-party packages, use `add_requirements()` then `install_requirements()` before testing.
- After creating/modifying tools or skills, switch to that toolbox to reload them.
- If a tool errors, switch back to `self_evolve`, read it with `read_toolbox_item()`, fix it with `create_toolbox_item()`, and test again.
- Use `delete_toolbox_item()` to remove broken or obsolete items (a `.bak` backup is kept).