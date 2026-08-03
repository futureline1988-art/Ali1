"""Tests for Phase 7: synchronization protocol foundation on the Attendance Server.

Every test here exercises only :mod:`server`; nothing touches the
Attendance Client's or the Developer Suite's own database, config, or
models. No business domain (customers, licenses, configuration, ...)
is wired into the sync mechanism here — every change pushed/pulled in
these tests uses a made-up ``entity_type`` purely to exercise the
generic protocol.
"""

from __future__ import annotations

import hashlib
import json
import os
import uuid

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import server.config as server_config_module
from fastapi.testclient import TestClient
from server.api.app import create_app
from server.auth.tokens import issue_token
from server.config import ServerConfig, get_server_config
from server.database.bootstrap import build_database
from server.models.device import SyncDevice, DeviceType
from server.models.sync import ChangeStatus, SyncOperation
from server.services.device_service import DeviceNotFoundError, DeviceService
from server.services.sync_service import (
    ChangeInput,
    ChangeNotInConflictError,
    ChangeRecordNotFoundError,
    SyncService,
)


def _checksum(payload: dict) -> str:
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


@pytest.fixture
def server_config(tmp_path, monkeypatch) -> ServerConfig:
    monkeypatch.setenv("ATTENDANCE_SERVER_DB_SQLITE_PATH", str(tmp_path / "attendance_server_test.db"))
    monkeypatch.setenv("ATTENDANCE_SERVER_SECRET_KEY", "test-secret-key")
    server_config_module._config_instance = None
    yield get_server_config()
    server_config_module._config_instance = None


@pytest.fixture
def server_database(server_config: ServerConfig):
    database = build_database(server_config)
    yield database
    database.dispose()


@pytest.fixture
def device_service(server_database, server_config: ServerConfig) -> DeviceService:
    return DeviceService(server_database, config=server_config)


@pytest.fixture
def sync_service(server_database) -> SyncService:
    return SyncService(server_database)


@pytest.fixture
def device(device_service: DeviceService) -> tuple[SyncDevice, str]:
    return device_service.register_device(name="Acme Co Installation", device_type=DeviceType.ATTENDANCE_CLIENT)


@pytest.fixture
def client(server_config: ServerConfig, server_database) -> TestClient:
    app = create_app(server_config, server_database)
    return TestClient(app, raise_server_exceptions=False)


@pytest.fixture
def admin_headers(server_config: ServerConfig) -> dict[str, str]:
    token = issue_token(
        {"principal_id": "admin-1", "principal_type": "developer_suite", "scopes": ["sync:admin"]},
        config=server_config,
    )
    return {"Authorization": f"Bearer {token}"}


class TestDeviceService:
    def test_register_returns_active_device_and_plaintext_key(
        self, device_service: DeviceService
    ) -> None:
        registered, api_key = device_service.register_device(
            name="Test SyncDevice", device_type=DeviceType.DEVELOPER_SUITE
        )
        assert registered.is_active is True
        assert registered.device_type is DeviceType.DEVELOPER_SUITE
        assert len(api_key) > 20
        # The stored hash is never the plaintext key itself.
        assert registered.api_key_hash != api_key

    def test_authenticate_accepts_correct_credentials(
        self, device_service: DeviceService, device: tuple[SyncDevice, str]
    ) -> None:
        registered, api_key = device
        authenticated = device_service.authenticate_device(registered.public_id, api_key)
        assert authenticated is not None
        assert authenticated.id == registered.id
        assert authenticated.last_seen_at is not None

    def test_authenticate_rejects_wrong_key(
        self, device_service: DeviceService, device: tuple[SyncDevice, str]
    ) -> None:
        registered, _api_key = device
        assert device_service.authenticate_device(registered.public_id, "wrong-key") is None

    def test_authenticate_rejects_unknown_device(self, device_service: DeviceService) -> None:
        assert device_service.authenticate_device(uuid.uuid4(), "any-key") is None

    def test_deactivated_device_fails_authentication(
        self, device_service: DeviceService, device: tuple[SyncDevice, str]
    ) -> None:
        registered, api_key = device
        device_service.deactivate_device(registered.id)
        assert device_service.authenticate_device(registered.public_id, api_key) is None

    def test_deactivate_raises_for_unknown_id(self, device_service: DeviceService) -> None:
        with pytest.raises(DeviceNotFoundError):
            device_service.deactivate_device(999999)

    def test_list_devices_returns_registered_devices(self, device_service: DeviceService) -> None:
        device_service.register_device(name="A", device_type=DeviceType.ATTENDANCE_CLIENT)
        device_service.register_device(name="B", device_type=DeviceType.DEVELOPER_SUITE)
        assert len(device_service.list_devices()) == 2


