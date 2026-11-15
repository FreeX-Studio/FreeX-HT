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
# Builds a FreeXHT system image, without root (mmdebstrap in a user namespace):
#
#   image/build-image.sh <rpi|x86_64> <version> <freexht_*.deb> <output directory>
#
#   rpi     arm64, one image for the Raspberry Pi 3, 4 and 5 (Debian + the Raspberry Pi archive for
#           kernel, firmware and the ffmpeg with HEVC decoding); MBR, FAT boot partition + ext4
#   x86_64  amd64, UEFI PCs (systemd-boot); GPT, EFI system partition + ext4
#
# Output: FreeXHT-<version>-<target>.img.xz and its .sha256. The root file system grows to the whole
# SD card or disk at the first start.
#
# Build host: Debian 13 (or newer) with
#   sudo apt install mmdebstrap uidmap e2fsprogs dosfstools mtools fdisk xz-utils gpg curl \
#       qemu-user-static binfmt-support arch-test
# and subordinate ids for the building user (/etc/subuid, /etc/subgid; `sudo usermod --add-subuids
# 100000-165535 --add-subgids 100000-165535 jenkins`). As root it works too.
#
# Environment (all optional):
#   SUITE (trixie), DEBIAN_MIRROR, SECURITY_MIRROR, RPI_MIRROR   where packages come from (a local
#                         mirror or apt-cacher-ng speeds up builds; the image itself uses the official ones)
#   MIRRANET_APT          https://apt.mirranet.de; the box updates FreeXHT from there ("" = not added)
#   MIRRANET_KEY          its signing key (file); default: downloaded from $MIRRANET_APT/mirranet.gpg
#   BOOT_SIZE_MB          512
#   MMDEBSTRAP_MODE       auto (unshare as user, root as root)
#   WORK_DIR              build directory (default: a new one under ${TMPDIR:-/tmp}, removed afterwards)
#   PACKAGES, COMPONENTS, ARCHIVE_KEYRING   replace the package list, the archive components and the
#                         archive key (tests with another distribution, see tests/image-smoke.sh)

set -euo pipefail

if [ "$#" -ne 4 ]; then
    echo "usage: build-image.sh <rpi|x86_64> <version> <freexht_*.deb> <output directory>" >&2
    exit 2
fi
TARGET_NAME="$1"
VERSION="$2"
DEB="$(realpath "$3")"
OUT="$4"
HERE="$(cd "$(dirname "$0")" && pwd)"

SUITE="${SUITE:-trixie}"
DEBIAN_MIRROR="${DEBIAN_MIRROR:-http://deb.debian.org/debian}"
SECURITY_MIRROR="${SECURITY_MIRROR:-http://deb.debian.org/debian-security}"
RPI_MIRROR="${RPI_MIRROR:-http://archive.raspberrypi.com/debian}"
MIRRANET_APT="${MIRRANET_APT-https://apt.mirranet.de}"
BOOT_SIZE_MB="${BOOT_SIZE_MB:-512}"
MMDEBSTRAP_MODE="${MMDEBSTRAP_MODE:-auto}"

log() { echo "build-image: $*"; }
die() { echo "build-image: $*" >&2; exit 1; }

for tool in mmdebstrap mke2fs mkfs.vfat mcopy sfdisk xz gpg dpkg-deb; do
    command -v "$tool" >/dev/null || die "$tool is missing (see the comment at the top of this script)"
done
[ -f "$DEB" ] || die "$DEB not found"

# Packages of every image: the session (freexht pulls sway, cog, mpv, NetworkManager, ...), the
# graphics drivers, updates and what a box needs to run on its own.
COMMON=(
    systemd-sysv systemd-timesyncd systemd-zram-generator udev dbus libpam-systemd polkitd
    network-manager wpasupplicant iw wireless-regdb openssh-server ca-certificates tzdata kmod
    iproute2 iputils-ping procps less nano initramfs-tools cloud-guest-utils e2fsprogs dosfstools fdisk
    libgl1-mesa-dri libegl-mesa0 libgles2 libgbm1 unattended-upgrades python3-apt
    freexht
)

case "$TARGET_NAME" in
    rpi)
        TARGET=rpi; ARCH=arm64; BOOT_DIR=/boot/firmware
        PKGS=("${COMMON[@]}" raspberrypi-archive-keyring raspi-firmware linux-image-rpi-v8 linux-image-rpi-2712
              firmware-brcm80211)
        ;;
    x86_64)
        TARGET=efi; ARCH=amd64; BOOT_DIR=/boot/efi
        PKGS=("${COMMON[@]}" linux-image-amd64 systemd-boot-efi firmware-linux-free firmware-misc-nonfree
              firmware-intel-graphics firmware-amd-graphics firmware-realtek firmware-iwlwifi
              intel-media-va-driver-non-free mesa-va-drivers)
        ;;
    *) die "unknown target $TARGET_NAME (rpi or x86_64)" ;;
esac
if [ -n "${PACKAGES:-}" ]; then
    read -r -a PKGS <<< "$PACKAGES"
    PKGS+=(freexht)
