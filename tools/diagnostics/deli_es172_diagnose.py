"""Safe, read-only network diagnostic for a DELI ES172 attendance device.

Standalone by design -- stdlib only, no dependency on this repository's
virtual environment or any third-party package -- so it can run on the
customer's Windows PC exactly as-is, with nothing installed beyond
Python itself.

What this does, and does not, do:

- Tests whether the device host is reachable (ping) and which of a
  fixed set of candidate ports accept a TCP connection.
- For each OPEN port, passively reads whatever bytes the service sends
  first (many services greet unprompted), then makes a small number of
  plain, unauthenticated, read-only HTTP GET requests on FRESH
  connections to see whether the port speaks HTTP -- never reusing a
  socket a proprietary protocol may already be mid-handshake on.
- For port 443, additionally attempts a TLS handshake (no certificate
  validation, since this is a local device almost certainly using a
  self-signed certificate) purely to read back the negotiated
  protocol/cipher and certificate subject, if any.
- For port 5005 specifically, additionally attempts one WebSocket
  upgrade handshake -- a standard, harmless HTTP Upgrade request -- to
  see whether the response is a 101 Switching Protocols (WebSocket), a
  normal HTTP response (plain HTTP), or something else entirely (likely
  a proprietary binary protocol).

This NEVER sends an API key, device UID, enrollment command, write
request, or any device-specific proprietary payload -- there is
nothing in this script for a human to guess at the DELI protocol with,
only generic, protocol-agnostic probing that is safe against any TCP/
HTTP service. See devices/device_interface.py's own module docstring
in the Ali1 repository for why: every real connector must be a
faithful adapter over a vendor's *documented* SDK, never a
reverse-engineered guess -- and neither is this diagnostic.

Usage (from a Command Prompt or by double-clicking Run_DELI_Diagnostic.bat):

    python deli_es172_diagnose.py [IP_ADDRESS]

If IP_ADDRESS is omitted, 192.168.1.28 (the device's currently reported
address) is used. Writes a timestamped report (.json and .txt) into a
"deli_diagnostic_output" folder created next to this script, and prints
the exact file to send back at the end.
"""

from __future__ import annotations

import json
import platform
import socket
import ssl
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

DEFAULT_TARGET_IP = "192.168.1.28"

#: Exactly the ports requested for this investigation.
CANDIDATE_PORTS = [5005, 4370, 7070, 80, 443, 8080]

CONNECT_TIMEOUT_SECONDS = 3.0
BANNER_READ_TIMEOUT_SECONDS = 2.0
BANNER_MAX_BYTES = 4096

#: Small, fixed set of conventional read-only paths. All GET, no body,
#: no auth headers -- a GET to a path that doesn't exist on a real web
#: server just returns 404, which is itself useful diagnostic signal.
HTTP_PROBE_PATHS = ["/", "/api", "/api/info", "/info", "/status", "/version"]

WEBSOCKET_PROBE_PORTS = {5005}


def _now_utc_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def ping_host(ip: str) -> dict[str, Any]:
    """Best-effort ICMP reachability check via the OS's own ping command.

    Uses the platform's ``ping`` binary (via subprocess) rather than a
    raw ICMP socket, which on Windows requires no special privileges
    and works identically for a non-technical user double-clicking a
    .bat file.
    """
    is_windows = platform.system().lower() == "windows"
    if is_windows:
        command = ["ping", "-n", "2", "-w", "1000", ip]
    else:
        command = ["ping", "-c", "2", "-W", "1", ip]

    try:
        result = subprocess.run(
            command, capture_output=True, text=True, timeout=10
        )
        reachable = result.returncode == 0
        return {
            "reachable": reachable,
            "return_code": result.returncode,
            "raw_output": (result.stdout or "") + (result.stderr or ""),
        }
    except Exception as exc:  # noqa: BLE001 - diagnostic tool, report any failure as-is
        return {"reachable": False, "return_code": None, "raw_output": "", "error": str(exc)}


def _decode_snippet(raw: bytes) -> str:
    """Render raw bytes as a readable snippet: UTF-8 if possible, else hex."""
    try:
        text = raw.decode("utf-8")
        if text.isprintable() or "\n" in text or "\r" in text:
            return text
    except UnicodeDecodeError:
        pass
    return raw.hex(" ")


def probe_tcp_port(ip: str, port: int) -> dict[str, Any]:
    """Whether ``ip:port`` accepts a TCP connection, and any unprompted banner."""
    entry: dict[str, Any] = {"port": port, "open": False}
    sock = None
    try:
        start = time.monotonic()
        sock = socket.create_connection((ip, port), timeout=CONNECT_TIMEOUT_SECONDS)
        entry["open"] = True
        entry["connect_time_ms"] = round((time.monotonic() - start) * 1000, 1)

        sock.settimeout(BANNER_READ_TIMEOUT_SECONDS)
        try:
            banner = sock.recv(BANNER_MAX_BYTES)
            entry["unsolicited_banner_bytes"] = len(banner)
            entry["unsolicited_banner_snippet"] = _decode_snippet(banner)
        except socket.timeout:
            entry["unsolicited_banner_bytes"] = 0
            entry["unsolicited_banner_snippet"] = None
            entry["banner_note"] = "no data sent within timeout (service may wait for the client to speak first)"
    except OSError as exc:
        entry["open"] = False
        entry["error"] = str(exc)
    finally:
        if sock is not None:
            try:
                sock.close()
            except OSError:
                pass
    return entry


