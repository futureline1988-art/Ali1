"""PyInstaller entry point: a minimal, dependency-free crash guard around main.py.

Every import in this file is Python stdlib only, deliberately - if
anything in the real application (PySide6, config.py, or any ui/ or
service/ module) fails to import in the frozen build, that failure
happens the instant ``import main`` runs below, and this file is the
first and only place positioned to catch it. Without this wrapper, an
import-time exception in a windowed (``console=False``) PyInstaller
build has nowhere to go: Python's default unhandled-exception
traceback prints to stderr, and a windowed build has no visible
stderr - the process just exits, with the user seeing literally
nothing. That silence is compounded by this build excluding
``tkinter`` (see ``main.spec``'s ``excludes``) to keep the build
smaller, which also removes the one toolkit some crash-reporting
fallbacks render with - this file's error dialog uses only the raw
Win32 ``MessageBoxW`` API instead, which has no such dependency.

``main.py`` itself is untouched and still perfectly runnable directly
(``python main.py``) for local development, where a normal traceback
printed to a real terminal is already good enough error reporting.
This wrapper is a purely defensive addition for the frozen, windowed
distribution, where that safety net doesn't otherwise exist.
"""

from __future__ import annotations

import datetime
import faulthandler
import os
import sys
import traceback
from pathlib import Path


def _enable_native_crash_diagnostics(log_path: Path) -> None:
    """Install a fatal-signal handler that can survive a native crash.

    A plain ``try/except`` around ``_run()`` only ever sees Python-level
    exceptions. A hard native crash -- an access violation from a
    misbehaving compiled extension (PySide6/Qt, cryptography's Rust
    bindings, bcrypt's C extension) or a corrupted/incompatible DLL --
    terminates the process at the OS level before Python's exception
    machinery ever runs, so it reaches neither the ``except`` clause
    below nor even Windows' own unhandled-exception dialog in a
    windowed build. ``faulthandler`` registers a SEH-based handler on
    Windows (and a signal handler elsewhere) that fires for exactly
    that case and prints the Python frame that was executing at the
    moment of the crash, which a plain exit code cannot show.
    """
    try:
        crash_fh = open(log_path, "a", encoding="utf-8")
        faulthandler.enable(file=crash_fh)
    except OSError:
        pass


def _crash_log_path() -> Path:
    """Where to write the crash log.

    Resolved independently of ``config.py``'s own path logic, since
    ``config.py`` failing to import or run is exactly one of the
    failures this must still be able to report.
    """
    base_env = os.environ.get("LOCALAPPDATA") or os.environ.get("TEMP") or os.environ.get("TMPDIR")
    base = Path(base_env) if base_env else Path.home()
    log_dir = base / "AttendanceManagementSystem" / "logs"
    try:
        log_dir.mkdir(parents=True, exist_ok=True)
        return log_dir / "startup_crash.log"
    except OSError:
        return Path.cwd() / "startup_crash.log"


def _report_fatal_startup_error(exc: BaseException) -> Path:
    """Write ``exc`` to the crash log and, on Windows, show a native message box.

    Last-resort error reporting for a failure so early that the
    application's own logging (``utils.logger.setup_logging``) or Qt
    itself may not be available yet.

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
                "Attendance Management System failed to start.\n\n"
                f"{exc.__class__.__name__}: {exc}\n\n"
                f"Details were written to:\n{log_path}"
            )
            ctypes.windll.user32.MessageBoxW(0, message, "Startup Error", 0x10)  # MB_ICONERROR
        except Exception:
            pass

    return log_path


def _run() -> int:
    """Import and run the real application, guarded end to end."""
    import main as app_main  # deliberately deferred -- see module docstring

    return app_main.main()


if __name__ == "__main__":
    _enable_native_crash_diagnostics(_crash_log_path())
    try:
        sys.exit(_run())
    except SystemExit:
        raise
    except BaseException as exc:  # noqa: BLE001 - last-resort top-level crash handler
        crash_log_path = _report_fatal_startup_error(exc)
        print(f"Fatal startup error - see {crash_log_path}", file=sys.stderr)
        sys.exit(1)