class TestSyncServiceChecksumAndConflicts:
    def test_compute_checksum_is_deterministic(self) -> None:
        payload = {"b": 2, "a": 1}
        assert SyncService.compute_checksum(payload) == SyncService.compute_checksum({"a": 1, "b": 2})

    def test_push_applies_first_change_for_a_new_entity(
        self, sync_service: SyncService, device: tuple[SyncDevice, str]
    ) -> None:
        registered, _ = device
        payload = {"name": "Acme"}
        change = ChangeInput(
            entity_type="widget",
            entity_id="w-1",
            operation=SyncOperation.CREATE,
            payload=payload,
            checksum=_checksum(payload),
            base_version=0,
        )
        results = sync_service.push_changes(registered.id, [change])
        assert results[0].status is ChangeStatus.APPLIED
        assert results[0].new_version == 1

    def test_push_rejects_bad_checksum(
        self, sync_service: SyncService, device: tuple[SyncDevice, str]
    ) -> None:
        registered, _ = device
        change = ChangeInput(
            entity_type="widget",
            entity_id="w-1",
            operation=SyncOperation.CREATE,
            payload={"name": "Acme"},
            checksum="0" * 64,
            base_version=0,
        )
        results = sync_service.push_changes(registered.id, [change])
        assert results[0].status is ChangeStatus.REJECTED
        assert "integrity" in results[0].conflict_reason.lower()

    def test_push_conflicts_on_stale_base_version(
        self, sync_service: SyncService, device: tuple[SyncDevice, str]
    ) -> None:
        registered, _ = device
        payload = {"name": "Acme"}
        first = ChangeInput(
            entity_type="widget",
            entity_id="w-1",
            operation=SyncOperation.CREATE,
            payload=payload,
            checksum=_checksum(payload),
            base_version=0,
        )
        sync_service.push_changes(registered.id, [first])

        stale = ChangeInput(
            entity_type="widget",
            entity_id="w-1",
            operation=SyncOperation.UPDATE,
            payload=payload,
            checksum=_checksum(payload),
            base_version=0,
        )
        results = sync_service.push_changes(registered.id, [stale])
        assert results[0].status is ChangeStatus.CONFLICT
        assert "version" in results[0].conflict_reason.lower()

    def test_push_applies_correctly_versioned_update(
        self, sync_service: SyncService, device: tuple[SyncDevice, str]
    ) -> None:
        registered, _ = device
        payload = {"name": "Acme"}
        sync_service.push_changes(
            registered.id,
            [
                ChangeInput(
                    entity_type="widget",
                    entity_id="w-1",
                    operation=SyncOperation.CREATE,
                    payload=payload,
                    checksum=_checksum(payload),
                    base_version=0,
                )
            ],
        )
        updated_payload = {"name": "Acme Corp"}
        results = sync_service.push_changes(
            registered.id,
            [
                ChangeInput(
                    entity_type="widget",
                    entity_id="w-1",
                    operation=SyncOperation.UPDATE,
                    payload=updated_payload,
                    checksum=_checksum(updated_payload),
                    base_version=1,
                )
            ],
        )
        assert results[0].status is ChangeStatus.APPLIED
        assert results[0].new_version == 2

    def test_push_processes_batch_in_order(
        self, sync_service: SyncService, device: tuple[SyncDevice, str]
    ) -> None:
        registered, _ = device
        payload = {"n": 1}
        changes = [
            ChangeInput(
                entity_type="widget",
                entity_id="w-1",
                operation=SyncOperation.CREATE,
                payload=payload,
                checksum=_checksum(payload),
                base_version=0,
            ),
            ChangeInput(
                entity_type="widget",
                entity_id="w-1",
                operation=SyncOperation.UPDATE,
                payload=payload,
                checksum=_checksum(payload),
                base_version=1,
            ),
        ]
        results = sync_service.push_changes(registered.id, changes)
        assert [r.status for r in results] == [ChangeStatus.APPLIED, ChangeStatus.APPLIED]
        assert [r.new_version for r in results] == [1, 2]


