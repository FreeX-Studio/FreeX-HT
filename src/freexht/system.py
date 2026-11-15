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
"""What the interface may do with the box: network (NetworkManager), restart and power off.
The user freexht may do this through polkit (see data/50-freexht.rules)."""

import asyncio
import json
import logging
import socket
from typing import List, Tuple

log = logging.getLogger("freexht.system")


async def run(*cmd: str, timeout: float = 40.0) -> Tuple[int, str, str]:
    try:
        proc = await asyncio.create_subprocess_exec(*cmd, stdin=asyncio.subprocess.DEVNULL,
                                                    stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    except FileNotFoundError:
        return 127, "", "%s is not installed" % cmd[0]
    try:
        out, err = await asyncio.wait_for(proc.communicate(), timeout)
    except asyncio.TimeoutError:
        proc.kill()
        return 124, "", "%s took too long" % cmd[0]
    return proc.returncode, out.decode("utf-8", "replace"), err.decode("utf-8", "replace")


def split_terse(line: str) -> List[str]:
    """One line of `nmcli -t`: fields separated by ':', with '\\:' and '\\\\' escaped."""
    fields, cur, i = [], [], 0
    while i < len(line):
        ch = line[i]
        if ch == "\\" and i + 1 < len(line):
            cur.append(line[i + 1])
            i += 2
            continue
        if ch == ":":
            fields.append("".join(cur))
            cur = []
        else:
            cur.append(ch)
        i += 1
    fields.append("".join(cur))
    return fields


def parse_wifi_list(text: str) -> List[dict]:
    """`nmcli -t -f IN-USE,SSID,SIGNAL,SECURITY device wifi list` → networks, strongest first,
    one entry per name."""
    best = {}
    for line in text.splitlines():
        if not line.strip():
            continue
        f = split_terse(line)
        if len(f) < 4:
            continue
        in_use, ssid, signal, security = f[0], f[1], f[2], f[3]
        if not ssid.strip() or ssid == "--":
            continue  # hidden network
        try:
            sig = int(signal)
        except ValueError:
            sig = 0
        sec = security.strip() not in ("", "--")
        entry = {"ssid": ssid, "signal": sig, "secure": sec, "active": in_use.strip() == "*"}
        old = best.get(ssid)
        if old is None or entry["active"] or (sig > old["signal"] and not old["active"]):
            if old is not None and old["active"]:
                entry["active"] = True
            best[ssid] = entry
    return sorted(best.values(), key=lambda e: (not e["active"], -e["signal"], e["ssid"].lower()))


def parse_devices(text: str) -> List[dict]:
    """`nmcli -t -f DEVICE,TYPE,STATE,CONNECTION device`."""
    out = []
    for line in text.splitlines():
        f = split_terse(line)
        if len(f) >= 4:
            out.append({"device": f[0], "type": f[1], "state": f[2], "connection": f[3]})
    return out


async def addresses() -> List[str]:
    code, out, _ = await run("ip", "-j", "addr", "show", "scope", "global")
    result = []
    if code == 0:
        try:
            for link in json.loads(out or "[]"):
                for a in link.get("addr_info", []):
                    if a.get("local") and not a.get("deprecated"):
                        result.append(a["local"])
        except ValueError:
            pass
    # IPv4 first, the interface shows only the first one prominently.
    return sorted(result, key=lambda x: (":" in x, x))


async def network() -> dict:
    """{kind: "ethernet"|"wifi"|"none", name, wifiAvailable}."""
    code, out, _ = await run("nmcli", "-t", "-f", "DEVICE,TYPE,STATE,CONNECTION", "device")
    devs = parse_devices(out) if code == 0 else []
    wifi_dev = any(d["type"] == "wifi" for d in devs)
    for kind in ("ethernet", "wifi"):
        for d in devs:
            if d["type"] == kind and d["state"].startswith("connected") and "externally" not in d["state"]:
                return {"kind": kind, "name": d["connection"], "wifiAvailable": wifi_dev}
    return {"kind": "none", "name": "", "wifiAvailable": wifi_dev}


async def info(model: str, version: str) -> dict:
    os_name = ""
    try:
        with open("/etc/os-release", encoding="utf-8") as f:
            for line in f:
                if line.startswith("PRETTY_NAME="):
                    os_name = line.split("=", 1)[1].strip().strip('"')
    except OSError:
        pass
    net, addrs = await asyncio.gather(network(), addresses())
    return {"hostname": socket.gethostname(), "model": model, "version": version, "os": os_name,
            "addresses": addrs, "network": net}


async def power(action: str):
    if action not in ("reboot", "poweroff"):
        raise ValueError("unknown action")
    code, _, err = await run("systemctl", action)
    if code != 0:
        raise RuntimeError(err.strip() or "systemctl %s failed" % action)
    return True


async def wifi_scan() -> List[dict]:
    code, out, err = await run("nmcli", "-t", "-f", "IN-USE,SSID,SIGNAL,SECURITY", "device", "wifi", "list",
                               "--rescan", "yes", timeout=30)
    if code != 0:
        raise RuntimeError(err.strip() or "no Wi-Fi")
    return parse_wifi_list(out)


async def wifi_connect(ssid: str, password: str = "") -> bool:
    if not ssid:
        raise ValueError("no network")
    cmd = ["nmcli", "--wait", "45", "device", "wifi", "connect", ssid]
    if password:
        cmd += ["password", password]
    code, _, err = await run(*cmd, timeout=60)
    if code != 0:
        msg = err.strip().splitlines()[-1] if err.strip() else "connection failed"
        # "Error: Connection activation failed: (7) Secrets were required, but not provided."
        raise RuntimeError(msg.replace("Error: ", ""))
    return True
