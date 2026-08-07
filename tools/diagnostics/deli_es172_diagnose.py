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
- For EVERY open port (not just 443 -- a proprietary service can just
  as well be TLS-wrapped on a non-standard port), additionally attempts
  a TLS handshake (no certificate validation, since this is a local
  device almost certainly using a self-signed certificate) purely to
  read back the negotiated protocol/cipher and certificate subject, if
  any.
- For port 5005 specifically, additionally attempts one WebSocket
  upgrade handshake -- a standard, harmless HTTP Upgrade request -- to
  see whether the response is a 101 Switching Protocols (WebSocket), a
  normal HTTP response (plain HTTP), or something else entirely (likely
  a proprietary binary protocol).
- For any port that DOES turn out to speak plain HTTP, additionally
  fetches the full "/" page and investigates it as a possible
  development/test web interface: parses every link, form, script,
  and inline script the page itself references, and follows -- GET
  only, same-origin only, never guessed -- whatever JS/CSS files AND
  links it finds. See :func:`investigate_http_dev_interface`'s own
  docstring for the exact, hard read-only boundary this respects. This
  step exists because the real DELI ES172 was found to expose exactly
  such an interface on port 80 ("Attendance & Access machine
  development test interface", served by Boost.Beast) -- confirmed by
  a real on-device diagnostic run, not assumed.
- For any port that stays unidentified after all of the above (open,
  but neither HTTP, WebSocket, nor TLS -- exactly what port 5005 turned
  out to be on the real device), additionally runs
  :func:`investigate_unknown_tcp_service`: listens passively for longer
  in case the service greets slowly, then sends one generic,
  protocol-agnostic "nudge" (the same kind of harmless probe nmap's own
  service-detection uses) to see whether it reacts to *any* input at
  all -- recording every byte sent and received in both hex and ASCII.
  This is the deepest this tool goes without real protocol evidence;
  see that function's own docstring for exactly what it does and does
  not send.

This NEVER sends an API key, device UID, enrollment command, write
request, or any device-specific proprietary payload -- there is
nothing in this script for a human to guess at the DELI protocol with,
only generic, protocol-agnostic probing that is safe against any TCP/
HTTP service. See devices/device_interface.py's own module docstring
in the Ali1 repository for why: every real connector must be a
faithful adapter over a vendor's *documented* SDK, never a
reverse-engineered guess -- and neither is this diagnostic.

Usage (from a Command Prompt or by double-clicking Run_DELI_Diagnostic.bat --
requires a separately installed Python 3, see that file's own notes):

    python deli_es172_diagnose.py [IP_ADDRESS]

If IP_ADDRESS is omitted, 192.168.1.28 (the device's currently reported
address) is used. Writes a timestamped report (.json and .txt) into a
"deli_diagnostic_output" folder created next to this script, and prints
the exact file to send back at the end.

On a normal customer installation, prefer the built-in "تشخيص شبكة
الجهاز" (Network diagnostic) button on the Devices page of the
Attendance Management System application instead -- it calls
:func:`diagnose` and :func:`_write_reports` directly from inside the
already-installed, already-bundled application (see ``ui/devices.py``'s
``NetworkDiagnosticDialog``), so it needs no separate Python install at
all. This standalone script remains for developers/CI and for running
the check before the application itself is installed.
"""

from __future__ import annotations

import json
import platform
import re
import socket
import ssl
import subprocess
import sys
import time
from datetime import datetime, timezone
from html.parser import HTMLParser
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

#: Only a convenience pre-fill for the CLI arg / UI field -- the device's
#: IP has already changed twice during this investigation (originally
#: 192.168.68.138, then 192.168.1.28 after a DHCP reassignment, now
#: confirmed at 192.168.1.8) and always remains fully overridable
#: everywhere this is used; nothing in this module or in any connector
#: ever hard-codes a specific device address.
DEFAULT_TARGET_IP = "192.168.1.8"

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

#: Safety caps for the HTTP development-interface investigation -- large
#: enough for any reasonable single-page dev/test UI, small enough that
#: a misbehaving or hostile response can never make the diagnostic hang
#: or blow up memory.
DEV_INTERFACE_PAGE_MAX_BYTES = 2_000_000
DEV_INTERFACE_RESOURCE_MAX_BYTES = 1_000_000
DEV_INTERFACE_MAX_RESOURCES_FOLLOWED = 25

#: Endpoint-/path-looking string literals in HTML or JavaScript source,
#: e.g. "/api/employees" or '/set_wifi'. Deliberately conservative (must
#: start with "/", quoted, short) to keep false positives low.
_ENDPOINT_LIKE_PATTERN = re.compile(r"""["'](/[A-Za-z0-9_\-./]{1,120})["']""")
_FETCH_CALL_PATTERN = re.compile(r"""fetch\s*\(\s*[`'"](.*?)[`'"]""", re.IGNORECASE)
_XHR_OPEN_PATTERN = re.compile(
    r"""\.open\s*\(\s*[`'"](\w+)[`'"]\s*,\s*[`'"](.*?)[`'"]""", re.IGNORECASE
)
_WEBSOCKET_URL_PATTERN = re.compile(r"""(wss?://[^\s"'`]+)""", re.IGNORECASE)
_AUTH_KEYWORD_PATTERN = re.compile(
    r"""(?i)(api[_-]?key|apikey|authorization|bearer|x-auth|access[_-]?token|device[_-]?uid)"""
)


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


def _dechunk_if_needed(headers: list[str], body: bytes) -> bytes:
    """Decode an HTTP chunked-transfer-encoded body back to plain bytes.

    Reading a response until the server closes the connection (this
    module's HTTP requests all send ``Connection: close``) is correct
    for both ``Content-Length`` and chunked bodies, but a chunked body's
    raw bytes still contain the hex chunk-size markers interleaved with
    the actual content -- confusing for HTML parsing and grep-style
    endpoint extraction. Falls back to the raw body unchanged if it does
    not look chunked, or if parsing it as chunked fails for any reason.
    """
    header_text = "\n".join(headers).lower()
    if "transfer-encoding" not in header_text or "chunked" not in header_text:
        return body
    out = bytearray()
    pos = 0
    try:
        while pos < len(body):
            line_end = body.index(b"\r\n", pos)
            size_token = body[pos:line_end].split(b";", 1)[0].strip()
            chunk_size = int(size_token, 16)
            if chunk_size == 0:
                break
            chunk_start = line_end + 2
            chunk_end = chunk_start + chunk_size
            out.extend(body[chunk_start:chunk_end])
            pos = chunk_end + 2
        return bytes(out)
    except (ValueError, IndexError):
        return body


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


def fetch_full_http_resource(
    ip: str, port: int, path: str, *, max_bytes: int = DEV_INTERFACE_PAGE_MAX_BYTES
) -> dict[str, Any]:
    """A full (not 1KB-truncated) plain HTTP GET, for reading an entire page/script.

    Unlike :func:`try_http_get` (used only for protocol fingerprinting,
    hence its small body snippet), this is used to actually read a
    development/test web interface's real content -- still one GET per
    call, on a fresh connection, ``Connection: close``, capped at
    ``max_bytes`` as a safety bound rather than left unbounded.
    """
    result: dict[str, Any] = {"path": path}
    sock = None
    try:
        sock = socket.create_connection((ip, port), timeout=CONNECT_TIMEOUT_SECONDS)
        sock.settimeout(BANNER_READ_TIMEOUT_SECONDS)
        request = (
            f"GET {path} HTTP/1.1\r\n"
            f"Host: {ip}\r\n"
            f"User-Agent: Ali1-DELI-Diagnostic/1.0\r\n"
            f"Connection: close\r\n\r\n"
        ).encode("ascii")
        sock.sendall(request)

        chunks = []
        total = 0
        try:
            while True:
                chunk = sock.recv(8192)
                if not chunk:
                    break
                chunks.append(chunk)
                total += len(chunk)
                if total > max_bytes:
                    result["truncated"] = True
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
            body = _dechunk_if_needed(result["headers"], body)
            result["body_text"] = _decode_snippet(body)
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


class _DevInterfaceHTMLParser(HTMLParser):
    """Extracts links/forms/inputs/buttons/scripts/stylesheets from one HTML page.

    Deliberately lenient (``HTMLParser`` tolerates malformed HTML) and
    purely observational -- it never executes anything, only records
    what the page's own markup already contains.
    """

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.links: list[str] = []
        self.stylesheets: list[str] = []
        self.script_srcs: list[str] = []
        self.inline_scripts: list[str] = []
        self.forms: list[dict[str, Any]] = []
        self.buttons: list[dict[str, Any]] = []
        self._current_script_has_src = False
        self._current_script_buffer: list[str] | None = None
        self._current_form: dict[str, Any] | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attr_dict = dict(attrs)
        tag_lower = tag.lower()
        if tag_lower == "a" and attr_dict.get("href"):
            self.links.append(attr_dict["href"])
        elif tag_lower == "link" and attr_dict.get("href"):
            self.stylesheets.append(attr_dict["href"])
        elif tag_lower == "script":
            src = attr_dict.get("src")
            if src:
                self.script_srcs.append(src)
                self._current_script_has_src = True
                self._current_script_buffer = None
            else:
                self._current_script_has_src = False
                self._current_script_buffer = []
        elif tag_lower == "form":
            self._current_form = {
                "action": attr_dict.get("action"),
                "method": (attr_dict.get("method") or "GET").upper(),
                "inputs": [],
            }
        elif tag_lower == "input" and self._current_form is not None:
            self._current_form["inputs"].append(
                {"name": attr_dict.get("name"), "type": attr_dict.get("type")}
            )
        elif tag_lower == "button":
            self.buttons.append(
                {
                    "name": attr_dict.get("name"),
                    "type": attr_dict.get("type"),
                    "id": attr_dict.get("id"),
                    "onclick": attr_dict.get("onclick"),
                }
            )

    def handle_endtag(self, tag: str) -> None:
        tag_lower = tag.lower()
        if tag_lower == "script":
            if not self._current_script_has_src and self._current_script_buffer:
                self.inline_scripts.append("".join(self._current_script_buffer))
            self._current_script_buffer = None
        elif tag_lower == "form" and self._current_form is not None:
            self.forms.append(self._current_form)
            self._current_form = None

    def handle_data(self, data: str) -> None:
        if self._current_script_buffer is not None:
            self._current_script_buffer.append(data)


def _extract_endpoint_like_strings(text: str) -> list[str]:
    return sorted(set(_ENDPOINT_LIKE_PATTERN.findall(text)))


def _extract_fetch_calls(text: str) -> list[str]:
    return sorted(set(_FETCH_CALL_PATTERN.findall(text)))


def _extract_xhr_calls(text: str) -> list[dict[str, str]]:
    seen = set()
    calls = []
    for method, url in _XHR_OPEN_PATTERN.findall(text):
        key = (method.upper(), url)
        if key not in seen:
            seen.add(key)
            calls.append({"method": method.upper(), "url": url})
    return calls


def _extract_websocket_urls(text: str) -> list[str]:
    return sorted(set(_WEBSOCKET_URL_PATTERN.findall(text)))


def _extract_auth_hints(text: str) -> list[str]:
    """Lines mentioning an auth-related keyword (api key/token/authorization/device UID).

    A hint, not a conclusion -- surfaced verbatim (truncated) so a human
    can judge relevance, since a keyword match alone proves nothing
    about how (or whether) the device actually uses it.
    """
    hints = []
    for line in text.splitlines():
        if _AUTH_KEYWORD_PATTERN.search(line):
            stripped = line.strip()
            if stripped:
                hints.append(stripped[:200])
    return hints[:50]


def _analyze_script_text(text: str) -> dict[str, Any]:
    """Run every JS/HTML text-pattern extraction used by the dev-interface investigation."""
    return {
        "fetch_calls": _extract_fetch_calls(text),
        "xhr_calls": _extract_xhr_calls(text),
        "websocket_urls": _extract_websocket_urls(text),
        "endpoint_like_strings": _extract_endpoint_like_strings(text),
        "auth_hints": _extract_auth_hints(text),
        "mentions_swagger_or_openapi": bool(re.search(r"(?i)swagger|openapi", text)),
    }


def _same_origin_request_path(url: str | None) -> str | None:
    """The request path if `url` is same-origin (relative, no scheme/host), else None.

    Deliberately refuses anything with an explicit scheme or host --
    including plain ``http://`` to a *different* host -- so this
    investigation only ever follows a resource the page's own markup
    points back at itself, never a third-party or cross-origin URL.
    """
    if not url:
        return None
    parsed = urlsplit(url)
    if parsed.scheme or parsed.netloc:
        return None
    path = parsed.path or "/"
    if parsed.query:
        path = f"{path}?{parsed.query}"
    return path


def investigate_http_dev_interface(ip: str, port: int) -> dict[str, Any]:
    """Safe, GET-only investigation of an HTTP development/test interface.

    Built after a real on-device diagnostic run found the DELI ES172
    itself serving exactly such an interface on port 80 ("Attendance &
    Access machine development test interface", Boost.Beast) -- this is
    evidence-driven, not a guess at any device's behavior.

    Fetches the root page ("/") in full, parses its HTML for every
    link/form/input/button/script/stylesheet it contains, scans its
    inline scripts (and the page itself) for ``fetch()``/XHR/WebSocket
    calls and endpoint-looking path strings, and follows -- GET only --
    same-origin JS/CSS files the page itself references, up to
    :data:`DEV_INTERFACE_MAX_RESOURCES_FOLLOWED` of them. Never invents
    a path to try, never sends a body, never sends POST/PUT/DELETE,
    never touches anything not already linked from the page.
    """
    investigation: dict[str, Any] = {"port": port}

    root = fetch_full_http_resource(ip, port, "/", max_bytes=DEV_INTERFACE_PAGE_MAX_BYTES)
    investigation["root_page"] = root
    if not root.get("looks_like_http") or "body_text" not in root:
        investigation["error"] = "root page did not return a readable HTTP body"
        return investigation

    html_text = root["body_text"]
    parser = _DevInterfaceHTMLParser()
    try:
        parser.feed(html_text)
    except Exception as exc:  # noqa: BLE001 - never let malformed HTML crash the diagnostic
        investigation["html_parse_error"] = str(exc)

    investigation["links"] = parser.links
    investigation["stylesheets"] = parser.stylesheets
    investigation["script_srcs"] = parser.script_srcs
    investigation["forms"] = parser.forms
    investigation["buttons"] = parser.buttons
    investigation["inline_scripts"] = parser.inline_scripts

    combined_inline_script_text = "\n".join(parser.inline_scripts)
    investigation["inline_script_findings"] = _analyze_script_text(combined_inline_script_text)
    investigation["html_findings"] = _analyze_script_text(html_text)
    investigation["mentions_port_5005"] = "5005" in html_text or "5005" in combined_inline_script_text

    same_origin_paths: list[tuple[str, str]] = []
    seen_paths: set[str] = set()
    for referenced_url in [*parser.script_srcs, *parser.stylesheets]:
        path = _same_origin_request_path(referenced_url)
        if path is not None and path not in seen_paths:
            seen_paths.add(path)
            same_origin_paths.append((referenced_url, path))
    same_origin_paths = same_origin_paths[:DEV_INTERFACE_MAX_RESOURCES_FOLLOWED]

    fetched_resources = []
    for referenced_url, path in same_origin_paths:
        resource = fetch_full_http_resource(ip, port, path, max_bytes=DEV_INTERFACE_RESOURCE_MAX_BYTES)
        resource["referenced_as"] = referenced_url
        if resource.get("looks_like_http") and "body_text" in resource:
            resource["findings"] = _analyze_script_text(resource["body_text"])
            if "5005" in resource["body_text"]:
                investigation["mentions_port_5005"] = True
        fetched_resources.append(resource)
    investigation["fetched_resources"] = fetched_resources

    # Same treatment for every same-origin <a href> link the page itself
    # exposes -- explicitly requested evidence-gathering (e.g. "the exact
    # URL behind the single detected link"), still GET-only and still
    # never a path this tool invents itself.
    same_origin_link_paths: list[tuple[str, str]] = []
    seen_link_paths: set[str] = set(seen_paths)
    for referenced_url in parser.links:
        path = _same_origin_request_path(referenced_url)
        if path is not None and path not in seen_link_paths:
            seen_link_paths.add(path)
            same_origin_link_paths.append((referenced_url, path))
    same_origin_link_paths = same_origin_link_paths[:DEV_INTERFACE_MAX_RESOURCES_FOLLOWED]

    followed_links = []
    for referenced_url, path in same_origin_link_paths:
        followed = fetch_full_http_resource(ip, port, path, max_bytes=DEV_INTERFACE_RESOURCE_MAX_BYTES)
        followed["referenced_as"] = referenced_url
        if followed.get("looks_like_http") and "body_text" in followed:
            followed["findings"] = _analyze_script_text(followed["body_text"])
            if "5005" in followed["body_text"]:
                investigation["mentions_port_5005"] = True
        followed_links.append(followed)
    investigation["followed_links"] = followed_links

    return investigation


def _hex_and_ascii(raw: bytes) -> dict[str, str]:
    """Both representations of raw bytes, always -- never just one or the other."""
    return {"hex": raw.hex(" "), "ascii": raw.decode("ascii", errors="replace")}


def _extended_passive_listen(ip: str, port: int, *, window_seconds: float = 5.0) -> dict[str, Any]:
    """Listen on a fresh connection for up to ``window_seconds``, never sending anything.

    :func:`probe_tcp_port` only waits :data:`BANNER_READ_TIMEOUT_SECONDS`
    for a single read; some proprietary services take longer to greet,
    or send their banner in more than one chunk. Every chunk received is
    recorded with its elapsed time and both hex and ASCII forms.
    """
    reads: list[dict[str, Any]] = []
    total_bytes = 0
    sock = None
    start = time.monotonic()
    try:
        sock = socket.create_connection((ip, port), timeout=CONNECT_TIMEOUT_SECONDS)
        sock.settimeout(1.0)
        while time.monotonic() - start < window_seconds:
            try:
                chunk = sock.recv(4096)
            except socket.timeout:
                continue
            if not chunk:
                break
            reads.append({"elapsed_ms": round((time.monotonic() - start) * 1000, 1), **_hex_and_ascii(chunk)})
            total_bytes += len(chunk)
    except OSError as exc:
        return {"error": str(exc), "reads": reads, "total_bytes": total_bytes}
    finally:
        if sock is not None:
            try:
                sock.close()
            except OSError:
                pass
    return {"reads": reads, "total_bytes": total_bytes}


def _try_generic_probe(ip: str, port: int, name: str, payload: bytes) -> dict[str, Any]:
    """Send one small, protocol-agnostic payload on a fresh connection and record the raw response.

    ``payload`` is deliberately restricted to the same generic, harmless
    probes port-scanners like nmap use for service version detection
    (e.g. a bare ``\\r\\n\\r\\n``) -- never a guessed vendor-specific
    command or opcode. An empty payload sends nothing (equivalent to
    :func:`_extended_passive_listen`'s single-read case, offered here
    only for a uniform result shape when called from
    :func:`investigate_unknown_tcp_service`).
    """
    tx = _hex_and_ascii(payload)
    result: dict[str, Any] = {"name": name, "tx_bytes": len(payload), "tx_hex": tx["hex"], "tx_ascii": tx["ascii"]}
    sock = None
    try:
        sock = socket.create_connection((ip, port), timeout=CONNECT_TIMEOUT_SECONDS)
        sock.settimeout(BANNER_READ_TIMEOUT_SECONDS)
        if payload:
            sock.sendall(payload)
        try:
            raw = sock.recv(4096)
        except socket.timeout:
            raw = b""
        rx = _hex_and_ascii(raw)
        result["rx_bytes"] = len(raw)
        result["rx_hex"] = rx["hex"]
        result["rx_ascii"] = rx["ascii"]
    except OSError as exc:
        result["error"] = str(exc)
    finally:
        if sock is not None:
            try:
                sock.close()
            except OSError:
                pass
    return result


def investigate_unknown_tcp_service(ip: str, port: int) -> dict[str, Any]:
    """Deeper, still entirely passive/generic investigation of a port whose protocol is unidentified.

    Run automatically (see :func:`diagnose`) for any open port that
    turned out to speak neither HTTP nor WebSocket nor TLS -- exactly
    the situation port 5005 was found in on the real device (open, but
    silent to every HTTP path tried and to a WebSocket upgrade). Two
    steps, both well short of "sending a command":

    1. :func:`_extended_passive_listen` -- wait longer than the initial
       banner read, in case the service greets slowly or in multiple
       chunks.
    2. One generic, protocol-agnostic "nudge" (``\\r\\n\\r\\n``, the same
       probe nmap's own generic service-detection uses) to see whether
       the service reacts to *any* client input at all, without
       guessing what that input should mean to it.

    Every byte sent and received is recorded in both hex and ASCII so a
    human (or a later, evidence-based connector) can judge what -- if
    anything -- it reveals about the framing/protocol in use.
    """
    return {
        "extended_passive_listen": _extended_passive_listen(ip, port),
        "generic_probes": [
            _try_generic_probe(ip, port, "generic_lines_crlf", b"\r\n\r\n"),
        ],
    }


def diagnose(ip: str) -> dict[str, Any]:
    report: dict[str, Any] = {
        "tool": "deli_es172_diagnose.py",
        "tool_version": "1.2",
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

        if any_http:
            print("        investigating HTTP development/test interface (GET-only)...")
            entry["dev_interface"] = investigate_http_dev_interface(ip, port)
            dev = entry["dev_interface"]
            if not dev.get("error"):
                print(
                    f"        -> {len(dev.get('links', []))} links, "
                    f"{len(dev.get('script_srcs', []))} scripts, "
                    f"{len(dev.get('fetched_resources', []))} same-origin resources fetched"
                )

        # Attempted on every open port, not just 443 -- a proprietary
        # service (like port 5005) could just as well be TLS-wrapped on
        # a non-standard port; this is a standard handshake only, no
        # data exchanged beyond the negotiation itself.
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

        protocol_identified = (
            any_http
            or entry.get("websocket_probe", {}).get("is_101_switching_protocols")
            or entry["tls"].get("tls_handshake_ok")
        )
        if not protocol_identified:
            print("        protocol not yet identified -- extended passive listen + generic probe ...")
            entry["unknown_protocol_investigation"] = investigate_unknown_tcp_service(ip, port)
            unk = entry["unknown_protocol_investigation"]
            print(
                f"        -> passive listen: {unk['extended_passive_listen'].get('total_bytes', 0)} bytes captured; "
                f"{len(unk['generic_probes'])} generic probe(s) sent"
            )

        ports_report.append(entry)

    report["ports"] = ports_report

    print("[3/3] Done.")
    return report


def _render_dev_interface_txt(dev: dict[str, Any]) -> list[str]:
    """Human-readable summary of an :func:`investigate_http_dev_interface` result.

    Counts and short excerpts only -- the full HTML/JS content and
    every detail always remain in the ``.json`` report; this is meant
    to be skimmable.
    """
    lines = ["  --- HTTP development/test interface investigation (GET-only) ---"]
    if dev.get("error"):
        lines.append(f"    error: {dev['error']}")
        return lines

    root = dev.get("root_page", {})
    lines.append(f"    root page status: {root.get('status_line')}")
    for header in root.get("headers", []):
        lines.append(f"    header: {header}")
    lines.append(f"    links found: {len(dev.get('links', []))}")
    lines.append(f"    forms found: {len(dev.get('forms', []))}")
    lines.append(f"    buttons found: {len(dev.get('buttons', []))}")
    lines.append(f"    script files referenced: {len(dev.get('script_srcs', []))}")
    lines.append(f"    stylesheets/resources referenced: {len(dev.get('stylesheets', []))}")
    lines.append(f"    inline <script> blocks: {len(dev.get('inline_scripts', []))}")
    lines.append(f"    same-origin resources fetched: {len(dev.get('fetched_resources', []))}")
    lines.append(f"    mentions port 5005 anywhere: {dev.get('mentions_port_5005')}")

    html_findings = dev.get("html_findings", {})
    inline_findings = dev.get("inline_script_findings", {})
    if html_findings.get("mentions_swagger_or_openapi") or inline_findings.get(
        "mentions_swagger_or_openapi"
    ):
        lines.append("    mentions Swagger/OpenAPI: True")

    endpoint_strings = sorted(
        set(html_findings.get("endpoint_like_strings", []))
        | set(inline_findings.get("endpoint_like_strings", []))
    )
    if endpoint_strings:
        lines.append(f"    endpoint-like strings on the page: {', '.join(endpoint_strings[:30])}")

    if inline_findings.get("fetch_calls"):
        lines.append(f"    fetch() calls in inline scripts: {', '.join(inline_findings['fetch_calls'][:20])}")
    if inline_findings.get("xhr_calls"):
        xhr_summary = ", ".join(
            f"{c['method']} {c['url']}" for c in inline_findings["xhr_calls"][:20]
        )
        lines.append(f"    XMLHttpRequest calls in inline scripts: {xhr_summary}")
    if inline_findings.get("websocket_urls"):
        lines.append(f"    WebSocket URLs in inline scripts: {', '.join(inline_findings['websocket_urls'][:20])}")

    all_auth_hints = list(html_findings.get("auth_hints", [])) + list(inline_findings.get("auth_hints", []))
    for resource in dev.get("fetched_resources", []):
        findings = resource.get("findings", {})
        all_auth_hints.extend(findings.get("auth_hints", []))
    if all_auth_hints:
        lines.append("    possible authentication/API-key clues (verbatim lines, review manually):")
        for hint in all_auth_hints[:20]:
            lines.append(f"      {hint}")

    for resource in dev.get("fetched_resources", []):
        findings = resource.get("findings", {})
        lines.append(
            f"    resource {resource.get('path')} (referenced as {resource.get('referenced_as')}): "
            f"status={resource.get('status_line')}"
        )
        if findings.get("fetch_calls"):
            lines.append(f"      fetch() calls: {', '.join(findings['fetch_calls'][:20])}")
        if findings.get("xhr_calls"):
            xhr_summary = ", ".join(f"{c['method']} {c['url']}" for c in findings["xhr_calls"][:20])
            lines.append(f"      XMLHttpRequest calls: {xhr_summary}")
        if findings.get("websocket_urls"):
            lines.append(f"      WebSocket URLs: {', '.join(findings['websocket_urls'][:20])}")
        if findings.get("endpoint_like_strings"):
            lines.append(f"      endpoint-like strings: {', '.join(findings['endpoint_like_strings'][:30])}")

    for link in dev.get("followed_links", []):
        lines.append(f"    link {link.get('path')} (href=\"{link.get('referenced_as')}\"): status={link.get('status_line')}")
        if link.get("error"):
            lines.append(f"      error: {link['error']}")
        findings = link.get("findings", {})
        if findings.get("endpoint_like_strings"):
            lines.append(f"      endpoint-like strings: {', '.join(findings['endpoint_like_strings'][:30])}")

    lines.append("    NOTE: full HTML, full JS/CSS content, and every raw header are in the .json report.")
    return lines


def _render_unknown_protocol_txt(unk: dict[str, Any]) -> list[str]:
    """Human-readable summary of an :func:`investigate_unknown_tcp_service` result.

    Full hex/ASCII dumps always remain in the ``.json`` report; this
    surfaces just enough to tell at a glance whether anything came back
    at all.
    """
    lines = ["  --- unidentified-protocol investigation (passive listen + one generic probe) ---"]
    listen = unk.get("extended_passive_listen", {})
    if listen.get("error"):
        lines.append(f"    passive listen error: {listen['error']}")
    else:
        lines.append(f"    passive listen: {len(listen.get('reads', []))} read(s), {listen.get('total_bytes', 0)} bytes total")
        for read in listen.get("reads", []):
            lines.append(f"      +{read['elapsed_ms']} ms: {read['hex']}")
    for probe in unk.get("generic_probes", []):
        lines.append(f"    probe '{probe['name']}' sent {probe['tx_bytes']} bytes ({probe['tx_hex']})")
        if probe.get("error"):
            lines.append(f"      error: {probe['error']}")
        else:
            lines.append(f"      response: {probe.get('rx_bytes', 0)} bytes ({probe.get('rx_hex', '')})")
    lines.append("    NOTE: full hex/ASCII detail for every byte is in the .json report.")
    return lines


def _write_reports(report: dict[str, Any], output_dir: Path | None = None) -> tuple[Path, Path]:
    """Write the JSON + TXT reports, returning both paths.

    Args:
        report: The dict returned by :func:`diagnose`.
        output_dir: Where to write the reports. Defaults to a
            ``deli_diagnostic_output`` folder next to this script (the
            CLI usage) -- pass an explicit, guaranteed-writable
            directory (e.g. the running application's per-user data
            directory) when calling this from inside a frozen build,
            since a onefile build's own directory is a temporary
            extraction discarded after every run.
    """
    if output_dir is None:
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
        if "dev_interface" in entry:
            lines.extend(_render_dev_interface_txt(entry["dev_interface"]))
        if "unknown_protocol_investigation" in entry:
            lines.extend(_render_unknown_protocol_txt(entry["unknown_protocol_investigation"]))
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
