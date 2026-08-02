# Attendance Management System — Administrator Manual

This manual covers installation, licensing, multi-company setup, user and
permission management, backups, and troubleshooting — everything beyond
day-to-day use, which is covered in the **User Manual**.

## 1. Installation

See the **Installation Guide** for the full step-by-step procedure
(system requirements, `Setup.exe` vs. `Portable.exe`, first-run
activation, and verifying the install). Summary: run `Setup.exe` and
follow the wizard (administrator rights are needed for this one-time
step only), or run `Portable.exe` directly with no installation step at
all. No Python installation or other runtime is required on the target
machine — either option bundles everything the application needs.

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

- Employees, departments, branches, and shifts.
- Users, roles, and permissions.
- Devices and attendance records.
- Settings (time zone, date format, currency, backups).

### 3.1 Branches

Branches represent your company's physical locations/sites — separate
from departments, which represent organizational structure. An employee
or a device can optionally be assigned to a branch, letting you track
where staff work and where a device is physically installed when a
company operates from more than one location. Exactly one branch per
company can be marked as the "main branch" at a time; designating a new
one automatically un-marks the previous one. Branch management is its own
permission-gated screen (Branches), independent of Departments.

## 4. Users, Roles & Permissions

- **Roles** are per-company and fully customizable — there is no fixed
  global role list beyond what you define.
- **Permissions** are granted per role from a fixed catalog covering every
  module (dashboard, employees, departments, branches, attendance,
  devices, shifts, holidays, leave, reports, users/roles, settings,
  backups), each typically split into `.view` and `.manage` (or
  `.export`) grades — e.g. a role can be allowed to view attendance
  without being able to edit it, or view reports without being able to
  export them.
- **Permissions are enforced at runtime, not just reflected in the menu.**
  Every action — in the desktop app and in the optional REST API alike —
  checks the acting user's granted permission codes before touching the
  database. A user without a permission cannot perform the corresponding
  action even by some other route into the same feature; they simply
  don't see the corresponding screen or sidebar entry in the first place,
  since a screen is only ever built for permissions the logged-in user
  actually holds.
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

### 5.1 Automatic device synchronization

In addition to manually clicking "Sync," the application can pull new
punches from every active device on a schedule, in the background, with
no user action required. This is controlled by environment variables
(there is no in-app toggle, since it is a deployment-level setting):

| Variable | Default | Meaning |
|---|---|---|
| `DEVICE_AUTO_SYNC_ENABLED` | `true` | Whether scheduled sync runs at all |
| `DEVICE_AUTO_SYNC_INTERVAL_MINUTES` | `15` | How often it runs |

A failure syncing one device (e.g. temporarily unreachable) never blocks
syncing the others — each device's result is independent, and failures
are recorded in the application log rather than shown as a pop-up, since
no one is necessarily watching the screen when a scheduled sync runs.

## 6. Backup & Restore

- **Manual backup**: from Settings → Backup, create a backup on demand.
  Backups are SQLite-WAL-compatible database snapshots stored under
  `%LOCALAPPDATA%\AttendanceManagementSystem\backups\`, **encrypted** at
  rest the same way sensitive database columns are (see 6.2 below) — a
  stolen backup file is not readable without the machine's own encryption
  key.
- **Automatic scheduled backup**: enabled by default (Settings →
  Preferences → "Auto Backup", or the `BACKUP_AUTO_ENABLED` /
  `BACKUP_INTERVAL_HOURS` environment variables) — the application checks
  periodically whether enough time has passed since the last backup and,
  if so, creates one and applies the retention policy (older backups
  beyond `BACKUP_RETENTION_COUNT`, default 14, are pruned automatically).
  You do not need to remember to back up manually once this is on.
- **Restore**: from the same screen, restore from a previously created
  backup file. Restoring replaces the current database — make a fresh
  backup of the current state first if you might need to go back to it.
- **Recommended practice**: back up before any major change (bulk
  employee import, department restructuring, a software upgrade) and keep
  periodic backups copied off-machine (network share, cloud storage) in
  case of hardware failure — the application's own backup feature protects
  against data mistakes, not against the machine itself being lost.

### 6.1 Data encryption

Certain sensitive columns — employee salary and device communication
keys — are encrypted at rest in the database, not stored as plain text,
using a per-installation encryption key generated automatically on first
run and stored alongside the database
(`%LOCALAPPDATA%\AttendanceManagementSystem\data\.field_encryption.key`).
This key is what both live encrypted columns and encrypted backups are
protected with — losing it means an encrypted backup from this machine
cannot be restored elsewhere, so back up that key file itself along with
your regular database backups if you rely on off-machine backup
restoration. It never needs to be entered or managed manually in normal
operation.

## 7. Optional REST API

A separate, optional process (not part of the desktop application, and
not started automatically) exposes the same company data over HTTP for
external integrations — payroll systems, custom dashboards, mobile
companion apps. It is **disabled by default**.

To enable it: set `API_ENABLED=true` (and optionally `API_HOST`/
`API_PORT`, default `127.0.0.1:8000`) in the environment, then run
`python run_api.py` from the application's installation — this is a
separate, deliberate step; installing or running the desktop application
alone never opens a network port.

Authentication works the same way as the desktop login (username,
password, and company), returning a bearer token used on every
subsequent request; the same roles and permissions granted through
Users/Roles govern what each API caller can do, with no separate
permission system to maintain. Set a strong, unique `APP_SECRET_KEY`
before enabling the API in any environment reachable by anyone other than
trusted administrators — this key signs every issued token, and the
built-in default is a placeholder meant to be overridden, not a real
secret.

## 8. Troubleshooting

| Symptom | Likely cause / fix |
|---|---|
| App won't start, no window appears | Check `%LOCALAPPDATA%\AttendanceManagementSystem\logs\` for the most recent log file and the error it recorded. |
| "تعذر الاتصال بقاعدة البيانات" (database connection error) at startup | The data folder or database file may be missing/corrupted, or in use by another process. Restore from a recent backup if needed. |
| Device shows disconnected | Confirm the device is powered on and reachable (ping its IP); confirm the IP/port configured in Devices still matches the device. |
| Scheduled device sync / backup never seems to run | Confirm the relevant `*_ENABLED` environment variable isn't set to `false`, and that the application process is actually kept running (it only runs while the desktop app is open — closing it stops the schedule until next launch). |
| Arabic text garbled in an exported PDF | Should not occur in a correctly built release — the app bundles its own Arabic-capable font and text-shaping libraries. If it does, the installation may be corrupted; reinstall. |
| Login screen shows no companies | No company has been set up yet, or all companies are marked inactive — check company setup / reactivate the company. |
| User locked out | Wait for the lockout window to pass, or have another administrator reset the account. |
| License shows expired | Contact your vendor for a renewed key and re-activate via the activation screen. |
| REST API returns 401 for a request you expect to succeed | Confirm the bearer token hasn't expired (`API_TOKEN_EXPIRES_MINUTES`) and that you're sending `Authorization: Bearer <token>` exactly as returned by `/api/auth/login`. |
| REST API returns 403 for a request you expect to succeed | The logged-in user's role doesn't have the required permission — grant it via Users → Roles, the same as you would for the desktop app. |

For issues not covered here, collect the relevant log file(s) from
`%LOCALAPPDATA%\AttendanceManagementSystem\logs\` before contacting
support — they contain the detail needed to diagnose most problems
quickly.
