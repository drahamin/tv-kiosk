#!/usr/bin/env python3
"""Run the hardware-appropriate full-screen browser from kiosk.json."""

import json
import os
import signal
import subprocess
import time
import re
from pathlib import Path
from urllib.error import URLError
from urllib.parse import quote
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[1]
STATE_DIR = Path(os.environ.get("KIOSK_STATE_DIR", Path.home() / ".config" / "tv-kiosk"))
CONFIG_PATH = Path(os.environ.get("KIOSK_CONFIG", STATE_DIR / "kiosk.json"))
DEFAULT_CONFIG = ROOT / "config" / "kiosk.json"
DEBUG_PORT = int(os.environ.get("KIOSK_DEBUG_PORT", "9222"))
CHROMIUM = os.environ.get("KIOSK_CHROMIUM", "chromium")
COG = os.environ.get("KIOSK_COG", "cog")
HARDWARE_PROFILE = os.environ.get("KIOSK_HARDWARE_PROFILE", "multi").strip().lower()
KIOSK_VARIANT = os.environ.get("KIOSK_VARIANT", "auto").strip().lower()
running = True
requested_step = 0
BOOT_MIN_SECONDS = 4


def stop(_signum, _frame):
    global running
    running = False


def request_page(step):
    def handler(_signum, _frame):
        global requested_step
        requested_step = step
    return handler


def load_config():
    source = CONFIG_PATH if CONFIG_PATH.exists() else DEFAULT_CONFIG
    with source.open(encoding="utf-8") as handle:
        config = json.load(handle)
    if not config.get("setup_complete", True):
        port = int(config.get("listen_port", 8999))
        return {"rotation_seconds": 3600, "zoom_percent": int(config.get("zoom_percent", 100)), "audio_enabled": bool(config.get("audio_enabled", True)), "audio_volume": int(config.get("audio_volume", 60)), "pages": [{"name": "Configure Rahamin Kiosk", "url": f"http://127.0.0.1:{port}/setup"}]}
    pages = [page for page in config.get("pages", []) if page.get("enabled", True) and page.get("url")]
    if HARDWARE_PROFILE == "zero":
        pages = pages[:1]
    if not 1 <= len(pages) <= 5:
        raise ValueError("Rahamin Kiosk requires one to five enabled pages")
    return {
        "rotation_seconds": max(5, int(config.get("rotation_seconds", 45))),
        "zoom_percent": int(config.get("zoom_percent", 100)),
        "audio_enabled": bool(config.get("audio_enabled", True)),
        "audio_volume": int(config.get("audio_volume", 60)),
        "secondary_display_enabled": bool(config.get("secondary_display_enabled", False)),
        "secondary_display_url": str(config.get("secondary_display_url", "http://192.168.0.10:8101")),
        "secondary_zoom_percent": int(config.get("secondary_zoom_percent", 100)),
        "pages": [{"name": str(page["name"]), "url": str(page["url"])} for page in pages],
    }


def devtools(path, method="GET", timeout=3):
    request = Request(f"http://127.0.0.1:{DEBUG_PORT}{path}", method=method)
    with urlopen(request, timeout=timeout) as response:
        payload = response.read()
    if not payload:
        return None
    try:
        return json.loads(payload)
    except json.JSONDecodeError:
        return payload.decode("utf-8", errors="replace")


def wait_for_chromium(process):
    for _attempt in range(80):
        if process.poll() is not None:
            raise RuntimeError("Chromium exited before its control port was ready")
        try:
            devtools("/json/version")
            return
        except (OSError, URLError, ValueError):
            time.sleep(0.25)
    raise RuntimeError("Chromium control port did not become ready")


def open_tab(url):
    return devtools(f"/json/new?{quote(url, safe='')}", method="PUT")


def activate(tab_id):
    devtools(f"/json/activate/{tab_id}")


def close(tab_id):
    try:
        devtools(f"/json/close/{tab_id}")
    except (OSError, URLError, ValueError):
        pass


def replace_tab(url, old_id=None):
    target = open_tab(url)
    new_id = target["id"]
    activate(new_id)
    if old_id and old_id != new_id:
        close(old_id)
    for target in devtools("/json/list"):
        if target.get("type") == "page" and target.get("id") != new_id:
            close(target["id"])
    return new_id


def configure_audio(config):
    helper = ROOT / "scripts" / "rahamin-kiosk-audio"
    subprocess.run(
        [str(helper), "on" if config["audio_enabled"] else "off", str(config["audio_volume"])],
        check=False,
        timeout=25,
    )


def hardware_model():
    override = os.environ.get("KIOSK_HARDWARE_MODEL", "").strip()
    if override:
        return override
    try:
        return Path("/proc/device-tree/model").read_bytes().rstrip(b"\0").decode()
    except OSError:
        return ""


def dual_hdmi_capable():
    model = hardware_model().lower()
    return "raspberry pi 4" in model or "raspberry pi 5" in model


