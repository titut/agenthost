# Description

Common patterns for writing, editing, and reviewing code in this project.

# Development Patterns

## When to Create vs. Extend

- **Create a new module** when the feature introduces a new bounded concept, public API surface, or responsibility
- **Extend an existing module** when the change is a natural addition to existing code
- **Never duplicate** a pattern that already exists in the repo — reuse it

## Before Writing

1. Read the architecture document from SYS_ENG:
   - `agents/SYS_ENG/output/architecture/<feature-name>.md`
2. Explore the repo root with `read_directory_tree(path=".", max_depth=3)`
3. Read the most relevant existing files to understand conventions

## After Writing

1. `read_file` the file you just wrote
2. Check imports, signatures, and naming against the spec
3. Confirm the file is reachable from the project (imported, referenced, or executed)

## Tool Usage

- `write_file(path, content)` — for new files (also overwrites existing files, but only after backing them up)
- `edit_file(path, old_string, new_string)` — for modifying a unique snippet inside an existing file; refuses if `old_string` is missing or ambiguous
- `read_file(path, max_lines)` — for verification and context gathering (watch for truncation warnings)
- `read_directory_tree(path, max_depth)` — for exploring structure

Both `write_file` and `edit_file` create timestamped backups under `.agenthost/backups/` before overwriting anything.

## Convention Matching

Follow the existing codebase for:

- Quote style (single vs. double)
- Import ordering and style
- Naming conventions (snake_case, CamelCase, kebab-case)
- Docstring format
- Error handling patterns
- Test file locations (but do not write tests — that is TEST_ENG's job)
