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

# The first dual-HDMI Rahamin release exposed HDMI 2 in Admin but left the
# shipped five-page configuration disabled. Enable it exactly once on Pi 4/5
# installations. The marker prevents later updates from undoing a deliberate
# manual disable in Admin.
HARDWARE_MODEL=${HARDWARE_MODEL:-$(tr -d '\000' < /proc/device-tree/model 2>/dev/null || printf 'Raspberry Pi')}
DUAL_HDMI_DEFAULT_MARKER="$STATE_DIR/dual-hdmi-default-v1"
KIOSK_CONFIG="/home/$KIOSK_USER/.config/tv-kiosk/kiosk.json"
case "${KIOSK_VARIANT:-auto}:$HARDWARE_MODEL" in
  rahamin:*"Raspberry Pi 4"*|rahamin:*"Raspberry Pi 5"*)
    if [ ! -e "$DUAL_HDMI_DEFAULT_MARKER" ] && [ -f "$KIOSK_CONFIG" ]; then
      python3 - "$KIOSK_CONFIG" <<'PY'
import json
import sys

path = sys.argv[1]
with open(path, encoding="utf-8") as handle:
    config = json.load(handle)
config["secondary_display_enabled"] = True
config.setdefault("secondary_display_url", "http://192.168.0.10:8101")
config.setdefault("secondary_zoom_percent", 100)
with open(path, "w", encoding="utf-8") as handle:
    json.dump(config, handle, indent=2)
    handle.write("\n")
PY
      chown "$KIOSK_USER:$KIOSK_USER" "$KIOSK_CONFIG"
    fi
    install -d -m 0755 "$STATE_DIR"
    touch "$DUAL_HDMI_DEFAULT_MARKER"
    ;;
esac

# Current Raspberry Pi OS Lite releases do not always consume userconf.txt
# before this root-owned first-boot installer runs. In that case useradd creates
# the kiosk account with a locked shadow entry. LightDM may start the first
# session, but SSH and later user services are rejected after reboot. Give only
# locked/passwordless kiosk accounts a discarded random password. SSH password
# authentication remains disabled; this merely makes key-only login and the
# graphical service account valid across reboots and updates.
KIOSK_PASSWORD_STATE=$(passwd -S "$KIOSK_USER" 2>/dev/null | awk '{print $2}')
case "$KIOSK_PASSWORD_STATE" in
  L|LK|NP)
    KIOSK_RANDOM_PASSWORD=$(od -An -N32 -tx1 /dev/urandom | tr -d ' \n')
    printf '%s:%s\n' "$KIOSK_USER" "$KIOSK_RANDOM_PASSWORD" | chpasswd
    unset KIOSK_RANDOM_PASSWORD
    ;;
esac

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
  install -m 0755 "$APP_DIR/scripts/rahamin-zramswap" /usr/local/sbin/rahamin-zramswap
  install -d -m 0755 /etc/systemd/system/zramswap.service.d
  cat > /etc/systemd/system/zramswap.service.d/rahamin-kiosk.conf <<'EOF'
[Service]
ExecStart=
ExecStart=/usr/local/sbin/rahamin-zramswap
EOF
  systemctl daemon-reload
  systemctl reset-failed zramswap.service >/dev/null 2>&1 || true
  systemctl enable --now zramswap.service >/dev/null 2>&1 || true
  systemctl disable --now dphys-swapfile.service >/dev/null 2>&1 || true

fi

# The completed kiosk installation no longer needs cloud-init. Disabling its
# generator removes the cloud-final ordering cycle and boot-screen warning.
install -d -m 0755 /etc/cloud
touch /etc/cloud/cloud-init.disabled

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
  else
    case "$(tr -d '\000' </proc/device-tree/model 2>/dev/null || true)" in
      *"Raspberry Pi 4"*|*"Raspberry Pi 5"*)
        cat >> "$BOOT_CONFIG" <<'EOF'
# Two scanout framebuffers allow independent Rahamin and Baiamonte HDMI TVs.
max_framebuffers=2
EOF
        ;;
    esac
  fi
  cat >> "$BOOT_CONFIG" <<'EOF'
# END Rahamin Kiosk HDMI
EOF
fi

BROWSER_PACKAGE=
if [ "$KIOSK_HARDWARE_PROFILE" = zero ] && ! command -v cog >/dev/null 2>&1; then
  BROWSER_PACKAGE=cog
elif [ "$KIOSK_HARDWARE_PROFILE" != zero ] && ! command -v chromium >/dev/null 2>&1; then
  BROWSER_PACKAGE=chromium
