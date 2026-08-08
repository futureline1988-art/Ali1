# Changelog

All notable changes to the Attendance Management System are documented in
this file.

## [Client 2.2.0] - 2026-08-08 — Payroll module + DELI ES172 on-device biometric enrollment

A full payroll system and real on-device biometric enrollment for DELI
ES172, built on top of the existing attendance/shift/employee/RBAC
architecture (no parallel systems, no new database engine, no
duplicated device connector).

### Payroll

- New `models.payroll` module: `PayrollAutomaticRule` (one row per
  company per late/early-leave/absence/missing-punch/overtime rule,
  each independently enabled/disabled with a configurable calculation
  method — fixed amount, per-minute, per-hour, half-day, full-day, or
  percentage of daily salary), `PayrollAdjustment` (manual or
  automatic deductions/bonuses, always carrying the reason, date,
  source, and — for automatic ones — the exact attendance figures and
  rule that produced the amount), `PayrollRun`/`PayrollRunLine` (one
  computed row per employee per month: base salary, attendance/absence
  days, late/early/overtime minutes, automatic and manual deduction
  totals, bonus total, net salary), and `PayrollRunSnapshot` (an
  immutable copy of every line captured at each finalization, so a
  later reopen-and-correct cycle can never silently rewrite payroll
  history).
- Attendance facts (late minutes, absence, missing punches, overtime)
  are always computed and recorded regardless of payroll settings — a
  financial deduction only ever happens if a manager has explicitly
  enabled that rule. Disabling a rule after the fact never
  retroactively changes payroll that has already been finalized.
- New "الرواتب" screen: compute/recompute a month's payroll, configure
  automatic rules, add/cancel manual deductions and bonuses per
  employee, review each employee's full calculation breakdown, and
  move a run through Draft → Reviewed → Finalized → (controlled)
  Reopened, with every step permission-gated (`payroll.view`,
  `payroll.manage_rules`, `payroll.manage_adjustments`,
  `payroll.finalize`, `payroll.reopen`) through the existing RBAC
  system.
- "إضافة خصم"/"إضافة مكافأة" actions on the Employees screen reuse the
  same payroll dialog and controller as the payroll screen — one real
  implementation behind both entry points.
- Three new report types (ملخص الرواتب / الخصومات / المكافآت
  والإضافات) on the existing Reports screen, built from real persisted
  `PayrollRunLine`/`PayrollAdjustment` rows, exported through the
  existing PDF/Excel/CSV pipeline.

### DELI ES172 on-device biometric enrollment

- `devices.deli_es172_device.DeliES172Connector` now also implements
  the document's biometric-enrollment commands: `BeginEnrollFace`/
  `BeginEnrollFp`/`BeginEnrollPalm`, `QueryJobStatus`, `CancelJob`,
  `CancelAllJobs`, and attaching a captured template to an employee
  via `SetUserInfo`. `BeginEnrollCard` and photo-to-template conversion
  are documented but intentionally not implemented — this project only
  ever captures biometrics at the physical device.
- New "تسجيل بصمة الوجه" flow (Employees screen) now works against
  either a ZKTeco device (existing heuristic flow, unchanged) or a
  DELI ES172 device (a real capture-then-attach job with a definitive
  device-reported result) depending on which device the operator
  selects; two new DELI-only actions, "تسجيل بصمة الإصبع" and "تسجيل
  بصمة الكف", cover fingerprint/palm enrollment the same way.
- `Employee.palm_registered` and `DeviceCapabilities.supports_palm` /
  `UserBiometricStatus.face_registered`/`palm_registered` extend the
  existing biometric-status model; the biometric-status dialog and
  "تحديث من الجهاز" refresh action show all three biometrics.

### Fixed

- **Overnight (midnight-spanning) shifts computed the wrong day's
  attendance.** A shift's punch-fetch window was always exactly one
  calendar day, so a check-out after local midnight (the normal case
  for a night shift) fell outside the day it belonged to — that day
  wrongly showed "missing check-out" while the *next* day wrongly
  showed "missing check-in" for the same punch, with worked hours,
  lateness, and overtime all computed as if the employee had not
  worked. The punch window now extends to the shift's actual end time
  for a midnight-spanning shift, and a punch already attributed to one
  day's record is never pulled into another day's computation.
