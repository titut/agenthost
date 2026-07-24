"""DOCX writer tool for the document_writer toolbox.

Creates high-quality Word documents from Markdown or structured JSON blocks.
"""

from __future__ import annotations

import importlib.util
import json
import re
from pathlib import Path
from typing import Any

_tools_dir = Path(__file__).resolve().parent
_spec = importlib.util.spec_from_file_location("_shared", str(_tools_dir / "_shared.py"))
_shared = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_shared)


def _resolve_image_path(path: str) -> Path:
    """Resolve an image path relative to the agenthost home directory."""
    from agenthost.filesystem_tools import _resolve_and_guard

    try:
        return _resolve_and_guard(path)
    except ValueError:
        # Fallback: treat as relative to agenthost home.
        home = _shared.ensure_agenthost_home()
        target = (home / path).resolve()
        if target.exists() and target.is_file():
            return target
        raise FileNotFoundError(f"Image not found: {path}")


def _add_page_number(paragraph: Any) -> None:
    """Insert a Word page-number field into the paragraph."""
    from docx.oxml import OxmlElement  # type: ignore[import-untyped]
    from docx.oxml.ns import qn  # type: ignore[import-untyped]

    run = paragraph.add_run()
    fld_char_begin = OxmlElement("w:fldChar")
    fld_char_begin.set(qn("w:fldCharType"), "begin")
    instr_text = OxmlElement("w:instrText")
    instr_text.set(qn("xml:space"), "preserve")
    instr_text.text = " PAGE "
    fld_char_separate = OxmlElement("w:fldChar")
    fld_char_separate.set(qn("w:fldCharType"), "separate")
    fld_char_end = OxmlElement("w:fldChar")
    fld_char_end.set(qn("w:fldCharType"), "end")
    run._r.append(fld_char_begin)
    run._r.append(instr_text)
    run._r.append(fld_char_separate)
    run._r.append(fld_char_end)


def _add_toc(paragraph: Any, levels: str = "1-3") -> None:
    """Insert a Word table-of-contents field into the paragraph."""
    from docx.oxml import OxmlElement  # type: ignore[import-untyped]
    from docx.oxml.ns import qn  # type: ignore[import-untyped]

    run = paragraph.add_run()
    fld_char_begin = OxmlElement("w:fldChar")
    fld_char_begin.set(qn("w:fldCharType"), "begin")
    instr_text = OxmlElement("w:instrText")
    instr_text.set(qn("xml:space"), "preserve")
    instr_text.text = f' TOC \\o "{levels}" \\h \\z \\u '
    fld_char_separate = OxmlElement("w:fldChar")
    fld_char_separate.set(qn("w:fldCharType"), "separate")
    fld_char_end = OxmlElement("w:fldChar")
    fld_char_end.set(qn("w:fldCharType"), "end")
    run._r.append(fld_char_begin)
    run._r.append(instr_text)
    run._r.append(fld_char_separate)
    run._r.append(fld_char_end)


def _add_hyperlink(paragraph: Any, text: str, url: str) -> Any:
    """Add a clickable external hyperlink to a paragraph."""
    from docx.oxml import OxmlElement  # type: ignore[import-untyped]
    from docx.oxml.ns import qn  # type: ignore[import-untyped]

    part = paragraph.part
    r_id = part.relate_to(
        url,
        "http://schemas.openxmlformats.org/officeDocument/2006/relationships/hyperlink",
        is_external=True,
    )
    hyperlink = OxmlElement("w:hyperlink")
    hyperlink.set(qn("r:id"), r_id)
    new_run = OxmlElement("w:r")
    rPr = OxmlElement("w:rPr")
    color = OxmlElement("w:color")
    color.set(qn("w:val"), "0563C1")
    rPr.append(color)
    u = OxmlElement("w:u")
    u.set(qn("w:val"), "single")
    rPr.append(u)
    new_run.append(rPr)
    new_run.text = text
    hyperlink.append(new_run)
    paragraph._p.append(hyperlink)
    return hyperlink


