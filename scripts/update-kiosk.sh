#!/bin/sh
set -eu

APP_DIR=${KIOSK_APP_DIR:-/opt/tv-kiosk}
BRANCH=${KIOSK_UPDATE_BRANCH:-main}

cd "$APP_DIR"

if ! git remote get-url origin >/dev/null 2>&1; then
  echo "No Git origin configured; leaving installed payload unchanged"
  exit 0
fi

if ! git diff --quiet || ! git diff --cached --quiet; then
  echo "Local changes detected; update skipped"
  exit 0
fi

git fetch --quiet origin "$BRANCH"
current=$(git rev-parse HEAD)
target=$(git rev-parse "origin/$BRANCH")

if [ "$current" = "$target" ]; then
  exit 0
fi

git merge-base --is-ancestor "$current" "$target" || {
  echo "Remote is not a fast-forward update; update skipped"
  exit 1
}

git merge --ff-only "$target"
systemctl restart tv-kiosk-web.service
systemctl try-restart tv-kiosk-browser.service || true
