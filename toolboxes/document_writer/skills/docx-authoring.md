# Description

DOCX Authoring. Use this skill whenever the user wants a high-quality Word document (.docx).

# DOCX Authoring

Use this skill whenever the user wants a high-quality Word document (.docx). It adapts the best practices from the `docx-js` ecosystem to our Python/`python-docx` tool.

## When to use which input mode

The `write_docx` tool accepts two input modes:

| Mode | Use for | How to trigger |
|------|---------|----------------|
| **Markdown** | Simple documents: memo, note, short report, single-page content. | Pass regular Markdown in `content` and set `content_type="markdown"` (or leave it as `"auto"`). |
| **JSON** | Professional documents: reports with cover page, TOC, headers/footers, page numbers, tables, images, specific page setup. | Pass a JSON array of blocks in `content` and set `content_type="json"`. |

For complex/professional documents, always use **JSON mode**. It gives explicit control over structure and formatting.

## Page setup defaults

The tool uses these defaults unless overridden in `options`:

- Page size: **US Letter** (8.5" × 11")
- Margins: 1 inch on all sides
- Orientation: portrait
- Default font: Arial, 11 pt
- Default color: black

Always override for non-US documents if the user specifies A4 or landscape.

## JSON document blocks

`content` is a JSON array of block objects. Supported block types:

### `heading`

```json
{"type": "heading", "level": 1, "text": "Executive Summary"}
```

- `level`: 1–6. Use level 1 for the document title.
- Headings use built-in Word styles and are TOC-compatible.

### `paragraph`

```json
{"type": "paragraph", "text": "Revenue grew 15% year over year.", "alignment": "left"}
```

- `alignment`: `left` (default), `center`, `right`, `justify`.
- Inline Markdown formatting inside `text` is supported: `**bold**`, `*italic*`, `` `code` ``.

### `bullet_list`

```json
{"type": "bullet_list", "items": ["First item", "Second item"]}
```

- Uses real Word bullets. **Never** prepend Unicode bullet characters like `•`.

### `numbered_list`

```json
{"type": "numbered_list", "items": ["Step one", "Step two"]}
```

- Uses real Word numbering.

### `table`

```json
{
  "type": "table",
  "headers": ["Metric", "Value"],
  "rows": [
    ["Revenue", "$1M"],
    ["Growth", "15%"]
  ],
  "widths": [60, 40]
}
```

- `widths`: percentages that sum to 100. The tool converts them to DXA widths.
- All cells get consistent internal padding.
- Header row gets a light shaded background.
- **Never** use tables as visual dividers or rules.

### `image`

```json
{"type": "image", "path": "uploads/chart.png", "width": 5, "caption": "Figure 1"}
```

- `path`: relative to the agenthost project root, or absolute inside the project.
- `width`: width in inches.
- `caption` is optional.

### `page_break`

```json
{"type": "page_break"}
```

### `toc`

```json
{"type": "toc", "title": "Table of Contents", "levels": "1-3"}
```

- Inserts a Word TOC field. Word updates it automatically when the document is opened (or press F9).
- TOC only works if headings use the `heading` block type.

## Options JSON

Pass document-level settings as a JSON string in the `options` parameter:

```json
{
  "title": "Q3 Report",
  "author": "Finance Agent",
  "subject": "Quarterly financial summary",
  "header": "Company Confidential",
  "footer": "Page {page_number}",
  "page_size": "letter",
  "orientation": "portrait",
  "margins": {"top": 1, "bottom": 1, "left": 1, "right": 1}
}
```

- `page_size`: `"letter"` or `"a4"`.
- `orientation`: `"portrait"` or `"landscape"`.
- `margins`: values in inches.
- `{page_number}` in `footer` (or `header`) is replaced by a Word page-number field.

## Markdown mode syntax

Use standard Markdown:

- `# Heading` through `###### Heading`
- Paragraphs separated by blank lines
- `- item`, `* item`, `+ item` for bullets
- `1. item` for numbered lists
- `**bold**`, `*italic*`, `` `code` ``
- ```` ``` ```` fenced code blocks
- Markdown tables
- `![alt](path)` images (width defaults to 5 inches)
- `[text](url)` external hyperlinks
- `---` horizontal rule is rendered as a page break

## Critical rules

1. **Use real lists.** Never manually type `•` or other Unicode bullets.
2. **Use `heading` blocks for TOC.** A TOC only picks up proper headings.
3. **Set table widths.** In JSON mode, provide `widths` that sum to 100.
4. **Use `{page_number}` placeholder** in headers/footers; the tool converts it to a real field.
5. **Prefer JSON mode** for reports, letters, memos, and any document needing headers/footers or TOC.
6. **Keep captions brief.** Image captions are plain paragraphs below the image.
7. **Do not use tables as dividers.** Use a page break or a horizontal rule instead.

## Example: professional report

```json
[
  {"type": "heading", "level": 1, "text": "Q3 Financial Report"},
  {"type": "paragraph", "text": "Prepared by the Finance Agent on 2026-07-12.", "alignment": "center"},
  {"type": "page_break"},
  {"type": "toc", "title": "Table of Contents"},
  {"type": "page_break"},
  {"type": "heading", "level": 1, "text": "Executive Summary"},
  {"type": "paragraph", "text": "Revenue increased 15% year over year, driven by strong performance in APAC."},
  {"type": "heading", "level": 2, "text": "Key Metrics"},
  {"type": "table", "headers": ["Metric", "Q2", "Q3"], "rows": [["Revenue", "$8.5M", "$9.8M"], ["Growth", "12%", "15%"]], "widths": [40, 30, 30]}
]
```

Pass options:

```json
{"title": "Q3 Financial Report", "author": "Finance Agent", "header": "Company Confidential", "footer": "Page {page_number}"}
```
