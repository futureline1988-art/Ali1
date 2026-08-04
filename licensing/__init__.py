"""Machine-locked application licensing.

The single, shared licensing library for the whole platform: the
Attendance Client verifies license keys against this package, and the
vendor's Developer Suite signs them through it (see
:mod:`licensing.crypto.signing`) — there is exactly one license format,
one Ed25519 keypair, and one machine-ID algorithm, both applications
import from here rather than each keeping their own copy. This package
protects the *installed application itself*, on *this machine*, before
any company or user is even known: Trial / Monthly / Yearly / Lifetime
activation, verified at process startup, independent of the database
entirely (so a missing or corrupt license never depends on — and never
touches — attendance, employee, device, or reporting logic).

Modules:
    machine_id: Stable per-machine fingerprint used to bind a license —
        the single implementation both applications call.
    enums: :class:`~licensing.enums.LicenseType` and status codes.
    keys: The application's embedded Ed25519 public key (verification
        only — the matching private key never ships with the app).
    license_key: Signed license key encode/decode (vendor-side signing,
        app-side verification) — the one ``AMS1.<payload>.<signature>``
        format; there is no other.
    license_store: Encrypted local persistence for the activated
        license, keyed to this machine.
    license_service: The high-level API — ``get_status()``,
        ``activate()``, ``start_trial()`` — everything else in this
        package builds on. This is the only module the Attendance
        Client imports from this package.
    license_generator: An offline, vendor-only CLI for issuing signed
        license keys. Never imported by the running application.

Subpackages:
    crypto: Private-key Ed25519 operations (keypair generation,
        signing), used by the Developer Suite
        (:mod:`developer_suite.services.license_service`). Never
        imported by the Attendance Client — only an app holding the
        private key has any reason to (verified by an isolation test).
    validator: Pure, side-effect-free predicates
        (``version_check.is_version_licensed``,
        ``developer_mode.is_developer_mode_permitted``).
"""
