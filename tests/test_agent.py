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
"""Tests of the agent: python3 -m unittest discover -s tests (from the repository root)."""

import asyncio
import base64
import json
import os
import struct
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "src"))

from freexht import bootconfig, cec, config, device, system, web  # noqa: E402
from freexht.mpv import Mpv, default_options, mpv_conf  # noqa: E402


def write(root, rel, text):
    path = os.path.join(root, rel)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)
    return path


class ConfigTest(unittest.TestCase):
    def test_parse(self):
        cfg = config.parse('# comment\nwifi_ssid = "My Net"\nWIFI-PASSWORD=se=cret\n\nserver=10.0.0.2\nbad line\n')
        self.assertEqual(cfg, {"wifi_ssid": "My Net", "wifi_password": "se=cret", "server": "10.0.0.2"})

    def test_later_file_wins(self):
        with tempfile.TemporaryDirectory() as d:
            a = write(d, "a.conf", "server=a\nhostname=box\n")
            b = write(d, "b.conf", "server=b\n")
            cfg = config.load([a, b, os.path.join(d, "missing.conf")])
        self.assertEqual(cfg["server"], "b")
        self.assertEqual(cfg["hostname"], "box")
        self.assertEqual(cfg["hwdec"], "")

    def test_items_and_flag(self):
        self.assertEqual(config.items("ac3, eac3 dts"), ["ac3", "eac3", "dts"])
        self.assertTrue(config.flag("Yes"))
        self.assertFalse(config.flag("off", True))
        self.assertTrue(config.flag("", True))