def _apply_inline_formatting(paragraph: Any, text: str) -> None:
    """Add text to a paragraph with Markdown inline formatting."""
    from docx.shared import Pt, RGBColor  # type: ignore[import-untyped]

    parts = re.split(
        r"(\[.*?\]\(.*?\)|\*\*.*?\*\*|__.*?__|\*.*?\*|_.*?_|`.*?`)", text
    )
    for part in parts:
        if not part:
            continue

        # Hyperlink.
        link_match = re.match(r"\[(.*?)\]\((.*?)\)", part)
        if link_match:
            _add_hyperlink(paragraph, link_match.group(1), link_match.group(2))
            continue

        run = paragraph.add_run()
        clean = part
        if part.startswith("**") and part.endswith("**") and len(part) > 4:
            clean = part[2:-2]
            run.bold = True
        elif part.startswith("__") and part.endswith("__") and len(part) > 4:
            clean = part[2:-2]
            run.bold = True
        elif part.startswith("*") and part.endswith("*") and len(part) > 2:
            clean = part[1:-1]
            run.italic = True
        elif part.startswith("_") and part.endswith("_") and len(part) > 2:
            clean = part[1:-1]
            run.italic = True
        elif part.startswith("`") and part.endswith("`") and len(part) > 2:
            clean = part[1:-1]
            run.font.name = "Courier New"
            run.font.size = Pt(9)
            run.font.color.rgb = RGBColor(0x33, 0x33, 0x33)
        run.text = clean


def _set_docx_defaults(doc: Any) -> None:
    """Set high-quality defaults for fonts, heading sizes, and colors."""
    from docx.shared import Pt, RGBColor  # type: ignore[import-untyped]

    normal = doc.styles["Normal"]
    normal.font.name = "Arial"
    normal.font.size = Pt(11)
    normal.font.color.rgb = RGBColor(0, 0, 0)

    heading_sizes = {1: 18, 2: 16, 3: 14, 4: 12, 5: 11, 6: 11}
    for level, size in heading_sizes.items():
        try:
            style = doc.styles[f"Heading {level}"]
            style.font.name = "Arial"
            style.font.size = Pt(size)
            style.font.bold = True
            style.font.color.rgb = RGBColor(0, 0, 0)
        except KeyError:
            pass


def _set_page_setup(
    section: Any,
    page_size: str = "letter",
    orientation: str = "portrait",
    margins: dict[str, float] | None = None,
) -> None:
    """Configure page size, orientation, and margins."""
    from docx.enum.section import WD_ORIENT  # type: ignore[import-untyped]
    from docx.shared import Inches  # type: ignore[import-untyped]

    sizes = {
        "letter": (8.5, 11.0),
        "a4": (8.27, 11.69),
    }
    width, height = sizes.get(page_size.lower(), (8.5, 11.0))
    if orientation.lower() == "landscape":
        width, height = height, width

    section.page_width = Inches(width)
    section.page_height = Inches(height)
    section.orientation = (
        WD_ORIENT.LANDSCAPE
        if orientation.lower() == "landscape"
        else WD_ORIENT.PORTRAIT
    )

    margins = margins or {}
    section.top_margin = Inches(margins.get("top", 1.0))
    section.bottom_margin = Inches(margins.get("bottom", 1.0))
    section.left_margin = Inches(margins.get("left", 1.0))
    section.right_margin = Inches(margins.get("right", 1.0))


def _content_width_dxa(section: Any) -> int:
    """Return available content width in DXA (page width - margins)."""
    return int(section.page_width - section.left_margin - section.right_margin)


def _set_header(section: Any, text: str) -> None:
    """Set section header text, replacing {page_number} with a field."""
    header = section.header
    paragraph = header.paragraphs[0] if header.paragraphs else header.add_paragraph()
    paragraph.clear()
    if "{page_number}" in text:
        before, after = text.split("{page_number}", 1)
        if before:
            paragraph.add_run(before)
        _add_page_number(paragraph)
        if after:
            paragraph.add_run(after)
    else:
        paragraph.add_run(text)