fi

if [ -n "${WORK_DIR:-}" ]; then
    WORK="$WORK_DIR"
    mkdir -p "$WORK"
else
    WORK="$(mktemp -d "${TMPDIR:-/tmp}/freexht-image.XXXXXX")"
    trap 'rm -rf "$WORK"' EXIT
fi
rm -rf "$WORK/keys" "$WORK/bootfs" "$WORK/localrepo" "$WORK/rootfs.tar" "$WORK/root.img" "$WORK/boot.img" "$WORK/disk.img"
mkdir -p "$WORK/keys" "$WORK/localrepo" "$OUT"
NAME="FreeXHT-$VERSION-$TARGET_NAME"

# --- signing keys ----------------------------------------------------------------------------
cp "${ARCHIVE_KEYRING:-/usr/share/keyrings/debian-archive-keyring.gpg}" "$WORK/keys/" 2>/dev/null \
    || die "debian-archive-keyring is missing (sudo apt install debian-archive-keyring)"
if [ "$TARGET" = rpi ]; then
    if [ -n "${RPI_KEY:-}" ]; then
        gpg --dearmor < "$RPI_KEY" > "$WORK/keys/raspberrypi.gpg" 2>/dev/null || cp "$RPI_KEY" "$WORK/keys/raspberrypi.gpg"
    else
        curl -fsSL "$RPI_MIRROR/raspberrypi.gpg.key" | gpg --dearmor > "$WORK/keys/raspberrypi.gpg" \
            || die "cannot load the key of the Raspberry Pi archive (set RPI_KEY to a file)"
    fi
fi
MIRRANET_KEY_FILE=""
if [ -n "$MIRRANET_APT" ]; then
    MIRRANET_KEY_FILE="$WORK/mirranet.gpg"
    if [ -n "${MIRRANET_KEY:-}" ]; then
        cp "$MIRRANET_KEY" "$MIRRANET_KEY_FILE"
    elif ! curl -fsSL "$MIRRANET_APT/mirranet.gpg" -o "$MIRRANET_KEY_FILE"; then
        log "warning: no key from $MIRRANET_APT, the image gets no MirraNET repository"
        MIRRANET_KEY_FILE=""
    fi
    # An armored key works as .gpg only after dearmoring.
    if [ -n "$MIRRANET_KEY_FILE" ] && grep -q "BEGIN PGP" "$MIRRANET_KEY_FILE" 2>/dev/null; then
        gpg --dearmor < "$MIRRANET_KEY_FILE" > "$MIRRANET_KEY_FILE.bin" && mv "$MIRRANET_KEY_FILE.bin" "$MIRRANET_KEY_FILE"
    fi
fi

# --- the freexht package as a local repository (apt resolves its dependencies) --------------
cp "$DEB" "$WORK/localrepo/freexht.deb"
{
    dpkg-deb -f "$WORK/localrepo/freexht.deb"
    echo "Filename: ./freexht.deb"
    echo "Size: $(stat -c %s "$WORK/localrepo/freexht.deb")"
    echo "SHA256: $(sha256sum "$WORK/localrepo/freexht.deb" | cut -d' ' -f1)"
    echo
} > "$WORK/localrepo/Packages"

# --- partition ids ---------------------------------------------------------------------------
if [ "$TARGET" = rpi ]; then
    DISK_ID="$(od -An -N4 -tx4 /dev/urandom | tr -d ' \n')"
    BOOT_PARTUUID="$DISK_ID-01"
    ROOT_PARTUUID="$DISK_ID-02"
else
    BOOT_PARTUUID="$(cat /proc/sys/kernel/random/uuid)"
    ROOT_PARTUUID="$(cat /proc/sys/kernel/random/uuid)"
fi

# --- root file system ------------------------------------------------------------------------
COMPONENTS="${COMPONENTS:-main contrib non-free non-free-firmware}"
SOURCES=("deb $DEBIAN_MIRROR $SUITE $COMPONENTS"
         "deb $DEBIAN_MIRROR $SUITE-updates $COMPONENTS"
         "deb $SECURITY_MIRROR $SUITE-security $COMPONENTS")
[ "$TARGET" = rpi ] && SOURCES+=("deb $RPI_MIRROR $SUITE main")
SOURCES+=("deb [trusted=yes] copy://$WORK/localrepo ./")

export FREEXHT_TARGET="$TARGET" FREEXHT_BOOT_DIR="$BOOT_DIR" FREEXHT_VERSION="$VERSION" ROOT_PARTUUID BOOT_PARTUUID \
    IMAGE_DIR="$HERE" BOOTFS_OUT="$WORK/bootfs" SUITE MIRRANET_APT MIRRANET_KEY="$MIRRANET_KEY_FILE"

