# Rahamin and Baiamonte TV Kiosk Images

Rahamin Kiosk is a full-screen, remotely managed display for a 75-inch 16:9
Samsung television. It can rotate up to five enabled web pages at a configurable
interval. The default playlist rotates every 45 seconds:

1. Rahamin ADS-B TV
2. Rahamin AIS TV (Miami)
3. Airport / Samsung TV board
4. Miami Weather
5. Sicily Weather

The project produces two flashable image products:

1. **Baiamonte Kiosk** — one image for both Pi Zero and Pi 3+. It detects the
   board at first boot. Pi Zero uses the lightweight dashboard mode and omits
   camera-heavy dashboard sections; Pi 3+ uses Chromium and displays every
   Baiamonte dashboard section. Both open the VPN-local TV display at
   `http://192.168.0.10:8101` by default.
2. **Rahamin Kiosk — Five Page** — a Pi 3+ image with the five enabled ADS-B,
   AIS, airport, Miami weather, and Sicily weather pages listed above. It begins
   rotating immediately at 45-second intervals.

The images share the same Admin page, dual Wi-Fi setup, DHCP Ethernet, key-only
SSH maintenance, HDMI/CEC handling, recovery supervision, and GitHub updater.
Set `BAIAMONTE_TV_URL` during image creation or edit Page 1 in Admin to change
the Baiamonte dashboard address.

The local service listens on port `8999` by default and provides:

- `/` and `/tv` — rotating full-screen playlist
- `/airport-tv` and `/tv/airport` — full-screen airport board
- `/setup` — first-deployment instructions and detected DHCP addresses
- `/healthz` — health check
- `/admin` — Rahamin Kiosk administration

## First deployment

Ethernet and the preloaded `Home` and `Baiamonte` Wi-Fi connections use DHCP.
Both Wi-Fi profiles use the one `WIFI_PSK` supplied privately during the image
build, and the kiosk automatically joins whichever is available. A new image
opens the setup screen on the TV and displays every detected IPv4 address, the
`.local` address, and the admin URL. No keyboard is required.

The initial administrator login is `admin` / `admin`. Change it after signing
in, then enable **Setup complete** to start the playlist. Existing installations
keep their current login and runtime settings during installs and GitHub updates.

## Administration

The control center supports:

- up to five saved pages, individual enable/disable controls, and connection tests;
- rotation timing, transition timing, display colors, themes, title, and web port;
- fixed page zoom choices from 50% to 200%;
- automatic HDMI audio detection, enable/disable control, and a 0–100 TV volume setting;
- Raspberry Pi temperature, load, memory, disk, uptime, software revision, and
  service status;
- Wi-Fi, Ethernet, DHCP or static IPv4, IPv6, DNS, gateway, hostname, Wi-Fi
  credentials, autoconnect, radio state, and MAC policy;
- configuration backup, secure password changes, live status refresh, display
  restart, forced GitHub update, and clean Raspberry Pi reboot.

Static address fields remain blank while DHCP is selected. Network changes can
move the admin page to a new address. Saved Wi-Fi passwords are never displayed.
Passwords are stored as salted PBKDF2 hashes and sessions use HTTP-only cookies.

HDMI audio automatically follows the available Pi 3 or Pi 4 HDMI sink. It uses
the PipeWire and WirePlumber session already provided by Raspberry Pi OS, applies
settings only at startup or when Admin changes, and adds no polling audio daemon,
software transcoder, or idle CPU workload. Chromium is permitted to play page
audio without waiting for a mouse or keyboard gesture.
After HDMI is selected, a gentle sub-second three-note twinkle confirms that the
TV audio path is working. The same twinkle plays after a real GitHub update, but
not after routine checks that find nothing new. It leaves no player or audio
task running afterward.

During each normal boot, a full-screen Rahamin Kiosk startup page replaces the
desktop and reports display, HDMI audio, network, and page-loading stages. The
unneeded desktop shell and panel are stopped, saving roughly 40 MB of memory.

## Samsung remote

The kiosk listens for the Samsung television remote through Anynet+ (HDMI-CEC),
with no USB receiver or keyboard required. Enable Anynet+ on the TV, then use:

- arrows and OK to navigate the active web page;
- Play/Pause to control page media;
- Fast-forward and Rewind to zoom in and out;
- Stop to reset browser zoom to 100%.

Samsung keeps the Channel rocker for television functions and may show an
unsupported-mode message when it is pressed on an HDMI input. The CEC listener
registers Rahamin Kiosk as the active HDMI playback source so
Samsung Anynet+ forwards key presses to it. The Wayland compositor hides and
parks the pointer after startup; no mouse or X11 cursor utility is required.

