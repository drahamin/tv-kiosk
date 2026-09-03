#!/usr/bin/env python3
"""Run the hardware-appropriate full-screen browser from kiosk.json."""

import json
import os
import base64
import signal
import socket
import struct
import subprocess
import time
import re
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlparse
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
CLOUDCONNEXA_STATUS = Path(os.environ.get("KIOSK_CLOUDCONNEXA_STATUS", "/run/tv-kiosk/cloudconnexa-status.json"))
BAIAMONTE_LOCAL_HOST = os.environ.get("KIOSK_BAIAMONTE_HOST", "192.168.0.10")
BAIAMONTE_LOCAL_PORT = int(os.environ.get("KIOSK_BAIAMONTE_PORT", "8123"))
BAIAMONTE_VPN_URL = os.environ.get("KIOSK_BAIAMONTE_VPN_URL", "http://ha.dashboard.baiamonte:8123")
running = True
requested_step = 0
BOOT_MIN_SECONDS = 4
PAGE_RETRY_SECONDS = 15
PAGE_HEALTH_SECONDS = 30


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
        "secondary_display_url": str(config.get("secondary_display_url", "http://192.168.0.10:8123")),
        "secondary_zoom_percent": int(config.get("secondary_zoom_percent", 100)),
        "pages": [{"name": str(page["name"]), "url": str(page["url"])} for page in pages],
    }


def cloudconnexa_connected():
    try:
        status = json.loads(CLOUDCONNEXA_STATUS.read_text(encoding="utf-8"))
        return status.get("state") == "VPN connected"
    except (OSError, ValueError):
        return False


def resolved_dashboard_url(url):
    """Swap only the Baiamonte local HA origin for its private VPN origin."""
    if KIOSK_VARIANT != "baiamonte":
        return url
    try:
        parsed = urlparse(str(url))
    except ValueError:
        return url
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    if parsed.hostname == BAIAMONTE_LOCAL_HOST and port == BAIAMONTE_LOCAL_PORT and cloudconnexa_connected():
        return BAIAMONTE_VPN_URL
    return url


def resolved_config(config):
    result = {**config}
    result["pages"] = [{**page, "url": resolved_dashboard_url(page["url"])} for page in config["pages"]]
    result["secondary_display_url"] = resolved_dashboard_url(config.get("secondary_display_url", ""))
    return result


def devtools(path, method="GET", timeout=3, port=DEBUG_PORT):
    request = Request(f"http://127.0.0.1:{port}{path}", method=method)
    with urlopen(request, timeout=timeout) as response:
        payload = response.read()
    if not payload:
        return None
    try:
        return json.loads(payload)
    except json.JSONDecodeError:
        return payload.decode("utf-8", errors="replace")


def websocket_command(websocket_url, method, params=None, timeout=3):
    """Send one Chrome DevTools Protocol command to a local WebSocket."""
    parsed = urlparse(websocket_url)
    if parsed.hostname not in ("127.0.0.1", "localhost"):
        raise ValueError("Refusing a non-local Chromium control socket")
    connection = socket.create_connection((parsed.hostname, parsed.port or DEBUG_PORT), timeout)
    try:
        key = base64.b64encode(os.urandom(16)).decode("ascii")
        request = (
            f"GET {parsed.path} HTTP/1.1\r\nHost: {parsed.hostname}:{parsed.port or DEBUG_PORT}\r\n"
            "Upgrade: websocket\r\nConnection: Upgrade\r\n"
            f"Sec-WebSocket-Key: {key}\r\nSec-WebSocket-Version: 13\r\n\r\n"
        )
        connection.sendall(request.encode("ascii"))
        headers = b""
        while b"\r\n\r\n" not in headers:
            headers += connection.recv(4096)
        response_headers, pending = headers.split(b"\r\n\r\n", 1)
        if b" 101 " not in response_headers.split(b"\r\n", 1)[0]:
            raise RuntimeError("Chromium rejected its local control socket")

        buffered = bytearray(pending)

        def receive(count):
            while len(buffered) < count:
                chunk = connection.recv(max(4096, count - len(buffered)))
                if not chunk:
                    raise RuntimeError("Chromium control socket closed")
                buffered.extend(chunk)
            result = bytes(buffered[:count])
            del buffered[:count]
            return result

        payload = json.dumps({"id": 1, "method": method, "params": params or {}}).encode("utf-8")
        mask = os.urandom(4)
        frame = bytearray([0x81])
        if len(payload) < 126:
            frame.append(0x80 | len(payload))
        elif len(payload) < 65536:
            frame.append(0x80 | 126)
            frame.extend(struct.pack("!H", len(payload)))
        else:
            frame.append(0x80 | 127)
            frame.extend(struct.pack("!Q", len(payload)))
        frame.extend(mask)
        frame.extend(byte ^ mask[index % 4] for index, byte in enumerate(payload))
        connection.sendall(frame)

        while True:
            header = receive(2)
            length = header[1] & 0x7f
            if length == 126:
                length = struct.unpack("!H", receive(2))[0]
            elif length == 127:
                length = struct.unpack("!Q", receive(8))[0]
            response = receive(length)
            decoded = json.loads(response)
            if decoded.get("id") == 1:
                if "error" in decoded:
                    raise RuntimeError(decoded["error"].get("message", "Chromium command failed"))
                return decoded.get("result", {})
    finally:
        connection.close()


