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

- `write_file(path, content)` — for new files only
- `edit_file(path, content)` — for modifying files that already exist
- `read_file(path, max_lines)` — for verification and context gathering
- `read_directory_tree(path, max_depth)` — for exploring structure

## Convention Matching

Follow the existing codebase for:

- Quote style (single vs. double)
- Import ordering and style
- Naming conventions (snake_case, CamelCase, kebab-case)
- Docstring format
- Error handling patterns
- Test file locations (but do not write tests — that is TEST_ENG's job)
