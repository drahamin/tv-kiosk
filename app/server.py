#!/usr/bin/env python3
import hashlib
import hmac
import html
import json
import os
import platform
import re
import secrets
import shutil
import socket
import subprocess
import threading
import time
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, urlparse
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONFIG_PATH = ROOT / "config" / "kiosk.json"
STATE_DIR = Path(os.environ.get("KIOSK_STATE_DIR", Path.home() / ".config" / "tv-kiosk"))
CONFIG_PATH = Path(os.environ.get("KIOSK_CONFIG", STATE_DIR / "kiosk.json"))
CREDENTIALS_PATH = Path(os.environ.get("KIOSK_CREDENTIALS", STATE_DIR / "admin.json"))
NETWORK_REQUEST_PATH = STATE_DIR / "network-request.json"
NETWORK_STATUS_PATH = STATE_DIR / "network-status.json"
SESSION_TTL = 8 * 60 * 60
PASSWORD_ROUNDS = 260_000
MAX_PAGES = 5
ZOOM_LEVELS = (50, 67, 75, 80, 90, 100, 110, 125, 150, 175, 200)
SESSIONS = {}
SESSION_LOCK = threading.Lock()
FILE_LOCK = threading.Lock()


def atomic_json_write(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    os.chmod(temporary, 0o600)
    os.replace(temporary, path)


def default_config():
    with DEFAULT_CONFIG_PATH.open(encoding="utf-8") as handle:
        return json.load(handle)


def validate_config(config):
    incoming = list(config.get("pages", []))
    if not incoming or len(incoming) > MAX_PAGES:
        raise ValueError("Rahamin Kiosk supports one to five configured pages")
    original_count = len(incoming)
    while len(incoming) < MAX_PAGES:
        incoming.append({"name": f"Page {len(incoming) + 1}", "url": "", "enabled": False})

    pages = []
    for index, page in enumerate(incoming, 1):
        enabled = bool(page.get("enabled", index <= original_count))
        name = str(page.get("name", "")).strip() or f"Page {index}"
        url = str(page.get("url", "")).strip()
        if len(name) > 80:
            raise ValueError(f"Page {index} name must be 80 characters or fewer")
        if enabled and not url:
            raise ValueError(f"Page {index} needs a URL before it can be enabled")
        if url:
            parsed = urlparse(url)
            if parsed.scheme not in ("http", "https") or not parsed.netloc:
                raise ValueError(f"Page {index} has an invalid URL")
        pages.append({"name": name, "url": url, "enabled": enabled})
    if not any(page["enabled"] for page in pages):
        raise ValueError("At least one page must be enabled")

    interval = int(config.get("rotation_seconds", 25))
    if not 5 <= interval <= 3600:
        raise ValueError("Rotation time must be between 5 and 3600 seconds")
    port = int(config.get("listen_port", os.environ.get("KIOSK_PORT", "8999")))
    if not 1024 <= port <= 65535:
        raise ValueError("Web port must be between 1024 and 65535")
    transition = float(config.get("transition_seconds", 0.7))
    if not 0 <= transition <= 5:
        raise ValueError("Transition time must be between 0 and 5 seconds")
    title = str(config.get("title", "Rahamin Kiosk")).strip()
    if not title or len(title) > 80:
        raise ValueError("Display title must be between 1 and 80 characters")
    background = str(config.get("background", "#080706")).strip()
    if not re.fullmatch(r"#[0-9a-fA-F]{6}", background):
        raise ValueError("Background must be a six-digit color such as #080706")
    theme = str(config.get("theme", "rahamin"))
    if theme not in ("rahamin", "midnight", "coastal"):
        theme = "rahamin"
    zoom_percent = int(config.get("zoom_percent", 100))
    if zoom_percent not in ZOOM_LEVELS:
        raise ValueError("Zoom must be one of the available levels")
    return {
        "title": title,
        "listen_port": port,
        "rotation_seconds": interval,
        "transition_seconds": transition,
        "show_status": bool(config.get("show_status", True)),
        "background": background,
        "theme": theme,
        "zoom_percent": zoom_percent,
        "setup_complete": bool(config.get("setup_complete", True)),
        "pages": pages,
    }


def load_config():
    with FILE_LOCK:
        if not CONFIG_PATH.exists():
            atomic_json_write(CONFIG_PATH, validate_config(default_config()))
        with CONFIG_PATH.open(encoding="utf-8") as handle:
            raw = json.load(handle)
        clean = validate_config(raw)
        if clean != raw:
            atomic_json_write(CONFIG_PATH, clean)
        return clean


def save_config(config):
    clean = validate_config(config)
    with FILE_LOCK:
        atomic_json_write(CONFIG_PATH, clean)
    return clean


def password_record(username, password):
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, PASSWORD_ROUNDS)
    return {"username": username, "rounds": PASSWORD_ROUNDS, "salt": salt.hex(), "password_hash": digest.hex()}


def load_credentials():
    with FILE_LOCK:
        if not CREDENTIALS_PATH.exists():
            atomic_json_write(CREDENTIALS_PATH, password_record("admin", "admin"))
        with CREDENTIALS_PATH.open(encoding="utf-8") as handle:
            return json.load(handle)


def check_credentials(username, password):
    record = load_credentials()
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(record["salt"]), int(record["rounds"])).hex()
    return hmac.compare_digest(username, record["username"]) and hmac.compare_digest(digest, record["password_hash"])


def change_credentials(current_password, username, password, confirmation):
    current = load_credentials()
    if not check_credentials(current["username"], current_password):
        raise ValueError("Current password is incorrect")
    if not re.fullmatch(r"[A-Za-z0-9_.-]{3,32}", username):
        raise ValueError("Username must be 3–32 letters, numbers, dots, dashes, or underscores")
    if len(password) < 5:
        raise ValueError("New password must be at least 5 characters")
    if password != confirmation:
        raise ValueError("New passwords do not match")
    with FILE_LOCK:
        atomic_json_write(CREDENTIALS_PATH, password_record(username, password))
    with SESSION_LOCK:
        SESSIONS.clear()


def new_session(username):
    token, csrf = secrets.token_urlsafe(32), secrets.token_urlsafe(24)
    with SESSION_LOCK:
        SESSIONS[token] = {"username": username, "csrf": csrf, "expires": time.time() + SESSION_TTL}
    return token, csrf


