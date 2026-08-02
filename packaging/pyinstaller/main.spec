# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec: onedir build of the Attendance Management System.

This is the build consumed by ``packaging/installer/setup.iss`` (Inno
Setup packages the whole output folder into Setup.exe). Build with:

    pyinstaller packaging/pyinstaller/main.spec --noconfirm

Run from the repository root so the relative paths below resolve
correctly. See ``BUILD_WINDOWS.md`` for the full, copy-pasteable
Windows build procedure.
"""

import sys
from pathlib import Path

from PyInstaller.utils.hooks import collect_all

block_cipher = None

# packaging/pyinstaller/main.spec -> repository root
PROJECT_ROOT = Path(SPECPATH).resolve().parent.parent

# cryptography and bcrypt both ship compiled/native extensions (Rust and C
# respectively). PyInstaller's built-in hooks for them are usually
# sufficient, but a missing transitive DLL from either is a classic cause
# of a frozen Windows build that fails at import time with no visible
# error (see bootstrap.py) -- collect_all() is the belt-and-suspenders
# option: it pulls in every submodule, binary, and data file each package
# itself declares, rather than relying solely on hook-level detection.
_cryptography_datas, _cryptography_binaries, _cryptography_hidden = collect_all("cryptography")
_bcrypt_datas, _bcrypt_binaries, _bcrypt_hidden = collect_all("bcrypt")

datas = [
    (str(PROJECT_ROOT / "assets"), "assets"),
    *_cryptography_datas,
    *_bcrypt_datas,
]

hiddenimports = [
    # PySide6 plugins/submodules PyInstaller's static analysis can miss.
    "PySide6.QtSvg",
    "PySide6.QtPrintSupport",
    # ui/dashboard_page.py imports this directly, so Analysis should already
    # find it -- listed explicitly anyway so the dashboard's executive
    # charts (attendance trend, department breakdown) are never silently
    # dropped from a build by an unrelated refactor of that import.
    "PySide6.QtCharts",
    # SQLAlchemy's SQLite dialect is loaded via a plugin-style string
    # lookup (create_engine("sqlite://...")), not a literal top-level
    # import, so PyInstaller's import scanner cannot discover it on its
    # own.
    "sqlalchemy.dialects.sqlite",
    # devices/zkteco_device.py imports these lazily inside a method body
    # (only once a ZKTeco device is actually used), which PyInstaller's
    # bytecode scan usually catches but is not guaranteed to -- listed
    # explicitly to be safe. Harmless no-op if pyzk isn't installed in
    # the build venv (see requirements-runtime.txt).
    "zk",
    "zk.exception",
    # reportlab's PDF encoding/font-metrics tables are looked up
    # dynamically by name rather than imported directly.
    "reportlab.pdfbase._fontdata",
    "reportlab.pdfbase._fontdata_enc_winansi",
    "reportlab.pdfbase._fontdata_widths_helvetica",
    "reportlab.pdfbase._fontdata_widths_helveticabold",
    "reportlab.pdfbase._fontdata_widths_helveticaoblique",
    "reportlab.pdfbase._fontdata_widths_helveticaboldoblique",
    # python-barcode picks its writer backend (image vs. SVG) dynamically.
    "barcode.writer",
    *_cryptography_hidden,
    *_bcrypt_hidden,
]

a = Analysis(
    # bootstrap.py, not main.py directly: a stdlib-only guard that catches
    # and reports (crash log + native message box) any exception raised
    # while importing or running main.py -- including an import-time
    # failure in main.py itself, which is otherwise invisible in a
    # windowed (console=False) build. See bootstrap.py's module docstring.
    [str(PROJECT_ROOT / "bootstrap.py")],
    pathex=[str(PROJECT_ROOT)],
    binaries=[*_cryptography_binaries, *_bcrypt_binaries],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        # Declared in requirements.txt for possible future use but not
        # imported anywhere in the shipped app today (see
        # requirements-runtime.txt's header comment for the full
        # rationale) -- excluded to keep the build smaller and faster.
        # apscheduler is deliberately NOT here: services/scheduler_service.py
        # genuinely imports it for automatic device sync and backups, so
        # excluding it would break the frozen build at runtime. fastapi/
        # uvicorn stay excluded -- they back run_api.py, a separate optional
        # process main.py never imports, so the desktop build has no need
        # for them.
        "matplotlib",
        "fastapi",
        "uvicorn",
        "alembic",
        "psycopg2",
        "pymysql",
        "pandas",
        "pytest",
        "pytestqt",
        "tkinter",
        # Root cause of the real Windows startup crash (reproduced and
        # diagnosed on a windows-latest CI runner via faulthandler --
        # see bootstrap.py and windows-release.yml's smoke-test steps):
        # chardet ships mypyc-compiled native extension modules, and an
        # unpinned transitive install of it crashed the frozen build
        # with STATUS_ACCESS_VIOLATION the instant chardet\detector.py
        # was imported. Nothing in this app imports chardet directly --
        # requests.compat._resolve_char_detection() only tries it as an
        # optional fallback and already falls back to charset_normalizer
        # (requests' real, pinned dependency) via its own
        # `except ImportError: pass`, which is the exact code path every
        # Linux build/test in this repo has been exercising all along
        # (chardet isn't installed there either). Excluding it here
        # makes that fallback the guaranteed behavior on Windows too,
        # instead of depending on whichever chardet version pip happens
        # to resolve transitively at build time.
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
    name="AttendanceManagementSystem",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=str(PROJECT_ROOT / "assets" / "icons" / "app.ico"),
    version=str(Path(SPECPATH) / "version_info.txt"),
    manifest=str(Path(SPECPATH) / "app.manifest"),
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="AttendanceManagementSystem",
)
