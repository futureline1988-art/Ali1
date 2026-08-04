# Changelog

All notable changes to the Attendance Management System are documented in
this file.

## [1.1.0] - 2026-08-04

### Added

Fifteen phases of commercial-platform work (Developer Suite + Attendance
Server, alongside the existing Attendance Client), all now included in a
Windows build for the first time:

- **Developer Suite**: a new, separately-versioned desktop application for
  the vendor — customer registry, license issuance/renewal/revocation,
  a real-time dashboard with charts, remote configuration publishing,
  remote software update management (sign/upload/target/schedule/publish/
  rollback), and a Reporting & Analytics module (executive, customer,
  license, synchronization, update deployment, audit log, device, and
  configuration-publication-history reports, with filtering/search/sort/
  date-range/grouping and PDF/Excel/CSV export).
- **Attendance Server**: a new platform server providing device
  registration, a generic synchronization ledger (push/pull/conflict
  resolution), administrator authentication (RBAC, sessions, audit
  logging, password reset), and read/write REST APIs backing every
  Developer Suite feature above.
- **Attendance Client**: gained remote-configuration synchronization
  (pull-and-apply, with rollback support), a background software-update
  checker (checksum- and signature-verified downloads, resumable,
  mandatory-update handling), and a status bar reflecting synchronization
  state — all opt-in and fully functional offline when no Attendance
  Server is configured or reachable.
- **Developer Suite Windows installer** (`DeveloperSuite-Setup.exe`),
  published as an additional asset on this same `v1.1.0` release: the
  vendor-side desktop application above is now installable, not just
  buildable from source — previously only the Attendance Client shipped
  a Windows build, leaving no way to actually issue the license keys it
  asks for. Built from `packaging/pyinstaller/developer_suite.spec` (its
  own onedir PyInstaller build, trimmed to what this application
  actually imports — no `bcrypt`, `pyzk`, `python-barcode`, `qrcode`, or
  `requests`) and `packaging/installer/setup_developer_suite.iss` (its
  own Inno Setup script and AppId, so it installs side by side with the
  Attendance Client without conflicting), guarded at startup by
  `developer_suite_bootstrap.py` (the same stdlib-only crash guard
  `bootstrap.py` provides for the Attendance Client). Windows-runner
  smoke-tested the same way as the Attendance Client's own builds (see
  `windows-release.yml`'s `build-developer-suite` job) before being
  attached to this release. `developer_suite/config.py`'s
  `DeveloperSuiteConfig.app_version` is bumped to `1.0.0` for this first
  installable build — independent of the Attendance Client's own
  version number, even though both now ship from the same GitHub
  Release.

- **Attendance Server Windows installer** (`AttendanceServer-Setup.exe`),
  published as a third asset on this same `v1.1.0` release: previously the
  only way to run the Attendance Server at all was `python -m server.main`
  from a Python environment with its dependencies installed, which meant
  neither the Attendance Client's sync/update features nor the Developer
  Suite's First Run Setup/login could do anything useful without a
  hand-run Python process. Built from `packaging/pyinstaller/attendance_server.spec`
  (console-mode PyInstaller build — an operator watches its own log
  output the same way `python -m server.main` already behaves in
  development, so no windowed-app stdio/message-box workarounds are
  needed) and `packaging/installer/setup_attendance_server.iss` (its own
  AppId, so all three applications install side by side without
  conflicting). By far the smallest of the three builds: no PySide6, no
  device-communication or QR/barcode/reporting dependencies — just
  FastAPI, uvicorn, SQLAlchemy, and bcrypt. Fixed a real bug found while
  packaging it: `server/config.py`'s `_resolve_data_root()` never checked
  `sys.frozen`, so a frozen build would have written its SQLite database
  into PyInstaller's own temp extraction path (wiped between runs)
  instead of persisting it — now resolves under
  `%LOCALAPPDATA%\AttendanceServer`, matching the other two applications'
  frozen-build path logic. Windows-runner smoke-tested with a real
  `GET /health` HTTP request against both the unpacked build and the
  installed executable (stronger than the two desktop apps' "still
  running" checks, since this application's entire job is answering HTTP
  requests). `server/config.py`'s `ServerConfig.app_version` is bumped
  from the placeholder `0.1.0` to `1.0.0` for this first installable
  build.

