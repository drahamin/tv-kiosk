#!/usr/bin/env python3
import html
import json
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parents[1]
CONFIG_PATH = Path(os.environ.get("KIOSK_CONFIG", ROOT / "config" / "kiosk.json"))
PORT = int(os.environ.get("KIOSK_PORT", "8999"))


def load_config():
    with CONFIG_PATH.open(encoding="utf-8") as handle:
        config = json.load(handle)
    pages = config.get("pages", [])
    if len(pages) != 3:
        raise ValueError("kiosk config must contain exactly three pages")
    interval = int(config.get("rotation_seconds", 25))
    if interval < 5:
        raise ValueError("rotation_seconds must be at least 5")
    return {"rotation_seconds": interval, "pages": pages}


def frame_page(url, title):
    safe_url = html.escape(url, quote=True)
    safe_title = html.escape(title)
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{safe_title}</title><style>
html,body,iframe{{width:100%;height:100%;margin:0;border:0;overflow:hidden;background:#05070a}}
</style></head><body><iframe src="{safe_url}" title="{safe_title}" allow="fullscreen"></iframe></body></html>"""


def rotation_page(config):
    data = json.dumps(config, separators=(",", ":")).replace("</", "<\\/")
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Rahamin TV Kiosk</title><style>
html,body,#stage{{width:100%;height:100%;margin:0;overflow:hidden;background:#05070a}}
iframe{{position:absolute;inset:0;width:100%;height:100%;border:0;background:#05070a;opacity:0;transition:opacity .7s ease}}
iframe.active{{opacity:1}}
#status{{position:fixed;right:18px;bottom:14px;z-index:5;color:#d8e2ee;background:#111a;border:1px solid #fff2;border-radius:8px;padding:7px 10px;font:500 16px system-ui;opacity:0;transition:opacity .25s}}
body:hover #status{{opacity:1}}
</style></head><body><div id="stage"></div><div id="status"></div><script>
const config={data};
const stage=document.getElementById('stage');
const status=document.getElementById('status');
const frames=config.pages.map((page,index)=>{{
  const frame=document.createElement('iframe');
  frame.src=page.url; frame.title=page.name; frame.allow='fullscreen';
  if(index===0) frame.className='active'; stage.appendChild(frame); return frame;
}});
let current=0;
function show(index){{frames[current].classList.remove('active');current=index;frames[current].classList.add('active');status.textContent=config.pages[current].name;}}
status.textContent=config.pages[0].name;
setInterval(()=>show((current+1)%frames.length),config.rotation_seconds*1000);
</script></body></html>"""


def render_path(path, config):
    if path in ("/", "/tv"):
        return 200, "text/html; charset=utf-8", rotation_page(config)
    if path in ("/airport-tv", "/tv/airport"):
        board = config["pages"][2]
        return 200, "text/html; charset=utf-8", frame_page(board["url"], board["name"])
    if path == "/healthz":
        return 200, "application/json", '{"status":"ok"}'
    return 404, "text/plain; charset=utf-8", "Not found\n"


class Handler(BaseHTTPRequestHandler):
    def reply(self, status, content_type, body):
        payload = body.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(payload)

    def do_GET(self):
        path = urlparse(self.path).path.rstrip("/") or "/"
        try:
            config = load_config()
            self.reply(*render_path(path, config))
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            self.reply(500, "text/plain; charset=utf-8", f"Configuration error: {exc}\n")

    def log_message(self, fmt, *args):
        print(f"{self.address_string()} - {fmt % args}")


if __name__ == "__main__":
    server = ThreadingHTTPServer(("0.0.0.0", PORT), Handler)
    print(f"TV kiosk listening on http://0.0.0.0:{PORT}")
    server.serve_forever()
