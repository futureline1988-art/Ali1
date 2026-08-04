"""Shared Ed25519 signing infrastructure for the platform.

The file-based, machine-locked license system this package used to
implement (signed ``AMS1.<payload>.<signature>`` keys, local encrypted
storage, machine-ID binding) has been retired: the Attendance Client
no longer holds or verifies any license file at all. Subscriptions are
now server-managed data — see :mod:`server.models.subscription` and
:mod:`services.subscription_check_service` — created, renewed,
suspended, and reactivated only from the Developer Suite
(:mod:`developer_suite.services.subscription_service`), and checked by
the Attendance Client over HTTP at startup, never from a local file.

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
