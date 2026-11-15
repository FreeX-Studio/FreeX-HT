<p align="center">
  <img src="https://raw.githubusercontent.com/FreeX-Studio/FreeX-TV-Apps/main/web/img/logo.svg" alt="FreeX" width="110">
</p>

<h1 align="center">FreeXHT</h1>

<p align="center">
  <strong>The FreeX TV interface as a whole system: a Raspberry Pi or a small PC that boots straight into
  FreeX.</strong><br>
  <em>Your Media. Your Server. Your Freedom.</em>
</p>

FreeXHT is a system image for [FreeX Media Server](https://github.com/FreeX-Studio/Freex-Media-Server), in the
spirit of the media-center images of the past: flash it to an SD card or disk, plug the box into the TV, and it
starts the FreeX TV interface (the same one as on Samsung, LG and Fire TV, from
[FreeX-TV-Apps](https://github.com/FreeX-Studio/FreeX-TV-Apps)) with a real video player underneath. There is no
desktop and nothing to set up beyond the server address and, if needed, Wi-Fi. Both can be done with the TV's
remote control.

## Hardware

| Board | Image | Plays directly (no conversion on the server) |
|---|---|---|
| **Raspberry Pi 5** / 500 / CM5 | `FreeXHT-<version>-rpi.img.xz` | HEVC (also 10-bit, up to 4K) in hardware; H.264, VP9, MPEG-2/4 in software |
| **Raspberry Pi 4** / 400 / CM4 | `FreeXHT-<version>-rpi.img.xz` | HEVC (also 10-bit, up to 4K) and H.264 (up to 1080p) in hardware |
| **Raspberry Pi 3** / 3+ / Zero 2 W | `FreeXHT-<version>-rpi.img.xz` | H.264 up to 1080p in hardware; everything else is converted by the server |
| **PC** (x86_64, UEFI) | `FreeXHT-<version>-x86_64.img.xz` | H.264, HEVC, VP9, AV1, MPEG-2/4, VC-1 (VA-API on Intel and AMD, otherwise software) |

Every sound format plays (decoded by mpv, or passed through to the TV or receiver with `audio_passthrough`).
Whatever a box cannot decode, the server converts on the fly, as for every FreeX app.

The Raspberry Pi image is one image for the Pi 3, 4 and 5 (arm64). The PC image needs UEFI with Secure Boot
switched off.

## Installing

1. Write the image to an SD card (Raspberry Pi) or a USB stick or SSD (PC), with
   [Raspberry Pi Imager](https://www.raspberrypi.com/software/) (*Use custom*), balenaEtcher, or
   `xzcat FreeXHT-<version>-rpi.img.xz | sudo dd of=/dev/sdX bs=4M conv=fsync status=progress`.
2. Optional: open the partition **FREEXHT** on a computer and edit `freexht.conf`: Wi-Fi, the address of your
   server, an SSH key. Everything there can also be done later on the box.
3. Put the card into the box, connect it to the TV (and to the network by cable, or use Wi-Fi) and switch it on.
   At the first start the root file system grows to the whole card; that takes a moment.
4. Enter the server address and sign in, with the remote and the on-screen keyboard or with a USB keyboard.

### freexht.conf

A plain text file on the boot partition, read at every start. The most important settings:

| Key | |
|---|---|
| `wifi_ssid`, `wifi_password`, `wifi_country` | Wi-Fi network. The password is stored on the box and then removed from the file. |
| `server` | Address of the FreeX Media Server (e.g. `192.168.1.10`); the box skips the connect screen. |
| `hostname`, `device_name` | Name in the network and in the server's activity view. |
| `ssh_authorized_key` | A public key: switches SSH on, root logs in with that key. Without a key SSH stays off. |
| `timezone`, `keyboard_layout` | e.g. `Europe/Berlin`, `de`. |
| `display_mode` | `auto`, `1080p` (default on a Pi), `2160p`, `1920x1080@50`, … |
| `audio_passthrough` | e.g. `ac3,eac3,dts` for an AV receiver. |
| `audio_device`, `hwdec`, `video_output`, `video_codecs` | mpv's sound device, hardware decoding, video output and the codecs the server may send directly; normally chosen for the board. |

All keys with explanations are in [`data/freexht.conf`](data/freexht.conf). More mpv settings: an `mpv.conf` next to
`freexht.conf`.

## Remote control

The TV's remote works over **HDMI-CEC**: the box registers as a playback device "FreeXHT", switches the TV to its
input and receives the buttons. USB keyboards, air mice and USB remotes that act as keyboards work as well.

| TV remote | Keyboard | |
|---|---|---|
| Arrows, OK | Arrows, Enter | move, open |
| Back / Return / Exit | Escape, Backspace | back; on the home screen: switch off or restart |
| ⏯ ⏸ ▶ ⏹ ⏪ ⏩ ⏮ ⏭ | media keys | in the player as on the TVs |
| Info / Display | `i` | details |
| Menu / Setup / Guide | `m` | menu |

Text fields open an on-screen keyboard (letters, digits and symbols, Shift, Done). Typing on a USB keyboard
hides it.

In **Settings → This box**: model, network and name, the **Network** screen (Wi-Fi search, sign-in with the
on-screen keyboard), **Restart** and **Switch off**.

## How it works

```
┌─────────────────────────────── sway (Wayland, tty1, user freexht) ───────────────────────────────┐
│                                                                                                    │
│   cog (WPE WebKit) ── http://127.0.0.1:7650 ──►  freexht-agent (Python)                            │
│   FreeX TV interface,      /         the interface (/usr/share/freexht/web)                        │
│   transparent over the     /freexht/bridge.js   window.FreeXNative (like the Android app's)        │
│   video                    /freexht/ws          commands ◄──► player events                        │
│        ▲                                              │                                            │
│        │ on top                                       │ JSON IPC                                   │
│   mpv (window "freexht-video", below, idle and black between videos) ◄──┘                          │
│   hardware decoding (V4L2 on the Pi, VA-API on PCs), HDMI sound                                    │
└────────────────────────────────────────────────────────────────────────────────────────────────────┘
   freexht-config     at boot (root): hostname, Wi-Fi, time zone, SSH key from freexht.conf
   freexht-cec@cecN   HDMI-CEC playback device with remote control passthrough (udev)
   freexht-firstboot  once: grow the root file system, SSH host keys
   apt (unattended)   security updates and new FreeXHT versions from apt.mirranet.de
```

- **Interface:** `web/` of FreeX-TV-Apps, unchanged except for the platform `freexht` in `backends.js` and
  `freexht.js` (on-screen keyboard, network, power), which only act on this platform.
- **Playback:** the interface asks the server how to play a title with the box's capabilities, exactly like the
  TV apps, and hands the URL to mpv through the bridge. mpv draws the picture in its own window under the page;
  the seek bar, seek previews, *Skip intro*, *Next episode* and text subtitles are drawn by the interface.
- **Agent:** `freexht-agent` serves the interface on `127.0.0.1` only, starts and watches mpv and cog, sets the
  screen mode, and answers the interface's system calls (network through NetworkManager, restart and switch off
  through logind; polkit allows these for the user `freexht`). After an update it starts the session again as
  soon as no video plays.
- **System:** Debian 13 (trixie); on the Raspberry Pi with the Raspberry Pi archive for the kernel, the firmware
  and its ffmpeg (HEVC decoding). Logs and swap stay in RAM to spare SD cards.

## Updates

The box installs Debian security updates and new versions of the package `freexht` (interface and agent) by
itself (unattended-upgrades, no automatic reboot). The package comes from `apt.mirranet.de` (suite `stable`).
For the Raspberry Pi the repository must list `arm64` in `conf/distributions` (`Architectures: amd64 arm64`, then
`reprepro -b <repo> export`), otherwise the Pi images see no FreeXHT updates.

A new image is only needed for a new Debian release or kernel changes the package cannot bring.

## Building

Jenkins (`Jenkinsfile`): tests, the package, both images, and publishing the package to the APT repository like
freex-media-server (`APT_REPO`, `APT_HOST`, `APT_SUITE`). `IMAGES=false` builds only the package. The build agent
needs Debian 13; the packages to install are listed at the top of the `Jenkinsfile`.

By hand:

```bash
git clone --depth 1 https://github.com/FreeX-Studio/FreeX-TV-Apps.git build/tv-apps
packaging/build-deb.sh 0.1.0 build/tv-apps/web dist          # → dist/freexht_0.1.0_all.deb
image/build-image.sh rpi    0.1.0 dist/freexht_0.1.0_all.deb dist
image/build-image.sh x86_64 0.1.0 dist/freexht_0.1.0_all.deb dist
```

`image/build-image.sh` runs without root (mmdebstrap in a user namespace; the build user needs subordinate ids)
and writes the partitions directly into the image file. Its comment lists the environment variables (mirrors,
sizes).

## Development

| | |
|---|---|
| `src/freexht/` | the agent: `session.py` (the session), `mpv.py` (mpv over JSON IPC), `web.py` (HTTP + WebSocket), `device.py` (boards, codecs, sound, screen), `system.py` (network, power), `bootconfig.py` (freexht-config), `cec.py` (freexht-cec), `config.py` (freexht.conf) |
| `data/` | `bridge.js`, sway and cog configuration, polkit rule, udev rule, PAM, CEC keymap, `freexht.conf` |
| `bin/`, `systemd/` | the programs in `/usr/lib/freexht` and their units |
| `packaging/` | `build-deb.sh`, `apt/publish.sh` |
| `image/` | `build-image.sh`, `customize.sh`, boot files for the Pi (`config.txt`, `cmdline.txt`) and systemd-boot |
| `tests/` | unit tests, `e2e_ui.py`, `image-smoke.sh`, stand-ins for mpv and nmcli |

The agent needs nothing beyond Python's standard library. Without a box:

```bash
python3 -m unittest discover -s tests                       # unit tests (mpv through tests/fake_mpv.py)
PYTHONPATH=src python3 -m freexht.session --no-browser --web ../FreeX-TV-Apps/web --mpv mpv
# then open http://127.0.0.1:7650/ in a browser at 1920 × 1080 (or with --mpv tests/fake_mpv.py)
python3 tests/e2e_ui.py ../FreeX-TV-Apps/web /tmp/shots      # the interface with the remote, end to end (Playwright)
sudo tests/image-smoke.sh ../FreeX-TV-Apps/web /tmp/smoke    # image build and first start in QEMU (Ubuntu as stand-in)
```

On a box (with an SSH key in `freexht.conf`): `journalctl -u freexht` (session, agent, mpv, cog),
`journalctl -u freexht-config`, `systemctl restart freexht`.

## License

GNU Lesser General Public License v3.0, see [LICENSE](LICENSE).
