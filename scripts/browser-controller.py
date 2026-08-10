#!/usr/bin/env python3
"""Run Chromium in kiosk mode and rotate full browser tabs from kiosk.json."""

import json
import os
import signal
import subprocess
import time
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


def stop(_signum, _frame):
    global running
    running = False


def load_config():
    source = CONFIG_PATH if CONFIG_PATH.exists() else DEFAULT_CONFIG
    with source.open(encoding="utf-8") as handle:
        config = json.load(handle)
    pages = config.get("pages", [])
    if len(pages) != 3:
        raise ValueError("kiosk browser requires exactly three pages")
    return {
        "rotation_seconds": max(5, int(config.get("rotation_seconds", 25))),
        "pages": [{"name": str(page["name"]), "url": str(page["url"])} for page in pages],
    }


def devtools(path, method="GET", timeout=3):
    request = Request(f"http://127.0.0.1:{DEBUG_PORT}{path}", method=method)
    with urlopen(request, timeout=timeout) as response:
        payload = response.read()
    return json.loads(payload) if payload else None


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


def replace_tabs(config, old_ids):
    new_ids = []
    for page in config["pages"]:
        target = open_tab(page["url"])
        new_ids.append(target["id"])
    activate(new_ids[0])
    for tab_id in old_ids:
        if tab_id not in new_ids:
            close(tab_id)
    for target in devtools("/json/list"):
        if target.get("type") == "page" and target.get("id") not in new_ids:
            close(target["id"])
    return new_ids


def launch_chromium():
    profile = STATE_DIR / "chromium-profile"
    cache = STATE_DIR / "chromium-cache"
    profile.mkdir(parents=True, exist_ok=True)
    cache.mkdir(parents=True, exist_ok=True)
    command = [
        CHROMIUM,
        "--ozone-platform=wayland",
        "--password-store=basic",
        f"--user-data-dir={profile}",
        f"--disk-cache-dir={cache}",
        "--remote-debugging-address=127.0.0.1",
        f"--remote-debugging-port={DEBUG_PORT}",
        "--kiosk",
        "--noerrdialogs",
        "--disable-infobars",
        "--disable-session-crashed-bubble",
        "--no-first-run",
        "--incognito",
        "--disable-pinch",
        "--overscroll-history-navigation=0",
        "about:blank",
    ]
    return subprocess.Popen(command)


def supervise():
    while running:
        process = launch_chromium()
        tab_ids = []
        try:
            wait_for_chromium(process)
            fingerprint = None
            current = 0
            next_rotation = 0
            while running and process.poll() is None:
                config = load_config()
                new_fingerprint = json.dumps(config, sort_keys=True)
                if new_fingerprint != fingerprint:
                    tab_ids = replace_tabs(config, tab_ids)
                    fingerprint = new_fingerprint
                    current = 0
                    next_rotation = time.monotonic() + config["rotation_seconds"]
                    print("Loaded kiosk playlist:", ", ".join(page["name"] for page in config["pages"]), flush=True)
                if time.monotonic() >= next_rotation:
                    current = (current + 1) % len(tab_ids)
                    activate(tab_ids[current])
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
    supervise()
