"""Developer Suite security primitives: at-rest encryption for its own sensitive columns.

See :mod:`developer_suite.security.field_encryption` for why this is a
small, independent module rather than a reuse of :mod:`utils.encryption`.
"""

from __future__ import annotations
