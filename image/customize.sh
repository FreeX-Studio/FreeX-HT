#!/bin/sh
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
# The last step of the image build, as mmdebstrap customize hook: everything the packages do not
# do themselves. Called by build-image.sh with the root directory and the settings in the
# environment (FREEXHT_TARGET, FREEXHT_BOOT_DIR, ROOT_PARTUUID, BOOT_PARTUUID, IMAGE_DIR,
# SUITE, MIRRANET_APT, MIRRANET_KEY).

set -eu

R="$1"
in_root() { chroot "$R" "$@"; }

echo "customize: $FREEXHT_TARGET in $R"

# --- identity of the box ----------------------------------------------------------------------
echo freexht > "$R/etc/hostname"
cat > "$R/etc/hosts" <<HOSTS
127.0.0.1	localhost
127.0.1.1	freexht
::1		localhost ip6-localhost ip6-loopback
ff02::1		ip6-allnodes
ff02::2		ip6-allrouters
HOSTS
echo 'LANG=C.UTF-8' > "$R/etc/default/locale"
ln -sf /usr/share/zoneinfo/Europe/Berlin "$R/etc/localtime"
echo Europe/Berlin > "$R/etc/timezone"
# Every box gets its own machine id and SSH host keys at the first start.
: > "$R/etc/machine-id"
rm -f "$R/var/lib/dbus/machine-id" "$R"/etc/ssh/ssh_host_*

# --- file systems -------------------------------------------------------------------------------
cat > "$R/etc/fstab" <<FSTAB
# FreeXHT
PARTUUID=$ROOT_PARTUUID  /  ext4  defaults,noatime,commit=60  0  1
PARTUUID=$BOOT_PARTUUID  $FREEXHT_BOOT_DIR  vfat  defaults,noatime,fmask=0022,dmask=0022  0  2
FSTAB
mkdir -p "$R$FREEXHT_BOOT_DIR"

# Small logs and swap in RAM: kind to SD cards, and the Pi 3 has 1 GB only.
mkdir -p "$R/etc/systemd/journald.conf.d"
printf '[Journal]\nSystemMaxUse=48M\nRuntimeMaxUse=24M\n' > "$R/etc/systemd/journald.conf.d/freexht.conf"
if [ -d "$R/usr/lib/systemd/system-generators" ] && [ -e "$R/usr/lib/systemd/system-generators/zram-generator" ]; then
    printf '[zram0]\nzram-size = min(ram / 2, 2048)\ncompression-algorithm = zstd\n' > "$R/etc/systemd/zram-generator.conf"
fi

# --- boot partition ------------------------------------------------------------------------------
case "$FREEXHT_TARGET" in
    rpi)
        install -m 644 "$IMAGE_DIR/rpi/config.txt" "$R/boot/firmware/config.txt"
        sed "s/@ROOT_PARTUUID@/$ROOT_PARTUUID/" "$IMAGE_DIR/rpi/cmdline.txt" > "$R/boot/firmware/cmdline.txt"
        ;;
    efi)
        mkdir -p "$R/boot/efi/loader/entries" "$R/boot/efi/freexht"
        install -m 644 "$IMAGE_DIR/x86_64/loader.conf" "$R/boot/efi/loader/loader.conf"
        for e in freexht freexht-previous; do
            sed "s/@ROOT_PARTUUID@/$ROOT_PARTUUID/" "$IMAGE_DIR/x86_64/$e.conf" > "$R/boot/efi/loader/entries/$e.conf"
        done
        ;;
esac
install -m 644 "$R/usr/share/freexht/freexht.conf.example" "$R$FREEXHT_BOOT_DIR/freexht.conf"
in_root /usr/lib/freexht/freexht-boot-sync
if [ "$FREEXHT_TARGET" = efi ]; then
    # The fallback entry has something to boot from the start.
    cp "$R/boot/efi/freexht/vmlinuz" "$R/boot/efi/freexht/vmlinuz.old"
    cp "$R/boot/efi/freexht/initrd.img" "$R/boot/efi/freexht/initrd.img.old"
    test -f "$R/boot/efi/EFI/BOOT/BOOTX64.EFI" || { echo "customize: systemd-boot is missing" >&2; exit 1; }
