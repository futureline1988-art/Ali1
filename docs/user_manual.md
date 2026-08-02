# Attendance Management System — User Manual

## 1. Getting Started

### 1.1 Launching the Application

Start the application from the Desktop shortcut or the Start Menu entry
created by the installer ("Attendance Management System"). On first
launch, you will see a **license activation** screen if the software has
not yet been activated on this computer — enter the license key you were
given and click Activate. Once activated (or on every subsequent launch
once already activated), you will see the **login screen**.

### 1.2 Logging In

1. Select your **company** from the dropdown — this list shows every
   company registered on this installation.
2. Enter your **username** and **password**.
3. Click **تسجيل الدخول (Log In)**, or press Enter.

If your account has been locked after repeated failed login attempts, wait
for the lockout period to pass, or contact your administrator to unlock it.
Your session will automatically log out after a period of inactivity — you
will be returned to the login screen and asked to sign in again.

## 2. The Main Window

After logging in, you land on the **Dashboard**. The sidebar on the right
(the app runs right-to-left) lists every section you have permission to
access:

- **لوحة التحكم (Dashboard)** — live overview.
- **الموظفون (Employees)** — employee records.
- **الحضور والانصراف (Attendance)** — punches and daily attendance.
- **الأقسام (Departments)** — the department tree.
- **الأجهزة (Devices)** — biometric device management.
- **التقارير (Reports)** — generate and export reports.
- **المستخدمون (Users)** — user accounts, roles, and permissions.
- **الإعدادات (Settings)** — company profile, preferences, backups.

The top bar shows your name and role, a theme toggle (light/dark), and a
logout button. Which sections you see depends on the permissions your role
has been granted — if a section is missing, ask your administrator.

## 3. Dashboard

The dashboard gives an at-a-glance view of the current state of your
company: number of active employees, number of departments, device
connection status, and a breakdown of today's attendance (present, late,
absent, on leave). It refreshes automatically and requires no action from
you.

## 4. Employees

- **Search**: use the search box to filter by name, employee code, or
  department.
- **Add an employee**: click Add, fill in the form (name, department,
  shift, contact details, hire date, etc.), and save. A QR code and
  barcode are generated automatically for the new employee — use these on
  printed badges for devices/kiosks that scan codes instead of biometrics.
- **Edit / deactivate**: select an employee to edit their details, or
  deactivate them if they've left the company (deactivating preserves
  their historical attendance records — it does not delete anything).

## 5. Departments

Departments are organized as a tree (parent/child), matching your
company's real organizational structure. You can:

- Add a department, optionally under a parent department.
- Reorder departments by dragging them within the tree.
- Assign employees to a department from the Employees screen.

## 6. Attendance

- **Automatic**: punches synced from a connected biometric device appear
  here automatically and are used to compute each employee's daily status
  (present, late, early leave, overtime) against their assigned shift.
- **Manual entry**: for employees without a device (or to correct a
  missed punch), use "Add Manual Entry" and select the employee, date,
  and time(s).
- **Daily status** is computed in your company's configured time zone,
  taking holidays and approved leave into account.

## 7. Devices

- **Add a device**: enter its IP address, port, and protocol (ZKTeco or
  Hikvision), then **Test Connection** to confirm the app can reach it
  before saving.
- **Sync**: pull new punch records from the device into the system.
- **Push employees**: send employee records to the device so it
  recognizes them at the terminal (fingerprint/face/card enrollment
  itself still happens on the device's own hardware).

If a device shows as disconnected, check that it's powered on and
reachable on the network from the computer running this application (same
subnet, or routed/firewalled to allow the connection), and that the
IP/port configured here still matches the device.

## 8. Reports

Six report types are available: attendance summary, by employee, by
department, late arrivals, overtime, and absences. For each:

1. Choose the report type and a date range (and department/employee
   filter, where applicable).
2. Click **Generate**.
3. Export to **Excel**, **PDF**, or **CSV** using the export buttons —
   PDF reports render Arabic text correctly, right-to-left, exactly as it
   appears elsewhere in the application.

## 9. Users (if you have Users management permission)

Administrators can create additional user accounts, assign them a role,
and customize what each role can see and do via the permission catalog
(e.g. a role that can view but not edit attendance). See the
**Administrator Manual** for the full permissions/roles reference.

## 10. Settings

- **Company profile**: name, logo, contact details.
- **Preferences**: time zone, date format, currency, and other
  display defaults used throughout the app.
- **Backup & restore**: create a backup of the database on demand, or
  restore from a previous backup file. See the Administrator Manual for
  details and recommended backup practice.

## 11. Getting Help

If you encounter an error message, note exactly what it says and what you
were doing when it appeared, and contact your system administrator or IT
support. Application logs (useful for diagnosing issues) are stored under
`%LOCALAPPDATA%\AttendanceManagementSystem\logs\` on Windows.