- **Fixed a default-port mismatch between the three applications**:
  `server/config.py`'s `ApiConfig.port` default was `9000`, while both
  `developer_suite.config.DeveloperSuiteConfig.attendance_server_url`
  and this repo's own `config.SyncConfig.server_url` had always
  defaulted independently to `http://127.0.0.1:8000` — meaning a fresh,
  all-defaults install of all three applications side by side could
  never actually talk to each other without someone manually overriding
  one of them. The Attendance Server's own default is now `8000`,
  matching what both clients already expected, so a brand-new
  installation works out of the box with zero manual configuration.
  `.github/workflows/windows-release.yml`'s `build-attendance-server`
  job's `GET /health` smoke tests were updated to poll the corrected
  port `8000` instead of the old `9000`. Verified end to end: the real
  Attendance Server, Developer Suite, and Attendance Client — each
  started with none of their own port/URL environment variables set —
  now reach each other, complete First Run Setup, log in, and enroll a
  device entirely on their own defaults.

### Security

- **Removed the Developer Suite's hidden bootstrap-admin credential
  mechanism** (`ATTENDANCE_SERVER_BOOTSTRAP_ADMIN_USERNAME`/
  `ATTENDANCE_SERVER_BOOTSTRAP_ADMIN_PASSWORD` environment variables) and
  replaced it with a proper **First Run Setup** flow: on first launch
  against a brand-new Attendance Server (one with no admin account yet),
  the Developer Suite now shows a setup wizard prompting the operator to
  create the first administrator account themselves — the exact same
  screen everyone else logs in through, just for the one-time case where
  there is no account to log into yet. Once that account exists, every
  later launch goes straight to the ordinary login screen; the wizard
  never reappears. New server endpoints `GET /api/v1/auth/setup-status`
  and `POST /api/v1/auth/setup` back this (self-limiting: the account
  count is re-verified inside the same transaction that creates the
  first one, so `/setup` can only ever succeed once per deployment — a
  second attempt is rejected with 409, even under a race between two
  people opening the wizard at once). `developer_suite/config.py`'s
  `DeveloperSuiteConfig.app_version` is bumped to `1.0.1` for this fix,
  published as an updated `DeveloperSuite-Setup.exe` asset on this same
  `v1.1.0` release (no new tag).

### Fixed

- **"No license signing key found" when clicking Issue License** (this
  release): a fresh Developer Suite installation had no signing
  keypair, and nothing ever created one — key generation only existed
  as `licensing/license_generator.py`'s offline CLI, unusable from a
  packaged, Python-less Windows install, and it wrote to a repo
  -relative path (`licensing/vendor/private_key.pem`) the packaged
  application never looked at anyway. `licensing/crypto/signing.py`
  gained `ensure_keypair()`: loads an existing signing key as-is
  (never overwrites it, even under two Developer Suite processes
  racing to bootstrap the same missing key at once — verified with
  real concurrent threads, not mocked), or generates and atomically
  persists a fresh Ed25519 keypair the first time one is actually
  needed, with no algorithm or key-format change.
  `developer_suite/services/license_service.py`'s and
  `update_manager_service.py`'s `_load_private_key()` (the vendor's
  license-signing and update-signing keys, respectively — both hit the
  identical bug) now call it instead of requiring the file to already
  exist; a *missing* key no longer reaches the UI as an error at all,
  only a genuinely corrupt one does. This key is held only by the
  Developer Suite — the Attendance Client never imports
  `licensing.crypto.signing` or anything that could produce a
  signature, only `licensing/keys.py`'s embedded public key (verified
  by a new, dynamic-import isolation test, not just by convention).
  Separately, the private keys matching the previously-committed
  `licensing/keys.py` and `updates/keys.py` public keys were generated
  in an early development session and never persisted anywhere
  retrievable (correctly excluded from git) — lost before any license
  or update package was ever actually issued under them, so both were
  safely re-keyed as part of this fix; `licensing/keys.py`'s and
  `updates/keys.py`'s embedded `PUBLIC_KEY_PEM` constants are updated
  to match. Verified with a real, unmocked end-to-end test covering
  the full lifecycle: generate → issue → activate → verify → renew,
  crossing from the Developer Suite's real issuance code into the
  Attendance Client's real, unmodified verification code.
  `developer_suite/config.py`'s `DeveloperSuiteConfig.app_version` is
  bumped to `1.0.2` and `config.py`'s `AppConfig.app_version` to
  `1.1.1` for this fix (the Attendance Client rebuild is required
  because it embeds the updated `licensing/keys.py`/`updates/keys.py`
  public keys), published as updated `DeveloperSuite-Setup.exe` /
  `Setup.exe` / `Portable.exe` assets on this same `v1.1.0` release
  train (no new major/minor tag).