def try_http_get(ip: str, port: int, path: str, *, use_tls: bool) -> dict[str, Any]:
    """One plain, read-only HTTP GET on a fresh connection; never reuses a socket."""
    result: dict[str, Any] = {"path": path, "use_tls": use_tls}
    sock = None
    try:
        raw_sock = socket.create_connection((ip, port), timeout=CONNECT_TIMEOUT_SECONDS)
        if use_tls:
            context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
            context.check_hostname = False
            context.verify_mode = ssl.CERT_NONE
            sock = context.wrap_socket(raw_sock, server_hostname=ip)
        else:
            sock = raw_sock
        sock.settimeout(BANNER_READ_TIMEOUT_SECONDS)

        request = (
            f"GET {path} HTTP/1.1\r\n"
            f"Host: {ip}\r\n"
            f"User-Agent: Ali1-DELI-Diagnostic/1.0\r\n"
            f"Connection: close\r\n\r\n"
        ).encode("ascii")
        sock.sendall(request)

        chunks = []
        try:
            while True:
                chunk = sock.recv(4096)
                if not chunk:
                    break
                chunks.append(chunk)
                if sum(len(c) for c in chunks) > 65536:
                    break
        except socket.timeout:
            pass
        raw = b"".join(chunks)

        result["response_bytes"] = len(raw)
        looks_like_http = raw.startswith(b"HTTP/")
        result["looks_like_http"] = looks_like_http
        if looks_like_http:
            head, _, body = raw.partition(b"\r\n\r\n")
            lines = head.decode("iso-8859-1", errors="replace").split("\r\n")
            result["status_line"] = lines[0] if lines else None
            result["headers"] = lines[1:]
            result["body_snippet"] = _decode_snippet(body[:1024])
        else:
            result["raw_snippet"] = _decode_snippet(raw[:1024])
    except OSError as exc:
        result["error"] = str(exc)
    finally:
        if sock is not None:
            try:
                sock.close()
            except OSError:
                pass
    return result


def try_tls_handshake(ip: str, port: int) -> dict[str, Any]:
    """Attempt a TLS handshake without validating the certificate, purely to inspect it."""
    result: dict[str, Any] = {}
    try:
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        context.check_hostname = False
        context.verify_mode = ssl.CERT_NONE
        with socket.create_connection((ip, port), timeout=CONNECT_TIMEOUT_SECONDS) as raw_sock:
            with context.wrap_socket(raw_sock, server_hostname=ip) as tls_sock:
                result["tls_handshake_ok"] = True
                result["negotiated_protocol"] = tls_sock.version()
                result["cipher"] = tls_sock.cipher()
                cert = tls_sock.getpeercert(binary_form=True)
                result["certificate_present"] = cert is not None
    except Exception as exc:  # noqa: BLE001 - report any TLS failure as diagnostic data
        result["tls_handshake_ok"] = False
        result["error"] = str(exc)
    return result


def try_websocket_handshake(ip: str, port: int) -> dict[str, Any]:
    """One standard WebSocket upgrade request on a fresh connection -- harmless if refused."""
    result: dict[str, Any] = {}
    sock = None
    try:
        sock = socket.create_connection((ip, port), timeout=CONNECT_TIMEOUT_SECONDS)
        sock.settimeout(BANNER_READ_TIMEOUT_SECONDS)
        # A fixed, valid-format (but not cryptographically meaningful) key is
        # sufficient to elicit a real server's handshake response; this
        # diagnostic never proceeds past the handshake.
        request = (
            "GET / HTTP/1.1\r\n"
            f"Host: {ip}\r\n"
            "Upgrade: websocket\r\n"
            "Connection: Upgrade\r\n"
            "Sec-WebSocket-Key: ZGVsaS1kaWFnbm9zdGljMTY=\r\n"
            "Sec-WebSocket-Version: 13\r\n\r\n"
        ).encode("ascii")
        sock.sendall(request)
        try:
            raw = sock.recv(4096)
        except socket.timeout:
            raw = b""
        result["response_bytes"] = len(raw)
        result["is_101_switching_protocols"] = raw.startswith(b"HTTP/1.1 101")
        result["raw_snippet"] = _decode_snippet(raw[:1024])
    except OSError as exc:
        result["error"] = str(exc)
    finally:
        if sock is not None:
            try:
                sock.close()
            except OSError:
                pass
    return result