def _set_footer(section: Any, text: str) -> None:
    """Set section footer text, replacing {page_number} with a field."""
    footer = section.footer
    paragraph = footer.paragraphs[0] if footer.paragraphs else footer.add_paragraph()
    paragraph.clear()
    if "{page_number}" in text:
        before, after = text.split("{page_number}", 1)
        if before:
            paragraph.add_run(before)
        _add_page_number(paragraph)
        if after:
            paragraph.add_run(after)
    else:
        paragraph.add_run(text)


def _add_markdown_table(doc: Any, lines: list[str], start_idx: int) -> int:
    """Parse and add a Markdown table starting at start_idx. Returns new index."""
    from docx.enum.table import WD_TABLE_ALIGNMENT  # type: ignore[import-untyped]
    from docx.shared import Inches  # type: ignore[import-untyped]

    header_line = lines[start_idx]
    headers = [cell.strip() for cell in header_line.split("|")[1:-1]]
    rows: list[list[str]] = []
    i = start_idx + 2  # skip separator line
    while i < len(lines) and lines[i].strip().startswith("|"):
        cells = [cell.strip() for cell in lines[i].split("|")[1:-1]]
        while len(cells) < len(headers):
            cells.append("")
        rows.append(cells[: len(headers)])
        i += 1

    if not headers:
        return i

    table = doc.add_table(rows=1 + len(rows), cols=len(headers))
    table.style = "Table Grid"
    table.alignment = WD_TABLE_ALIGNMENT.CENTER
    content_width = _content_width_dxa(doc.sections[0])
    col_width = content_width // len(headers)

    hdr_cells = table.rows[0].cells
    for j, header in enumerate(headers):
        hdr_cells[j].text = header
        hdr_cells[j].width = Inches(col_width / 1440)
        for paragraph in hdr_cells[j].paragraphs:
            for run in paragraph.runs:
                run.bold = True

    for r_idx, row in enumerate(rows):
        cells = table.rows[r_idx + 1].cells
        for c_idx, cell_text in enumerate(row[: len(headers)]):
            cells[c_idx].text = cell_text
            cells[c_idx].width = Inches(col_width / 1440)

    return i


def _add_markdown_content(doc: Any, content: str) -> None:
    """Enhanced Markdown to DOCX conversion."""
    from docx.enum.text import WD_ALIGN_PARAGRAPH  # type: ignore[import-untyped]
    from docx.shared import Inches, Pt, RGBColor  # type: ignore[import-untyped]

    lines = content.splitlines()
    i = 0
    in_code_block = False
    code_lines: list[str] = []

    def _close_code_block() -> None:
        nonlocal in_code_block, code_lines
        if in_code_block and code_lines:
            para = doc.add_paragraph()
            run = para.add_run("\n".join(code_lines))
            run.font.name = "Courier New"
            run.font.size = Pt(9)
            run.font.color.rgb = RGBColor(0x33, 0x33, 0x33)
            code_lines = []
        in_code_block = False

    while i < len(lines):
        line = lines[i]
        stripped = line.strip()

        if stripped.startswith("```"):
            if in_code_block:
                _close_code_block()
            else:
                in_code_block = True
            i += 1
            continue

        if in_code_block:
            code_lines.append(line)
            i += 1
            continue

        if not stripped:
            i += 1
            continue

        if re.match(r"^-{3,}$", stripped) or re.match(r"^\*{3,}$", stripped):
            doc.add_page_break()
            i += 1
            continue

        if stripped.startswith("|") and i + 1 < len(lines) and lines[i + 1].strip().startswith("|"):
            i = _add_markdown_table(doc, lines, i)
            continue

        image_match = re.match(r"!\[(.*?)\]\((.*?)\)", stripped)
        if image_match:
            alt, path = image_match.group(1), image_match.group(2)
            try:
                img_path = _resolve_image_path(path)
                para = doc.add_paragraph()
                para.add_run().add_picture(str(img_path), width=Inches(5.0))
                if alt:
                    cap = doc.add_paragraph()
                    cap.alignment = WD_ALIGN_PARAGRAPH.CENTER
                    cap.add_run(alt).italic = True
            except Exception as exc:  # noqa: BLE001
                doc.add_paragraph(f"[Image error: {exc}]")
            i += 1
            continue

        header_match = re.match(r"^(#{1,6})\s+(.*)", stripped)
        if header_match:
            level = len(header_match.group(1))
            text = header_match.group(2).strip()
            doc.add_heading(text, level=min(level, 6))
            i += 1
            continue

        bullet_match = re.match(r"^[-*+]\s+(.*)", stripped)
        if bullet_match:
            text = bullet_match.group(1)
            para = doc.add_paragraph(style="List Bullet")
            _apply_inline_formatting(para, text)
            i += 1
            continue

        numbered_match = re.match(r"^(\d+)\.\s+(.*)", stripped)
        if numbered_match:
            text = numbered_match.group(2)
            para = doc.add_paragraph(style="List Number")
            _apply_inline_formatting(para, text)
            i += 1
            continue

        para = doc.add_paragraph()
        _apply_inline_formatting(para, stripped)
        i += 1

    _close_code_block()


