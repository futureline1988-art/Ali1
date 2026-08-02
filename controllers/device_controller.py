"""Device controller: bridges the devices screen to ``DeviceService``."""

from __future__ import annotations

from typing import Any

from PySide6.QtCore import Signal
from sqlalchemy.orm import Session

from controllers.base_controller import BaseController, requires_permission
from devices.device_manager import DeviceManager
from models.device import Device
from models.enums import DeviceProtocol
from repositories.device_repository import DeviceRepository
from repositories.employee_repository import EmployeeRepository
from services.device_service import DeviceService


def _device_to_dict(device: Device) -> dict[str, Any]:
    """Serialize a device plus its display labels, while the session is open."""
    data = device.to_dict(exclude={"communication_key"})
    data["protocol_label_ar"] = device.protocol_label_ar
    data["protocol_label_en"] = device.protocol_label_en
    data["status_label_ar"] = device.status_label_ar
    data["status_label_en"] = device.status_label_en
    return data


class DeviceController(BaseController):
    """Controller for the biometric devices management screen."""

    devices_changed = Signal()
    """Emitted after any successful create/update/delete/sync."""

    def __init__(
        self,
        *,
        company_id: int,
        actor_user_id: int | None = None,
        permission_codes: frozenset[str] = frozenset(),
        device_manager: DeviceManager | None = None,
    ) -> None:
        """Create a device controller.

        Args:
            company_id: The company to scope every operation to.
            actor_user_id: The current user, for audit attribution.
            permission_codes: The current user's granted permission
                codes.
            device_manager: Custom device manager, primarily for
                injecting a fake connector in tests.
        """
        super().__init__(
            company_id=company_id,
            actor_user_id=actor_user_id,
            permission_codes=permission_codes,
        )
        self._device_manager = device_manager

    @requires_permission("devices.manage")
    def create_device(
        self,
        *,
        name: str,
        protocol: DeviceProtocol,
        host: str,
        port: int,
        branch_id: int | None = None,
        communication_key: str | None = None,
        timeout_seconds: int | None = None,
        notes: str | None = None,
    ) -> dict[str, Any] | None:
        """Register a new device.

        Args mirror :meth:`~services.device_service.DeviceService.create_device`.

        Returns:
            The new device's data as a dict, or ``None`` on failure.
        """

        def do_create(session: Session) -> dict[str, Any]:
            service = DeviceService(
                session, company_id=self.company_id, actor_user_id=self.actor_user_id
            )
            device = service.create_device(
                name=name,
                protocol=protocol,
                host=host,
                port=port,
                branch_id=branch_id,
                communication_key=communication_key,
                timeout_seconds=timeout_seconds,
                notes=notes,
            )
            return _device_to_dict(device)

        result = self._run(do_create)
        if result is not None:
            self.devices_changed.emit()
        return result

    @requires_permission("devices.manage")
    def update_device(self, device_id: int, **fields: Any) -> dict[str, Any] | None:
        """Update an existing device.

        Args:
            device_id: The device to update.
            **fields: See
                :meth:`~services.device_service.DeviceService.update_device`.

        Returns:
            The updated device's data as a dict, or ``None`` on
            failure.
        """

        def do_update(session: Session) -> dict[str, Any]:
            repo = DeviceRepository(session, company_id=self.company_id)
            device = repo.get_by_id(device_id)
            if device is None:
                raise ValueError(f"Device {device_id!r} was not found.")
            service = DeviceService(
                session, company_id=self.company_id, actor_user_id=self.actor_user_id
            )
            updated = service.update_device(device, **fields)
            return _device_to_dict(updated)

        result = self._run(do_update)
        if result is not None:
            self.devices_changed.emit()
        return result

    @requires_permission("devices.manage", default=False)
    def delete_device(self, device_id: int) -> bool:
        """Soft-delete a device.

        Args:
            device_id: The device to delete.

        Returns:
            ``True`` on success, ``False`` on failure.
        """

        def do_delete(session: Session) -> bool:
            repo = DeviceRepository(session, company_id=self.company_id)
            device = repo.get_by_id(device_id)
            if device is None:
                raise ValueError(f"Device {device_id!r} was not found.")
            service = DeviceService(
                session, company_id=self.company_id, actor_user_id=self.actor_user_id
            )
            service.delete_device(device)
            return True

        result = self._run(do_delete)
        if result:
            self.devices_changed.emit()
        return bool(result)

    @requires_permission("devices.view", "devices.manage", default=False)
    def test_connection(self, device_id: int) -> bool:
        """Test connectivity to a device and update its recorded status.

        Args:
            device_id: The device to test.

        Returns:
            ``True`` if the connection succeeded, ``False`` otherwise
            or on failure.
        """

        def do_test(session: Session) -> bool:
            repo = DeviceRepository(session, company_id=self.company_id)
            device = repo.get_by_id(device_id)
            if device is None:
                raise ValueError(f"Device {device_id!r} was not found.")
            service = DeviceService(
                session,
                company_id=self.company_id,
                actor_user_id=self.actor_user_id,
                device_manager=self._device_manager,
            )
            return service.test_connection(device)

        result = self._run(do_test)
        self.devices_changed.emit()
        return bool(result)

    @requires_permission("devices.manage", default=0)
    def sync_attendance_logs(self, device_id: int) -> int:
        """Download and persist a device's attendance logs.

        Args:
            device_id: The device to sync.

        Returns:
            The number of new punches created, or ``0`` on failure.
        """

        def do_sync(session: Session) -> int:
            repo = DeviceRepository(session, company_id=self.company_id)
            device = repo.get_by_id(device_id)
            if device is None:
                raise ValueError(f"Device {device_id!r} was not found.")
            service = DeviceService(
                session,
                company_id=self.company_id,
                actor_user_id=self.actor_user_id,
                device_manager=self._device_manager,
            )
            return len(service.sync_attendance_logs(device))

        result = self._run(do_sync)
        if result:
            self.devices_changed.emit()
        return result or 0

    @requires_permission("devices.manage", default=False)
    def push_employee_to_device(self, *, device_id: int, employee_id: int) -> bool:
        """Enroll one employee on a device.

        Args:
            device_id: The device to push to.
            employee_id: The employee to enroll.

        Returns:
            ``True`` on success, ``False`` on failure.
        """

        def do_push(session: Session) -> bool:
            device_repo = DeviceRepository(session, company_id=self.company_id)
            device = device_repo.get_by_id(device_id)
            if device is None:
                raise ValueError(f"Device {device_id!r} was not found.")
            employee_repo = EmployeeRepository(session, company_id=self.company_id)
            employee = employee_repo.get_by_id(employee_id)
            if employee is None:
                raise ValueError(f"Employee {employee_id!r} was not found.")
            service = DeviceService(
                session,
                company_id=self.company_id,
                actor_user_id=self.actor_user_id,
                device_manager=self._device_manager,
            )
            service.push_employee_to_device(device, employee)
            return True

        return bool(self._run(do_push))

    @requires_permission("devices.view", "devices.manage", default=[])
    def list_devices(self, *, active_only: bool = False) -> list[dict[str, Any]]:
        """List this company's devices.

        Args:
            active_only: Restrict to devices eligible for scheduled
                sync jobs.

        Returns:
            Matching devices' data as dicts; an empty list on failure.
        """

        def do_list(session: Session) -> list[dict[str, Any]]:
            service = DeviceService(session, company_id=self.company_id)
            return [_device_to_dict(device) for device in service.list_devices(active_only=active_only)]

        return self._run(do_list) or []
