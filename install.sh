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

apt-get update
DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
  chromium git lightdm network-manager openbox python3 unclutter x11-xserver-utils xserver-xorg

if ! id "$KIOSK_USER" >/dev/null 2>&1; then
  useradd --create-home --shell /bin/bash "$KIOSK_USER"
fi

if [ "$SOURCE_DIR" != "$APP_DIR" ]; then
  rm -rf "$APP_DIR"
  mkdir -p "$APP_DIR"
  cp -a "$SOURCE_DIR"/. "$APP_DIR"/
fi

chown -R root:root "$APP_DIR"
chmod +x "$APP_DIR/app/server.py" "$APP_DIR/scripts/update-kiosk.sh"

mkdir -p /etc/tv-kiosk /etc/lightdm/lightdm.conf.d "/home/$KIOSK_USER/.config/openbox"
cat > /etc/tv-kiosk/kiosk.env <<EOF
KIOSK_PORT=$KIOSK_PORT
KIOSK_UPDATE_BRANCH=$KIOSK_UPDATE_BRANCH
EOF

cat > /etc/lightdm/lightdm.conf.d/50-tv-kiosk.conf <<EOF
[Seat:*]
autologin-user=$KIOSK_USER
autologin-user-timeout=0
user-session=openbox
xserver-command=X -s 0 -dpms
EOF

sed "s#127.0.0.1:8999#127.0.0.1:$KIOSK_PORT#" "$APP_DIR/session/openbox-autostart" > "/home/$KIOSK_USER/.config/openbox/autostart"
chown -R "$KIOSK_USER:$KIOSK_USER" "/home/$KIOSK_USER/.config"
chmod +x "/home/$KIOSK_USER/.config/openbox/autostart"

install -m 0644 "$APP_DIR/systemd/tv-kiosk-web.service" /etc/systemd/system/
install -m 0644 "$APP_DIR/systemd/tv-kiosk-update.service" /etc/systemd/system/
install -m 0644 "$APP_DIR/systemd/tv-kiosk-update.timer" /etc/systemd/system/

systemctl daemon-reload
systemctl enable tv-kiosk-web.service tv-kiosk-update.timer lightdm.service
systemctl set-default graphical.target

echo "TV kiosk installed. Reboot to start the full-screen display."
