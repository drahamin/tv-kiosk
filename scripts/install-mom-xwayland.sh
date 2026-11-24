#!/bin/sh
# Called only from the supported root-owned Mom installation/update path.
set -eu
variant=${1:-}
model=${2:-}
case "$variant:$model" in
  mom:*"Raspberry Pi 4"*|mom:*"Raspberry Pi 5"*) ;;
  *) exit 0 ;;
esac
if [ "$(id -u)" -ne 0 ]; then
  echo "The Mom display setup requires existing OS administrator access." >&2
  exit 1
fi
if ! command -v Xwayland >/dev/null 2>&1; then
  if [ "${KIOSK_APT_UPDATED:-0}" != 1 ]; then
    apt-get update
  fi
  DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends xwayland
fi
