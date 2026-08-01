"""Best-effort LAN device discovery.

Implemented as a bounded, concurrent TCP connect-scan over a subnet for
the ports known biometric devices listen on — not any vendor's
proprietary broadcast-discovery protocol, which needs no reverse
-engineering and works identically regardless of vendor. A positive
result here only means *something* is listening on that port; callers
should confirm with an actual protocol connector (e.g.
:meth:`~services.device_service.DeviceService.test_connection`) before
treating a discovered host as a real, working device.
"""

from __future__ import annotations

import socket
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from ipaddress import IPv4Network

from config import get_config
from models.enums import DeviceProtocol

#: Well-known ports for the protocols this project supports, used as
#: the default scan target set.
DEFAULT_CANDIDATE_PORTS: dict[int, DeviceProtocol] = {
    4370: DeviceProtocol.ZKTECO_TCP,
    80: DeviceProtocol.HIKVISION,
}

_DEFAULT_MAX_WORKERS = 50


@dataclass(frozen=True)
class DiscoveredDevice:
    """A host that responded on a known device port during a scan.

    Attributes:
        host: The responding host's IP address.
        port: The port that accepted a connection.
        likely_protocol: The protocol conventionally associated with
            ``port`` — a hint, not a confirmed identification.
    """

    host: str
    port: int
    likely_protocol: DeviceProtocol


def _probe(host: str, port: int, timeout: float) -> bool:
    """Whether ``host:port`` accepts a TCP connection within ``timeout`` seconds."""
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def scan_network(
    cidr: str,
    *,
    ports: dict[int, DeviceProtocol] | None = None,
    timeout: float | None = None,
    max_workers: int = _DEFAULT_MAX_WORKERS,
) -> list[DiscoveredDevice]:
    """Scan a subnet for hosts listening on known biometric-device ports.

    Args:
        cidr: Subnet to scan, in CIDR notation (e.g. ``"192.168.1.0/24"``).
        ports: Port -> protocol mapping to probe; defaults to
            :data:`DEFAULT_CANDIDATE_PORTS`.
        timeout: Per-host connection timeout, in seconds; defaults to
            :attr:`config.DeviceConfig.discovery_timeout_seconds`.
        max_workers: Maximum concurrent connection attempts, bounding
            resource usage on large subnets.

    Returns:
        One :class:`DiscoveredDevice` per responding host/port
        combination.
    """
    resolved_ports = ports or DEFAULT_CANDIDATE_PORTS
    resolved_timeout = (
        timeout if timeout is not None else get_config().device.discovery_timeout_seconds
    )
    network = IPv4Network(cidr, strict=False)

    targets = [
        (str(host), port, protocol)
        for host in network.hosts()
        for port, protocol in resolved_ports.items()
    ]

    discovered: list[DiscoveredDevice] = []
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        future_to_target = {
            executor.submit(_probe, host, port, resolved_timeout): (host, port, protocol)
            for host, port, protocol in targets
        }
        for future, (host, port, protocol) in future_to_target.items():
            if future.result():
                discovered.append(
                    DiscoveredDevice(host=host, port=port, likely_protocol=protocol)
                )

    return discovered