def _add_json_content(doc: Any, blocks: list[dict[str, Any]]) -> None:
    """Render structured JSON blocks into the DOCX document."""
    from docx.enum.text import WD_ALIGN_PARAGRAPH  # type: ignore[import-untyped]
    from docx.shared import Inches  # type: ignore[import-untyped]

    alignment_map = {
        "left": WD_ALIGN_PARAGRAPH.LEFT,
        "center": WD_ALIGN_PARAGRAPH.CENTER,
        "right": WD_ALIGN_PARAGRAPH.RIGHT,
        "justify": WD_ALIGN_PARAGRAPH.JUSTIFY,
    }
    section = doc.sections[0]
    content_width = _content_width_dxa(section)

    for block in blocks:
        btype = block.get("type", "paragraph")

        if btype == "heading":
            level = int(block.get("level", 1))
            text = str(block.get("text", ""))
            doc.add_heading(text, level=min(max(level, 1), 6))

        elif btype == "paragraph":
            text = str(block.get("text", ""))
            alignment = alignment_map.get(str(block.get("alignment", "left")).lower())
            para = doc.add_paragraph()
            if alignment is not None:
                para.alignment = alignment
            _apply_inline_formatting(para, text)

        elif btype == "bullet_list":
            items = block.get("items", [])
            for item in items:
                para = doc.add_paragraph(style="List Bullet")
                _apply_inline_formatting(para, str(item))

        elif btype == "numbered_list":
            items = block.get("items", [])
            for item in items:
                para = doc.add_paragraph(style="List Number")
                _apply_inline_formatting(para, str(item))

        elif btype == "table":
            headers = list(block.get("headers", []))
            rows = [list(row) for row in block.get("rows", [])]
            widths = block.get("widths")
            num_cols = max(len(headers), max((len(r) for r in rows), default=0))

            table = doc.add_table(rows=1 + len(rows), cols=num_cols)
            table.style = "Table Grid"

            if widths and len(widths) == num_cols and sum(widths) > 0:
                col_widths = [int(content_width * w / 100) for w in widths]
            else:
                col_widths = [content_width // num_cols] * num_cols

            hdr_cells = table.rows[0].cells
            for j in range(num_cols):
                text = headers[j] if j < len(headers) else ""
                hdr_cells[j].text = str(text)
                hdr_cells[j].width = Inches(col_widths[j] / 1440)
                for paragraph in hdr_cells[j].paragraphs:
                    for run in paragraph.runs:
                        run.bold = True

            for r_idx, row in enumerate(rows):
                cells = table.rows[r_idx + 1].cells
                for c_idx in range(num_cols):
                    text = row[c_idx] if c_idx < len(row) else ""
                    cells[c_idx].text = str(text)
                    cells[c_idx].width = Inches(col_widths[c_idx] / 1440)

        elif btype == "image":
            path = str(block.get("path", ""))
            width = float(block.get("width", 5.0))
            caption = block.get("caption")
            try:
                img_path = _resolve_image_path(path)
                para = doc.add_paragraph()
                para.add_run().add_picture(str(img_path), width=Inches(width))
                if caption:
                    cap = doc.add_paragraph()
                    cap.alignment = WD_ALIGN_PARAGRAPH.CENTER
                    cap.add_run(str(caption)).italic = True
            except Exception as exc:  # noqa: BLE001
                doc.add_paragraph(f"[Image error: {exc}]")

        elif btype == "page_break":
            doc.add_page_break()

        elif btype == "toc":
            title = str(block.get("title", "Table of Contents"))
            levels = str(block.get("levels", "1-3"))
            para = doc.add_paragraph()
            para.add_run(title).bold = True
            toc_para = doc.add_paragraph()
            _add_toc(toc_para, levels)


def _parse_options(options: str) -> dict[str, Any]:
    """Parse the options JSON string."""
    if not options or not options.strip():
        return {}
    try:
        parsed = json.loads(options)
        if isinstance(parsed, dict):
            return parsed
    except json.JSONDecodeError:
        pass
    return {}


def write_docx(
    filename: str,
    content: str,
    content_type: str = "auto",
    options: str = "{}",
) -> str:
    """Write content to a high-quality Word document.

    Args:
        filename: Name of the file. If it does not end with `.docx`, the
            extension is appended automatically.
        content: Markdown text or a JSON document specification.
        content_type: `"markdown"`, `"json"`, or `"auto"`. Auto detects JSON
            if `content` starts with `[` or `{`.
        options: JSON string with document-level settings such as title,
            author, subject, header, footer, page_size, orientation, margins.

    Returns:
        JSON string with the relative file path and format.
    """
    from docx import Document  # type: ignore[import-untyped]

    if not filename.lower().endswith(".docx"):
        filename = f"{filename}.docx"

    ctype = content_type.lower().strip()
    if ctype == "auto":
        trimmed = content.strip()
        if trimmed.startswith("[") or trimmed.startswith("{"):
            ctype = "json"
        else:
            ctype = "markdown"

    opts = _parse_options(options)

    _shared.OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    target = _shared.unique_path(_shared.OUTPUT_DIR / filename)

    doc = Document()
    _set_docx_defaults(doc)

    section = doc.sections[0]
    _set_page_setup(
        section,
        page_size=str(opts.get("page_size", "letter")),
        orientation=str(opts.get("orientation", "portrait")),
        margins=opts.get("margins"),
    )

    core = doc.core_properties
    if opts.get("title"):
        core.title = str(opts["title"])
    if opts.get("author"):
        core.author = str(opts["author"])
    if opts.get("subject"):
        core.subject = str(opts["subject"])

    if opts.get("header"):
        _set_header(section, str(opts["header"]))
    if opts.get("footer"):
        _set_footer(section, str(opts["footer"]))

    if ctype == "json":
        try:
            blocks = json.loads(content)
            if isinstance(blocks, dict):
                blocks = blocks.get("content", [])
            if not isinstance(blocks, list):
                return json.dumps({"error": "JSON content must be an array of blocks or an object with a 'content' array."})
            _add_json_content(doc, blocks)
        except json.JSONDecodeError as exc:
            return json.dumps({"error": f"Invalid JSON content: {exc}"})
    else:
        _add_markdown_content(doc, content)

    doc.save(str(target))
    _shared.logger.info("Wrote docx file: %s", target)
    return _shared.format_response(target, ".docx")