- `PayrollPage`'s automatic-rules save silently discarded a failed
  save's error message a moment after showing it (an unconditional
  `clear_error()` ran right after), so a rejected rule change looked
  identical to a successful one. A failed save now leaves its error
  visible; a successful save now shows an explicit confirmation.

## [Client 2.1.0] - 2026-08-07 — Real DELI ES172 connector, built from the official SDK protocol document

The vendor's official "DELI offline SDK protocol" document is now the
source of truth for this integration (superseding the earlier blind
port/HTTP-interface investigation). DELI ES172 is a full, selectable
device type: read-only device info, employee download, and attendance
log download all work exactly per the documented commands and
pagination scheme. The ZKTeco and Hikvision connectors are unchanged.

### Why the on-device "API Key for SDK" field was blank

Per the document's "Security Configuration" section, the API key is
only enforced once `SetSecurityConfig` has explicitly set one -- "the
device will only accept commands if the key matches the currently set
value". A factory-fresh device has never had a key set, so the field is
blank by design, and read-only commands are expected to work with an
empty API key. `SetSecurityConfig` is **not implemented** in this
release: unlike `SetUserInfo` (which explicitly documents that fields
"can be omitted to keep the current value"), the document never states
that any `SetSecurityConfig` field (`api_key`/`enable_http`/`tls_conf`)
can be safely omitted, so sending it could risk disabling the very
HTTP interface this application depends on. It will only be added once
that is proven safe from a vendor-confirmed example.

### Added

- `models.enums.DeviceProtocol.DELI_ES172` -- selectable in the
  Attendance Client's "إضافة جهاز جديد" form alongside ZKTeco/Hikvision,
  with a hint about the default port (80) and the blank-API-key
  convention for an unconfigured device.
- `devices.deli_es172_device.DeliES172Connector` -- a full
  `DeviceConnector` implementation over the documented
  `POST /control?api_key=...` interface, covering every read-only
  command the document defines: `GetVersionInfo`, `GetDeviceUid`,
  `GetDeviceCapabilities`, `GetCapacityLimit`, `GetCurrentUsage`,
  `GetUserIdList`, `GetUserInfo`, `GetAttendLog` -- plus `SetUserInfo`
  for employee push. Pagination (`start_pos`/`next_page_pos` for users,
  `start_pos`/`next_pos` for attendance logs) follows the document's
  own communication-example tables exactly. Never sends
  `EraseAttendLog`, `ClearUsers`, `ClearAllData`, or `SetSecurityConfig`.
- `devices.device_manager.DeviceManager` now resolves
  `DeviceProtocol.DELI_ES172` to `DeliES172Connector`, so the existing
  protocol-agnostic `DeviceService` (connection test, employee push/
  pull, attendance sync) works for DELI devices with no service-layer
  changes.
- 15 new tests (`tests/test_deli_es172_connector.py`) against a local
  HTTP server that speaks the documented response envelope: connection
  test + capability reading, exact request-contract verification, the
  blank-API-key case, device-level error responses (`user_id_not_exists`
  handled as "not found", not a failure), full `GetUserIdList`/
  `GetAttendLog` pagination sequences, `SetUserInfo` push, and a check
  that no destructive or `SetSecurityConfig` command is ever sent.

### Known limitation

The document's `GetAttendLog` entries carry a verification method
(`mode`: face/fp/card/pwd) but no check-in/check-out direction field.
This is the same limitation the existing Hikvision connector already
has; every DELI attendance entry is reported as a check-in, and the
existing daily first-in/last-out aggregation in the service layer is
what derives the actual check-in/check-out pairing.

Preserves the existing ZKTeco and Hikvision connectors unchanged.

## [Client 2.0.4] - 2026-08-07 — First real DELI ES172 API call: GetVersionInfo

