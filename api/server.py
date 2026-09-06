"""Phase 3 API + static web, stdlib http.server. Reads graph.sqlite.

Endpoints (JSON):
  GET /api/search?q=&mode=name|subtitle|both&page=
  GET /api/browse?path=/asmr
  GET /api/resolve?path=/asmr/.../track.mp3   -> live raw_url
Static: everything else served from ../web (index.html default).

Run:  python api/server.py   ->  http://localhost:8000
"""
import json
import os
import sqlite3
import sys
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import urllib.request  # noqa: E402
from common import (DB_PATH, UA, raw_url, fs_list, ext_of,  # noqa: E402
                    kind_of, norm_key)

WEB = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "web")


def q(sql, args=()):
    c = sqlite3.connect(DB_PATH)
    c.row_factory = sqlite3.Row
    try:
        return [dict(r) for r in c.execute(sql, args).fetchall()]
    finally:
        c.close()


def snippet(text, term, pad=12):
    i = text.lower().find(term.lower())
    if i < 0:
        return text[:60]
    a, b = max(0, i - pad), i + len(term) + pad
    s = ("…" if a else "") + text[a:i] + "[" + text[i:i + len(term)] + "]" \
        + text[i + len(term):b] + ("…" if b < len(text) else "")
    return s


def live_children(path):
    """Fetch dir from live AList, cache into nodes, return browse rows."""
    try:                                         # interactive: one fast try
        d = fs_list(path, retries=1, timeout=8)
    except Exception:                            # noqa: BLE001  flaky backend
        return []
    c = sqlite3.connect(DB_PATH)
    rows = []
    for it in (d.get("content") or []):
        child = path.rstrip("/") + "/" + it["name"]
        is_dir = 1 if it["is_dir"] else 0
        parts = child.strip("/").split("/")
        mount = parts[0]
        work = parts[2] if len(parts) > 2 else (parts[1] if len(parts) > 1 else "")
        kind = "dir" if is_dir else kind_of(it["name"])
        c.execute("INSERT INTO nodes"
                  "(path,name,parent,is_dir,size,kind,ext,modified,mount,"
                  "work,nkey) VALUES(?,?,?,?,?,?,?,?,?,?,?) "
                  "ON CONFLICT(path) DO UPDATE SET name=excluded.name,"
                  "parent=excluded.parent,is_dir=excluded.is_dir,"
                  "size=excluded.size,kind=excluded.kind,ext=excluded.ext,"
                  "modified=excluded.modified,mount=excluded.mount,"
                  "work=excluded.work,nkey=excluded.nkey",
                  (child, it["name"], path, is_dir, it.get("size", 0), kind,
                   ext_of(it["name"]), it.get("modified", ""), mount, work,
                   norm_key(it["name"])))
        if not is_dir:
            c.execute("INSERT INTO files_fts(name,path) VALUES(?,?)",
                      (it["name"], child))
        rows.append({"path": child, "name": it["name"], "is_dir": is_dir,
                     "kind": kind, "size": it.get("size", 0)})
    c.commit()
    c.close()
    rows.sort(key=lambda r: (-r["is_dir"], r["name"]))
    return rows


def search(qs, mode, page):
    """Substring (LIKE) search — works for any length incl. CJK 1-2 chars."""
    per, off = 30, (page - 1) * 30
    like = "%" + qs.replace("%", r"\%").replace("_", r"\_") + "%"
    out = []
    if mode in ("name", "both"):
        for r in q("SELECT path,name,kind,mount,work FROM nodes "
                   "WHERE name LIKE ? ESCAPE '\\' ORDER BY is_dir, name "
                   "LIMIT ? OFFSET ?", (like, per, off)):
            r["hit"] = "name"
            out.append(r)
    if mode in ("subtitle", "both"):
        for r in q("SELECT media_path AS path, sub_path, text FROM subs "
                   "WHERE text LIKE ? ESCAPE '\\' LIMIT ? OFFSET ?",
                   (like, per, off)):
            out.append({"hit": "subtitle", "path": r["path"],
                        "sub_path": r["sub_path"],
                        "name": (r["path"] or r["sub_path"]).rsplit("/", 1)[-1],
                        "snippet": snippet(r["text"], qs)})
    return out


