"""PDF report export with correct Arabic (RTL) text rendering.

reportlab has no built-in Arabic text shaping or bidirectional
reordering — its canvas always draws glyphs left-to-right in the exact
sequence given. Two extra steps make Arabic render correctly:

1. :func:`prepare_rtl_text` reshapes logical Arabic characters into
   their contextual (initial/medial/final/isolated) presentation forms
   via ``arabic_reshaper``, then reorders the string into visual order
   via ``python-bidi``'s implementation of the Unicode Bidirectional
   Algorithm.
2. The bundled ``DejaVuSans`` font (see ``assets/fonts/``) is
   registered with reportlab, since the default PDF base fonts
   (Helvetica, Times) have no Arabic glyphs at all.

Shares the ``rows`` / ``(field_key, header_label)`` column-spec shape
used by :mod:`utils.csv_export` and :mod:`utils.excel`.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Sequence
from xml.sax.saxutils import escape as xml_escape

import arabic_reshaper
from bidi.algorithm import get_display
from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_LEFT, TA_RIGHT
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import cm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from config import get_config

FONT_NAME = "DejaVuSans"
FONT_NAME_BOLD = "DejaVuSans-Bold"

_fonts_registered = False


def _ensure_fonts_registered() -> None:
    """Register the bundled Arabic-capable font with reportlab.

    Idempotent: reportlab raises if the same font name is registered
    twice, so this only performs the registration once per process.
    """
    global _fonts_registered
    if _fonts_registered:
        return
    fonts_dir = get_config().paths.assets_dir / "fonts"
    pdfmetrics.registerFont(TTFont(FONT_NAME, str(fonts_dir / "DejaVuSans.ttf")))
    pdfmetrics.registerFont(TTFont(FONT_NAME_BOLD, str(fonts_dir / "DejaVuSans-Bold.ttf")))
    _fonts_registered = True


def prepare_rtl_text(text: Any) -> str:
    """Reshape and bidi-reorder text for reportlab's left-to-right-only canvas.

    Safe to call on any value, including pure-Latin/digit text —
    ``arabic_reshaper`` only transforms Arabic-script characters, and
    the Unicode Bidi Algorithm is a correct no-op for already-LTR text.

    Args:
        text: Raw logical-order value (converted to ``str`` first, so
            numbers/dates from a row dict are accepted directly).

    Returns:
        Text reordered into visual glyph order, ready to draw as-is.
    """
    reshaped = arabic_reshaper.reshape(str(text))
    return get_display(reshaped)


def _cell_paragraph(value: Any, style: ParagraphStyle) -> Paragraph:
    """Build a table-cell Paragraph: XML-escape, then shape/reorder for RTL."""
    return Paragraph(prepare_rtl_text(xml_escape(str(value))), style)


def export_to_pdf(
    rows: Sequence[dict[str, Any]],
    columns: Sequence[tuple[str, str]],
    output_path: Path,
    *,
    title: str,
    subtitle: str | None = None,
    rtl: bool = True,
    landscape_orientation: bool = True,
) -> Path:
    """Render ``rows`` as a titled table in a PDF file at ``output_path``.

    Args:
        rows: Row data; each dict is keyed by the ``field_key`` from
            ``columns``. A missing key is rendered as an empty cell.
        columns: Ordered ``(field_key, header_label)`` pairs defining
            which fields to export and what to title each column.
        output_path: Destination file path; parent directories are
            created if needed.
        title: Report title, printed centered at the top of the page.
        subtitle: Optional secondary line under the title (e.g. a date
            range or company name).
        rtl: Whether to lay the table out right-to-left (the first
            entry in ``columns`` becomes the rightmost table column,
            matching natural Arabic reading order) and right-align cell
            text. PDF pages have no native text-direction concept the
            way HTML/Excel do, so this is implemented by physically
            reversing the column order.
        landscape_orientation: Whether to use landscape A4 (usually
            needed for attendance tables with many columns) instead of
            portrait.

    Returns:
        ``output_path``, for chaining.
    """
    _ensure_fonts_registered()
    output_path.parent.mkdir(parents=True, exist_ok=True)

    page_size = landscape(A4) if landscape_orientation else A4
    document = SimpleDocTemplate(
        str(output_path),
        pagesize=page_size,
        leftMargin=1.5 * cm,
        rightMargin=1.5 * cm,
        topMargin=1.5 * cm,
        bottomMargin=1.5 * cm,
    )

    body_alignment = TA_RIGHT if rtl else TA_LEFT
    title_style = ParagraphStyle(
        "ReportTitle",
        fontName=FONT_NAME_BOLD,
        fontSize=16,
        alignment=TA_CENTER,
        spaceAfter=6,
    )
    subtitle_style = ParagraphStyle(
        "ReportSubtitle",
        fontName=FONT_NAME,
        fontSize=10,
        alignment=TA_CENTER,
        textColor=colors.grey,
        spaceAfter=12,
    )
    header_style = ParagraphStyle(
        "TableHeader",
        fontName=FONT_NAME_BOLD,
        fontSize=10,
        alignment=TA_CENTER,
        textColor=colors.white,
    )
    cell_style = ParagraphStyle(
        "TableCell", fontName=FONT_NAME, fontSize=9, alignment=body_alignment
    )

    story: list[Any] = [Paragraph(prepare_rtl_text(title), title_style)]
    if subtitle:
        story.append(Paragraph(prepare_rtl_text(subtitle), subtitle_style))
    else:
        story.append(Spacer(1, 0.4 * cm))

    ordered_columns = list(reversed(columns)) if rtl else list(columns)
    field_keys = [key for key, _label in ordered_columns]

    header_row = [_cell_paragraph(label, header_style) for _key, label in ordered_columns]
    table_data: list[list[Any]] = [header_row]
    for row in rows:
        table_data.append(
            [_cell_paragraph(row.get(key, ""), cell_style) for key in field_keys]
        )

    table = Table(table_data, repeatRows=1)
    table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#1F4E78")),
                ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#B0B0B0")),
                (
                    "ROWBACKGROUNDS",
                    (0, 1),
                    (-1, -1),
                    [colors.white, colors.HexColor("#F2F2F2")],
                ),
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                ("TOPPADDING", (0, 0), (-1, -1), 6),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
            ]
        )
    )
    story.append(table)

    document.build(story)
    return output_path
