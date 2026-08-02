# Changelog

All notable changes to the Attendance Management System are documented in
this file.

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
