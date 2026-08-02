"""Render this project's Markdown documentation into release-ready PDFs.

A small, purpose-built Markdown -> PDF renderer (headings, paragraphs,
bullet/numbered lists, tables, fenced code blocks, **bold**, `inline
code`, horizontal rules) — not a general CommonMark implementation,
just enough for the five documents this project ships. Reuses the exact
Arabic-capable font registration and RTL text shaping
(``utils.pdf.prepare_rtl_text``) already relied on for report PDF
export, so an embedded Arabic phrase inside an otherwise-English manual
renders correctly instead of as boxes or reversed glyphs.

Usage (from the repository root):

    python docs/build_pdfs.py

Regenerates every PDF next to its Markdown source in ``docs/`` (e.g.
``docs/user_manual.md`` -> ``docs/User Manual.pdf``). Run this after
editing any of the source Markdown files, then re-run
``packaging/build_all.bat`` (or copy the refreshed PDFs into ``Release/``
by hand) to ship the updated documentation.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path
from xml.sax.saxutils import escape as xml_escape

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from reportlab.lib import colors
from reportlab.lib.enums import TA_LEFT, TA_RIGHT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import cm
from reportlab.platypus import (
    HRFlowable,
    ListFlowable,
    ListItem,
    Paragraph,
    Preformatted,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

from utils.pdf import FONT_NAME, FONT_NAME_BOLD, _ensure_fonts_registered, prepare_rtl_text

_ARABIC_RE = re.compile(r"[\u0600-\u06FF]")
_BOLD_RE = re.compile(r"\*\*(.+?)\*\*")
_INLINE_CODE_RE = re.compile(r"`([^`]+)`")
_MARKDOWN_LINK_RE = re.compile(r"\[([^\]]+)\]\([^)]+\)")


def _strip_markdown_links(text: str) -> str:
    """Reduce ``[label](target)`` to just ``label`` (e.g. a table-of-contents entry).

    This renderer has no PDF hyperlink support, so keeping the target
    would only show confusing raw Markdown syntax on the page.
    """
    return _MARKDOWN_LINK_RE.sub(r"\1", text)


def _is_mostly_arabic(text: str) -> bool:
    """Heuristic: does ``text`` read more naturally right-to-left?"""
    letters = re.findall(r"[^\W\d_]", text, flags=re.UNICODE)
    if not letters:
        return False
    arabic_count = sum(1 for ch in letters if _ARABIC_RE.match(ch))
    return arabic_count > len(letters) / 2


def _inline_to_reportlab_markup(text: str) -> str:
    """Convert a line's inline Markdown (``**bold**``, `` `code` ``) to
    reportlab Paragraph XML, reshaping/reordering each plain-text
    segment for RTL independently so inserted tags are never exposed to
    the bidi algorithm.
    """

    def render_segment(segment: str) -> str:
        return prepare_rtl_text(xml_escape(segment))

    text = _strip_markdown_links(text)

    # Split on bold spans first, keeping the delimiters via capture group.
    parts = _BOLD_RE.split(text)
    rendered = []
    for index, part in enumerate(parts):
        is_bold = index % 2 == 1
        # Within each part, also handle inline code spans.
        code_parts = _INLINE_CODE_RE.split(part)
        piece = ""
        for code_index, code_part in enumerate(code_parts):
            if code_index % 2 == 1:
                # Courier (true monospace) is what makes inline code visually
                # distinct from body text; only fall back to DejaVuSans (no
                # monospace look, but full Unicode coverage) for the rare
                # code span with a non-ASCII character Courier can't render.
                face = "Courier" if code_part.isascii() else FONT_NAME
                piece += f'<font face="{face}">{render_segment(code_part)}</font>'
            else:
                piece += render_segment(code_part)
        rendered.append(f"<b>{piece}</b>" if is_bold else piece)
    return "".join(rendered)


def _paragraph_style(*, bold: bool = False, size: int = 10, extra: dict | None = None) -> ParagraphStyle:
    kwargs = dict(
        fontName=FONT_NAME_BOLD if bold else FONT_NAME,
        fontSize=size,
        leading=size * 1.45,
    )
    if extra:
        kwargs.update(extra)
    return ParagraphStyle("Body", **kwargs)


def _make_paragraph(text: str, *, size: int = 10, bold: bool = False) -> Paragraph:
    alignment = TA_RIGHT if _is_mostly_arabic(text) else TA_LEFT
    style = _paragraph_style(bold=bold, size=size, extra={"alignment": alignment})
    return Paragraph(_inline_to_reportlab_markup(text), style)


def _parse_table(lines: list[str], start: int) -> tuple[list[list[str]], int]:
    """Parse a GitHub-style Markdown table starting at ``lines[start]``."""
    rows: list[list[str]] = []
    index = start
    while index < len(lines) and lines[index].strip().startswith("|"):
        raw = lines[index].strip()
        if index == start + 1 and re.fullmatch(r"\|[\s:\-|]+\|?", raw):
            index += 1
            continue
        cells = [cell.strip() for cell in raw.strip("|").split("|")]
        rows.append(cells)
        index += 1
    return rows, index


def _build_story(markdown_text: str, *, doc_title: str) -> list:
    _ensure_fonts_registered()
    lines = markdown_text.splitlines()
    story: list = []

    title_style = _paragraph_style(bold=True, size=20, extra={"alignment": TA_LEFT, "spaceAfter": 4})
    story.append(Paragraph(xml_escape(doc_title), title_style))
    story.append(HRFlowable(width="100%", thickness=1, color=colors.HexColor("#1F4E78")))
    story.append(Spacer(1, 0.5 * cm))

    in_code_block = False
    code_lines: list[str] = []
    index = 0
    first_h1_skipped = False

    while index < len(lines):
        line = lines[index]
        stripped = line.strip()

        if stripped.startswith("```"):
            if not in_code_block:
                in_code_block = True
                code_lines = []
            else:
                in_code_block = False
                code_text = "\n".join(code_lines)
                # Courier (true monospace) renders real command examples best,
                # but has no box-drawing glyphs (├── └── │ etc., used by a few
                # file-tree diagrams) -- those fall back to DejaVuSans, which
                # has full Unicode coverage, at the cost of losing perfect
                # monospace column alignment for that one block.
                has_box_drawing = any(0x2500 <= ord(ch) <= 0x257F for ch in code_text)
                code_style = ParagraphStyle(
                    "Code",
                    fontName=FONT_NAME if has_box_drawing else "Courier",
                    fontSize=8.5,
                    leading=11,
                    backColor=colors.HexColor("#F4F4F4"),
                    borderPadding=6,
                )
                story.append(Preformatted(code_text, code_style))
                story.append(Spacer(1, 0.3 * cm))
            index += 1
            continue
        if in_code_block:
            code_lines.append(line)
            index += 1
            continue

        if not stripped:
            index += 1
            continue

        if stripped.startswith("| ") or stripped.startswith("|-") or (
            stripped.startswith("|") and stripped.endswith("|")
        ):
            rows, index = _parse_table(lines, index)
            if rows:
                cell_style = _paragraph_style(size=8.5)
                header_style = _paragraph_style(bold=True, size=8.5, extra={"textColor": colors.white})
                table_data = [
                    [Paragraph(_inline_to_reportlab_markup(cell), header_style) for cell in rows[0]]
                ]
                for row in rows[1:]:
                    table_data.append(
                        [Paragraph(_inline_to_reportlab_markup(cell), cell_style) for cell in row]
                    )
                table = Table(table_data, repeatRows=1, hAlign="LEFT")
                table.setStyle(
                    TableStyle(
                        [
                            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#1F4E78")),
                            ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#B0B0B0")),
                            (
                                "ROWBACKGROUNDS", (0, 1), (-1, -1),
                                [colors.white, colors.HexColor("#F2F2F2")],
                            ),
                            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                            ("TOPPADDING", (0, 0), (-1, -1), 4),
                            ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
                            ("LEFTPADDING", (0, 0), (-1, -1), 5),
                            ("RIGHTPADDING", (0, 0), (-1, -1), 5),
                        ]
                    )
                )
                story.append(table)
                story.append(Spacer(1, 0.35 * cm))
            continue

        if re.fullmatch(r"-{3,}", stripped):
            story.append(HRFlowable(width="100%", thickness=0.5, color=colors.HexColor("#B0B0B0")))
            story.append(Spacer(1, 0.2 * cm))
            index += 1
            continue

        heading_match = re.match(r"(#{1,4})\s+(.*)", stripped)
        if heading_match:
            level = len(heading_match.group(1))
            heading_text = heading_match.group(2)
            if level == 1 and not first_h1_skipped:
                # The document's own H1 duplicates doc_title already rendered above.
                first_h1_skipped = True
                index += 1
                continue
            size = {1: 18, 2: 15, 3: 12.5, 4: 11}.get(level, 10.5)
            story.append(Spacer(1, 0.35 * cm))
            story.append(_make_paragraph(heading_text, size=size, bold=True))
            story.append(Spacer(1, 0.15 * cm))
            index += 1
            continue

        list_match = re.match(r"([-*]|\d+\.)\s+(.*)", stripped)
        if list_match:
            items = []
            ordered = list_match.group(1) not in ("-", "*")
            while index < len(lines):
                current = lines[index].strip()
                match = re.match(r"([-*]|\d+\.)\s+(.*)", current)
                if not match:
                    break
                item_lines = [match.group(2)]
                index += 1
                # Fold soft-wrapped continuation lines (indented, no marker
                # of their own) into this same list item, exactly like a
                # plain paragraph's continuation lines below.
                while index < len(lines) and lines[index].strip() and not re.match(
                    r"(#{1,4}\s|[-*]\s|\d+\.\s|```|\|)", lines[index].strip()
                ):
                    item_lines.append(lines[index].strip())
                    index += 1
                items.append(_make_paragraph(" ".join(item_lines), size=10))
            bullet_type = "1" if ordered else "bullet"
            story.append(
                ListFlowable(
                    [ListItem(item, spaceBefore=2) for item in items],
                    bulletType=bullet_type,
                    leftIndent=16,
                )
            )
            story.append(Spacer(1, 0.25 * cm))
            continue

        # Plain paragraph: accumulate contiguous non-blank, non-special lines.
        paragraph_lines = [stripped]
        index += 1
        while index < len(lines) and lines[index].strip() and not re.match(
            r"(#{1,4}\s|[-*]\s|\d+\.\s|```|\|)", lines[index].strip()
        ):
            paragraph_lines.append(lines[index].strip())
            index += 1
        story.append(_make_paragraph(" ".join(paragraph_lines), size=10))
        story.append(Spacer(1, 0.2 * cm))

    return story


def render_markdown_to_pdf(markdown_path: Path, output_path: Path, *, title: str) -> Path:
    """Render one Markdown file to a PDF at ``output_path``."""
    markdown_text = markdown_path.read_text(encoding="utf-8")
    story = _build_story(markdown_text, doc_title=title)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    document = SimpleDocTemplate(
        str(output_path),
        pagesize=A4,
        leftMargin=2 * cm,
        rightMargin=2 * cm,
        topMargin=2 * cm,
        bottomMargin=2 * cm,
        title=title,
    )
    document.build(story)
    return output_path


_TARGETS: list[tuple[Path, Path, str]] = [
    (PROJECT_ROOT / "README.md", PROJECT_ROOT / "docs" / "README.pdf", "README / نظرة عامة"),
    (
        PROJECT_ROOT / "docs" / "user_manual.md",
        PROJECT_ROOT / "docs" / "User Manual.pdf",
        "User Manual",
    ),
    (
        PROJECT_ROOT / "docs" / "administrator_manual.md",
        PROJECT_ROOT / "docs" / "Administrator Manual.pdf",
        "Administrator Manual",
    ),
    (
        PROJECT_ROOT / "docs" / "installation_guide.md",
        PROJECT_ROOT / "docs" / "Installation Guide.pdf",
        "Installation Guide",
    ),
    (
        PROJECT_ROOT / "docs" / "release_notes.md",
        PROJECT_ROOT / "docs" / "Release Notes.pdf",
        "Release Notes",
    ),
]


def main() -> None:
    for source, destination, title in _TARGETS:
        render_markdown_to_pdf(source, destination, title=title)
        print(f"Wrote {destination.relative_to(PROJECT_ROOT)}")


if __name__ == "__main__":
    main()
