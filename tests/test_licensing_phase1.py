"""Tests for Phase 1 of the commercial platform work (see
``docs/PLATFORM_ARCHITECTURE_GAP_ANALYSIS.md``): the new
``licensing.crypto`` and ``licensing.validator`` foundation modules,
plus the backward-compatible ``LicensePayload.licensed_version`` field.

None of this code is wired into the running application yet — these
tests exercise it standalone, exactly as it will be consumed once a
later phase integrates it.
"""

from __future__ import annotations

from datetime import date

import pytest
from cryptography.exceptions import InvalidSignature

from config import Environment
from licensing.crypto.signing import (
    InvalidPrivateKeyError,
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
