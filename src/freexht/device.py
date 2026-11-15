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
The hardware FreeXHT runs on and what follows from it: the codecs the server may send directly,
mpv's hardware decoding, the HDMI sound device and the screen mode.

Boards:
  rpi3  Raspberry Pi 3 / 3+ / Zero 2 W / CM3: H.264 in hardware (up to 1080p), no HEVC.
  rpi4  Raspberry Pi 4 / 400 / CM4: HEVC (also 10-bit, up to 4K) and H.264 (up to 1080p) in hardware.
  rpi5  Raspberry Pi 5 / 500 / CM5: HEVC in hardware; H.264, VP9 and the rest fast enough in software.
  x86   PCs: VA-API (Intel, AMD) or software; everything the server knows.
"""

import glob
import os
import re
import socket
from typing import Dict, List, Optional

from . import config as conf

ALL_AUDIO = ["aac", "mp3", "ac3", "eac3", "dts", "truehd", "flac", "alac", "opus", "vorbis", "mp2",
             "pcm_s16le", "pcm_s24le", "pcm_s16be"]
CONTAINERS = ["mp4", "mkv", "webm", "mpegts", "ts", "avi", "mov", "flv", "ogg"]

VIDEO = {
    "rpi3": ["h264", "mpeg4", "mpeg2video"],
    "rpi4": ["h264", "hevc", "mpeg4", "mpeg2video"],
    "rpi5": ["h264", "hevc", "vp9", "mpeg4", "mpeg2video", "vc1"],
    "x86": ["h264", "hevc", "vp9", "av1", "mpeg4", "mpeg2video", "vc1"],
    "other": ["h264", "hevc", "vp9", "mpeg4", "mpeg2video"],
}

#: mpv's --hwdec per board: tried in this order per file. "drm" is the V4L2 request API of the
#: Raspberry Pi's ffmpeg (HEVC), "v4l2m2m-copy" the H.264 block of the Pi 3 and 4.
HWDEC = {
    "rpi3": "v4l2m2m-copy",
    "rpi4": "drm,v4l2m2m-copy",
    "rpi5": "drm",
    "x86": "auto-safe",
    "other": "auto-safe",
}


def read(path: str) -> str:
    try:
        with open(path, "rb") as f:
            return f.read().replace(b"\0", b"").decode("utf-8", "replace").strip()
    except OSError:
        return ""


def model(root: str = "/") -> str:
    """'Raspberry Pi 4 Model B Rev 1.4', or the PC's maker and product name."""
    dt = read(os.path.join(root, "proc/device-tree/model")) or read(os.path.join(root, "sys/firmware/devicetree/base/model"))
    if dt:
        return dt
    vendor = read(os.path.join(root, "sys/class/dmi/id/sys_vendor"))
    product = read(os.path.join(root, "sys/class/dmi/id/product_name"))
    junk = {"", "to be filled by o.e.m.", "default string", "system product name", "system manufacturer"}
    parts = [p for p in (vendor, product) if p.lower() not in junk]
    if len(parts) == 2 and parts[1].lower().startswith(parts[0].lower()):
        parts = [parts[1]]
    return " ".join(parts) or "PC"


def board(model_name: str, machine: Optional[str] = None) -> str:
    m = model_name.lower()
    if "raspberry pi" in m:
        if re.search(r"raspberry pi (5|500)|compute module 5", m):
            return "rpi5"
        if re.search(r"raspberry pi (4|400)|compute module 4", m):
            return "rpi4"
        return "rpi3"
    machine = machine or os.uname().machine
    if machine in ("x86_64", "amd64", "i686"):
        return "x86"
    return "other"


def short_model(model_name: str) -> str:
    """'Raspberry Pi 4 Model B Rev 1.4' → 'Raspberry Pi 4 Model B'."""
    return re.sub(r"\s+Rev\s+[\w.]+$", "", model_name).strip()


def capabilities(board_name: str, cfg: Dict[str, str]) -> dict:
    """What mpv plays on this board, in the form the server's playback decision expects."""
    video = conf.items(cfg.get("video_codecs", "")) or list(VIDEO.get(board_name, VIDEO["other"]))
    mse_video = [c for c in ("h264", "hevc") if c in video] or ["h264"]
    caps = {
        "containers": list(CONTAINERS),
        "video": video,
        "audio": list(ALL_AUDIO),
        # Conversions of the server (HLS): mpv plays them like a file.
        "mseVideo": mse_video,
        "mseAudio": ["aac", "mp3", "ac3", "eac3", "flac", "opus"],
    }
    main10 = cfg.get("hevc_main10", "")
    if main10:
        caps["hevcMain10"] = caps["mseHevcMain10"] = conf.flag(main10, True)
    return caps


