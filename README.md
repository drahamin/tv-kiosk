# Rahamin and Baiamonte TV Kiosk Images

Rahamin Kiosk is a full-screen, remotely managed display for a 75-inch 16:9
Samsung television. It can rotate up to five enabled web pages at a configurable
interval. The default playlist rotates every 45 seconds:

1. Rahamin ADS-B TV
2. Rahamin AIS TV (Miami)
3. Airport / Samsung TV board
4. Miami Weather
5. Sicily Weather

The project produces three flashable image products:

1. **Baiamonte Kiosk** — one image for both Pi Zero and Pi 3+. It detects the
   board at first boot. Pi Zero uses the lightweight dashboard mode and omits
   camera-heavy dashboard sections; Pi 3+ uses Chromium and displays every
   Baiamonte TV page. Pi 3+ opens the dedicated [Vineyard kiosk display](http://192.168.0.10:8101/tv?device=vineyard-kiosk), and Pi Zero retains its lighter Home Assistant dashboard at `http://192.168.0.10:8123`.
2. **Rahamin Kiosk — Five Page** — a Pi 3+ image with the five enabled ADS-B,
   AIS, airport, Miami weather, and Sicily weather pages listed above. It begins
   rotating immediately at 45-second intervals.
3. **Mom Dual Display** — a VPN-free Pi 4 image. HDMI 1 rotates the five Miami
   pages and HDMI 2 shows the internally rotating Baiamonte TV dashboard. Four
   loopback-only proxies inject a Cloudflare Access service token, so neither
   the browser nor the destination network needs VPN credentials.

The images share the same Admin page, dual Wi-Fi setup, DHCP Ethernet, key-only
SSH maintenance, HDMI/CEC handling, recovery supervision, and GitHub updater.
Set `BAIAMONTE_TV_URL` during image creation or edit Page 1 in Admin to change
the Baiamonte dashboard address.

An optional CloudConnexa fallback provides access to Home Assistant when the local display is unreachable.
The kiosk first tries `192.168.0.10:8123` directly. After three consecutive
failures it starts the private `baiamonte-dashboard` OpenVPN profile, while
leaving the tunnel off whenever the dashboard is reachable on the local LAN.
While connected, the display switches to
`http://ha.dashboard.baiamonte:8123`; when local access returns, it disconnects
the tunnel and switches back to the local page automatically.
The Admin status page reports **Local LAN**, **VPN connected**, or the current
retry state.

The local service listens on port `8999` by default and provides:

- `/` and `/tv` — rotating full-screen playlist
- `/airport-tv` and `/tv/airport` — full-screen airport board
- `/setup` — first-deployment instructions and detected DHCP addresses
- `/healthz` — health check
- `/admin` — Rahamin Kiosk administration

## First deployment

Ethernet and both preloaded Wi-Fi connections use DHCP. Each Wi-Fi profile can
have its own `WIFI_PRIMARY_PSK` or `WIFI_SECONDARY_PSK`; legacy builds can still
use one shared `WIFI_PSK`. The kiosk automatically joins whichever is available,
including hidden SSIDs. A new image
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

- Left/Right change pages and Up/Down zoom the active ADS-B, AIS, or weather map on both Rahamin and Baiamonte displays;
- OK activates the selected control or opens Baiamonte display controls from the page background;
- Back/Exit closes Baiamonte controls or returns to Today, when Anynet+ forwards the key;
- Play/Pause pauses or resumes rotation when the displayed dashboard supports it;
- Fast-forward and Rewind to zoom in and out;
- Stop to reset browser zoom to 100%.

Samsung keeps the Channel rocker for television functions and may show an
unsupported-mode message when it is pressed on an HDMI input. The passive CEC
listener does not announce itself as the active source, avoiding unwanted
overnight TV power-on and input switching. The Wayland compositor hides and
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

## Pi 4/5 second HDMI display

The Rahamin Admin page can enable an independent Baiamonte dashboard on the
second HDMI connector of a Raspberry Pi 4 or Pi 5. Connect both TVs before
enabling it, then open Admin, enable **Baiamonte display on HDMI 2**, confirm the
dashboard URL (default `http://192.168.0.10:8123`), and save. HDMI 1 retains the
five-page Rahamin rotation while HDMI 2 keeps Baiamonte loaded continuously.

Each display uses a separate Chromium profile, cache, and supervised process.
The compositor positions the windows before maximizing them, and the Rahamin
window is started last so Samsung remote input remains focused on the primary
kiosk. If either browser exits, the controller rebuilds both displays. Pi 3 and
Pi Zero safely ignore the second-display setting.

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
- `dist/mom-dual-display-pi4.img.xz` for a VPN-free dual-display Pi 4;
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
WIFI_PRIMARY_PSK=your-primary-password
WIFI_SECONDARY_PSK=your-secondary-password
KIOSK_PROFILE=auto
KIOSK_VARIANT=baiamonte
BAIAMONTE_TV_URL=
KIOSK_REPO_URL=https://github.com/OWNER/tv-kiosk.git
CLOUDCONNEXA_PROFILE_FILE=image/cloudconnexa-baiamonte-dashboard.ovpn
```

Set `BAIAMONTE_TV_URL` to an alternate local or external display URL without
changing the image software.

Wi-Fi credentials are read from the ignored `image/config.local.env`; see
`image/config.example.env`. The generated image contains the Wi-Fi password, so
keep it private and never commit the local environment file or generated image.
The CloudConnexa profile is also private and is ignored by Git. Download the
unattended `.ovpn` profile for the kiosk from the CloudConnexa portal, place it
at `image/cloudconnexa-baiamonte-dashboard.ovpn`, and set the path above before
building. A Cloud ID by itself normally requires an interactive sign-in and is
not sufficient for a keyboard-free kiosk. The fallback uses split routing, so
GitHub updates and unrelated web traffic continue through the deployment's
ordinary Ethernet or Wi-Fi connection.

### Mom dual-display Cloudflare edge

Create four Cloudflare Tunnel published applications and point them at the
private display services from the connector at each site:

- Baiamonte TV → `http://192.168.0.10:8101`
- Miami ADS-B → `http://192.168.86.155:8998`
- Miami airport board → `http://192.168.86.155:8999`
- Miami AIS/weather → `http://192.168.86.196:8999`

Protect all four hostnames with a Cloudflare Access **Service Auth** policy and
one service token. Set the resulting HTTPS hostnames and token in the ignored
`image/config.local.env`, select `KIOSK_VARIANT=mom` and `KIOSK_PROFILE=multi`,
then run `sudo ./image/build-mom-image.sh`. The token may instead be supplied
through `MOM_ACCESS_CREDENTIALS_FILE`, pointing to a private JSON file with
`client_id` and `client_secret`. The credentials are stored root-only
on the Pi. CloudConnexa is rejected and disabled for this image. Admin remains
at `http://<pi-address>:8999/admin`, SSH remains key-only as `kiosk`, and the
normal GitHub update timer remains enabled.
