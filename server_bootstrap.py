"""PyInstaller entry point: a minimal, dependency-free crash guard around server/main.py.

The Attendance Server's own counterpart to ``bootstrap.py``/
``developer_suite_bootstrap.py`` -- same "catch and report any
exception raised while importing or running the real application"
purpose, but simpler in one respect: this is a **console-mode**
PyInstaller build (``console=True`` in
``packaging/pyinstaller/attendance_server.spec``), unlike the two
windowed desktop apps. A console build always has real, attached
``sys.stdout``/``sys.stderr`` handles, so neither
``_ensure_stdio_streams()`` nor a native ``MessageBoxW`` fallback is
needed here -- a plain traceback printed to stderr is already visible
in the console window the operator is watching.

What a console build *does* still need: if this .exe was started by
double-clicking it in Explorer (rather than from an already-open
terminal), Windows opens a brand-new console window for it -- and that
window closes itself the instant the process exits, including on a
crash. Without the pause at the bottom of this file, a startup crash
would flash a traceback on screen for a fraction of a second and then
vanish, leaving the operator with no way to read it. The crash log
file is the reliable record either way.

``server/main.py`` itself is untouched and still perfectly runnable
directly (``python -m server.main``) for local development.
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
    exceptions. A hard native crash -- an access violation from bcrypt's
    compiled C extension, or a corrupted/incompatible DLL -- terminates
    the process at the OS level before Python's exception machinery
    ever runs. ``faulthandler`` registers a handler that fires for
    exactly that case and prints the Python frame that was executing at
    the moment of the crash, which a plain exit code cannot show.
    """
    try:
        crash_fh = open(log_path, "a", encoding="utf-8")
        faulthandler.enable(file=crash_fh)
    except OSError:
        pass


def _crash_log_path() -> Path:
    """Where to write the crash log.

    Resolved independently of ``server/config.py``'s own path logic,
    since that module failing to import or run is exactly one of the
    failures this must still be able to report. Uses ``AttendanceServer``
    as the folder name -- matching ``server.config._resolve_data_root()``'s
    frozen-build path -- so a startup crash and a normal run's own logs
    end up in the same place.
    """
    base_env = os.environ.get("LOCALAPPDATA") or os.environ.get("TEMP") or os.environ.get("TMPDIR")
    base = Path(base_env) if base_env else Path.home()
    log_dir = base / "AttendanceServer" / "logs"
    try:
        log_dir.mkdir(parents=True, exist_ok=True)
        return log_dir / "startup_crash.log"
    except OSError:
        return Path.cwd() / "startup_crash.log"


def _report_fatal_startup_error(exc: BaseException) -> Path:
    """Write ``exc`` to the crash log and print it to the (real, attached) console.

    Returns:
        The log file path.
    """
    log_path = _crash_log_path()
    text = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))
    timestamp = datetime.datetime.now(datetime.timezone.utc).isoformat()
    try:
        with open(log_path, "a", encoding="utf-8") as log_file:
            log_file.write(f"\n=== Fatal startup error at {timestamp} ===\n{text}")
    except OSError:
        pass

    print("=" * 70, file=sys.stderr)
    print("Attendance Server failed to start.", file=sys.stderr)
    print(text, file=sys.stderr)
    print(f"Details were also written to: {log_path}", file=sys.stderr)
    print("=" * 70, file=sys.stderr)
    return log_path


def _run() -> int:
    """Import and run the real application, guarded end to end."""
    import server.main as app_main  # deliberately deferred -- see module docstring

    return app_main.main()


if __name__ == "__main__":
    _enable_native_crash_diagnostics(_crash_log_path())
    try:
        sys.exit(_run())
    except SystemExit:
        raise
    except KeyboardInterrupt:
        # Ctrl+C is the normal way to stop this server; not a crash.
        sys.exit(0)
    except BaseException as exc:  # noqa: BLE001 - last-resort top-level crash handler
        _report_fatal_startup_error(exc)
        try:
            input("Press Enter to exit...")
        except (EOFError, OSError):
            pass  # no interactive console attached (e.g. launched by a service manager)
        sys.exit(1)
