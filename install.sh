#!/bin/sh
set -eu

if [ "$(id -u)" -ne 0 ]; then
  echo "Run this installer as root: sudo ./install.sh" >&2
  exit 1
fi

SOURCE_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
APP_DIR=/opt/tv-kiosk
KIOSK_USER=${KIOSK_USER:-kiosk}
KIOSK_PORT=${KIOSK_PORT:-8999}
KIOSK_UPDATE_BRANCH=${KIOSK_UPDATE_BRANCH:-main}
KIOSK_PROFILE=${KIOSK_PROFILE:-auto}
KIOSK_VARIANT=${KIOSK_VARIANT:-auto}
BAIAMONTE_TV_URL=${BAIAMONTE_TV_URL:-http://192.168.0.10:8101}
WIFI_PRIMARY_SSID=${WIFI_PRIMARY_SSID:-Home}
WIFI_SECONDARY_SSID=${WIFI_SECONDARY_SSID:-Baiamonte}
WIFI_PSK=${WIFI_PSK:-}
CLOUDCONNEXA_PROFILE_FILE=${CLOUDCONNEXA_PROFILE_FILE:-}

HARDWARE_MODEL=$(tr -d '\000' < /proc/device-tree/model 2>/dev/null || printf 'Raspberry Pi')
HARDWARE_PROFILE=$(KIOSK_PROFILE="$KIOSK_PROFILE" KIOSK_HARDWARE_MODEL="$HARDWARE_MODEL" sh "$SOURCE_DIR/scripts/detect-hardware-profile")
case "$KIOSK_VARIANT" in
  auto)
    if [ "$HARDWARE_PROFILE" = zero ]; then
      INSTALL_VARIANT=baiamonte
    else
      INSTALL_VARIANT=rahamin
    fi
    ;;
  baiamonte|rahamin) INSTALL_VARIANT=$KIOSK_VARIANT ;;
  *)
    echo "KIOSK_VARIANT must be auto, baiamonte, or rahamin" >&2
    exit 1
    ;;
esac
if [ "$INSTALL_VARIANT" = rahamin ] && [ "$HARDWARE_PROFILE" = zero ]; then
  echo "The Rahamin five-page kiosk requires a Raspberry Pi 3 or newer." >&2
  exit 1
fi
echo "Installing $INSTALL_VARIANT kiosk on $HARDWARE_MODEL ($HARDWARE_PROFILE profile)"

if [ "${KIOSK_APT_UPDATED:-0}" != 1 ]; then
  apt-get update
fi
DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
  avahi-daemon cec-utils git labwc lightdm network-manager network-manager-openvpn openvpn openssh-server pipewire pipewire-pulse python3 wireplumber wtype
if [ "$HARDWARE_PROFILE" = zero ]; then
  DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends cog zram-tools
else
  DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends chromium
fi

if ! id "$KIOSK_USER" >/dev/null 2>&1; then
  useradd --create-home --shell /bin/bash "$KIOSK_USER"
fi

groupadd --force autologin
groupadd --force nopasswdlogin
usermod --append --groups autologin,nopasswdlogin "$KIOSK_USER"
for group in audio video render input plugdev netdev tty; do
  if getent group "$group" >/dev/null 2>&1; then
    usermod --append --groups "$group" "$KIOSK_USER"
  fi
done

if [ "$SOURCE_DIR" != "$APP_DIR" ]; then
  rm -rf "$APP_DIR"
  mkdir -p "$APP_DIR"
  cp -a "$SOURCE_DIR"/. "$APP_DIR"/
fi

chown -R root:root "$APP_DIR"
chmod +x "$APP_DIR/app/server.py" "$APP_DIR/scripts/update-kiosk.sh" "$APP_DIR/scripts/launch-browser.sh" "$APP_DIR/scripts/browser-controller.py" "$APP_DIR/scripts/startup-chime.py" "$APP_DIR/scripts/apply-system-config.sh" "$APP_DIR/scripts/detect-hardware-profile" "$APP_DIR/scripts/rahamin-kiosk-network" "$APP_DIR/scripts/rahamin-kiosk-cleanup" "$APP_DIR/scripts/rahamin-kiosk-action" "$APP_DIR/scripts/rahamin-kiosk-remote" "$APP_DIR/scripts/rahamin-kiosk-audio" "$APP_DIR/scripts/rahamin-kiosk-chime" "$APP_DIR/scripts/rahamin-kiosk-cloudconnexa"