class TestSyncServicePull:
    def test_pull_returns_only_applied_changes_after_cursor(
        self, sync_service: SyncService, device: tuple[SyncDevice, str]
    ) -> None:
        registered, _ = device
        payload = {"n": 1}
        sync_service.push_changes(
            registered.id,
            [
                ChangeInput(
                    entity_type="widget",
                    entity_id="w-1",
                    operation=SyncOperation.CREATE,
                    payload=payload,
                    checksum=_checksum(payload),
                    base_version=0,
                )
            ],
        )
        changes, next_cursor = sync_service.pull_changes(0)
        assert len(changes) == 1
        assert changes[0].entity_id == "w-1"
        assert next_cursor == changes[0].id

    def test_pull_excludes_conflicted_and_rejected_changes(
        self, sync_service: SyncService, device: tuple[SyncDevice, str]
    ) -> None:
        registered, _ = device
        payload = {"n": 1}
        sync_service.push_changes(
            registered.id,
            [
                ChangeInput(
                    entity_type="widget",
                    entity_id="w-1",
                    operation=SyncOperation.CREATE,
                    payload=payload,
                    checksum=_checksum(payload),
                    base_version=0,
                )
            ],
        )
        # A stale re-push of the same base_version -> CONFLICT, must not appear in pull.
        sync_service.push_changes(
            registered.id,
            [
                ChangeInput(
                    entity_type="widget",
                    entity_id="w-1",
                    operation=SyncOperation.UPDATE,
                    payload=payload,
                    checksum=_checksum(payload),
                    base_version=0,
                )
            ],
        )
        changes, _ = sync_service.pull_changes(0)
        assert len(changes) == 1

    def test_pull_respects_since_cursor(
        self, sync_service: SyncService, device: tuple[SyncDevice, str]
    ) -> None:
        registered, _ = device
        payload = {"n": 1}
        sync_service.push_changes(
            registered.id,
            [
                ChangeInput(
                    entity_type="widget",
                    entity_id="w-1",
                    operation=SyncOperation.CREATE,
                    payload=payload,
                    checksum=_checksum(payload),
                    base_version=0,
                )
            ],
        )
        first_changes, cursor = sync_service.pull_changes(0)
        changes, next_cursor = sync_service.pull_changes(cursor)
        assert changes == []
        assert next_cursor == cursor

    def test_pull_filters_by_entity_type(
        self, sync_service: SyncService, device: tuple[SyncDevice, str]
    ) -> None:
        registered, _ = device
        payload = {"n": 1}
        sync_service.push_changes(
            registered.id,
            [
                ChangeInput(
                    entity_type="widget",
                    entity_id="w-1",
                    operation=SyncOperation.CREATE,
                    payload=payload,
                    checksum=_checksum(payload),
                    base_version=0,
                ),
                ChangeInput(
                    entity_type="gadget",
                    entity_id="g-1",
                    operation=SyncOperation.CREATE,
                    payload=payload,
                    checksum=_checksum(payload),
                    base_version=0,
                ),
            ],
        )
        changes, _ = sync_service.pull_changes(0, entity_type="gadget")
        assert len(changes) == 1
        assert changes[0].entity_type == "gadget"

    def test_pull_change_device_is_eager_loaded_after_session_close(
        self, sync_service: SyncService, device: tuple[SyncDevice, str]
    ) -> None:
        registered, _ = device
        payload = {"n": 1}
        sync_service.push_changes(
            registered.id,
            [
                ChangeInput(
                    entity_type="widget",
                    entity_id="w-1",
                    operation=SyncOperation.CREATE,
                    payload=payload,
                    checksum=_checksum(payload),
                    base_version=0,
                )
            ],
        )
        changes, _ = sync_service.pull_changes(0)
        # Accessed after the session that fetched it is closed - proves eager loading.
        assert changes[0].device.name == "Acme Co Installation"


