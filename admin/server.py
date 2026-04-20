#!/usr/bin/env python3
"""
甘栗大福ブログ 管理画面サーバー
使い方: python3 admin/server.py
→ http://localhost:8888 にアクセス
"""

import json, os, re, cgi, io, mimetypes
from datetime import datetime, timezone, timedelta
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.parse import urlparse, parse_qs

BLOG_ROOT   = Path(__file__).parent.parent
POSTS_DIR   = BLOG_ROOT / "content" / "posts"
IMAGES_DIR  = BLOG_ROOT / "static" / "images" / "posts"
SETTINGS_F  = Path(__file__).parent / "settings.json"
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

    toml = f'''baseURL = "{s.get("baseURL","https://example.com/")}"
languageCode = "ja"
title = "{s.get("title","ブログ")}"
defaultContentLanguage = "ja"
hasCJKLanguage = true
paginate = 10
enableRobotsTXT = true
summaryLength = 70

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
                self._json({"ok": True, "file": p.name})
            except Exception as e:
                self._json({"error": str(e)}, 500)

        elif path == "/api/settings":
            data = json.loads(self.rfile.read(length))
            try:
                save_settings(data)
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

        elif path == "/api/delete":
            body = json.loads(self.rfile.read(length))
            fpath = POSTS_DIR / body.get("file", "")
            if fpath.exists():
                fpath.unlink()
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
