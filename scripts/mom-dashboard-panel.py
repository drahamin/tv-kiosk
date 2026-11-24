#!/usr/bin/env python3
"""Scope Mom's Samsung panel controls to the local Baiamonte dashboard."""

import json
import runpy
import sys
from pathlib import Path
from urllib.parse import urlparse

controller = Path(__file__).resolve().with_name("browser-controller.py")
if not controller.is_file():
    controller = Path("/opt/tv-kiosk/scripts/browser-controller.py")
runtime = runpy.run_path(str(controller))
KIOSK_VARIANT = runtime["KIOSK_VARIANT"]
DEBUG_PORT = runtime["DEBUG_PORT"]
devtools = runtime["devtools"]
websocket_command = runtime["websocket_command"]
MOM_PANEL_ORIGIN = "http://127.0.0.1:18101"


def mom_panel_action(action):
    """Operate only the unique visible Mom Baiamonte page, not browser/TV input."""
    buttons = {"close": "controlClose", "toggle": "controlToggle"}
    if KIOSK_VARIANT != "mom" or action not in buttons:
        return False
    targets = []
    for port in (DEBUG_PORT, DEBUG_PORT + 1):
        try:
            pages = devtools("/json/list", timeout=0.5, port=port)
        except (OSError, ValueError):
            continue
        if not isinstance(pages, list):
            continue
        for page in pages:
            if (not isinstance(page, dict) or page.get("type") != "page" or
                    not isinstance(page.get("id"), str) or
                    page.get("url") not in (MOM_PANEL_ORIGIN, MOM_PANEL_ORIGIN + "/")):
                continue
            socket_url = page.get("webSocketDebuggerUrl")
            if not isinstance(socket_url, str):
                continue
            try:
                socket_target = urlparse(socket_url)
                if (socket_target.scheme == "ws" and socket_target.hostname == "127.0.0.1" and
                        socket_target.port == port and
                        socket_target.path == "/devtools/page/" + page.get("id", "")):
                    targets.append(socket_url)
            except ValueError:
                continue
    if len(targets) != 1:
        return False
    # Check the document again inside Chromium to avoid acting after navigation.
    skip_closed = "|| panel.hidden" if action == "close" else ""
    expression = (
        "(() => { if (location.origin !== " + json.dumps(MOM_PANEL_ORIGIN) +
        " || location.pathname !== '/' || document.visibilityState !== 'visible' "
        "|| document.title !== 'Tenuta Baiamonte Live') return false; "
        "const panel = document.getElementById('tvControlPanel'); "
        "if (!panel " + skip_closed + ") return false; "
        "const button = document.getElementById(" + json.dumps(buttons[action]) + "); "
        "if (!button) return false; button.click(); return true; })()"
    )
    try:
        result = websocket_command(targets[0], "Runtime.evaluate", {
            "expression": expression, "returnByValue": True,
        }, timeout=0.5)
        return result.get("result", {}).get("value") is True
    except (OSError, ValueError, RuntimeError, KeyError):
        return False


if __name__ == "__main__":
    raise SystemExit(0 if len(sys.argv) == 2 and mom_panel_action(sys.argv[1]) else 1)
