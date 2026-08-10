#!/usr/bin/env python3
import hashlib
import hmac
import html
import json
import os
import re
import secrets
import threading
import time
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONFIG_PATH = ROOT / "config" / "kiosk.json"
STATE_DIR = Path(os.environ.get("KIOSK_STATE_DIR", Path.home() / ".config" / "tv-kiosk"))
CONFIG_PATH = Path(os.environ.get("KIOSK_CONFIG", STATE_DIR / "kiosk.json"))
CREDENTIALS_PATH = Path(os.environ.get("KIOSK_CREDENTIALS", STATE_DIR / "admin.json"))
SESSION_TTL = 8 * 60 * 60
PASSWORD_ROUNDS = 260_000
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
    pages = config.get("pages", [])
    if len(pages) != 3:
        raise ValueError("kiosk config must contain exactly three pages")
    clean_pages = []
    for page in pages:
        name = str(page.get("name", "")).strip()
        url = str(page.get("url", "")).strip()
        parsed = urlparse(url)
        if not name or len(name) > 80:
            raise ValueError("each page needs a name of 80 characters or fewer")
        if parsed.scheme not in ("http", "https") or not parsed.netloc:
            raise ValueError(f"invalid page URL: {url}")
        clean_pages.append({"name": name, "url": url})

    interval = int(config.get("rotation_seconds", 25))
    if not 5 <= interval <= 3600:
        raise ValueError("rotation_seconds must be between 5 and 3600")
    port = int(config.get("listen_port", os.environ.get("KIOSK_PORT", "8999")))
    if not 1024 <= port <= 65535:
        raise ValueError("listen_port must be between 1024 and 65535")
    transition = float(config.get("transition_seconds", 0.7))
    if not 0 <= transition <= 5:
        raise ValueError("transition_seconds must be between 0 and 5")
    title = str(config.get("title", "Rahamin TV Kiosk")).strip()
    if not title or len(title) > 80:
        raise ValueError("title must be between 1 and 80 characters")
    background = str(config.get("background", "#080706")).strip()
    if not re.fullmatch(r"#[0-9a-fA-F]{6}", background):
        raise ValueError("background must be a six-digit color such as #080706")
    theme = str(config.get("theme", "baiamonte"))
    if theme not in ("baiamonte", "midnight", "coastal"):
        raise ValueError("unknown kiosk theme")

    return {
        "title": title,
        "listen_port": port,
        "rotation_seconds": interval,
        "transition_seconds": transition,
        "show_status": bool(config.get("show_status", True)),
        "background": background,
        "theme": theme,
        "pages": clean_pages,
    }


def load_config():
    with FILE_LOCK:
        if not CONFIG_PATH.exists():
            atomic_json_write(CONFIG_PATH, validate_config(default_config()))
        with CONFIG_PATH.open(encoding="utf-8") as handle:
            return validate_config(json.load(handle))


def save_config(config):
    clean = validate_config(config)
    with FILE_LOCK:
        atomic_json_write(CONFIG_PATH, clean)
    return clean


def password_record(username, password):
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, PASSWORD_ROUNDS)
    return {
        "username": username,
        "rounds": PASSWORD_ROUNDS,
        "salt": salt.hex(),
        "password_hash": digest.hex(),
    }


def load_credentials():
    with FILE_LOCK:
        if not CREDENTIALS_PATH.exists():
            atomic_json_write(CREDENTIALS_PATH, password_record("admin", "admin"))
        with CREDENTIALS_PATH.open(encoding="utf-8") as handle:
            return json.load(handle)


def check_credentials(username, password):
    record = load_credentials()
    digest = hashlib.pbkdf2_hmac(
        "sha256", password.encode(), bytes.fromhex(record["salt"]), int(record["rounds"])
    ).hex()
    return hmac.compare_digest(username, record["username"]) and hmac.compare_digest(
        digest, record["password_hash"]
    )


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
    token = secrets.token_urlsafe(32)
    csrf = secrets.token_urlsafe(24)
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


def page_shell(title, content):
    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>{html.escape(title)}</title>
