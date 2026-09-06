"""Static server for web/ WITH HTTP Range support (seek + correct duration).

Python's stock http.server ignores Range → <audio> can't seek and misreads
duration. This handler answers 206 Partial Content so playback/seek work.

    python serve.py            # http://127.0.0.1:8901
    python serve.py --port 8901 --dir web
"""
import argparse
import os
import re
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer


class RangeHandler(SimpleHTTPRequestHandler):
    def send_head(self):
        rng = self.headers.get("Range")
        if not rng:
            return super().send_head()
        path = self.translate_path(self.path)
        if not os.path.isfile(path):
            return super().send_head()          # let base handle 404/dirs
        m = re.match(r"bytes=(\d*)-(\d*)", rng)
        size = os.path.getsize(path)
        if not m:
            self.send_error(400, "bad Range")
            return None
        start = int(m.group(1)) if m.group(1) else 0
        end = int(m.group(2)) if m.group(2) else size - 1
        end = min(end, size - 1)
        if start > end:
            self.send_error(416)
            self.send_header("Content-Range", f"bytes */{size}")
            return None
        f = open(path, "rb")
        f.seek(start)
        self.send_response(206)
        self.send_header("Content-Type", self.guess_type(path))
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
        self.send_header("Content-Length", str(end - start + 1))
        self._range = (start, end)
        self.end_headers()
        return f

    def copyfile(self, source, outputfile):
        rng = getattr(self, "_range", None)
        if not rng:
            return super().copyfile(source, outputfile)
        start, end = rng
        remaining = end - start + 1
        while remaining > 0:
            chunk = source.read(min(1 << 16, remaining))
            if not chunk:
                break
            outputfile.write(chunk)
            remaining -= len(chunk)
        self._range = None

    def end_headers(self):
        if getattr(self, "_range", None) is None:
            self.send_header("Accept-Ranges", "bytes")   # advertise on plain GET
        super().end_headers()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8901)
    ap.add_argument("--dir", default="web")
    a = ap.parse_args()
    os.chdir(os.path.join(os.path.dirname(os.path.abspath(__file__)), a.dir))
    httpd = ThreadingHTTPServer(("127.0.0.1", a.port), RangeHandler)
    print(f"serving {a.dir} on http://127.0.0.1:{a.port} (Range enabled)")
    httpd.serve_forever()


if __name__ == "__main__":
    main()
