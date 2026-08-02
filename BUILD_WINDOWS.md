# Building the Windows Release

This document is the exact procedure to produce `Setup.exe` and
`Portable.exe` on a **real Windows machine**. Everything else — the
PyInstaller spec files, the Inno Setup script, the icon, version metadata,
and the app manifest — is already prepared in this repository; a Windows
machine is only needed to run the actual compilers (PyInstaller's bootloader
and Inno Setup's `ISCC.exe` both produce Windows PE binaries and cannot run
on Linux).

## Option A: automated (GitHub Actions, no Windows machine needed)

`.github/workflows/windows-release.yml` runs every step below on a
`windows-latest` GitHub-hosted runner automatically:

- **On every `v*.*.*` tag push** (e.g. `git tag v1.0.0 && git push --tags`)
  — builds `Setup.exe`/`Portable.exe` and attaches them to a GitHub
  Release for that tag.
- **On demand** — trigger it manually from the Actions tab
  ("Run workflow") for a build without tagging.

Either way, download the finished `Setup.exe`/`Portable.exe` from the
workflow run's Artifacts (or the GitHub Release, for a tag-triggered run)
— no local Windows machine, Python install, or Inno Setup install
required on your end. Use Option B below only if you need to build
locally (e.g. to test a change before tagging, or you don't have GitHub
Actions available).

## Option B: manual, on a real Windows machine

### Prerequisites