- **Issued license key was never shown or exported** (this release): the
  License Manager's "Issue New License" and "Renew" actions called
  `LicenseService.issue_license()` / `.renew_license()`, discarded the
  returned, signed `IssuedLicense.license_key`, and just reloaded the
  table — the license was created and marked active in the database,
  but there was no way to actually hand the key to the customer, which
  blocked activation in the Attendance Client entirely (its activation
  dialog only ever accepts a pasted key). Added a new
  `developer_suite/ui/license_key_dialog.py` (`LicenseKeyDialog`) that
  now pops up automatically right after a successful issue or renew,
  showing the full signed key in a read-only field with a one-click
  "نسخ مفتاح الترخيص" (Copy to clipboard) button and a "تصدير إلى
  ملف..." (Export to `.lic` file) button. The same key is also now
  retrievable later, without re-issuing anything, from "عرض التفاصيل"
  (View Details) on any existing license row —
  `developer_suite/ui/license_details_dialog.py`'s "الترخيص الحالي" tab
  gained the same read-only key field and Copy button. No change to the
  license algorithm, signing, or verification code — this only surfaces
  a value the service layer was already computing and returning.
  `developer_suite/config.py`'s `DeveloperSuiteConfig.app_version`
  bumped to `1.0.3` for this fix.
- **Windows build (this release)**: `requirements-runtime.txt` was
  missing `httpx`, a hard dependency of the new remote-configuration-sync
  and software-update client code `main.py` imports unconditionally —
  the frozen `.exe` would have failed at import time. Added.
- **Windows build (this release)**: the frozen `.exe` crashed at startup
  with `ModuleNotFoundError: No module named 'backports'`, from
  PyInstaller's automatically-bundled `pkg_resources` runtime hook
  needing `backports.tarfile` (a transitive dependency modern
  `setuptools` pulls in under Python < 3.12) that was not installed in
  the build environment. Added `backports.tarfile` to
  `requirements-runtime.txt` — confirmed fixed by reproducing the exact
  crash in a local PyInstaller build and rebuilding clean after the fix.

## [1.0.2] - 2026-08-03

### Fixed

