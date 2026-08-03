# Architecture Gap Analysis — Commercial Platform (Developer Suite + Attendance Client)

**Status: DRAFT — awaiting approval. No implementation has started.**

This document inspects the existing, released Attendance Management System v1.0.2 codebase and maps it against the new requirement: evolve it into a two-application commercial desktop platform (the existing **Attendance Client**, unchanged in spirit, plus a new **Developer Suite** for managing every customer). Per instruction, nothing below has been implemented — this is the analysis to review before any code is written.

---

## 1. What Already Exists

| Area | Current state |
|---|---|
| **Multi-tenancy** | `models/company.py` `Company` is the tenant root. Every tenant table mixes in `CompanyScopedMixin` (`models/base.py`). `CompanyScopedRepository` (`repositories/base_repository.py`) is constructed with a fixed `company_id` and scopes every query/write to it, raising on cross-tenant writes. `CompanySettings` is a 1:1-per-company preferences row. |
| **RBAC** | Two-tier: a global `Permission` catalog (`models/permission.py`, seeded once by `main.py:_seed_default_permissions`) + per-company `Role` rows (`models/role.py`, one row per company even for the 5 built-in `UserRole` kinds, so editing one company's role never touches another's). Enforced via `controllers/base_controller.py:requires_permission(...)` (desktop) and `api/dependencies.py:require_permission(...)` (REST API) — same permission codes, same source of truth. No cross-company "superadmin" role exists anywhere. |
| **Theming** | `ui/theme.py`: two hardcoded palettes (light/dark), a `ThemeManager` singleton applying one QSS stylesheet to `QApplication`. Font family/size come from `config.ui`. No per-company override, no logo, no custom accent color mechanism exists today. |
| **Company branding** | `Company.logo_path` (a filesystem path field) exists and has a file picker in `ui/settings.py`'s `CompanyInfoTab`, but nothing else in the codebase (report generation, UI header) actually reads it — defined but not consumed. |
| **Backup/restore/export** | `services/backup_service.py`: encrypted (Fernet), WAL-consistent SQLite snapshots via SQLite's native backup API, for the single shared installation-level database (not per-company). `utils/excel.py`/`utils/csv_export.py` export only — no import capability exists anywhere in the app today. |
| **Database** | One shared SQLite/PostgreSQL/MySQL database per installation (dialect-agnostic via `config.DatabaseDialect`), tenant isolation via `company_id` columns, not separate files. `session_scope()` (`database/database.py`) is the one session pattern used identically by desktop controllers and the REST API. |
| **REST API** | `api/` (FastAPI, `run_api.py` entrypoint) — separate optional process, not started by the desktop app. 6 routers today (auth, companies, employees, departments, attendance, dashboard), reusing the exact same service layer as the desktop app. Auth via the same signed-token scheme as desktop sessions. |
| **Scheduler** | `services/scheduler_service.py`: `APScheduler BackgroundScheduler` running exactly two jobs (device sync, auto-backup) via `add_job(...)`. No plugin registry, but adding a new periodic job is a one-line, well-established pattern. |
| **Update mechanism** | None. No version-check code, no auto-update, anywhere in the codebase. |
| **Encryption** | `utils/encryption.py`: Fernet, installation-local key (`<data_dir>/.field_encryption.key`, 0600 permissions, never embedded in the binary) — used for encrypted DB columns and backups. `utils/security.py`: bcrypt password hashing, and a dependency-free signed/expiring HMAC-SHA256 token scheme used by both desktop sessions and the REST API. |
| **Audit log** | `models/audit_log.py` `AuditLog`: generic `entity_type`/`entity_id`/`changes` JSON shape, nullable `company_id` for platform-level events. Already written by nearly every service. Schema is already general enough to log developer-initiated cross-company actions with zero changes. |
| **Offline licensing (`licensing/`)** | The package this session already built: Ed25519 signature verification, `machine_id` fingerprinting, encrypted local `LicenseStore`, `LicenseService` (activate/renew/trial/deactivate/transfer-request). Fully offline. Public key only ships in the app; private key is vendor-only, gitignored, never committed. |
| **DB-backed `License` model (`models/license.py`)** | A **separate, currently unused** concept: a per-company entitlement row (max users/devices/branches, feature flags) inside the shared database. Confirmed by full-repo grep: no service, controller, or UI constructs `LicenseRepository` or touches this table. It predates (or was designed for a different future than) the offline Ed25519 system that actually gates the app today. **Left untouched — see §2.** |

