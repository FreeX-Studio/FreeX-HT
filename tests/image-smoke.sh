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
# Smoke test of the image build and the first start, for machines without Debian mirrors: builds
# the x86_64 image with Ubuntu 24.04 in place of Debian (same tools, same layout; Ubuntu has no
# cog, so the package is built without that dependency) and boots it in QEMU with UEFI. Checks:
# systemd-boot, the root partition by PARTUUID, growing to the disk, freexht.conf (hostname, SSH
# key), and the session (sway, agent, mpv) on tty1.
#
#   tests/image-smoke.sh <FreeX-TV-Apps/web> <work directory>
#
# Needs root (or subuids), qemu-system-x86, ovmf, ubuntu-keyring, openssh-client, mtools. Without KVM the
# first start takes a few minutes.

set -euo pipefail
WEB="$(realpath "$1")"
OUT="$(realpath -m "$2")"
HERE="$(cd "$(dirname "$0")/.." && pwd)"
VERSION=0.0.0~smoke
mkdir -p "$OUT/deb"

"$HERE/packaging/build-deb.sh" "$VERSION" "$WEB" "$OUT/deb"
rm -rf "$OUT/deb/x" && dpkg-deb -R "$OUT/deb/freexht_${VERSION}_all.deb" "$OUT/deb/x"
sed -i 's/ cog,//' "$OUT/deb/x/DEBIAN/control"
dpkg-deb --root-owner-group -b "$OUT/deb/x" "$OUT/deb/freexht-smoke.deb" >/dev/null

SUITE=noble DEBIAN_MIRROR=http://archive.ubuntu.com/ubuntu SECURITY_MIRROR=http://archive.ubuntu.com/ubuntu \
COMPONENTS="main restricted universe" ARCHIVE_KEYRING=/usr/share/keyrings/ubuntu-archive-keyring.gpg MIRRANET_APT= \
PACKAGES="systemd-sysv systemd-timesyncd udev dbus libpam-systemd polkitd network-manager iproute2 kmod \
initramfs-tools cloud-guest-utils e2fsprogs fdisk dosfstools linux-image-virtual systemd-boot-efi libgl1-mesa-dri \
libegl-mesa0 libgles2 libgbm1 openssh-server ca-certificates tzdata procps less" \
WORK_DIR="$OUT/work" "$HERE/image/build-image.sh" x86_64 "$VERSION" "$OUT/deb/freexht-smoke.deb" "$OUT"

# --- first start in QEMU ---------------------------------------------------------------------
IMG="$OUT/disk.img"
xz -dc "$OUT/FreeXHT-$VERSION-x86_64.img.xz" > "$IMG"
rm -f "$OUT/key" "$OUT/key.pub" && ssh-keygen -q -t ed25519 -N "" -f "$OUT/key"
mcopy -i "$IMG@@4M" ::/freexht.conf "$OUT/freexht.conf"
sed -i "s|^ssh_authorized_key =.*|ssh_authorized_key = $(cat "$OUT/key.pub")|; s|^hostname = .*|hostname = smoketest|;
        s|^server =.*|server = 10.0.2.2:32500|; s|^wifi_ssid =.*|wifi_ssid = TestNet|; s|^wifi_password =.*|wifi_password = secret123|" \
    "$OUT/freexht.conf"
mcopy -o -i "$IMG@@4M" "$OUT/freexht.conf" ::/freexht.conf
truncate -s +2G "$IMG"  # the first start must grow the root file system
cp /usr/share/OVMF/OVMF_VARS_4M.fd "$OUT/vars.fd"
ACCEL=(); [ -w /dev/kvm ] && ACCEL=(-enable-kvm -cpu host)
qemu-system-x86_64 "${ACCEL[@]}" -machine q35 -m 2048 -smp 2 \
    -drive if=pflash,format=raw,readonly=on,file=/usr/share/OVMF/OVMF_CODE_4M.fd -drive if=pflash,format=raw,file="$OUT/vars.fd" \
    -drive file="$IMG",format=raw,if=virtio -device virtio-gpu-pci -netdev user,id=n0,hostfwd=tcp:127.0.0.1:2222-:22 \
    -device virtio-net-pci,netdev=n0 -display none -daemonize -pidfile "$OUT/qemu.pid"
trap 'kill "$(cat "$OUT/qemu.pid")" 2>/dev/null || true' EXIT
SSH=(ssh -q -i "$OUT/key" -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -o ConnectTimeout=5 -p 2222 root@127.0.0.1)
for _ in $(seq 1 60); do "${SSH[@]}" true 2>/dev/null && break; sleep 10; done
sleep 20  # the session starts after SSH
scp -q -i "$OUT/key" -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -P 2222 "$HERE/tests/vm_check.py" root@127.0.0.1:/tmp/
"${SSH[@]}" sh -eu <<'CHECK'
test "$(hostname)" = smoketest
systemctl is-active freexht freexht-config freexht-firstboot NetworkManager ssh
test "$(systemctl --failed --no-legend | wc -l)" = 0
grep -q '^wifi_password =$' /boot/efi/freexht.conf                     # moved onto the box
grep -q 'psk=secret123' /etc/NetworkManager/system-connections/freexht-wifi.nmconnection
test "$(stat -c %a /etc/NetworkManager/system-connections/freexht-wifi.nmconnection)" = 600
test "$(df --output=size -BG / | tail -1 | tr -dc 0-9)" -ge 3            # grown to the disk
loginctl list-sessions --no-legend | grep -q 'freexht seat0 tty1'
pgrep -u freexht -x sway && pgrep -u freexht -x mpv && pgrep -u freexht -f freexht-agent
runuser -u freexht -- busctl call org.freedesktop.login1 /org/freedesktop/login1 org.freedesktop.login1.Manager CanReboot | grep -q yes
runuser -u freexht -- nmcli -t general permissions | grep -q 'settings.modify.system:yes'
python3 /tmp/vm_check.py
CHECK
echo "image-smoke: all checks passed"
