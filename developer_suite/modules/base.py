"""The interface every Developer Suite platform module implements.

A "module" here is a top-level navigation destination (Customer
Management, License Manager, ...), not a Python package in the
generic sense — this is the seam a later phase's real business logic
plugs into without touching :mod:`developer_suite.ui.main_window` or
:mod:`developer_suite.ui.navigation`, both of which only ever depend
on this abstract interface.
"""

from __future__ import annotations

from abc import ABC, abstractmethod

from PySide6.QtWidgets import QWidget


class PlatformModule(ABC):
    """A single top-level section of the Developer Suite's navigation.

    Concrete modules in Phase 2 (see this package's other files) are
    empty placeholders: :meth:`build_page` returns a "coming soon"
    widget and nothing else exists yet. A later phase gives each one
    real business logic behind the same three properties and
    :meth:`build_page`, so the navigation/main-window wiring built in
    this phase never needs to change.
    """

    @property
    @abstractmethod
    def module_id(self) -> str:
        """A short, stable, unique identifier (e.g. ``"customer_management"``).

        Used as a dictionary key and Qt object name — never shown to
        the user and never changed once a module exists, since a later
        phase may persist "last selected module" against it.
        """
        raise NotImplementedError

    @property
    @abstractmethod
    def display_name_ar(self) -> str:
        """The Arabic label shown in the navigation sidebar."""
        raise NotImplementedError

    @property
    @abstractmethod
    def display_name_en(self) -> str:
        """The English label, for window titles and logs."""
        raise NotImplementedError

    @abstractmethod
    def build_page(self) -> QWidget:
        """Build this module's main content-area widget.

        Called once per module when the main window is constructed;
        the returned widget is added to the content stack and shown
        when this module is selected in the navigation sidebar.

        Returns:
            The module's root widget.
        """
        raise NotImplementedError