def diagnose(ip: str) -> dict[str, Any]:
    report: dict[str, Any] = {
        "tool": "deli_es172_diagnose.py",
        "tool_version": "1.0",
        "generated_at_utc": _now_utc_iso(),
        "target_ip": ip,
        "runner_platform": platform.platform(),
        "runner_python": sys.version,
    }

    print(f"[1/3] Pinging {ip} ...")
    report["ping"] = ping_host(ip)
    print(f"      reachable = {report['ping']['reachable']}")

    print(f"[2/3] Probing TCP ports {CANDIDATE_PORTS} ...")
    ports_report = []
    for port in CANDIDATE_PORTS:
        print(f"      - port {port}: connecting ...", end=" ")
        entry = probe_tcp_port(ip, port)
        if not entry["open"]:
            print("closed/unreachable")
            ports_report.append(entry)
            continue
        print(f"OPEN ({entry.get('connect_time_ms')} ms)")

        print(f"        reading unsolicited banner ...", end=" ")
        print(f"{entry.get('unsolicited_banner_bytes', 0)} bytes")

        print("        trying plain HTTP GET requests ...")
        entry["http_probes"] = [
            try_http_get(ip, port, path, use_tls=False) for path in HTTP_PROBE_PATHS
        ]
        any_http = any(p.get("looks_like_http") for p in entry["http_probes"])
        print(f"        -> looks like plain HTTP: {any_http}")

        if port == 443:
            print("        attempting TLS handshake ...")
            entry["tls"] = try_tls_handshake(ip, port)
            if entry["tls"].get("tls_handshake_ok"):
                print("        TLS OK, trying HTTPS GET requests ...")
                entry["https_probes"] = [
                    try_http_get(ip, port, path, use_tls=True) for path in HTTP_PROBE_PATHS
                ]

        if port in WEBSOCKET_PROBE_PORTS:
            print(f"        trying WebSocket upgrade handshake on port {port} ...")
            entry["websocket_probe"] = try_websocket_handshake(ip, port)
            print(f"        -> is 101 Switching Protocols: {entry['websocket_probe'].get('is_101_switching_protocols')}")

        ports_report.append(entry)

    report["ports"] = ports_report

    print("[3/3] Done.")
    return report


def _write_reports(report: dict[str, Any]) -> tuple[Path, Path]:
    output_dir = Path(__file__).resolve().parent / "deli_diagnostic_output"
    output_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    json_path = output_dir / f"deli_diagnostic_{timestamp}.json"
    txt_path = output_dir / f"deli_diagnostic_{timestamp}.txt"

    json_path.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")

    lines = [
        "DELI ES172 network diagnostic report",
        f"Generated: {report['generated_at_utc']}",
        f"Target IP: {report['target_ip']}",
        f"Runner: {report['runner_platform']}",
        "",
        f"Ping reachable: {report['ping']['reachable']}",
        "",
    ]
    for entry in report["ports"]:
        lines.append(f"Port {entry['port']}: {'OPEN' if entry['open'] else 'closed/unreachable'}")
        if not entry["open"]:
            if entry.get("error"):
                lines.append(f"  error: {entry['error']}")
            continue
        lines.append(f"  connect time: {entry.get('connect_time_ms')} ms")
        lines.append(f"  unsolicited banner bytes: {entry.get('unsolicited_banner_bytes', 0)}")
        if entry.get("unsolicited_banner_snippet"):
            lines.append(f"  unsolicited banner: {entry['unsolicited_banner_snippet']!r}")
        for probe in entry.get("http_probes", []):
            if probe.get("looks_like_http"):
                lines.append(f"  GET {probe['path']} -> HTTP: {probe.get('status_line')}")
            elif probe.get("error"):
                lines.append(f"  GET {probe['path']} -> error: {probe['error']}")
            else:
                lines.append(f"  GET {probe['path']} -> non-HTTP response ({probe.get('response_bytes', 0)} bytes)")
        if "tls" in entry:
            lines.append(f"  TLS handshake ok: {entry['tls'].get('tls_handshake_ok')}")
        if "websocket_probe" in entry:
            ws = entry["websocket_probe"]
            lines.append(f"  WebSocket upgrade -> 101 Switching Protocols: {ws.get('is_101_switching_protocols')}")
            lines.append(f"  WebSocket raw response snippet: {ws.get('raw_snippet')!r}")
        lines.append("")

    txt_path.write_text("\n".join(lines), encoding="utf-8")
    return json_path, txt_path


def main() -> int:
    ip = sys.argv[1].strip() if len(sys.argv) > 1 and sys.argv[1].strip() else DEFAULT_TARGET_IP
    print("=" * 70)
    print("DELI ES172 safe network diagnostic")
    print(f"Target device: {ip}")
    print("This tool only performs read-only network probing.")
    print("It never sends an API key, device UID, or any enrollment/write command.")
    print("=" * 70)
    print()

    report = diagnose(ip)
    json_path, txt_path = _write_reports(report)

    print()
    print("=" * 70)
    print("DONE. Please send BOTH of these files back:")
    print(f"  1) {json_path}")
    print(f"  2) {txt_path}")
    print("=" * 70)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