<style>
:root{{--ink:#211b15;--brown:#4b2d1c;--gold:#c99b43;--olive:#68704a;--cream:#f3ecdc;--paper:#fffdf7;--line:#d8cbb3;--danger:#8b2f27}}
*{{box-sizing:border-box}}body{{margin:0;background:linear-gradient(140deg,#18130f,#35291e 55%,#596044);color:var(--ink);font:16px/1.45 system-ui,-apple-system,sans-serif;min-height:100vh}}
.top{{background:#130f0cdd;border-bottom:1px solid #d8b15f55;color:white;padding:22px max(24px,calc((100% - 1120px)/2));display:flex;align-items:center;justify-content:space-between}}
.brand small,.eyebrow{{display:block;color:#d6b367;letter-spacing:.18em;font-size:11px;font-weight:800}}.brand strong{{font:700 25px Georgia,serif}}.top a{{color:#f4dfae;text-decoration:none;margin-left:18px}}
main{{max-width:1120px;margin:28px auto;padding:0 20px 50px}}.hero{{color:white;margin:8px 0 25px}}h1{{font:700 clamp(30px,5vw,52px)/1.05 Georgia,serif;margin:7px 0}}.hero p{{color:#e5dccd;max-width:720px}}
.card{{background:var(--paper);border:1px solid #ffffff55;border-radius:18px;box-shadow:0 18px 50px #0005;margin:18px 0;overflow:hidden}}.card-head{{padding:22px 25px;border-bottom:1px solid var(--line);background:linear-gradient(90deg,#f7f0df,#fff)}}.card-head h2{{font:700 24px Georgia,serif;margin:2px 0}}.body{{padding:24px 25px}}
.grid{{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:17px}}.wide{{grid-column:1/-1}}label{{font-size:13px;font-weight:800;color:#5c4a36;letter-spacing:.02em}}input,select{{display:block;width:100%;margin-top:7px;border:1px solid #cdbfaa;border-radius:9px;background:white;padding:12px;font:inherit;color:#221b14}}input:focus,select:focus{{outline:3px solid #c99b4333;border-color:var(--gold)}}
.page-row{{display:grid;grid-template-columns:.65fr 1.6fr;gap:14px;padding:16px 0;border-bottom:1px solid #e7ddca}}.page-row:last-child{{border-bottom:0}}.actions{{display:flex;gap:12px;align-items:center;justify-content:flex-end;margin-top:20px}}button,.button{{border:0;border-radius:9px;background:var(--brown);color:white;padding:12px 18px;font-weight:800;cursor:pointer;text-decoration:none}}button.gold{{background:var(--gold);color:#24190d}}.note{{color:#756958;font-size:13px}}.flash{{padding:14px 17px;border-radius:10px;background:#e8f0db;color:#334120;margin:15px 0}}.flash.error{{background:#f5ded8;color:var(--danger)}}.check{{display:flex;align-items:center;gap:10px}}.check input{{width:auto;margin:0}}footer{{color:#d9cfbe;text-align:center;font-size:12px;margin-top:28px}}
@media(max-width:700px){{.grid,.page-row{{grid-template-columns:1fr}}.top{{align-items:flex-start;gap:14px;flex-direction:column}}.actions{{justify-content:stretch;flex-direction:column}}button,.button{{width:100%;text-align:center}}}}
</style></head><body><header class="top"><div class="brand"><small>BAIAMONTE • RAHAMIN</small><strong>TV Kiosk Control</strong></div><nav><a href="/tv" target="_blank">Open TV</a><a href="/admin/logout">Sign out</a></nav></header>{content}</body></html>"""


def login_page(message=""):
    notice = f'<div class="flash error">{html.escape(message)}</div>' if message else ""
    content = f"""<main style="max-width:520px"><section class="hero"><span class="eyebrow">SECURE LOCAL CONTROL</span><h1>Welcome aboard.</h1><p>Sign in to manage the Samsung TV kiosk.</p></section><section class="card"><div class="card-head"><h2>Administrator sign in</h2></div><div class="body">{notice}<form method="post" action="/admin/login"><label>Username<input name="username" autocomplete="username" required autofocus></label><br><label>Password<input type="password" name="password" autocomplete="current-password" required></label><div class="actions"><button class="gold" type="submit">Sign in</button></div></form></div></section><footer>Initial credentials: admin / admin — change them after signing in.</footer></main>"""
    return page_shell("Kiosk administrator sign in", content)


def admin_page(config, session, message="", error=False):
    notice = f'<div class="flash{" error" if error else ""}">{html.escape(message)}</div>' if message else ""
    page_rows = "".join(
        f'<div class="page-row"><label>Page {index} name<input name="page_{index}_name" value="{html.escape(page["name"], quote=True)}" required></label><label>Page {index} URL<input type="url" name="page_{index}_url" value="{html.escape(page["url"], quote=True)}" required></label></div>'
        for index, page in enumerate(config["pages"], 1)
    )
    checked = " checked" if config["show_status"] else ""
    content = f"""<main><section class="hero"><span class="eyebrow">SAMSUNG 75-INCH DISPLAY</span><h1>Kiosk settings</h1><p>Control the three rotating displays, timing, appearance, server address, and administrator access.</p></section>{notice}
<form method="post" action="/admin/settings"><input type="hidden" name="csrf" value="{session['csrf']}">
<section class="card"><div class="card-head"><span class="eyebrow">DISPLAY PLAYLIST</span><h2>Three full-screen pages</h2></div><div class="body">{page_rows}</div></section>
<section class="card"><div class="card-head"><span class="eyebrow">PRESENTATION</span><h2>Timing and Baiamonte appearance</h2></div><div class="body grid"><label>Display title<input name="title" value="{html.escape(config['title'], quote=True)}" required></label><label>Rotation time (seconds)<input type="number" min="5" max="3600" name="rotation_seconds" value="{config['rotation_seconds']}" required></label><label>Crossfade time (seconds)<input type="number" min="0" max="5" step="0.1" name="transition_seconds" value="{config['transition_seconds']}" required></label><label>Theme<select name="theme"><option value="baiamonte"{' selected' if config['theme']=='baiamonte' else ''}>Baiamonte heritage</option><option value="midnight"{' selected' if config['theme']=='midnight' else ''}>Midnight aviation</option><option value="coastal"{' selected' if config['theme']=='coastal' else ''}>Miami coastal</option></select></label><label>Screen background<input type="color" name="background" value="{config['background']}"></label><label class="check"><input type="checkbox" name="show_status"{checked}>Show page name when the pointer moves</label></div></section>
<section class="card"><div class="card-head"><span class="eyebrow">SERVER</span><h2>Local web address</h2></div><div class="body grid"><label>Web port<input type="number" min="1024" max="65535" name="listen_port" value="{config['listen_port']}" required></label><p class="note">Port changes take effect after the Pi restarts. The browser launcher follows the saved port automatically.</p></div></section>
<div class="actions"><a class="button" href="/admin">Discard changes</a><button class="gold" type="submit">Save kiosk settings</button></div></form>
<section class="card"><div class="card-head"><span class="eyebrow">SECURITY</span><h2>Change administrator login</h2></div><div class="body"><form class="grid" method="post" action="/admin/credentials"><input type="hidden" name="csrf" value="{session['csrf']}"><label>New username<input name="username" value="{html.escape(session['username'], quote=True)}" required></label><label>Current password<input type="password" name="current_password" required></label><label>New password<input type="password" name="password" required></label><label>Confirm new password<input type="password" name="confirmation" required></label><div class="actions wide"><button type="submit">Change administrator login</button></div></form></div></section><footer>Rahamin TV Kiosk • {html.escape(str(CONFIG_PATH))}</footer></main>"""
    return page_shell("TV Kiosk Control", content)


def theme_colors(theme):
    return {
        "baiamonte": ("#080706", "#c69b49"),
        "midnight": ("#05070a", "#7da8d8"),
        "coastal": ("#071314", "#63b6b1"),
    }[theme]


def frame_page(url, title, config):
    safe_url = html.escape(url, quote=True)
    safe_title = html.escape(title)
    background, _accent = theme_colors(config["theme"])
    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>{safe_title}</title><style>html,body,iframe{{width:100%;height:100%;margin:0;border:0;overflow:hidden;background:{background}}}</style></head><body><iframe src="{safe_url}" title="{safe_title}" allow="fullscreen"></iframe></body></html>"""


def rotation_page(config):
    data = json.dumps(config, separators=(",", ":")).replace("</", "<\\/")
    background, accent = theme_colors(config["theme"])
    status_display = "block" if config["show_status"] else "none"
    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>{html.escape(config['title'])}</title><style>
html,body,#stage{{width:100%;height:100%;margin:0;overflow:hidden;background:{html.escape(config['background'])}}}iframe{{position:absolute;inset:0;width:100%;height:100%;border:0;background:{background};opacity:0;transition:opacity {config['transition_seconds']}s ease}}iframe.active{{opacity:1}}#status{{display:{status_display};position:fixed;right:22px;bottom:18px;z-index:5;color:#f6ead2;background:#1a120de8;border-left:4px solid {accent};border-radius:8px;padding:9px 13px;font:700 15px system-ui;letter-spacing:.03em;opacity:0;transition:opacity .25s}}body:hover #status{{opacity:1}}
</style></head><body><div id="stage"></div><div id="status"></div><script>const config={data};const stage=document.getElementById('stage');const status=document.getElementById('status');const frames=config.pages.map((page,index)=>{{const frame=document.createElement('iframe');frame.src=page.url;frame.title=page.name;frame.allow='fullscreen';if(index===0)frame.className='active';stage.appendChild(frame);return frame;}});let current=0;function show(index){{frames[current].classList.remove('active');current=index;frames[current].classList.add('active');status.textContent=config.pages[current].name;}}status.textContent=config.pages[0].name;setInterval(()=>show((current+1)%frames.length),config.rotation_seconds*1000);</script></body></html>"""


def render_path(path, config):
    if path in ("/", "/tv"):
        return 200, "text/html; charset=utf-8", rotation_page(config)
    if path in ("/airport-tv", "/tv/airport"):
        board = config["pages"][2]
        return 200, "text/html; charset=utf-8", frame_page(board["url"], board["name"], config)
    if path == "/healthz":
        return 200, "application/json", '{"status":"ok"}'
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

    def require_csrf(self, session, form):
        return hmac.compare_digest(form.get("csrf", ""), session["csrf"])

    def do_GET(self):
        path = urlparse(self.path).path.rstrip("/") or "/"
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
            if path == "/admin":
                session = self.require_session()
                if session:
                    self.reply(200, "text/html; charset=utf-8", admin_page(load_config(), session))
                return
            config = load_config()
            self.reply(*render_path(path, config))
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            self.reply(500, "text/plain; charset=utf-8", f"Configuration error: {exc}\n")

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
            if not self.require_csrf(session, form):
                self.reply(403, "text/plain; charset=utf-8", "Invalid security token\n")
                return
            if path == "/admin/settings":
                config = save_config({
                    "title": form.get("title", ""),
                    "listen_port": form.get("listen_port", "8999"),
                    "rotation_seconds": form.get("rotation_seconds", "25"),
                    "transition_seconds": form.get("transition_seconds", "0.7"),
                    "show_status": form.get("show_status") == "on",
                    "background": form.get("background", "#080706"),
                    "theme": form.get("theme", "baiamonte"),
                    "pages": [{"name": form.get(f"page_{i}_name", ""), "url": form.get(f"page_{i}_url", "")} for i in range(1, 4)],
                })
                self.reply(200, "text/html; charset=utf-8", admin_page(config, session, "Settings saved. The TV playlist updates on its next page load; restart the Pi after changing the port."))
                return
            if path == "/admin/credentials":
                change_credentials(form.get("current_password", ""), form.get("username", ""), form.get("password", ""), form.get("confirmation", ""))
                self.redirect("/admin/login")
                return
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
    print(f"TV kiosk listening on http://0.0.0.0:{port}")
    server.serve_forever()
