"""Offline, vendor-only CLI for issuing signed license keys.

Never imported by the running application — this is a tool the
software vendor runs by hand (or from an internal issuing system) on a
machine that holds ``licensing/vendor/private_key.pem``, which must
never ship with the application itself.

Usage::

    # One-time setup: generate the signing keypair (only if one does
    # not already exist - re-running this invalidates every key issued
    # under the old keypair).
    python -m licensing.license_generator generate-keypair \\
        --private-key-out licensing/vendor/private_key.pem \\
        --public-key-out /tmp/public_key.pem

    # Issue a one-year key for a customer, locked to their machine ID:
    python -m licensing.license_generator issue \\
        --private-key licensing/vendor/private_key.pem \\
        --customer "Jane Doe" --company "Acme Co" \\
        --type yearly \\
        --machine-id A1B2C3D4E5F6A1B2C3D4E5F6A1B2C3D4

    # Issue a floating lifetime key (activates on whichever machine
    # uses it first):
    python -m licensing.license_generator issue \\
        --private-key licensing/vendor/private_key.pem \\
        --customer "Acme Co" --type lifetime
"""

from __future__ import annotations

import argparse
import sys
import uuid
from datetime import date, timedelta
from pathlib import Path

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import load_pem_private_key

from licensing.enums import LicenseType
from licensing.license_key import LicensePayload, encode_license_key

_EXPIRY_DAYS_BY_TYPE: dict[LicenseType, int | None] = {
    LicenseType.TRIAL: 14,
    LicenseType.MONTHLY: 30,
    LicenseType.YEARLY: 365,
    LicenseType.LIFETIME: None,
}


def _compute_expires_at(license_type: LicenseType, issued_at: date, days: int | None) -> date | None:
    """Determine a key's expiry date from its type and issue date.

    Args:
        license_type: The plan being issued.
        issued_at: The issue date to compute the expiry relative to.
        days: Explicit override for the validity period in days;
            ``None`` uses :data:`_EXPIRY_DAYS_BY_TYPE`'s default for
            ``license_type`` (always ``None``/never-expires for
            :attr:`~licensing.enums.LicenseType.LIFETIME`, regardless
            of this argument).

    Returns:
        The computed expiry date, or ``None`` for a lifetime license.
    """
    if license_type is LicenseType.LIFETIME:
        return None
    resolved_days = days if days is not None else _EXPIRY_DAYS_BY_TYPE[license_type]
    return issued_at + timedelta(days=resolved_days)


def generate_keypair(*, private_key_out: Path, public_key_out: Path) -> None:
    """Generate a new Ed25519 signing keypair and write both halves to disk.

    Args:
        private_key_out: Where to write the PEM-encoded private key.
            Keep this file offline and out of version control.
        public_key_out: Where to write the PEM-encoded public key. This
            is the value that belongs in ``licensing/keys.py``'s
            ``PUBLIC_KEY_PEM``.
    """
    private_key = Ed25519PrivateKey.generate()
    public_key = private_key.public_key()

    private_key_out.parent.mkdir(parents=True, exist_ok=True)
    private_key_out.write_bytes(
        private_key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.PKCS8,
            encryption_algorithm=serialization.NoEncryption(),
        )
    )

    public_key_out.parent.mkdir(parents=True, exist_ok=True)
    public_key_out.write_bytes(
        public_key.public_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PublicFormat.SubjectPublicKeyInfo,
        )
    )


def issue_license_key(
    *,
    private_key_path: Path,
    customer_name: str,
    license_type: LicenseType,
    machine_id: str | None = None,
    issued_at: date | None = None,
    days: int | None = None,
    company_name: str | None = None,
) -> str:
    """Build and sign a license key.

    Args:
        private_key_path: Path to the vendor's PEM-encoded private key.
        customer_name: Who the license is issued to.
        license_type: Which plan to issue.
        machine_id: Lock the key to a specific machine's fingerprint;
            ``None`` leaves it unlocked (binds to whichever machine
            activates it first).
        issued_at: Override the issue date; defaults to today.
        days: Override the validity period in days; ignored for
            :attr:`~licensing.enums.LicenseType.LIFETIME`.
        company_name: The organization this license is issued to, for
            display in the License Information screen; omit if there
            is no distinct organization beyond ``customer_name``.

    Returns:
        The signed license key string, ready to hand to the customer.
    """
    private_key = load_pem_private_key(private_key_path.read_bytes(), password=None)
    if not isinstance(private_key, Ed25519PrivateKey):
        raise TypeError(f"{private_key_path} does not contain an Ed25519 private key.")

    resolved_issued_at = issued_at or date.today()
    payload = LicensePayload(
        license_id=str(uuid.uuid4()),
        customer_name=customer_name,
        company_name=company_name,
        license_type=license_type,
        machine_id=machine_id,
        issued_at=resolved_issued_at,
        expires_at=_compute_expires_at(license_type, resolved_issued_at, days),
    )
    return encode_license_key(payload, private_key)


def _build_arg_parser() -> argparse.ArgumentParser:
    """Build the CLI's argument parser."""
    parser = argparse.ArgumentParser(
        prog="python -m licensing.license_generator",
        description="Vendor-only tool for issuing signed attendance-system license keys.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    keypair_parser = subparsers.add_parser(
        "generate-keypair", help="Generate a new signing keypair (one-time setup)."
    )
    keypair_parser.add_argument("--private-key-out", type=Path, required=True)
    keypair_parser.add_argument("--public-key-out", type=Path, required=True)

    issue_parser = subparsers.add_parser("issue", help="Issue a signed license key.")
    issue_parser.add_argument("--private-key", type=Path, required=True)
    issue_parser.add_argument("--customer", required=True, dest="customer_name")
    issue_parser.add_argument("--company", default=None, dest="company_name")
    issue_parser.add_argument(
        "--type", required=True, dest="license_type", choices=[t.value for t in LicenseType]
    )
    issue_parser.add_argument("--machine-id", default=None)
    issue_parser.add_argument(
        "--issued-at", type=date.fromisoformat, default=None, help="YYYY-MM-DD, defaults to today."
    )
    issue_parser.add_argument(
        "--days", type=int, default=None, help="Override the default validity period."
    )
    issue_parser.add_argument(
        "--output", type=Path, default=None, help="Write the key to this file instead of stdout."
    )

    return parser


def main(argv: list[str] | None = None) -> int:
    """CLI entry point."""
    parser = _build_arg_parser()
    args = parser.parse_args(argv)

    if args.command == "generate-keypair":
        generate_keypair(private_key_out=args.private_key_out, public_key_out=args.public_key_out)
        print(f"Private key written to {args.private_key_out}")
        print(f"Public key written to {args.public_key_out}")
        print("Copy the public key's contents into licensing/keys.py's PUBLIC_KEY_PEM.")
        return 0

    key = issue_license_key(
        private_key_path=args.private_key,
        customer_name=args.customer_name,
        company_name=args.company_name,
        license_type=LicenseType(args.license_type),
        machine_id=args.machine_id,
        issued_at=args.issued_at,
        days=args.days,
    )
    if args.output:
        args.output.write_text(key + "\n", encoding="utf-8")
        print(f"License key written to {args.output}")
    else:
        print(key)
    return 0


if __name__ == "__main__":
    sys.exit(main())
