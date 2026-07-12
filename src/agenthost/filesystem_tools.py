"""Read-only filesystem tools for agents.

Used for accessing uploaded files and project documents. All paths are
resolved relative to the agenthost project root and ``..`` traversal is
blocked for safety.
"""
from __future__ import annotations

import csv
import json
import os
from pathlib import Path
from typing import Any

from agenthost.home import get_agenthost_home
from agenthost.logger import setup_logging


logger = setup_logging("agenthost.filesystem")

# Directory where Discord/uploads store user-provided files.
UPLOADS_DIR = get_agenthost_home() / "uploads"

# Root directory that agents are allowed to read from. All requested paths
# must resolve inside this directory.
PROJECT_ROOT = get_agenthost_home()


def _resolve_and_guard(path: str) -> Path:
    """Resolve a user-provided path relative to the project root.

    Blocks absolute paths outside the project and ``..`` traversal. Returns
    the resolved Path if safe, otherwise raises ValueError.
    """
    if not path:
        raise ValueError("Path cannot be empty")

    target = Path(path)
    if target.is_absolute():
        resolved = target.resolve()
    else:
        resolved = (PROJECT_ROOT / target).resolve()

    # Ensure the resolved path is inside the project root.
    try:
        resolved.relative_to(PROJECT_ROOT)
    except ValueError as exc:
        raise ValueError(
            f"Access denied: '{path}' resolves outside the project directory."
        ) from exc

    return resolved


class FileSystemTools:
    """Read-only tools for inspecting uploaded files and project documents."""

    def read_file(self, path: str, max_lines: int = 1000, offset: int = 1) -> str:
        """Read lines from a text file inside the project directory.

        The path is resolved relative to the agenthost project root. Absolute
        paths and ``..`` traversal are blocked. Useful for reading uploaded
        documents that have been extracted to `.txt` sidecars.

        Args:
            path: Relative or absolute path inside the project directory.
            max_lines: Maximum number of lines to return (default 1000).
            offset: Line number to start from (1-based).

        Returns:
            JSON string with the requested lines and metadata.
        """
        try:
            target = _resolve_and_guard(path)
        except ValueError as exc:
            return json.dumps({"error": str(exc)})

        if not target.exists():
            return json.dumps({"error": f"File not found: {path}"})
        if not target.is_file():
            return json.dumps({"error": f"Path is not a file: {path}"})

        try:
            with target.open("r", encoding="utf-8", errors="replace") as fh:
                lines = fh.readlines()
        except UnicodeDecodeError:
            return json.dumps(
                {"error": f"File appears to be binary or non-text: {path}"}
            )
        except Exception as exc:  # noqa: BLE001
            return json.dumps({"error": f"Failed to read file: {exc}"})

        start = max(0, offset - 1)
        end = start + max_lines
        selected = lines[start:end]

        return json.dumps(
            {
                "path": path,
                "total_lines": len(lines),
                "offset": offset,
                "max_lines": max_lines,
                "returned_lines": len(selected),
                "content": "".join(selected),
            },
            indent=2,
        )

    def list_uploads(self) -> str:
        """List files in the shared uploads directory.

        Returns original files and their extracted text sidecars, if any.
        """
        try:
            UPLOADS_DIR.mkdir(parents=True, exist_ok=True)
        except Exception as exc:  # noqa: BLE001
            return json.dumps({"error": f"Failed to create uploads dir: {exc}"})

        files: list[dict[str, Any]] = []
        try:
            for entry in sorted(UPLOADS_DIR.iterdir()):
                if not entry.is_file():
                    continue
                # Skip sidecar files themselves.
                if entry.suffix.lower() == ".txt" and entry.with_suffix("").suffix.lower() not in PLAINTEXT_SUFFIXES:
                    continue

                if entry.suffix.lower() in PLAINTEXT_SUFFIXES:
                    text_sidecar_name = entry.name
                else:
                    sidecar = Path(str(entry) + ".txt")
                    text_sidecar_name = sidecar.name if sidecar.exists() else None

                files.append(
                    {
                        "name": entry.name,
                        "size_bytes": entry.stat().st_size,
                        "extracted_text": text_sidecar_name,
                    }
                )
        except Exception as exc:  # noqa: BLE001
            return json.dumps({"error": f"Failed to list uploads: {exc}"})

        return json.dumps(
            {
                "uploads_dir": str(UPLOADS_DIR.relative_to(PROJECT_ROOT)),
                "count": len(files),
                "files": files,
            },
            indent=2,
        )


