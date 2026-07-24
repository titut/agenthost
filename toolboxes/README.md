# Toolboxes

A toolbox is a folder-based package that bundles a set of tools and skills.
An agent can switch between toolboxes at runtime while keeping the same
memory and conversation state.

## Layout

```
toolboxes/
└── <toolbox-name>/
    ├── tools/
    │   └── *.py          # top-level async or sync functions become tools
    └── skills/
        └── *.md          # markdown skills listed in the system prompt
```

## Switching

From inside the agent, call:

- `toolbox(action="list")` to see all available toolboxes.
- `toolbox(action="list", target="<name>")` to inspect a specific toolbox's tools and skills.
- `toolbox(action="switch", target="<name>")` to load a toolbox's tools and skills.
- `toolbox(action="switch", target="")` to revert to the agent's default tools and skills.

Only one toolbox is active at a time. Built-in tools are always preserved.
