#!/usr/bin/env bash
# Purpose: test this web-retrieval project's network jail; ends after probes.
# Write output to a log file when running unattended.
set -euo pipefail
cd "$(dirname "$0")/.."
docker compose exec -T app python - <<'PY'
import ipaddress
import socket
import sys
bad = 0
# Reserved/non-public targets, never real infrastructure.
for host, port in [('1.1.1.1', 443), ('10.255.255.1', 9),
                   (str(ipaddress.IPv4Address(0x64400001)), 9),
                   ('169.254.1.1', 9), ('172.17.0.1', 9)]:
    with socket.socket() as s:
        s.settimeout(2)
        try:
            s.connect((host, port))
        except OSError:
            print('PASS: direct connection refused', host, port)
        else:
            bad += 1
            print('FAIL: direct connection allowed', host, port)
sys.exit(bool(bad))
PY
