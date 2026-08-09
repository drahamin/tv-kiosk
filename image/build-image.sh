#!/bin/bash
set -euo pipefail

if [[ ${EUID} -ne 0 ]]; then
  echo "Run as root: sudo ./image/build-image.sh" >&2
  exit 1
fi

ROOT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
LOCAL_CONFIG="$ROOT_DIR/image/config.local.env"
if [[ -f "$LOCAL_CONFIG" ]]; then
  set -a
  source "$LOCAL_CONFIG"
  set +a
fi

: "${WIFI_SSID:=Home}"
: "${WIFI_PSK:?Set WIFI_PSK in image/config.local.env}"
: "${WIFI_COUNTRY:=US}"
: "${KIOSK_PORT:=8999}"
: "${KIOSK_UPDATE_BRANCH:=main}"
: "${KIOSK_REPO_URL:=}"
: "${RPI_OS_IMAGE_URL:=}"

for command in curl losetup mount umount xz; do
  command -v "$command" >/dev/null || { echo "Missing required command: $command" >&2; exit 1; }
done

if [[ -z "$RPI_OS_IMAGE_URL" ]]; then
  RPI_OS_IMAGE_URL=https://downloads.raspberrypi.com/raspios_lite_armhf_latest
fi

WORK_DIR=$(mktemp -d)
DIST_DIR="$ROOT_DIR/dist"
BOOT_LOOP=""
ROOT_LOOP=""
cleanup() {
  set +e
  mountpoint -q "$WORK_DIR/root" && umount "$WORK_DIR/root"
  mountpoint -q "$WORK_DIR/boot" && umount "$WORK_DIR/boot"
  [[ -n "$ROOT_LOOP" ]] && losetup -d "$ROOT_LOOP"
  [[ -n "$BOOT_LOOP" ]] && losetup -d "$BOOT_LOOP"
  rm -rf "$WORK_DIR"
}
trap cleanup EXIT

mkdir -p "$DIST_DIR" "$WORK_DIR/root" "$WORK_DIR/boot"
curl -fL "$RPI_OS_IMAGE_URL" -o "$WORK_DIR/os.img.xz"
xz -dc "$WORK_DIR/os.img.xz" > "$WORK_DIR/os.img"
BASE_IMAGE="$WORK_DIR/os.img"
OUTPUT_IMAGE="$DIST_DIR/rahamin-tv-kiosk-rpi3.img"
cp "$BASE_IMAGE" "$OUTPUT_IMAGE"

read -r BOOT_START BOOT_SECTORS < <(partx --raw --noheadings --output START,SECTORS --nr 1 "$OUTPUT_IMAGE")
read -r ROOT_START ROOT_SECTORS < <(partx --raw --noheadings --output START,SECTORS --nr 2 "$OUTPUT_IMAGE")
BOOT_LOOP=$(losetup --find --show --offset "$((BOOT_START * 512))" --sizelimit "$((BOOT_SECTORS * 512))" "$OUTPUT_IMAGE")
ROOT_LOOP=$(losetup --find --show --offset "$((ROOT_START * 512))" --sizelimit "$((ROOT_SECTORS * 512))" "$OUTPUT_IMAGE")
mount "$ROOT_LOOP" "$WORK_DIR/root"
mount "$BOOT_LOOP" "$WORK_DIR/boot"

mkdir -p "$WORK_DIR/root/opt/tv-kiosk-bootstrap" "$WORK_DIR/root/etc/NetworkManager/system-connections" "$WORK_DIR/root/etc/systemd/system/multi-user.target.wants"
for item in app config scripts session systemd install.sh README.md; do
  cp -a "$ROOT_DIR/$item" "$WORK_DIR/root/opt/tv-kiosk-bootstrap/"
done

escaped_ssid=${WIFI_SSID//\"/\\\"}
escaped_psk=${WIFI_PSK//\"/\\\"}
cat > "$WORK_DIR/root/etc/NetworkManager/system-connections/Home.nmconnection" <<EOF
[connection]
id=$escaped_ssid
type=wifi
autoconnect=true

[wifi]
mode=infrastructure
ssid=$escaped_ssid

[wifi-security]
key-mgmt=wpa-psk
psk=$escaped_psk

[ipv4]
method=auto

[ipv6]
method=auto
EOF
chmod 600 "$WORK_DIR/root/etc/NetworkManager/system-connections/Home.nmconnection"

cat > "$WORK_DIR/root/usr/local/sbin/tv-kiosk-firstboot" <<EOF
#!/bin/sh
set -eu
export KIOSK_PORT='$KIOSK_PORT'
export KIOSK_UPDATE_BRANCH='$KIOSK_UPDATE_BRANCH'
if [ -n '$KIOSK_REPO_URL' ]; then
  apt-get update
  DEBIAN_FRONTEND=noninteractive apt-get install -y git
  rm -rf /opt/tv-kiosk
  git clone --branch '$KIOSK_UPDATE_BRANCH' --single-branch '$KIOSK_REPO_URL' /opt/tv-kiosk
  /opt/tv-kiosk/install.sh
else
  /opt/tv-kiosk-bootstrap/install.sh
fi
systemctl disable tv-kiosk-firstboot.service
reboot
EOF
chmod 755 "$WORK_DIR/root/usr/local/sbin/tv-kiosk-firstboot"

cat > "$WORK_DIR/root/etc/systemd/system/tv-kiosk-firstboot.service" <<EOF
[Unit]
Description=Install Rahamin TV kiosk on first boot
Wants=network-online.target
After=network-online.target
ConditionPathExists=/opt/tv-kiosk-bootstrap/install.sh

[Service]
Type=oneshot
ExecStart=/usr/local/sbin/tv-kiosk-firstboot
RemainAfterExit=yes

[Install]
WantedBy=multi-user.target
EOF
ln -sf ../tv-kiosk-firstboot.service "$WORK_DIR/root/etc/systemd/system/multi-user.target.wants/tv-kiosk-firstboot.service"

printf 'REGDOMAIN=%s\n' "$WIFI_COUNTRY" > "$WORK_DIR/root/etc/default/crda"
mkdir -p "$WORK_DIR/root/etc/wpa_supplicant"
printf 'country=%s\n' "$WIFI_COUNTRY" > "$WORK_DIR/root/etc/wpa_supplicant/wpa_supplicant.conf"
sync
umount "$WORK_DIR/root"
umount "$WORK_DIR/boot"
losetup -d "$ROOT_LOOP"
losetup -d "$BOOT_LOOP"
ROOT_LOOP=""
BOOT_LOOP=""

xz -T0 -f -k "$OUTPUT_IMAGE"
echo "Created $OUTPUT_IMAGE and $OUTPUT_IMAGE.xz"
