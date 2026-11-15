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
mpv as the player of the interface.

mpv runs the whole time as its own window (idle, black) underneath the transparent interface,
started with an IPC socket. The page drives it like ExoPlayer in the Android app: open → "ready"
(paused at the start position) → play; while a file is open, a "time" event every half second;
"buffering", "state", "ended" and "error" as they come.
"""

import asyncio
import json
import logging
import os
from typing import Callable, Dict, List, Optional

log = logging.getLogger("freexht.mpv")

APP_ID = "freexht-video"


def mpv_conf(opts: Dict[str, str], include: Optional[str]) -> str:
    """The mpv.conf FreeXHT starts mpv with; a user's mpv.conf is included last and wins."""
    lines = ["# Written by FreeXHT at every start. Your own settings: mpv.conf next to freexht.conf."]
    for k, v in opts.items():
        lines.append("%s=%s" % (k, v) if v is not None else k)
    if include:
        lines.append("include=%s" % include)
    return "\n".join(lines) + "\n"


def default_options(hwdec: str, audio_device: str, passthrough: str, vo: str, small_memory: bool, user_agent: str,
                    fast: bool = False) -> Dict[str, str]:
    opts = {}
    if fast:
        # Simple scaling, no dithering or HDR peak detection: far less work for a small GPU.
        opts["profile"] = "fast"
    opts.update({
        "vo": vo or "gpu",
        "gpu-context": "wayland",
        "hwdec": hwdec,
        "ao": "alsa",
        "audio-device": audio_device or "auto",
        # Subtitles, the seek bar and every other overlay come from the interface.
        "sid": "no",
        "sub-auto": "no",
        "osc": "no",
        "osd-level": "0",
        "osd-bar": "no",
        "input-default-bindings": "no",
        "input-vo-keyboard": "no",
        "input-cursor": "no",
        "cursor-autohide": "always",
        "load-scripts": "no",
        "ytdl": "no",
        "keep-open": "no",
        # The window is the whole screen; the picture keeps its aspect inside it.
        "keepaspect-window": "no",
        "idle": "yes",
        "force-window": "yes",
        "wayland-app-id": APP_ID,
        "title": "FreeXHT video",
        # The server converts on request: segments may take a while.
        "network-timeout": "75",
        "cache": "yes",
        "demuxer-max-bytes": "48MiB" if small_memory else "150MiB",
        "demuxer-max-back-bytes": "16MiB" if small_memory else "64MiB",
        "demuxer-readahead-secs": "20",
        "hls-bitrate": "max",
        "user-agent": user_agent,
        "volume": "100",
        "volume-max": "100",
    })
    if passthrough:
        opts["audio-spdif"] = passthrough
    return opts


