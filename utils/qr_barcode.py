"""QR code and barcode generation for employee badges.

Both generators write a PNG file under
``config.PathsConfig.qrcodes_dir`` / ``barcodes_dir`` and return its
path — the caller (``services/employee_service.py``) is responsible for
storing that path in
:attr:`~models.employee.Employee.qr_code_path` /
:attr:`~models.employee.Employee.barcode_path`. Nothing here touches the
database.
"""

from __future__ import annotations

import re
from pathlib import Path

import barcode as barcode_lib
import qrcode
from barcode.writer import ImageWriter

from config import get_config

_SAFE_FILENAME_PATTERN = re.compile(r"[^A-Za-z0-9_-]+")


def _safe_filename(value: str) -> str:
    """Sanitize ``value`` into a filesystem-safe filename component.

    Deliberately excludes ``.`` from the allowed character set (every
    caller in this module appends its own extension separately) so a
    directory-traversal sequence like ``".."`` can never survive —
    each run of disallowed characters, including any dots, collapses
    to a single underscore.
    """
    sanitized = _SAFE_FILENAME_PATTERN.sub("_", value.strip()).strip("_")
    return sanitized or "code"


def generate_qr_code(
    data: str,
    *,
    file_stem: str,
    output_dir: Path | None = None,
    box_size: int = 10,
    border: int = 4,
) -> Path:
    """Generate a QR code image encoding ``data``.

    Args:
        data: The payload to encode (typically an employee's
            :attr:`~models.base.UUIDMixin.public_id` or
            :attr:`~models.employee.Employee.employee_number`).
        file_stem: Base filename (without extension); sanitized before
            use, so a raw employee number is safe to pass directly.
        output_dir: Directory to write into; defaults to
            :attr:`config.PathsConfig.qrcodes_dir`.
        box_size: Pixel size of each QR module.
        border: Width, in modules, of the quiet-zone border.

    Returns:
        The path to the generated PNG file.
    """
    resolved_dir = output_dir or get_config().paths.qrcodes_dir
    resolved_dir.mkdir(parents=True, exist_ok=True)

    qr = qrcode.QRCode(
        error_correction=qrcode.constants.ERROR_CORRECT_M,
        box_size=box_size,
        border=border,
    )
    qr.add_data(data)
    qr.make(fit=True)
    image = qr.make_image(fill_color="black", back_color="white")

    output_path = resolved_dir / f"{_safe_filename(file_stem)}.png"
    image.save(output_path)
    return output_path


def generate_barcode(
    data: str,
    *,
    file_stem: str,
    output_dir: Path | None = None,
    barcode_format: str = "code128",
) -> Path:
    """Generate a linear barcode image encoding ``data``.

    Args:
        data: The payload to encode. Defaults to Code128, which — unlike
            EAN/UPC — accepts arbitrary alphanumeric strings, matching
            free-form employee number schemes like ``"EMP-0042"``.
        file_stem: Base filename (without extension); sanitized before
            use.
        output_dir: Directory to write into; defaults to
            :attr:`config.PathsConfig.barcodes_dir`.
        barcode_format: Any format name from
            ``barcode.PROVIDED_BARCODES`` (e.g. ``"code128"``,
            ``"code39"``, ``"ean13"``).

    Returns:
        The path to the generated PNG file.

    Raises:
        ValueError: If ``barcode_format`` is not a recognized barcode
            symbology, or if ``data`` is not valid for the chosen
            symbology (e.g. non-numeric data with a numeric-only format
            like ``"ean13"``).
    """
    resolved_dir = output_dir or get_config().paths.barcodes_dir
    resolved_dir.mkdir(parents=True, exist_ok=True)

    try:
        barcode_class = barcode_lib.get_barcode_class(barcode_format)
    except barcode_lib.errors.BarcodeNotFoundError as exc:
        raise ValueError(f"Unknown barcode format: {barcode_format!r}") from exc

    try:
        code = barcode_class(data, writer=ImageWriter())
    except Exception as exc:  # python-barcode raises plain Exception subtypes
        raise ValueError(
            f"Data {data!r} is not valid for barcode format {barcode_format!r}: {exc}"
        ) from exc

    output_stem = resolved_dir / _safe_filename(file_stem)
    saved_path = code.save(str(output_stem), options={"write_text": False})
    return Path(saved_path)


def generate_employee_codes(
    *, employee_number: str, public_id: str
) -> tuple[Path, Path]:
    """Generate both the QR code and barcode for one employee.

    The QR code encodes the employee's globally-unique ``public_id``
    (safe to expose externally, unlike the internal auto-incrementing
    id — see :class:`~models.base.UUIDMixin`), while the barcode encodes
    the human-assigned, human-readable ``employee_number`` — the natural
    choice for a linear barcode meant to be typed/read at a glance.

    Args:
        employee_number: The employee's
            :attr:`~models.employee.Employee.employee_number`, used as
            both the barcode payload and the filename stem for both
            images.
        public_id: The employee's
            :attr:`~models.base.UUIDMixin.public_id`, used as the QR
            code payload.

    Returns:
        A ``(qr_code_path, barcode_path)`` tuple.
    """
    qr_path = generate_qr_code(public_id, file_stem=employee_number)
    barcode_path = generate_barcode(employee_number, file_stem=employee_number)
    return qr_path, barcode_path