class H(BaseHTTPRequestHandler):
    def _json(self, obj, code=200):
        b = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(b)))
        self.end_headers()
        self.wfile.write(b)

    def _file(self, path):
        fp = os.path.join(WEB, path.lstrip("/") or "index.html")
        if not os.path.abspath(fp).startswith(WEB) or not os.path.isfile(fp):
            self.send_error(404)
            return
        ctype = "text/html; charset=utf-8" if fp.endswith(".html") else "application/octet-stream"
        with open(fp, "rb") as f:
            b = f.read()
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(b)))
        self.end_headers()
        self.wfile.write(b)

    def _stream(self, path):
        """Proxy origin bytes with our UA (Cloudflare blocks direct browser
        hits). Forwards Range for seeking."""
        raw = raw_url(path)
        hdrs = {"User-Agent": UA}
        rng = self.headers.get("Range")
        if rng:
            hdrs["Range"] = rng
        req = urllib.request.Request(raw, headers=hdrs)
        try:
            up = urllib.request.urlopen(req, timeout=30)
        except Exception as e:                               # noqa: BLE001
            self.send_error(502, str(e))
            return
        self.send_response(up.status)
        for h in ("Content-Type", "Content-Length", "Content-Range",
                  "Accept-Ranges"):
            v = up.headers.get(h)
            if v:
                self.send_header(h, v)
        self.end_headers()
        while True:
            chunk = up.read(65536)
            if not chunk:
                break
            try:
                self.wfile.write(chunk)
            except (BrokenPipeError, ConnectionResetError):
                break

    def do_GET(self):
        u = urllib.parse.urlparse(self.path)
        p = u.path
        args = {k: v[0] for k, v in urllib.parse.parse_qs(u.query).items()}
        try:
            if p == "/api/search":
                self._json(search(args.get("q", ""),
                                  args.get("mode", "both"),
                                  int(args.get("page", 1))))
            elif p == "/api/browse":
                path = args.get("path", "/")
                if path == "/":
                    self._json(q("SELECT DISTINCT mount AS name, "
                                 "'/'||mount AS path, 1 AS is_dir, "
                                 "'dir' AS kind, 0 AS size FROM nodes "
                                 "WHERE mount<>'' ORDER BY mount"))
                else:
                    rows = q("SELECT path,name,is_dir,kind,size FROM nodes "
                             "WHERE parent=? ORDER BY is_dir DESC, name", (path,))
                    if not rows:                 # not crawled yet: live + cache
                        rows = live_children(path)
                    self._json(rows)
            elif p == "/api/stats":
                s = q("SELECT "
                      "(SELECT COUNT(*) FROM nodes) nodes,"
                      "(SELECT COUNT(*) FROM nodes WHERE is_dir=1) dirs,"
                      "(SELECT COUNT(*) FROM nodes WHERE is_dir=0) files,"
                      "(SELECT COUNT(*) FROM nodes WHERE kind='audio') audio,"
                      "(SELECT COUNT(*) FROM subs) subs")[0]
                s["mounts"] = [r["mount"] for r in
                               q("SELECT DISTINCT mount FROM nodes "
                                 "WHERE mount<>'' ORDER BY mount")]
                self._json(s)
            elif p == "/api/resolve":
                self._json({"raw_url": raw_url(args["path"])})
            elif p == "/api/stream":
                self._stream(args["path"])
            else:
                self._file(p)
        except Exception as e:                                # noqa: BLE001
            self._json({"error": str(e)}, 500)

    def log_message(self, *a):
        pass


if __name__ == "__main__":
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8000
    print(f"http://localhost:{port}")
    ThreadingHTTPServer(("127.0.0.1", port), H).serve_forever()