def constrained_chromium(profile_name):
    """Use a lean Chromium process model on Zero and Baiamonte Pi 3 units."""
    if profile_name == "zero":
        return True
    model = hardware_model().lower()
    return KIOSK_VARIANT == "baiamonte" and "raspberry pi 3" in model


def display_outputs():
    """Return connected Wayland outputs with their current or preferred size."""
    try:
        result = subprocess.run(["wlr-randr"], capture_output=True, text=True, timeout=3, check=False)
    except (OSError, subprocess.SubprocessError):
        return []
    outputs = []
    block = []
    for line in result.stdout.splitlines():
        if line and not line[0].isspace():
            if block:
                outputs.append(block)
            block = [line]
        elif block:
            block.append(line)
    if block:
        outputs.append(block)
    parsed = []
    for lines in outputs:
        name = lines[0].split()[0]
        text = "\n".join(lines)
        match = re.search(r"(\d+)x(\d+) px, [^\n]+\((?:current|preferred)[^)]*\)", text)
        if match:
            parsed.append({"name": name, "width": int(match.group(1)), "height": int(match.group(2))})
    return parsed


def configure_dual_outputs(config):
    if KIOSK_VARIANT == "baiamonte" or not config.get("secondary_display_enabled") or not dual_hdmi_capable():
        return []
    outputs = display_outputs()
    hdmi = sorted((item for item in outputs if item["name"].startswith("HDMI-A-")), key=lambda item: item["name"])
    if len(hdmi) < 2:
        return []
    primary, secondary = hdmi[:2]
    subprocess.run([
        "wlr-randr",
        "--output", primary["name"], "--on", "--preferred", "--pos", "0,0",
        "--output", secondary["name"], "--on", "--preferred", "--pos", f"{primary['width']},0",
    ], check=False, timeout=8)
    return [primary, secondary]


def display_size(output_name=None):
    for output in display_outputs():
        if output_name is None or output["name"] == output_name:
            return output["width"], output["height"]
    try:
        result = subprocess.run(["wlr-randr"], capture_output=True, text=True, timeout=3, check=False)
        match = re.search(r"(\d+)x(\d+) px, [^\n]+\(current\)", result.stdout)
        if match:
            return int(match.group(1)), int(match.group(2))
    except (OSError, subprocess.SubprocessError):
        pass
    return 1920, 1080


def launch_chromium(zoom_percent=100, audio_enabled=True, hardware_profile=None, role="primary", url=None, output_name=None, output_x=0, dual=False):
    suffix = "" if role == "primary" else f"-{role}"
    profile = STATE_DIR / f"chromium-profile{suffix}"
    cache = STATE_DIR / f"chromium-cache{suffix}"
    profile.mkdir(parents=True, exist_ok=True)
    cache.mkdir(parents=True, exist_ok=True)
    width, height = display_size(output_name)
    profile_name = hardware_profile or HARDWARE_PROFILE
    constrained = constrained_chromium(profile_name)
    renderer_limit = 1 if profile_name == "zero" else (2 if constrained else 3)
    cache_size = 134217728 if constrained else 268435456
    command = [
        CHROMIUM,
        f"--ozone-platform={'x11' if dual else 'wayland'}",
        "--password-store=basic",
        f"--user-data-dir={profile}",
        f"--disk-cache-dir={cache}",
        "--remote-debugging-address=127.0.0.1",
        f"--remote-debugging-port={DEBUG_PORT if role == 'primary' else DEBUG_PORT + 1}",
        f"--class={'RahaminPrimary' if role == 'primary' else 'BaiamonteSecondary'}",
        "--start-maximized",
        f"--window-position={output_x},0",
        f"--window-size={width},{height}",
        f"--force-device-scale-factor={zoom_percent / 100:g}",
        "--autoplay-policy=no-user-gesture-required",
        "--noerrdialogs",
        "--disable-infobars",
        "--disable-session-crashed-bubble",
        "--no-first-run",
        "--disable-background-networking",
        "--disable-component-update",
        "--disable-sync",
        "--metrics-recording-only",
        f"--renderer-process-limit={renderer_limit}",
        f"--disk-cache-size={cache_size}",
        "--disable-pinch",
        "--overscroll-history-navigation=0",
    ]
    start_url = url or f"{(ROOT / 'session' / 'boot.html').as_uri()}?profile={quote(profile_name)}&variant={quote(KIOSK_VARIANT)}"
    if dual:
        command.append(f"--app={start_url}")
    else:
        command[command.index("--start-maximized"):command.index("--start-maximized")] = ["--kiosk", "--start-fullscreen"]
        command.append(start_url)
    if constrained:
        heap_size = 192 if profile_name == "zero" else 256
        command[1:1] = ["--enable-low-end-device-mode", "--disable-smooth-scrolling", "--process-per-site", f"--js-flags=--max-old-space-size={heap_size}"]
    if not audio_enabled:
        command.insert(-1, "--mute-audio")
    return subprocess.Popen(command)