---

## 2. What Should Never Change

- **The multi-tenancy and RBAC model.** `CompanyScopedMixin`/`CompanyScopedRepository`, the permission-catalog + per-company-role pattern, and every existing controller/service/repository built on them. The Developer Suite's notion of "company" (a customer record it manages) is a **separate, parallel concept** living in the Developer Suite's own database — it must never be confused with, or implemented as a modification of, the Attendance Client's `Company` table.
- **The existing offline license verification flow.** `licensing.validator` (formerly `license_service.py`) continues to gate app startup in `main.py` exactly as today: Ed25519 signature check against the embedded public key, no network dependency, no behavior change for any currently-installed v1.0.2 customer.
- **Database schema and session architecture** (`session_scope`, dialect-agnostic engine, WAL pragmas) — untouched.
- **The existing PyInstaller/Inno Setup/CI pipeline for the Attendance Client** (`packaging/pyinstaller/main*.spec`, `packaging/installer/setup.iss`, `.github/workflows/windows-release.yml`) — produces the exact same `Setup.exe`/`Portable.exe` it does today. The Developer Suite gets its **own, separate** packaging pipeline; nothing here is repurposed or merged.
- **`models/license.py`/`repositories/license_repository.py`.** Confirmed vestigial (§1). Left completely alone — not repurposed, not deleted, not wired up as part of this platform. Reusing it would conflate two different licensing systems (per-installation offline activation vs. an unused per-company DB entitlement row) and is out of scope unless you separately decide to build a multi-tenant-per-installation SaaS tier later.
- **A framing point, not a code change:** the new spec says customers must never have access to "License renewal." The existing `LicenseService.renew()` — where a customer *pastes a vendor-issued renewal key into their own app* — is not in conflict with this: the customer never gains the ability to *mint* a key (only the vendor's private key, held solely by the Developer Suite, can do that). This existing method is preserved unchanged.

---

## 3. What Can Be Reused

- **`licensing/`** — the natural foundation for the Developer Suite's License Manager. Extended (not replaced) with a new private-key signing module the Attendance Client never imports.
- **`utils/encryption.py`** (Fernet, local key-at-rest pattern) — directly reusable for encrypting the Developer Suite's own local database and any remotely-pushed secrets.
- **`utils/security.py`'s signed-token scheme** — directly reusable as the authentication mechanism between Attendance Client and Developer Suite (already proven across both desktop sessions and the REST API).
- **`services/backup_service.py`'s encrypted-snapshot pattern** — directly reusable, unmodified approach, for the Developer Suite's own "Backup database / Restore backup."
- **`utils/excel.py`** — reusable for "Export Excel"; "Import Excel" is new code (nothing to reuse there) but follows the same openpyxl foundation.
- **`models/audit_log.py`'s schema** — reusable as-is for auditing Developer Suite actions; zero schema changes needed.
- **`services/scheduler_service.py`'s APScheduler wiring** — directly reusable pattern for a new periodic "check in with Developer Suite" job.
- **The existing REST API (`api/`)** — the natural base for the sync/communication layer (§6) rather than inventing a parallel protocol from scratch.
- **`ui/theme.py`'s `Palette`/`ThemeManager`/QSS pipeline** — extended, not replaced, to support a per-company override layer.
- **Windows packaging conventions** (spec structure, version_info.txt, Inno Setup script, CI smoke-test pattern) — reused as a *template* for the Developer Suite's own installer, never as shared binaries.

---

## 4. What New Modules Are Required

### 4.1 Shared libraries (importable by both applications)

- **`licensing/`, extended** — `crypto/signing.py` (private-key operations; imported by the Developer Suite and the offline `license_generator` CLI only), `validator/version_check.py` (compares the running app version against a license's optional version cap), `validator/developer_mode.py` (pure, side-effect-free: `is_frozen()`, `is_developer_mode_permitted(environment)` — the safety primitive Developer Build consumes later).
- **`remote_config/`** (new) — data model for the "Remote Configuration" list (company name, logo, colors, fonts, theme, language, print/attendance/shift/holiday/device/backup settings) + serialization + a `Backend` protocol mirroring the existing `LicenseBackend` extension-point pattern, so the transport is swappable.
- **`branding/`** (new) — logo file handling (validate/copy/resize), an extended palette structure with per-company overrides, integration into `ui/theme.py`'s existing pipeline and into report/PDF generation (which does not consume `logo_path` today).
- **`update_service/`** (new) — version-compare logic (reusing `licensing.validator.version_check`'s parser), download+verify+apply flow for "Force updates."
- **`sync/`** (new) — the actual transport: a small set of authenticated endpoints (check-in/heartbeat, pull-pending-config, report crash log) built on `utils/security.py`'s signed-token scheme. Client side lives in the Attendance Client; server side extends the existing `api/` FastAPI app, hosted by the Developer Suite side of the platform.
- **`monitoring/`** (new, mostly Developer-Suite-local) — aggregation over the Developer Suite's own database (license/customer health) plus ingestion of check-in pings for online/offline status and "last synchronization."

### 4.2 Developer Suite (new, standalone application)

Own PySide6 desktop app, own entry point, own PyInstaller spec/installer, **own local database** (never the same file as any customer's Attendance database). Holds the Ed25519 private key, encrypted at rest, passphrase-gated — the only place in the platform it is allowed to exist. UI modules map 1:1 to the requirements: Customer Management, License Manager, Remote Configuration, Remote Management, Monitoring dashboard.

### 4.3 Attendance Client — additive changes only

- A new **opt-in, disabled-by-default** background job (via the existing `SchedulerService`) for periodic check-in/config-pull. Disabled until an installation is actually paired with a Developer Suite instance, so every existing v1.0.2 customer's behavior is unchanged unless they opt in.
- `ui/theme.py` gains an optional company-override layer, falling back to today's fixed palettes if nothing has been pushed.
- Consumes the shared library's version-check and (later, per your existing Developer Build spec) developer-mode bypass.
- **No existing screen, controller, service, model, or repository is rewritten.** This is exclusively new files plus a handful of additive call sites in `main.py` (same pattern as today's `LicenseService`/`SchedulerService` wiring).

---

## 5. Database Impact

- **Attendance Client:** zero schema changes required for version-check or developer-mode. If/when per-company branding needs to persist locally (so it survives without connectivity), that is a small, additive, backward-compatible Alembic migration (nullable columns, or one new table) — never touching or dropping existing columns, exactly like every prior schema change in this project.
- **Developer Suite:** an entirely new, separate SQLite database — new schema from scratch (Customer, IssuedLicense with real persisted history so "revoke" is trackable — unlike today's stateless Ed25519-only flow, RemoteConfigProfile, SyncCheckIn). Never shares a file with any customer database.

---

## 6. Communication Between Applications

Your spec explicitly requires "Push configuration/branding/themes," "Force updates," "Online/offline status," and "Last synchronization" for every managed company. These are meaningless without genuine network communication — this cannot be done purely offline the way license activation is today. This is the one area of the platform that is a **real architectural fork**, so I'm proposing a specific approach rather than leaving it open:

**Proposal:** extend the existing `api/` FastAPI app (already built, already unused by the desktop app itself, already has the exact auth primitive needed) into the Developer Suite's sync backend. The Attendance Client's `SchedulerService` gets a new opt-in periodic job that calls a small number of authenticated endpoints (check-in, pull-pending-config, report status), applies anything pending, and reports version/last-seen back. **Offline is a first-class, permanent, expected state** — a customer who can never be reached still runs exactly as they do today; sync failures are logged, never block startup or normal operation (mirroring the honest offline-first framing `licensing/license_service.py` already uses).

This requires you to host something reachable from customer machines (even a small VPS) — a deployment/business decision on your side, not just a code decision. **This is the one item I most want your explicit sign-off on before building anything**, since it's the one part of this platform that isn't purely local desktop software.

---

## 7. Security Model

- The Ed25519 **private key exists only inside the Developer Suite**, encrypted at rest, passphrase-gated at startup, never logged or exported. Signing happens exclusively in that process. The Attendance Client ships only the public key, unchanged from today.
- Sync channel: HTTPS in production, signed/expiring bearer tokens (reusing `utils/security.py`), scoped per-installation — each Attendance Client instance gets its own credential (issued at license activation) so a compromised customer machine can't impersonate another.
- The Developer Suite's own local database is encrypted at rest via the same `utils/encryption.py` Fernet pattern already used for backups.
- No RBAC inside the Developer Suite itself (single operator, per your spec) — but it still needs its own login/passphrase gate to protect the private key if the machine is ever shared or stolen.
- **Developer Build:** bypass logic lives in the shared library as a pure predicate (`is_frozen()` + explicit opt-in flag). A CI assertion (extending the existing `windows-release.yml` smoke tests) fails the Release build automatically if developer-mode is ever true in a frozen artifact — enforced on every CI run, not just trusted at review time.

---

## 8. Deployment Strategy

- **Attendance Client:** no change to the existing pipeline beyond the small additive integration points in §4.3. Same `Setup.exe`/`Portable.exe`, same GitHub Release flow already proven through v1.0.2.
- **Developer Suite:** a new, separate PyInstaller spec + Inno Setup script, modeled on the existing ones but never sharing spec files with the customer-facing build (must never risk bundling private-key-handling code into a customer artifact). Given it's single-operator tooling handling sensitive key material, I'd recommend it's **never published as a public GitHub Release** — either built locally on your own machine per `BUILD_WINDOWS.md`, or a private CI artifact only you download. Flagging this as a recommendation, not a decision I've made for you.
- **Sync backend** (if §6 is approved): needs real hosting — your decision, out of scope for me to provision.

---

## Open Decisions Requiring Your Approval

1. **§6 Communication model** — extend `api/` as the sync backend, opt-in check-in job, graceful offline degradation. Requires you to host something reachable from customer machines.
2. **§8 Developer Suite distribution** — never a public Release; local or private-artifact build only.
3. **§5 Branding persistence** — a small, additive, backward-compatible Alembic migration on the Attendance Client's existing database.
4. **Proposed phase sequence** (each committed separately, per your instruction):
   - **Phase 1** — Shared licensing library extension (`crypto/signing`, `version_check`, `developer_mode`). Pure library code, zero app behavior change, fully backward compatible.
   - **Phase 2** — Developer Suite skeleton: Customer Management + License Manager modules, own database, own app. No sync yet.
   - **Phase 3** — Remote Configuration + Branding data model, editable in Developer Suite. No live sync yet — exportable/appliable via file if you want to defer the network buildout.
   - **Phase 4** — Sync layer (`api/` extension + Attendance Client scheduler job) — only once §6 is approved.
   - **Phase 5** — Update Service ("Force updates") — depends on Phase 4.
   - **Phase 6** — Developer Build mode integrated into the Attendance Client (bypass + red banner + CI build-fail guard).
   - **Phase 7** — Documentation, tests, Windows installer for the Developer Suite.

Nothing above has been implemented. I'll wait for your go-ahead (and any corrections) before starting Phase 1.
