"""Shared config + AList client. Stdlib only."""
import json
import re
import time
import urllib.request
import urllib.error

ORIGIN = "https://www.asmrgay.com"          # AList API + browse host
FILE_ORIGIN = "https://asmr.121231234.xyz"  # nginx file origin (sign not enforced)
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
      "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36")
DB_PATH = "graph.sqlite"
MOUNTS = ["asmr", "asmr2", "asmr3", "asmr4", "asmr5", "asmr6"]

AUDIO_EXT = {".mp3", ".wav", ".m4a", ".flac"}
HLS_EXT = {".m3u8"}
SUB_EXT = {".srt", ".vtt", ".ass", ".lrc"}


def _post(path, payload, retries=3, timeout=15):
    body = json.dumps(payload).encode("utf-8")
    last = None
    for i in range(retries):
        req = urllib.request.Request(
            ORIGIN + path, body,
            {"Content-Type": "application/json", "User-Agent": UA})
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return json.load(r)
        except urllib.error.HTTPError as e:
            last = e
            if e.code in (403, 429, 500, 502, 503):
                if i < retries - 1:
                    time.sleep(0.4 * (i + 1))    # 0.4s, 0.8s — fast
                continue
            raise
        except Exception as e:                       # noqa: BLE001
            last = e
            if i < retries - 1:
                time.sleep(0.4 * (i + 1))
    raise RuntimeError(f"POST {path} failed: {last}")


def fs_list(path, page=1, per_page=200, retries=3, timeout=15):
    d = _post("/api/fs/list",
              {"path": path, "page": page, "per_page": per_page},
              retries=retries, timeout=timeout)["data"]
    return d or {"content": [], "total": 0}


def fs_get(path):
    return _post("/api/fs/get", {"path": path})["data"]


def fs_search(keywords, parent="/", page=1, per_page=50):
    return _post("/api/fs/search",
                 {"parent": parent, "keywords": keywords,
                  "page": page, "per_page": per_page})["data"]


def raw_url(path):
    """Direct file-origin URL. Skips flaky fs_get (sign not enforced)."""
    return FILE_ORIGIN + urllib.request.quote(path)


def ext_of(name):
    m = re.search(r"(\.[A-Za-z0-9]+)$", name)
    return m.group(1).lower() if m else ""


def kind_of(name):
    e = ext_of(name)
    if e in AUDIO_EXT:
        return "audio"
    if e in HLS_EXT:
        return "hls"
    if e in SUB_EXT:
        return "sub"
    return "other"


def norm_key(name):
    """Normalized basename for matching sub <-> media."""
    n = name.lower()
    n = re.sub(r"\.(mp3|wav|m4a|flac|srt|vtt|ass|lrc)$", "", n)
    n = re.sub(r"\.mp3$", "", n)           # double-ext leftovers
    n = re.sub(r"[#\[\]()]", " ", n)
    n = re.sub(r"^\s*\d+[\s._-]*", "", n)  # leading track number
    n = re.sub(r"\s+", " ", n).strip()
    return n
