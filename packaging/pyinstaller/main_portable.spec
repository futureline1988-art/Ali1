# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec: onefile "Portable.exe" build.

A single self-contained executable for users who want to run the app
from a USB stick or a folder without running Setup.exe. Functionally
identical to the onedir build (``main.spec``) -- same code, same
hidden-imports, same bundled assets -- just packed into one file that
self-extracts to a temp directory (``sys._MEIPASS``) on each launch.

Because that temp directory is thrown away between runs, this build
relies on the same ``config._resolve_data_root()`` logic as the onedir
build to keep the database, license, and logs in
``%LOCALAPPDATA%\\AttendanceManagementSystem`` instead -- nothing about
this file needs to duplicate that, it is a property of ``sys.frozen``
being true, which both build modes set identically.

Build with:

    pyinstaller packaging/pyinstaller/main_portable.spec --noconfirm

Run from the repository root. See ``BUILD_WINDOWS.md`` for the full
Windows build procedure.
"""

from pathlib import Path

from PyInstaller.utils.hooks import collect_all

block_cipher = None

PROJECT_ROOT = Path(SPECPATH).resolve().parent.parent

# See main.spec for the rationale -- kept in sync deliberately.
_cryptography_datas, _cryptography_binaries, _cryptography_hidden = collect_all("cryptography")
_bcrypt_datas, _bcrypt_binaries, _bcrypt_hidden = collect_all("bcrypt")

datas = [
    (str(PROJECT_ROOT / "assets"), "assets"),
    *_cryptography_datas,
    *_bcrypt_datas,
]

hiddenimports = [
    "PySide6.QtSvg",
    "PySide6.QtPrintSupport",
    # See main.spec -- the dashboard's executive charts need this.
    "PySide6.QtCharts",
    "sqlalchemy.dialects.sqlite",
    "zk",
    "zk.exception",
    "reportlab.pdfbase._fontdata",
    "reportlab.pdfbase._fontdata_enc_winansi",
    "reportlab.pdfbase._fontdata_widths_helvetica",
    "reportlab.pdfbase._fontdata_widths_helveticabold",
    "reportlab.pdfbase._fontdata_widths_helveticaoblique",
    "reportlab.pdfbase._fontdata_widths_helveticaboldoblique",
    "barcode.writer",
    *_cryptography_hidden,
    *_bcrypt_hidden,
]

a = Analysis(
    # bootstrap.py, not main.py directly -- see main.spec and
    # bootstrap.py's module docstring.
    [str(PROJECT_ROOT / "bootstrap.py")],
    pathex=[str(PROJECT_ROOT)],
    binaries=[*_cryptography_binaries, *_bcrypt_binaries],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        # See main.spec's excludes list for the full rationale -- kept in
        # sync deliberately. apscheduler is genuinely used
        # (services/scheduler_service.py) and must NOT be excluded.
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
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name="AttendanceManagementSystem-Portable",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=str(PROJECT_ROOT / "assets" / "icons" / "app.ico"),
    version=str(Path(SPECPATH) / "version_info.txt"),
    manifest=str(Path(SPECPATH) / "app.manifest"),
)
