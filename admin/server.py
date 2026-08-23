#!/usr/bin/env python3
"""
甘栗大福ブログ 管理画面サーバー
使い方: python3 admin/server.py
→ http://localhost:8888 にアクセス
"""

import json, os, re, cgi, io, mimetypes, subprocess, threading, shlex, shutil, time
import urllib.request, urllib.error
from datetime import datetime, timezone, timedelta, date as dateobj
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.parse import urlparse, parse_qs, unquote

BLOG_ROOT   = Path(__file__).parent.parent
POSTS_DIR   = BLOG_ROOT / "content" / "posts"
IMAGES_DIR  = BLOG_ROOT / "static" / "images" / "posts"
LOGO_DIR    = BLOG_ROOT / "static" / "images"
SETTINGS_F  = Path(__file__).parent / "settings.json"
EVENTS_F    = BLOG_ROOT / "data" / "events.json"
HUGO_TOML   = BLOG_ROOT / "hugo.toml"
ADMIN_HTML  = Path(__file__).parent / "admin.html"
PORT        = 8888
JST         = timezone(timedelta(hours=9))

# 月別集計のキャッシュ {(site, start, end): (取得時刻, データ)}
MONTHS_CACHE = {}

def parse_ymd(s):
    """'YYYY-MM-DD' を date にする。空・不正なら None。"""
    try:
        return datetime.strptime((s or "").strip(), "%Y-%m-%d").date()
    except ValueError:
        return None

def goatcounter_get(site, token, endpoint, timeout=12):
    """GoatCounter APIを叩く。戻り値 (データ, エラー) のどちらか一方が None。

    GoatCounterは初回アクセスで統計の準備が間に合わず404等を返すことがある
    （リロード＋2回目で見られる症状）。少し待って自動リトライする。
    """
    last_err = None
    for _ in range(4):
        try:
            url = f"https://{site}.goatcounter.com{endpoint}"
            req = urllib.request.Request(url, headers={"Authorization": f"Bearer {token}"})
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return json.loads(r.read()), None
        except urllib.error.HTTPError as e:
            last_err = {"error": f"HTTP {e.code}: {e.reason}"}
            # 404/5xx は準備待ちのことがあるのでリトライ。401/403は権限なので即中断。
            if e.code in (401, 403):
                break
            time.sleep(1.5)
        except Exception as e:
            last_err = {"error": str(e)}
            time.sleep(1.5)
    return None, last_err

def find_logo():
    for ext in ("png", "jpg", "gif"):
        p = LOGO_DIR / f"logo.{ext}"
        if p.exists():
            return p
    return None

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

