"""Tests for ``licensing.crypto.signing``: generic Ed25519 keypair/signing primitives.

The retired file-based license system (``licensing.enums``,
``licensing.license_key``, ``licensing.license_service``,
``licensing.validator``) has been removed in favor of server-managed
subscriptions (see :mod:`server.models.subscription`); this module
survives because it is genuinely shared infrastructure the Developer
Suite's software-update package signing still depends on (see
:mod:`developer_suite.services.update_manager_service`).
"""

from __future__ import annotations

import ast
import threading
from pathlib import Path

import pytest
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import load_pem_public_key

from licensing.crypto.signing import (
    InvalidPrivateKeyError,
    ensure_keypair,
    generate_keypair,
    load_private_key,
    save_private_key,
    save_public_key,
    sign_bytes,
)


class TestSigning:
    def test_generate_keypair_produces_a_matching_pair(self) -> None:
        private_key, public_key = generate_keypair()
        signature = private_key.sign(b"hello")
        public_key.verify(signature, b"hello")  # raises on mismatch

    def test_sign_bytes_matches_direct_sign(self) -> None:
        private_key, public_key = generate_keypair()
        signature = sign_bytes(private_key, b"payload")
        public_key.verify(signature, b"payload")  # no exception = success

    def test_sign_bytes_signature_does_not_verify_against_wrong_data(self) -> None:
        private_key, public_key = generate_keypair()
        signature = sign_bytes(private_key, b"payload")
        with pytest.raises(InvalidSignature):
            public_key.verify(signature, b"different payload")

    def test_save_and_load_private_key_round_trips_unencrypted(self, tmp_path) -> None:
        private_key, _ = generate_keypair()
        path = tmp_path / "private_key.pem"
        save_private_key(private_key, path)

        loaded = load_private_key(path)
        signature = sign_bytes(loaded, b"round-trip")
        private_key.public_key().verify(signature, b"round-trip")

    def test_save_and_load_private_key_round_trips_encrypted(self, tmp_path) -> None:
        private_key, _ = generate_keypair()
        path = tmp_path / "private_key.pem"
        save_private_key(private_key, path, password=b"correct horse battery staple")

        loaded = load_private_key(path, password=b"correct horse battery staple")
        signature = sign_bytes(loaded, b"round-trip")
        private_key.public_key().verify(signature, b"round-trip")

    def test_load_private_key_creates_parent_directories(self, tmp_path) -> None:
        private_key, _ = generate_keypair()
        path = tmp_path / "nested" / "dir" / "private_key.pem"
        save_private_key(private_key, path)
        assert path.exists()

    def test_save_public_key_creates_parent_directories(self, tmp_path) -> None:
        _, public_key = generate_keypair()
        path = tmp_path / "nested" / "dir" / "public_key.pem"
        save_public_key(public_key, path)
        assert path.exists()
        assert b"BEGIN PUBLIC KEY" in path.read_bytes()

    def test_load_private_key_rejects_a_public_key_file(self, tmp_path) -> None:
        _, public_key = generate_keypair()
        path = tmp_path / "public_key.pem"
        save_public_key(public_key, path)

        with pytest.raises(InvalidPrivateKeyError):
            load_private_key(path)