The device-discovered HTTP control interface is now called for real:
`POST http://<device-ip>/control?api_key=<API_KEY>` with the device-
confirmed, read-only `GetVersionInfo` command. This is the first actual
DELI ES172 API request the application makes -- still not a full
connector (no employee upload/download, no attendance log download, no
write/enrollment command of any kind), only a dedicated connection test
to prove the endpoint, authentication, and framing work against the
physical device.

### Added

- New "اختبار اتصال DELI" button on the Attendance Client's Devices
  page toolbar, opening a dedicated dialog: enter the device's IP and
  its "API Key for SDK", click "اختبار اتصال DELI", and see both the
  raw and parsed (pretty-printed JSON) response. Clearly distinguishes
  a wrong/rejected API key (HTTP 401/403), a timeout, a network error,
  a non-JSON response, an HTTP error status, and success -- each with
  its own precise Arabic message.
- `devices/deli_es172_device.py`: the client for this one command.
  Deliberately not registered as a `DeviceProtocol`/wired into
  `DeviceManager` yet -- DELI ES172 is not a general connector until
  more commands (device info, employee list, attendance log) are each
  confirmed against the real device the same evidence-driven way.
  Never sends anything but the confirmed request; the API key is never
  logged or displayed in plaintext in any error detail (it is redacted
  even from a raw network-failure exception message, which would
  otherwise echo the full request URL).
- 15 new tests: every response classification (success, 401, 403,
  invalid JSON, HTTP error, network error, timeout) against local
  throwaway HTTP servers, exact request-contract verification
  (path/headers/body), API-key redaction, and the dialog's own
  success/error/empty-field behavior. Full regression: 661/661.

Preserves the existing ZKTeco and Hikvision connectors unchanged.

## [Client 2.0.3 / Developer Suite 1.1.4] - 2026-08-07 — Deeper DELI ES172 port-80/5005 investigation, Developer Suite diagnostic page

A real on-device run of v2.0.2 confirmed the device is now at
192.168.1.8, port 5005 is open, and port 80's development interface has
exactly one link and no JavaScript resources. This release deepens the
investigation of both ports with real evidence still driving every
change -- **no DELI connector is implemented yet**; the protocol on
port 5005 remains unproven.

### Added

- The diagnostic now follows same-origin `<a href>` links the port-80
  page exposes, not just `<script src>`/`<link href>` resources --
  captures the full response behind "the exact URL behind the
  detected link".
- TLS handshake is now attempted on **every** open port, not just 443
  -- a proprietary service can be TLS-wrapped on any port.
- For any port that stays unidentified after HTTP/WebSocket/TLS all
  fail (port 5005 on the real device): a new, still strictly
  read-only investigation listens passively for up to 5 seconds (in
  case the service greets slowly or in multiple chunks) and sends one
  generic, protocol-agnostic probe (the same kind nmap's own
  service-detection uses) -- never a guessed DELI-specific command.
  Every byte sent and received is recorded in both hex and ASCII.
- A new **"تشخيص الأجهزة" (Device Diagnostics)** page in the Developer
  Suite, reusing the exact same diagnostic logic as the Attendance
  Client's Devices page -- for validating a device's network behavior
  from the Developer Suite before it ships to a customer, again with
  no separate Python install needed.
- The diagnostic's default pre-filled IP is now `192.168.1.8` (the
  device's current confirmed address) -- still just a convenience
  default; the IP field remains fully free-form everywhere, and
  nothing in the diagnostic or any future connector hard-codes a
  specific device address.

## [Client 2.0.2] - 2026-08-07 — DELI ES172 HTTP development-interface investigation

