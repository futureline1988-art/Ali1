"""Tests for :class:`~devices.zkteco_device.ZKTecoConnector`, especially device-UID assignment stability.

These tests fake pyzk's ``zk`` package entirely (via ``sys.modules``)
rather than requiring real hardware or the real network protocol --
this project treats ``pyzk`` as an external dependency it only ever
calls through a handful of documented methods (see
``devices/zkteco_device.py``'s own docstring: "a thin adapter only"),
so a test double that faithfully reproduces those methods' documented
*behavior* (uid auto-assignment via ``uid=None``, user lookup by
``user_id``, face/fingerprint capability reporting) is exactly what's
needed to prove this connector's own logic is correct without needing
a physical device.
"""

from __future__ import annotations

import sys
import types

import pytest


class _FakeUser:
    def __init__(self, uid, user_id, name, card=0):
        self.uid = uid
        self.user_id = user_id
        self.name = name
        self.card = card


class _FakeFinger:
    def __init__(self, uid, fid, valid, template):
        self.uid = uid
        self.fid = fid
        self.valid = valid
        self.template = template


class _FakeDeviceState:
    """In-memory state for one simulated ZKTeco device, shared across reconnects."""

    def __init__(self):
        self.users: dict[int, _FakeUser] = {}
        self.next_uid = 1
        self.fingers: list[_FakeFinger] = []
        self.face_fun_on = 0
        self.face_version = 0
        self.faces = 0
        self.faces_cap = 0
        self.fingers_cap = 3000
        self.records = 0
        self.users_cap = 3000
        self.serial_number = "FAKE-SERIAL-0001"
        self.platform = "ZMM220_TFT"
        self.firmware_version = "Ver 6.60"

    def enable_face(self, *, capacity=50):
        self.face_fun_on = 1
        self.face_version = 7
        self.faces_cap = capacity


class _FakeConnection:
    """Fakes a connected ``zk.ZK`` instance, backed by shared device state."""

    def __init__(self, state: _FakeDeviceState):
        self._state = state

    def get_users(self):
        return list(self._state.users.values())

    def set_user(self, uid=None, name="", privilege=0, password="", group_id="", user_id="", card=0):
        if uid is None:
            uid = self._state.next_uid
        self._state.users[uid] = _FakeUser(uid, user_id, name, card)
        if uid >= self._state.next_uid:
            self._state.next_uid = uid + 1
        return True

    def get_attendance(self):
        return []

    def get_templates(self):
        return list(self._state.fingers)

    def read_sizes(self):
        self.users = len(self._state.users)
        self.fingers = len(self._state.fingers)
        self.records = self._state.records
        self.faces = self._state.faces
        self.faces_cap = self._state.faces_cap
        self.fingers_cap = self._state.fingers_cap
        self.users_cap = self._state.users_cap
        return True

    def get_face_version(self):
        return self._state.face_version

    def get_face_fun_on(self):
        return self._state.face_fun_on

    def get_serialnumber(self):
        return self._state.serial_number

    def get_platform(self):
        return self._state.platform

    def get_device_name(self):
        return self._state.platform

    def get_firmware_version(self):
        return self._state.firmware_version

    def disconnect(self):
        return True


class _FakeZK:
    """Fakes ``zk.ZK`` -- constructed fresh per connect, backed by shared state."""

    _shared_state: _FakeDeviceState | None = None  # set by fake_zkteco_device

    def __init__(
        self,
        ip,
        port=4370,
        timeout=60,
        password=0,
        force_udp=False,
        ommit_ping=False,
        verbose=False,
        encoding="UTF-8",
    ):
        self.ip = ip
        self.port = port

    def connect(self):
        return _FakeConnection(type(self)._shared_state)


