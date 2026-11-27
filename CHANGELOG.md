# Changelog

## FreeXHT 0.1.2

- The system images are built by GitHub Actions too (Raspberry Pi on an arm64 runner, x86_64), for every
  release and by hand (*Actions → Build → Run workflow*, *images*); a release carries the package and both images

## FreeXHT 0.1.1

- Raspberry Pi 3: the screen runs in 720p by default (`display_mode` changes it) and the interface without
  transitions or animations (`lite_ui`); video and the page on top were too much for its GPU in 1080p
- mpv uses its `fast` profile on every Raspberry Pi (simple scaling, no dithering)
- The journal shows for every video what plays how (codec, size, frame rate, hardware decoding or software,
  output) and, at the end, the frames dropped by the decoder and the output

## FreeXHT 0.1.0

First version of FreeXHT, the FreeX system image for the TV:

- **Images** for the Raspberry Pi 3, 4 and 5 (one arm64 image) and for x86_64 PCs (UEFI), based on Debian 13
  (trixie); on the Pi with the Raspberry Pi archive for kernel, firmware and HEVC decoding. Built without root by
  `image/build-image.sh` (mmdebstrap); the root file system grows to the whole card at the first start
- **Session:** sway on tty1 with the FreeX TV interface (FreeX-TV-Apps, in cog/WPE WebKit) transparent on top of
  mpv; `freexht-agent` serves the interface locally, drives mpv over its IPC through the same bridge as the Android
  app (`window.FreeXNative`), keeps mpv and the browser running and restarts the session after an update once no
  video plays
- **Playback** with hardware decoding per board (V4L2 on the Pi, VA-API on PCs), HDMI sound with optional
  passthrough, capabilities per board for the server's playback decision
- **Remote control** over HDMI-CEC (playback device "FreeXHT", switches the TV to its input) and USB keyboards
- **freexht.conf** on the boot partition: Wi-Fi (the password is moved onto the box), server address, hostname,
  time zone, SSH key, screen mode, sound and mpv settings
- **Updates** through unattended-upgrades: Debian security updates and new FreeXHT packages from apt.mirranet.de
- Jenkinsfile: tests, the package, both images, publishing to the APT repository
