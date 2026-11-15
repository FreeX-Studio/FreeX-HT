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
The local web server of the interface, on 127.0.0.1 only:

  /                    the FreeX TV interface, with the FreeXHT bridge loaded before backends.js
  /freexht/bridge.js   window.FreeXNative for the page (see data/bridge.js)
  /freexht/ws          WebSocket: commands from the page, player events to the page
  everything else      files of the interface

A small HTTP/1.1 server and RFC 6455 WebSocket on asyncio streams, so the package needs nothing
beyond Python's standard library.
"""

import asyncio
import base64
import hashlib
import json
import logging
import mimetypes
import os
import struct
from typing import Awaitable, Callable, Optional, Set
from urllib.parse import unquote, urlsplit

log = logging.getLogger("freexht.web")

WS_GUID = b"258EAFA5-E914-47DA-95CA-C5AB0DC85B11"
BRIDGE_TAG = '<script src="freexht/bridge.js"></script>'
TYPES = {
    ".html": "text/html; charset=utf-8", ".js": "text/javascript; charset=utf-8", ".css": "text/css; charset=utf-8",
    ".json": "application/json", ".svg": "image/svg+xml", ".png": "image/png", ".woff2": "font/woff2",
    ".txt": "text/plain; charset=utf-8",
}


def inject_bridge(html: str) -> str:
    """The bridge has to exist before backends.js looks for the platform."""
    marker = '<script src="js/backends.js"></script>'
    if marker in html:
        return html.replace(marker, BRIDGE_TAG + "\n" + marker, 1)
    return html.replace("</head>", BRIDGE_TAG + "\n</head>", 1)


def accept_key(key: str) -> str:
    return base64.b64encode(hashlib.sha1(key.encode() + WS_GUID).digest()).decode()


def encode_frame(payload: bytes, opcode: int = 1) -> bytes:
    n = len(payload)
    head = bytes([0x80 | opcode])
    if n < 126:
        head += bytes([n])
    elif n < 65536:
        head += bytes([126]) + struct.pack("!H", n)
    else:
        head += bytes([127]) + struct.pack("!Q", n)
    return head + payload


async def read_frame(reader: asyncio.StreamReader):
    """(fin, opcode, payload) of one frame from the browser (always masked)."""
    b1, b2 = await reader.readexactly(2)
    fin, opcode = bool(b1 & 0x80), b1 & 0x0F
    masked, n = bool(b2 & 0x80), b2 & 0x7F
    if n == 126:
        n = struct.unpack("!H", await reader.readexactly(2))[0]
    elif n == 127:
        n = struct.unpack("!Q", await reader.readexactly(8))[0]
    if n > 4 * 1024 * 1024:
        raise ValueError("frame too large")
    mask = await reader.readexactly(4) if masked else b""
    data = await reader.readexactly(n)
    if masked:
        data = bytes(b ^ mask[i % 4] for i, b in enumerate(data))
    return fin, opcode, data


class Client:
    def __init__(self, writer: asyncio.StreamWriter):
        self.writer = writer
        self.lock = asyncio.Lock()

    async def send(self, obj: dict):
        data = encode_frame(json.dumps(obj, separators=(",", ":")).encode())
        async with self.lock:
            self.writer.write(data)
            await self.writer.drain()


class WebServer:
    def __init__(self, web_dir: str, bridge_js: Callable[[], str],
                 on_message: Callable[[Client, dict], Awaitable[Optional[dict]]],
                 host: str = "127.0.0.1", port: int = 7650):
        self.web_dir = os.path.realpath(web_dir)
        self.bridge_js = bridge_js
        self.on_message = on_message
        self.host = host
        self.port = port
        self.clients: Set[Client] = set()
        self.server: Optional[asyncio.AbstractServer] = None

    @property
    def origin(self) -> str:
        return "http://%s:%d" % (self.host, self.port)

    async def start(self):
        self.server = await asyncio.start_server(self._handle, self.host, self.port)
        if self.port == 0:
            self.port = self.server.sockets[0].getsockname()[1]
        log.info("interface on %s", self.origin)

    async def close(self):
        if self.server:
            self.server.close()
            await self.server.wait_closed()

    def broadcast(self, obj: dict):
        for c in list(self.clients):
            asyncio.get_running_loop().create_task(self._send(c, obj))

    async def _send(self, c: Client, obj: dict):
        try:
            await c.send(obj)
        except (ConnectionError, OSError):
            self.clients.discard(c)

    # ---------------------------------------------------------------- HTTP

    async def _handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter):
        try:
            head = await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), 30)
        except (asyncio.IncompleteReadError, asyncio.LimitOverrunError, asyncio.TimeoutError, ConnectionError):
            writer.close()
            return
        lines = head.decode("latin-1").split("\r\n")
        try:
            method, target, _ = lines[0].split(" ", 2)
        except ValueError:
            writer.close()
            return
        headers = {}
        for line in lines[1:]:
            if ":" in line:
                k, v = line.split(":", 1)
                headers[k.strip().lower()] = v.strip()
        path = unquote(urlsplit(target).path)
        try:
            if path == "/freexht/ws" and headers.get("upgrade", "").lower() == "websocket":
                await self._websocket(reader, writer, headers)
                return
            if method not in ("GET", "HEAD"):
                await self._respond(writer, 405, b"method not allowed", "text/plain")
            elif path == "/freexht/bridge.js":
                await self._respond(writer, 200, self.bridge_js().encode(), TYPES[".js"], head_only=method == "HEAD")
            elif path == "/freexht/health":
                await self._respond(writer, 200, b"ok", "text/plain")
            else:
                await self._file(writer, path, method == "HEAD")
        except (ConnectionError, OSError):
            pass
        finally:
            if not writer.is_closing():
                writer.close()

    async def _respond(self, writer, status: int, body: bytes, ctype: str, head_only: bool = False):
        reason = {200: "OK", 403: "Forbidden", 404: "Not Found", 405: "Method Not Allowed"}.get(status, "OK")
        writer.write(("HTTP/1.1 %d %s\r\nContent-Type: %s\r\nContent-Length: %d\r\nCache-Control: no-cache\r\n"
                      "Connection: close\r\n\r\n" % (status, reason, ctype, len(body))).encode())
        if not head_only:
            writer.write(body)
        await writer.drain()

    async def _file(self, writer, path: str, head_only: bool):
        rel = path.lstrip("/") or "index.html"
        full = os.path.realpath(os.path.join(self.web_dir, rel))
        if full != self.web_dir and not full.startswith(self.web_dir + os.sep):
            return await self._respond(writer, 403, b"forbidden", "text/plain")
        if os.path.isdir(full):
            full = os.path.join(full, "index.html")
        if not os.path.isfile(full):
            return await self._respond(writer, 404, b"not found", "text/plain")
        with open(full, "rb") as f:
            body = f.read()
        if os.path.basename(full) == "index.html" and os.path.dirname(full) == self.web_dir:
            body = inject_bridge(body.decode("utf-8")).encode("utf-8")
        ext = os.path.splitext(full)[1].lower()
        ctype = TYPES.get(ext) or mimetypes.guess_type(full)[0] or "application/octet-stream"
        await self._respond(writer, 200, body, ctype, head_only)

    # ---------------------------------------------------------------- WebSocket

    async def _websocket(self, reader, writer, headers):
        # Only the interface itself may drive the box (not some other page in the same browser).
        origin = headers.get("origin", "")
        if origin and origin not in (self.origin, "http://localhost:%d" % self.port):
            return await self._respond(writer, 403, b"forbidden origin", "text/plain")
        key = headers.get("sec-websocket-key")
        if not key:
            return await self._respond(writer, 403, b"no key", "text/plain")
        writer.write(("HTTP/1.1 101 Switching Protocols\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n"
                      "Sec-WebSocket-Accept: %s\r\n\r\n" % accept_key(key)).encode())
        await writer.drain()
        client = Client(writer)
        self.clients.add(client)
        buf = b""
        try:
            while True:
                fin, opcode, data = await read_frame(reader)
                if opcode == 8:  # close
                    async with client.lock:
                        writer.write(encode_frame(data[:2], 8))
                        await writer.drain()
                    break
                if opcode == 9:  # ping
                    async with client.lock:
                        writer.write(encode_frame(data, 10))
                        await writer.drain()
                    continue
                if opcode in (1, 0):
                    buf += data
                    if not fin:
                        continue
                    text, buf = buf, b""
                    try:
                        msg = json.loads(text.decode("utf-8"))
                    except ValueError:
                        continue
                    asyncio.get_running_loop().create_task(self._dispatch(client, msg))
        except (asyncio.IncompleteReadError, ConnectionError, OSError, ValueError):
            pass
        finally:
            self.clients.discard(client)

    async def _dispatch(self, client: Client, msg: dict):
        try:
            reply = await self.on_message(client, msg)
        except Exception as e:  # noqa: BLE001 - the page gets the error
            log.warning("message %s: %s", msg.get("call") or msg.get("cmd"), e)
            reply = {"id": msg["id"], "error": str(e) or e.__class__.__name__} if "id" in msg else None
        if reply is not None:
            try:
                await client.send(reply)
            except (ConnectionError, OSError):
                self.clients.discard(client)