def get_session(cookie_header):
    cookie = SimpleCookie()
    try:
        cookie.load(cookie_header or "")
        token = cookie["kiosk_session"].value
    except (KeyError, ValueError):
        return None
    with SESSION_LOCK:
        session = SESSIONS.get(token)
        if not session or session["expires"] < time.time():
            SESSIONS.pop(token, None)
            return None
        session["expires"] = time.time() + SESSION_TTL
        return {**session, "token": token}


def run_command(command, timeout=3):
    try:
        result = subprocess.run(command, capture_output=True, text=True, timeout=timeout, check=False)
        return result.stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return ""


def human_duration(seconds):
    seconds = int(seconds)
    days, seconds = divmod(seconds, 86400)
    hours, seconds = divmod(seconds, 3600)
    minutes, _seconds = divmod(seconds, 60)
    return " ".join(part for part in (f"{days}d" if days else "", f"{hours}h" if hours else "", f"{minutes}m") if part) or "under 1m"


def read_meminfo():
    values = {}
    try:
        for line in Path("/proc/meminfo").read_text(encoding="utf-8").splitlines():
            key, value = line.split(":", 1)
            values[key] = int(value.strip().split()[0]) * 1024
    except (OSError, ValueError):
        pass
    return values


def current_browser_page():
    try:
        with urlopen("http://127.0.0.1:9222/json/list", timeout=2) as response:
            targets = json.load(response)
        return next(target.get("url", "") for target in targets if target.get("type") == "page")
    except (OSError, URLError, ValueError, StopIteration):
        return "Not available"


def system_snapshot():
    mem = read_meminfo()
    total = mem.get("MemTotal", 0)
    available = mem.get("MemAvailable", 0)
    swap_total = mem.get("SwapTotal", 0)
    swap_free = mem.get("SwapFree", 0)
    disk = shutil.disk_usage("/")
    try:
        uptime = float(Path("/proc/uptime").read_text().split()[0])
    except (OSError, ValueError, IndexError):
        uptime = 0
    try:
        temperature = int(Path("/sys/class/thermal/thermal_zone0/temp").read_text()) / 1000
    except (OSError, ValueError):
        temperature = 0
    model = "Raspberry Pi"
    try:
        model = Path("/proc/device-tree/model").read_bytes().rstrip(b"\0").decode()
    except OSError:
        pass
    nm = run_command(["nmcli", "-t", "-f", "GENERAL.CONNECTION,GENERAL.HWADDR,IP4.ADDRESS,IP4.GATEWAY,IP4.DNS", "device", "show", "wlan0"])
    network = {}
    for line in nm.splitlines():
        if ":" in line:
            key, value = line.split(":", 1)
            network.setdefault(key, []).append(value)
    wifi = run_command(["nmcli", "-t", "-f", "IN-USE,SSID,SIGNAL,SECURITY", "device", "wifi", "list", "--rescan", "no"])
    active_wifi = next((line for line in wifi.splitlines() if line.startswith("*:")), "")
    wifi_parts = active_wifi.split(":")
    services = {name: run_command(["systemctl", "is-active", name]) or "unknown" for name in ("tv-kiosk-web.service", "tv-kiosk-update.timer")}
    remote = "Connected" if Path("/dev/cec0").exists() else "HDMI-CEC unavailable"
    commit = run_command(["git", "-c", "safe.directory=/opt/tv-kiosk", "-C", "/opt/tv-kiosk", "rev-parse", "--short", "HEAD"])
    load = os.getloadavg()
    return {
        "hostname": socket.gethostname(),
        "model": model,
        "kernel": platform.release(),
        "uptime": human_duration(uptime),
        "temperature": f"{temperature:.1f}°C" if temperature else "Unavailable",
        "load": f"{load[0]:.2f} / {load[1]:.2f} / {load[2]:.2f}",
        "memory": f"{(total - available) / 1048576:.0f} MB used / {available / 1048576:.0f} MB available" if total else "Unavailable",
        "swap": f"{(swap_total - swap_free) / 1048576:.0f} MB used / {swap_free / 1048576:.0f} MB available" if swap_total else "Disabled",
        "disk": f"{disk.used / 1073741824:.1f} GB used / {disk.free / 1073741824:.1f} GB available",
        "connection": (network.get("GENERAL.CONNECTION", [""])[0] or "Not connected"),
        "ssid": wifi_parts[1] if len(wifi_parts) > 1 else (network.get("GENERAL.CONNECTION", [""])[0] or "Unavailable"),
        "signal": f"{wifi_parts[2]}%" if len(wifi_parts) > 2 and wifi_parts[2] else "Unavailable",
        "security": wifi_parts[3] if len(wifi_parts) > 3 else "Unavailable",
        "address": ", ".join(network.get("IP4.ADDRESS[1]", []) or network.get("IP4.ADDRESS", [])) or "Unavailable",
        "gateway": ", ".join(network.get("IP4.GATEWAY", [])) or "Unavailable",
        "dns": ", ".join(value for key, values in network.items() if key.startswith("IP4.DNS") for value in values) or "Unavailable",
        "mac": ", ".join(network.get("GENERAL.HWADDR", [])) or "Unavailable",
        "current_page": current_browser_page(),
        "web_service": services["tv-kiosk-web.service"],
        "updater": services["tv-kiosk-update.timer"],
        "remote": remote,
        "commit": commit or "Unavailable",
        "timestamp": time.strftime("%Y-%m-%d %H:%M:%S %Z"),
    }


def test_page(url):
    started = time.monotonic()
    try:
        request = Request(url, headers={"User-Agent": "Rahamin-Kiosk/1.0", "Range": "bytes=0-1023"})
        with urlopen(request, timeout=8) as response:
            status = response.status
            response.read(1024)
        return {"ok": 200 <= status < 400, "status": status, "latency_ms": round((time.monotonic() - started) * 1000), "message": "Page responded"}
    except HTTPError as exc:
        return {"ok": False, "status": exc.code, "latency_ms": round((time.monotonic() - started) * 1000), "message": str(exc.reason)}
    except (OSError, URLError) as exc:
        return {"ok": False, "status": 0, "latency_ms": round((time.monotonic() - started) * 1000), "message": str(exc.reason if isinstance(exc, URLError) else exc)}


def nm_value(connection, field):
    return run_command(["nmcli", "-g", field, "connection", "show", connection]) if connection else ""


def connection_name(device, connection_type):
    active = run_command(["nmcli", "-g", "GENERAL.CONNECTION", "device", "show", device])
    if active and active != "--":
        return active
    for line in run_command(["nmcli", "-t", "-f", "NAME,TYPE", "connection", "show"]).splitlines():
        name, _, kind = line.partition(":")
        if kind == connection_type:
            return name
    return ""