@pytest.fixture
def fake_zkteco_device(monkeypatch):
    """Install a fake ``zk`` package and return its shared, in-memory device state.

    Simulates reconnecting to the *same physical device* across
    multiple :class:`~devices.zkteco_device.ZKTecoConnector` instances
    (each connector opens and closes its own connection) by keeping
    one :class:`_FakeDeviceState` alive for the whole test, exactly
    like a real device's own internal storage persists across TCP
    sessions.
    """
    state = _FakeDeviceState()
    _FakeZK._shared_state = state

    fake_zk_module = types.ModuleType("zk")
    fake_zk_module.ZK = _FakeZK

    fake_exception_module = types.ModuleType("zk.exception")

    class ZKErrorConnection(Exception):
        pass

    class ZKErrorResponse(Exception):
        pass

    class ZKNetworkError(Exception):
        pass

    fake_exception_module.ZKErrorConnection = ZKErrorConnection
    fake_exception_module.ZKErrorResponse = ZKErrorResponse
    fake_exception_module.ZKNetworkError = ZKNetworkError
    fake_zk_module.exception = fake_exception_module

    monkeypatch.setitem(sys.modules, "zk", fake_zk_module)
    monkeypatch.setitem(sys.modules, "zk.exception", fake_exception_module)

    yield state

    _FakeZK._shared_state = None


def _connector(**overrides):
    from devices.zkteco_device import ZKTecoConnector

    params = dict(host="10.0.0.50", port=4370, password="0", timeout=5, force_udp=False)
    params.update(overrides)
    return ZKTecoConnector(**params)


class TestPushUsersUidAssignment:
    """The bug fixed here: ``push_users`` used to always pass ``uid=0``, a literal
    slot, so every pushed employee silently overwrote the same device user."""

    def test_two_new_employees_get_two_distinct_uids(self, fake_zkteco_device):
        from devices.device_interface import RawDeviceUser

        connector = _connector()
        connector.push_users(
            [
                RawDeviceUser(device_user_reference="1001", name="Employee One"),
                RawDeviceUser(device_user_reference="1002", name="Employee Two"),
            ]
        )
        connector.disconnect()

        assert len(fake_zkteco_device.users) == 2
        uids_by_reference = {u.user_id: u.uid for u in fake_zkteco_device.users.values()}
        assert uids_by_reference["1001"] != uids_by_reference["1002"]

    def test_repushing_the_same_employee_preserves_their_existing_uid(self, fake_zkteco_device):
        """Simulates a re-sync/reconnect: the employee's device slot must not change."""
        from devices.device_interface import RawDeviceUser

        first_connector = _connector()
        first_connector.push_users([RawDeviceUser(device_user_reference="2001", name="Ali")])
        first_connector.disconnect()

        original_uid = next(iter(fake_zkteco_device.users.values())).uid

        second_connector = _connector()  # a brand-new connector, like a fresh sync run
        second_connector.push_users(
            [RawDeviceUser(device_user_reference="2001", name="Ali Updated Name")]
        )
        second_connector.disconnect()

        assert len(fake_zkteco_device.users) == 1  # no duplicate slot created
        preserved_user = next(iter(fake_zkteco_device.users.values()))
        assert preserved_user.uid == original_uid
        assert preserved_user.name == "Ali Updated Name"

    def test_multiple_employees_survive_a_second_sync_round_without_reassignment_or_collision(
        self, fake_zkteco_device
    ):
        """Several employees enrolled, then a second sync adds one more employee --
        every existing UID must stay exactly as it was, and nothing collides."""
        from devices.device_interface import RawDeviceUser

        first_connector = _connector()
        first_connector.push_users(
            [
                RawDeviceUser(device_user_reference="3001", name="Employee A"),
                RawDeviceUser(device_user_reference="3002", name="Employee B"),
                RawDeviceUser(device_user_reference="3003", name="Employee C"),
            ]
        )
        first_connector.disconnect()

        uids_after_first_sync = {u.user_id: u.uid for u in fake_zkteco_device.users.values()}
        assert len(set(uids_after_first_sync.values())) == 3

        second_connector = _connector()
        second_connector.push_users(
            [
                RawDeviceUser(device_user_reference="3001", name="Employee A"),
                RawDeviceUser(device_user_reference="3002", name="Employee B"),
                RawDeviceUser(device_user_reference="3003", name="Employee C"),
                RawDeviceUser(device_user_reference="3004", name="Employee D"),
            ]
        )
        second_connector.disconnect()

        uids_after_second_sync = {u.user_id: u.uid for u in fake_zkteco_device.users.values()}
        assert len(fake_zkteco_device.users) == 4
        assert len(set(uids_after_second_sync.values())) == 4
        for reference in ("3001", "3002", "3003"):
            assert uids_after_second_sync[reference] == uids_after_first_sync[reference]
        assert uids_after_second_sync["3004"] not in uids_after_first_sync.values()

    def test_uid_lookup_is_always_fresh_never_locally_cached(self, fake_zkteco_device):
        """Manually re-numbering a user on the device (as if someone edited it
        directly) must be picked up on the next push, not overridden by a stale
        local assumption -- this connector must never keep its own uid cache."""
        from devices.device_interface import RawDeviceUser

        first_connector = _connector()
        first_connector.push_users([RawDeviceUser(device_user_reference="4001", name="Sara")])
        first_connector.disconnect()

        # Simulate the device operator manually moving this user to a
        # different internal slot directly on the device.
        old_entry = fake_zkteco_device.users.pop(next(iter(fake_zkteco_device.users)))
        fake_zkteco_device.users[99] = _FakeUser(99, old_entry.user_id, old_entry.name)

        second_connector = _connector()
        second_connector.push_users(
            [RawDeviceUser(device_user_reference="4001", name="Sara Renamed")]
        )
        second_connector.disconnect()

        assert len(fake_zkteco_device.users) == 1
        current = next(iter(fake_zkteco_device.users.values()))
        assert current.uid == 99  # followed the device's current truth, not a stale cache
        assert current.name == "Sara Renamed"


