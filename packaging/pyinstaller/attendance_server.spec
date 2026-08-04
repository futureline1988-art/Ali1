# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec: console-mode build of the Attendance Server.

Packages the FastAPI/uvicorn platform server (``server/main.py``) into
a standalone ``AttendanceServer.exe`` for an operator who wants to run
it on a Windows machine without installing Python -- consumed by
``packaging/installer/setup_attendance_server.iss`` (Inno Setup
packages the whole output folder into AttendanceServer-Setup.exe).
Build with:

    pyinstaller packaging/pyinstaller/attendance_server.spec --noconfirm

Run from the repository root. Mirrors ``main.spec``/``developer_suite.spec``
deliberately (same structure, same rationale for each choice) with two
differences: ``console=True`` (this is a server process an operator
watches for log output, not a windowed desktop app -- see
``server_bootstrap.py``'s module docstring for why that also means no
``MessageBoxW`` crash fallback is needed), and a trimmed dependency set
-- no PySide6 at all (zero UI), no bcrypt... wait, bcrypt *is* needed
(admin password hashing via utils.security, exercised by
server/services/admin_auth_service.py), but none of the Attendance
Client/Developer Suite's device-communication, QR/barcode, or
PDF/Excel reporting dependencies are.

One more dependency this server needs despite having no UI: importing
anything from ``models.base`` (see ``utils/security.py`` and every
``server/models/*.py`` file) runs ``models/__init__.py``, which -- to
guarantee every table is always registered on ``Base.metadata`` (see
that module's own docstring) -- eagerly imports every model in the
shared client/server ``models/`` package, including
``models.device.Device``. That model uses the shared
``models.encrypted_types.EncryptedString`` column type for its
encrypted-at-rest fields, which needs ``cryptography.fernet.Fernet``.
So although this server's own code never imports ``cryptography``
directly, it is a real, unavoidable transitive dependency -- omitting
it fails at import time with "ModuleNotFoundError: No module named
'cryptography'" (caught first by a real Windows-runner build, see
``build-attendance-server``'s "Build onedir app" and smoke-test steps).
"""

from pathlib import Path

from PyInstaller.utils.hooks import collect_all

block_cipher = None

# packaging/pyinstaller/attendance_server.spec -> repository root
PROJECT_ROOT = Path(SPECPATH).resolve().parent.parent

# bcrypt and cryptography both ship compiled/native extensions (C and
# Rust respectively). PyInstaller's built-in hooks are usually
# sufficient, but a missing transitive DLL is a classic cause of a
# frozen Windows build that fails at import time with no visible
# error -- collect_all() is the belt-and-suspenders option, same
# rationale main.spec/developer_suite.spec already apply to both.
_bcrypt_datas, _bcrypt_binaries, _bcrypt_hidden = collect_all("bcrypt")
_cryptography_datas, _cryptography_binaries, _cryptography_hidden = collect_all("cryptography")

datas = [
    *_bcrypt_datas,
    *_cryptography_datas,
]

hiddenimports = [
    # SQLAlchemy's SQLite dialect is loaded via a plugin-style string
    # lookup (create_engine("sqlite://...")), not a literal top-level
    # import, so PyInstaller's import scanner cannot discover it on
    # its own -- same rationale as the other two specs.
    "sqlalchemy.dialects.sqlite",
    # uvicorn.run() picks its event loop and HTTP/WebSocket protocol
    # implementations at runtime via importlib, based on what's
    # installed (uvicorn.loops.auto, uvicorn.protocols.http.auto,
    # uvicorn.protocols.websockets.auto each try a preferred backend
    # and fall back to asyncio/h11's pure-Python ones) -- none of
    # these are literal top-level imports uvicorn's own module ever
    # makes at parse time, so PyInstaller's static import scanner
    # cannot discover them, and a frozen build calling uvicorn.run()
    # fails at startup with "ModuleNotFoundError" pointing at whichever
    # of these it needed first. uvicorn.lifespan.on backs FastAPI's
    # startup/shutdown event hooks the same dynamic way.
    "uvicorn.logging",
    "uvicorn.loops",
    "uvicorn.loops.auto",
    "uvicorn.protocols",
    "uvicorn.protocols.http",
    "uvicorn.protocols.http.auto",
    "uvicorn.protocols.websockets",
    "uvicorn.protocols.websockets.auto",
    "uvicorn.lifespan",
    "uvicorn.lifespan.on",
    *_bcrypt_hidden,
    *_cryptography_hidden,
]

a = Analysis(
    # server_bootstrap.py, not server/main.py directly: a stdlib-only
    # guard that catches and reports (crash log + console traceback)
    # any exception raised while importing or running server/main.py --
    # including an import-time failure in that module itself. See
    # server_bootstrap.py's module docstring.
    [str(PROJECT_ROOT / "server_bootstrap.py")],
    pathex=[str(PROJECT_ROOT)],
    binaries=[*_bcrypt_binaries, *_cryptography_binaries],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        # This server has zero UI of its own -- PySide6 is never
        # imported anywhere under server/, unlike both desktop
        # applications' own specs which need it.
        "PySide6",
        # None of these are imported by server/ either -- see this
        # file's module docstring and main.spec's excludes list for
        # the shared rationale (smaller/faster build, no unrelated
        # failure modes).
        "matplotlib",
        "alembic",
        "psycopg2",
        "pymysql",
        "pandas",
        "pytest",
        "pytestqt",
        "tkinter",
        "pyzk",
        "zk",
        "qrcode",
        "barcode",
        "reportlab",
        "openpyxl",
        "arabic_reshaper",
        "bidi",
        # requests/httpx are what the *other* two applications use to
        # call this server -- this server never calls out to anyone,
        # so neither HTTP client library is imported under server/.
        "requests",
        "httpx",
        # chardet -- root cause of a real Windows startup crash
        # reproduced and diagnosed on this project's Attendance Client
        # build (see main.spec's excludes list for the full
        # rationale). Not imported here either, excluded defensively
        # for the same reason.
        "chardet",
    ],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="AttendanceServer",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    # Console, not windowed: an operator runs this and watches its log
    # output the same way `python -m server.main` already behaves in
    # development -- see this file's and server_bootstrap.py's module
    # docstrings for why that also removes the need for the two
    # desktop apps' windowed-build stdio/MessageBoxW workarounds.
    console=True,
    disable_windowed_traceback=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=str(PROJECT_ROOT / "assets" / "icons" / "app.ico"),
    version=str(Path(SPECPATH) / "attendance_server_version_info.txt"),
    manifest=str(Path(SPECPATH) / "attendance_server_app.manifest"),
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="AttendanceServer",
)
