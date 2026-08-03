"""Shared "coming soon" placeholder widget for empty Phase 2 modules.

Not part of this package's public interface (leading underscore) — a
small internal helper so the five module files in this package do not
each duplicate the same few lines of widget construction. Removed
entirely once every module has a real page to build in a later phase.
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QLabel, QVBoxLayout, QWidget


def build_placeholder_page(display_name_ar: str) -> QWidget:
    """Build a minimal "coming soon" page for a module with no logic yet.

    Args:
        display_name_ar: The module's Arabic display name, shown as
            the page heading.

    Returns:
        A centered, single-label placeholder widget.
    """
    page = QWidget()
    page.setObjectName("platformModulePlaceholder")

    layout = QVBoxLayout(page)
    layout.setAlignment(Qt.AlignmentFlag.AlignCenter)

    label = QLabel(f"{display_name_ar}\n\nقيد التطوير")
    label.setAlignment(Qt.AlignmentFlag.AlignCenter)
    layout.addWidget(label)

    return page
