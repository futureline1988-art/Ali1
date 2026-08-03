"""Platform Server data-access layer.

Mirrors the Attendance Client's and Developer Suite's own repository
pattern (``repositories/base_repository.py``,
``developer_suite/repositories/base_repository.py``) deliberately — a
reader already familiar with either should recognize this one
immediately. Not literally the same class: ``BaseRepository`` there is
statically typed against a different declarative base, which
:class:`~server.database.base.ServerBaseModel` does not inherit (all
three compose the same mixins onto three different declarative
bases — see ``server/database/base.py``'s docstring for why). The
generic CRUD *behavior* itself is duplicated here at the small,
mechanical level unavoidable for a third independent schema; the
meaningful, reusable pieces (mixins, enum helper) are not duplicated —
see :mod:`server.database.base`.
"""

from __future__ import annotations
