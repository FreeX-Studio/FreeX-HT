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
"""
freexht-config: applies the system settings of freexht.conf at every boot, as root, before the
network starts: hostname, Wi-Fi, Wi-Fi country, time zone and an SSH key.

The Wi-Fi password is removed from the file on the boot partition once it is stored on the box
(NetworkManager, /etc/NetworkManager/system-connections, readable by root only).
"""

import logging
import os
import re
import subprocess
import sys
import uuid
from typing import Dict, Optional

from . import config as conf

log = logging.getLogger("freexht.config")

STATE_DIR = "/var/lib/freexht"
NM_FILE = "/etc/NetworkManager/system-connections/freexht-wifi.nmconnection"


def write(path: str, text: str, mode: int = 0o644):
    tmp = path + ".freexht-tmp"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, mode)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(text)
    os.chmod(tmp, mode)
    os.replace(tmp, path)


def read(path: str) -> str:
    try:
        with open(path, encoding="utf-8") as f:
            return f.read()
    except OSError:
        return ""


# ---------------------------------------------------------------------------------- hostname

def valid_hostname(name: str) -> bool:
    return bool(re.fullmatch(r"[A-Za-z0-9]([A-Za-z0-9-]{0,61}[A-Za-z0-9])?", name))


def apply_hostname(name: str, root: str = "/") -> bool:
    if not name:
        return False
    if not valid_hostname(name):
        log.warning("hostname %r is not valid (letters, digits and '-')", name)
        return False
    hostname_file = os.path.join(root, "etc/hostname")
    if read(hostname_file).strip() == name:
        return False
    write(hostname_file, name + "\n")
    hosts_file = os.path.join(root, "etc/hosts")
    hosts = read(hosts_file)
    line = "127.0.1.1\t%s" % name
    if re.search(r"^127\.0\.1\.1\s.*$", hosts, re.M):
        hosts = re.sub(r"^127\.0\.1\.1\s.*$", line, hosts, flags=re.M)
    else:
        hosts = hosts.rstrip("\n") + "\n" + line + "\n"
    write(hosts_file, hosts)
    if root == "/":
        subprocess.run(["hostname", name], check=False)
    log.info("hostname: %s", name)
    return True


# ---------------------------------------------------------------------------------- Wi-Fi

def nm_keyfile(ssid: str, password: str, uid: Optional[str] = None) -> str:
    def esc(v: str) -> str:
        # GKeyFile: backslash escapes; a leading space would be lost.
        v = v.replace("\\", "\\\\").replace("\n", "\\n")
        return "\\s" + v[1:] if v.startswith(" ") else v

    lines = ["[connection]", "id=freexht-wifi", "uuid=" + (uid or str(uuid.uuid4())), "type=wifi",
             "autoconnect=true", "", "[wifi]", "mode=infrastructure", "ssid=" + esc(ssid), ""]
    if password:
        lines += ["[wifi-security]", "key-mgmt=wpa-psk", "psk=" + esc(password), ""]
    lines += ["[ipv4]", "method=auto", "", "[ipv6]", "addr-gen-mode=default", "method=auto", ""]
    return "\n".join(lines)


def blank_password(path: str) -> None:
    """wifi_password=… → wifi_password= (stored on the box now)."""
    text = read(path)
    new = re.sub(r"^(\s*wifi[_-]password\s*=).*$", r"\1", text, flags=re.M | re.I)
    if new != text:
        with open(path, "w", encoding="utf-8") as f:  # FAT: no rename-over, write in place
            f.write(new)


