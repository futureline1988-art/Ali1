# Attendance Management System — Installation Guide

This guide is for installing the **pre-built** application from a release
package (`Setup.exe` or `Portable.exe`). If you need to build the
application from source instead, see `BUILD_WINDOWS.md` in the source
repository — that is a developer procedure, not needed for a normal
installation.

## 1. System Requirements

| Requirement | Minimum |
|---|---|
| Operating System | Windows 10 or Windows 11, 64-bit |
| Processor | Any 64-bit x86 processor from the last decade |
| Memory | 4 GB RAM (8 GB recommended for larger employee counts) |
| Disk space | 500 MB for the application; more for your database, backups, and exported reports as they accumulate |
| Network | Only required to reach biometric devices on your local network; the application itself does not require internet access |
| Permissions | Administrator rights for the one-time `Setup.exe` install step only |

No separate Python installation, database server, or other runtime is
required — everything the application needs is bundled into the
installer/portable executable.

## 2. What's in the Release Package

```
Release/
├── Setup.exe                     # Full installer (recommended for most users)
├── Portable.exe                  # Standalone, no installation needed
├── CHANGELOG.md                  # Full technical change history
├── Release Notes.pdf             # What's new in this version
├── Installation Guide.pdf        # This document
├── User Manual.pdf                # Day-to-day usage
├── Administrator Manual.pdf       # Licensing, roles, backups, troubleshooting
└── README.pdf                     # Project overview
```

Choose **`Setup.exe`** for a normal installation with Start Menu/Desktop
shortcuts and a proper uninstaller — this is the right choice for almost
everyone. Choose **`Portable.exe`** only if you specifically need to run
the application without installing anything (e.g. from a USB drive, or on
a machine where you cannot get administrator rights even for the one-time
install step).

## 3. Installing with Setup.exe

1. Copy `Setup.exe` to the target computer and run it.
2. If Windows shows a SmartScreen warning ("Windows protected your PC"),
   click **More info** then **Run anyway** — this is expected for an
   application that isn't yet code-signed with a commercial certificate;
   verify you obtained `Setup.exe` from a trusted source (your vendor)
   before proceeding.
3. Follow the setup wizard:
   - Accept the default install location (or choose another) — this step
     requires administrator rights.
   - Optionally check **"Create a desktop icon"**.
   - Click **Install**, then **Finish**. You can launch the application
     immediately from the final wizard page.
4. The installer creates a Start Menu entry and (if selected) a Desktop
   shortcut, and registers the application under Windows' **Apps &
   Features** for later uninstallation.

No restart is required.

## 4. Installing with Portable.exe

1. Copy `Portable.exe` to any folder, USB drive, or network location.
2. Run it directly — no installation step, no administrator rights
   required.
3. The first launch is slightly slower than subsequent ones (it extracts
   to a temporary folder each time it starts); this is normal.

`Portable.exe` stores its data in the same location as an installed copy
run under the same Windows user account (see section 6 below), so you can
switch between a portable and installed copy on the same machine without
losing data — they are the same application, just packaged differently.

## 5. First Run: License Activation and Initial Login

1. On first launch, the **license activation** screen appears. Enter the
   license key provided by your vendor and click **Activate**. An internet
   connection is not required for activation — the key is verified
   locally against a signature embedded in the application.
2. Once activated, you'll see the **login screen** with a company
   selector. If this is a brand-new installation with no company set up
   yet, follow your vendor's or administrator's initial company/admin-user
   setup instructions before logging in.
3. Log in with the username, password, and company provided by your
   administrator.

Every subsequent launch goes straight to the login screen — activation is
a one-time step per machine (see the Administrator Guide if you need to
move the license to a new machine).

## 6. Where Your Data Lives

The application never writes inside its own install folder. All data —
the database, logs, backups, and exported files — lives under:

```
%LOCALAPPDATA%\AttendanceManagementSystem\
```

This folder is created automatically on first launch. It survives
uninstalling and reinstalling (see below), and is never touched by
upgrading to a newer version.

## 7. Upgrading to a Newer Version

Run the new version's `Setup.exe` over your existing installation — this
performs an in-place upgrade. Your database, license activation, logs,
and backups are untouched; only the application program files are
replaced. No manual export/import step is needed.

If you use `Portable.exe`, simply replace the old file with the new one;
your data (under `%LOCALAPPDATA%`) is shared between versions
automatically.

## 8. Uninstalling

From Windows Settings → **Apps** (or the Start Menu shortcut), uninstall
"Attendance Management System." The uninstaller removes the program files
and then asks separately whether to also delete your data folder:

- **No** (recommended unless you're fully done with the application) —
  keeps your database, license, and logs in place in case you reinstall
  later.
- **Yes** — permanently deletes all attendance data, the license
  activation, and logs from this machine.

`Portable.exe` has no installer to run, so "uninstalling" it is simply
deleting the file; your data folder is not touched and must be removed
manually if you want it gone too.

## 9. Verifying the Installation

After installing, confirm:

- The application launches without any console window, Python traceback,
  or "missing DLL" error.
- The license activation screen (first run) or login screen (subsequent
  runs) appears correctly, with Arabic text rendering right-to-left.
- You can log in and see the dashboard.
- Exporting a PDF report shows correctly shaped Arabic text (this
  confirms the bundled font and text-shaping libraries are intact).

If any of these fail, see the **Troubleshooting** section of the
Administrator Manual, or contact your vendor's support with the relevant
log file from `%LOCALAPPDATA%\AttendanceManagementSystem\logs\`.

## 10. Next Steps

- New users: continue to the **User Manual** for day-to-day operation.
- Administrators: continue to the **Administrator Manual** for licensing
  details, multi-company setup, user/role management, backup practices,
  and troubleshooting.
