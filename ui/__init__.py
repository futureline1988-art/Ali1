"""Desktop UI layer (PySide6): windows, views, and shared presentation code.

Every window/view in this package talks to the rest of the application
exclusively through the ``controllers`` layer — never directly to a
``service`` or ``repository`` — so the UI only ever sees plain dicts and
Qt signals, never a SQLAlchemy session or ORM object.
"""
