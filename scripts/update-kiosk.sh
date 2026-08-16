#!/bin/sh
set -eu

APP_DIR=${KIOSK_APP_DIR:-/opt/tv-kiosk}
BRANCH=${KIOSK_UPDATE_BRANCH:-main}

cd "$APP_DIR"

# This idempotent root setup also runs when no source update is needed, allowing
# an older image to receive new system helpers on the next timer pass.
if [ -x "$APP_DIR/scripts/apply-system-config.sh" ]; then
  "$APP_DIR/scripts/apply-system-config.sh"
fi

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
if [ -x "$APP_DIR/scripts/apply-system-config.sh" ]; then
  "$APP_DIR/scripts/apply-system-config.sh"
fi
systemctl restart tv-kiosk-web.service
KIOSK_UID=$(id -u kiosk)
if [ -S "/run/user/$KIOSK_UID/bus" ]; then
  runuser -u kiosk -- env XDG_RUNTIME_DIR="/run/user/$KIOSK_UID" systemctl --no-block --user try-restart tv-kiosk-browser.service || true
  runuser -u kiosk -- env XDG_RUNTIME_DIR="/run/user/$KIOSK_UID" systemctl --no-block --user try-restart tv-kiosk-remote.service || true
  # Audible confirmation is reserved for a real installed update, not the
  # routine timer checks that find no new commit.
  runuser -u kiosk -- env XDG_RUNTIME_DIR="/run/user/$KIOSK_UID" systemctl --no-block --user restart tv-kiosk-chime.service || true
fi
