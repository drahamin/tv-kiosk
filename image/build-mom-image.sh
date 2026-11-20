#!/bin/bash
set -euo pipefail

ROOT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
echo "Building Mom VPN-free Pi 4 dual-display image"
KIOSK_VARIANT=mom KIOSK_PROFILE=multi "$ROOT_DIR/image/build-image.sh"