def network_configuration():
    wifi = connection_name("wlan0", "802-11-wireless")
    ethernet = connection_name("eth0", "802-3-ethernet")
    wifi_security = nm_value(wifi, "802-11-wireless-security.key-mgmt")
    return {
        "hostname": socket.gethostname(),
        "wifi_enabled": run_command(["nmcli", "radio", "wifi"]) == "enabled",
        "wifi_autoconnect": nm_value(wifi, "connection.autoconnect") != "no",
        "wifi_ssid": nm_value(wifi, "802-11-wireless.ssid"),
        "wifi_password": "",
        "wifi_security": "open" if not wifi_security else "wpa-psk",
        "wifi_mac_policy": nm_value(wifi, "802-11-wireless.cloned-mac-address") or "preserve",
        "wifi_ipv4_mode": nm_value(wifi, "ipv4.method") or "auto",
        "wifi_ipv4_address": nm_value(wifi, "ipv4.addresses"),
        "wifi_ipv4_gateway": nm_value(wifi, "ipv4.gateway"),
        "wifi_ipv4_dns": nm_value(wifi, "ipv4.dns").replace(";", ","),
        "wifi_ipv6_mode": nm_value(wifi, "ipv6.method") or "auto",
        "wifi_ipv6_address": nm_value(wifi, "ipv6.addresses"),
        "wifi_ipv6_gateway": nm_value(wifi, "ipv6.gateway"),
        "wifi_ipv6_dns": nm_value(wifi, "ipv6.dns").replace(";", ","),
        "ethernet_enabled": nm_value(ethernet, "connection.autoconnect") != "no",
        "ethernet_ipv4_mode": nm_value(ethernet, "ipv4.method") or "auto",
        "ethernet_ipv4_address": nm_value(ethernet, "ipv4.addresses"),
        "ethernet_ipv4_gateway": nm_value(ethernet, "ipv4.gateway"),
        "ethernet_ipv4_dns": nm_value(ethernet, "ipv4.dns").replace(";", ","),
        "ethernet_ipv6_mode": nm_value(ethernet, "ipv6.method") or "auto",
        "ethernet_ipv6_address": nm_value(ethernet, "ipv6.addresses"),
        "ethernet_ipv6_gateway": nm_value(ethernet, "ipv6.gateway"),
        "ethernet_ipv6_dns": nm_value(ethernet, "ipv6.dns").replace(";", ","),
    }


def validate_network_request(form):
    data = {
        "hostname": form.get("hostname", "").strip(),
        "wifi_enabled": form.get("wifi_enabled") == "on",
        "wifi_autoconnect": form.get("wifi_autoconnect") == "on",
        "wifi_ssid": form.get("wifi_ssid", "").strip(),
        "wifi_password": form.get("wifi_password", ""),
        "wifi_security": form.get("wifi_security", "wpa-psk"),
        "wifi_mac_policy": form.get("wifi_mac_policy", "preserve"),
        "ethernet_enabled": form.get("ethernet_enabled") == "on",
    }
    for prefix in ("wifi_ipv4", "wifi_ipv6", "ethernet_ipv4", "ethernet_ipv6"):
        data[f"{prefix}_mode"] = form.get(f"{prefix}_mode", "auto")
        data[f"{prefix}_address"] = form.get(f"{prefix}_address", "").strip()
        data[f"{prefix}_gateway"] = form.get(f"{prefix}_gateway", "").strip()
        data[f"{prefix}_dns"] = form.get(f"{prefix}_dns", "").strip()
    if not re.fullmatch(r"(?=.{1,63}$)[A-Za-z0-9](?:[A-Za-z0-9-]*[A-Za-z0-9])?", data["hostname"]):
        raise ValueError("Hostname must contain only letters, numbers, and internal dashes")
    if not 1 <= len(data["wifi_ssid"]) <= 32:
        raise ValueError("Wi-Fi SSID must be 1–32 characters")
    if data["wifi_security"] not in ("wpa-psk", "open"):
        raise ValueError("Unknown Wi-Fi security mode")
    if data["wifi_password"] and not 8 <= len(data["wifi_password"]) <= 63:
        raise ValueError("Wi-Fi password must be 8–63 characters")
    if data["wifi_mac_policy"] not in ("preserve", "permanent", "random"):
        raise ValueError("Unknown Wi-Fi MAC policy")
    for prefix in ("wifi_ipv4", "ethernet_ipv4"):
        if data[f"{prefix}_mode"] not in ("auto", "manual"):
            raise ValueError("IPv4 mode must be automatic or manual")
        if data[f"{prefix}_mode"] == "manual" and not data[f"{prefix}_address"]:
            raise ValueError("A static IPv4 address with prefix is required")
    for prefix in ("wifi_ipv6", "ethernet_ipv6"):
        if data[f"{prefix}_mode"] not in ("auto", "manual", "disabled"):
            raise ValueError("IPv6 mode must be automatic, manual, or disabled")
        if data[f"{prefix}_mode"] == "manual" and not data[f"{prefix}_address"]:
            raise ValueError("A static IPv6 address with prefix is required")
    return data


def apply_network_later():
    time.sleep(2)
    subprocess.run(["sudo", "-n", "/usr/local/sbin/rahamin-kiosk-network"], check=False, timeout=90)


def page_shell(title, content):
    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>{html.escape(title)}</title><style>
