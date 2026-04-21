#!/usr/bin/env python3
"""
甘栗大福ブログ 管理画面サーバー
使い方: python3 admin/server.py
→ http://localhost:8888 にアクセス
"""

import json, os, re, cgi, io, mimetypes, subprocess, threading, shlex
import urllib.request, urllib.error
from datetime import datetime, timezone, timedelta, date as dateobj
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.parse import urlparse, parse_qs

BLOG_ROOT   = Path(__file__).parent.parent
POSTS_DIR   = BLOG_ROOT / "content" / "posts"
IMAGES_DIR  = BLOG_ROOT / "static" / "images" / "posts"
SETTINGS_F  = Path(__file__).parent / "settings.json"
EVENTS_F    = BLOG_ROOT / "data" / "events.json"
HUGO_TOML   = BLOG_ROOT / "hugo.toml"
ADMIN_HTML  = Path(__file__).parent / "admin.html"
PORT        = 8888
JST         = timezone(timedelta(hours=9))

DEFAULTS = {
    "title": "甘栗大福",
    "baseURL": "https://higedaihuku.com/",
    "description": "ゲイ向け同人誌サークル「甘栗大福」の活動記録ブログ",
    "authorName": "甘栗大福",
    "authorNameEn": "AMAGURI DAIFUKU",
    "authorNick": "ひげ大福",
    "authorBio": "同人誌を作っている個人サークル「甘栗大福」です。野暮ったい筋肉と情の厚いおじさん達の話を描きます。感想はX・Blueskyまでどうぞ。",
    "heroLine1": "オトナのおじさん達、",
    "heroLine2Em": "ゆっくりと、",
    "heroLine2": "描いています。",
    "heroSub": "ゲイ向け同人誌サークル「甘栗大福」の活動記録です。新刊情報・即売会・通販・FANBOX更新のお知らせをお届けします。",
    "socialLinks": [
        {"name": "X",       "url": "https://x.com/"},
        {"name": "BLSKY",   "url": "https://bsky.app/"},
        {"name": "PIXIV",   "url": "https://pixiv.net/"},
        {"name": "FANBOX",  "url": "https://fanbox.cc/"},
        {"name": "BOOTH",   "url": "https://higedaihuku.booth.pm/"},
        {"name": "FANZA",   "url": "https://fanza.com/"},
        {"name": "DLSITE",  "url": "https://dlsite.com/"},
        {"name": "MISSKEY", "url": "https://misskey.io/"},
    ]
}

# ── Git push ─────────────────────────────────────────────────────────────────

_git_status = {"state": "idle", "message": ""}

def git_push_bg(commit_msg: str):
    """バックグラウンドで git add -A → commit → push をログインシェル経由で実行する"""
    global _git_status
    _git_status = {"state": "pushing", "message": ""}

    # ログインシェル(-l)で実行することで ~/.zshrc の PATH・認証ヘルパーを引き継ぐ
    safe_msg = commit_msg.replace("'", "'\\''")   # シングルクォートをエスケープ
    script = (
        f"cd {shlex.quote(str(BLOG_ROOT))} && "
        f"git add -A && "
        f"(git commit -m '{safe_msg}' || true) && "
        f"git push"
    )
    try:
        res = subprocess.run(
            ["/bin/zsh", "-l", "-c", script],
            capture_output=True, text=True, timeout=45
        )
        if res.returncode != 0:
            msg = (res.stderr or res.stdout).strip()
            # "nothing to commit" や "up-to-date" は正常扱い
            if any(s in msg for s in ["nothing to commit", "up-to-date", "Everything up-to-date"]):
                _git_status = {"state": "ok", "message": ""}
            else:
                _git_status = {"state": "error", "message": msg}
        else:
            _git_status = {"state": "ok", "message": ""}
    except subprocess.TimeoutExpired:
        _git_status = {"state": "error", "message": "タイムアウト（45秒）。ターミナルから git push を実行してください。"}
    except Exception as e:
        _git_status = {"state": "error", "message": str(e)}

def git_push(commit_msg: str):
    threading.Thread(target=git_push_bg, args=(commit_msg,), daemon=True).start()

# ── Frontmatter ──────────────────────────────────────────────────────────────

def parse_post(path: Path) -> dict:
    text = path.read_text(encoding="utf-8")
    m = re.match(r'^---\n(.*?)\n---\n?(.*)', text, re.DOTALL)
    if not m:
        return {"title": path.stem, "date": "", "draft": False,
                "categories": [], "tags": [], "thumbnail": "", "r18": False, "body": text, "file": path.name}
    fm_raw, body = m.group(1), m.group(2).lstrip("\n")
    fm = {}

    def get(key):
        r = re.search(rf'^{key}:\s*"?([^"\n]*)"?\s*$', fm_raw, re.MULTILINE)
        return r.group(1).strip() if r else ""

    def get_list(key):
        r = re.search(rf'^{key}:\n((?:  - .*\n?)*)', fm_raw, re.MULTILINE)
        if not r: return []
        return [re.sub(r'^  - "?|"?\s*$', '', l).strip() for l in r.group(1).splitlines() if l.strip()]

    return {
        "file":       path.name,
        "title":      get("title"),
        "date":       get("date"),
        "draft":      get("draft") == "true",
        "categories": get_list("categories"),
        "tags":       get_list("tags"),
        "thumbnail":  get("thumbnail"),
        "r18":        get("r18") == "true",
        "body":       body,
    }