class TestGetCapabilities:
    def test_reports_face_unsupported_when_firmware_reports_it_off(self, fake_zkteco_device):
        capabilities = _connector().get_capabilities()
        assert capabilities.supports_face is False
        assert capabilities.supports_fingerprint is True

    def test_reports_face_supported_when_firmware_enables_it(self, fake_zkteco_device):
        fake_zkteco_device.enable_face(capacity=80)
        fake_zkteco_device.faces = 3

        capabilities = _connector().get_capabilities()

        assert capabilities.supports_face is True
        assert capabilities.face_template_count == 3
        assert capabilities.face_capacity == 80

    def test_reports_device_identity_fields(self, fake_zkteco_device):
        capabilities = _connector().get_capabilities()
        assert capabilities.serial_number == "FAKE-SERIAL-0001"
        assert capabilities.device_model == "ZMM220_TFT"
        assert capabilities.firmware_version == "Ver 6.60"

    def test_reports_user_and_record_counts(self, fake_zkteco_device):
        from devices.device_interface import RawDeviceUser

        connector = _connector()
        connector.push_users([RawDeviceUser(device_user_reference="5001", name="X")])
        connector.disconnect()
        fake_zkteco_device.records = 42

        capabilities = _connector().get_capabilities()
        assert capabilities.user_count == 1
        assert capabilities.attendance_log_count == 42


class TestGetUserBiometricStatus:
    def test_unknown_user_returns_zero_status(self, fake_zkteco_device):
        status = _connector().get_user_biometric_status("no-such-user")
        assert status.fingerprint_count == 0
        assert status.card_assigned is False

    def test_reports_card_assignment_for_a_known_user(self, fake_zkteco_device):
        fake_zkteco_device.users[1] = _FakeUser(1, "6001", "Card Holder", card=555)
        fake_zkteco_device.next_uid = 2

        status = _connector().get_user_biometric_status("6001")
        assert status.card_assigned is True

    def test_counts_only_this_users_valid_fingerprint_templates(self, fake_zkteco_device):
        fake_zkteco_device.users[1] = _FakeUser(1, "7001", "Has Two Fingers")
        fake_zkteco_device.users[2] = _FakeUser(2, "7002", "Has One Finger")
        fake_zkteco_device.next_uid = 3
        fake_zkteco_device.fingers = [
            _FakeFinger(uid=1, fid=0, valid=1, template=b"a"),
            _FakeFinger(uid=1, fid=1, valid=1, template=b"b"),
            _FakeFinger(uid=1, fid=2, valid=0, template=b"c"),  # invalid, not counted
            _FakeFinger(uid=2, fid=0, valid=1, template=b"d"),
        ]

        assert _connector().get_user_biometric_status("7001").fingerprint_count == 2
        assert _connector().get_user_biometric_status("7002").fingerprint_count == 1
