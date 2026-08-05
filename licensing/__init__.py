"""Shared Ed25519 signing infrastructure for the platform.

The file-based, machine-locked license system this package used to
implement (signed ``AMS1.<payload>.<signature>`` keys, local encrypted
storage, machine-ID binding) has been retired, as has the later
server-managed subscription system that replaced it: the Attendance
Client is now a permanently-owned, one-time-purchase product that
never holds, verifies, or checks any license or subscription at all,
locally or over the network — see ``main.py``'s and
:mod:`ui.first_run_wizard`'s own docstrings. ``server/`` and
``developer_suite/`` keep their own subscription/customer-management
data for internal use, entirely decoupled from the Attendance Client's
runtime.

What remains here is genuinely shared, license-format-agnostic
infrastructure that other, still-active parts of the platform depend
on:

Subpackages:
    crypto: Generic Ed25519 keypair/signing primitives
        (:mod:`licensing.crypto.signing`), used by the Developer
        Suite's *software update* package signing
        (:mod:`developer_suite.services.update_manager_service`) and
        verified by the Attendance Client's update installer
        (:mod:`updates.verifier`) — nothing here is specific to the
        retired license format; it only ever generates/signs/verifies
        raw bytes.
"""
