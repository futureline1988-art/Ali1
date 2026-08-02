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
- **Dashboard**: live counts (active employees, departments, device status)
  and today's attendance summary (present / late / absent / on leave).
- **Employee & department management**: search, a reorderable department
  tree, and automatic QR code + barcode generation per employee.
- **Attendance & punches**: import punches from biometric devices or enter
  them manually; daily status (present / late / absent / leave / holiday)
  computed automatically in the company's local time zone.
- **Biometric device integration**: ZKTeco (TCP/UDP) and Hikvision (ISAPI)
  protocol support, connection testing, punch synchronization, and pushing
  employee data to enrolled devices.
- **Reports**: 6 report types (attendance summary, by employee, by
  department, late arrivals, overtime, absences), exportable to Excel, PDF,
  and CSV, with full Arabic RTL text shaping in PDF output.
- **Users, roles & permissions**: per-company customizable roles, a granular
  permission catalog, and a full audit log for sensitive operations.
- **Settings**: company profile, display preferences (time zone, date
  format, currency), and safe database backup/restore (WAL-compatible).
- **Arabic-first UI**: RTL by default, `DD/MM/YYYY` dates, 24-hour clock,
  optional Arabic-Indic numerals, and a light/dark Fluent-inspired theme.
- **Security**: bcrypt password hashing, account lockout after repeated
  failed attempts, idle-session timeout, and comprehensive audit logging.
- **Licensing**: Ed25519-signed license keys bound to the machine, with an
  activation window shown before login and a license-info screen for
  viewing status, expiry, and seat usage.
- **Windows packaging**: PyInstaller onedir + onefile ("Portable") build
  specs, an Inno Setup installer (desktop/Start Menu shortcuts, registered
  uninstaller, upgrade-safe — user data in `%LOCALAPPDATA%` is never
  touched by install/upgrade/uninstall), application icon, version
  metadata, and a DPI-aware manifest. See `BUILD_WINDOWS.md`.
