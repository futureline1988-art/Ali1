"""Developer Mode safety primitives.

Two pure, side-effect-free predicates — no bypass logic, no UI, no
wiring into ``main.py``'s actual startup flow. That integration (the
red "Developer Mode - Licensing Disabled" banner, actually skipping
:class:`~licensing.license_service.LicenseService`, and a CI assertion
that fails a Release build if Developer Mode is ever true in a frozen
artifact) is explicitly later-phase work — see
``docs/PLATFORM_ARCHITECTURE_GAP_ANALYSIS.md``'s Phase 6. What's here
now is the one thing worth getting right early and testing thoroughly
in isolation: the exact boolean logic that decides whether bypassing
the license check is *permitted at all*, since every later phase's
safety depends on this function alone, never a config flag trusted at
face value.
"""

from __future__ import annotations

import sys

from config import Environment


def is_frozen() -> bool:
    """Whether this process is running as a PyInstaller-frozen executable.

    PyInstaller sets ``sys.frozen = True`` on every bootloader-started
    process (both the onedir and onefile/Portable builds); it is never
    set when running via ``python main.py``. This is the one signal
    :func:`is_developer_mode_permitted` treats as absolute — Developer
    Mode must never activate in a frozen build regardless of any other
    setting, since a frozen build is by definition something that got
    packaged for distribution.

    Returns:
        ``True`` if this process is a PyInstaller-frozen executable.
    """
    return bool(getattr(sys, "frozen", False))


def is_developer_mode_permitted(environment: Environment) -> bool:
    """Whether Developer Mode is allowed to activate in the current process.

    Both conditions must hold:

    1. ``environment`` is :attr:`~config.Environment.DEVELOPMENT` —
       :attr:`~config.Environment.TESTING` and
       :attr:`~config.Environment.PRODUCTION` never permit it,
       regardless of how the process was started.
    2. :func:`is_frozen` is ``False`` — a packaged executable never
       permits it, regardless of ``environment``.

    This is a pure function precisely so it can be unit-tested against
    every combination of the two inputs without needing to actually
    build a PyInstaller executable — the later phase that wires this
    into ``main.py`` only has to trust that combination, not
    re-derive it.

    Args:
        environment: The application's configured runtime environment
            (:attr:`config.AppConfig.environment`).

    Returns:
        ``True`` only if Developer Mode may activate.
    """
    return environment is Environment.DEVELOPMENT and not is_frozen()
