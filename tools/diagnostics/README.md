# DELI ES172 network diagnostic

A standalone, read-only field diagnostic for investigating the DELI
ES172 attendance device's local network protocol (currently unknown --
see the DELI ES172 integration investigation in the project history).
Used because `devices/zkteco_device.py`'s ZKTeco/pyzk protocol does
**not** apply to this device, and no connector should be written
against a guessed protocol.

## What it does

- Checks whether the device host is reachable (ping).
- Tests TCP connectivity on ports 5005, 4370, 7070, 80, 443, 8080.
- For every open port: passively captures any unsolicited banner,
  tries a handful of plain read-only HTTP GET requests on fresh
  connections, and (port 443 only) attempts a TLS handshake.
- For port 5005 specifically: additionally attempts one standard
  WebSocket upgrade handshake.
- Writes a timestamped `.json` (machine-readable) and `.txt`
  (human-readable) report into `deli_diagnostic_output/` (gitignored
  -- contains a customer's real network data, never committed).

## What it deliberately does NOT do

- Never sends the device's API Key, Device UID, or any credential --
  it doesn't even accept them as input.
- Never sends an enrollment, write, or configuration command.
- Never emulates or guesses a proprietary wire protocol; every probe
  is a generic, standards-based TCP/HTTP/WebSocket operation that is
  safe against *any* service, not specific to DELI.

## Running it

**Non-technical user:** double-click `Run_DELI_Diagnostic.bat`, press
Enter to accept the default IP (or type a different one), wait for it
to finish, then send back the two file paths it prints at the end.

**From a terminal:**

```
python deli_es172_diagnose.py [IP_ADDRESS]
```

`IP_ADDRESS` defaults to `192.168.1.28` if omitted.

## Tests

`tests/test_deli_diagnostic_tool.py` (in the main pytest suite) proves
the HTTP/WebSocket/banner detection logic against local throwaway test
servers -- there is no way to reach the real device from the
development/CI environment.