class DeviceTest(unittest.TestCase):
    def test_boards(self):
        self.assertEqual(device.board("Raspberry Pi 3 Model B Plus Rev 1.3"), "rpi3")
        self.assertEqual(device.board("Raspberry Pi Zero 2 W Rev 1.0"), "rpi3")
        self.assertEqual(device.board("Raspberry Pi 4 Model B Rev 1.5"), "rpi4")
        self.assertEqual(device.board("Raspberry Pi 400 Rev 1.1"), "rpi4")
        self.assertEqual(device.board("Raspberry Pi Compute Module 4 Rev 1.0"), "rpi4")
        self.assertEqual(device.board("Raspberry Pi 5 Model B Rev 1.0"), "rpi5")
        self.assertEqual(device.board("Raspberry Pi Compute Module 5 Rev 1.0"), "rpi5")
        self.assertEqual(device.board("Intel NUC", "x86_64"), "x86")

    def test_model_from_device_tree_and_dmi(self):
        with tempfile.TemporaryDirectory() as d:
            write(d, "proc/device-tree/model", "Raspberry Pi 4 Model B Rev 1.4\0")
            self.assertEqual(device.model(d), "Raspberry Pi 4 Model B Rev 1.4")
        with tempfile.TemporaryDirectory() as d:
            write(d, "sys/class/dmi/id/sys_vendor", "Intel(R) Client Systems\n")
            write(d, "sys/class/dmi/id/product_name", "NUC11TNKi5\n")
            self.assertEqual(device.model(d), "Intel(R) Client Systems NUC11TNKi5")
        with tempfile.TemporaryDirectory() as d:
            write(d, "sys/class/dmi/id/sys_vendor", "To Be Filled By O.E.M.\n")
            self.assertEqual(device.model(d), "PC")
        self.assertEqual(device.short_model("Raspberry Pi 4 Model B Rev 1.4"), "Raspberry Pi 4 Model B")

    def test_capabilities(self):
        pi3 = device.capabilities("rpi3", config.DEFAULTS)
        self.assertNotIn("hevc", pi3["video"])
        self.assertEqual(pi3["mseVideo"], ["h264"])
        self.assertIn("dts", pi3["audio"])
        pi4 = device.capabilities("rpi4", config.DEFAULTS)
        self.assertIn("hevc", pi4["video"])
        self.assertEqual(pi4["mseVideo"], ["h264", "hevc"])
        self.assertNotIn("hevcMain10", pi4)
        custom = device.capabilities("rpi4", dict(config.DEFAULTS, video_codecs="h264", hevc_main10="no"))
        self.assertEqual(custom["video"], ["h264"])
        self.assertFalse(custom["hevcMain10"])
        for key in ("containers", "video", "audio", "mseVideo", "mseAudio"):
            self.assertIsInstance(device.capabilities("x86", config.DEFAULTS)[key], list)

    def test_hwdec(self):
        self.assertEqual(device.hwdec("rpi3", config.DEFAULTS), "v4l2m2m-copy")
        self.assertEqual(device.hwdec("rpi5", config.DEFAULTS), "drm")
        self.assertEqual(device.hwdec("x86", dict(config.DEFAULTS, hwdec="vaapi")), "vaapi")

    def test_audio_device(self):
        with tempfile.TemporaryDirectory() as d:
            write(d, "proc/asound/cards", " 0 [Headphones     ]: bcm2835_headpho - bcm2835 Headphones\n"
                                          " 1 [vc4hdmi0       ]: vc4-hdmi - vc4-hdmi-0\n"
                                          " 2 [vc4hdmi1       ]: vc4-hdmi - vc4-hdmi-1\n")
            self.assertEqual(device.audio_device(config.DEFAULTS, "rpi4", d), "alsa/hdmi:CARD=vc4hdmi0,DEV=0")
        with tempfile.TemporaryDirectory() as d:
            write(d, "proc/asound/cards", " 0 [PCH            ]: HDA-Intel - HDA Intel PCH\n")
            write(d, "proc/asound/card0/eld#2.0", "monitor_present\t\t0\n")
            write(d, "proc/asound/card0/eld#2.1", "monitor_present\t\t1\neld_valid\t\t1\n")
            write(d, "proc/asound/card0/eld#2.2", "monitor_present\t\t0\n")
            self.assertEqual(device.audio_device(config.DEFAULTS, "x86", d), "alsa/hdmi:CARD=PCH,DEV=1")
        with tempfile.TemporaryDirectory() as d:
            self.assertEqual(device.audio_device(config.DEFAULTS, "x86", d), "auto")
        self.assertEqual(device.audio_device(dict(config.DEFAULTS, audio_device="alsa/x"), "x86", "/nonexistent"), "alsa/x")

    def test_display_mode(self):
        self.assertEqual(device.display_mode(config.DEFAULTS, "rpi4"), "1920x1080")
        self.assertEqual(device.display_mode(config.DEFAULTS, "rpi3"), "1280x720")
        self.assertTrue(device.lite_ui(config.DEFAULTS, "rpi3"))
        self.assertFalse(device.lite_ui(config.DEFAULTS, "rpi4"))
        self.assertTrue(device.lite_ui(dict(config.DEFAULTS, lite_ui="yes"), "x86"))
        self.assertEqual(device.display_mode(config.DEFAULTS, "x86"), "auto")
        self.assertEqual(device.display_mode(dict(config.DEFAULTS, display_mode="4K"), "rpi5"), "3840x2160")
        modes = [{"width": 3840, "height": 2160, "refresh": 60000}, {"width": 1920, "height": 1080, "refresh": 50000},
                 {"width": 1920, "height": 1080, "refresh": 60000}, {"width": 1920, "height": 1080, "refresh": 119880}]
        self.assertEqual(device.pick_mode(modes, "1920x1080"), "1920x1080@60.000Hz")
        self.assertEqual(device.pick_mode(modes, "1920x1080@50"), "1920x1080@50.000Hz")
        self.assertIsNone(device.pick_mode(modes, "1280x720"))
        self.assertIsNone(device.pick_mode(modes, "auto"))


class SystemTest(unittest.TestCase):
    def test_terse(self):
        self.assertEqual(system.split_terse(r"*:My\:Net:80:WPA2"), ["*", "My:Net", "80", "WPA2"])
        self.assertEqual(system.split_terse(r"a\\b:c"), ["a\\b", "c"])

    def test_wifi_list(self):
        text = (" :Home:54:WPA2\n*:Home:70:WPA2\n :Cafe:90:--\n :Home:80:WPA2\n : :40:WPA2\n"
                " :Office\\:5G:30:WPA2 802.1X\n")
        nets = system.parse_wifi_list(text)
        self.assertEqual([n["ssid"] for n in nets], ["Home", "Cafe", "Office:5G"])
        self.assertTrue(nets[0]["active"])
        self.assertTrue(nets[0]["secure"])
        self.assertFalse(nets[1]["secure"])

    def test_devices(self):
        devs = system.parse_devices("eth0:ethernet:connected:Wired connection 1\nwlan0:wifi:disconnected:\nlo:loopback:connected (externally):lo\n")
        self.assertEqual(devs[0]["connection"], "Wired connection 1")
        self.assertEqual(devs[1]["type"], "wifi")


