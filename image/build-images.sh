#!/bin/bash
set -euo pipefail

if [[ ${EUID} -ne 0 ]]; then
  echo "Run as root: sudo ./image/build-images.sh" >&2
  exit 1
fi

ROOT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)

echo "Building Baiamonte adaptive image (Pi Zero or Pi 3+)"
KIOSK_VARIANT=baiamonte KIOSK_PROFILE=auto "$ROOT_DIR/image/build-image.sh"

echo "Building Rahamin five-page image (Pi 3+)"
KIOSK_VARIANT=rahamin KIOSK_PROFILE=multi "$ROOT_DIR/image/build-image.sh"

echo "Both kiosk images are ready in $ROOT_DIR/dist"
