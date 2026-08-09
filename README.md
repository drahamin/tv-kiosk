# TV Kiosk for Raspberry Pi 3

This repository builds and installs a full-screen Raspberry Pi kiosk for a
75-inch 16:9 television. The kiosk rotates three pages every 25 seconds:

1. Rahamin ADS-B TV
2. Rahamin AIS TV (Miami)
3. Airport / Samsung TV board

The local kiosk listens on port `8999` by default and exposes:

- `/` and `/tv` — rotating three-page kiosk
- `/airport-tv` and `/tv/airport` — full-screen airport board
- `/healthz` — health check

## Quick install on Raspberry Pi OS

Flash Raspberry Pi OS (32-bit) and then run:

```sh
sudo ./install.sh
```

The installer creates a dedicated `kiosk` account, configures graphical
autologin, starts Chromium in kiosk mode, enables Wi-Fi, and installs an update
timer. Reboot when it completes.

## Configuration

Edit `config/kiosk.json` to change URLs or the rotation interval. Set the local
HTTP port with `KIOSK_PORT` in `/etc/tv-kiosk/kiosk.env` (default `8999`).

The updater runs five minutes after boot and every five minutes thereafter. It
uses the checkout's `origin` remote and only accepts fast-forward updates from
the configured branch.

## Build a flashable image

On an Ubuntu Linux machine with `sudo`, `curl`, `unzip`, `losetup`, and `mount`:

```sh
sudo ./image/build-image.sh
```

This downloads the latest official Raspberry Pi OS Desktop 32-bit image, embeds
the kiosk payload and Wi-Fi profile, and writes the finished image under
`dist/`. The desktop and Chromium are already present, shortening first setup.
Allow roughly 5–15 minutes before the display appears. No username entry is required: the
image provisions a dedicated `kiosk` account and logs it in automatically. A
full-screen progress display shows the current installation stage and reports
automatic retries if Wi-Fi or package installation is temporarily unavailable.

The image also enables key-only SSH maintenance as `kiosk@tv-kiosk.local` from
the first boot. Password and root SSH logins are disabled. The public key is
embedded in the image; its private key stays in `image/kiosk_admin_ed25519` and
is excluded from Git.

For GitHub auto-updates, publish this repository first and pass its clone URL:

```sh
sudo KIOSK_REPO_URL=https://github.com/OWNER/tv-kiosk.git ./image/build-image.sh
```

Wi-Fi credentials are read from `image/config.local.env`, which is excluded
from Git. See `image/config.example.env`.

## Security

The generated image contains the Wi-Fi password. Keep the image private. Do
not commit `image/config.local.env` or a generated image to Git.
