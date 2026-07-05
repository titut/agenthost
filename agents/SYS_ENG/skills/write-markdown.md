# Description

How and when to use the write_markdown tool for documentation.

# Tools

## write_markdown

Use this to produce structured systems-engineering artifacts.

- `path`: relative path inside the output directory, e.g. `idr/multi-region-postgres.md`
- `content`: complete markdown content

## read_directory_tree

Use this to understand the repository layout before making recommendations.

- `path`: directory relative to the repo root (default `"."`)
- `max_depth`: how many levels to expand (default `3`)

Returns a JSON tree of files and directories, skipping ignored folders like `.git`, `venv`, `__pycache__`, `node_modules`.

## read_file

Use this to inspect configuration files, source code, documentation, or existing runbooks.

- `path`: file path relative to the repo root
- `max_lines`: maximum lines to return (default `300`)

Returns the file content and metadata. Binary or very large files are rejected.

## When to Read the Repo

- Before proposing infrastructure changes, read `pyproject.toml`, `Dockerfile`, `docker-compose.yml`, CI configs, and deployment manifests
- Before writing a runbook, read the relevant service code and existing operational docs
- Before making architectural recommendations, read `README.md`, `AGENTS.md`, and the `src/` tree
- If a file is too long, read it in chunks with `max_lines` or focus on the most relevant sections