def write_post(data: dict) -> Path:
    title     = data.get("title", "").replace('"', '\\"')
    date_str  = data.get("date") or datetime.now(JST).strftime("%Y-%m-%dT%H:%M:%S+09:00")
    draft     = "true" if data.get("draft") else "false"
    r18       = "true" if data.get("r18") else "false"
    thumbnail = data.get("thumbnail", "")
    cats      = data.get("categories", [])
    tags      = data.get("tags", [])
    body      = data.get("body", "")

    cats_yaml = ("\ncategories:\n" + "\n".join(f'  - "{c}"' for c in cats)) if cats else ""
    tags_yaml = ("\ntags:\n"       + "\n".join(f'  - "{t}"' for t in tags)) if tags else ""
    thumb_yaml = f'\nthumbnail: "{thumbnail}"' if thumbnail else ""
    r18_yaml  = f"\nr18: {r18}" if data.get("r18") else ""

    fm = f'---\ntitle: "{title}"\ndate: {date_str}\ndraft: {draft}{cats_yaml}{tags_yaml}{thumb_yaml}{r18_yaml}\n---\n\n{body}'

    # filename
    fname = data.get("file")
    if not fname:
        slug = re.sub(r'[^\w\-]', '-', title)[:40].strip('-') or "post"
        date_slug = datetime.now(JST).strftime("%Y%m%d")
        fname = f"{date_slug}-{slug}.md"

    path = POSTS_DIR / fname
    path.write_text(fm, encoding="utf-8")
    return path

# ── Settings ─────────────────────────────────────────────────────────────────

def load_settings() -> dict:
    if SETTINGS_F.exists():
        return json.loads(SETTINGS_F.read_text(encoding="utf-8"))
    SETTINGS_F.write_text(json.dumps(DEFAULTS, ensure_ascii=False, indent=2))
    return DEFAULTS.copy()

def save_settings(data: dict):
    SETTINGS_F.write_text(json.dumps(data, ensure_ascii=False, indent=2))
    _write_hugo_toml(data)

def _write_hugo_toml(s: dict):
    links = ""
    for l in s.get("socialLinks", []):
        links += f'\n[[params.socialLinks]]\n  name = "{l["name"]}"\n  url  = "{l["url"]}"\n'

    gc_site = s.get("goatcounterSite", "")
    gc_line = f'\n  goatcounterSite = "{gc_site}"' if gc_site else ""

    toml = f'''baseURL = "{s.get("baseURL","https://example.com/")}"
languageCode = "ja"
title = "{s.get("title","ブログ")}"
defaultContentLanguage = "ja"
hasCJKLanguage = true
enableRobotsTXT = true
summaryLength = 70

[pagination]
  pagerSize = 10

[params]
  description    = "{s.get("description","")}"
  authorName     = "{s.get("authorName","")}"
  authorNameEn   = "{s.get("authorNameEn","")}"
  authorNick     = "{s.get("authorNick","")}"
  authorBio      = "{s.get("authorBio","")}"
  heroLine1      = "{s.get("heroLine1","")}"
  heroLine2Em    = "{s.get("heroLine2Em","")}"
  heroLine2      = "{s.get("heroLine2","")}"
  heroSub        = "{s.get("heroSub","")}"
{gc_line}
{links}
[taxonomies]
  category = "categories"
  tag      = "tags"
'''
    HUGO_TOML.write_text(toml, encoding="utf-8")

# ── HTTP Handler ──────────────────────────────────────────────────────────────

