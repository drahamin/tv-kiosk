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
if [ -r /etc/tv-kiosk/kiosk.env ]; then
  . /etc/tv-kiosk/kiosk.env
fi
if [ -z "${KIOSK_HARDWARE_PROFILE:-}" ]; then
  HARDWARE_MODEL=$(tr -d '\000' < /proc/device-tree/model 2>/dev/null || printf 'Raspberry Pi')
  KIOSK_HARDWARE_PROFILE=$(KIOSK_PROFILE=auto KIOSK_HARDWARE_MODEL="$HARDWARE_MODEL" sh "$APP_DIR/scripts/detect-hardware-profile")
  printf 'KIOSK_HARDWARE_PROFILE=%s\n' "$KIOSK_HARDWARE_PROFILE" >> /etc/tv-kiosk/kiosk.env
fi

if [ "$KIOSK_HARDWARE_PROFILE" = zero ]; then
  cat > /etc/default/zramswap <<'EOF'
ALGO=zstd
PERCENT=50
PRIORITY=100
EOF
  cat > /etc/sysctl.d/90-rahamin-kiosk-zero.conf <<'EOF'
vm.swappiness=80
vm.vfs_cache_pressure=100
vm.dirty_background_ratio=5
vm.dirty_ratio=15
EOF
  systemctl enable --now zramswap.service >/dev/null 2>&1 || true
  systemctl disable --now dphys-swapfile.service >/dev/null 2>&1 || true

fi

# Keep a known, recoverable maintenance key on every kiosk. This also repairs
# early images that were provisioned with a public key whose private half was
# not retained on the maintenance Mac.
if [ -s "$APP_DIR/config/kiosk-admin.pub" ]; then
  install -d -m 0700 -o "$KIOSK_USER" -g "$KIOSK_USER" "/home/$KIOSK_USER/.ssh"
  install -m 0600 -o "$KIOSK_USER" -g "$KIOSK_USER" "$APP_DIR/config/kiosk-admin.pub" "/home/$KIOSK_USER/.ssh/authorized_keys"
fi

# Keep HDMI enabled when a Samsung TV powers up slowly or briefly drops HPD.
# EDID remains enabled so multi-profile Pis use the TV's preferred mode. The
# Zero profile uses a stable 1080p60 fallback to keep GPU/memory load bounded.
BOOT_CONFIG=/boot/firmware/config.txt
[ -f "$BOOT_CONFIG" ] || BOOT_CONFIG=/boot/config.txt
if [ -f "$BOOT_CONFIG" ]; then
  sed -i '/^# BEGIN Rahamin Kiosk HDMI$/,/^# END Rahamin Kiosk HDMI$/d' "$BOOT_CONFIG"
  # Replace the marker used by earlier images before writing the managed block.
  sed -i '/^# Rahamin Pi Zero Samsung HDMI:/,/^max_framebuffers=1$/d' "$BOOT_CONFIG"
  cat >> "$BOOT_CONFIG" <<'EOF'

# BEGIN Rahamin Kiosk HDMI
# Read the connected TV EDID, but keep HDMI/HPD asserted during slow TV startup.
display_auto_detect=1
hdmi_force_hotplug=1
hdmi_drive=2
hdmi_force_edid_audio=1
disable_overscan=1
EOF
  if [ "$KIOSK_HARDWARE_PROFILE" = zero ]; then
    cat >> "$BOOT_CONFIG" <<'EOF'
# Stable CEA 1080p60 fallback for the resource-constrained Pi Zero.
hdmi_group=1
hdmi_mode=16
max_framebuffers=1
EOF
  fi
  cat >> "$BOOT_CONFIG" <<'EOF'
# END Rahamin Kiosk HDMI
EOF
fi

if ! command -v cec-client >/dev/null 2>&1 || ! command -v wtype >/dev/null 2>&1; then
  apt-get update
  DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends cec-utils wtype
fi

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
REMOTE_UNIT="/home/$KIOSK_USER/.config/systemd/user/tv-kiosk-remote.service"
REMOTE_CHANGED=false
if ! cmp -s "$APP_DIR/systemd/tv-kiosk-remote.service" "$REMOTE_UNIT"; then
  install -m 0644 -o "$KIOSK_USER" -g "$KIOSK_USER" "$APP_DIR/systemd/tv-kiosk-remote.service" "$REMOTE_UNIT"
  REMOTE_CHANGED=true
