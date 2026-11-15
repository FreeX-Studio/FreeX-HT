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
"""
A stand-in for mpv in the tests: the parts of mpv's JSON IPC that FreeXHT uses, with a clock
instead of a video. URLs containing "broken" fail to load; every file is 120 s long, files with
"short" 2 s (to reach the end).
"""

import asyncio
import json
import sys


class FakeMpv:
    def __init__(self):
        self.writers = []
        self.observed = {}  # name -> id
        self.props = {"pause": False, "start": "none", "time-pos": None, "duration": None,
                      "paused-for-cache": False, "seeking": False}
        self.entry = 0
        self.loaded = False
        self.length = 120.0
        self.clock = None

    def send(self, obj):
        data = (json.dumps(obj) + "\n").encode()
        for w in list(self.writers):
            try:
                w.write(data)
            except Exception:  # noqa: BLE001
                self.writers.remove(w)

    def set(self, name, value):
        self.props[name] = value
        if name in self.observed:
            self.send({"event": "property-change", "id": self.observed[name], "name": name, "data": value})

    async def run_clock(self):
        while self.loaded:
            await asyncio.sleep(0.1)
            if not self.loaded or self.props["pause"]:
                continue
            pos = (self.props["time-pos"] or 0) + 0.1
            if pos >= self.length:
                self.loaded = False
                self.send({"event": "end-file", "reason": "eof", "playlist_entry_id": self.entry})
                self.set("time-pos", None)
                return
            self.set("time-pos", round(pos, 3))

    async def load(self, url, entry):
        if self.loaded:
            self.loaded = False
            self.send({"event": "end-file", "reason": "stop", "playlist_entry_id": entry - 1})
        self.send({"event": "start-file", "playlist_entry_id": entry})
        await asyncio.sleep(0.05)
        if "broken" in url:
            self.send({"event": "end-file", "reason": "error", "playlist_entry_id": entry,
                       "file_error": "loading failed"})
            return
        self.length = 2.0 if "short" in url else 120.0
        start = self.props.get("start")
        pos = float(start) if start not in (None, "none") else 0.0
        self.loaded = True
        self.set("duration", self.length)
        self.set("time-pos", pos)
        self.send({"event": "file-loaded"})
        await asyncio.sleep(0.05)
        self.send({"event": "playback-restart"})
        self.clock = asyncio.get_running_loop().create_task(self.run_clock())

    async def handle(self, cmd):
        name = cmd[0]
        if name == "observe_property":
            self.observed[cmd[2]] = cmd[1]
            self.set(cmd[2], self.props.get(cmd[2]))
            return None
        if name == "set_property":
            self.set(cmd[1], cmd[2])
            return None
        if name == "get_property":
            return self.props.get(cmd[1])
        if name == "loadfile":
            self.entry += 1
            asyncio.get_running_loop().create_task(self.load(cmd[1], self.entry))
            return {"playlist_entry_id": self.entry}
        if name == "seek":
            self.set("seeking", True)
            self.set("time-pos", float(cmd[1]))
            self.set("seeking", False)
            self.send({"event": "playback-restart"})
            return None
        if name == "stop":
            if self.loaded:
                self.loaded = False
                self.send({"event": "end-file", "reason": "stop", "playlist_entry_id": self.entry})
            return None
        if name == "quit":
            asyncio.get_running_loop().call_later(0.05, sys.exit, 0)
            return None
        raise ValueError("unknown command " + name)

    async def client(self, reader, writer):
        self.writers.append(writer)
        while True:
            line = await reader.readline()
            if not line:
                break
            msg = json.loads(line)
            rid = msg.get("request_id")
            try:
                data = await self.handle(msg["command"])
                reply = {"error": "success", "data": data, "request_id": rid}
            except Exception as e:  # noqa: BLE001
                reply = {"error": str(e), "request_id": rid}
            writer.write((json.dumps(reply) + "\n").encode())
            await writer.drain()


async def main():
    sock = next(a.split("=", 1)[1] for a in sys.argv[1:] if a.startswith("--input-ipc-server="))
    fake = FakeMpv()
    server = await asyncio.start_unix_server(fake.client, sock)
    async with server:
        await server.serve_forever()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except SystemExit:
        pass
