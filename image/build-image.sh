#!/bin/bash
set -euo pipefail

if [[ ${EUID} -ne 0 ]]; then
  echo "Run as root: sudo ./image/build-image.sh" >&2
  exit 1
fi

ROOT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
LOCAL_CONFIG="$ROOT_DIR/image/config.local.env"
REQUESTED_KIOSK_PROFILE=${KIOSK_PROFILE-}
REQUESTED_KIOSK_VARIANT=${KIOSK_VARIANT-}
if [[ -f "$LOCAL_CONFIG" ]]; then
  set -a
  source "$LOCAL_CONFIG"
  set +a
fi
[[ -z "$REQUESTED_KIOSK_PROFILE" ]] || KIOSK_PROFILE=$REQUESTED_KIOSK_PROFILE
[[ -z "$REQUESTED_KIOSK_VARIANT" ]] || KIOSK_VARIANT=$REQUESTED_KIOSK_VARIANT

: "${WIFI_PRIMARY_SSID:=${WIFI_SSID:-Home}}"
: "${WIFI_SECONDARY_SSID:=Baiamonte}"
: "${WIFI_PSK:?Set WIFI_PSK in image/config.local.env}"
: "${WIFI_COUNTRY:=US}"
: "${KIOSK_PORT:=8999}"
: "${KIOSK_UPDATE_BRANCH:=main}"
: "${KIOSK_PROFILE:=auto}"
: "${KIOSK_VARIANT:=auto}"
: "${BAIAMONTE_TV_URL:=http://192.168.0.10:8101}"
: "${KIOSK_REPO_URL:=}"
: "${RPI_OS_IMAGE_URL:=}"
: "${SSH_PUBLIC_KEY_FILE:=$ROOT_DIR/image/kiosk_admin_ed25519.pub}"