class TestEnsureKeypair:
    """``ensure_keypair`` — auto-bootstrap-if-missing for a signing key."""

    def test_generates_a_new_key_when_none_exists(self, tmp_path) -> None:
        private_path = tmp_path / "keys" / "private_key.pem"
        assert not private_path.exists()

        private_key = ensure_keypair(private_path)

        assert private_path.exists()
        signature = sign_bytes(private_key, b"payload")
        private_key.public_key().verify(signature, b"payload")

    def test_also_writes_the_public_key_when_a_path_is_given(self, tmp_path) -> None:
        private_path = tmp_path / "private_key.pem"
        public_path = tmp_path / "public_key.pem"

        private_key = ensure_keypair(private_path, public_key_path=public_path)

        assert public_path.exists()
        assert b"BEGIN PUBLIC KEY" in public_path.read_bytes()
        signature = sign_bytes(private_key, b"payload")
        load_pem_public_key(public_path.read_bytes()).verify(signature, b"payload")

    def test_public_key_path_is_optional(self, tmp_path) -> None:
        private_path = tmp_path / "private_key.pem"
        # Must not raise just because no public_key_path was given.
        ensure_keypair(private_path)
        assert private_path.exists()

    def test_loads_and_returns_an_existing_key_unchanged(self, tmp_path) -> None:
        private_path = tmp_path / "private_key.pem"
        original_key, _ = generate_keypair()
        save_private_key(original_key, private_path)
        original_bytes = private_path.read_bytes()
        original_mtime_ns = private_path.stat().st_mtime_ns

        returned_key = ensure_keypair(private_path)

        assert private_path.read_bytes() == original_bytes
        assert private_path.stat().st_mtime_ns == original_mtime_ns
        signature = sign_bytes(returned_key, b"same key")
        original_key.public_key().verify(signature, b"same key")

    def test_never_overwrites_an_existing_key_even_with_a_public_key_path_given(
        self, tmp_path
    ) -> None:
        private_path = tmp_path / "private_key.pem"
        public_path = tmp_path / "public_key.pem"
        original_key, _ = generate_keypair()
        save_private_key(original_key, private_path)
        original_bytes = private_path.read_bytes()

        ensure_keypair(private_path, public_key_path=public_path)

        assert private_path.read_bytes() == original_bytes
        # No public key file existed for this (pre-existing) private
        # key, and ensure_keypair must not invent one retroactively --
        # only a freshly *generated* key writes its public half.
        assert not public_path.exists()

    def test_does_not_regenerate_when_the_public_key_already_exists_too(self, tmp_path) -> None:
        private_path = tmp_path / "private_key.pem"
        public_path = tmp_path / "public_key.pem"
        original_private, original_public = generate_keypair()
        save_private_key(original_private, private_path)
        save_public_key(original_public, public_path)
        original_public_bytes = public_path.read_bytes()

        ensure_keypair(private_path, public_key_path=public_path)

        assert public_path.read_bytes() == original_public_bytes

    def test_raises_rather_than_replacing_a_corrupt_existing_key(self, tmp_path) -> None:
        private_path = tmp_path / "private_key.pem"
        _, public_key = generate_keypair()
        # A public key file at the private-key path is "present but
        # invalid" -- must surface as an error, never be silently
        # treated as "missing" and overwritten.
        save_public_key(public_key, private_path)
        corrupt_bytes = private_path.read_bytes()

        with pytest.raises(InvalidPrivateKeyError):
            ensure_keypair(private_path)

        assert private_path.read_bytes() == corrupt_bytes

    def test_concurrent_first_calls_converge_on_the_same_generated_key(self, tmp_path) -> None:
        """Two callers racing to bootstrap a missing key must never disagree.

        Real threads, real filesystem, no mocks.
        """
        private_path = tmp_path / "private_key.pem"
        results: list[Ed25519PrivateKey] = []
        barrier = threading.Barrier(8)

        def _worker() -> None:
            barrier.wait()
            results.append(ensure_keypair(private_path))

        threads = [threading.Thread(target=_worker) for _ in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert len(results) == 8
        signature = results[0].sign(b"race")
        for key in results:
            # Every caller must have ended up with a key that verifies
            # against the *same* signature -- i.e. every one returned
            # the one keypair that actually landed on disk, not each
            # its own independently generated one.
            key.public_key().verify(signature, b"race")


class TestObsoleteFileBasedLicensingRemoved:
    """The file-based license system's modules and hooks are fully gone.

    Confirms the migration to server-managed subscriptions (see
    :mod:`server.models.subscription`) actually removed the old
    system, rather than leaving it as dead, unreachable code alongside
    the new one.
    """

    def test_retired_licensing_modules_no_longer_exist(self) -> None:
        for module_name in (
            "licensing.enums",
            "licensing.license_key",
            "licensing.license_service",
            "licensing.license_store",
            "licensing.license_generator",
            "licensing.machine_id",
            "licensing.keys",
            "licensing.validator",
            "licensing.validator.developer_mode",
            "licensing.validator.version_check",
            "models.license",
            "repositories.license_repository",
            "ui.license_window",
            "ui.license_info_window",
        ):
            with pytest.raises(ModuleNotFoundError):
                __import__(module_name)

    def test_main_py_no_longer_imports_anything_from_licensing(self) -> None:
        main_py = Path(__file__).resolve().parent.parent / "main.py"
        source = main_py.read_text(encoding="utf-8")
        tree = ast.parse(source, filename=str(main_py))
        licensing_imports = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module and node.module.startswith("licensing"):
                licensing_imports.add(node.module)
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name.startswith("licensing"):
                        licensing_imports.add(alias.name)
        assert licensing_imports == set()

    def test_company_model_carries_no_licenses_relationship(self) -> None:
        from models.company import Company

        assert not hasattr(Company, "licenses")
        assert not hasattr(Company, "current_license")

    def test_legacy_licenses_table_is_not_in_the_attendance_client_schema(self) -> None:
        from models.base import Base

        assert "licenses" not in Base.metadata.tables