class TestSyncServiceConflictResolution:
    def _push_conflict(self, sync_service: SyncService, registered: SyncDevice) -> int:
        payload = {"n": 1}
        sync_service.push_changes(
            registered.id,
            [
                ChangeInput(
                    entity_type="widget",
                    entity_id="w-1",
                    operation=SyncOperation.CREATE,
                    payload=payload,
                    checksum=_checksum(payload),
                    base_version=0,
                )
            ],
        )
        results = sync_service.push_changes(
            registered.id,
            [
                ChangeInput(
                    entity_type="widget",
                    entity_id="w-1",
                    operation=SyncOperation.UPDATE,
                    payload=payload,
                    checksum=_checksum(payload),
                    base_version=0,
                )
            ],
        )
        return results[0].change_record_id

    def test_list_conflicts_returns_unresolved_conflicts(
        self, sync_service: SyncService, device: tuple[SyncDevice, str]
    ) -> None:
        registered, _ = device
        self._push_conflict(sync_service, registered)
        conflicts = sync_service.list_conflicts()
        assert len(conflicts) == 1
        assert conflicts[0].status is ChangeStatus.CONFLICT

    def test_resolve_conflict_force_applies_and_bumps_version(
        self, sync_service: SyncService, device: tuple[SyncDevice, str]
    ) -> None:
        registered, _ = device
        change_id = self._push_conflict(sync_service, registered)
        resolved = sync_service.resolve_conflict(change_id, apply_incoming=True)
        assert resolved.status is ChangeStatus.APPLIED
        assert resolved.new_version == 2
        assert sync_service.list_conflicts() == []

    def test_resolve_conflict_can_discard(
        self, sync_service: SyncService, device: tuple[SyncDevice, str]
    ) -> None:
        registered, _ = device
        change_id = self._push_conflict(sync_service, registered)
        resolved = sync_service.resolve_conflict(change_id, apply_incoming=False)
        assert resolved.status is ChangeStatus.REJECTED
        assert sync_service.list_conflicts() == []

    def test_resolve_conflict_raises_for_unknown_id(self, sync_service: SyncService) -> None:
        with pytest.raises(ChangeRecordNotFoundError):
            sync_service.resolve_conflict(999999, apply_incoming=True)

    def test_resolve_conflict_raises_when_not_in_conflict(
        self, sync_service: SyncService, device: tuple[SyncDevice, str]
    ) -> None:
        registered, _ = device
        payload = {"n": 1}
        results = sync_service.push_changes(
            registered.id,
            [
                ChangeInput(
                    entity_type="widget",
                    entity_id="w-1",
                    operation=SyncOperation.CREATE,
                    payload=payload,
                    checksum=_checksum(payload),
                    base_version=0,
                )
            ],
        )
        with pytest.raises(ChangeNotInConflictError):
            sync_service.resolve_conflict(results[0].change_record_id, apply_incoming=True)