def devtools_command(method, params=None, timeout=3, port=DEBUG_PORT):
    """Send one browser-level Chrome DevTools Protocol command."""
    version = devtools("/json/version", timeout=timeout, port=port)
    return websocket_command(version["webSocketDebuggerUrl"], method, params, timeout)


def ensure_fullscreen(port=DEBUG_PORT):
    """Idempotently force the single-display browser over the entire HDMI canvas."""
    targets = [target for target in devtools("/json/list", port=port) if target.get("type") == "page"]
    if not targets:
        return False
    result = devtools_command("Browser.getWindowForTarget", {"targetId": targets[0]["id"]}, port=port)
    window_id = result["windowId"]
    if result.get("bounds", {}).get("windowState") != "fullscreen":
        devtools_command("Browser.setWindowBounds", {"windowId": window_id, "bounds": {"windowState": "fullscreen"}}, port=port)
    return True


def navigate_app_window(url, tab_id=None, port=DEBUG_PORT):
    """Navigate the existing app window without creating browser chrome."""
    targets = [target for target in devtools("/json/list", port=port) if target.get("type") == "page"]
    target = next((item for item in targets if item.get("id") == tab_id), targets[0] if targets else None)
    if target is None:
        return replace_tab(url, tab_id, port)
    websocket_command(target["webSocketDebuggerUrl"], "Page.navigate", {"url": url})
    activate(target["id"], port=port)
    for extra in targets:
        if extra.get("id") != target["id"]:
            close(extra["id"], port=port)
    return target["id"]


def show_page(url, old_id=None, port=DEBUG_PORT, preserve_app_window=False):
    if preserve_app_window:
        return navigate_app_window(url, old_id, port)
    return replace_tab(url, old_id, port)


def wait_for_chromium(process, port=DEBUG_PORT):
    # Two independent 4K profiles can take longer on their first launch while
    # Chromium initializes caches. Avoid a destructive retry loop by allowing
    # each browser a full minute and starting the displays sequentially.
    for _attempt in range(240):
        if process.poll() is not None:
            raise RuntimeError("Chromium exited before its control port was ready")
        try:
            devtools("/json/version", port=port)
            return
        except (OSError, URLError, ValueError):
            time.sleep(0.25)
    raise RuntimeError("Chromium control port did not become ready")


def open_tab(url, port=DEBUG_PORT):
    return devtools(f"/json/new?{quote(url, safe='')}", method="PUT", port=port)


def activate(tab_id, port=DEBUG_PORT):
    devtools(f"/json/activate/{tab_id}", port=port)


def close(tab_id, port=DEBUG_PORT):
    try:
        devtools(f"/json/close/{tab_id}", port=port)
    except (OSError, URLError, ValueError):
        pass


def replace_tab(url, old_id=None, port=DEBUG_PORT):
    target = open_tab(url, port=port)
    new_id = target["id"]
    activate(new_id, port=port)
    if old_id and old_id != new_id:
        close(old_id, port=port)
    for target in devtools("/json/list", port=port):
        if target.get("type") == "page" and target.get("id") != new_id:
            close(target["id"], port=port)
    return new_id


