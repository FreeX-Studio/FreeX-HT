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
freexht-agent: the session of the box, started by sway (data/sway.conf).

  1. sets the screen mode and the keyboard layout (swaymsg),
  2. starts mpv (window "freexht-video", at the bottom),
  3. serves the interface on 127.0.0.1:7650 and starts the browser (cog) on top of mpv,
  4. passes the page's commands to mpv and the system, and mpv's events back to the page,
  5. keeps mpv and the browser running.

If the agent itself ends, it ends sway too; systemd then starts everything again.
"""

import argparse
import asyncio
import json
import logging
import os
import shutil
import signal
import sys
from typing import Optional

from . import WEB_DIR, LIB_DIR, version
from . import config as conf
from . import device, system
from .mpv import Mpv, default_options
from .web import Client, WebServer

log = logging.getLogger("freexht")

BROWSER_APP_ID = "com.igalia.Cog"


async def swaymsg(*args: str) -> Optional[str]:
    if not shutil.which("swaymsg") or not os.environ.get("SWAYSOCK"):
        return None
    code, out, err = await system.run("swaymsg", *args, timeout=10)
    if code != 0:
        text = (err or out).strip()
        # No window of that kind (yet): nothing to report.
        level = logging.DEBUG if "No matching node" in text else logging.WARNING
        log.log(level, "swaymsg %s: %s", " ".join(args), " ".join(text.split()))
        return None
    return out


class Session:
    def __init__(self, args):
        self.args = args
        self.cfg = conf.load()
        self.model = device.model()
        self.board = device.board(self.model)
        self.version = version()
        self.web: Optional[WebServer] = None
        self.mpv: Optional[Mpv] = None
        self.browser: Optional[asyncio.subprocess.Process] = None
        self.stopping = asyncio.Event()
        log.info("FreeXHT %s on %s (%s)", self.version, self.model, self.board)

    # ---------------------------------------------------------------- bridge

    def bridge_info(self) -> dict:
        return {
            "platform": "freexht",
            "version": self.version,
            "model": device.short_model(self.model),
            "board": self.board,
            "deviceName": device.device_name(self.cfg, self.model),
            "caps": device.capabilities(self.board, self.cfg),
            "server": self.cfg.get("server", ""),
        }

    def bridge_js(self) -> str:
        path = os.path.join(LIB_DIR, "bridge.js")
        if not os.path.exists(path):
            path = os.path.join(os.path.dirname(__file__), "..", "..", "data", "bridge.js")
        with open(path, encoding="utf-8") as f:
            js = f.read()
        info = json.dumps(self.bridge_info(), ensure_ascii=False).replace("</", "<\\/")
        return js.replace("__FREEXHT_INFO__", info)

    def emit(self, event: dict):
        if self.web:
            self.web.broadcast({"event": event})

    async def on_message(self, client: Client, msg: dict):
        """{cmd, args} for the player (no answer), {id, call, args} for everything else."""
        cmd = msg.get("cmd")
        args = msg.get("args") or {}
        if cmd:
            await self.player(cmd, args)
            return None
        call = msg.get("call")
        if not call or "id" not in msg:
            return None
        if call == "info":
            result = await system.info(device.short_model(self.model), self.version)
        elif call == "power":
            result = await system.power(str(args.get("action", "")))
        elif call == "wifi.scan":
            result = await system.wifi_scan()
        elif call == "wifi.connect":
            result = await system.wifi_connect(str(args.get("ssid", "")), str(args.get("password", "")))
        elif call == "reload":
            result = True
            asyncio.get_running_loop().call_later(0.2, self.restart_browser)
        else:
            raise ValueError("unknown call " + call)
        return {"id": msg["id"], "result": result}

    async def player(self, cmd: str, args: dict):
        if not self.mpv:
            return
        try:
            if cmd == "open":
                await self.mpv.open(str(args["url"]), int(args.get("startMs") or 0), bool(args.get("hls")))
            elif cmd == "play":
                await self.mpv.play()
            elif cmd == "pause":
                await self.mpv.pause()
            elif cmd == "seek":
                await self.mpv.seek(int(float(args.get("ms") or 0)))
            elif cmd == "stop":
                await self.mpv.stop()
            elif cmd == "exit":
                # A box has nothing to go back to: the interface stays (power off is in the settings).
                log.info("exit requested by the interface, ignored")
        except Exception as e:  # noqa: BLE001 - reported to the page
            log.warning("player %s: %s", cmd, e)
            if cmd == "open":
                self.emit({"type": "error", "message": str(e) or "player not ready"})

    # ---------------------------------------------------------------- screen

    async def setup_outputs(self):
        wanted = device.display_mode(self.cfg, self.board)
        if wanted != "auto":
            out = await swaymsg("-t", "get_outputs", "--raw")
            try:
                outputs = json.loads(out) if out else []
            except ValueError:
                outputs = []
            for o in outputs:
                mode = device.pick_mode(o.get("modes") or [], wanted)
                if mode:
                    await swaymsg("output", o["name"], "mode", mode)
                else:
                    log.info("output %s has no mode %s, keeping the TV's preferred one", o.get("name"), wanted)
        layout = self.cfg.get("keyboard_layout")
        if layout:
            await swaymsg("input", "type:keyboard", "xkb_layout", layout)

    async def raise_interface(self):
        """The interface on top and focused: keys go to the page, never to mpv."""
        await swaymsg('[app_id="%s"]' % BROWSER_APP_ID, "focus")

    def on_mpv_window(self):
        asyncio.get_running_loop().create_task(self.raise_interface())

    # ---------------------------------------------------------------- browser

    def browser_command(self, url: str):
        custom = self.cfg.get("browser")
        if custom:
            return [c.replace("{url}", url) for c in custom.split()]
        cog_ini = os.path.join(LIB_DIR, "cog.ini")
        cmd = ["cog", "--platform=wl", "--bg-color=#00000000", "--webprocess-failure=restart"]
        if os.path.exists(cog_ini):
            cmd.append("--config=" + cog_ini)
        return cmd + [url]

    async def run_browser(self, url: str):
        """Keeps the browser running. One that ends at once again and again (missing, broken
        graphics) is started less and less often, up to every minute."""
        delay = 2.0
        loop = asyncio.get_running_loop()
        while not self.stopping.is_set():
            cmd = self.browser_command(url)
            started = loop.time()
            try:
                self.browser = await asyncio.create_subprocess_exec(*cmd, stdin=asyncio.subprocess.DEVNULL)
                log.info("browser started: %s", " ".join(cmd))
                loop.call_later(2, lambda: asyncio.ensure_future(self.raise_interface()))
                code = await self.browser.wait()
                log.warning("browser exited with %s", code)
            except FileNotFoundError:
                log.error("browser not found: %s", cmd[0])
            if self.stopping.is_set():
                return
            delay = 2.0 if loop.time() - started > 60 else min(delay * 2, 60.0)
            await asyncio.sleep(delay)

    async def watch_updates(self):
        """An update of the package (apt, unattended) brings a new interface and agent: the session
        starts again with them, but only while nothing plays."""
        while not self.stopping.is_set():
            await asyncio.sleep(60)
            installed = version()
            if installed != self.version and not (self.mpv and (self.mpv.loaded or self.mpv.entry is not None)):
                log.info("FreeXHT %s installed (running %s): starting the session again", installed, self.version)
                self.stopping.set()
                return

    def restart_browser(self):
        if self.browser and self.browser.returncode is None:
            self.browser.terminate()

    # ---------------------------------------------------------------- run

    async def run(self):
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGTERM, signal.SIGINT):
            loop.add_signal_handler(sig, self.stopping.set)
        await self.setup_outputs()

        runtime = os.environ.get("XDG_RUNTIME_DIR") or "/tmp"
        cfg = self.cfg
        opts = default_options(
            hwdec=device.hwdec(self.board, cfg),
            audio_device=device.audio_device(cfg, self.board),
            passthrough=",".join(conf.items(cfg.get("audio_passthrough", ""))),
            vo=cfg.get("video_output", ""),
            small_memory=self.board == "rpi3",
            user_agent="FreeXHT/%s" % self.version,
        )
        log.info("mpv: hwdec=%s audio-device=%s", opts["hwdec"], opts["audio-device"])
        self.mpv = Mpv(runtime, opts, conf.boot_file("mpv.conf"), self.emit, self.on_mpv_window,
                       binary=self.args.mpv)
        mpv_task = loop.create_task(self.mpv.supervise())
        try:
            await asyncio.wait_for(self.mpv.ready_event.wait(), 20)
        except asyncio.TimeoutError:
            log.error("mpv did not start; the interface starts anyway")

        self.web = WebServer(self.args.web, self.bridge_js, self.on_message, port=self.args.port)
        await self.web.start()
        tasks = [mpv_task, loop.create_task(self.watch_updates())]
        if not self.args.no_browser:
            tasks.append(loop.create_task(self.run_browser(self.web.origin + "/")))

        await self.stopping.wait()
        log.info("stopping")
        if self.browser and self.browser.returncode is None:
            self.browser.terminate()
        await self.mpv.close()
        await self.web.close()
        for t in tasks:
            t.cancel()


def main(argv=None):
    p = argparse.ArgumentParser(prog="freexht-agent", description="FreeXHT session: interface, player and system.")
    p.add_argument("--web", default=WEB_DIR, help="directory of the FreeX TV interface")
    p.add_argument("--port", type=int, default=7650)
    p.add_argument("--mpv", default="mpv", help="mpv binary")
    p.add_argument("--no-browser", action="store_true", help="only the server and mpv (development)")
    p.add_argument("--debug", action="store_true")
    args = p.parse_args(argv)
    logging.basicConfig(level=logging.DEBUG if args.debug else logging.INFO,
                        format="%(name)s: %(message)s", stream=sys.stderr)
    try:
        asyncio.run(Session(args).run())
    finally:
        # Without the agent the box is useless: end sway, systemd starts the session again.
        if os.environ.get("SWAYSOCK") and shutil.which("swaymsg") and not args.no_browser:
            os.system("swaymsg exit >/dev/null 2>&1")


if __name__ == "__main__":
    main()