class Mpv:
    """One mpv process with its JSON IPC; restarted when it dies."""

    def __init__(self, runtime_dir: str, options: Dict[str, str], include: Optional[str],
                 emit: Callable[[dict], None], on_window: Optional[Callable[[], None]] = None,
                 binary: str = "mpv"):
        self.dir = os.path.join(runtime_dir, "freexht-mpv")
        self.sock = os.path.join(self.dir, "ipc.sock")
        self.options = options
        self.include = include
        self.emit = emit
        self.on_window = on_window
        self.binary = binary
        self.proc: Optional[asyncio.subprocess.Process] = None
        self.reader: Optional[asyncio.StreamReader] = None
        self.writer: Optional[asyncio.StreamWriter] = None
        self.req = 0
        self.waiting: Dict[int, asyncio.Future] = {}
        self.closing = False
        self.reset_state()
        self.ticker: Optional[asyncio.Task] = None
        self.ready_event = asyncio.Event()

    def reset_state(self):
        self.entry = None  # playlist entry id of the file the page opened
        self.loaded = False
        self.ready_sent = False
        self.position = 0.0
        self.duration = 0.0
        self.paused = True
        self.buffering = False
        self.drops = {}

    # ---------------------------------------------------------------- process

    async def start(self):
        os.makedirs(self.dir, exist_ok=True)
        with open(os.path.join(self.dir, "mpv.conf"), "w", encoding="utf-8") as f:
            f.write(mpv_conf(self.options, self.include))
        try:
            os.unlink(self.sock)
        except FileNotFoundError:
            pass
        self.proc = await asyncio.create_subprocess_exec(
            self.binary, "--config-dir=" + self.dir, "--input-ipc-server=" + self.sock, "--no-terminal",
            stdin=asyncio.subprocess.DEVNULL)
        log.info("mpv started (pid %s)", self.proc.pid)
        for _ in range(100):
            if os.path.exists(self.sock):
                try:
                    self.reader, self.writer = await asyncio.open_unix_connection(self.sock)
                    break
                except OSError:
                    pass
            if self.proc.returncode is not None:
                raise RuntimeError("mpv exited with %s" % self.proc.returncode)
            await asyncio.sleep(0.1)
        else:
            raise RuntimeError("mpv did not open its IPC socket")
        asyncio.get_running_loop().create_task(self._read())
        for i, name in enumerate(("time-pos", "duration", "pause", "paused-for-cache", "seeking"), 1):
            await self.command(["observe_property", i, name])
        self.ready_event.set()
        if self.on_window:
            self.on_window()

    async def supervise(self):
        """Keeps mpv running: a crash ends the current video with an error, mpv comes back."""
        while not self.closing:
            try:
                await self.start()
                await self.proc.wait()
            except Exception as e:  # noqa: BLE001 - logged, mpv is started again
                log.error("mpv: %s", e)
            self.ready_event.clear()
            if self.closing:
                return
            was_open = self.loaded or self.entry is not None
            self._drop_connection()
            self.reset_state()
            if was_open:
                self.emit({"type": "error", "message": "The player stopped unexpectedly."})
            log.warning("mpv exited (%s), starting it again", self.proc.returncode if self.proc else "?")
            await asyncio.sleep(1)

    def _drop_connection(self):
        for fut in self.waiting.values():
            if not fut.done():
                fut.set_exception(ConnectionError("mpv is gone"))
        self.waiting.clear()
        if self.writer:
            try:
                self.writer.close()
            except Exception:  # noqa: BLE001
                pass
        self.reader = self.writer = None

    async def close(self):
        self.closing = True
        if self.ticker:
            self.ticker.cancel()
        if self.proc and self.proc.returncode is None:
            try:
                await asyncio.wait_for(self.command(["quit"]), 2)
            except Exception:  # noqa: BLE001
                pass
            try:
                await asyncio.wait_for(self.proc.wait(), 3)
            except asyncio.TimeoutError:
                self.proc.kill()

    # ---------------------------------------------------------------- IPC

    async def command(self, cmd: List, timeout: float = 10.0):
        if not self.writer:
            raise ConnectionError("mpv is not running")
        self.req += 1
        rid = self.req
        fut = asyncio.get_running_loop().create_future()
        self.waiting[rid] = fut
        self.writer.write((json.dumps({"command": cmd, "request_id": rid}) + "\n").encode())
        await self.writer.drain()
        try:
            return await asyncio.wait_for(fut, timeout)
        finally:
            self.waiting.pop(rid, None)

    async def _read(self):
        reader = self.reader
        while reader and not reader.at_eof():
            try:
                line = await reader.readline()
            except (ConnectionError, OSError):
                break
            if not line:
                break
            try:
                msg = json.loads(line)
            except ValueError:
                continue
            if "request_id" in msg and "event" not in msg:
                fut = self.waiting.get(msg["request_id"])
                if fut and not fut.done():
                    if msg.get("error") == "success":
                        fut.set_result(msg.get("data"))
                    else:
                        fut.set_exception(RuntimeError(msg.get("error", "error")))
                continue
            try:
                self.on_message(msg)
            except Exception:  # noqa: BLE001 - one odd event must not stop the reader
                log.exception("mpv event %s", msg)

    # ---------------------------------------------------------------- events → page

    def state(self, kind: str) -> dict:
        return {"type": kind, "position": int(self.position * 1000), "duration": int(self.duration * 1000),
                "playing": self.loaded and not self.paused}

    def on_message(self, msg: dict):
        ev = msg.get("event")
        if ev == "property-change":
            name, data = msg.get("name"), msg.get("data")
            if name == "time-pos" and data is not None:
                self.position = max(0.0, float(data))
            elif name == "duration" and data:
                self.duration = float(data)
            elif name == "pause" and data is not None:
                changed = bool(data) != self.paused
                self.paused = bool(data)
                if changed and self.loaded:
                    self.emit(self.state("state"))
            elif name in ("paused-for-cache", "seeking"):
                on = bool(data)
                if self.loaded and on != self.buffering:
                    self.buffering = on
                    self.emit({"type": "buffering", "on": on})
        elif ev == "start-file":
            if msg.get("playlist_entry_id") is not None:
                self.entry = msg["playlist_entry_id"]
        elif ev == "file-loaded":
            self.loaded = True
            # The start position was only for this file.
            asyncio.get_running_loop().create_task(self._quiet(["set_property", "start", "none"]))
        elif ev == "playback-restart":
            # First picture at the start position: ready, still paused until the page says play.
            if self.loaded and not self.ready_sent:
                self.ready_sent = True
                self.emit(self.state("ready"))
                self.emit({"type": "buffering", "on": False})
                asyncio.get_running_loop().create_task(self._log_playback())
            elif self.loaded:
                self.emit(self.state("time"))
        elif ev == "end-file":
            entry = msg.get("playlist_entry_id")
            if entry is not None and self.entry is not None and entry != self.entry:
                return  # the file before the current one (replaced)
            if self.loaded and self.drops:
                log.info("played to %ds: %s", int(self.position), self.drops)
            reason = msg.get("reason")
            was_loaded = self.loaded
            self.loaded = False
            if reason == "eof":
                if was_loaded:
                    self.emit(self.state("ended"))
            elif reason == "error":
                self.emit({"type": "error", "message": "mpv: " + str(msg.get("file_error") or "cannot play this file")})

    async def _get(self, name):
        try:
            return await self.command(["get_property", name], timeout=3)
        except Exception:  # noqa: BLE001 - unavailable now
            return None

    async def _log_playback(self):
        """What plays how, in the journal: the first thing to look at when a video stutters."""
        names = ("video-codec", "width", "height", "container-fps", "hwdec-current", "audio-codec-name",
                 "current-vo", "display-width", "display-height")
        v = dict(zip(names, [await self._get(n) for n in names]))
        log.info("playing %s %sx%s %.3g fps, decoding: %s, audio %s, output %s %sx%s",
                 v["video-codec"], v["width"], v["height"], v["container-fps"] or 0,
                 v["hwdec-current"] or "software", v["audio-codec-name"], v["current-vo"],
                 v["display-width"], v["display-height"])
        self.drops = {}
        while self.loaded and self.ready_sent:
            await asyncio.sleep(5)
            dec, vo = await self._get("decoder-frame-drop-count"), await self._get("frame-drop-count")
            self.drops = {"dropped by the decoder": dec or 0, "dropped by the output": vo or 0}

    async def _quiet(self, cmd):
        try:
            await self.command(cmd)
        except Exception:  # noqa: BLE001
            pass

    async def _tick(self):
        while True:
            await asyncio.sleep(0.5)
            if self.loaded and self.ready_sent:
                self.emit(self.state("time"))

    # ---------------------------------------------------------------- page → mpv

    async def open(self, url: str, start_ms: int = 0, hls: bool = False):
        await asyncio.wait_for(self.ready_event.wait(), 15)
        self.reset_state()
        if self.ticker is None or self.ticker.done():
            self.ticker = asyncio.get_running_loop().create_task(self._tick())
        self.position = max(0, start_ms) / 1000.0
        await self.command(["set_property", "pause", True])
        await self.command(["set_property", "start", "%.3f" % self.position if start_ms > 0 else "none"])
        data = await self.command(["loadfile", url, "replace"])
        if isinstance(data, dict) and data.get("playlist_entry_id") is not None:
            self.entry = data["playlist_entry_id"]

    async def play(self):
        await self.command(["set_property", "pause", False])

    async def pause(self):
        await self.command(["set_property", "pause", True])

    async def seek(self, ms: int):
        self.position = max(0, ms) / 1000.0
        await self.command(["seek", "%.3f" % self.position, "absolute"])

    async def stop(self):
        self.reset_state()
        await self.command(["stop"])
