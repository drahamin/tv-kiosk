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

HARDWARE_MODEL=$(tr -d '\000' < /proc/device-tree/model 2>/dev/null || printf 'Raspberry Pi')
echo "Installing Rahamin Kiosk on $HARDWARE_MODEL"

if [ "${KIOSK_APT_UPDATED:-0}" != 1 ]; then
  apt-get update
fi
DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
  avahi-daemon cec-utils chromium git lightdm network-manager openbox openssh-server python3 unclutter wtype x11-xserver-utils xserver-xorg

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
chmod +x "$APP_DIR/app/server.py" "$APP_DIR/scripts/update-kiosk.sh" "$APP_DIR/scripts/launch-browser.sh" "$APP_DIR/scripts/browser-controller.py" "$APP_DIR/scripts/apply-system-config.sh" "$APP_DIR/scripts/rahamin-kiosk-network" "$APP_DIR/scripts/rahamin-kiosk-cleanup" "$APP_DIR/scripts/rahamin-kiosk-action" "$APP_DIR/scripts/rahamin-kiosk-remote"

mkdir -p /etc/tv-kiosk /etc/lightdm/lightdm.conf.d "/home/$KIOSK_USER/.config/openbox" "/home/$KIOSK_USER/.config/labwc" "/home/$KIOSK_USER/.config/tv-kiosk"
cat > /etc/tv-kiosk/kiosk.env <<EOF
KIOSK_PORT=$KIOSK_PORT
KIOSK_UPDATE_BRANCH=$KIOSK_UPDATE_BRANCH
EOF

cat > /etc/lightdm/lightdm.conf.d/50-tv-kiosk.conf <<EOF
[Seat:*]
autologin-user=$KIOSK_USER
autologin-user-timeout=0
autologin-session=openbox
user-session=openbox
xserver-command=X -s 0 -dpms
EOF

cp "$APP_DIR/session/openbox-autostart" "/home/$KIOSK_USER/.config/openbox/autostart"
cp "$APP_DIR/session/labwc-autostart" "/home/$KIOSK_USER/.config/labwc/autostart"
cp "$APP_DIR/session/labwc-rc.xml" "/home/$KIOSK_USER/.config/labwc/rc.xml"
if [ ! -e "/home/$KIOSK_USER/.config/tv-kiosk/kiosk.json" ]; then
  cp "$APP_DIR/config/kiosk.json" "/home/$KIOSK_USER/.config/tv-kiosk/kiosk.json"
fi
chown -R "$KIOSK_USER:$KIOSK_USER" "/home/$KIOSK_USER"
chmod +x "/home/$KIOSK_USER/.config/openbox/autostart"
chmod +x "/home/$KIOSK_USER/.config/labwc/autostart"

"$APP_DIR/scripts/apply-system-config.sh"

install -m 0644 "$APP_DIR/systemd/tv-kiosk-web.service" /etc/systemd/system/
install -m 0644 "$APP_DIR/systemd/tv-kiosk-update.service" /etc/systemd/system/
install -m 0644 "$APP_DIR/systemd/tv-kiosk-update.timer" /etc/systemd/system/

systemctl daemon-reload
systemctl enable avahi-daemon.service ssh.service tv-kiosk-web.service tv-kiosk-update.timer lightdm.service
systemctl set-default graphical.target

echo "Rahamin Kiosk installed. Reboot to start the full-screen display."
