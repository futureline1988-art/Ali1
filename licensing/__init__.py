"""Machine-locked application licensing.

Independent of, and not to be confused with, :class:`~models.license.License`
(a per-company SaaS subscription-limits record living in the app's own
database — user/device/branch caps, feature flags — enforced once a
company already exists). This package instead protects the *installed
application itself*, on *this machine*, before any company or user is
even known: Trial / Monthly / Yearly / Lifetime activation, verified at
process startup, independent of the database entirely (so a missing or
corrupt license never depends on — and never touches — attendance,
employee, device, or reporting logic).

Modules:
    machine_id: Stable per-machine fingerprint used to bind a license.
    enums: :class:`~licensing.enums.LicenseType` and status codes.
    keys: The application's embedded Ed25519 public key (verification
        only — the matching private key never ships with the app).
    license_key: Signed license key encode/decode (vendor-side signing,
        app-side verification).
    license_store: Encrypted local persistence for the activated
        license, keyed to this machine.
    license_service: The high-level API — ``get_status()``,
        ``activate()``, ``start_trial()`` — everything else in this
        package builds on.
    license_generator: An offline, vendor-only CLI for issuing signed
        license keys. Never imported by the running application.
"""
