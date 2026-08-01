"""CSV report export.

Every exporter in this package (``csv_export``, ``excel``, ``pdf``)
shares the same input shape: a list of row dictionaries plus an ordered
list of ``(field_key, header_label)`` column definitions, so
``services/report_service.py`` can build one column spec and hand it to
whichever format the user picked.
"""

from __future__ import annotations

import csv
from pathlib import Path
from typing import Any, Sequence


def export_to_csv(
    rows: Sequence[dict[str, Any]],
    columns: Sequence[tuple[str, str]],
    output_path: Path,
) -> Path:
    """Write ``rows`` to a CSV file at ``output_path``.

    Uses ``utf-8-sig`` encoding (a UTF-8 byte-order mark) rather than
    plain ``utf-8``: without the BOM, Excel on Windows — the deployment
    target for this application — misdetects the encoding and renders
    Arabic (and any other non-ASCII) text as mojibake when the file is
    double-clicked to open.

    Args:
        rows: Row data; each dict is keyed by the ``field_key`` from
            ``columns``. A missing key is written as an empty cell.
        columns: Ordered ``(field_key, header_label)`` pairs defining
            which fields to export and what to title each column.
        output_path: Destination file path; parent directories are
            created if needed.

    Returns:
        ``output_path``, for chaining.
    """
    output_path.parent.mkdir(parents=True, exist_ok=True)
    field_keys = [key for key, _label in columns]
    headers = [label for _key, label in columns]

    with output_path.open("w", newline="", encoding="utf-8-sig") as csv_file:
        writer = csv.writer(csv_file)
        writer.writerow(headers)
        for row in rows:
            writer.writerow([row.get(key, "") for key in field_keys])

    return output_path
