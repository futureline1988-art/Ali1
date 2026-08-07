# DELI ES172 network diagnostic

A standalone, read-only field diagnostic for investigating the DELI
ES172 attendance device's local network protocol (still not fully
identified -- see the DELI ES172 integration investigation in the
project history). Used because `devices/zkteco_device.py`'s ZKTeco/pyzk
protocol does **not** apply to this device (confirmed: port 4370 is
closed on the real device, port 5005 is open but speaks neither HTTP
nor WebSocket), and no connector should be written against a guessed
protocol.

A real on-device run found the device serving an HTTP "Attendance &
Access machine development test interface" (Boost.Beast) on **port
80** -- a major, evidence-based lead this tool now investigates in
detail (see below), still strictly read-only.

## What it does

- Checks whether the device host is reachable (ping).
- Tests TCP connectivity on ports 5005, 4370, 7070, 80, 443, 8080.
- For every open port: passively captures any unsolicited banner,
  tries a handful of plain read-only HTTP GET requests on fresh
  connections, and (port 443 only) attempts a TLS handshake.
- For port 5005 specifically: additionally attempts one standard
  WebSocket upgrade handshake.
- **For any port that turns out to speak plain HTTP** (this is how
  port 80's development/test interface was found): fetches the full
  `/` page, parses its HTML for every link, form (action/method/input
  names), button, `<script>`/`<link>` reference, and inline script it
  contains, scans all of that (plus every same-origin JS/CSS file the
  page itself references -- and only those, never guessed or
  brute-forced) for `fetch()` calls, `XMLHttpRequest` calls, WebSocket
  URLs, endpoint-looking path strings, and possible
  authentication/API-key clues. Also checks whether the page mentions
  port 5005 anywhere.
- Writes a timestamped `.json` (machine-readable, includes the full
  HTML/JS content and all findings) and `.txt` (human-readable
  summary) report into `deli_diagnostic_output/` (gitignored -- contains
  a customer's real network data, never committed).

## What it deliberately does NOT do

- Never sends the device's API Key, Device UID, or any credential --
  it doesn't even accept them as input.
- Never sends an enrollment, write, or configuration command; every
  HTTP request this tool ever makes is a `GET`, never `POST`/`PUT`/
  `DELETE`.
- Never emulates or guesses a proprietary wire protocol; every probe
  is a generic, standards-based TCP/HTTP/WebSocket operation that is
  safe against *any* service, not specific to DELI.
- Never brute-forces a URL and never follows a cross-origin resource --
  the HTML development-interface investigation only ever fetches
  same-origin JS/CSS files the page's own markup already links to.

## Running it

**Non-technical user, from the installed Attendance Client (recommended,
no separate Python install needed):** open the app, go to the Devices
page, click **"تشخيص شبكة الجهاز"**, confirm/enter the IP, click "بدء
الفحص". This runs the exact same logic as the standalone script below,
bundled into the application itself.

**Standalone script (developers/CI, or checking a device before the
application is installed -- requires a separately installed Python 3):**
double-click `Run_DELI_Diagnostic.bat`, press Enter to accept the
default IP (or type a different one), wait for it to finish, then send
back the two file paths it prints at the end. Or from a terminal:

```
python deli_es172_diagnose.py [IP_ADDRESS]
```

`IP_ADDRESS` defaults to `192.168.1.8` if omitted.

## Tests

`tests/test_deli_diagnostic_tool.py` (in the main pytest suite) proves
the HTTP/WebSocket/banner detection logic against local throwaway test
servers -- there is no way to reach the real device from the
development/CI environment.