The Admin page also provides persistent zoom choices from 50% through 200%.
That saved zoom applies at every boot; temporary remote zoom remains available
while the kiosk is running.

Runtime state is stored outside the Git checkout under
`/home/kiosk/.config/tv-kiosk`, so automatic updates do not overwrite it.

## Quick install on Raspberry Pi OS

Flash Raspberry Pi OS Desktop (32-bit), then run:

```sh
sudo ./install.sh
```

The installer configures graphical autologin, prevents login and keyring prompts,
starts Chromium in kiosk mode, installs the web control center and Samsung
HDMI-CEC remote service, enables GitHub
updates, and installs supervised browser recovery. It also disables unused
Bluetooth and NFS/RPC services and safely trims old package archives, logs, and
crash reports without removing required Raspberry Pi components.

The adaptive Baiamonte image supports Pi Zero/Zero W/Zero 2 W and Pi 3+.
The Rahamin five-page image supports Raspberry Pi 3B/3B+, Pi 4B, and Pi 5. Pi 4 uses its
first micro-HDMI port and should use a proper 5V/3A USB-C supply. Pi 3 should use
a reliable 5V/2.5A micro-USB supply. Undervoltage can reset either model while
Chromium is loading map pages.

On Pi Zero, installation enables compressed RAM swap, disables unused printing,
Bluetooth, modem, NFS, and RPC services, limits Chromium to one renderer and a
128 MB disk cache, caps its JavaScript heap, and configures a stable 1080p60
HDMI mode with audio for Samsung televisions. SSH, NetworkManager, the Admin
page, HDMI-CEC, audible startup/update confirmation, browser supervision, and
the GitHub updater remain enabled. USB Ethernet adapters use DHCP automatically.

The attached TV rotates full Chromium tabs instead of embedding remote pages.
This avoids remote `X-Frame-Options` restrictions and keeps memory use reasonable
on both supported models.

The display uses the highest mode exposed by the attached TV and Pi. On the Pi 3
this is normally native 1920×1080 at 60 Hz. Chromium keeps a bounded 256 MB disk
cache for map tiles and page assets, limits renderer processes, and disables
unneeded background services so repeat page loads are faster without retaining
all five heavy map pages in memory.

## GitHub updates

The updater checks five minutes after boot and every five minutes thereafter. It
accepts fast-forward updates from the configured branch, reapplies the system
configuration, and restarts the managed services. Local runtime settings and the
changed administrator password remain untouched.

## Build a flashable image

On an Ubuntu Linux machine with `sudo`, `curl`, `xz`, `losetup`, `mtools`, and
`mount` installed, build both products with one command:

```sh
sudo ./image/build-images.sh
```

The builder downloads Raspberry Pi OS Lite 32-bit, provisions the `kiosk`
account, enables key-only SSH, embeds DHCP Ethernet plus both Wi-Fi profiles,
and writes:

- `dist/baiamonte-kiosk-universal.img.xz` for Pi Zero or Pi 3+;
- `dist/rahamin-kiosk-five-page.img.xz` for Pi 3+;
- a matching `.sha256` integrity file for each compressed image.

On first boot, a full-screen
progress display reports every installation stage and automatic retry. After the
reboot, the configuration screen displays the address to use from a phone or
computer on the same network.

For GitHub auto-updates, publish the repository and pass its clone URL:

```sh
sudo KIOSK_REPO_URL=https://github.com/OWNER/tv-kiosk.git ./image/build-images.sh
```

`KIOSK_VARIANT` selects `baiamonte`, `rahamin`, or the backward-compatible
`auto` behavior. `KIOSK_PROFILE` selects the hardware tuning: `auto`
(recommended), `zero`, or `multi`. The two-image builder chooses the correct
combination automatically. A typical private
`image/config.local.env` is:

```sh
WIFI_PRIMARY_SSID=Home
WIFI_SECONDARY_SSID=Baiamonte
WIFI_PSK=your-shared-password
KIOSK_PROFILE=auto
KIOSK_VARIANT=baiamonte
BAIAMONTE_TV_URL=http://192.168.0.10:8101
KIOSK_REPO_URL=https://github.com/OWNER/tv-kiosk.git
```

Set `BAIAMONTE_TV_URL` to an alternate local or external display URL without
changing the image software.

Wi-Fi credentials are read from the ignored `image/config.local.env`; see
`image/config.example.env`. The generated image contains the Wi-Fi password, so
keep it private and never commit the local environment file or generated image.