def launch_cog(url):
    environment = os.environ.copy()
    environment["COG_PLATFORM_WL_VIEW_FULLSCREEN"] = "1"
    environment["COG_PLATFORM_WL_VIEW_MAXIMIZE"] = "1"
    return subprocess.Popen([
        COG,
        "--platform=wl",
        "--enable-page-cache=false",
        "--enable-offline-web-application-cache=true",
        "--enable-smooth-scrolling=false",
        url,
    ], env=environment)


def supervise_cog():
    """Keep one low-memory WebKit view alive on original ARMv6 Pi Zero boards."""
    while running:
        config = load_config()
        configure_audio(config)
        fingerprint = json.dumps(config, sort_keys=True)
        process = launch_cog(config["pages"][0]["url"])
        print(f"Loaded Pi Zero kiosk page: {config['pages'][0]['name']}", flush=True)
        try:
            while running and process.poll() is None:
                updated = load_config()
                if json.dumps(updated, sort_keys=True) != fingerprint:
                    print("Pi Zero page, zoom, or audio settings changed; restarting Cog", flush=True)
                    break
                time.sleep(2)
        except Exception as exc:
            print(f"Pi Zero Cog controller: {exc}", flush=True)
        finally:
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=8)
                except subprocess.TimeoutExpired:
                    process.kill()
        if running:
            time.sleep(3)


def supervise():
    global requested_step
    if HARDWARE_PROFILE == "zero":
        supervise_cog()
        return
    while running:
        launch_config = load_config()
        configure_audio(launch_config)
        outputs = configure_dual_outputs(launch_config)
        dual = len(outputs) == 2
        secondary_process = None
        if dual:
            secondary_process = launch_chromium(
                launch_config.get("secondary_zoom_percent", 100),
                False,
                role="secondary",
                url=launch_config.get("secondary_display_url", "http://192.168.0.10:8101"),
                output_name=outputs[1]["name"],
                output_x=outputs[0]["width"],
                dual=True,
            )
            time.sleep(1)
        process = launch_chromium(
            launch_config["zoom_percent"],
            launch_config["audio_enabled"],
            role="primary",
            output_name=outputs[0]["name"] if dual else None,
            dual=dual,
        )
        launched_at = time.monotonic()
        tab_id = None
        try:
            wait_for_chromium(process)
            remaining_boot_time = BOOT_MIN_SECONDS - (time.monotonic() - launched_at)
            if remaining_boot_time > 0:
                time.sleep(remaining_boot_time)
            fingerprint = None
            current = 0
            next_rotation = 0
            while running and process.poll() is None:
                config = load_config()
                if secondary_process is not None and secondary_process.poll() is not None:
                    print("Baiamonte second display exited; restarting both displays", flush=True)
                    break
                if config["zoom_percent"] != launch_config["zoom_percent"] or config["audio_enabled"] != launch_config["audio_enabled"]:
                    print("Display scale or audio mode changed; restarting Chromium", flush=True)
                    break
                secondary_settings = ("secondary_display_enabled", "secondary_display_url", "secondary_zoom_percent")
                if any(config.get(key) != launch_config.get(key) for key in secondary_settings):
                    print("Second HDMI settings changed; rebuilding the display layout", flush=True)
                    break
                new_fingerprint = json.dumps(config, sort_keys=True)
                if new_fingerprint != fingerprint:
                    configure_audio(config)
                    tab_id = replace_tab(config["pages"][0]["url"], tab_id)
                    fingerprint = new_fingerprint
                    current = 0
                    next_rotation = float("inf") if len(config["pages"]) == 1 else time.monotonic() + config["rotation_seconds"]
                    print("Loaded kiosk playlist:", ", ".join(page["name"] for page in config["pages"]), flush=True)
                if requested_step and len(config["pages"]) > 1:
                    step = requested_step
                    requested_step = 0
                    current = (current + step) % len(config["pages"])
                    tab_id = replace_tab(config["pages"][current]["url"], tab_id)
                    print(f"Remote selected page {current + 1}: {config['pages'][current]['name']}", flush=True)
                    next_rotation = time.monotonic() + config["rotation_seconds"]
                elif requested_step:
                    requested_step = 0
                if time.monotonic() >= next_rotation:
                    current = (current + 1) % len(config["pages"])
                    tab_id = replace_tab(config["pages"][current]["url"], tab_id)
                    print(f"Showing page {current + 1}: {config['pages'][current]['name']}", flush=True)
                    next_rotation = time.monotonic() + config["rotation_seconds"]
                time.sleep(1)
        except Exception as exc:
            print(f"Kiosk browser controller: {exc}", flush=True)
        finally:
            for browser in (process, secondary_process):
                if browser is None or browser.poll() is not None:
                    continue
                browser.terminate()
                try:
                    browser.wait(timeout=8)
                except subprocess.TimeoutExpired:
                    browser.kill()
        if running:
            time.sleep(3)


if __name__ == "__main__":
    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    signal.signal(signal.SIGUSR1, request_page(1))
    signal.signal(signal.SIGUSR2, request_page(-1))
    supervise()
