# Attendance Management System — Release Notes

## Version 1.0.0 — General Availability

This is the first General Availability release of the Attendance
Management System: a multi-company desktop application for managing
employee attendance, integrated with biometric devices, with a full
Arabic-first interface.

### Highlights

- **Multi-company from day one.** Run any number of independent companies
  from a single installation, each with completely isolated data, users,
  and settings.
- **Enforced role-based access control.** What a user can see and do is
  checked on every action, not just hidden in the menu — a role with
  view-only access to attendance genuinely cannot edit it, from either the
  desktop app or the optional REST API.
- **Automatic, hands-off operation.** Device synchronization and database
  backups run on a schedule in the background; no one needs to remember to
  click "Sync" or "Backup" every day.
- **A complete attendance model.** Shifts, holidays, and leave requests all
  feed into automatic daily attendance computation — present, late,
  absent, on leave, holiday, or weekend — with no manual reconciliation.
- **Executive dashboard.** At-a-glance company health plus a 14-day
  attendance trend and department headcount chart for a manager who wants
  the bigger picture without running a report.
- **Data protection.** Sensitive fields (salary, device credentials) are
  encrypted at rest, and database backups are encrypted end-to-end.
- **Arabic-first, genuinely bilingual.** Right-to-left layout, Arabic date
  and number formatting, and correctly shaped Arabic text in every exported
  PDF report — not an afterthought translation layer.
- **Built for integration.** An optional REST API (disabled by default)
  exposes the same company data over HTTP for organizations that want to
  connect their own tools, using the same permission model as the desktop
  app.

### What's included in this release

| Area | Capability |
|---|---|
| Employees & Departments | Records, search, hierarchical departments, branches, QR/barcode badges |
| Attendance | Device + manual punches, automatic daily status computation, shift/holiday/leave aware |
| Devices | ZKTeco (TCP/UDP) and Hikvision (ISAPI), connection testing, scheduled auto-sync |
| Reports | 6 report types, Excel/PDF/CSV export, Arabic RTL PDF rendering |
| Users & Roles | Per-company custom roles, granular permission catalog, audit log |
| Dashboard | Live stats, attendance trend chart, department breakdown chart |
| Backups | Manual + scheduled automatic backup, encrypted, WAL-safe |
| Licensing | Machine-bound activation, trial/subscription/lifetime license types |
| REST API | Optional, disabled by default, covers the same core data model |

### Upgrade notes

This is the first release — there is no upgrade path from a prior
version. Installations of pre-release/development builds should treat
this as a fresh install; contact your administrator before reusing an
existing database file, since the schema is now managed by Alembic (see
`alembic/README.md` if you maintain your own deployment).

### Known limitations

- The REST API is a separate process from the desktop application; the
  desktop app does not need it running and does not start it
  automatically.
- Windows is the only packaged distribution target for this release
  (`Setup.exe` / `Portable.exe`); the application itself runs anywhere
  Python and PySide6 do, but only Windows builds are produced and tested
  as installable artifacts.
- Employee self-service (a login scoped to only an employee's own records)
  is not part of this release — every screen operates at full company
  scope, gated by role permissions.

### Getting help

See the **Installation Guide** for setup, the **User Guide** for day-to-day
operation, and the **Administrator Guide** for licensing, backups, roles,
and troubleshooting.