def apply_wifi(cfg: Dict[str, str], boot_path: Optional[str], root: str = "/") -> bool:
    ssid, password = cfg.get("wifi_ssid", ""), cfg.get("wifi_password", "")
    if not ssid:
        return False
    state = os.path.join(root, STATE_DIR.lstrip("/"), "wifi-ssid")
    applied = read(state).strip()
    # A password is new information; without one, only a different network (an open one) is.
    if not password and applied == ssid:
        return False
    nm = os.path.join(root, NM_FILE.lstrip("/"))
    os.makedirs(os.path.dirname(nm), exist_ok=True)
    old_uuid = re.search(r"^uuid=(\S+)", read(nm), re.M)
    write(nm, nm_keyfile(ssid, password, old_uuid.group(1) if old_uuid else None), 0o600)
    os.makedirs(os.path.dirname(state), exist_ok=True)
    write(state, ssid + "\n")
    if password and boot_path:
        try:
            blank_password(boot_path)
        except OSError as e:
            log.warning("could not remove the Wi-Fi password from %s: %s", boot_path, e)
    log.info("Wi-Fi: %s", ssid)
    return True


def apply_country(country: str, root: str = "/") -> bool:
    country = (country or "").strip().upper()
    if not re.fullmatch(r"[A-Z]{2}", country):
        return False
    path = os.path.join(root, "etc/modprobe.d/freexht-wifi-country.conf")
    text = "# From freexht.conf (wifi_country)\noptions cfg80211 ieee80211_regdom=%s\n" % country
    if read(path) != text:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        write(path, text)
    if root == "/":
        subprocess.run(["iw", "reg", "set", country], check=False, capture_output=True)
    return True


# ---------------------------------------------------------------------------------- time, SSH

def apply_timezone(tz: str, root: str = "/") -> bool:
    if not tz or ".." in tz:
        return False
    zone = os.path.join(root, "usr/share/zoneinfo", tz)
    if not os.path.isfile(zone):
        log.warning("unknown time zone %r", tz)
        return False
    link = os.path.join(root, "etc/localtime")
    target = "/usr/share/zoneinfo/" + tz
    if os.path.islink(link) and os.readlink(link) == target:
        return False
    tmp = link + ".freexht-tmp"
    if os.path.lexists(tmp):
        os.unlink(tmp)
    os.symlink(target, tmp)
    os.replace(tmp, link)
    write(os.path.join(root, "etc/timezone"), tz + "\n")
    log.info("time zone: %s", tz)
    return True


def apply_ssh_key(key: str, root: str = "/") -> bool:
    """An SSH key for root; SSH is switched on with it (off without one)."""
    key = (key or "").strip()
    if not key:
        return False
    if not re.match(r"^(ssh-|ecdsa-|sk-)", key):
        log.warning("ssh_authorized_key does not look like a public key")
        return False
    d = os.path.join(root, "root/.ssh")
    os.makedirs(d, mode=0o700, exist_ok=True)
    path = os.path.join(d, "authorized_keys")
    keys = read(path)
    if key not in keys.splitlines():
        write(path, keys.rstrip("\n") + ("\n" if keys.strip() else "") + key + "\n", 0o600)
    if root == "/":
        subprocess.run(["systemctl", "enable", "ssh.service"], check=False, capture_output=True)
        subprocess.run(["systemctl", "start", "--no-block", "ssh.service"], check=False, capture_output=True)
    return True


def main(argv=None):
    logging.basicConfig(level=logging.INFO, format="%(name)s: %(message)s")
    cfg = conf.load()
    boot = conf.boot_config_path()
    for step, fn in (("hostname", lambda: apply_hostname(cfg.get("hostname", ""))),
                     ("wifi_country", lambda: apply_country(cfg.get("wifi_country", ""))),
                     ("wifi", lambda: apply_wifi(cfg, boot)),
                     ("timezone", lambda: apply_timezone(cfg.get("timezone", ""))),
                     ("ssh", lambda: apply_ssh_key(cfg.get("ssh_authorized_key", "")))):
        try:
            fn()
        except Exception as e:  # noqa: BLE001 - one bad setting must not stop the others
            log.error("%s: %s", step, e)
    return 0


if __name__ == "__main__":
    sys.exit(main())