class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        print(f"  {args[0]} {args[1]}")

    def _json(self, data, code=200):
        body = json.dumps(data, ensure_ascii=False).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Content-Length", len(body))
        self.end_headers()
        self.wfile.write(body)

    def _html(self, body: bytes):
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", len(body))
        self.end_headers()
        self.wfile.write(body)

    def do_OPTIONS(self):
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET,POST,PUT,DELETE,OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

    def do_GET(self):
        parsed = urlparse(self.path)
        path   = parsed.path
        qs     = parse_qs(parsed.query)

        if path == "/" or path == "/admin":
            self._html(ADMIN_HTML.read_bytes())

        elif path == "/api/posts":
            files = sorted(POSTS_DIR.glob("*.md"), key=lambda p: p.stat().st_mtime, reverse=True)
            posts = []
            for f in files:
                p = parse_post(f)
                posts.append({k: v for k, v in p.items() if k != "body"})
            self._json(posts)

        elif path == "/api/post":
            fname = qs.get("file", [""])[0]
            fpath = POSTS_DIR / fname
            if not fpath.exists():
                self._json({"error": "not found"}, 404)
            else:
                self._json(parse_post(fpath))

        elif path == "/api/settings":
            self._json(load_settings())

        elif path == "/api/git-status":
            self._json(_git_status)

        elif path == "/api/events":
            if EVENTS_F.exists():
                self._json(json.loads(EVENTS_F.read_text(encoding="utf-8")))
            else:
                self._json([])

        elif path == "/api/analytics":
            cfg = load_settings()
            site  = cfg.get("goatcounterSite", "").strip()
            token = cfg.get("goatcounterToken", "").strip()
            if not site or not token:
                self._json({"configured": False})
                return
            today = datetime.now(JST).date()
            start = today - timedelta(days=29)
            results = {"configured": True}
            # daily hits per page (past 30 days)
            for key, endpoint in [
                ("pages", f"/api/v0/stats/hits?start={start}&end={today}&daily=true&limit=50"),
            ]:
                try:
                    url = f"https://{site}.goatcounter.com{endpoint}"
                    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {token}"})
                    with urllib.request.urlopen(req, timeout=8) as r:
                        results[key] = json.loads(r.read())
                except urllib.error.HTTPError as e:
                    results[key] = {"error": f"HTTP {e.code}: {e.reason}"}
                except Exception as e:
                    results[key] = {"error": str(e)}
            self._json(results)

        elif path.startswith("/images/"):
            # serve uploaded images
            img_path = BLOG_ROOT / "static" / path.lstrip("/")
            if img_path.exists():
                ct, _ = mimetypes.guess_type(str(img_path))
                data = img_path.read_bytes()
                self.send_response(200)
                self.send_header("Content-Type", ct or "application/octet-stream")
                self.send_header("Content-Length", len(data))
                self.end_headers()
                self.wfile.write(data)
            else:
                self._json({"error": "not found"}, 404)
        else:
            self._json({"error": "not found"}, 404)

    def do_POST(self):
        parsed = urlparse(self.path)
        path   = parsed.path
        length = int(self.headers.get("Content-Length", 0))

        if path == "/api/post":
            body = json.loads(self.rfile.read(length))
            try:
                p = write_post(body)
                title = body.get("title", p.name)
                git_push(f"記事を更新: {title}")
                self._json({"ok": True, "file": p.name})
            except Exception as e:
                self._json({"error": str(e)}, 500)

        elif path == "/api/settings":
            data = json.loads(self.rfile.read(length))
            try:
                save_settings(data)
                git_push("サイト設定を更新")
                self._json({"ok": True})
            except Exception as e:
                self._json({"error": str(e)}, 500)

        elif path == "/api/upload":
            ct = self.headers.get("Content-Type", "")
            # parse multipart
            environ = {"REQUEST_METHOD": "POST", "CONTENT_TYPE": ct, "CONTENT_LENGTH": str(length)}
            raw = self.rfile.read(length)
            fs = cgi.FieldStorage(fp=io.BytesIO(raw), headers=self.headers, environ=environ)
            fileitem = fs["file"] if "file" in fs else None
            if fileitem and fileitem.filename:
                IMAGES_DIR.mkdir(parents=True, exist_ok=True)
                ext  = Path(fileitem.filename).suffix.lower()
                name = f"{datetime.now(JST).strftime('%Y%m%d%H%M%S')}{ext}"
                (IMAGES_DIR / name).write_bytes(fileitem.file.read())
                self._json({"ok": True, "url": f"/images/posts/{name}"})
            else:
                self._json({"error": "no file"}, 400)

        elif path == "/api/events":
            data = json.loads(self.rfile.read(length))
            try:
                EVENTS_F.parent.mkdir(parents=True, exist_ok=True)
                EVENTS_F.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
                git_push("イベント情報を更新")
                self._json({"ok": True})
            except Exception as e:
                self._json({"error": str(e)}, 500)

        elif path == "/api/delete":
            body = json.loads(self.rfile.read(length))
            fpath = POSTS_DIR / body.get("file", "")
            if fpath.exists():
                title = parse_post(fpath).get("title", fpath.name)
                fpath.unlink()
                git_push(f"記事を削除: {title}")
                self._json({"ok": True})
            else:
                self._json({"error": "not found"}, 404)
        else:
            self._json({"error": "not found"}, 404)

if __name__ == "__main__":
    POSTS_DIR.mkdir(parents=True, exist_ok=True)
    IMAGES_DIR.mkdir(parents=True, exist_ok=True)
    if not SETTINGS_F.exists():
        save_settings(DEFAULTS)
    print(f"\n🍡 甘栗大福 管理画面")
    print(f"   http://localhost:{PORT}\n")
    HTTPServer(("", PORT), Handler).serve_forever()