def hwdec(board_name: str, cfg: Dict[str, str]) -> str:
    return cfg.get("hwdec") or HWDEC.get(board_name, "auto-safe")


def device_name(cfg: Dict[str, str], model_name: str) -> str:
    """The name in the server's activity view."""
    if cfg.get("device_name"):
        return cfg["device_name"]
    host = socket.gethostname()
    if host and host not in ("localhost", "freexht"):
        return "FreeXHT " + host
    return "FreeXHT (" + short_model(model_name) + ")"


# ---------------------------------------------------------------------------------- sound


def alsa_cards(root: str = "/") -> List[dict]:
    """[{index, id, name}] from /proc/asound/cards."""
    cards = []
    text = read(os.path.join(root, "proc/asound/cards"))
    for m in re.finditer(r"^\s*(\d+)\s+\[(\S+)\s*\]:\s*(.*)$", text, re.M):
        cards.append({"index": int(m.group(1)), "id": m.group(2), "name": m.group(3)})
    return cards


def connected_hdmi_device(card_index: int, root: str = "/") -> Optional[int]:
    """The HDMI device (DEV=) of an HDA card with a screen connected, from the ELD files."""
    elds = sorted(glob.glob(os.path.join(root, "proc/asound/card%d/eld#*" % card_index)))
    for n, path in enumerate(elds):
        if re.search(r"monitor_present\s+1", read(path)):
            return n
    return None


def audio_device(cfg: Dict[str, str], board_name: str, root: str = "/") -> str:
    """mpv's --audio-device: the HDMI output the TV is on. 'auto' if none is found."""
    if cfg.get("audio_device"):
        return cfg["audio_device"]
    cards = alsa_cards(root)
    # Raspberry Pi: vc4hdmi0 is the HDMI port next to the power socket (Pi 3: just vc4hdmi).
    for c in cards:
        if c["id"].lower().startswith("vc4hdmi"):
            return "alsa/hdmi:CARD=%s,DEV=0" % c["id"]
    # PCs: the HDMI/DisplayPort output with a screen (Intel/AMD/NVIDIA HDA).
    for c in cards:
        dev = connected_hdmi_device(c["index"], root)
        if dev is not None:
            return "alsa/hdmi:CARD=%s,DEV=%d" % (c["id"], dev)
    return "auto"


# ---------------------------------------------------------------------------------- screen


def display_mode(cfg: Dict[str, str], board_name: str) -> str:
    """'auto' (the TV's preferred mode) or WIDTHxHEIGHT[@RATE]. The Raspberry Pi runs in 1080p by
    default (4K costs the Pi 4 too much for the web page on top), the Pi 3 in 720p: its GPU
    cannot draw video and the transparent page on top in 1080p fast enough."""
    mode = (cfg.get("display_mode") or "").strip().lower()
    aliases = {"1080p": "1920x1080", "720p": "1280x720", "2160p": "3840x2160", "4k": "3840x2160", "uhd": "3840x2160"}
    mode = aliases.get(mode, mode)
    if mode:
        return mode
    if board_name == "rpi3":
        return "1280x720"
    return "1920x1080" if board_name.startswith("rpi") else "auto"


def lite_ui(cfg: Dict[str, str], board_name: str) -> bool:
    """The interface without transitions, animations and shadows (lite_ui); on by default on the Pi 3."""
    return conf.flag(cfg.get("lite_ui", ""), board_name == "rpi3")


def pick_mode(modes: List[dict], wanted: str) -> Optional[str]:
    """The sway mode string for WIDTHxHEIGHT[@RATE] out of an output's modes, or None.
    Without a rate the best one up to 60 Hz is taken."""
    m = re.match(r"^(\d+)x(\d+)(?:@(\d+(?:\.\d+)?))?", wanted)
    if not m:
        return None
    w, h = int(m.group(1)), int(m.group(2))
    rate = float(m.group(3)) if m.group(3) else None
    fits = [x for x in modes if x.get("width") == w and x.get("height") == h]
    if not fits:
        return None
    if rate is not None:
        best = min(fits, key=lambda x: abs(x.get("refresh", 0) / 1000.0 - rate))
    else:
        under = [x for x in fits if x.get("refresh", 0) <= 60500]
        best = max(under or fits, key=lambda x: x.get("refresh", 0))
    return "%dx%d@%.3fHz" % (w, h, best.get("refresh", 60000) / 1000.0)