:root{{--ink:#171f22;--navy:#101c24;--blue:#1c5366;--gold:#d3a94f;--teal:#3d7d7a;--paper:#fbfcfa;--line:#d8e0dd;--muted:#617075;--good:#2f7653;--danger:#9b3d35}}
*{{box-sizing:border-box}}body{{margin:0;background:linear-gradient(140deg,#0c171d,#18323b 55%,#355d59);color:var(--ink);font:15px/1.45 system-ui,-apple-system,sans-serif;min-height:100vh}}
.top{{position:sticky;top:0;z-index:10;background:#09151bdd;border-bottom:1px solid #d3a94f55;color:white;padding:18px max(22px,calc((100% - 1180px)/2));display:flex;align-items:center;justify-content:space-between;backdrop-filter:blur(12px)}}
.brand small,.eyebrow{{display:block;color:var(--gold);letter-spacing:.18em;font-size:10px;font-weight:900}}.brand strong{{font:700 24px Georgia,serif}}.top a{{color:#f7dfaa;text-decoration:none;margin-left:18px;font-weight:700}}
main{{max-width:1180px;margin:25px auto;padding:0 18px 50px}}.hero{{color:white;margin:5px 0 22px}}h1{{font:700 clamp(30px,5vw,50px)/1.05 Georgia,serif;margin:7px 0}}.hero p{{color:#d7e2e1;max-width:780px}}
.card{{background:var(--paper);border:1px solid #ffffff66;border-radius:16px;box-shadow:0 16px 45px #0004;margin:17px 0;overflow:hidden}}.card-head{{padding:18px 22px;border-bottom:1px solid var(--line);background:linear-gradient(90deg,#eef4f1,#fff)}}.card-head h2{{font:700 23px Georgia,serif;margin:2px 0}}.body{{padding:21px 22px}}
.grid{{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:15px}}.stats{{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:12px}}.stat{{background:#eef3f1;border:1px solid #d8e2df;border-radius:11px;padding:13px}}.stat small{{display:block;color:var(--muted);font-size:10px;font-weight:900;letter-spacing:.1em}}.stat b{{display:block;margin-top:4px;overflow-wrap:anywhere}}.wide{{grid-column:1/-1}}
label{{font-size:12px;font-weight:850;color:#43545a;letter-spacing:.02em}}input,select{{display:block;width:100%;margin-top:6px;border:1px solid #bdcbc8;border-radius:8px;background:white;padding:11px;font:inherit;color:#172025}}input:focus,select:focus{{outline:3px solid #3d7d7a33;border-color:var(--teal)}}
.page-row{{display:grid;grid-template-columns:auto .65fr 1.45fr auto;gap:12px;align-items:end;padding:15px 0;border-bottom:1px solid #dce5e2}}.page-row:last-child{{border-bottom:0}}.page-number{{align-self:center;width:35px;height:35px;border-radius:50%;display:grid;place-items:center;background:var(--navy);color:white;font-weight:900}}.page-tools{{display:flex;gap:7px;align-items:center;padding-bottom:1px}}.test-result{{font-size:12px;min-width:75px;color:var(--muted)}}
.actions{{display:flex;gap:10px;align-items:center;justify-content:flex-end;margin-top:18px;flex-wrap:wrap}}button,.button{{border:0;border-radius:8px;background:var(--blue);color:white;padding:11px 16px;font-weight:850;cursor:pointer;text-decoration:none}}button.gold{{background:var(--gold);color:#1e180d}}button.secondary,.button.secondary{{background:#e3ece9;color:#24424a}}button.danger{{background:var(--danger)}}.note{{color:var(--muted);font-size:12px}}.flash{{padding:13px 16px;border-radius:9px;background:#dceee4;color:#23583d;margin:14px 0}}.flash.error{{background:#f4dedb;color:var(--danger)}}.check{{display:flex;align-items:center;gap:8px;white-space:nowrap}}.check input{{width:auto;margin:0}}.status-good{{color:var(--good)}}.status-bad{{color:var(--danger)}}footer{{color:#cbd9d7;text-align:center;font-size:12px;margin-top:27px}}
@media(max-width:900px){{.stats{{grid-template-columns:repeat(2,1fr)}}.page-row{{grid-template-columns:auto 1fr}}.page-row label:nth-of-type(2),.page-tools{{grid-column:2}}}}@media(max-width:650px){{.grid,.stats{{grid-template-columns:1fr}}.top{{align-items:flex-start;gap:12px;flex-direction:column}}.actions{{flex-direction:column}}button,.button{{width:100%;text-align:center}}}}
</style></head><body><header class="top"><div class="brand"><small>RAHAMIN OPERATIONS</small><strong>Rahamin Kiosk</strong></div><nav><a href="/tv" target="_blank">Open display</a><a href="/admin/config.json">Backup</a><a href="/admin/logout">Sign out</a></nav></header>{content}</body></html>"""


def login_page(message=""):
    notice = f'<div class="flash error">{html.escape(message)}</div>' if message else ""
    content = f"""<main style="max-width:520px"><section class="hero"><span class="eyebrow">SECURE LOCAL CONTROL</span><h1>Rahamin Kiosk</h1><p>Sign in to manage the Samsung TV display.</p></section><section class="card"><div class="card-head"><h2>Administrator sign in</h2></div><div class="body">{notice}<form method="post" action="/admin/login"><label>Username<input name="username" autocomplete="username" required autofocus></label><br><label>Password<input type="password" name="password" autocomplete="current-password" required></label><div class="actions"><button class="gold" type="submit">Sign in</button></div></form></div></section><footer>Rahamin Kiosk secure local administration</footer></main>"""
    return page_shell("Rahamin Kiosk sign in", content)


def setup_page(config):
    snapshot = system_snapshot()
    port = int(config["listen_port"])
    addresses = run_command(["hostname", "-I"]).split()
    links = "".join(f'<div class="stat"><small>ADMIN ADDRESS</small><b>http://{html.escape(address)}:{port}/admin</b></div>' for address in addresses if ":" not in address)
    if not links:
        links = '<div class="stat"><small>NETWORK</small><b>Waiting for a DHCP address…</b></div>'
    return f'''<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><meta http-equiv="refresh" content="10"><title>Configure Rahamin Kiosk</title><style>html,body{{margin:0;min-height:100%;background:linear-gradient(135deg,#071219,#173640 60%,#35625d);color:white;font:22px/1.45 system-ui,sans-serif}}main{{max-width:1300px;margin:auto;padding:7vh 6vw}}.eyebrow{{color:#d3a94f;letter-spacing:.2em;font-size:15px;font-weight:900}}h1{{font:700 clamp(50px,7vw,92px)/1 Georgia,serif;margin:18px 0}}p{{color:#d7e5e3;max-width:1050px}}.steps{{display:grid;grid-template-columns:repeat(3,1fr);gap:20px;margin:40px 0}}.step,.stat{{background:#fff;color:#15252a;border-radius:16px;padding:25px;box-shadow:0 15px 40px #0005}}.step b{{display:block;color:#1c5366;font-size:28px;margin-bottom:8px}}.addresses{{display:grid;grid-template-columns:repeat(2,1fr);gap:16px}}.stat small{{display:block;color:#627579;font-size:13px;font-weight:900;letter-spacing:.12em}}.stat b{{display:block;color:#17252a;font-size:25px;overflow-wrap:anywhere}}footer{{margin-top:35px;color:#b9cfcb}}@media(max-width:800px){{.steps,.addresses{{grid-template-columns:1fr}}}}</style></head><body><main><span class="eyebrow">FIRST DEPLOYMENT • NO KEYBOARD REQUIRED</span><h1>Configure Rahamin Kiosk</h1><p>Ethernet and Wi‑Fi use DHCP automatically. Connect a phone or computer to the same network, then open one of the admin addresses shown below.</p><div class="steps"><div class="step"><b>1. Connect</b>Plug in Ethernet, or use the preloaded <strong>Home</strong> Wi‑Fi network.</div><div class="step"><b>2. Sign in</b>Open the admin page. New images begin with <strong>admin / admin</strong>.</div><div class="step"><b>3. Save</b>Configure network and up to five pages, change the password, then save display settings.</div></div><div class="addresses">{links}<div class="stat"><small>HOSTNAME</small><b>http://{html.escape(snapshot['hostname'])}.local:{port}/admin</b></div></div><footer>This screen refreshes every 10 seconds and disappears after setup is saved.</footer></main></body></html>'''


def stat_card(label, value, key=""):
    return f'<div class="stat"{f" data-status=\"{key}\"" if key else ""}><small>{html.escape(label)}</small><b>{html.escape(str(value))}</b></div>'


def admin_page(config, session, message="", error=False, snapshot=None):
    snapshot = snapshot or system_snapshot()
    net = network_configuration()
    notice = f'<div class="flash{" error" if error else ""}">{html.escape(message)}</div>' if message else ""
    page_rows = ""
    for index, page in enumerate(config["pages"], 1):
        checked = " checked" if page["enabled"] else ""
        page_rows += f'''<div class="page-row"><div class="page-number">{index}</div><label>Page name<input name="page_{index}_name" value="{html.escape(page['name'], quote=True)}"></label><label>Page URL<input type="url" name="page_{index}_url" value="{html.escape(page['url'], quote=True)}" placeholder="http://device.local:port/tv"></label><div class="page-tools"><label class="check"><input type="checkbox" name="page_{index}_enabled"{checked}>Enabled</label><button class="secondary test-page" type="button" data-index="{index}">Test</button><a class="button secondary" href="{html.escape(page['url'], quote=True) if page['url'] else '#'}" target="_blank">Open</a><span class="test-result" id="test-{index}"></span></div></div>'''
    show_checked = " checked" if config["show_status"] else ""
    setup_checked = " checked" if config["setup_complete"] else ""
    zoom_options = "".join(f'<option value="{level}"{" selected" if config["zoom_percent"] == level else ""}>{level}%</option>' for level in ZOOM_LEVELS)
    hardware = "".join((stat_card("Model", snapshot["model"], "model"), stat_card("Uptime", snapshot["uptime"], "uptime"), stat_card("CPU temperature", snapshot["temperature"], "temperature"), stat_card("Load (1/5/15m)", snapshot["load"], "load"), stat_card("Memory", snapshot["memory"], "memory"), stat_card("Swap", snapshot["swap"], "swap"), stat_card("Storage", snapshot["disk"], "disk"), stat_card("Kernel", snapshot["kernel"], "kernel")))
    network = "".join((stat_card("Hostname", snapshot["hostname"], "hostname"), stat_card("Wi-Fi network", snapshot["ssid"], "ssid"), stat_card("Signal", snapshot["signal"], "signal"), stat_card("Security", snapshot["security"], "security"), stat_card("IP address", snapshot["address"], "address"), stat_card("Gateway", snapshot["gateway"], "gateway"), stat_card("DNS", snapshot["dns"], "dns"), stat_card("Wi-Fi MAC", snapshot["mac"], "mac")))
    def selected(value, expected):
        return " selected" if value == expected else ""
    def checked(value):
        return " checked" if value else ""
    network_form = f'''<form method="post" action="/admin/network"><input type="hidden" name="csrf" value="{session['csrf']}"><div class="grid"><label>Hostname<input name="hostname" value="{html.escape(net['hostname'], quote=True)}" required></label><label class="check"><input type="checkbox" name="wifi_enabled"{checked(net['wifi_enabled'])}>Wi-Fi radio enabled</label><label>Wi-Fi SSID<input name="wifi_ssid" value="{html.escape(net['wifi_ssid'], quote=True)}" required></label><label>Wi-Fi password<input type="password" name="wifi_password" placeholder="leave blank to keep saved password"></label><label>Wi-Fi security<select name="wifi_security"><option value="wpa-psk"{selected(net['wifi_security'],'wpa-psk')}>WPA/WPA2/WPA3 personal</option><option value="open"{selected(net['wifi_security'],'open')}>Open network</option></select></label><label>Wi-Fi MAC policy<select name="wifi_mac_policy"><option value="preserve"{selected(net['wifi_mac_policy'],'preserve')}>Preserve current MAC</option><option value="permanent"{selected(net['wifi_mac_policy'],'permanent')}>Hardware MAC</option><option value="random"{selected(net['wifi_mac_policy'],'random')}>Random MAC</option></select></label><label class="check"><input type="checkbox" name="wifi_autoconnect"{checked(net['wifi_autoconnect'])}>Connect to Wi-Fi automatically</label></div>
<h3>Wi-Fi IPv4</h3><div class="grid"><label>Mode<select name="wifi_ipv4_mode"><option value="auto"{selected(net['wifi_ipv4_mode'],'auto')}>Automatic (DHCP)</option><option value="manual"{selected(net['wifi_ipv4_mode'],'manual')}>Static</option></select></label><label>Address / prefix<input name="wifi_ipv4_address" value="{html.escape(net['wifi_ipv4_address'], quote=True)}" placeholder="192.168.86.118/24"></label><label>Gateway<input name="wifi_ipv4_gateway" value="{html.escape(net['wifi_ipv4_gateway'], quote=True)}"></label><label>DNS servers<input name="wifi_ipv4_dns" value="{html.escape(net['wifi_ipv4_dns'], quote=True)}" placeholder="1.1.1.1, 8.8.8.8"></label></div>
<h3>Wi-Fi IPv6</h3><div class="grid"><label>Mode<select name="wifi_ipv6_mode"><option value="auto"{selected(net['wifi_ipv6_mode'],'auto')}>Automatic</option><option value="manual"{selected(net['wifi_ipv6_mode'],'manual')}>Static</option><option value="disabled"{selected(net['wifi_ipv6_mode'],'disabled')}>Disabled</option></select></label><label>Address / prefix<input name="wifi_ipv6_address" value="{html.escape(net['wifi_ipv6_address'], quote=True)}"></label><label>Gateway<input name="wifi_ipv6_gateway" value="{html.escape(net['wifi_ipv6_gateway'], quote=True)}"></label><label>DNS servers<input name="wifi_ipv6_dns" value="{html.escape(net['wifi_ipv6_dns'], quote=True)}"></label></div>
<h3>Ethernet</h3><div class="grid"><label class="check"><input type="checkbox" name="ethernet_enabled"{checked(net['ethernet_enabled'])}>Ethernet connection enabled</label><label>IPv4 mode<select name="ethernet_ipv4_mode"><option value="auto"{selected(net['ethernet_ipv4_mode'],'auto')}>Automatic (DHCP)</option><option value="manual"{selected(net['ethernet_ipv4_mode'],'manual')}>Static</option></select></label><label>IPv4 address / prefix<input name="ethernet_ipv4_address" value="{html.escape(net['ethernet_ipv4_address'], quote=True)}"></label><label>IPv4 gateway<input name="ethernet_ipv4_gateway" value="{html.escape(net['ethernet_ipv4_gateway'], quote=True)}"></label><label>IPv4 DNS<input name="ethernet_ipv4_dns" value="{html.escape(net['ethernet_ipv4_dns'], quote=True)}"></label><label>IPv6 mode<select name="ethernet_ipv6_mode"><option value="auto"{selected(net['ethernet_ipv6_mode'],'auto')}>Automatic</option><option value="manual"{selected(net['ethernet_ipv6_mode'],'manual')}>Static</option><option value="disabled"{selected(net['ethernet_ipv6_mode'],'disabled')}>Disabled</option></select></label><label>IPv6 address / prefix<input name="ethernet_ipv6_address" value="{html.escape(net['ethernet_ipv6_address'], quote=True)}"></label><label>IPv6 gateway<input name="ethernet_ipv6_gateway" value="{html.escape(net['ethernet_ipv6_gateway'], quote=True)}"></label><label>IPv6 DNS<input name="ethernet_ipv6_dns" value="{html.escape(net['ethernet_ipv6_dns'], quote=True)}"></label></div><p class="flash error"><strong>Network changes can disconnect this browser.</strong> Verify the SSID, password, static address, gateway, and DNS before applying. The saved Wi-Fi password is never displayed.</p><div class="actions"><button class="danger" type="submit">Apply network configuration</button></div></form>'''
    operations = "".join((stat_card("Current page", snapshot["current_page"], "current_page"), stat_card("Samsung remote", snapshot["remote"], "remote"), stat_card("Web service", snapshot["web_service"], "web_service"), stat_card("Auto-updater", snapshot["updater"], "updater"), stat_card("Installed version", snapshot["commit"], "commit")))
    content = f"""<main><section class="hero"><span class="eyebrow">SAMSUNG 75-INCH DISPLAY</span><h1>Rahamin Kiosk control center</h1><p>Manage the playlist, test every source, review the Pi and network, and keep the display healthy.</p></section>{notice}
<section class="card"><div class="card-head"><span class="eyebrow">LIVE OPERATIONS</span><h2>Kiosk status</h2></div><div class="body"><div class="stats" id="operations-stats">{operations}</div><div class="actions"><span class="note" id="status-time">Updated {html.escape(snapshot['timestamp'])}</span><button class="secondary" type="button" id="refresh-status">Refresh status</button><form method="post" action="/admin/action"><input type="hidden" name="csrf" value="{session['csrf']}"><input type="hidden" name="action" value="start-display"><button type="submit">Start display</button></form><form method="post" action="/admin/action"><input type="hidden" name="csrf" value="{session['csrf']}"><input type="hidden" name="action" value="stop-display"><button class="secondary" type="submit">Stop display</button></form><form method="post" action="/admin/restart-display"><input type="hidden" name="csrf" value="{session['csrf']}"><button type="submit">Restart display</button></form><form method="post" action="/admin/action"><input type="hidden" name="csrf" value="{session['csrf']}"><input type="hidden" name="action" value="force-update"><button class="gold" type="submit">Force update now</button></form><form method="post" action="/admin/action" onsubmit="return confirm('Reboot Rahamin Kiosk now?')"><input type="hidden" name="csrf" value="{session['csrf']}"><input type="hidden" name="action" value="reboot"><button class="danger" type="submit">Reboot Pi</button></form></div></div></section>
<section class="card"><div class="card-head"><span class="eyebrow">HARDWARE</span><h2>Raspberry Pi health</h2></div><div class="body"><div class="stats" id="hardware-stats">{hardware}</div></div></section>
<section class="card"><div class="card-head"><span class="eyebrow">NETWORK STATUS</span><h2>Current connection</h2></div><div class="body"><div class="stats" id="network-stats">{network}</div></div></section>
<section class="card"><div class="card-head"><span class="eyebrow">NETWORK CONFIGURATION</span><h2>Wi-Fi, Ethernet, IPv4, IPv6, DNS, and hostname</h2></div><div class="body">{network_form}</div></section>
<form method="post" action="/admin/settings"><input type="hidden" name="csrf" value="{session['csrf']}"><section class="card"><div class="card-head"><span class="eyebrow">PLAYLIST</span><h2>Up to five full-screen pages</h2></div><div class="body">{page_rows}<p class="note">Disabled pages stay saved but are skipped by the TV. At least one page must remain enabled.</p></div></section>
<section class="card"><div class="card-head"><span class="eyebrow">DISPLAY</span><h2>Rahamin Kiosk appearance and timing</h2></div><div class="body grid"><label>Display title<input name="title" value="{html.escape(config['title'], quote=True)}" required></label><label>Rotation time (seconds)<input type="number" min="5" max="3600" name="rotation_seconds" value="{config['rotation_seconds']}" required></label><label>Transition time (seconds)<input type="number" min="0" max="5" step="0.1" name="transition_seconds" value="{config['transition_seconds']}" required></label><label>Page zoom<select name="zoom_percent">{zoom_options}</select></label><label>Theme<select name="theme"><option value="rahamin"{' selected' if config['theme']=='rahamin' else ''}>Rahamin signature</option><option value="midnight"{' selected' if config['theme']=='midnight' else ''}>Midnight aviation</option><option value="coastal"{' selected' if config['theme']=='coastal' else ''}>Miami coastal</option></select></label><label>Screen background<input type="color" name="background" value="{config['background']}"></label><label class="check"><input type="checkbox" name="show_status"{show_checked}>Show page name on pointer movement</label><label class="check"><input type="checkbox" name="setup_complete"{setup_checked}>Setup complete — show the rotating pages</label><p class="note wide"><strong>Samsung remote (Anynet+):</strong> arrows and OK navigate the page, Play/Pause controls media, Channel Up/Down changes browser zoom, and 0 resets zoom to 100%. Enable Anynet+ (HDMI-CEC) in the TV settings.</p></div></section>
<section class="card"><div class="card-head"><span class="eyebrow">SERVER</span><h2>Local administration address</h2></div><div class="body grid"><label>Web port<input type="number" min="1024" max="65535" name="listen_port" value="{config['listen_port']}" required></label><p class="note">Port changes take effect after the Pi restarts. Keep 8999 unless another service requires it.</p></div></section><div class="actions"><a class="button secondary" href="/admin">Discard changes</a><button class="gold" type="submit">Save Rahamin Kiosk settings</button></div></form>
<section class="card"><div class="card-head"><span class="eyebrow">SECURITY</span><h2>Administrator login</h2></div><div class="body"><form class="grid" method="post" action="/admin/credentials"><input type="hidden" name="csrf" value="{session['csrf']}"><label>New username<input name="username" value="{html.escape(session['username'], quote=True)}" required></label><label>Current password<input type="password" name="current_password" required></label><label>New password<input type="password" name="password" required></label><label>Confirm new password<input type="password" name="confirmation" required></label><div class="actions wide"><button type="submit">Change administrator login</button></div></form></div></section><footer>Rahamin Kiosk • Settings and password changes persist across GitHub updates</footer></main>
<script>
const csrf={json.dumps(session['csrf'])};
document.querySelectorAll('.test-page').forEach(button=>button.addEventListener('click',async()=>{{const target=document.getElementById('test-'+button.dataset.index);target.textContent='Checking…';try{{const response=await fetch('/admin/test-page?index='+button.dataset.index);const result=await response.json();target.textContent=(result.ok?'✓ ':'✕ ')+(result.status||result.message)+' · '+result.latency_ms+'ms';target.className='test-result '+(result.ok?'status-good':'status-bad')}}catch(error){{target.textContent='Test failed';target.className='test-result status-bad'}}}}));
document.getElementById('refresh-status').addEventListener('click',async()=>{{const response=await fetch('/admin/status');const status=await response.json();Object.entries(status).forEach(([key,value])=>{{const node=document.querySelector('[data-status="'+key+'"] b');if(node)node.textContent=value}});document.getElementById('status-time').textContent='Updated '+status.timestamp;}});
</script>"""
    return page_shell("Rahamin Kiosk control center", content)


def theme_colors(theme):
    return {"rahamin": ("#080e12", "#d3a94f"), "midnight": ("#05070a", "#7da8d8"), "coastal": ("#071314", "#63b6b1")}[theme]


def active_pages(config):
    return [page for page in config["pages"] if page["enabled"]]


def frame_page(url, title, config):
    background, _accent = theme_colors(config["theme"])
    return f'''<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>{html.escape(title)}</title><style>html,body,iframe{{width:100%;height:100%;margin:0;border:0;overflow:hidden;background:{background}}}</style></head><body><iframe src="{html.escape(url, quote=True)}" title="{html.escape(title)}" allow="fullscreen"></iframe></body></html>'''


def rotation_page(config):
    browser_config = {**config, "pages": active_pages(config)}
    data = json.dumps(browser_config, separators=(",", ":")).replace("</", "<\\/")
    background, accent = theme_colors(config["theme"])
    status_display = "block" if config["show_status"] else "none"
    return f'''<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>{html.escape(config['title'])}</title><style>html,body,#stage{{width:100%;height:100%;margin:0;overflow:hidden;background:{html.escape(config['background'])}}}iframe{{position:absolute;inset:0;width:100%;height:100%;border:0;background:{background};opacity:0;transition:opacity {config['transition_seconds']}s ease}}iframe.active{{opacity:1}}#status{{display:{status_display};position:fixed;right:22px;bottom:18px;z-index:5;color:#f6ead2;background:#101c24e8;border-left:4px solid {accent};border-radius:8px;padding:9px 13px;font:700 15px system-ui;opacity:0}}body:hover #status{{opacity:1}}</style></head><body><div id="stage"></div><div id="status"></div><script>const config={data};const stage=document.getElementById('stage');const status=document.getElementById('status');const frames=config.pages.map((page,index)=>{{const frame=document.createElement('iframe');frame.src=page.url;frame.title=page.name;if(index===0)frame.className='active';stage.appendChild(frame);return frame;}});let current=0;function show(index){{frames[current].classList.remove('active');current=index;frames[current].classList.add('active');status.textContent=config.pages[current].name}}status.textContent=config.pages[0].name;setInterval(()=>show((current+1)%frames.length),config.rotation_seconds*1000);</script></body></html>'''


def render_path(path, config):
    if path in ("/", "/tv"):
        return 200, "text/html; charset=utf-8", rotation_page(config)
    if path == "/setup":
        return 200, "text/html; charset=utf-8", setup_page(config)
    if path in ("/airport-tv", "/tv/airport"):
        board = next((page for page in config["pages"] if "airport" in page["name"].lower() and page["url"]), active_pages(config)[0])
        return 200, "text/html; charset=utf-8", frame_page(board["url"], board["name"], config)
    if path == "/healthz":
        return 200, "application/json", '{"status":"ok","name":"Rahamin Kiosk"}'
    return 404, "text/plain; charset=utf-8", "Not found\n"


class Handler(BaseHTTPRequestHandler):
    def reply(self, status, content_type, body, headers=None):
        payload = body.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY" if self.path.startswith("/admin") else "SAMEORIGIN")
        self.send_header("Content-Security-Policy", "frame-ancestors 'none'" if self.path.startswith("/admin") else "frame-ancestors 'self'")
        for name, value in headers or []:
            self.send_header(name, value)
        self.end_headers()
        self.wfile.write(payload)

    def json_reply(self, status, data):
        self.reply(status, "application/json", json.dumps(data, separators=(",", ":")))

    def redirect(self, location, headers=None):
        self.send_response(303)
        self.send_header("Location", location)
        for name, value in headers or []:
            self.send_header(name, value)
        self.end_headers()

    def form(self):
        length = int(self.headers.get("Content-Length", "0"))
        if length > 64 * 1024:
            raise ValueError("Request is too large")
        return {key: values[-1] for key, values in parse_qs(self.rfile.read(length).decode(), keep_blank_values=True).items()}

    def require_session(self):
        session = get_session(self.headers.get("Cookie"))
        if not session:
            self.redirect("/admin/login")
        return session

    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path.rstrip("/") or "/"
        try:
            if path == "/admin/login":
                self.reply(200, "text/html; charset=utf-8", login_page())
                return
            if path == "/admin/logout":
                session = get_session(self.headers.get("Cookie"))
                if session:
                    with SESSION_LOCK:
                        SESSIONS.pop(session["token"], None)
                self.redirect("/admin/login", [("Set-Cookie", "kiosk_session=; Path=/; HttpOnly; SameSite=Strict; Max-Age=0")])
                return
            if path.startswith("/admin"):
                session = self.require_session()
                if not session:
                    return
                if path == "/admin":
                    self.reply(200, "text/html; charset=utf-8", admin_page(load_config(), session))
                elif path == "/admin/status":
                    self.json_reply(200, system_snapshot())
                elif path == "/admin/test-page":
                    query = parse_qs(parsed.query)
                    index = int(query.get("index", ["0"])[0]) - 1
                    config = load_config()
                    if not 0 <= index < MAX_PAGES or not config["pages"][index]["url"]:
                        self.json_reply(400, {"ok": False, "status": 0, "latency_ms": 0, "message": "Page URL is empty"})
                    else:
                        self.json_reply(200, test_page(config["pages"][index]["url"]))
                elif path == "/admin/config.json":
                    self.reply(200, "application/json", json.dumps(load_config(), indent=2) + "\n", [("Content-Disposition", "attachment; filename=rahamin-kiosk-config.json")])
                else:
                    self.reply(404, "text/plain; charset=utf-8", "Not found\n")
                return
            self.reply(*render_path(path, load_config()))
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            self.reply(500, "text/plain; charset=utf-8", f"Rahamin Kiosk error: {exc}\n")

    def do_POST(self):
        path = urlparse(self.path).path.rstrip("/")
        try:
            form = self.form()
            if path == "/admin/login":
                if check_credentials(form.get("username", ""), form.get("password", "")):
                    token, _csrf = new_session(form["username"])
                    self.redirect("/admin", [("Set-Cookie", f"kiosk_session={token}; Path=/; HttpOnly; SameSite=Strict; Max-Age={SESSION_TTL}")])
                else:
                    time.sleep(0.5)
                    self.reply(401, "text/html; charset=utf-8", login_page("Incorrect username or password"))
                return
            session = self.require_session()
            if not session:
                return
            if not hmac.compare_digest(form.get("csrf", ""), session["csrf"]):
                self.reply(403, "text/plain; charset=utf-8", "Invalid security token\n")
                return
            if path == "/admin/settings":
                config = save_config({"title": form.get("title", ""), "listen_port": form.get("listen_port", "8999"), "rotation_seconds": form.get("rotation_seconds", "25"), "transition_seconds": form.get("transition_seconds", "0.7"), "zoom_percent": form.get("zoom_percent", "100"), "show_status": form.get("show_status") == "on", "setup_complete": form.get("setup_complete") == "on", "background": form.get("background", "#080706"), "theme": form.get("theme", "rahamin"), "pages": [{"name": form.get(f"page_{i}_name", ""), "url": form.get(f"page_{i}_url", ""), "enabled": form.get(f"page_{i}_enabled") == "on"} for i in range(1, MAX_PAGES + 1)]})
                self.reply(200, "text/html; charset=utf-8", admin_page(config, session, "Settings saved. The live playlist will reload automatically."))
            elif path == "/admin/network":
                if not Path("/usr/local/sbin/rahamin-kiosk-network").exists():
                    raise ValueError("The network helper is not installed yet; allow the GitHub updater to finish and try again")
                request = validate_network_request(form)
                atomic_json_write(NETWORK_REQUEST_PATH, request)
                threading.Thread(target=apply_network_later, daemon=True).start()
                self.reply(200, "text/html; charset=utf-8", admin_page(load_config(), session, "Network configuration accepted. It will apply in two seconds and this address may change."))
            elif path == "/admin/credentials":
                change_credentials(form.get("current_password", ""), form.get("username", ""), form.get("password", ""), form.get("confirmation", ""))
                self.redirect("/admin/login")
            elif path == "/admin/restart-display":
                subprocess.run(["pkill", "-x", "chromium"], check=False)
                self.reply(200, "text/html; charset=utf-8", admin_page(load_config(), session, "Display restart requested. Chromium will return automatically."))
            elif path == "/admin/action":
                action = form.get("action", "")
                if action not in ("force-update", "reboot", "start-display", "stop-display"):
                    raise ValueError("Unknown system action")
                helper = Path("/usr/local/sbin/rahamin-kiosk-action")
                if not helper.exists():
                    raise ValueError("The system action helper is not installed yet; allow the GitHub updater to finish and try again")
                result = subprocess.run(["sudo", "-n", str(helper), action], capture_output=True, text=True, check=False, timeout=15)
                if result.returncode:
                    raise ValueError((result.stderr or result.stdout or "System action failed").strip())
                messages = {"force-update": "GitHub update started. Refresh status in about one minute.", "reboot": "Rahamin Kiosk is rebooting. The display and admin page will return automatically.", "start-display": "Display started.", "stop-display": "Display stopped and will remain off across reboots and updates."}
                message = messages[action]
                self.reply(200, "text/html; charset=utf-8", admin_page(load_config(), session, message))
            else:
                self.reply(404, "text/plain; charset=utf-8", "Not found\n")
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            session = get_session(self.headers.get("Cookie"))
            if session:
                self.reply(400, "text/html; charset=utf-8", admin_page(load_config(), session, str(exc), True))
            else:
                self.reply(400, "text/html; charset=utf-8", login_page(str(exc)))

    def log_message(self, fmt, *args):
        print(f"{self.address_string()} - {fmt % args}")


if __name__ == "__main__":
    startup_config = load_config()
    port = int(startup_config.get("listen_port", os.environ.get("KIOSK_PORT", "8999")))
    server = ThreadingHTTPServer(("0.0.0.0", port), Handler)
    print(f"Rahamin Kiosk listening on http://0.0.0.0:{port}")
    server.serve_forever()
