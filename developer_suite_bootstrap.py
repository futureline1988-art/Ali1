"""PyInstaller entry point: a minimal, dependency-free crash guard around developer_suite/main.py.

The Developer Suite's own counterpart to the Attendance Client's
``bootstrap.py`` -- same rationale, same mechanism, duplicated rather
than shared because the two applications are built, versioned, and
distributed as fully independent PyInstaller executables (see
``packaging/pyinstaller/developer_suite.spec`` vs. ``main.spec``).

Every import in this file is Python stdlib only, deliberately - if
anything in the real application (PySide6, developer_suite/config.py,
or any developer_suite/ui or developer_suite/services module) fails to
import in the frozen build, that failure happens the instant
``import developer_suite.main`` runs below, and this file is the first
and only place positioned to catch it. Without this wrapper, an
import-time exception in a windowed (``console=False``) PyInstaller
build has nowhere to go: Python's default unhandled-exception
traceback prints to stderr, and a windowed build has no visible
stderr - the process just exits, with the user seeing literally
nothing.

``developer_suite/main.py`` itself is untouched and still perfectly
runnable directly (``python -m developer_suite.main``) for local
development, where a normal traceback printed to a real terminal is
already good enough error reporting. This wrapper is a purely
defensive addition for the frozen, windowed distribution, where that
safety net doesn't otherwise exist.
"""

from __future__ import annotations

import datetime
import faulthandler
import os
import sys
import traceback
from pathlib import Path


def _ensure_stdio_streams() -> None:
    """Give ``sys.stdout``/``sys.stderr`` real (if inert) streams if missing.

    See ``bootstrap.py``'s identical helper for the full explanation:
    a windowed PyInstaller build launched with no attached console has
    both streams set to ``None``, which crashes any code (including
    this app's own logging setup) that unconditionally writes to one
    of them.
    """
    if sys.stdout is None:
        sys.stdout = open(os.devnull, "w", encoding="utf-8")
    if sys.stderr is None:
        sys.stderr = open(os.devnull, "w", encoding="utf-8")


def _enable_native_crash_diagnostics(log_path: Path) -> None:
    """Install a fatal-signal handler that can survive a native crash.

    See ``bootstrap.py``'s identical helper -- same rationale, applied
    to this application's own crash log.
    """
    try:
        crash_fh = open(log_path, "a", encoding="utf-8")
        faulthandler.enable(file=crash_fh)
    except OSError:
        pass


def _crash_log_path() -> Path:
    """Where to write the crash log.

    Resolved independently of ``developer_suite/config.py``'s own path
    logic, since that module failing to import or run is exactly one
    of the failures this must still be able to report. Uses
    ``DeveloperSuite`` as the folder name -- matching
    ``developer_suite.config._resolve_data_root()`` -- so a user
    pointed at "check the logs folder" finds this file in the same
    place as everything else the running application writes.
    """
    base_env = os.environ.get("LOCALAPPDATA") or os.environ.get("TEMP") or os.environ.get("TMPDIR")
    base = Path(base_env) if base_env else Path.home()
    log_dir = base / "DeveloperSuite" / "logs"
    try:
        log_dir.mkdir(parents=True, exist_ok=True)
        return log_dir / "startup_crash.log"
    except OSError:
        return Path.cwd() / "startup_crash.log"


def _report_fatal_startup_error(exc: BaseException) -> Path:
    """Write ``exc`` to the crash log and, on Windows, show a native message box.

    Last-resort error reporting for a failure so early that the
    application's own logging or Qt itself may not be available yet.

    Returns:
        The log file path, so the caller can also print it (harmless
        even when there is no visible console to print to).
    """
    log_path = _crash_log_path()
    text = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))
    timestamp = datetime.datetime.now(datetime.timezone.utc).isoformat()
    try:
        with open(log_path, "a", encoding="utf-8") as log_file:
            log_file.write(f"\n=== Fatal startup error at {timestamp} ===\n{text}")
    except OSError:
        pass

    if sys.platform == "win32":
        try:
            import ctypes

            message = (
                "Developer Suite failed to start.\n\n"
                f"{exc.__class__.__name__}: {exc}\n\n"
                f"Details were written to:\n{log_path}"
            )
            ctypes.windll.user32.MessageBoxW(0, message, "Startup Error", 0x10)  # MB_ICONERROR
        except Exception:
            pass

    return log_path


def _run() -> int:
    """Import and run the real application, guarded end to end."""
    from developer_suite.main import main as app_main

    return app_main()


if __name__ == "__main__":
    _ensure_stdio_streams()
    _enable_native_crash_diagnostics(_crash_log_path())
    try:
        sys.exit(_run())
    except SystemExit:
        raise
    except BaseException as exc:  # noqa: BLE001 - last-resort top-level crash handler
        crash_log_path = _report_fatal_startup_error(exc)
        try:
            print(f"Fatal startup error - see {crash_log_path}", file=sys.stderr)
        except OSError:
            pass  # never let the crash reporter itself crash
        sys.exit(1)