if [[ "$SSH_PUBLIC_KEY_FILE" != /* ]]; then
  SSH_PUBLIC_KEY_FILE="$ROOT_DIR/$SSH_PUBLIC_KEY_FILE"
fi
[[ -s "$SSH_PUBLIC_KEY_FILE" ]] || { echo "Missing SSH public key: $SSH_PUBLIC_KEY_FILE" >&2; exit 1; }
[[ "$WIFI_COUNTRY" =~ ^[A-Z]{2}$ ]] || { echo "WIFI_COUNTRY must be a two-letter uppercase country code" >&2; exit 1; }
[[ "$KIOSK_PROFILE" =~ ^(auto|zero|multi)$ ]] || { echo "KIOSK_PROFILE must be auto, zero, or multi" >&2; exit 1; }
[[ "$KIOSK_VARIANT" =~ ^(auto|baiamonte|rahamin)$ ]] || { echo "KIOSK_VARIANT must be auto, baiamonte, or rahamin" >&2; exit 1; }
if [[ "$KIOSK_VARIANT" == rahamin && "$KIOSK_PROFILE" == zero ]]; then
  echo "The Rahamin five-page image requires KIOSK_PROFILE=multi or auto." >&2
  exit 1
fi

for command in curl losetup mcopy mount openssl sha256sum umount xz; do
  command -v "$command" >/dev/null || { echo "Missing required command: $command" >&2; exit 1; }
done

if [[ -z "$RPI_OS_IMAGE_URL" ]]; then
  # Lite has no Raspberry desktop, welcome wizard, panel, or file manager. The
  # kiosk installer adds only X, Openbox, Chromium, audio, and maintenance tools.
  RPI_OS_IMAGE_URL=https://downloads.raspberrypi.com/raspios_lite_armhf_latest
fi

WORK_DIR=$(mktemp -d)
DIST_DIR="$ROOT_DIR/dist"
ROOT_LOOP=""
cleanup() {
  set +e
  mountpoint -q "$WORK_DIR/root" && umount "$WORK_DIR/root"
  [[ -n "$ROOT_LOOP" ]] && losetup -d "$ROOT_LOOP"
  rm -rf "$WORK_DIR"
}
trap cleanup EXIT

mkdir -p "$DIST_DIR" "$WORK_DIR/root"
curl -fL "$RPI_OS_IMAGE_URL" -o "$WORK_DIR/os.img.xz"
xz -dc "$WORK_DIR/os.img.xz" > "$WORK_DIR/os.img"
BASE_IMAGE="$WORK_DIR/os.img"
case "$KIOSK_VARIANT" in
  baiamonte) OUTPUT_IMAGE="$DIST_DIR/baiamonte-kiosk-universal.img" ;;
  rahamin) OUTPUT_IMAGE="$DIST_DIR/rahamin-kiosk-five-page.img" ;;
  *) OUTPUT_IMAGE="$DIST_DIR/rahamin-kiosk-universal.img" ;;
esac
cp "$BASE_IMAGE" "$OUTPUT_IMAGE"

read -r BOOT_START BOOT_SECTORS < <(partx --raw --noheadings --output START,SECTORS --nr 1 "$OUTPUT_IMAGE")
read -r ROOT_START ROOT_SECTORS < <(partx --raw --noheadings --output START,SECTORS --nr 2 "$OUTPUT_IMAGE")

# Raspberry Pi OS otherwise stops at its first-run username prompt. Provision a
# dedicated account through the same boot-file mechanism used by Pi Imager. The
# random password is intentionally discarded; LightDM logs this account in
# automatically after the kiosk packages are installed.
KIOSK_PASSWORD=$(openssl rand -hex 32)
KIOSK_PASSWORD_HASH=$(printf '%s' "$KIOSK_PASSWORD" | openssl passwd -6 -stdin)
printf 'kiosk:%s\n' "$KIOSK_PASSWORD_HASH" > "$WORK_DIR/userconf.txt"
mcopy -o -i "$OUTPUT_IMAGE@@$((BOOT_START * 512))" "$WORK_DIR/userconf.txt" ::userconf
mcopy -o -i "$OUTPUT_IMAGE@@$((BOOT_START * 512))" "$WORK_DIR/userconf.txt" ::userconf.txt
touch "$WORK_DIR/ssh"
mcopy -o -i "$OUTPUT_IMAGE@@$((BOOT_START * 512))" "$WORK_DIR/ssh" ::ssh
mcopy -i "$OUTPUT_IMAGE@@$((BOOT_START * 512))" ::cmdline.txt "$WORK_DIR/cmdline.txt"
sed -i -E 's/[[:space:]]+cfg80211\.ieee80211_regdom=[^[:space:]]+//g' "$WORK_DIR/cmdline.txt"
sed -i -E 's/[[:space:]]+(logo\.nologo|quiet|loglevel=[^[:space:]]+|vt\.global_cursor_default=[^[:space:]]+)//g' "$WORK_DIR/cmdline.txt"
sed -i "s/$/ cfg80211.ieee80211_regdom=$WIFI_COUNTRY logo.nologo quiet loglevel=3 vt.global_cursor_default=0/" "$WORK_DIR/cmdline.txt"
mcopy -o -i "$OUTPUT_IMAGE@@$((BOOT_START * 512))" "$WORK_DIR/cmdline.txt" ::cmdline.txt
mcopy -i "$OUTPUT_IMAGE@@$((BOOT_START * 512))" ::config.txt "$WORK_DIR/config.txt"
if ! grep -q '^# BEGIN Rahamin Kiosk boot branding$' "$WORK_DIR/config.txt"; then
  cat >> "$WORK_DIR/config.txt" <<'EOF'

[all]
# BEGIN Rahamin Kiosk boot branding
# Suppress the stock rainbow/Raspberry splash; the kiosk displays its own logo.
disable_splash=1
# END Rahamin Kiosk boot branding
EOF
fi
mcopy -o -i "$OUTPUT_IMAGE@@$((BOOT_START * 512))" "$WORK_DIR/config.txt" ::config.txt

ROOT_LOOP=$(losetup --find --show --offset "$((ROOT_START * 512))" --sizelimit "$((ROOT_SECTORS * 512))" "$OUTPUT_IMAGE")
mount "$ROOT_LOOP" "$WORK_DIR/root"

mkdir -p "$WORK_DIR/root/opt/tv-kiosk-bootstrap" "$WORK_DIR/root/etc/NetworkManager/system-connections" "$WORK_DIR/root/etc/systemd/system/multi-user.target.wants" "$WORK_DIR/root/etc/tv-kiosk"
for item in app config scripts session systemd install.sh README.md; do
  cp -a "$ROOT_DIR/$item" "$WORK_DIR/root/opt/tv-kiosk-bootstrap/"
done

escaped_psk=${WIFI_PSK//\"/\\\"}
write_wifi_profile() {
  local ssid=$1 filename=$2 priority=$3
  local escaped_ssid=${ssid//\"/\\\"}
  cat > "$WORK_DIR/root/etc/NetworkManager/system-connections/$filename.nmconnection" <<EOF
[connection]
id=Rahamin WiFi $escaped_ssid
type=wifi
autoconnect=true
autoconnect-priority=$priority
autoconnect-retries=0

[wifi]
mode=infrastructure
ssid=$escaped_ssid
band=bg
powersave=2

[wifi-security]
key-mgmt=wpa-psk
psk=$escaped_psk

[ipv4]
method=auto

[ipv6]
method=auto
EOF
  chmod 600 "$WORK_DIR/root/etc/NetworkManager/system-connections/$filename.nmconnection"
}

write_wifi_profile "$WIFI_PRIMARY_SSID" Rahamin-Home 20
if [[ "$WIFI_SECONDARY_SSID" != "$WIFI_PRIMARY_SSID" ]]; then
  write_wifi_profile "$WIFI_SECONDARY_SSID" Rahamin-Baiamonte 10
fi

cat > "$WORK_DIR/root/etc/NetworkManager/system-connections/Rahamin-Ethernet.nmconnection" <<EOF
[connection]
id=Rahamin Ethernet
type=ethernet
autoconnect=true

[ethernet]

[ipv4]
method=auto

[ipv6]
method=auto
EOF
chmod 600 "$WORK_DIR/root/etc/NetworkManager/system-connections/Rahamin-Ethernet.nmconnection"

printf 'tv-kiosk\n' > "$WORK_DIR/root/etc/hostname"
if grep -q '^127\.0\.1\.1' "$WORK_DIR/root/etc/hosts"; then
  sed -i 's/^127\.0\.1\.1.*/127.0.1.1\ttv-kiosk/' "$WORK_DIR/root/etc/hosts"
else
  printf '127.0.1.1\ttv-kiosk\n' >> "$WORK_DIR/root/etc/hosts"
fi

cat > "$WORK_DIR/root/etc/modprobe.d/rfkill_default.conf" <<EOF
options rfkill default_state=1
EOF

install -d -m 0700 -o 1000 -g 1000 "$WORK_DIR/root/home/kiosk/.ssh"
install -m 0600 -o 1000 -g 1000 "$SSH_PUBLIC_KEY_FILE" "$WORK_DIR/root/home/kiosk/.ssh/authorized_keys"
mkdir -p "$WORK_DIR/root/etc/ssh/sshd_config.d"
cat > "$WORK_DIR/root/etc/ssh/sshd_config.d/20-tv-kiosk.conf" <<EOF
PermitRootLogin no
PasswordAuthentication no
KbdInteractiveAuthentication no
PubkeyAuthentication yes
EOF

install -m 0755 "$ROOT_DIR/image/tv-kiosk-firstboot" "$WORK_DIR/root/usr/local/sbin/tv-kiosk-firstboot"
install -m 0644 "$ROOT_DIR/image/tv-kiosk-firstboot.service" "$WORK_DIR/root/etc/systemd/system/tv-kiosk-firstboot.service"
{
  printf 'KIOSK_PORT=%q\n' "$KIOSK_PORT"
  printf 'KIOSK_UPDATE_BRANCH=%q\n' "$KIOSK_UPDATE_BRANCH"
  printf 'KIOSK_REPO_URL=%q\n' "$KIOSK_REPO_URL"
  printf 'WIFI_COUNTRY=%q\n' "$WIFI_COUNTRY"
  printf 'WIFI_PRIMARY_SSID=%q\n' "$WIFI_PRIMARY_SSID"
  printf 'WIFI_SECONDARY_SSID=%q\n' "$WIFI_SECONDARY_SSID"
  printf 'KIOSK_PROFILE=%q\n' "$KIOSK_PROFILE"
  printf 'KIOSK_VARIANT=%q\n' "$KIOSK_VARIANT"
  printf 'BAIAMONTE_TV_URL=%q\n' "$BAIAMONTE_TV_URL"
} > "$WORK_DIR/root/etc/tv-kiosk/bootstrap.env"
chmod 600 "$WORK_DIR/root/etc/tv-kiosk/bootstrap.env"
ln -sf ../tv-kiosk-firstboot.service "$WORK_DIR/root/etc/systemd/system/multi-user.target.wants/tv-kiosk-firstboot.service"

# The stock first-run wizard can seize the display and wait forever for input.
# This image is fully provisioned and must remain keyboard-free.
rm -f "$WORK_DIR/root/etc/systemd/system/multi-user.target.wants/userconfig.service"
ln -sf /dev/null "$WORK_DIR/root/etc/systemd/system/userconfig.service"
# The installer owns tty1 during first boot. Masking getty avoids both screen
# contention and the ordering cycle caused by placing a multi-user service
# before getty.target.
ln -sf /dev/null "$WORK_DIR/root/etc/systemd/system/getty@tty1.service"

if [[ -e "$WORK_DIR/root/lib/systemd/system/ssh.service" ]]; then
  ln -sf /lib/systemd/system/ssh.service "$WORK_DIR/root/etc/systemd/system/multi-user.target.wants/ssh.service"
elif [[ -e "$WORK_DIR/root/usr/lib/systemd/system/ssh.service" ]]; then
  ln -sf /usr/lib/systemd/system/ssh.service "$WORK_DIR/root/etc/systemd/system/multi-user.target.wants/ssh.service"
fi

mkdir -p "$WORK_DIR/root/etc/wpa_supplicant"
printf 'country=%s\n' "$WIFI_COUNTRY" > "$WORK_DIR/root/etc/wpa_supplicant/wpa_supplicant.conf"
sync
umount "$WORK_DIR/root"
losetup -d "$ROOT_LOOP"
ROOT_LOOP=""

xz -T0 -f -k "$OUTPUT_IMAGE"
(
  cd "$(dirname "$OUTPUT_IMAGE")"
  sha256sum "$(basename "$OUTPUT_IMAGE").xz" > "$(basename "$OUTPUT_IMAGE").xz.sha256"
)
echo "Created $OUTPUT_IMAGE, $OUTPUT_IMAGE.xz, and $OUTPUT_IMAGE.xz.sha256"