class TestDeviceRegistrationEndpoint:
    def test_requires_admin_scope(self, client: TestClient) -> None:
        response = client.post(
            "/api/v1/devices/register",
            json={"name": "SyncDevice", "device_type": "attendance_client"},
        )
        assert response.status_code == 401

    def test_rejects_insufficient_scope(self, client: TestClient, server_config: ServerConfig) -> None:
        token = issue_token(
            {"principal_id": "x", "principal_type": "developer_suite", "scopes": ["read"]},
            config=server_config,
        )
        response = client.post(
            "/api/v1/devices/register",
            json={"name": "SyncDevice", "device_type": "attendance_client"},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert response.status_code == 403

    def test_registers_a_device_and_returns_api_key_once(
        self, client: TestClient, admin_headers: dict[str, str]
    ) -> None:
        response = client.post(
            "/api/v1/devices/register",
            json={"name": "Acme Installation", "device_type": "attendance_client"},
            headers=admin_headers,
        )
        assert response.status_code == 201
        body = response.json()
        assert body["device"]["name"] == "Acme Installation"
        assert "api_key" in body
        assert "api_key_hash" not in body["device"]


class TestSyncPushPullEndpoints:
    def _device_headers(self, client: TestClient, admin_headers: dict[str, str]) -> dict[str, str]:
        response = client.post(
            "/api/v1/devices/register",
            json={"name": "Acme Installation", "device_type": "attendance_client"},
            headers=admin_headers,
        )
        body = response.json()
        return {
            "X-Device-Id": body["device"]["public_id"],
            "X-Device-Api-Key": body["api_key"],
        }

    def test_push_requires_device_credentials(self, client: TestClient) -> None:
        response = client.post("/api/v1/sync/push", json={"changes": []})
        assert response.status_code == 401

    def test_push_rejects_wrong_device_credentials(self, client: TestClient) -> None:
        payload = {"n": 1}
        response = client.post(
            "/api/v1/sync/push",
            json={
                "changes": [
                    {
                        "entity_type": "widget",
                        "entity_id": "w-1",
                        "operation": "create",
                        "payload": payload,
                        "checksum": _checksum(payload),
                        "base_version": 0,
                    }
                ]
            },
            headers={"X-Device-Id": str(uuid.uuid4()), "X-Device-Api-Key": "nope"},
        )
        assert response.status_code == 401

    def test_push_then_pull_round_trip(self, client: TestClient, admin_headers: dict[str, str]) -> None:
        headers = self._device_headers(client, admin_headers)
        payload = {"company_name": "Acme"}
        push_response = client.post(
            "/api/v1/sync/push",
            json={
                "changes": [
                    {
                        "entity_type": "customer",
                        "entity_id": "cust-1",
                        "operation": "create",
                        "payload": payload,
                        "checksum": _checksum(payload),
                        "base_version": 0,
                    }
                ]
            },
            headers=headers,
        )
        assert push_response.status_code == 200
        assert push_response.json()["results"][0]["status"] == "applied"

        pull_response = client.get("/api/v1/sync/pull", params={"since": 0}, headers=headers)
        assert pull_response.status_code == 200
        body = pull_response.json()
        assert len(body["changes"]) == 1
        assert body["changes"][0]["entity_id"] == "cust-1"
        assert body["next_cursor"] == body["changes"][0]["id"]

    def test_pull_limit_is_clamped_to_maximum(
        self, client: TestClient, admin_headers: dict[str, str]
    ) -> None:
        headers = self._device_headers(client, admin_headers)
        response = client.get(
            "/api/v1/sync/pull", params={"since": 0, "limit": 999999}, headers=headers
        )
        assert response.status_code == 200


class TestSyncConflictsEndpoints:
    def _device_headers(self, client: TestClient, admin_headers: dict[str, str]) -> dict[str, str]:
        response = client.post(
            "/api/v1/devices/register",
            json={"name": "Acme Installation", "device_type": "attendance_client"},
            headers=admin_headers,
        )
        body = response.json()
        return {
            "X-Device-Id": body["device"]["public_id"],
            "X-Device-Api-Key": body["api_key"],
        }

    def test_list_conflicts_requires_admin_scope(self, client: TestClient) -> None:
        assert client.get("/api/v1/sync/conflicts").status_code == 401

    def test_full_conflict_lifecycle_through_the_api(
        self, client: TestClient, admin_headers: dict[str, str]
    ) -> None:
        headers = self._device_headers(client, admin_headers)
        payload = {"n": 1}
        client.post(
            "/api/v1/sync/push",
            json={
                "changes": [
                    {
                        "entity_type": "widget",
                        "entity_id": "w-1",
                        "operation": "create",
                        "payload": payload,
                        "checksum": _checksum(payload),
                        "base_version": 0,
                    }
                ]
            },
            headers=headers,
        )
        conflict_push = client.post(
            "/api/v1/sync/push",
            json={
                "changes": [
                    {
                        "entity_type": "widget",
                        "entity_id": "w-1",
                        "operation": "update",
                        "payload": payload,
                        "checksum": _checksum(payload),
                        "base_version": 0,
                    }
                ]
            },
            headers=headers,
        )
        assert conflict_push.json()["results"][0]["status"] == "conflict"

        conflicts_response = client.get("/api/v1/sync/conflicts", headers=admin_headers)
        assert len(conflicts_response.json()["conflicts"]) == 1
        change_id = conflicts_response.json()["conflicts"][0]["id"]

        resolve_response = client.post(
            f"/api/v1/sync/conflicts/{change_id}/resolve",
            json={"apply_incoming": True},
            headers=admin_headers,
        )
        assert resolve_response.status_code == 200
        assert resolve_response.json()["status"] == "applied"

        assert client.get("/api/v1/sync/conflicts", headers=admin_headers).json()["conflicts"] == []

    def test_resolve_unknown_change_returns_404(
        self, client: TestClient, admin_headers: dict[str, str]
    ) -> None:
        response = client.post(
            "/api/v1/sync/conflicts/999999/resolve",
            json={"apply_incoming": True},
            headers=admin_headers,
        )
        assert response.status_code == 404

    def test_resolve_non_conflicting_change_returns_409(
        self, client: TestClient, admin_headers: dict[str, str]
    ) -> None:
        headers = self._device_headers(client, admin_headers)
        payload = {"n": 1}
        push_response = client.post(
            "/api/v1/sync/push",
            json={
                "changes": [
                    {
                        "entity_type": "widget",
                        "entity_id": "w-1",
                        "operation": "create",
                        "payload": payload,
                        "checksum": _checksum(payload),
                        "base_version": 0,
                    }
                ]
            },
            headers=headers,
        )
        change_id = push_response.json()["results"][0]["change_record_id"]
        response = client.post(
            f"/api/v1/sync/conflicts/{change_id}/resolve",
            json={"apply_incoming": True},
            headers=admin_headers,
        )
        assert response.status_code == 409


class TestZeroImpactOnOtherApplications:
    def test_sync_tables_live_only_in_the_attendance_server_schema(self) -> None:
        from developer_suite.database.base import Base as DeveloperSuiteBase
        from models.base import Base as AttendanceBase
        from server.database.base import Base as ServerBase

        for table_name in ("sync_devices", "change_records", "entity_versions"):
            assert table_name in ServerBase.metadata.tables
            assert table_name not in AttendanceBase.metadata.tables
            assert table_name not in DeveloperSuiteBase.metadata.tables
