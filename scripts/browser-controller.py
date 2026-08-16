#!/usr/bin/env python3
"""Run Chromium in kiosk mode and rotate full browser tabs from kiosk.json."""

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
    if not 1 <= len(pages) <= 5:
        raise ValueError("Rahamin Kiosk requires one to five enabled pages")
    return {
        "rotation_seconds": max(5, int(config.get("rotation_seconds", 45))),
        "zoom_percent": int(config.get("zoom_percent", 100)),
        "audio_enabled": bool(config.get("audio_enabled", True)),
        "audio_volume": int(config.get("audio_volume", 60)),
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


def display_size():
    try:
        result = subprocess.run(["wlr-randr"], capture_output=True, text=True, timeout=3, check=False)
        match = re.search(r"(\d+)x(\d+) px, [^\n]+\(current\)", result.stdout)
        if match:
            return int(match.group(1)), int(match.group(2))
    except (OSError, subprocess.SubprocessError):
        pass
    return 1920, 1080


def launch_chromium(zoom_percent=100, audio_enabled=True):
    profile = STATE_DIR / "chromium-profile"
    cache = STATE_DIR / "chromium-cache"
    profile.mkdir(parents=True, exist_ok=True)
    cache.mkdir(parents=True, exist_ok=True)
    width, height = display_size()
    command = [
        CHROMIUM,
        "--ozone-platform=wayland",
        "--password-store=basic",
        f"--user-data-dir={profile}",
        f"--disk-cache-dir={cache}",
        "--remote-debugging-address=127.0.0.1",
        f"--remote-debugging-port={DEBUG_PORT}",
        "--kiosk",
        "--start-fullscreen",
        "--start-maximized",
        "--window-position=0,0",
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
        "--renderer-process-limit=3",
        "--disk-cache-size=268435456",
        "--disable-pinch",
        "--overscroll-history-navigation=0",
        (ROOT / "session" / "boot.html").as_uri(),
    ]
    if not audio_enabled:
        command.insert(-1, "--mute-audio")
    return subprocess.Popen(command)


def supervise():
    global requested_step
    while running:
        launch_config = load_config()
        configure_audio(launch_config)
        process = launch_chromium(launch_config["zoom_percent"], launch_config["audio_enabled"])
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
                if config["zoom_percent"] != launch_config["zoom_percent"] or config["audio_enabled"] != launch_config["audio_enabled"]:
                    print("Display scale or audio mode changed; restarting Chromium", flush=True)
                    break
                new_fingerprint = json.dumps(config, sort_keys=True)
                if new_fingerprint != fingerprint:
                    configure_audio(config)
                    tab_id = replace_tab(config["pages"][0]["url"], tab_id)
                    fingerprint = new_fingerprint
                    current = 0
                    next_rotation = time.monotonic() + config["rotation_seconds"]
                    print("Loaded kiosk playlist:", ", ".join(page["name"] for page in config["pages"]), flush=True)
                if requested_step:
                    step = requested_step
                    requested_step = 0
                    current = (current + step) % len(config["pages"])
                    tab_id = replace_tab(config["pages"][current]["url"], tab_id)
                    print(f"Remote selected page {current + 1}: {config['pages'][current]['name']}", flush=True)
                    next_rotation = time.monotonic() + config["rotation_seconds"]
                if time.monotonic() >= next_rotation:
                    current = (current + 1) % len(config["pages"])
                    tab_id = replace_tab(config["pages"][current]["url"], tab_id)
                    print(f"Showing page {current + 1}: {config['pages'][current]['name']}", flush=True)
                    next_rotation = time.monotonic() + config["rotation_seconds"]
                time.sleep(1)
        except Exception as exc:
            print(f"Kiosk browser controller: {exc}", flush=True)
        finally:
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=8)
                except subprocess.TimeoutExpired:
                    process.kill()
        if running:
            time.sleep(3)


if __name__ == "__main__":
    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    signal.signal(signal.SIGUSR1, request_page(1))
    signal.signal(signal.SIGUSR2, request_page(-1))
    supervise()