A real on-device diagnostic run (v2.0.1's in-app tool) confirmed port 4370
closed, port 5005 open but neither HTTP nor WebSocket, and **port 80
serving a real HTTP "Attendance & Access machine development test
interface"** (Boost.Beast). This release extends the diagnostic to
investigate that interface in detail -- still no connector is
implemented yet, and no protocol is assumed.

### Added

- The in-app "تشخيص شبكة الجهاز" diagnostic now, for any port that
  turns out to speak plain HTTP: fetches the full root page, parses its
  HTML (links, forms/actions/inputs, buttons, `<script>`/`<link>`
  references, inline scripts), and follows -- GET only, same-origin
  only, never guessed -- every JS/CSS file the page itself references.
  Extracts `fetch()`/`XMLHttpRequest`/WebSocket calls, endpoint-looking
  path strings, and possible authentication/API-key clues from all of
  it, and flags whether the page mentions port 5005 anywhere. Every
  request is a read-only `GET`; nothing on the device is ever written,
  enrolled, or reconfigured.
- The saved `.json` report now includes the complete page HTML, the
  complete content of every same-origin resource it fetched, and every
  finding above; the `.txt` report summarizes counts and short excerpts
  with a pointer to the full detail in the `.json` file.

## [Client 2.0.1] - 2026-08-07 — DELI ES172 network diagnostic, no Python required

### Added

- "تشخيص شبكة الجهاز" (Network diagnostic) button on the Devices page:
  a safe, protocol-agnostic TCP/HTTP/WebSocket network probe for
  investigating a device with no supported connector yet (built for
  the DELI ES172 investigation, generic enough for any device). It
  never sends a password, communication key, or write/enrollment
  command — only read-only reachability and protocol fingerprinting —
  and saves a JSON + TXT report the customer can send back for
  diagnosis. Runs entirely inside the already-installed application,
  so it needs no separately installed Python interpreter.

### Fixed

- The standalone diagnostic script (`tools/diagnostics/deli_es172_diagnose.py`
  and its `Run_DELI_Diagnostic.bat` launcher) required a system Python
  install and failed on a normal Windows PC with "Python was not
  found". The in-app button above is now the supported way to run this
  diagnostic on a customer machine; the standalone script remains for
  developers/CI and for checking a device before the application
  itself is installed.

## [Client 2.0.0] - 2026-08-05 — First standalone commercial edition

The Attendance Client's first release as a permanently-owned, one-time-
purchase desktop product: no subscription, no Company Code, no central
Attendance Server dependency for normal operation (`server/` and
`developer_suite/` remain in the repository as optional internal tools
only). This release folds together the standalone simplification, the
non-technical-user UX pass, and this final production-readiness pass
into one commercial-grade build.

### Added

- First-run setup wizard (company name/logo, brand colors,
  administrator account, optional attendance device with a mandatory
  connection test) — a brand-new installation is fully usable without
  reading any documentation.
- Rich, precisely-classified device connection diagnostics (wrong
  communication key, timeout, port closed, network unreachable,
  success) with device model/serial/firmware persisted on a successful
  test, and a one-line on-screen hint for finding a ZKTeco device's IP
  address on the device's own screen.
- Download employees from a device (`pull_employees_from_device`),
  completing the push/pull pair.
- Every long-running operation (device test/sync/push/pull, face
  enrollment, report generation, backup create/restore) now shows an
  indeterminate progress dialog with a plain-Arabic current-action
  message instead of silently freezing the window, and confirms
  success or failure afterward instead of completing silently.
- Friendly, human-readable backup names ("نسخة احتياطية بتاريخ ...")
  in place of raw filesystem paths in the backup list and status
  messages.

### Fixed

- `ZKTecoConnector._connect()` was not catching `ZKNetworkError` (a
  sibling of `ZKErrorConnection` in `pyzk`, not a subclass) — a real
  network failure during a device operation could previously propagate
  as an unhandled exception instead of a clean Arabic diagnostic.

### Removed

- `sync/` package, subscription checking, Company Code enrollment,
  server-issued initial-admin download, and every other client-side
  dependency on the central Attendance Server for normal operation.

## [Client 1.2.4 / Developer Suite 1.1.3 / Server 1.1.3] - 2026-08-05

### Changed — The Attendance Client can no longer create a company's first administrator

Every prior enrollment design still let the Attendance Client itself
decide who the very first user of a newly-onboarded company was. This
moves that decision exclusively into the Developer Suite, and adds a
Support Information section the vendor manages centrally:

- **Attendance Server**: `Subscription` gains six optional
  `support_*` columns (primary/secondary phone, WhatsApp, email,
  hours, message) — Developer-Suite-writable only, via a new
  `PATCH /api/v1/subscriptions/{id}/support-info`, and included in the
  existing device-facing `GET /api/v1/subscription/status` so the
  Attendance Client already gets them on every routine check. A new
  `InitialAdminAccount` table (one row per subscription, unique) holds
  a company's initial Company Administrator — created only via the new
  admin-scoped `PUT /api/v1/subscriptions/{id}/initial-admin` (which
  hashes the password immediately; the plaintext is never stored or
  re-transmitted) and `GET .../initial-admin` to check what's
  configured. The device-facing counterpart,
  `GET /api/v1/subscription/initial-admin`, lets any device already
  linked to that subscription download the same bcrypt hash — not
  consumed on fetch, so multiple independent installations of the same
  company can each bootstrap their own local admin account from it.
- **Attendance Client**: on a company's first-ever enrollment (its
  Company Code resolves, but no local `Company` exists here yet), the
  login screen now creates that local company itself, downloads the
  Developer-Suite-created initial administrator, and stores it as this
  installation's first local user (`system_admin` role) using the
  downloaded bcrypt hash directly — the plaintext the user just typed
  is only ever checked against that stored hash, never used to create
  it. If the Developer Suite has not configured an initial
  administrator yet, enrollment is blocked with a clear message
  instead of falling back to any local creation flow — there is none.
  From that point on, the administrator creates every additional user
  locally exactly as before. The login screen also gained a "Need
  Help? / Contact Support" button opening a dialog with the company's
  cached Support Information (or "No support information has been
  configured." if none is set) — populated from the same subscription
  check that already runs at every login/startup, so it stays visible
  offline too.
- **Developer Suite**: the Subscription Manager gained two toolbar
  actions — "Set Initial Administrator" (username, full name,
  password; shows what's currently configured, if anything) and
  "Support Information" (all six fields, pre-filled with the
  subscription's current values) — the only place either is ever set.

## [Client 1.2.3 / Developer Suite 1.1.2 / Server 1.1.2] - 2026-08-05

### Changed — Company Code replaces any company list or selection

The Attendance Server is multi-tenant, so the Attendance Client must
never display, list, or let a user pick from other companies. The
1.2.2 login screen still resolved a company via its exact name (no
list was shown, but a name is guessable and not secret). This replaces
that with an opaque, unique Company Code:

- **Attendance Server**: `Subscription` gains a unique, system
  -generated `company_code` (e.g. `FUTURELINE-7X4K9P`), created
  automatically the moment a subscription is created — never entered
  manually anywhere. `POST /api/v1/devices/self-register` now takes
  `company_code` instead of `company_name`. Its two "this code doesn't
  work" cases — no subscription exists for the code, or one exists but
  is suspended/expired — now return the *exact same* response (422,
  `"Invalid or inactive company code."`) so a caller can never tell
  "not a real code" apart from "real code, just inactive" by comparing
  error messages, which would otherwise let codes be enumerated one
  guess at a time. `GET /api/v1/subscriptions` and its create response
  include `company_code` for the Developer Suite to display; nothing
  else about the admin-token device/subscription endpoints changed.
- **Attendance Client**: the login screen's company field is now a
  plain Company Code text box, shown only on an installation's first
  -ever login — never a list or picker. The typed code is resolved
  with the server *before* local username/password authentication
  even runs (only the server knows which company a code belongs to);
  local authentication then proceeds exactly as before, against
  whichever local company the resolved name matches. Only once that
  local login succeeds is the device permanently bound to that company
  and the code saved locally (`ClientSyncCredential.company_code`) —
  every later login skips the code field entirely. The Settings
  screen's Subscription tab now shows the current company code and,
  for an administrator (`settings.manage`) only, a "Change Company
  Code" action — the sole way to change it, since there is no in-place
  "switch companies" flow: it resets this installation's enrollment
  entirely and requires restarting the application to re-enroll with a
  new code.
- **Developer Suite**: the Subscription Manager's table gained a
  Company Code column, and creating a subscription now shows the
  generated code in a confirmation dialog immediately, since it is
  never shown to the client on its own and must be handed to that
  company's administrator out of band.

## [Client 1.2.2] - 2026-08-05

### Changed — Company is now established by the first login, not preconfigured

The previous automatic-enrollment design (1.2.1) still required a
`SYNC_COMPANY_NAME` value to be preconfigured on each installation
before it could enroll — unworkable for a single central Attendance
Server onboarding many different companies, since a freshly installed
client has no way to already know which company it belongs to.

- `config.py`'s `SyncConfig.company_name` / `SYNC_COMPANY_NAME` is
  removed entirely. No company is ever preconfigured on the client.
- The login screen's existing company picker + username/password form
  is now also what establishes this installation's company: local
  authentication happens first (unchanged), and only once it succeeds
  does `SubscriptionCheckService.check_for_login` self-register this
  device to the authenticated company's active subscription (if not
  already enrolled) and permanently bind this device to that company.
- The subscription/device-enrollment check no longer runs before the
  login screen — it can't, since the company isn't known yet. It runs
  once, immediately after a successful login, and denies access with a
  clear message ("Maximum allowed devices reached.", "Subscription is
  suspended/expired," etc.) if it fails, logging the session back out.
- Once a device has completed this first-login enrollment, every
  future login skips the company picker entirely — it's locked to the
  bound company — and no re-registration is attempted; only a live
  subscription status check runs, same as before.
- `ClientSyncCredential` (the device's own local record of its
  Attendance Server identity) gained a `bound_company_id` column
  recording which local company a device was bound to.
- `SubscriptionBlockedWindow` is now shown after a denied login
  (previously: before any login, gating startup itself); closing it
  without a successful retry returns to the login screen rather than
  quitting the application, since there is always a login screen to
  fall back to.

## [Client 1.2.1 / Developer Suite 1.1.1 / Server 1.1.1] - 2026-08-05

### Added — Fully automatic device enrollment

A fresh Attendance Client installation now registers and links itself to
its company's subscription on first startup, with no administrator action
anywhere:

- **Attendance Server**: new unauthenticated `POST /api/v1/devices/self-register`
  endpoint (distinct from the existing admin-token-gated
  `POST /api/v1/devices/register`), scoped narrowly to
  `device_type=attendance_client` installations. Given only a device name
  and `company_name`, it looks up that company's subscription, rejects the
  request if none exists or it isn't currently active (422), rejects it if
  the subscription's device cap is already reached (403, "Maximum allowed
  devices reached."), and otherwise creates and links the device
  immediately. `GET /api/v1/devices` and `POST /api/v1/devices/register`
  responses now also include each device's linked `company_name`.
- **Attendance Client**: `config.py`'s `SyncConfig.company_name`
  (`SYNC_COMPANY_NAME`) is now the only input a fresh installation needs.
  `SubscriptionCheckService` drives the entire first-run sequence itself —
  connect, self-register if unknown, and check live subscription status —
  so the same "Retry" action on the blocked-access screen also retries
  enrollment. Clear, specific messages are shown when no subscription
  exists for the configured company and when the device cap is reached.
  The old admin-token bootstrap field (`SyncConfig.bootstrap_admin_token` /
  `SYNC_BOOTSTRAP_ADMIN_TOKEN`) is removed, since it's no longer needed for
  this flow.
- **Developer Suite**: the Monitoring page's device and recent-registrations
  tables gained a "الشركة" (Company) column, so a newly self-registered
  device's subscription link is visible immediately with no separate
  linking step.
- Existing admin-token-based enrollment (`ClientSyncCoordinator.enroll`,
  `POST /api/v1/devices/register`) is unchanged and continues to work
  exactly as before; self-registration is a purely additive, parallel path.

## [Client 1.2.0 / Developer Suite 1.1.0 / Server 1.1.0] - 2026-08-04

### Changed — Replaced file-based licensing with server-managed subscriptions

The Ed25519-signed, machine-locked license-key system has been removed
entirely and replaced with company subscriptions stored on the Attendance
Server:

- **Attendance Server**: new `Subscription` model (company name, start/end
  date, Active/Suspended status, max devices, optional max users) with a
  full admin CRUD API (`/api/v1/subscriptions`) and a device-facing status
  check (`GET /api/v1/subscription/status`, authenticated the same way as
  sync push/pull). Device registration now optionally enforces a
  subscription's device cap when `company_name` is supplied.
- **Developer Suite**: the License Manager module, its issuance/renewal/
  revocation UI, and the local `IssuedLicense` model/repository are gone,
  replaced by a Subscription Manager module (create/renew/suspend/
  reactivate, all server-side). The dashboard and reporting modules'
  license-specific charts/reports are replaced by subscription-status
  equivalents (active/suspended/expired breakdown, upcoming expirations,
  a subscription report).
- **Attendance Client**: no longer holds, verifies, or activates a local
  license file. At startup, a new `SubscriptionCheckService` asks the
  Attendance Server for this installation's subscription status (reusing
  its existing device sync credential) and caches the last confirmed
  result. If the server is unreachable, the installation may keep running
  for a 7-day grace period since the last successful check; an explicit
  suspended/expired verdict from the server always applies immediately,
  regardless of any remaining grace. Clear messages are shown for every
  outcome ("Subscription expired," "Company suspended," "Maximum devices
  reached," etc.). The old license activation window and the Settings
  screen's License tab are replaced by a minimal blocking status screen
  and a read-only Subscription tab, respectively.
- Device enrollment (`ClientSyncCoordinator.enroll`/`sync.client.register_device`)
  now carries a `company_name`, matched against the Attendance Server's
  subscription records; `config.py` gained `SyncConfig.company_name`
  (`SYNC_COMPANY_NAME` environment variable).
- Removed the now-obsolete `licensing/` modules (`license_key.py`,
  `license_service.py`, `license_store.py`, `license_generator.py`,
  `keys.py`, `machine_id.py`, `enums.py`, `validator/`) — the shared
  Ed25519 signing primitives (`licensing/crypto/signing.py`) remain,
  since the Developer Suite's software-update package signing still
  depends on them.
- Existing customer and company management, and every other module, are
  unaffected by this migration.

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
- **Unify the licensing system on a single format, library, and keypair**
  (this release): audited the whole licensing surface end to end after
  the two fixes above and confirmed the Attendance Client and the
  Developer Suite already shared one implementation for everything
  that matters — the `AMS1.<payload>.<signature>` key format
  (`licensing/license_key.py`), the Ed25519 signing/verification
  algorithm (`licensing/crypto/signing.py`), and machine-ID generation
  (`licensing/machine_id.py`) are each a single module both
  applications import, not separate copies. The one real gap found was
  dead code, not a second live format: `models/license.py` and
  `repositories/license_repository.py` were a per-company,
  `String(255)`-keyed license model predating the shared `licensing/`
  package, never wired into any service, controller, or UI in this
  codebase (confirmed unused by grepping the whole tree), and had it
  ever been connected it would have silently truncated a real signed
  key on save. Removed both files, the `licenses`
  relationship/`current_license` property off `Company`, and dropped
  the orphaned `licenses` table via a new Alembic migration
  (`551b9791696c`) — a clean removal, not a data migration, since the
  table was never populated by any live code path. Added
  `tests/test_licensing_phase1.py::TestSingleUnifiedLicensingSystem`
  (asserts the legacy model/table/relationship are gone and that both
  applications resolve to the exact same `get_machine_id` function
  object) and
  `tests/test_license_issuance_end_to_end.py::TestFullWorkflowThroughTheRealUiWidgets`
  (drives the real `LicenseKeyDialog` export-to-file and copy-to
  -clipboard actions, then pastes the result into the real
  `LicenseActivationWindow` and activates — proving the exported/copied
  key round-trips without truncation and activates on a clean client).
  `config.py`'s `AppConfig.app_version` bumped to `1.1.2` for the
  migration.
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
