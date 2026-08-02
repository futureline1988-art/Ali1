# Attendance Management System — Administrator Manual

This manual covers installation, licensing, multi-company setup, user and
permission management, backups, and troubleshooting — everything beyond
day-to-day use, which is covered in the **User Manual**.

## 1. Installation

Run `Setup.exe` on the target Windows machine and follow the wizard:

1. Choose an install location (defaults to Program Files) — administrator
   rights are required for this one-time step.
2. Optionally check "Create a desktop icon."
3. Click Install, then Finish (the app can launch immediately from the
   final wizard page).

The installer creates Start Menu and (optionally) Desktop shortcuts, and
registers an uninstaller under Windows' "Apps & Features." No Python
installation or other runtime is required on the target machine — the
installer bundles everything the application needs.

**Portable use**: `Portable.exe` (also available in the release) is a
single, standalone executable — copy it to a USB drive or any folder and
run it directly, with no installation step. It stores its data in the same
`%LOCALAPPDATA%\AttendanceManagementSystem\` location as the installed
version when run on the same Windows user account.

### 1.1 Where application data lives

The application never writes inside its own install folder (Program
Files is not writable by a standard user, and the goal is also to survive
upgrades cleanly). All writable data — the database, logs, backups,
uploaded files, and the license — lives under:

```
%LOCALAPPDATA%\AttendanceManagementSystem\
├── data\        (SQLite database)
├── logs\        (application logs, useful for troubleshooting)
├── backups\     (database backups)
└── uploads\     (employee photos, generated QR codes/barcodes, report exports)
```

This folder is created automatically on first launch and is **never**
touched by installing, upgrading, or uninstalling the application — see
"Upgrading" and "Uninstalling" below.

### 1.2 Upgrading

Running a newer `Setup.exe` over an existing installation performs an
in-place upgrade: program files under the install directory are replaced,
and `%LOCALAPPDATA%\AttendanceManagementSystem\` (database, license, logs,
backups) is left completely untouched. No manual data migration step is
needed for a standard upgrade.

### 1.3 Uninstalling

Uninstall from Windows Settings → Apps, or via the Start Menu shortcut.
The uninstaller removes only the installed program files. It then asks
separately whether to also delete your data folder
(`%LOCALAPPDATA%\AttendanceManagementSystem\`) — choose **No** to keep your
database, license, and logs in place for a future reinstall, or **Yes** to
fully remove all traces of the application and its data from the machine.

## 2. Licensing

The application requires an activated license before the login screen is
shown. License keys are cryptographically signed (Ed25519) and bound to
the specific machine they're activated on — a key generated for one
computer will not activate on another.

### 2.1 Activation

On first launch (or whenever no valid license is present), the activation
screen appears. Enter the license key you were provided and click
Activate. On success, the application proceeds to the login screen; every
subsequent launch skips this screen as long as the license remains valid.

### 2.2 License types

| Type | Behavior |
|---|---|
| Trial | Time-limited; expires after its trial period. |
| Monthly / Yearly | Subscription-style; renewable, expires on the configured date. |
| Lifetime | Never expires. |

### 2.3 Checking license status

The **License Info** screen (accessible from within the application once
logged in — check the Settings area or your user menu) shows the current
license type, expiry date (if any), and days remaining. Renew before
expiry to avoid an interruption — contact your software vendor for a new
key when a subscription is nearing its end.

### 2.4 Reactivation / transferring to a new machine

License keys are bound to the machine they were activated on. If you need
to move the software to new hardware, contact your vendor for a
reactivation — this application does not generate license keys itself; key
generation is a vendor-side process kept separate from the shipped product
for security (the signing key is never distributed with the application).

## 3. Multi-Company Setup

This is a true multi-tenant application: one installation, one database,
any number of independent companies, each fully isolated from the others
(no employee, department, user, device, or report from one company is ever
visible to another). To onboard a new company, use the company management
flow reachable from the login screen / initial setup — no code changes or
per-company deployment is required.

Each company has its own:

- Employees, departments, and shifts.
- Users, roles, and permissions.
- Devices and attendance records.
- Settings (time zone, date format, currency, backups).

## 4. Users, Roles & Permissions

- **Roles** are per-company and fully customizable — there is no fixed
  global role list beyond what you define.
- **Permissions** are granted per role from a fixed catalog covering every
  module (dashboard, employees, departments, attendance, devices, reports,
  users/roles, settings, backups), each typically split into `.view` and
  `.manage` (or `.export`) grades — e.g. a role can be allowed to view
  attendance without being able to edit it, or view reports without being
  able to export them.
- **Audit log**: every sensitive operation (logins, record changes,
  backups, permission changes) is recorded with who performed it and when,
  for accountability and troubleshooting.

To create a new user: go to Users → Add, assign a company (implicit — you
manage users within the company you're logged into), a username, an
initial password, and a role. The user's password is hashed (bcrypt)
before storage; there is no way to view a user's existing password, only
to reset it.

### 4.1 Account lockout & sessions

Accounts lock temporarily after repeated failed login attempts, to resist
password-guessing. Sessions expire automatically after a period of
inactivity, requiring the user to log in again — this cannot be disabled
from the UI and is a deliberate security default.

## 5. Devices

Supported protocols: **ZKTeco** (TCP/UDP push/pull protocol) and
**Hikvision** (ISAPI over HTTP). For each device you configure its IP
address and port; use "Test Connection" before saving to confirm
reachability. Punch synchronization pulls new records from the device;
pushing employee data enrolls them for recognition on the device's own
hardware (biometric/card enrollment itself always happens on the device,
not in this application).

**Network requirements**: the computer running this application must be
able to reach each device's IP/port — same subnet, or routed and allowed
through any firewalls in between. If a device intermittently drops, check
for DHCP lease changes on the device (a static IP or DHCP reservation on
the device is strongly recommended).

## 6. Backup & Restore

- **Manual backup**: from Settings → Backup, create a backup on demand.
  Backups are SQLite-WAL-compatible database snapshots stored under
  `%LOCALAPPDATA%\AttendanceManagementSystem\backups\`.
- **Restore**: from the same screen, restore from a previously created
  backup file. Restoring replaces the current database — make a fresh
  backup of the current state first if you might need to go back to it.
- **Recommended practice**: back up before any major change (bulk
  employee import, department restructuring, a software upgrade) and keep
  periodic backups copied off-machine (network share, cloud storage) in
  case of hardware failure — the application's own backup feature protects
  against data mistakes, not against the machine itself being lost.

## 7. Troubleshooting

| Symptom | Likely cause / fix |
|---|---|
| App won't start, no window appears | Check `%LOCALAPPDATA%\AttendanceManagementSystem\logs\` for the most recent log file and the error it recorded. |
| "تعذر الاتصال بقاعدة البيانات" (database connection error) at startup | The data folder or database file may be missing/corrupted, or in use by another process. Restore from a recent backup if needed. |
| Device shows disconnected | Confirm the device is powered on and reachable (ping its IP); confirm the IP/port configured in Devices still matches the device. |
| Arabic text garbled in an exported PDF | Should not occur in a correctly built release — the app bundles its own Arabic-capable font and text-shaping libraries. If it does, the installation may be corrupted; reinstall. |
| Login screen shows no companies | No company has been set up yet, or all companies are marked inactive — check company setup / reactivate the company. |
| User locked out | Wait for the lockout window to pass, or have another administrator reset the account. |
| License shows expired | Contact your vendor for a renewed key and re-activate via the activation screen. |

For issues not covered here, collect the relevant log file(s) from
`%LOCALAPPDATA%\AttendanceManagementSystem\logs\` before contacting
support — they contain the detail needed to diagnose most problems
quickly.
