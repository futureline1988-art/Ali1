"""End-to-end test for the full license lifecycle across both applications.

Written for the "No license signing key found" production incident: a
fresh Developer Suite installation had no signing key, and nothing
ever created one, so clicking "Issue License" always failed (see
:func:`licensing.crypto.signing.ensure_keypair` and
:meth:`developer_suite.services.license_service.LicenseService._load_private_key`
for the fix). This file proves the fix end to end, using the real,
unmodified verification path on the Attendance Client side
(:mod:`licensing.license_service`) against a signing key the Developer
Suite bootstraps *itself*, automatically -- exactly the sequence a
real vendor machine goes through on first use:

    1. Generate -- the signing key does not exist yet; the first call
       that needs it (issuing a license) creates it automatically, no
       manual step.
    2. Issue -- that same call signs a license for a customer.
    3. Activate -- the Attendance Client activates that key.
    4. Verify -- the Attendance Client reports it as a valid license.
    5. Renew -- the Developer Suite issues a fresh key for the same
       grant (reusing the already-bootstrapped signing key, never
       regenerating it), and the Attendance Client renews with it.

The Attendance Client side verifies against whatever public key the
Developer Suite's auto-bootstrap actually produced in step 1 -- read
back from disk and injected in place of ``licensing/keys.py``'s real,
committed constant (the same technique
``tests/test_developer_suite_phase4.py``'s own docstring documents and
uses for its own throwaway keypairs) -- rather than the test needing
to already know the key in advance. This proves the *mechanism* is
genuinely self-consistent end to end without ever needing the real
production private key inside the test suite, which must never be
committed to version control.
"""

from __future__ import annotations

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import developer_suite.config as developer_suite_config_module
import licensing.keys as licensing_keys_module
import licensing.license_service as client_license_service_module
from developer_suite.config import DeveloperSuiteConfig, get_developer_suite_config
from developer_suite.database.bootstrap import build_database as build_dev_suite_database
from developer_suite.services.customer_service import CustomerService
from developer_suite.services.license_service import LicenseService
from licensing.enums import LicenseType
from licensing.license_service import LicenseService as ClientLicenseService
from licensing.license_service import LicenseStatusCode
from licensing.license_service import LocalLicenseBackend
from licensing.license_store import LicenseStore

_TEST_MACHINE_ID = "END-TO-END-TEST-MACHINE"


@pytest.fixture
def dev_suite_config(tmp_path, monkeypatch) -> DeveloperSuiteConfig:
    monkeypatch.setenv("DEV_SUITE_DB_SQLITE_PATH", str(tmp_path / "developer_suite.db"))
    monkeypatch.setenv(
        "DEV_SUITE_LICENSE_PRIVATE_KEY_PATH", str(tmp_path / "keys" / "license_private_key.pem")
    )
    monkeypatch.setenv(
        "DEV_SUITE_LICENSE_PUBLIC_KEY_PATH", str(tmp_path / "keys" / "license_public_key.pem")
    )
    developer_suite_config_module._config_instance = None
    yield get_developer_suite_config()
    developer_suite_config_module._config_instance = None


@pytest.fixture(autouse=True)
def fixed_machine_id(monkeypatch):
    """Pin the Attendance Client's machine fingerprint so a machine-locked key activates.

    ``licensing.license_service``'s machine-lock check calls
    :func:`licensing.machine_id.get_machine_id` directly (independent
    of :class:`~licensing.license_store.LicenseStore`'s own
    ``machine_id`` override), so it needs its own patch to agree with
    the fixed ``_TEST_MACHINE_ID`` this file issues every license
    against.
    """
    monkeypatch.setattr(client_license_service_module, "get_machine_id", lambda: _TEST_MACHINE_ID)


@pytest.fixture
def dev_suite_license_service(dev_suite_config: DeveloperSuiteConfig) -> LicenseService:
    database = build_dev_suite_database(dev_suite_config)
    return LicenseService(
        database,
        private_key_path=dev_suite_config.licensing_private_key_path,
        public_key_path=dev_suite_config.licensing_public_key_path,
    )


def _new_customer(dev_suite_license_service: LicenseService, *, company_name: str, contact_name: str) -> int:
    customer_service = CustomerService(dev_suite_license_service.database)
    customer = customer_service.create_customer(company_name=company_name, contact_name=contact_name)
    return customer.id


