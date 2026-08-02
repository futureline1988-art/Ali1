# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec: onedir build of the Attendance Management System.

This is the build consumed by ``packaging/installer/setup.iss`` (Inno
Setup packages the whole output folder into Setup.exe). Build with:

    pyinstaller packaging/pyinstaller/main.spec --noconfirm

Run from the ``attendance_system/`` project root so the relative paths
below resolve correctly. See ``BUILD_WINDOWS.md`` for the full,
copy-pasteable Windows build procedure.
"""

import sys
from pathlib import Path

block_cipher = None

# packaging/pyinstaller/main.spec -> attendance_system/
PROJECT_ROOT = Path(SPECPATH).resolve().parent.parent

datas = [
    (str(PROJECT_ROOT / "assets"), "assets"),
]

hiddenimports = [
    # PySide6 plugins/submodules PyInstaller's static analysis can miss.
    "PySide6.QtSvg",
    "PySide6.QtPrintSupport",
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
]

a = Analysis(
    [str(PROJECT_ROOT / "main.py")],
    pathex=[str(PROJECT_ROOT)],
    binaries=[],
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
        "matplotlib",
        "fastapi",
        "uvicorn",
        "apscheduler",
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