fi
AUDIO_UNIT="/home/$KIOSK_USER/.config/systemd/user/tv-kiosk-audio.service"
AUDIO_CHANGED=false
if ! cmp -s "$APP_DIR/systemd/tv-kiosk-audio.service" "$AUDIO_UNIT"; then
  install -m 0644 -o "$KIOSK_USER" -g "$KIOSK_USER" "$APP_DIR/systemd/tv-kiosk-audio.service" "$AUDIO_UNIT"
  AUDIO_CHANGED=true
fi
CHIME_UNIT="/home/$KIOSK_USER/.config/systemd/user/tv-kiosk-chime.service"
if ! cmp -s "$APP_DIR/systemd/tv-kiosk-chime.service" "$CHIME_UNIT"; then
  install -m 0644 -o "$KIOSK_USER" -g "$KIOSK_USER" "$APP_DIR/systemd/tv-kiosk-chime.service" "$CHIME_UNIT"
fi
install -d -m 0755 -o "$KIOSK_USER" -g "$KIOSK_USER" "/home/$KIOSK_USER/.config/systemd/user/default.target.wants"
DISPLAY_DISABLED="/home/$KIOSK_USER/.config/tv-kiosk/display-disabled"
if [ -e "$DISPLAY_DISABLED" ]; then
  rm -f "/home/$KIOSK_USER/.config/systemd/user/default.target.wants/tv-kiosk-browser.service"
else
  ln -sf ../tv-kiosk-browser.service "/home/$KIOSK_USER/.config/systemd/user/default.target.wants/tv-kiosk-browser.service"
fi
ln -sf ../tv-kiosk-remote.service "/home/$KIOSK_USER/.config/systemd/user/default.target.wants/tv-kiosk-remote.service"
ln -sf ../tv-kiosk-audio.service "/home/$KIOSK_USER/.config/systemd/user/default.target.wants/tv-kiosk-audio.service"
ln -sf ../tv-kiosk-chime.service "/home/$KIOSK_USER/.config/systemd/user/default.target.wants/tv-kiosk-chime.service"

# Commit helpers, sudo rules, and the browser unit to storage before any browser
# load is started. This protects the installation if an undervoltage reset occurs.
sync

if pgrep -x labwc >/dev/null 2>&1; then
  pkill -HUP -x labwc || true
fi

# The kiosk does not need a desktop shell or panel behind Chromium. Removing
# them saves roughly 40 MB on the Pi and leaves a clean background during boot.
pkill -u "$KIOSK_USER" -f '/usr/bin/lwrespawn /usr/bin/pcmanfm-pi' 2>/dev/null || true
pkill -u "$KIOSK_USER" -f '/usr/bin/lwrespawn /usr/bin/wf-panel-pi' 2>/dev/null || true
pkill -u "$KIOSK_USER" -x pcmanfm 2>/dev/null || true
pkill -u "$KIOSK_USER" -x wf-panel-pi 2>/dev/null || true

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
  runuser -u "$KIOSK_USER" -- env XDG_RUNTIME_DIR="/run/user/$KIOSK_UID" systemctl --user enable tv-kiosk-remote.service || true
  if [ "$REMOTE_CHANGED" = true ] || ! pgrep -u "$KIOSK_USER" -f "$APP_DIR/scripts/rahamin-kiosk-remote" >/dev/null 2>&1; then
    runuser -u "$KIOSK_USER" -- env XDG_RUNTIME_DIR="/run/user/$KIOSK_UID" systemctl --user restart tv-kiosk-remote.service || true
  fi
  runuser -u "$KIOSK_USER" -- env XDG_RUNTIME_DIR="/run/user/$KIOSK_UID" systemctl --user enable tv-kiosk-audio.service || true
  runuser -u "$KIOSK_USER" -- env XDG_RUNTIME_DIR="/run/user/$KIOSK_UID" systemctl --user enable tv-kiosk-chime.service || true
  if [ "$AUDIO_CHANGED" = true ] || ! runuser -u "$KIOSK_USER" -- env XDG_RUNTIME_DIR="/run/user/$KIOSK_UID" systemctl --user is-active --quiet tv-kiosk-audio.service; then
    runuser -u "$KIOSK_USER" -- env XDG_RUNTIME_DIR="/run/user/$KIOSK_UID" systemctl --user restart tv-kiosk-audio.service || true
  fi
fi

/usr/local/sbin/rahamin-kiosk-cleanup
