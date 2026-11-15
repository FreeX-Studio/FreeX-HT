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
freexht-cec: makes the box a playback device on HDMI-CEC, so the TV's remote control works.

Runs as root for each CEC adapter (/dev/cecN, started by udev, see data/70-freexht-cec.rules).
It claims a logical address as "FreeXHT" with remote control passthrough: the kernel then turns
the remote's buttons into key presses of an input device (keymap data/freexht-cec.toml), which
reach the interface like a keyboard. Then it switches the TV to the box (Image View On, Active
Source) with cec-ctl.
"""

import fcntl
import logging
import os
import struct
import subprocess
import sys
import time

log = logging.getLogger("freexht.cec")

# linux/cec.h
CEC_ADAP_G_PHYS_ADDR = 0x80026101
CEC_ADAP_S_LOG_ADDRS = 0xC05C6104  # _IOWR('a', 4, struct cec_log_addrs), 92 bytes
CEC_PHYS_ADDR_INVALID = 0xFFFF
CEC_LOG_ADDRS_FL_ALLOW_UNREG_FALLBACK = 1 << 0
CEC_LOG_ADDRS_FL_ALLOW_RC_PASSTHRU = 1 << 1
CEC_OP_CEC_VERSION_1_4 = 5
CEC_OP_PRIM_DEVTYPE_PLAYBACK = 4
CEC_LOG_ADDR_TYPE_PLAYBACK = 3
CEC_OP_ALL_DEVTYPE_PLAYBACK = 0x10
CEC_VENDOR_ID_NONE = 0xFFFFFFFF
LOG_ADDRS_SIZE = 92


def log_addrs(osd_name: str = "FreeXHT", count: int = 1) -> bytes:
    """struct cec_log_addrs for `count` playback devices (0 releases the addresses)."""
    name = osd_name.encode("ascii", "replace")[:14].ljust(15, b"\0")
    flags = CEC_LOG_ADDRS_FL_ALLOW_UNREG_FALLBACK | CEC_LOG_ADDRS_FL_ALLOW_RC_PASSTHRU
    data = struct.pack(
        "=4sHBBII15s4s4s4s48s",
        b"\0" * 4,  # log_addr (filled by the kernel)
        0,  # log_addr_mask
        CEC_OP_CEC_VERSION_1_4,
        count,
        CEC_VENDOR_ID_NONE,
        flags if count else 0,
        name if count else b"\0" * 15,
        bytes([CEC_OP_PRIM_DEVTYPE_PLAYBACK, 0, 0, 0]),
        bytes([CEC_LOG_ADDR_TYPE_PLAYBACK, 0, 0, 0]),
        bytes([CEC_OP_ALL_DEVTYPE_PLAYBACK, 0, 0, 0]),
        b"\0" * 48,
    )
    return data.ljust(LOG_ADDRS_SIZE, b"\0")


def phys_addr(fd: int) -> int:
    buf = bytearray(2)
    fcntl.ioctl(fd, CEC_ADAP_G_PHYS_ADDR, buf)
    return struct.unpack("=H", bytes(buf))[0]


def fmt_phys(pa: int) -> str:
    return "%d.%d.%d.%d" % ((pa >> 12) & 0xF, (pa >> 8) & 0xF, (pa >> 4) & 0xF, pa & 0xF)


def configure(dev: str, osd_name: str) -> None:
    fd = os.open(dev, os.O_RDWR)
    try:
        # Release first: setting addresses on a configured adapter fails with EBUSY.
        fcntl.ioctl(fd, CEC_ADAP_S_LOG_ADDRS, bytearray(log_addrs(osd_name, 0)))
        fcntl.ioctl(fd, CEC_ADAP_S_LOG_ADDRS, bytearray(log_addrs(osd_name, 1)))
        log.info("%s: playback device %r with remote control passthrough", dev, osd_name)
        # Switch the TV to us once it has told us where we are (needs the TV's EDID).
        for _ in range(30):
            pa = phys_addr(fd)
            if pa != CEC_PHYS_ADDR_INVALID:
                break
            time.sleep(1)
        else:
            log.info("%s: no physical address (TV off or not connected)", dev)
            return
    finally:
        os.close(fd)
    if os.path.exists("/usr/bin/cec-ctl"):
        subprocess.run(["cec-ctl", "-d", dev, "--to", "0", "--image-view-on"], capture_output=True, timeout=10)
        subprocess.run(["cec-ctl", "-d", dev, "--to", "15", "--active-source", "phys-addr=" + fmt_phys(pa)],
                       capture_output=True, timeout=10)
        log.info("%s: active source at %s", dev, fmt_phys(pa))


def main(argv=None):
    logging.basicConfig(level=logging.INFO, format="%(name)s: %(message)s")
    argv = sys.argv[1:] if argv is None else argv
    dev = argv[0] if argv else "/dev/cec0"
    if not dev.startswith("/dev/"):
        dev = "/dev/" + dev
    name = "FreeXHT"
    try:
        from . import config as conf
        name = conf.load().get("cec_name") or name
    except Exception:  # noqa: BLE001
        pass
    try:
        configure(dev, name)
    except OSError as e:
        log.error("%s: %s", dev, e)
        sys.exit(1)


if __name__ == "__main__":
    main()