class WebTest(unittest.TestCase):
    def test_inject(self):
        html = '<head></head><body><script src="js/core.js"></script>\n<script src="js/backends.js"></script></body>'
        out = web.inject_bridge(html)
        self.assertLess(out.index("freexht/bridge.js"), out.index("js/backends.js"))
        self.assertIn("freexht/bridge.js", web.inject_bridge("<head></head>"))

    def test_accept_key(self):
        # The example of RFC 6455.
        self.assertEqual(web.accept_key("dGhlIHNhbXBsZSBub25jZQ=="), "s3pPLMBiTxaQ9kYGzzhZRbK+xOo=")

    def test_frames(self):
        self.assertEqual(web.encode_frame(b"hi"), b"\x81\x02hi")
        self.assertEqual(web.encode_frame(b"x" * 300)[:4], b"\x81\x7e\x01\x2c")

        async def roundtrip():
            reader = asyncio.StreamReader()
            mask = b"\x01\x02\x03\x04"
            payload = b'{"cmd":"play"}'
            masked = bytes(b ^ mask[i % 4] for i, b in enumerate(payload))
            reader.feed_data(bytes([0x81, 0x80 | len(payload)]) + mask + masked)
            return await web.read_frame(reader)

        fin, op, data = asyncio.run(roundtrip())
        self.assertTrue(fin)
        self.assertEqual(op, 1)
        self.assertEqual(data, b'{"cmd":"play"}')

    def test_server(self):
        """Files, the bridge, path traversal, and a WebSocket round trip."""

        async def run():
            with tempfile.TemporaryDirectory() as d:
                write(d, "index.html", '<html><head></head><body><script src="js/backends.js"></script></body></html>')
                write(d, "js/backends.js", "// x")
                write(os.path.dirname(d), os.path.basename(d) + "-secret.txt", "secret")

                async def on_message(client, msg):
                    return {"id": msg["id"], "result": msg["args"]["n"] * 2} if "id" in msg else None

                srv = web.WebServer(d, lambda: "window.X=1;", on_message, port=0)
                await srv.start()

                async def get(path, extra=""):
                    r, w = await asyncio.open_connection("127.0.0.1", srv.port)
                    w.write(("GET %s HTTP/1.1\r\nHost: x\r\n%s\r\n" % (path, extra)).encode())
                    await w.drain()
                    return r, w

                r, w = await get("/")
                body = await r.read()
                w.close()
                self.assertIn(b"200 OK", body)
                self.assertIn(b"freexht/bridge.js", body)
                r, w = await get("/freexht/bridge.js")
                self.assertIn(b"window.X=1;", await r.read())
                w.close()
                r, w = await get("/../" + os.path.basename(d) + "-secret.txt")
                self.assertIn(b"403", (await r.read()).split(b"\r\n")[0])
                w.close()
                r, w = await get("/nope.js")
                self.assertIn(b"404", (await r.read()).split(b"\r\n")[0])
                w.close()

                # Wrong origin: refused.
                key = base64.b64encode(os.urandom(16)).decode()
                ws_head = "Upgrade: websocket\r\nConnection: Upgrade\r\nSec-WebSocket-Key: %s\r\nSec-WebSocket-Version: 13\r\n" % key
                r, w = await get("/freexht/ws", ws_head + "Origin: http://evil.example\r\n")
                self.assertIn(b"403", (await r.read()).split(b"\r\n")[0])
                w.close()

                r, w = await get("/freexht/ws", ws_head + "Origin: %s\r\n" % srv.origin)
                head = await r.readuntil(b"\r\n\r\n")
                self.assertIn(b"101", head)
                self.assertIn(web.accept_key(key).encode(), head)
                payload = json.dumps({"id": 7, "call": "x", "args": {"n": 21}}).encode()
                mask = os.urandom(4)
                w.write(bytes([0x81, 0x80 | len(payload)]) + mask + bytes(b ^ mask[i % 4] for i, b in enumerate(payload)))
                await w.drain()
                b1, b2 = await r.readexactly(2)
                reply = json.loads(await r.readexactly(b2 & 0x7F))
                self.assertEqual(reply, {"id": 7, "result": 42})
                srv.broadcast({"event": {"type": "time"}})
                b1, b2 = await r.readexactly(2)
                self.assertEqual(json.loads(await r.readexactly(b2 & 0x7F)), {"event": {"type": "time"}})
                w.close()
                await srv.close()

        asyncio.run(run())