def page_reachable(url, timeout=6):
    """Return true when a web server is reachable, including auth/error responses."""
    if not str(url).lower().startswith(("http://", "https://")):
        return True
    request = Request(str(url), method="HEAD", headers={"User-Agent": "Rahamin-Kiosk/1"})
    try:
        with urlopen(request, timeout=timeout):
            return True
    except HTTPError:
        # A 401, 403, or 405 still proves that the web server is available.
        return True
    except (OSError, URLError, ValueError):
        return False


def wait_for_page(url, process):
    """Keep the branded boot view up until a single-page kiosk is reachable."""
    while running and process.poll() is None:
        candidate = resolved_dashboard_url(url)
        if page_reachable(candidate):
            return candidate
        print(f"Page unavailable; retrying in {PAGE_RETRY_SECONDS} seconds: {candidate}", flush=True)
        time.sleep(PAGE_RETRY_SECONDS)
    return None


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


def place_dual_windows(outputs, primary_process, secondary_process):
    """Place both XWayland app windows exactly over their assigned outputs."""
    if len(outputs) != 2 or primary_process is None or secondary_process is None:
        return False
    placements = (
        (primary_process.pid, outputs[0], 0),
        (secondary_process.pid, outputs[1], outputs[0]["width"]),
    )
    for _attempt in range(40):
        windows = []
        for process_id, output, x_position in placements:
            try:
                found = subprocess.run(
                    ["xdotool", "search", "--onlyvisible", "--pid", str(process_id)],
                    capture_output=True, text=True, timeout=2, check=False,
                )
            except (OSError, subprocess.SubprocessError):
                return False
            ids = [item for item in found.stdout.split() if item.isdigit()]
            if not ids:
                break
            windows.append((ids[-1], output, x_position))
        if len(windows) == 2:
            for window_id, output, x_position in windows:
                subprocess.run([
                    "xdotool", "windowmove", "--sync", window_id, str(x_position), "0",
                    "windowsize", "--sync", window_id, str(output["width"]), str(output["height"]),
                ], check=False, timeout=5)
            subprocess.run(["xdotool", "windowraise", windows[0][0]], check=False, timeout=3)
            return True
        time.sleep(0.25)
    return False


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
        f"--window-position={output_x},0",
        f"--window-size={width},{height}",
        f"--force-device-scale-factor={zoom_percent / 100:g}",
        "--autoplay-policy=no-user-gesture-required",
        "--noerrdialogs",
        "--disable-infobars",
        "--disable-session-crashed-bubble",
        "--no-first-run",
        "--disable-background-networking",
        "--disable-background-timer-throttling",
        "--disable-backgrounding-occluded-windows",
        "--disable-renderer-backgrounding",
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
        command[1:1] = ["--kiosk", "--start-fullscreen", "--start-maximized"]
        # App mode removes Chromium's tab and address bars. Keep the kiosk and
        # fullscreen flags as well so both X11 and Wayland releases cover the
        # entire HDMI canvas without exposing browser navigation controls.
        command.append(f"--app={start_url}")
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
        config = resolved_config(load_config())
        configure_audio(config)
        fingerprint = json.dumps(config, sort_keys=True)
        process = launch_cog(config["pages"][0]["url"])
        print(f"Loaded Pi Zero kiosk page: {config['pages'][0]['name']}", flush=True)
        try:
            while running and process.poll() is None:
                updated = resolved_config(load_config())
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
        secondary_tab_id = None
        secondary_url = resolved_dashboard_url(launch_config.get("secondary_display_url", "http://192.168.0.10:8123"))
        secondary_waiting = False
        if dual:
            secondary_ready = page_reachable(secondary_url)
            secondary_boot_url = f"{(ROOT / 'session' / 'boot.html').as_uri()}?profile=multi&variant=baiamonte"
            secondary_process = launch_chromium(
                launch_config.get("secondary_zoom_percent", 100),
                False,
                role="secondary",
                url=secondary_url if secondary_ready else secondary_boot_url,
                output_name=outputs[1]["name"],
                output_x=outputs[0]["width"],
                dual=True,
            )
            secondary_waiting = not secondary_ready
            wait_for_chromium(secondary_process, DEBUG_PORT + 1)
            time.sleep(0.5)
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
            if dual and not place_dual_windows(outputs, process, secondary_process):
                print("Could not confirm dual-window placement; compositor rules remain active", flush=True)
            elif not dual:
                ensure_fullscreen()
            remaining_boot_time = BOOT_MIN_SECONDS - (time.monotonic() - launched_at)
            if remaining_boot_time > 0:
                time.sleep(remaining_boot_time)
            fingerprint = None
            current = 0
            next_rotation = 0
            next_geometry_check = 0
            next_page_health_check = 0
            next_secondary_retry = 0
            current_page_was_unreachable = False
            secondary_was_unreachable = secondary_waiting
            while running and process.poll() is None:
                raw_config = load_config()
                config = resolved_config(raw_config)
                if secondary_process is not None and secondary_process.poll() is not None:
                    print("Baiamonte second display exited; restarting both displays", flush=True)
                    break
                if raw_config["zoom_percent"] != launch_config["zoom_percent"] or raw_config["audio_enabled"] != launch_config["audio_enabled"]:
                    print("Display scale or audio mode changed; restarting Chromium", flush=True)
                    break
                secondary_settings = ("secondary_display_enabled", "secondary_display_url", "secondary_zoom_percent")
                if any(raw_config.get(key) != launch_config.get(key) for key in secondary_settings):
                    print("Second HDMI settings changed; rebuilding the display layout", flush=True)
                    break
                new_fingerprint = json.dumps(config, sort_keys=True)
                if new_fingerprint != fingerprint:
                    configure_audio(config)
                    if len(config["pages"]) == 1:
                        reachable_url = wait_for_page(raw_config["pages"][0]["url"], process)
                        if not reachable_url:
                            break
                        config["pages"][0]["url"] = reachable_url
                    tab_id = show_page(config["pages"][0]["url"], tab_id, preserve_app_window=dual)
                    fingerprint = new_fingerprint
                    current = 0
                    next_rotation = float("inf") if len(config["pages"]) == 1 else time.monotonic() + config["rotation_seconds"]
                    print("Loaded kiosk playlist:", ", ".join(page["name"] for page in config["pages"]), flush=True)
                now = time.monotonic()
                if len(config["pages"]) == 1 and now >= next_page_health_check:
                    reachable = page_reachable(config["pages"][0]["url"])
                    if reachable and current_page_was_unreachable:
                        tab_id = show_page(config["pages"][0]["url"], tab_id, preserve_app_window=dual)
                        print("Kiosk page connection restored; reloaded automatically", flush=True)
                    current_page_was_unreachable = not reachable
                    next_page_health_check = now + PAGE_HEALTH_SECONDS
                if secondary_process is not None and now >= next_secondary_retry:
                    reachable = page_reachable(secondary_url)
                    if reachable and (secondary_waiting or secondary_was_unreachable):
                        secondary_tab_id = show_page(secondary_url, secondary_tab_id, port=DEBUG_PORT + 1, preserve_app_window=True)
                        secondary_waiting = False
                        print("Baiamonte second display connection restored; reloaded automatically", flush=True)
                    secondary_was_unreachable = not reachable
                    next_secondary_retry = now + (PAGE_RETRY_SECONDS if secondary_was_unreachable else PAGE_HEALTH_SECONDS)
                if requested_step and len(config["pages"]) > 1:
                    step = requested_step
                    requested_step = 0
                    current = (current + step) % len(config["pages"])
                    tab_id = show_page(config["pages"][current]["url"], tab_id, preserve_app_window=dual)
                    print(f"Remote selected page {current + 1}: {config['pages'][current]['name']}", flush=True)
                    next_rotation = time.monotonic() + config["rotation_seconds"]
                elif requested_step:
                    requested_step = 0
                if time.monotonic() >= next_rotation:
                    current = (current + 1) % len(config["pages"])
                    tab_id = show_page(config["pages"][current]["url"], tab_id, preserve_app_window=dual)
                    print(f"Showing page {current + 1}: {config['pages'][current]['name']}", flush=True)
                    next_rotation = time.monotonic() + config["rotation_seconds"]
                # Chromium can reapply the desktop work-area geometry when a
                # newly navigated app page changes its native window title.
                # Reassert the two exact HDMI canvases so no panel or border
                # can reappear later in the rotation.
                if time.monotonic() >= next_geometry_check:
                    if dual:
                        place_dual_windows(outputs, process, secondary_process)
                    else:
                        ensure_fullscreen()
                    next_geometry_check = time.monotonic() + 5
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