log "$NAME: root file system ($ARCH, $SUITE)"
mmdebstrap \
    --mode="$MMDEBSTRAP_MODE" \
    --arch="$ARCH" \
    --variant=minbase \
    --keyring="$WORK/keys" \
    --aptopt='APT::Install-Recommends "false"' \
    --dpkgopt='path-exclude=/usr/share/doc/*' --dpkgopt='path-include=/usr/share/doc/*/copyright' \
    --dpkgopt='path-exclude=/usr/share/man/*' --dpkgopt='path-exclude=/usr/share/locale/*' \
    --dpkgopt='path-include=/usr/share/locale/locale.alias' \
    --include="$(IFS=,; echo "${PKGS[*]}")" \
    --setup-hook='mkdir -p "$1/etc/freexht" "$1'"$BOOT_DIR"'" && echo '"$TARGET"' > "$1/etc/freexht/target"' \
    --setup-hook='mkdir -p "$1/etc/initramfs-tools/conf.d" && echo MODULES=most > "$1/etc/initramfs-tools/conf.d/freexht.conf"' \
    --setup-hook='if [ -f "'"$WORK"'/keys/raspberrypi.gpg" ]; then mkdir -p "$1/usr/share/keyrings" && cp "'"$WORK"'/keys/raspberrypi.gpg" "$1/usr/share/keyrings/raspberrypi-archive-keyring.gpg"; fi' \
    --customize-hook="\"$HERE/customize.sh\" \"\$1\"" \
    "$SUITE" "$WORK/rootfs.tar" "${SOURCES[@]}"

# --- file system images ----------------------------------------------------------------------
tar_mb=$(( $(stat -c %s "$WORK/rootfs.tar") / 1048576 ))
ROOT_MB=$(( tar_mb * 13 / 10 + 400 ))
log "$NAME: ext4 root file system, $ROOT_MB MiB (contents $tar_mb MiB)"
if ! mke2fs -q -t ext4 -L freexht-root -E root_owner=0:0 -d "$WORK/rootfs.tar" "$WORK/root.img" "${ROOT_MB}M" 2>"$WORK/mke2fs.log"; then
    # mke2fs before 1.47.1 (or without libarchive) cannot read a tarball: unpack it (needs root).
    [ "$(id -u)" = 0 ] || { cat "$WORK/mke2fs.log" >&2; die "mke2fs cannot read the tarball; e2fsprogs >= 1.47.1 or root needed"; }
    log "mke2fs reads no tarballs here, unpacking it"
    rm -f "$WORK/root.img"
    mkdir -p "$WORK/rootdir"
    tar -C "$WORK/rootdir" --numeric-owner --xattrs --xattrs-include='*' -xpf "$WORK/rootfs.tar"
    mke2fs -q -t ext4 -L freexht-root -d "$WORK/rootdir" "$WORK/root.img" "${ROOT_MB}M"
    rm -rf "$WORK/rootdir"
fi
rm -f "$WORK/rootfs.tar"

log "$NAME: FAT boot partition, $BOOT_SIZE_MB MiB"
mkfs.vfat -F 32 -n FREEXHT -C "$WORK/boot.img" $(( BOOT_SIZE_MB * 1024 )) >/dev/null
mcopy -s -i "$WORK/boot.img" "$WORK"/bootfs/* ::/

# --- disk image --------------------------------------------------------------------------------
START=4  # MiB before the first partition
TOTAL_MB=$(( START + BOOT_SIZE_MB + ROOT_MB + 1 ))
truncate -s "${TOTAL_MB}M" "$WORK/disk.img"
s() { echo $(( $1 * 2048 )); }  # MiB → 512-byte sectors
if [ "$TARGET" = rpi ]; then
    sfdisk -q "$WORK/disk.img" <<PARTS
label: dos
label-id: 0x$DISK_ID
unit: sectors
start=$(s $START), size=$(s "$BOOT_SIZE_MB"), type=c, bootable
start=$(s $(( START + BOOT_SIZE_MB ))), size=$(s "$ROOT_MB"), type=83
PARTS
else
    sfdisk -q "$WORK/disk.img" <<PARTS
label: gpt
unit: sectors
start=$(s $START), size=$(s "$BOOT_SIZE_MB"), type=C12A7328-F81F-11D2-BA4B-00A0C93EC93B, uuid=$BOOT_PARTUUID, name="EFI"
start=$(s $(( START + BOOT_SIZE_MB ))), size=$(s "$ROOT_MB"), type=4F68BCE3-E8CD-4DB1-96E7-FBCAF984B709, uuid=$ROOT_PARTUUID, name="freexht-root"
PARTS
fi
dd if="$WORK/boot.img" of="$WORK/disk.img" bs=1M seek="$START" conv=notrunc,sparse status=none
dd if="$WORK/root.img" of="$WORK/disk.img" bs=1M seek=$(( START + BOOT_SIZE_MB )) conv=notrunc,sparse status=none
rm -f "$WORK/boot.img" "$WORK/root.img"

log "$NAME: compressing"
xz -T0 -6 -c "$WORK/disk.img" > "$OUT/$NAME.img.xz"
rm -f "$WORK/disk.img"
(cd "$OUT" && sha256sum "$NAME.img.xz" > "$NAME.img.xz.sha256")
log "$OUT/$NAME.img.xz ($(du -h "$OUT/$NAME.img.xz" | cut -f1))"