- **Windows startup crash** (`TypeError: Cannot log to objects of type
  'NoneType'` in `utils/logger.py`'s `setup_logging()`, reached via
  `bootstrap.py`): a windowed (`console=False`) build launched with no
  attached console — the normal case for double-clicking the installed
  `.exe` or a Start Menu shortcut — has `sys.stdout`/`sys.stderr` set to
  `None`, not a dummy stream (standard, documented PyInstaller and
  `pythonw.exe` behavior). `setup_logging()` unconditionally passed
  `sys.stderr` to Loguru's `logger.add()`, which does not accept `None`.
  `setup_logging()` now skips the console sink entirely when
  `sys.stderr is None` — an expected condition, not an error — since
  the two file-based sinks provide full logging either way.
  `bootstrap.py` additionally installs a `devnull` fallback for
  `sys.stdout`/`sys.stderr` before anything else runs, so no other code
  path (including bootstrap.py's own last-resort crash print) can hit
  this same class of bug.
- The release workflow's three Windows smoke tests previously redirected
  each launched process's stdout/stderr, which — as a side effect —
  gave those processes real (non-`None`) stream handles and let this
  exact bug slip through v1.0.1's "all tests green" CI run. The smoke
  tests no longer redirect stdio, matching how a real user actually
  launches the app.

## [1.0.1] - 2026-08-02

### Fixed

- **Windows startup crash (the installed app did nothing on launch, with
  no error)**: root-caused to `chardet` — an optional, unpinned transitive
  dependency of `requests` that ships mypyc-compiled native extension
  modules. Its native extension was not correctly bundled by PyInstaller,
  causing the frozen `.exe` to terminate with a native access violation
  (`STATUS_ACCESS_VIOLATION`, 0xC0000005) the instant `chardet` was
  imported — a crash that happens below Python's own exception handling,
  which is why nothing was ever shown to the user. `chardet` is now
  excluded from both PyInstaller specs; `requests` already falls back
  to `charset_normalizer` (its real, pinned dependency) automatically
  when `chardet` is unavailable, so this has no effect on functionality.
- Reproduced and confirmed on a real Windows machine via new smoke-test
  steps in the release workflow, which launch the onedir build, the
  Portable.exe, and the actual Setup.exe-installed executable on every
  CI run and fail the build if any of them exits early.

### Added

- `bootstrap.py`: a stdlib-only crash guard now used as the frozen app's
  entry point. Any future startup exception is written to
  `%LOCALAPPDATA%\AttendanceManagementSystem\logs\startup_crash.log`
  (with a native message box on Windows) instead of failing silently,
  and `faulthandler` is enabled so even a native-level crash can leave a
  diagnosable trace.

## [1.0.0] - 2026-08-02

Initial production release.

### Added

- **Multi-company / multi-tenant core**: unlimited companies on one shared
  database, with data isolation enforced at the repository layer
  (`CompanyScopedRepository`) — every tenant-scoped query is automatically
  filtered by `company_id`.
- **Role-based access control (RBAC)**: permission checks are enforced at
  runtime on every controller action and REST API route, not just reflected
  in the sidebar — a role only sees the screens and can only perform the
  actions its granted permission codes allow. Deny-by-default: a
  misconfigured or empty role is locked out rather than left wide open.
- **Dashboard**: live counts (active employees, departments, device status)
  and today's attendance summary (present / late / absent / on leave), plus
  executive charts — a 14-day company-wide attendance trend and a
  department headcount breakdown, rendered with native Qt Charts.
- **Employee, department & branch management**: search, a reorderable
  department tree, per-company branch (physical location) management, and
  automatic QR code + barcode generation per employee.
- **Shifts, holidays & leave**: configurable work shifts with grace
  periods and per-day working-day patterns, a company holiday calendar, and
  a leave request/approval workflow — all three feed directly into daily
  attendance computation.
- **Attendance & punches**: import punches from biometric devices or enter
  them manually; daily status (present / late / absent / leave / holiday /
  weekend) computed automatically in the company's local time zone,
  accounting for the employee's assigned shift, holidays, and approved
  leave.
- **Biometric device integration**: ZKTeco (TCP/UDP) and Hikvision (ISAPI)
  protocol support, connection testing, punch synchronization, and pushing
  employee data to enrolled devices.
- **Automatic scheduled tasks**: background device synchronization and
  database backups run on a configurable interval with no user action
  required, alongside on-demand manual sync/backup.
- **Field-level encryption**: sensitive columns (employee salary, device
  communication keys) are encrypted at rest (Fernet/AES), and database
  backups are themselves encrypted end-to-end.
- **Reports**: 6 report types (attendance summary, by employee, by
  department, late arrivals, overtime, absences), exportable to Excel, PDF,
  and CSV, with full Arabic RTL text shaping in PDF output.
- **Users, roles & permissions**: per-company customizable roles, a granular
  permission catalog, and a full audit log for sensitive operations.
- **Optional REST API**: a separate, disabled-by-default FastAPI process
  (`run_api.py`) exposing the same company data over HTTP — authentication,
  companies, employees, departments, attendance, and dashboard endpoints —
  for external integrations, sharing the same RBAC model and service layer
  as the desktop app.
- **Settings**: company profile, display preferences (time zone, date
  format, currency), and safe database backup/restore (WAL-compatible,
  encrypted).
- **Arabic-first UI**: RTL by default, `DD/MM/YYYY` dates, 24-hour clock,
  optional Arabic-Indic numerals, and a light/dark Fluent-inspired theme.
- **Security**: bcrypt password hashing, account lockout after repeated
  failed attempts, idle-session timeout, and comprehensive audit logging.
- **Licensing**: Ed25519-signed license keys bound to the machine, with an
  activation window shown before login and a license-info screen for
  viewing status, expiry, and seat usage.
- **Automated tests**: a real pytest suite (`tests/`, no mocked database)
  covering attendance computation, RBAC enforcement, encryption, branch
  management, scheduled-task logic, dashboard aggregation, and the REST API
  end-to-end.
- **Database migrations**: Alembic wired in with a baseline migration
  capturing the full v1.0.0 schema, for managed schema changes going
  forward (see `alembic/README.md`).
- **Windows packaging**: PyInstaller onedir + onefile ("Portable") build
  specs, an Inno Setup installer (desktop/Start Menu shortcuts, registered
  uninstaller, upgrade-safe — user data in `%LOCALAPPDATA%` is never
  touched by install/upgrade/uninstall), application icon, version
  metadata, a DPI-aware manifest, and a GitHub Actions workflow that builds
  and publishes both artifacts automatically on a version tag. See
  `BUILD_WINDOWS.md`.
