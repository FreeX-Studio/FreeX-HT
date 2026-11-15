#!/usr/bin/env bash
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
# Builds the Debian package "freexht" (Architecture: all) with dpkg-deb:
#
#   packaging/build-deb.sh <version> <FreeX TV web directory> <output directory>
#
# The web directory is web/ of FreeX-TV-Apps (the interface). Everything else comes from this
# repository: the agent (src/freexht), the scripts (bin/), sway/cog/polkit/udev files (data/) and
# the systemd units (systemd/).

set -euo pipefail

if [ "$#" -ne 3 ]; then
    echo "usage: build-deb.sh <version> <web directory> <output directory>" >&2
    exit 2
fi
VERSION="$1"
WEB="$2"
OUT="$3"
HERE="$(cd "$(dirname "$0")/.." && pwd)"

[ -f "$WEB/index.html" ] && [ -f "$WEB/js/backends.js" ] || { echo "build-deb: $WEB is not the FreeX TV interface" >&2; exit 1; }
grep -q 'freexht' "$WEB/js/backends.js" || { echo "build-deb: $WEB has no FreeXHT platform (FreeX-TV-Apps too old)" >&2; exit 1; }

PKG="$(mktemp -d)"
trap 'rm -rf "$PKG"' EXIT
umask 022
chmod 755 "$PKG"

lib="$PKG/usr/lib/freexht"
install -d "$lib/freexht" "$PKG/usr/share/freexht" "$PKG/usr/lib/systemd/system" "$PKG/usr/lib/udev/rules.d" \
    "$PKG/usr/share/polkit-1/rules.d" "$PKG/etc/pam.d" "$PKG/etc/rc_keymaps" "$PKG/etc/freexht/sway.d" \
    "$PKG/etc/kernel/postinst.d" "$PKG/etc/initramfs/post-update.d" "$PKG/usr/share/doc/freexht" "$PKG/DEBIAN" \
    "$PKG/usr/lib/NetworkManager/conf.d"

install -m 644 "$HERE"/src/freexht/*.py "$lib/freexht/"
for b in freexht-agent freexht-config freexht-cec freexht-firstboot freexht-boot-sync; do
    install -m 755 "$HERE/bin/$b" "$lib/$b"
done
install -m 644 "$HERE/data/bridge.js" "$HERE/data/sway.conf" "$HERE/data/cog.ini" "$lib/"
install -m 644 "$HERE/data/freexht.conf" "$PKG/usr/share/freexht/freexht.conf.example"
echo "$VERSION" > "$lib/VERSION"
cp -r "$WEB" "$PKG/usr/share/freexht/web"
find "$PKG/usr/share/freexht/web" -type d -exec chmod 755 {} + -o -type f -exec chmod 644 {} +

install -m 644 "$HERE"/systemd/*.service "$PKG/usr/lib/systemd/system/"
install -m 644 "$HERE/data/70-freexht-cec.rules" "$PKG/usr/lib/udev/rules.d/"
install -m 644 "$HERE/data/50-freexht.rules" "$PKG/usr/share/polkit-1/rules.d/"
install -m 644 "$HERE/data/pam.freexht" "$PKG/etc/pam.d/freexht"
install -m 644 "$HERE/data/NetworkManager.conf" "$PKG/usr/lib/NetworkManager/conf.d/90-freexht.conf"
install -m 644 "$HERE/data/freexht-cec.toml" "$PKG/etc/rc_keymaps/freexht-cec.toml"
ln -s /usr/lib/freexht/freexht-boot-sync "$PKG/etc/kernel/postinst.d/zz-freexht-boot"
ln -s /usr/lib/freexht/freexht-boot-sync "$PKG/etc/initramfs/post-update.d/zz-freexht-boot"
install -m 644 "$HERE/LICENSE" "$PKG/usr/share/doc/freexht/copyright"

cat > "$PKG/DEBIAN/control" <<CONTROL
Package: freexht
Version: $VERSION
Architecture: all
Maintainer: Niklas Linz <niklas.linz@mirranet.de>
Section: video
Priority: optional
Homepage: https://github.com/FreeX-Studio/FreeX-HT
Depends: python3 (>= 3.9), sway, cog, mpv, network-manager, polkitd, libpam-systemd, dbus,
 v4l-utils, ir-keytable, alsa-utils, iproute2, iw, cloud-guest-utils, e2fsprogs, fonts-dejavu-core
Recommends: openssh-server, wireless-regdb
Description: FreeXHT - the FreeX TV interface as the system of a box on the TV
 Turns a Raspberry Pi or a small PC into a FreeX player: it boots straight into
 the FreeX TV interface (cog, WPE WebKit) and plays with mpv, with hardware
 decoding, HDMI-CEC remote control, an on-screen keyboard, Wi-Fi set-up and
 settings on the boot partition (freexht.conf).
CONTROL
printf '%s\n' /etc/pam.d/freexht /etc/rc_keymaps/freexht-cec.toml > "$PKG/DEBIAN/conffiles"

cat > "$PKG/DEBIAN/postinst" <<'POSTINST'
#!/bin/sh
set -e
if [ "$1" = "configure" ]; then
    install -d -m 755 /var/lib/freexht
    if ! getent passwd freexht >/dev/null; then
        useradd --system --create-home --home-dir /var/lib/freexht/home --shell /bin/bash \
            --comment "FreeXHT interface" freexht
    fi
    for g in video render audio input netdev; do
        if getent group "$g" >/dev/null; then usermod -a -G "$g" freexht; fi
    done
    # The TV remote (HDMI-CEC) as keys of the interface.
    if [ -f /etc/rc_maps.cfg ] && ! grep -q 'freexht-cec' /etc/rc_maps.cfg; then
        sed -i '1i *\trc-cec\t/etc/rc_keymaps/freexht-cec.toml' /etc/rc_maps.cfg
    fi
    if [ -d /run/systemd/system ]; then
        systemctl daemon-reload || true
    fi
    systemctl enable freexht.service freexht-config.service freexht-firstboot.service >/dev/null 2>&1 || true
    systemctl set-default graphical.target >/dev/null 2>&1 || true
    # An update brings a new interface: the running agent notices the new version and starts
    # the session again as soon as no video plays (freexht.session, watch_updates).
    if [ -d /run/systemd/system ]; then
        udevadm control --reload-rules 2>/dev/null || true
    fi
fi
exit 0
POSTINST
cat > "$PKG/DEBIAN/prerm" <<'PRERM'
#!/bin/sh
set -e
if [ "$1" = "remove" ]; then
    systemctl disable freexht.service freexht-config.service freexht-firstboot.service >/dev/null 2>&1 || true
fi
exit 0
PRERM
chmod 755 "$PKG/DEBIAN/postinst" "$PKG/DEBIAN/prerm"

mkdir -p "$OUT"
dpkg-deb --root-owner-group -Zxz --build "$PKG" "$OUT/freexht_${VERSION}_all.deb" >/dev/null
echo "build-deb: $OUT/freexht_${VERSION}_all.deb"