def extract_text(source_path: Path) -> str:
    """Extract readable text from common document formats.

    Supported: .docx, .pdf, .xlsx, .csv, .md, .txt
    Returns the extracted text as a string. Raises ValueError for unsupported
    formats.
    """
    suffix = source_path.suffix.lower()

    if suffix == ".docx":
        return _extract_docx(source_path)
    if suffix == ".pdf":
        return _extract_pdf(source_path)
    if suffix == ".xlsx":
        return _extract_excel(source_path)
    if suffix == ".csv":
        return _extract_csv(source_path)
    if suffix in {".md", ".txt", ".json", ".yaml", ".yml"}:
        return source_path.read_text(encoding="utf-8", errors="replace")

    raise ValueError(f"Unsupported file type for text extraction: {suffix}")


def _extract_docx(path: Path) -> str:
    from docx import Document

    doc = Document(str(path))
    paragraphs = [p.text for p in doc.paragraphs if p.text.strip()]
    return "\n\n".join(paragraphs)


def _extract_pdf(path: Path) -> str:
    from pypdf import PdfReader

    reader = PdfReader(str(path))
    pages: list[str] = []
    for page in reader.pages:
        text = page.extract_text()
        if text:
            pages.append(text)
    return "\n\n".join(pages)


def _extract_excel(path: Path) -> str:
    import pandas as pd

    sheets = pd.read_excel(path, sheet_name=None, dtype=str)
    parts: list[str] = []
    for sheet_name, df in sheets.items():
        parts.append(f"# Sheet: {sheet_name}\n")
        parts.append(df.to_markdown(index=False))
        parts.append("")
    return "\n".join(parts)


def _extract_csv(path: Path) -> str:
    import pandas as pd

    df = pd.read_csv(path, dtype=str)
    return df.to_markdown(index=False)


PLAINTEXT_SUFFIXES = {".md", ".txt", ".json", ".yaml", ".yml"}


def save_upload_with_text(
    source_path: Path,
    original_filename: str,
) -> tuple[Path, Path]:
    """Save an uploaded file and, if needed, an extracted text sidecar.

    Plain-text files (.txt, .md, .json, .yaml, .yml) are kept as-is and the
    returned text_path points to the original file. Binary formats get a
    `.txt` sidecar.

    Returns (original_path, text_path) relative to the agenthost home directory.
    """
    UPLOADS_DIR.mkdir(parents=True, exist_ok=True)

    # Ensure a unique filename in the uploads directory.
    base = Path(original_filename).stem
    suffix = Path(original_filename).suffix
    candidate = UPLOADS_DIR / f"{base}{suffix}"
    counter = 1
    while candidate.exists():
        candidate = UPLOADS_DIR / f"{base}_{counter}{suffix}"
        counter += 1

    original_path = candidate
    original_path.write_bytes(source_path.read_bytes())

    if suffix.lower() in PLAINTEXT_SUFFIXES:
        return (
            original_path.relative_to(PROJECT_ROOT),
            original_path.relative_to(PROJECT_ROOT),
        )

    # Extract text and save sidecar.
    try:
        text = extract_text(original_path)
    except Exception as exc:  # noqa: BLE001
        text = f"[Extraction failed: {exc}]"

    text_path = Path(str(original_path) + ".txt")
    text_path.write_text(text, encoding="utf-8")

    return (
        original_path.relative_to(PROJECT_ROOT),
        text_path.relative_to(PROJECT_ROOT),
    )