fi
if [ "$FREEXHT_TARGET" = rpi ]; then
    for f in kernel8.img kernel_2712.img start4.elf bootcode.bin; do
        test -f "$R/boot/firmware/$f" || { echo "customize: /boot/firmware/$f is missing" >&2; exit 1; }
    done
fi

# --- services ------------------------------------------------------------------------------------
in_root systemctl enable NetworkManager.service freexht.service freexht-config.service freexht-firstboot.service
in_root systemctl set-default graphical.target
# SSH only with a key from freexht.conf (freexht-config switches it on).
in_root systemctl disable ssh.service 2>/dev/null || true
in_root systemctl mask getty@tty1.service

# --- package sources of the box ----------------------------------------------------------------
rm -f "$R/etc/apt/sources.list"
cat > "$R/etc/apt/sources.list.d/debian.sources" <<SOURCES
Types: deb
URIs: http://deb.debian.org/debian
Suites: $SUITE $SUITE-updates
Components: main contrib non-free non-free-firmware
Signed-By: /usr/share/keyrings/debian-archive-keyring.gpg

Types: deb
URIs: http://deb.debian.org/debian-security
Suites: $SUITE-security
Components: main contrib non-free non-free-firmware
Signed-By: /usr/share/keyrings/debian-archive-keyring.gpg
SOURCES
if [ "$FREEXHT_TARGET" = rpi ]; then
    # raspberrypi-archive-keyring 2025.1 ships .pgp, older versions .gpg.
    RPI_KEYRING=/usr/share/keyrings/raspberrypi-archive-keyring.pgp
    [ -f "$R$RPI_KEYRING" ] || RPI_KEYRING=/usr/share/keyrings/raspberrypi-archive-keyring.gpg
    [ -f "$R$RPI_KEYRING" ] || { echo "customize: raspberrypi-archive-keyring has no keyring" >&2; exit 1; }
    cat > "$R/etc/apt/sources.list.d/raspi.sources" <<SOURCES
Types: deb
URIs: http://archive.raspberrypi.com/debian
Suites: $SUITE
Components: main
Signed-By: $RPI_KEYRING
SOURCES
fi
if [ -n "${MIRRANET_APT:-}" ] && [ -s "${MIRRANET_KEY:-}" ]; then
    install -d "$R/etc/apt/keyrings"
    install -m 644 "$MIRRANET_KEY" "$R/etc/apt/keyrings/mirranet.gpg"
    cat > "$R/etc/apt/sources.list.d/mirranet.sources" <<SOURCES
Types: deb
URIs: $MIRRANET_APT
Suites: stable
Components: main
Signed-By: /etc/apt/keyrings/mirranet.gpg
SOURCES
fi

# Security updates and new FreeXHT versions install themselves; no automatic reboot.
cat > "$R/etc/apt/apt.conf.d/20auto-upgrades" <<APT
APT::Periodic::Update-Package-Lists "1";
APT::Periodic::Unattended-Upgrade "1";
APT
cat > "$R/etc/apt/apt.conf.d/52freexht-unattended-upgrades" <<'APT'
// FreeXHT: what installs itself (see /etc/apt/apt.conf.d/50unattended-upgrades for the rest).
Unattended-Upgrade::Origins-Pattern {
    "origin=Debian,codename=${distro_codename}-security,label=Debian-Security";
    "origin=MirraNET,codename=stable";
};
Unattended-Upgrade::Automatic-Reboot "false";
APT

echo "$FREEXHT_VERSION" > "$R/etc/freexht/image-version"
# The boot partition is taken out of the tree by build-image.sh (sync-out) for its own FAT image.
echo "customize: done"