def _client_service_trusting_whatever_was_just_bootstrapped(
    monkeypatch, dev_suite_config: DeveloperSuiteConfig, tmp_path, *, store_name: str = "license.dat"
) -> ClientLicenseService:
    """Build a real Attendance Client LicenseService that trusts the freshly bootstrapped key.

    Reads back the public key the Developer Suite's auto-bootstrap
    just wrote to disk and injects it in place of
    ``licensing.keys.PUBLIC_KEY_PEM`` -- must be called *after* the
    Developer Suite operation that triggers bootstrap (e.g.
    ``issue_license``), never before, or there is nothing on disk yet
    to read back.
    """
    public_key_pem = dev_suite_config.licensing_public_key_path.read_bytes()
    monkeypatch.setattr(licensing_keys_module, "PUBLIC_KEY_PEM", public_key_pem)
    store = LicenseStore(store_path=tmp_path / store_name, machine_id=_TEST_MACHINE_ID)
    return ClientLicenseService(backend=LocalLicenseBackend(), store=store)


class TestFullLicenseLifecycleAcrossBothApplications:
    """Generate -> issue -> activate -> verify -> renew, no mocks, real crypto."""

    def test_full_lifecycle(
        self,
        monkeypatch,
        tmp_path,
        dev_suite_config: DeveloperSuiteConfig,
        dev_suite_license_service: LicenseService,
    ) -> None:
        customer_id = _new_customer(
            dev_suite_license_service, company_name="Acme Co", contact_name="Jane Doe"
        )

        # ---- 1. Generate: the key does not exist until issuance needs it. ----
        assert not dev_suite_config.licensing_private_key_path.exists()

        # ---- 2. Issue: the Developer Suite signs a Monthly license,
        # bootstrapping its signing key as a side effect (no separate
        # "generate keypair" step). Monthly, not Trial -- renewal
        # below requires a Monthly/Yearly license already on record.
        issued = dev_suite_license_service.issue_license(
            customer_id=customer_id,
            license_type=LicenseType.MONTHLY,
            machine_id=_TEST_MACHINE_ID,
        )
        assert dev_suite_config.licensing_private_key_path.exists()
        assert dev_suite_config.licensing_public_key_path.exists()
        assert issued.license_key.startswith("AMS1.")

        client_service = _client_service_trusting_whatever_was_just_bootstrapped(
            monkeypatch, dev_suite_config, tmp_path
        )

        # ---- 3. Activate: the Attendance Client activates that key. ----
        status = client_service.activate(issued.license_key)
        assert status.code is LicenseStatusCode.VALID
        assert status.license_type is LicenseType.MONTHLY

        # ---- 4. Verify: re-checking reports the same valid status. ----
        rechecked = client_service.get_status()
        assert rechecked.code is LicenseStatusCode.VALID
        assert rechecked.days_remaining is not None and rechecked.days_remaining > 0

        details = client_service.get_details()
        assert details.company_name == "Acme Co"
        assert details.customer_name == "Jane Doe"

        # ---- 5. Renew: the Developer Suite issues a fresh key for the
        # same grant, reusing the *same*, already-bootstrapped signing
        # key (proving it was never regenerated/overwritten), and the
        # Attendance Client renews with it. ----
        key_bytes_before_renewal = dev_suite_config.licensing_private_key_path.read_bytes()
        renewed_grant = dev_suite_license_service.renew_license(issued.id, days=60)
        assert dev_suite_config.licensing_private_key_path.read_bytes() == key_bytes_before_renewal

        renewed_status = client_service.renew(renewed_grant.license_key)
        assert renewed_status.code is LicenseStatusCode.VALID
        assert renewed_status.days_remaining == 60

    def test_second_customer_reuses_the_same_bootstrapped_key(
        self,
        monkeypatch,
        tmp_path,
        dev_suite_config: DeveloperSuiteConfig,
        dev_suite_license_service: LicenseService,
    ) -> None:
        """Two independent issuances must be verifiable with one and the same public key."""
        first_customer_id = _new_customer(
            dev_suite_license_service, company_name="Acme Co", contact_name="Jane Doe"
        )
        first = dev_suite_license_service.issue_license(
            customer_id=first_customer_id, license_type=LicenseType.TRIAL
        )

        second_customer_id = _new_customer(
            dev_suite_license_service, company_name="Beta LLC", contact_name="John Roe"
        )
        key_bytes_before_second_issuance = dev_suite_config.licensing_private_key_path.read_bytes()
        second = dev_suite_license_service.issue_license(
            customer_id=second_customer_id, license_type=LicenseType.TRIAL
        )
        # The second issuance must not have touched the key generated
        # for the first.
        assert dev_suite_config.licensing_private_key_path.read_bytes() == key_bytes_before_second_issuance

        for index, issued in enumerate((first, second)):
            client_service = _client_service_trusting_whatever_was_just_bootstrapped(
                monkeypatch, dev_suite_config, tmp_path, store_name=f"license-{index}.dat"
            )
            status = client_service.activate(issued.license_key)
            assert status.code is LicenseStatusCode.VALID
