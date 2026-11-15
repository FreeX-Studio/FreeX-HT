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
End-to-end test of the interface on FreeXHT, with a browser instead of cog and fake_mpv.py
instead of mpv:

    python3 tests/e2e_ui.py <FreeX-TV-Apps/web> [screenshot directory]

Needs Playwright with a Chromium-based browser (pip install playwright). It starts a stand-in of
the FreeX Media Server API, the agent (freexht.session --no-browser) and drives the interface
with the keys of a remote: on-screen keyboard, sign-in, settings of the box, network screen,
and a video from opening to its end through mpv.
"""

import json
import os
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.join(HERE, "..")
AGENT_PORT = 7651

ITEM = {"id": "m1", "type": "movie", "title": "Test Movie", "year": 2026, "duration": 120000,
        "media": {"durationMs": 120000, "width": 1920, "height": 1080, "audio": [{"index": 1, "language": "de", "codec": "ac3"}],
                  "subtitles": []}}
requests = []


class Api(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def send(self, obj, status=200):
        body = json.dumps(obj).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Headers", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, PUT, DELETE")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_OPTIONS(self):
        self.send({})

    def body(self):
        n = int(self.headers.get("Content-Length") or 0)
        return json.loads(self.rfile.read(n) or b"{}") if n else {}

    def do_GET(self):
        p = self.path.split("?")[0]
        requests.append(("GET", p, None))
        if p == "/api/status":
            return self.send({"name": "Testserver", "version": "0.30.0", "setupRequired": False})
        if p == "/api/me":
            return self.send({"id": 1, "username": "niklas", "preferences": {}})
        if p == "/api/hubs":
            return self.send([{"key": "recent", "title": "Recently added", "items": [ITEM]}])
        if p == "/api/items/m1":
            return self.send(ITEM)
        return self.send([])

    def do_POST(self):
        p = self.path.split("?")[0]
        b = self.body()
        requests.append(("POST", p, b))
        if p == "/api/login":
            if b.get("username") == "niklas" and b.get("password") == "Geheim1":
                return self.send({"token": "tok", "user": {"id": 1, "username": "niklas", "preferences": {}}})
            return self.send({"error": "wrong password"}, 401)
        if p == "/api/items/m1/playback":
            return self.send({"url": "/stream/m1/short-movie.mkv?token=x", "mode": "direct", "startMs": 0,
                              "durationMs": 2000, "audio": 1, "qualities": []})
        return self.send({})

    def do_PUT(self):
        requests.append(("PUT", self.path, self.body()))
        self.send({})

    def do_DELETE(self):
        self.send({})


def main():
    web = os.path.abspath(sys.argv[1])
    shots = sys.argv[2] if len(sys.argv) > 2 else None
    api = ThreadingHTTPServer(("127.0.0.1", 0), Api)
    threading.Thread(target=api.serve_forever, daemon=True).start()
    server = "127.0.0.1:%d" % api.server_address[1]

    env = dict(os.environ, XDG_RUNTIME_DIR="/tmp/freexht-e2e", PYTHONPATH=os.path.join(ROOT, "src"),
               PATH=os.path.join(HERE, "fakebin") + os.pathsep + os.environ.get("PATH", ""))
    os.makedirs(env["XDG_RUNTIME_DIR"], exist_ok=True)
    agent = subprocess.Popen([sys.executable, "-m", "freexht.session", "--no-browser", "--web", web,
                              "--port", str(AGENT_PORT), "--mpv", os.path.join(HERE, "fake_mpv.py")], env=env)
    try:
        time.sleep(1.5)
        run(server, shots)
    finally:
        agent.terminate()
        agent.wait(5)
        api.shutdown()


def run(server, shots):
    from playwright.sync_api import sync_playwright

    errors = []
    with sync_playwright() as pw:
        browser = pw.chromium.launch(channel="chrome", args=["--autoplay-policy=no-user-gesture-required"])
        page = browser.new_page(viewport={"width": 1920, "height": 1080})
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.on("console", lambda m: errors.append(m.text) if m.type == "error" and "Failed to load resource" not in m.text else None)
        page.on("response", lambda r: errors.append("HTTP %d %s" % (r.status, r.url)) if r.status >= 400 and "favicon" not in r.url else None)

        def shot(name):
            if shots:
                os.makedirs(shots, exist_ok=True)
                page.screenshot(path=os.path.join(shots, name + ".png"))

        def key(k, n=1):
            for _ in range(n):
                page.keyboard.press(k)
                page.wait_for_timeout(60)

        def check(cond, what):
            if not cond:
                shot("FAILED")
                raise AssertionError(what)
            print("ok -", what)

        page.goto("http://127.0.0.1:%d/" % AGENT_PORT)
        page.wait_for_selector(".panel")
        check(page.evaluate("document.body.classList.contains('platform-freexht')"), "platform is freexht")
        check(page.evaluate("FreeXPlatform.deviceName()").startswith("FreeXHT"), "device name from the agent")
        caps = page.evaluate("FreeXPlatform.caps(true)")
        check("dts" in caps["audio"] and caps["directPlay"], "capabilities from the agent")

        # Connect screen: no server configured. OK on the field opens the on-screen keyboard.
        check(page.locator("text=Network").count() >= 1, "network button on the connect screen")
        page.wait_for_timeout(300)
        key("Enter")
        page.wait_for_selector(".osk:not([hidden])")
        shot("01-keyboard")
        check(page.evaluate("document.activeElement.tagName") == "INPUT", "field focused, keyboard open")

        def type_osk(text):
            """Types like a remote: arrows to each key, OK."""
            for ch in text:
                for attempt in range(2):
                    found = page.evaluate("""(ch) => {
                        const rows = [...document.querySelectorAll('.osk-row')];
                        for (let r = 0; r < rows.length; r++) {
                            const keys = [...rows[r].children];
                            for (let c = 0; c < keys.length; c++) if (keys[c].textContent === ch) return [r, c];
                        }
                        return null; }""", ch)
                    if found:
                        break
                    if ch.isalpha() and ch.upper() == ch:
                        goto_key(lambda k: k == "⇧")  # capital: shift for one letter
                    else:
                        goto_key(lambda k: k in ("#+=", "abc"))  # other page: letters/symbols
                    key("Enter")
                r, c = found
                cur = page.evaluate("""() => { const rows = [...document.querySelectorAll('.osk-row')];
                    for (let r = 0; r < rows.length; r++) { const i = [...rows[r].children].findIndex(k => k.classList.contains('focused'));
                    if (i >= 0) return [r, i]; } }""")
                while cur[0] != r:
                    key("ArrowDown" if cur[0] < r else "ArrowUp")
                    cur = page.evaluate("""() => { const rows = [...document.querySelectorAll('.osk-row')];
                        for (let r = 0; r < rows.length; r++) { const i = [...rows[r].children].findIndex(k => k.classList.contains('focused'));
                        if (i >= 0) return [r, i]; } }""")
                while cur[1] != c:
                    key("ArrowRight" if cur[1] < c else "ArrowLeft")
                    cur[1] += 1 if cur[1] < c else -1
                key("Enter")

        def goto_key(pred):
            labels = page.evaluate("[...document.querySelectorAll('.osk-row:last-child .osk-key')].map(k => k.textContent)")
            target = next(i for i, l in enumerate(labels) if pred(l))
            for _ in range(6):
                key("ArrowDown")
            cur = page.evaluate("[...document.querySelectorAll('.osk-row:last-child .osk-key')].findIndex(k => k.classList.contains('focused'))")
            while cur != target:
                key("ArrowRight" if cur < target else "ArrowLeft")
                cur += 1 if cur < target else -1

        type_osk(server)
        check(page.evaluate("document.querySelector('input').value") == server, "server address typed with the remote")
        goto_key(lambda l: l in ("Done", "Fertig"))
        key("Enter")
        check(page.evaluate("document.querySelector('.osk').hidden"), "Done closes the keyboard")
        key("Enter")  # Connect (focused after tvsubmit)
        page.wait_for_selector("input[type=password]")
        check("Testserver" in page.inner_text("h1"), "connected, sign-in screen")

        # Sign-in with a USB keyboard: the on-screen keyboard steps aside while typing.
        key("Enter")
        page.wait_for_selector(".osk:not([hidden])")
        page.keyboard.type("niklas")
        check(page.evaluate("document.querySelector('.osk').hidden"), "keyboard typing hides the on-screen keyboard")
        key("Enter")
        key("Enter")
        page.wait_for_selector(".osk:not([hidden])")
        type_osk("Geheim1")
        shot("02-password")
        check(page.inner_text(".osk-value") == "•••••••", "password shown as dots")
        goto_key(lambda l: l in ("Done", "Fertig"))
        key("Enter")
        key("Enter")
        page.wait_for_selector(".card", timeout=10000)
        check(True, "signed in, home screen")
        shot("03-home")

        # Settings: the box.
        page.evaluate("document.querySelector('.tab[data-key=settings], [data-tab=settings]') && 0")
        page.evaluate("""() => { const tabs = [...document.querySelectorAll('.f')].filter(e => /Settings|Einstellungen/.test(e.textContent) || e.querySelector && e.title === 'Settings');
            if (tabs[0]) { FreeXNav.focus(tabs[0]); tabs[0].click(); } }""")
        page.wait_for_selector(".freexht-system", timeout=5000)
        page.wait_for_function("!document.querySelector('.freexht-system').textContent.includes('…')", timeout=10000)
        text = page.inner_text(".freexht-system")
        check("FreeXHT" in text and ("Network" in text or "Netzwerk" in text), "box section in the settings")
        shot("04-settings")

        page.evaluate("FreeXApp.push(FreeXHT.networkScreen())")
        page.wait_for_selector(".freexht-network")
        page.wait_for_selector(".freexht-network .btn.wifi", timeout=10000)
        nets = page.eval_on_selector_all(".btn.wifi .ssid", "els => els.map(e => e.textContent)")
        check(nets == ["Nachbar", "Home", "Cafe: Gast"], "Wi-Fi networks listed, the connected one first (%s)" % nets)
        check("Nachbar" in page.inner_text(".freexht-network p.muted"), "network status shows the Wi-Fi")
        shot("05-network")
        page.wait_for_timeout(200)
        check(page.evaluate("FreeXNav.focused.textContent").startswith("Nachbar"), "first network focused")
        key("ArrowDown")
        key("Enter")  # Home: secured → password
        page.wait_for_selector(".dialog input[type=password]")
        key("Enter")
        page.wait_for_selector(".osk:not([hidden])")
        type_osk("pw12")
        goto_key(lambda l: l in ("Done", "Fertig"))
        key("Enter")
        key("Enter")  # Connect
        page.wait_for_function("document.getElementById('toast').textContent.indexOf('Home') >= 0", timeout=10000)
        check(True, "connected to a secured Wi-Fi with the on-screen keyboard")
        key("Escape")
        page.wait_for_selector(".freexht-network", state="detached")

        # Back on the home screen: power menu instead of "Close FreeX?".
        page.evaluate("FreeXApp.reset(FreeXScreens.main())")
        page.wait_for_selector(".card")
        page.wait_for_timeout(200)
        key("Escape")  # first Back: to the tabs
        key("Escape")  # second: the power menu
        page.wait_for_selector(".dialog")
        dialog_text = page.inner_text(".dialog")
        check("Switch" in dialog_text or "ausschalten" in dialog_text, "back on home offers switching off")
        key("Escape")

        # Playback through mpv (fake): ready → play → time → end.
        requests.clear()
        page.evaluate("FreeXPlayer.open('m1')")
        page.wait_for_selector(".player")
        page.wait_for_function("document.body.classList.contains('video-mode')")
        bg = page.evaluate("getComputedStyle(document.body).backgroundColor")
        check(bg in ("rgba(0, 0, 0, 0)", "transparent"), "page transparent while the video plays")
        page.wait_for_function("document.querySelector('.player .spinner') ? document.querySelector('.player .spinner').hidden : true",
                               timeout=10000)
        shot("06-player")
        plan_req = [r for r in requests if r[1] == "/api/items/m1/playback"]
        check(plan_req and plan_req[0][2]["caps"]["audio"] and plan_req[0][2]["device"].startswith("FreeXHT"),
              "playback request with the box's capabilities")
        page.wait_for_selector(".player", state="detached", timeout=15000)
        check(any(r[0] == "PUT" for r in requests), "progress reported to the server")
        check(not page.evaluate("document.body.classList.contains('video-mode')"), "video ended, interface back")

        bad = [e for e in errors if "favicon" not in e]
        check(not bad, "no script errors (%s)" % bad)
        browser.close()
    print("e2e: all checks passed")


if __name__ == "__main__":
    main()