fi
if ! command -v cec-client >/dev/null 2>&1 || ! command -v wtype >/dev/null 2>&1 || ! command -v labwc >/dev/null 2>&1 || ! command -v wlr-randr >/dev/null 2>&1 || [ -n "$BROWSER_PACKAGE" ]; then
  apt-get update
  DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends cec-utils labwc wlr-randr wtype $BROWSER_PACKAGE
fi

# Older builds selected Openbox even though the optimized Chromium and CEC
# units use native Wayland. Keep every update pinned to the matching labwc
# session so the browser starts full-screen instead of waiting indefinitely.
install -d -m 0755 /etc/lightdm/lightdm.conf.d
cat > /etc/lightdm/lightdm.conf.d/50-tv-kiosk.conf <<EOF
[Seat:*]
autologin-user=$KIOSK_USER
autologin-user-timeout=0
autologin-session=labwc
user-session=labwc
EOF

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
install -m 0755 "$APP_DIR/scripts/rahamin-kiosk-action-request" /usr/local/sbin/rahamin-kiosk-action-request
rm -f /etc/sudoers.d/90-rahamin-kiosk-network /etc/sudoers.d/91-rahamin-kiosk-action

for unit in tv-kiosk-web.service tv-kiosk-update.service tv-kiosk-update.timer tv-kiosk-action.service tv-kiosk-action.path tv-kiosk-network.service tv-kiosk-network.path; do
  install -m 0644 "$APP_DIR/systemd/$unit" "/etc/systemd/system/$unit"
done
systemctl daemon-reload
systemctl enable --now tv-kiosk-update.timer tv-kiosk-action.path tv-kiosk-network.path >/dev/null

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

# Commit helpers, protected request units, and the browser unit to storage before any browser
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
    runuser -u "$KIOSK_USER" -- env XDG_RUNTIME_DIR="/run/user/$KIOSK_UID" systemctl --no-block --user disable --now tv-kiosk-browser.service || true
  else
    runuser -u "$KIOSK_USER" -- env XDG_RUNTIME_DIR="/run/user/$KIOSK_UID" systemctl --user enable tv-kiosk-browser.service || true
  fi
  if [ -f "/home/$KIOSK_USER/.config/tv-kiosk/browser-controller.py" ]; then
    pkill -u "$KIOSK_USER" -f "/home/$KIOSK_USER/.config/tv-kiosk/browser-controller.py" 2>/dev/null || true
    rm -f "/home/$KIOSK_USER/.config/tv-kiosk/browser-controller.py"
    BROWSER_CHANGED=true
  fi
  if [ "$BROWSER_CHANGED" = true ] && [ ! -e "$DISPLAY_DISABLED" ]; then
    runuser -u "$KIOSK_USER" -- env XDG_RUNTIME_DIR="/run/user/$KIOSK_UID" systemctl --no-block --user restart tv-kiosk-browser.service || true
  fi
  runuser -u "$KIOSK_USER" -- env XDG_RUNTIME_DIR="/run/user/$KIOSK_UID" systemctl --user enable tv-kiosk-remote.service || true
  if [ "$REMOTE_CHANGED" = true ] || ! pgrep -u "$KIOSK_USER" -f "$APP_DIR/scripts/rahamin-kiosk-remote" >/dev/null 2>&1; then
    runuser -u "$KIOSK_USER" -- env XDG_RUNTIME_DIR="/run/user/$KIOSK_UID" systemctl --no-block --user restart tv-kiosk-remote.service || true
  fi
  runuser -u "$KIOSK_USER" -- env XDG_RUNTIME_DIR="/run/user/$KIOSK_UID" systemctl --user enable tv-kiosk-audio.service || true
  runuser -u "$KIOSK_USER" -- env XDG_RUNTIME_DIR="/run/user/$KIOSK_UID" systemctl --user enable tv-kiosk-chime.service || true
  if [ "$AUDIO_CHANGED" = true ] || ! runuser -u "$KIOSK_USER" -- env XDG_RUNTIME_DIR="/run/user/$KIOSK_UID" systemctl --user is-active --quiet tv-kiosk-audio.service; then
    runuser -u "$KIOSK_USER" -- env XDG_RUNTIME_DIR="/run/user/$KIOSK_UID" systemctl --no-block --user restart tv-kiosk-audio.service || true
  fi
fi

/usr/local/sbin/rahamin-kiosk-cleanup
