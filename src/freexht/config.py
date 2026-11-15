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
"""freexht.conf: simple `key = value` settings, editable on the boot partition from any computer."""

import os
from typing import Dict, Iterable, Optional

#: Searched in this order; a later file overrides an earlier one. The boot partition comes last,
#: so what the user writes there wins over the defaults of the image.
CONFIG_PATHS = (
    "/etc/freexht/freexht.conf",
    "/boot/efi/freexht.conf",
    "/boot/firmware/freexht.conf",
)

DEFAULTS: Dict[str, str] = {
    # Network and system (applied by freexht-config at boot, as root)
    "hostname": "",
    "wifi_ssid": "",
    "wifi_password": "",
    "wifi_country": "",
    "timezone": "",
    "ssh_authorized_key": "",
    # Interface
    "server": "",
    "device_name": "",
    "keyboard_layout": "",
    "cec_name": "",
    # Display and playback
    "display_mode": "",
    "audio_device": "",
    "audio_passthrough": "",
    "hwdec": "",
    "video_output": "",
    "video_codecs": "",
    "hevc_main10": "",
}


def parse(text: str) -> Dict[str, str]:
    """`key = value` lines; `#` starts a comment line; quotes around a value are removed."""
    out: Dict[str, str] = {}
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or line.startswith(";") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip().lower().replace("-", "_")
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        out[key] = value
    return out


def load(paths: Optional[Iterable[str]] = None) -> Dict[str, str]:
    cfg = dict(DEFAULTS)
    for path in paths if paths is not None else CONFIG_PATHS:
        try:
            with open(path, encoding="utf-8-sig") as f:
                cfg.update(parse(f.read()))
        except (OSError, UnicodeDecodeError):
            continue
    return cfg


def boot_config_path() -> Optional[str]:
    """The freexht.conf on the boot partition (FAT), if the image has one."""
    for path in ("/boot/firmware/freexht.conf", "/boot/efi/freexht.conf"):
        if os.path.exists(path):
            return path
    return None


def boot_file(name: str) -> Optional[str]:
    """A file the user may put next to freexht.conf (e.g. mpv.conf)."""
    for d in ("/boot/firmware", "/boot/efi", "/etc/freexht"):
        path = os.path.join(d, name)
        if os.path.isfile(path):
            return path
    return None


def flag(value: str, default: bool = False) -> bool:
    v = (value or "").strip().lower()
    if v in ("1", "yes", "true", "on"):
        return True
    if v in ("0", "no", "false", "off"):
        return False
    return default


def items(value: str):
    """A comma or space separated list."""
    return [v for v in (value or "").replace(",", " ").split() if v]
