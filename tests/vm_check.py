#!/usr/bin/env python3
#
# Copyright (c) 2026 MirraNET, Niklas Linz. All rights reserved.
#
# This file is part of the MirraNET project and is licensed under the
# GNU Lesser General Public License v3.0 (LGPLv3).
#
# You may use, distribute and modify this code under the terms
# of the LGPLv3 license. You should have received a copy of the
# license along with this file. If not, see <https://www.gnu.org/licenses/lgpl-3.0.html>
# or contact: niklas.linz@mirranet.de
#
"""Run inside a booted FreeXHT (tests/image-smoke.sh): the agent, its bridge and a WebSocket call."""
import base64, json, os, socket, urllib.request
b = urllib.request.urlopen("http://127.0.0.1:7650/freexht/bridge.js").read().decode()
info = json.loads(b.split("const INFO = ", 1)[1].split(";\n", 1)[0])
print("bridge:", info["platform"], "|", info["deviceName"], "|", info["board"], "| server:", info["server"], "| video:", info["caps"]["video"])
print("index injects bridge:", "freexht/bridge.js" in urllib.request.urlopen("http://127.0.0.1:7650/").read().decode())
s = socket.create_connection(("127.0.0.1", 7650))
key = base64.b64encode(os.urandom(16)).decode()
s.sendall(("GET /freexht/ws HTTP/1.1\r\nHost: x\r\nUpgrade: websocket\r\nConnection: Upgrade\r\nOrigin: http://127.0.0.1:7650\r\n"
           "Sec-WebSocket-Key: %s\r\nSec-WebSocket-Version: 13\r\n\r\n" % key).encode())
f = s.makefile("rb"); line = f.readline()
while f.readline() not in (b"\r\n", b""): pass
print("websocket:", line.strip().decode())
def call(i, method, args=None):
    p = json.dumps({"id": i, "call": method, "args": args or {}}).encode(); m = os.urandom(4)
    s.sendall(bytes([0x81, 0x80 | len(p)]) + m + bytes(c ^ m[j % 4] for j, c in enumerate(p)))
    while True:
        b1, b2 = f.read(2); n = b2 & 0x7F
        if n == 126: n = int.from_bytes(f.read(2), "big")
        msg = json.loads(f.read(n))
        if msg.get("id") == i: return msg
r = call(1, "info")
print("info:", r["result"]["hostname"], r["result"]["model"], r["result"]["network"], r["result"]["addresses"][:1])
assert info["platform"] == "freexht" and r["result"]["network"]["kind"] == "ethernet"
print("vm_check: ok")