1. **Windows 10 or 11, 64-bit.**
2. **Python 3.11 - 3.13**, installed from [python.org](https://www.python.org/downloads/windows/)
   and added to `PATH` (check "Add python.exe to PATH" in the installer).
3. **Inno Setup 6**, installed from
   [jrsoftware.org/isdl.php](https://jrsoftware.org/isdl.php) (default
   install location is fine — `build_all.bat` looks for it there
   automatically if `ISCC.exe` isn't already on `PATH`).
4. A clone of this repository, with `attendance_system/` as your working
   directory for every command below.

No other software is required — the build script creates its own isolated
virtual environment and installs every Python dependency into it.

### One-command build

```cmd
cd attendance_system
packaging\build_all.bat
```

This runs, in order:

1. Creates (or reuses) `.venv-build\`, an isolated virtual environment.
2. Installs `requirements-runtime.txt` into it — a trimmed dependency set
   containing only what the shipped app actually imports (PySide6,
   SQLAlchemy, bcrypt, cryptography, openpyxl, reportlab, arabic-reshaper,
   python-bidi, qrcode, python-barcode, Pillow, pyzk, requests,
   python-dotenv, loguru, APScheduler, PyInstaller). It intentionally
   excludes packages listed in `requirements.txt` that are not imported by
   any shipped code today (matplotlib, alembic, psycopg2-binary, PyMySQL,
   pandas, pytest, pytest-qt, httpx) to keep the build smaller and faster.
   fastapi and uvicorn are excluded too, but for a different reason: they
   back `run_api.py`, a separate optional REST API process `main.py` never
   imports — see `api/` — so the desktop build genuinely has no need for
   them, not that they're merely unused. For a byte-for-byte reproducible
   build instead (every transitive dependency exactly pinned, not just
   the direct ones), install `requirements-runtime.lock.txt` instead of
   `requirements-runtime.txt` in step 2.
3. Cleans any previous `build\` / `dist\` output.
4. Runs `pyinstaller packaging\pyinstaller\main.spec` — an onedir build,
   producing `dist\AttendanceManagementSystem\` (the exe plus an
   `_internal\` folder with every DLL, Qt plugin, and bundled asset).
5. Runs `pyinstaller packaging\pyinstaller\main_portable.spec` — a onefile
   build, producing `dist\AttendanceManagementSystem-Portable.exe`, copied
   to `Release\Portable.exe`.
6. Runs `ISCC packaging\installer\setup.iss`, which packages the onedir
   build from step 4 into `Release\Setup.exe`.
7. Copies `CHANGELOG.md` and any PDFs found in `docs\` into `Release\`.

When it finishes, `Release\` contains:

```
Release/
├── Setup.exe                     # the full installer
├── Portable.exe                  # standalone, no installation needed
├── CHANGELOG.md
├── README.pdf                    # if docs/README.pdf exists
├── User Manual.pdf               # if docs/User Manual.pdf exists
└── Administrator Manual.pdf      # if docs/Administrator Manual.pdf exists
```

(This repository already ships `Release/README.pdf`,
`Release/User Manual.pdf`, `Release/Administrator Manual.pdf`, and
`Release/CHANGELOG.md`, generated in the Linux development environment;
`build_all.bat` will refresh them from `docs\` if you regenerate the source
markdown, but re-running it is not required just to get `Setup.exe` and
`Portable.exe`.)

### Manual / step-by-step build

If you want to run each step yourself instead of `build_all.bat`:

```cmd
cd attendance_system

python -m venv .venv-build
.venv-build\Scripts\activate

pip install --upgrade pip
pip install -r requirements-runtime.txt

pyinstaller packaging\pyinstaller\main.spec --noconfirm
pyinstaller packaging\pyinstaller\main_portable.spec --noconfirm

"C:\Program Files (x86)\Inno Setup 6\ISCC.exe" packaging\installer\setup.iss
```

`Setup.exe` is written directly to `Release\` by the Inno Setup script
(`OutputDir={#SourceRoot}\Release` in `setup.iss`). Copy
`dist\AttendanceManagementSystem-Portable.exe` to `Release\Portable.exe`
yourself if you skip `build_all.bat`.

### Verifying the installer on a clean machine

Test on a Windows VM/machine **without Python installed** (or with Python
completely removed from `PATH`) to confirm the build is truly
self-contained:

1. Copy `Release\Setup.exe` to the clean machine and run it.
2. Confirm the wizard shows the app icon, name, and version, and completes
   without any missing-DLL or missing-module errors.
3. Confirm a desktop shortcut and Start Menu entry were created (the
   desktop one only if you checked that box in the wizard).
4. Launch the app from the shortcut. It should show the license activation
   window, then (after activating) the login screen — with no console
   window, no Python traceback, and no "missing DLL" dialog from Windows.
5. Log in, exercise a few screens (dashboard, employees, attendance,
   reports — try exporting a PDF report with Arabic text to confirm
   `arabic-reshaper`/`python-bidi`/the bundled DejaVu font all made it into
   the build), and confirm the app writes its database and logs under
   `%LOCALAPPDATA%\AttendanceManagementSystem\` (open that folder in
   Explorer while the app is running).
6. Close the app, reopen it, and confirm your data (and license
   activation) persisted.
7. From "Apps & Features" (or the Start Menu shortcut), run the
   uninstaller. Confirm it removes the installed program files and asks
   separately whether to also delete `%LOCALAPPDATA%\AttendanceManagementSystem`
   — answering "No" should leave your database/license/logs in place for a
   future reinstall, exactly as required for upgrade-safety.
8. To verify upgrades preserve data: install an older `Setup.exe`, use the
   app to create some data, then run a newer `Setup.exe` over it (same
   `AppId` in `setup.iss` makes this an in-place upgrade, not a
   side-by-side install) and confirm the data is still there afterward.

If anything in steps 2-8 fails, it is a genuine packaging bug — fix it in
the spec/`.iss` files (or, if the root cause is a path assumption in the
app itself such as `config.py`'s frozen-vs-dev path resolution, in that
file specifically) and rebuild. Do not change attendance, employee,
device, reports, database, or licensing *business logic* to work around a
packaging problem — the fix belongs in the packaging layer or in
`config.py`'s path resolution, never in feature code.

### Troubleshooting

- **`ISCC` not found**: install Inno Setup 6 from the link above, or add
  its install directory to `PATH`, or edit `packaging\build_all.bat`'s
  hardcoded fallback path if you installed it somewhere non-default.
- **`Hidden import 'zk.exception' not found` (or similar for `zk`)**: this
  means `pyzk` didn't install into `.venv-build`. Run
  `pip install pyzk==0.9` inside the activated venv and re-run PyInstaller;
  ZKTeco device support will not work in the build until this succeeds.
- **Antivirus flags the built .exe / installer**: this is a common false
  positive for PyInstaller-built executables (the bootloader's behavior —
  unpacking a bundled Python runtime at startup — resembles some malware
  packers). Code-signing the executables with an
  [Authenticode](https://learn.microsoft.com/windows/win32/seccrypto/cryptography-tools)
  certificate (not covered here — requires purchasing a certificate)
  resolves this for a real release; for internal testing, add an
  exclusion in Windows Defender for the `dist\`/`Release\` folder.
- **App starts but Arabic text in PDFs looks wrong**: confirm
  `assets\fonts\DejaVuSans.ttf` and `DejaVuSans-Bold.ttf` exist under
  `dist\AttendanceManagementSystem\_internal\assets\fonts\` after the
  build — `main.spec`/`main_portable.spec` bundle the whole `assets\`
  folder via their `datas` list, so a missing font means the build ran
  from the wrong working directory (must be `attendance_system\`, not
  `packaging\pyinstaller\`).
- **App can't find/write its database on first launch**: confirm you're
  testing the actual frozen build (`sys.frozen` is only true inside a
  PyInstaller build), not `python main.py` — in development mode the
  database lives under `attendance_system\data\` instead of
  `%LOCALAPPDATA%`, by design (see `config.py`'s `_resolve_data_root()`).
