# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec: onedir build of the Developer Suite.

This is the build consumed by
``packaging/installer/setup_developer_suite.iss`` (Inno Setup packages
the whole output folder into Setup.exe). Build with:

    pyinstaller packaging/pyinstaller/developer_suite.spec --noconfirm

Run from the repository root so the relative paths below resolve
correctly. Mirrors ``main.spec`` (the Attendance Client's own spec)
deliberately -- same structure, same rationale for each choice --
adjusted only for what this application actually bundles: no
``pyzk``/``python-barcode``/``qrcode`` device-communication code, no
``bcrypt`` (this application's admin authentication is a pure HTTP
client against the Attendance Server's own bcrypt-hashed accounts --
see ``developer_suite/config.py``'s docstring), but it does bundle
``reportlab``/``arabic-reshaper``/``python-bidi``/``openpyxl`` for its
own Phase 15 Reporting module.
"""

from pathlib import Path

from PyInstaller.utils.hooks import collect_all

block_cipher = None

# packaging/pyinstaller/developer_suite.spec -> repository root
PROJECT_ROOT = Path(SPECPATH).resolve().parent.parent

# cryptography ships a compiled (Rust) extension. PyInstaller's built-in
# hook is usually sufficient, but a missing transitive DLL is a classic
# cause of a frozen Windows build that fails at import time with no
# visible error (see developer_suite_bootstrap.py) -- collect_all() is
# the belt-and-suspenders option: it pulls in every submodule, binary,
# and data file the package itself declares, rather than relying solely
# on hook-level detection. See main.spec for the identical rationale,
# applied there to both cryptography and bcrypt -- this application has
# no bcrypt dependency at all (see this file's module docstring).
_cryptography_datas, _cryptography_binaries, _cryptography_hidden = collect_all("cryptography")

datas = [
    (str(PROJECT_ROOT / "assets"), "assets"),
    *_cryptography_datas,
]

hiddenimports = [
    # PySide6 plugins/submodules PyInstaller's static analysis can miss.
    "PySide6.QtSvg",
    "PySide6.QtPrintSupport",
    # developer_suite/ui/dashboard_charts.py and reporting_charts.py both
    # import this directly, so Analysis should already find it -- listed
    # explicitly anyway so the dashboard's and Reporting module's charts
    # are never silently dropped from a build by an unrelated refactor of
    # those imports (see main.spec's identical rationale).
    "PySide6.QtCharts",
    # SQLAlchemy's SQLite dialect is loaded via a plugin-style string
    # lookup (create_engine("sqlite://...")), not a literal top-level
    # import, so PyInstaller's import scanner cannot discover it on its
    # own.
    "sqlalchemy.dialects.sqlite",
    # reportlab's PDF encoding/font-metrics tables are looked up
    # dynamically by name rather than imported directly -- needed here
    # for developer_suite/ui/reporting_page.py's PDF export
    # (utils.pdf.export_to_pdf).
    "reportlab.pdfbase._fontdata",
    "reportlab.pdfbase._fontdata_enc_winansi",
    "reportlab.pdfbase._fontdata_widths_helvetica",
    "reportlab.pdfbase._fontdata_widths_helveticabold",
    "reportlab.pdfbase._fontdata_widths_helveticaoblique",
    "reportlab.pdfbase._fontdata_widths_helveticaboldoblique",
    *_cryptography_hidden,
]

a = Analysis(
    # developer_suite_bootstrap.py, not developer_suite/main.py directly:
    # a stdlib-only guard that catches and reports (crash log + native
    # message box) any exception raised while importing or running
    # developer_suite/main.py -- including an import-time failure in
    # that module itself, which is otherwise invisible in a windowed
    # (console=False) build. See developer_suite_bootstrap.py's module
    # docstring and main.spec's identical pattern for the Attendance
    # Client.
    [str(PROJECT_ROOT / "developer_suite_bootstrap.py")],
    pathex=[str(PROJECT_ROOT)],
    binaries=[*_cryptography_binaries],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        # This application never imports any of these -- see this
        # file's module docstring. Excluded to keep the build smaller
        # and faster, same doctrine as main.spec's excludes list.
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
        "bcrypt",
        "pyzk",
        "zk",
        "qrcode",
        "barcode",
        "requests",
        # See main.spec's excludes list for the full root-cause
        # rationale (a reproduced, diagnosed Windows startup crash):
        # chardet ships mypyc-compiled native extension modules and is
        # never imported by this application either -- excluding it
        # keeps the same guaranteed-fallback behavior this codebase
        # relies on everywhere else.
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
    name="DeveloperSuite",
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
    version=str(Path(SPECPATH) / "developer_suite_version_info.txt"),
    manifest=str(Path(SPECPATH) / "developer_suite_app.manifest"),
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="DeveloperSuite",
)
