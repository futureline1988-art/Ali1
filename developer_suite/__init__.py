"""Developer Suite — the platform administration application.

**Not an independent product.** This is the administration front-end
for the same commercial platform the Attendance Client (the top-level
``main.py``/``ui``/``services``/... packages) is the customer-facing
half of. See ``docs/PLATFORM_ARCHITECTURE_GAP_ANALYSIS.md`` for the
full architecture this implements.

Ownership boundary (this is the one rule every module in this package
must respect):

* **This package owns platform administration data only** — customer
  accounts, license records, platform settings, audit logs, remote
  configuration, update metadata. Its own database (see
  :mod:`developer_suite.database`) never contains a single row of
  customer *operational* data.
* **Customer operational data — employees, attendance, departments,
  shifts, reports, and everything else a company actually runs on —
  always lives in that customer's own Attendance Client database.**
  This package never reads or writes it directly, now or in any future
  phase; any interaction with it happens, if ever, through whatever
  API surface a later phase defines (see the Gap Analysis's
  "Communication Between Applications" section), not a shared
  database connection.

This is a single-operator tool ("used only by me" per the approved
spec) — it has no multi-user RBAC of its own, unlike the Attendance
Client.

Phase 2 scope (current): foundation only — project structure, main
window, navigation, module interfaces, a shared service-layer base, a
dependency-injection container, and a configuration system. Every
module under :mod:`developer_suite.modules` is an empty placeholder in
this phase; no business logic, synchronization, customer-application
communication, licensing changes, or remote administration exist yet.

Subpackages:
    config: This application's own configuration, built by composing
        the genuinely reusable pieces of :mod:`config` (the Attendance
        Client's config module) — ``DatabaseConfig``,
        ``SecurityConfig``, ``LoggingConfig``, ``Environment`` — with a
        small set of fields specific to this application (its own data
        directory, database file, app name/version). Never touches
        :func:`config.get_config`'s process-wide singleton.
    database: This application's own, independent database — same
        :class:`database.database.Database` engine/session machinery
        the Attendance Client uses, pointed at a completely separate
        SQLite file with its own (currently empty) schema.
    container: A minimal dependency-injection container wiring
        configuration, the database, and module instances together for
        the UI layer.
    services: The shared service-layer base every concrete platform
        service (customer management, license issuance, ...) will
        build on starting in a later phase.
    modules: The five platform modules (Customer Management, License
        Manager, Remote Configuration, Monitoring, Update Manager) as
        placeholder implementations of :class:`~developer_suite.modules.base.PlatformModule`.
    ui: The main window and navigation sidebar.
    main: Process entry point.
"""
