"""Tests for Phase 1 of the commercial platform work (see
``docs/PLATFORM_ARCHITECTURE_GAP_ANALYSIS.md``): the new
``licensing.crypto`` and ``licensing.validator`` foundation modules,
plus the backward-compatible ``LicensePayload.licensed_version`` field.

None of this code is wired into the running application yet — these
tests exercise it standalone, exactly as it will be consumed once a
later phase integrates it.
"""

from __future__ import annotations

import ast
import subprocess
import sys
import threading
from datetime import date
from pathlib import Path

import pytest
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import load_pem_public_key

from config import Environment
from licensing.crypto.signing import (
    InvalidPrivateKeyError,
    ensure_keypair,
    generate_keypair,
    load_private_key,
    save_private_key,
    save_public_key,
    sign_bytes,
)
from licensing.enums import LicenseType
from licensing.license_key import LicensePayload
from licensing.validator.developer_mode import is_developer_mode_permitted, is_frozen
from licensing.validator.version_check import (
    InvalidVersionStringError,
    is_version_licensed,
    parse_version,
)


class TestLicensePayloadBackwardCompatibility:
    """``licensed_version`` must not disturb any existing behavior."""

    def _base_payload(self, **overrides: object) -> LicensePayload:
        defaults: dict[str, object] = dict(
            license_id="lic-1",
            customer_name="Acme",
            license_type=LicenseType.YEARLY,
            machine_id=None,
            issued_at=date(2026, 1, 1),
            expires_at=date(2027, 1, 1),
        )
        defaults.update(overrides)
        return LicensePayload(**defaults)  # type: ignore[arg-type]

    def test_default_is_none(self) -> None:
        payload = self._base_payload()
        assert payload.licensed_version is None

    def test_round_trips_through_json_dict(self) -> None:
        payload = self._base_payload(licensed_version="1.2.0")
        restored = LicensePayload.from_json_dict(payload.to_json_dict())
        assert restored.licensed_version == "1.2.0"

    def test_json_dict_missing_the_key_entirely_still_decodes(self) -> None:
        """Simulates a key issued before this field existed."""
        payload = self._base_payload()
        data = payload.to_json_dict()
        del data["licensed_version"]
        restored = LicensePayload.from_json_dict(data)
        assert restored.licensed_version is None


class TestSigning:
    """``licensing.crypto.signing`` — new, not consumed anywhere yet."""

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

        Real threads, real filesystem, no mocks -- the same "actually
        run it under contention" standard this project's other race
        tests (e.g. the first-admin-setup bootstrap) already use.
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


class TestOnlyTheDeveloperSuiteCanIssueLicenses:
    """The Attendance Client must never load, need, or be able to reach private-key code.

    Structural verification, not just convention: a customer's
    Attendance Client installation holds only ``licensing/keys.py``'s
    embedded *public* key (see :class:`licensing.license_service.LocalLicenseBackend`)
    and can therefore only ever *verify* a license someone else signed
    — it has no code path, direct or transitive, that could produce a
    valid signature. License issuance lives entirely in
    :mod:`developer_suite.services.license_service`, which the
    Attendance Client's build never even imports.
    """

    def test_main_py_only_imports_the_license_verification_module(self) -> None:
        """Static check: main.py's own source never references issuance/signing code."""
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
        assert licensing_imports == {"licensing.license_service"}

    def test_attendance_clients_license_verification_module_never_loads_private_key_code(self) -> None:
        """Dynamic check: importing licensing.license_service must never pull in signing code.

        Runs in a fresh subprocess so ``sys.modules`` reflects only
        what this one import chain actually touches -- not whatever
        this test file itself happened to import earlier (this same
        file's ``TestSigning``/``TestEnsureKeypair`` classes do import
        ``licensing.crypto.signing`` directly, which would otherwise
        contaminate an in-process check).
        """
        script = (
            "import sys\n"
            "import licensing.license_service\n"
            "forbidden = {\n"
            "    name for name in sys.modules\n"
            "    if name == 'licensing.crypto.signing'\n"
            "    or name == 'licensing.crypto'\n"
            "    or name == 'licensing.license_generator'\n"
            "}\n"
            "print(','.join(sorted(forbidden)))\n"
        )
        result = subprocess.run(
            [sys.executable, "-c", script],
            cwd=Path(__file__).resolve().parent.parent,
            capture_output=True,
            text=True,
            timeout=30,
        )
        assert result.returncode == 0, result.stderr
        loaded_forbidden_modules = [m for m in result.stdout.strip().split(",") if m]
        assert loaded_forbidden_modules == []

    def test_licensing_keys_module_contains_no_private_key_material(self) -> None:
        """The embedded constant the Attendance Client ships must be public-only."""
        from licensing.keys import PUBLIC_KEY_PEM

        assert b"PRIVATE KEY" not in PUBLIC_KEY_PEM
        assert b"PUBLIC KEY" in PUBLIC_KEY_PEM