mkdir -p /etc/tv-kiosk /etc/lightdm/lightdm.conf.d "/home/$KIOSK_USER/.config/openbox" "/home/$KIOSK_USER/.config/labwc" "/home/$KIOSK_USER/.config/tv-kiosk"
if [ -n "$CLOUDCONNEXA_PROFILE_FILE" ]; then
  install -m 0600 "$CLOUDCONNEXA_PROFILE_FILE" /etc/tv-kiosk/cloudconnexa-baiamonte-dashboard.ovpn
fi
cat > /etc/tv-kiosk/kiosk.env <<EOF
KIOSK_PORT=$KIOSK_PORT
KIOSK_UPDATE_BRANCH=$KIOSK_UPDATE_BRANCH
KIOSK_HARDWARE_PROFILE=$HARDWARE_PROFILE
KIOSK_VARIANT=$INSTALL_VARIANT
EOF

cat > /etc/lightdm/lightdm.conf.d/50-tv-kiosk.conf <<EOF
[Seat:*]
autologin-user=$KIOSK_USER
autologin-user-timeout=0
autologin-session=labwc
user-session=labwc
xserver-command=X -s 0 -dpms
EOF

cp "$APP_DIR/session/openbox-autostart" "/home/$KIOSK_USER/.config/openbox/autostart"
cp "$APP_DIR/session/labwc-autostart" "/home/$KIOSK_USER/.config/labwc/autostart"
cp "$APP_DIR/session/labwc-rc.xml" "/home/$KIOSK_USER/.config/labwc/rc.xml"
if [ ! -e "/home/$KIOSK_USER/.config/tv-kiosk/kiosk.json" ]; then
  if [ "$INSTALL_VARIANT" = baiamonte ]; then
    if [ "$HARDWARE_PROFILE" = zero ]; then
      DEFAULT_CONFIG="$APP_DIR/config/kiosk-zero.json"
    else
      DEFAULT_CONFIG="$APP_DIR/config/kiosk-baiamonte.json"
    fi
    cp "$DEFAULT_CONFIG" "/home/$KIOSK_USER/.config/tv-kiosk/kiosk.json"
    python3 - "$BAIAMONTE_TV_URL" "/home/$KIOSK_USER/.config/tv-kiosk/kiosk.json" <<'PY'
import json, sys
url, target = sys.argv[1:]
with open(target, encoding="utf-8") as handle:
    config = json.load(handle)
config["pages"][0]["url"] = url
with open(target, "w", encoding="utf-8") as handle:
    json.dump(config, handle, indent=2)
    handle.write("\n")
PY
  else
    cp "$APP_DIR/config/kiosk.json" "/home/$KIOSK_USER/.config/tv-kiosk/kiosk.json"
  fi
fi
chown -R "$KIOSK_USER:$KIOSK_USER" "/home/$KIOSK_USER"
chmod +x "/home/$KIOSK_USER/.config/openbox/autostart"
chmod +x "/home/$KIOSK_USER/.config/labwc/autostart"

# Direct installs can preload the same two roaming Wi-Fi choices as the image.
# Existing image profiles are updated in place, while Ethernet remains DHCP.
if [ -n "$WIFI_PSK" ]; then
  for wifi_spec in "$WIFI_PRIMARY_SSID:20" "$WIFI_SECONDARY_SSID:10"; do
    wifi_ssid=${wifi_spec%:*}
    wifi_priority=${wifi_spec##*:}
    wifi_name="Rahamin WiFi $wifi_ssid"
    nmcli connection show "$wifi_name" >/dev/null 2>&1 || nmcli connection add type wifi ifname wlan0 con-name "$wifi_name" ssid "$wifi_ssid"
    nmcli connection modify "$wifi_name" connection.autoconnect yes connection.autoconnect-priority "$wifi_priority" connection.autoconnect-retries 0 802-11-wireless.band bg 802-11-wireless.powersave 2 ipv4.method auto ipv6.method auto wifi-sec.key-mgmt wpa-psk wifi-sec.psk "$WIFI_PSK"
  done
fi

"$APP_DIR/scripts/apply-system-config.sh"

install -m 0644 "$APP_DIR/systemd/tv-kiosk-web.service" /etc/systemd/system/
install -m 0644 "$APP_DIR/systemd/tv-kiosk-update.service" /etc/systemd/system/
install -m 0644 "$APP_DIR/systemd/tv-kiosk-update.timer" /etc/systemd/system/

systemctl daemon-reload
systemctl enable avahi-daemon.service ssh.service tv-kiosk-web.service tv-kiosk-update.timer lightdm.service
systemctl set-default graphical.target

echo "Rahamin Kiosk installed. Reboot to start the full-screen display."
