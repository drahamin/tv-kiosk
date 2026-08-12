#!/bin/sh
set -eu

if [ "$(id -u)" -ne 0 ]; then
  echo "Rahamin Kiosk system setup must run as root" >&2
  exit 1
fi

APP_DIR=${KIOSK_APP_DIR:-/opt/tv-kiosk}
KIOSK_USER=${KIOSK_USER:-kiosk}
KIOSK_UID=$(id -u "$KIOSK_USER")
STATE_DIR=/var/lib/rahamin-kiosk

# Early images could leave the dedicated home owned by root, which prevents
# pcmanfm, Chromium, and WirePlumber from creating normal session state.
if [ ! -e "$STATE_DIR/home-ownership-v1" ]; then
  install -d -m 0755 -o "$KIOSK_USER" -g "$KIOSK_USER" "/home/$KIOSK_USER/Desktop" "/home/$KIOSK_USER/.cache" "/home/$KIOSK_USER/.local/state"
  chown -R "$KIOSK_USER:$KIOSK_USER" "/home/$KIOSK_USER"
  install -d -m 0755 "$STATE_DIR"
  touch "$STATE_DIR/home-ownership-v1"
fi

install -d -m 0755 -o "$KIOSK_USER" -g "$KIOSK_USER" "/home/$KIOSK_USER/.config/labwc" "/home/$KIOSK_USER/.config/openbox"
install -m 0755 -o "$KIOSK_USER" -g "$KIOSK_USER" "$APP_DIR/session/labwc-autostart" "/home/$KIOSK_USER/.config/labwc/autostart"
install -m 0644 -o "$KIOSK_USER" -g "$KIOSK_USER" "$APP_DIR/session/labwc-rc.xml" "/home/$KIOSK_USER/.config/labwc/rc.xml"
install -m 0755 -o "$KIOSK_USER" -g "$KIOSK_USER" "$APP_DIR/session/openbox-autostart" "/home/$KIOSK_USER/.config/openbox/autostart"

install -m 0755 "$APP_DIR/scripts/rahamin-kiosk-network" /usr/local/sbin/rahamin-kiosk-network
install -m 0755 "$APP_DIR/scripts/rahamin-kiosk-cleanup" /usr/local/sbin/rahamin-kiosk-cleanup
install -m 0755 "$APP_DIR/scripts/rahamin-kiosk-action" /usr/local/sbin/rahamin-kiosk-action
printf '%s ALL=(root) NOPASSWD: /usr/local/sbin/rahamin-kiosk-network\n' "$KIOSK_USER" > /etc/sudoers.d/90-rahamin-kiosk-network
chmod 0440 /etc/sudoers.d/90-rahamin-kiosk-network
printf '%s ALL=(root) NOPASSWD: /usr/local/sbin/rahamin-kiosk-action force-update, /usr/local/sbin/rahamin-kiosk-action reboot, /usr/local/sbin/rahamin-kiosk-action start-display, /usr/local/sbin/rahamin-kiosk-action stop-display\n' "$KIOSK_USER" > /etc/sudoers.d/91-rahamin-kiosk-action
chmod 0440 /etc/sudoers.d/91-rahamin-kiosk-action

install -d -m 0755 -o "$KIOSK_USER" -g "$KIOSK_USER" "/home/$KIOSK_USER/.config/systemd/user"
BROWSER_UNIT="/home/$KIOSK_USER/.config/systemd/user/tv-kiosk-browser.service"
BROWSER_CHANGED=false
if ! cmp -s "$APP_DIR/systemd/tv-kiosk-browser.service" "$BROWSER_UNIT"; then
  install -m 0644 -o "$KIOSK_USER" -g "$KIOSK_USER" "$APP_DIR/systemd/tv-kiosk-browser.service" "$BROWSER_UNIT"
  BROWSER_CHANGED=true
fi
install -d -m 0755 -o "$KIOSK_USER" -g "$KIOSK_USER" "/home/$KIOSK_USER/.config/systemd/user/default.target.wants"
DISPLAY_DISABLED="/home/$KIOSK_USER/.config/tv-kiosk/display-disabled"
if [ -e "$DISPLAY_DISABLED" ]; then
  rm -f "/home/$KIOSK_USER/.config/systemd/user/default.target.wants/tv-kiosk-browser.service"
else
  ln -sf ../tv-kiosk-browser.service "/home/$KIOSK_USER/.config/systemd/user/default.target.wants/tv-kiosk-browser.service"
fi

# Commit helpers, sudo rules, and the browser unit to storage before any browser
# load is started. This protects the installation if an undervoltage reset occurs.
sync

if pgrep -x labwc >/dev/null 2>&1; then
  pkill -HUP -x labwc || true
fi

if [ -S "/run/user/$KIOSK_UID/bus" ]; then
  runuser -u "$KIOSK_USER" -- env XDG_RUNTIME_DIR="/run/user/$KIOSK_UID" systemctl --user daemon-reload || true
  if [ -e "$DISPLAY_DISABLED" ]; then
    runuser -u "$KIOSK_USER" -- env XDG_RUNTIME_DIR="/run/user/$KIOSK_UID" systemctl --user disable --now tv-kiosk-browser.service || true
  else
    runuser -u "$KIOSK_USER" -- env XDG_RUNTIME_DIR="/run/user/$KIOSK_UID" systemctl --user enable tv-kiosk-browser.service || true
  fi
  if [ -f "/home/$KIOSK_USER/.config/tv-kiosk/browser-controller.py" ]; then
    pkill -u "$KIOSK_USER" -f "/home/$KIOSK_USER/.config/tv-kiosk/browser-controller.py" 2>/dev/null || true
    rm -f "/home/$KIOSK_USER/.config/tv-kiosk/browser-controller.py"
    BROWSER_CHANGED=true
  fi
  if [ "$BROWSER_CHANGED" = true ] && [ ! -e "$DISPLAY_DISABLED" ]; then
    runuser -u "$KIOSK_USER" -- env XDG_RUNTIME_DIR="/run/user/$KIOSK_UID" systemctl --user restart tv-kiosk-browser.service || true
  fi
fi

/usr/local/sbin/rahamin-kiosk-cleanup
