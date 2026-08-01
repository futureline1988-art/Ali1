"""Biometric device integration: protocol-specific connectors behind a
single interface (see ``devices/device_interface.py``). No proprietary
protocol is reimplemented here — every connector is a thin adapter over
its vendor's documented SDK/API (pyzk for ZKTeco, Hikvision's ISAPI over
HTTP)."""