class MpvTest(unittest.TestCase):
    def test_conf(self):
        opts = default_options("drm", "alsa/hdmi:CARD=vc4hdmi0,DEV=0", "ac3,dts", "", True, "FreeXHT/1")
        text = mpv_conf(opts, "/boot/firmware/mpv.conf")
        self.assertIn("hwdec=drm\n", text)
        self.assertIn("audio-spdif=ac3,dts\n", text)
        self.assertIn("wayland-app-id=freexht-video\n", text)
        self.assertIn("demuxer-max-bytes=48MiB\n", text)
        self.assertTrue(text.rstrip().endswith("include=/boot/firmware/mpv.conf"))
        self.assertNotIn("profile=", text)
        fast = mpv_conf(default_options("drm", "auto", "", "", False, "t", fast=True), None)
        self.assertTrue(fast.splitlines()[1] == "profile=fast", "the profile first, so later options win")

    def test_with_fake_mpv(self):
        """The page's view of a playback: ready (paused, at the start position), time, state, seek,
        ended; a broken file ends in an error; a crashed mpv comes back."""

        async def run():
            events = []
            with tempfile.TemporaryDirectory() as d:
                m = Mpv(d, default_options("auto", "auto", "", "", False, "t"), None, events.append,
                        binary=os.path.join(HERE, "fake_mpv.py"))
                task = asyncio.get_running_loop().create_task(m.supervise())
                await asyncio.wait_for(m.ready_event.wait(), 10)

                async def wait_for(kind, timeout=5):
                    for _ in range(int(timeout * 20)):
                        for e in events:
                            if e["type"] == kind:
                                return e
                        await asyncio.sleep(0.05)
                    self.fail("no %s event in %s" % (kind, events))

                await m.open("http://server/video.mkv?token=x", 30000)
                ready = await wait_for("ready")
                self.assertEqual(ready["position"], 30000)
                self.assertEqual(ready["duration"], 120000)
                self.assertFalse(ready["playing"])
                await asyncio.sleep(0.4)
                self.assertEqual(m.position, 30.0, "paused until play")
                events.clear()
                await m.play()
                st = await wait_for("state")
                self.assertTrue(st["playing"])
                t = await wait_for("time")
                self.assertGreater(t["position"], 30000)
                await m.seek(90000)
                await asyncio.sleep(0.1)
                self.assertGreaterEqual(m.position, 90.0)
                await m.pause()
                self.assertFalse(m.state("x")["playing"])

                events.clear()
                await m.open("http://server/broken.mkv", 0)
                err = await wait_for("error")
                self.assertIn("loading failed", err["message"])

                events.clear()
                await m.open("http://server/short.mkv", 0)
                await wait_for("ready")
                await m.play()
                await wait_for("ended")

                # Replacing a file must not end the new one.
                events.clear()
                await m.open("http://server/a.mkv", 0)
                await wait_for("ready")
                await m.open("http://server/b.mkv", 0)
                await wait_for("ready")
                await asyncio.sleep(0.2)
                self.assertFalse([e for e in events if e["type"] in ("ended", "error")])

                # mpv crashes while a file is open: error for the page, mpv starts again.
                events.clear()
                m.proc.kill()
                await wait_for("error")
                await asyncio.sleep(1.5)
                await asyncio.wait_for(m.ready_event.wait(), 10)
                await m.open("http://server/c.mkv", 0)
                await wait_for("ready")

                await m.close()
                task.cancel()

        asyncio.run(run())