def shortlink_id(existing_file):
    """短縮リンク /p/<n> の連番を決める。
    既存記事の編集ならその番号を維持、新規なら（全記事の最大+1）を採番する。"""
    # 編集中の記事に既にエイリアスがあれば、それを維持
    if existing_file:
        p = POSTS_DIR / existing_file
        if p.exists():
            m = re.search(r'/p/(\d+)/', p.read_text(encoding="utf-8", errors="ignore"))
            if m:
                return int(m.group(1))
    # 無ければ全記事の最大id+1
    max_id = 0
    for f in POSTS_DIR.glob("*.md"):
        try:
            m = re.search(r'/p/(\d+)/', f.read_text(encoding="utf-8", errors="ignore"))
            if m:
                max_id = max(max_id, int(m.group(1)))
        except Exception:
            pass
    return max_id + 1


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

    # 短縮リンク用の連番エイリアス（新規は採番・編集は番号維持）
    pid = shortlink_id(data.get("file"))
    aliases_yaml = f'\naliases:\n  - "/p/{pid}/"'

    fm = f'---\ntitle: "{title}"\ndate: {date_str}\ndraft: {draft}{aliases_yaml}{cats_yaml}{tags_yaml}{thumb_yaml}{r18_yaml}\n---\n\n{body}'

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
            files = list(POSTS_DIR.glob("*.md"))
            posts = []
            for f in files:
                p = parse_post(f)
                posts.append({k: v for k, v in p.items() if k != "body"})
            # 投稿日付の新しい順にソート（日付なしは末尾）
            posts.sort(key=lambda p: p.get("date") or "", reverse=True)
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

        elif path == "/api/logo-exists":
            logo = find_logo()
            if logo:
                self._json({"exists": True, "url": f"/images/{logo.name}"})
            else:
                self._json({"exists": False})

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
            # 表示期間（?start=YYYY-MM-DD&end=YYYY-MM-DD）。省略時は過去30日。
            start = parse_ymd(qs.get("start", [""])[0]) or (today - timedelta(days=29))
            end   = parse_ymd(qs.get("end",   [""])[0]) or today
            if end   > today: end   = today
            if start > end:   start = end

            results = {"configured": True, "start": str(start), "end": str(end)}

            # GoatCounterのstart/endはUTC基準で区切られるため、JSTだと範囲の
            # 「最初の日」と「最後の日」が9時間ぶん欠ける（例：7月単独で取ると
            # 7/1が66、前後を含めて取ると75）。前後1日ぶん広く取得してから
            # 表示範囲だけ切り出すことで、どの期間で見ても同じ数になるようにする。
            pad_start = start - timedelta(days=1)
            pad_end   = end   + timedelta(days=1)
            in_range  = lambda d: d and str(start) <= d <= str(end)

            # ページ別（期間内）
            pages, err = goatcounter_get(
                site, token,
                f"/api/v0/stats/hits?start={pad_start}&end={pad_end}&daily=true&limit=50")
            if err:
                results["pages"] = err
            else:
                trimmed = []
                for p in (pages.get("hits") or []):
                    stats = [s for s in (p.get("stats") or []) if in_range(s.get("day"))]
                    count = sum(s.get("daily") or 0 for s in stats)
                    if count <= 0:
                        continue
                    q = dict(p)
                    q["stats"], q["count"] = stats, count
                    trimmed.append(q)
                results["pages"] = {"hits": trimmed}

            # 日別合計（サイト全体。上位50ページの合算では取りこぼすため total を使う）
            totals, err2 = goatcounter_get(
                site, token,
                f"/api/v0/stats/total?start={pad_start}&end={pad_end}")
            if err2:
                results["daily"] = []
            else:
                results["daily"] = [
                    {"day": s.get("day"), "count": s.get("daily") or 0}
                    for s in (totals.get("stats") or []) if in_range(s.get("day"))
                ]
            self._json(results)

        elif path == "/api/analytics/months":
            # 月別アクセス推移（既定：直近12ヶ月）。1回のAPI呼び出しで取れるが
            # 2秒ほどかかるので、短時間キャッシュして開き直しを軽くする。
            cfg = load_settings()
            site  = cfg.get("goatcounterSite", "").strip()
            token = cfg.get("goatcounterToken", "").strip()
            if not site or not token:
                self._json({"configured": False})
                return

            try:
                months_back = max(1, min(36, int(qs.get("months", ["12"])[0])))
            except ValueError:
                months_back = 12

            today = datetime.now(JST).date()
            first = today.replace(day=1)
            for _ in range(months_back - 1):          # months_back ヶ月前の1日まで戻る
                first = (first - timedelta(days=1)).replace(day=1)

            cache_key = (site, str(first), str(today))
            cached = MONTHS_CACHE.get(cache_key)
            if cached and time.time() - cached[0] < 600:   # 10分
                self._json(cached[1])
                return

            # 端の日が欠けないよう前後1日ぶん広く取り、表示範囲だけ集計する
            totals, err = goatcounter_get(
                site, token,
                f"/api/v0/stats/total?start={first - timedelta(days=1)}"
                f"&end={today + timedelta(days=1)}", timeout=40)
            if err:
                self._json({"configured": True, "error": err["error"], "months": []})
                return

            agg = {}
            for s in (totals.get("stats") or []):
                day = s.get("day") or ""
                if not (str(first) <= day <= str(today)):
                    continue
                ym = day[:7]
                if ym:
                    agg[ym] = agg.get(ym, 0) + (s.get("daily") or 0)
            payload = {
                "configured": True,
                "months": [{"month": m, "count": agg[m]} for m in sorted(agg)],
            }
            MONTHS_CACHE[cache_key] = (time.time(), payload)
            self._json(payload)

        elif path == "/api/pick-image":
            multiple = qs.get("multiple", ["false"])[0] == "true"
            default_dir = "/Users/dai/漫画作業"
            try:
                if multiple:
                    script = (
                        f'set fs to choose file of type {{"public.image"}} '
                        f'default location POSIX file "{default_dir}" with multiple selections allowed\n'
                        f'set out to ""\n'
                        f'repeat with f in fs\nset out to out & POSIX path of f & linefeed\nend repeat\n'
                        f'return out'
                    )
                else:
                    script = f'POSIX path of (choose file of type {{"public.image"}} default location POSIX file "{default_dir}")'
                result = subprocess.run(["osascript", "-e", script],
                                        capture_output=True, text=True, timeout=120)
                if result.returncode != 0:
                    self._json({"ok": False, "cancelled": True})
                    return
                paths = [p.strip() for p in result.stdout.strip().split("\n") if p.strip()]
                IMAGES_DIR.mkdir(parents=True, exist_ok=True)
                urls = []
                for src_path in paths:
                    src = Path(src_path)
                    if not src.exists():
                        continue
                    ext  = src.suffix.lower()
                    name = f"{datetime.now(JST).strftime('%Y%m%d%H%M%S')}_{len(urls)}{ext}"
                    shutil.copy2(src, IMAGES_DIR / name)
                    urls.append(f"/images/posts/{name}")
                if multiple:
                    self._json({"ok": True, "urls": urls})
                else:
                    self._json({"ok": True, "url": urls[0] if urls else None})
            except subprocess.TimeoutExpired:
                self._json({"ok": False, "error": "タイムアウト（2分）"})
            except Exception as e:
                self._json({"ok": False, "error": str(e)})

        elif path.startswith("/images/"):
            # static/images 以下を配信（logo含む）
            img_path = BLOG_ROOT / "static" / unquote(path).lstrip("/")
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
            if fileitem is not None and fileitem.filename:
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

        elif path == "/api/upload-logo":
            ct = self.headers.get("Content-Type", "")
            environ = {"REQUEST_METHOD": "POST", "CONTENT_TYPE": ct, "CONTENT_LENGTH": str(length)}
            raw = self.rfile.read(length)
            fs = cgi.FieldStorage(fp=io.BytesIO(raw), headers=self.headers, environ=environ)
            fileitem = fs["file"] if "file" in fs else None
            if fileitem is not None and fileitem.filename:
                # 既存ロゴを削除してから保存
                for old in find_logo() and [find_logo()] or []:
                    old.unlink(missing_ok=True)
                ext  = Path(fileitem.filename).suffix.lower() or ".png"
                dest = LOGO_DIR / f"logo{ext}"
                LOGO_DIR.mkdir(parents=True, exist_ok=True)
                dest.write_bytes(fileitem.file.read())
                git_push("ロゴ画像を更新")
                self._json({"ok": True, "url": f"/images/logo{ext}"})
            else:
                self._json({"error": "no file"}, 400)

        elif path == "/api/delete-logo":
            self.rfile.read(length)
            logo = find_logo()
            if logo:
                logo.unlink(missing_ok=True)
                git_push("ロゴ画像を削除")
            self._json({"ok": True})

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
