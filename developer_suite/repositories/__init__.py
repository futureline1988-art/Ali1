"""Developer Suite data-access layer.

Mirrors the Attendance Client's repository pattern
(``repositories/base_repository.py``) deliberately — a reader already
familiar with that module should recognize this one immediately. It is
not literally the same class: ``BaseRepository`` there is statically
typed against ``models.base.BaseModel``, which
:class:`~developer_suite.database.base.DeveloperSuiteBaseModel` does
not inherit (they compose the same mixins onto two different
declarative bases — see that module's docstring for why). The
generic CRUD *behavior* itself is duplicated here at the small,
mechanical level unavoidable for two independent schemas; the
meaningful, reusable pieces (mixins, enum helper) are not duplicated
— see :mod:`developer_suite.database.base`.
"""

from __future__ import annotations