class TestVersionCheck:
    """``licensing.validator.version_check`` — new, not consumed anywhere yet."""

    @pytest.mark.parametrize(
        ("version_string", "expected"),
        [
            ("1", (1,)),
            ("1.2", (1, 2)),
            ("1.2.0", (1, 2, 0)),
            ("1.2.0.1", (1, 2, 0, 1)),
            ("0.0.1", (0, 0, 1)),
        ],
    )
    def test_parse_version_valid(self, version_string: str, expected: tuple[int, ...]) -> None:
        assert parse_version(version_string) == expected

    @pytest.mark.parametrize(
        "version_string",
        ["", "abc", "1.2.x", "1..2", "-1.0", "1.2.0-beta", "v1.2.0"],
    )
    def test_parse_version_invalid(self, version_string: str) -> None:
        with pytest.raises(InvalidVersionStringError):
            parse_version(version_string)

    def test_no_cap_always_licensed(self) -> None:
        assert is_version_licensed("99.0.0", None) is True

    def test_running_version_below_cap_is_licensed(self) -> None:
        assert is_version_licensed("1.0.0", "1.2.0") is True

    def test_running_version_equal_to_cap_is_licensed(self) -> None:
        assert is_version_licensed("1.2.0", "1.2.0") is True

    def test_running_version_above_cap_is_not_licensed(self) -> None:
        assert is_version_licensed("1.3.0", "1.2.0") is False

    def test_different_length_versions_compare_correctly(self) -> None:
        # "1.2" and "1.2.0" name the same release.
        assert is_version_licensed("1.2", "1.2.0") is True
        assert is_version_licensed("1.2.0", "1.2") is True
        assert is_version_licensed("1.2.1", "1.2") is False

    def test_malformed_running_version_raises(self) -> None:
        with pytest.raises(InvalidVersionStringError):
            is_version_licensed("not-a-version", "1.0.0")

    def test_malformed_licensed_version_raises(self) -> None:
        with pytest.raises(InvalidVersionStringError):
            is_version_licensed("1.0.0", "not-a-version")


class TestDeveloperMode:
    """``licensing.validator.developer_mode`` — new, not consumed anywhere yet."""

    def test_is_frozen_false_under_pytest(self) -> None:
        # pytest never runs as a PyInstaller-frozen executable.
        assert is_frozen() is False

    def test_is_frozen_true_when_sys_frozen_set(self, monkeypatch) -> None:
        monkeypatch.setattr("sys.frozen", True, raising=False)
        assert is_frozen() is True

    def test_permitted_in_development_when_not_frozen(self, monkeypatch) -> None:
        monkeypatch.setattr("sys.frozen", False, raising=False)
        assert is_developer_mode_permitted(Environment.DEVELOPMENT) is True

    def test_never_permitted_in_production(self, monkeypatch) -> None:
        monkeypatch.setattr("sys.frozen", False, raising=False)
        assert is_developer_mode_permitted(Environment.PRODUCTION) is False

    def test_never_permitted_in_testing(self, monkeypatch) -> None:
        monkeypatch.setattr("sys.frozen", False, raising=False)
        assert is_developer_mode_permitted(Environment.TESTING) is False

    def test_never_permitted_when_frozen_even_in_development(self, monkeypatch) -> None:
        monkeypatch.setattr("sys.frozen", True, raising=False)
        assert is_developer_mode_permitted(Environment.DEVELOPMENT) is False

    def test_never_permitted_when_frozen_and_production(self, monkeypatch) -> None:
        monkeypatch.setattr("sys.frozen", True, raising=False)
        assert is_developer_mode_permitted(Environment.PRODUCTION) is False