class BootConfigTest(unittest.TestCase):
    def test_hostname(self):
        with tempfile.TemporaryDirectory() as d:
            write(d, "etc/hostname", "freexht\n")
            write(d, "etc/hosts", "127.0.0.1\tlocalhost\n127.0.1.1\tfreexht\n")
            self.assertTrue(bootconfig.apply_hostname("wohnzimmer", d))
            self.assertEqual(open(os.path.join(d, "etc/hostname")).read(), "wohnzimmer\n")
            self.assertIn("127.0.1.1\twohnzimmer", open(os.path.join(d, "etc/hosts")).read())
            self.assertFalse(bootconfig.apply_hostname("wohnzimmer", d))
            self.assertFalse(bootconfig.apply_hostname("bad name!", d))

    def test_wifi(self):
        with tempfile.TemporaryDirectory() as d:
            boot = write(d, "boot/firmware/freexht.conf", "wifi_ssid = Home\nwifi_password = s3cret\nserver=x\n")
            cfg = config.load([boot])
            self.assertTrue(bootconfig.apply_wifi(cfg, boot, d))
            nm = os.path.join(d, "etc/NetworkManager/system-connections/freexht-wifi.nmconnection")
            text = open(nm).read()
            self.assertIn("ssid=Home\n", text)
            self.assertIn("psk=s3cret\n", text)
            self.assertEqual(os.stat(nm).st_mode & 0o777, 0o600)
            uid = [line for line in text.splitlines() if line.startswith("uuid=")][0]
            # The password is gone from the boot partition, the rest stays.
            self.assertEqual(open(boot).read(), "wifi_ssid = Home\nwifi_password =\nserver=x\n")
            # Next boot: same network, no password → nothing to do.
            self.assertFalse(bootconfig.apply_wifi(config.load([boot]), boot, d))
            # A new password: the connection is replaced, its uuid kept.
            write(d, "boot/firmware/freexht.conf", "wifi_ssid = Home\nwifi_password = n3w\n")
            self.assertTrue(bootconfig.apply_wifi(config.load([boot]), boot, d))
            text = open(nm).read()
            self.assertIn("psk=n3w\n", text)
            self.assertIn(uid + "\n", text)
            # An open network.
            write(d, "boot/firmware/freexht.conf", "wifi_ssid = Cafe\n")
            self.assertTrue(bootconfig.apply_wifi(config.load([boot]), boot, d))
            self.assertNotIn("wifi-security", open(nm).read())

    def test_keyfile_escapes(self):
        text = bootconfig.nm_keyfile(" lead\\net", "p", "u")
        self.assertIn("ssid=\\slead\\\\net\n", text)

    def test_timezone_and_country(self):
        with tempfile.TemporaryDirectory() as d:
            write(d, "usr/share/zoneinfo/Europe/Berlin", "TZif")
            os.makedirs(os.path.join(d, "etc"))
            self.assertTrue(bootconfig.apply_timezone("Europe/Berlin", d))
            self.assertEqual(os.readlink(os.path.join(d, "etc/localtime")), "/usr/share/zoneinfo/Europe/Berlin")
            self.assertFalse(bootconfig.apply_timezone("Europe/Berlin", d))
            self.assertFalse(bootconfig.apply_timezone("../../etc/passwd", d))
            self.assertTrue(bootconfig.apply_country("de", d))
            self.assertIn("ieee80211_regdom=DE", open(os.path.join(d, "etc/modprobe.d/freexht-wifi-country.conf")).read())
            self.assertFalse(bootconfig.apply_country("Germany", d))

    def test_ssh_key(self):
        with tempfile.TemporaryDirectory() as d:
            key = "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAA niklas@pc"
            self.assertTrue(bootconfig.apply_ssh_key(key, d))
            self.assertTrue(bootconfig.apply_ssh_key(key, d))
            path = os.path.join(d, "root/.ssh/authorized_keys")
            self.assertEqual(open(path).read(), key + "\n")
            self.assertEqual(os.stat(path).st_mode & 0o777, 0o600)
            self.assertFalse(bootconfig.apply_ssh_key("not a key", d))


class CecTest(unittest.TestCase):
    def test_struct(self):
        data = cec.log_addrs("FreeXHT")
        self.assertEqual(len(data), 92)  # sizeof(struct cec_log_addrs)
        self.assertEqual(data[7], 1)  # num_log_addrs
        self.assertEqual(struct.unpack_from("=I", data, 12)[0], 3)  # flags: unreg fallback + RC passthrough
        self.assertEqual(data[16:23], b"FreeXHT")
        self.assertEqual(data[31], 4)  # primary device type: playback
        self.assertEqual(data[35], 3)  # logical address type: playback
        self.assertEqual(data[39], 0x10)
        self.assertEqual(cec.log_addrs("x", 0)[7], 0)
        self.assertEqual(cec.fmt_phys(0x1200), "1.2.0.0")


if __name__ == "__main__":
    unittest.main()
