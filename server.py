#!/usr/bin/env python3
"""
Habit Wallpaper Server
Returns a freshly generated wallpaper PNG on any GET request.
Hot-reloads gen_wallpaper.py on every request — edit and refresh, no restart needed.
Logs timing for every step so you can see what's slow.

Usage:
  python server.py                    # starts on port 8080
  python server.py --port 3000        # custom port
  python server.py --mock             # use mock data instead of Google APIs
"""

import io
import importlib
import argparse
import time
import traceback
from http.server import HTTPServer, BaseHTTPRequestHandler
from datetime import datetime

import gen_wallpaper

MOCK = False


def _log(msg):
    print(f"[{datetime.now().strftime('%H:%M:%S.%f')[:-3]}] {msg}")


def _timed(label, fn):
    """Run fn(), log how long it took, return the result."""
    start = time.monotonic()
    try:
        result = fn()
        elapsed = (time.monotonic() - start) * 1000
        _log(f"  {label}: {elapsed:.0f}ms ✓")
        return result
    except Exception as e:
        elapsed = (time.monotonic() - start) * 1000
        _log(f"  {label}: {elapsed:.0f}ms ✗ {e}")
        raise


def build_image():
    total_start = time.monotonic()
    _log("--- Request started ---")

    _timed("reload module", lambda: importlib.reload(gen_wallpaper))

    now = datetime.now()

    if MOCK:
        data = _timed("generate mock data", gen_wallpaper.generate_mock_data)
    else:
        creds = _timed("google auth", gen_wallpaper.get_google_creds)
        data = _timed("fetch sheets", lambda: gen_wallpaper.fetch_sheet_data(creds))
        tasks = _timed("fetch tasks", lambda: gen_wallpaper.fetch_google_tasks_today(creds))
        key = now.strftime("%Y-%m-%d")
        if key in data:
            data[key][-1] = tasks
        else:
            data[key] = [-1] * (len(gen_wallpaper.HABITS) - 1) + [tasks]

    img = _timed("render image", lambda: gen_wallpaper.render(data, now))

    buf = io.BytesIO()
    _timed("encode PNG", lambda: img.save(buf, "PNG"))
    png = buf.getvalue()

    total = (time.monotonic() - total_start) * 1000
    _log(f"--- Done: {total:.0f}ms total, {len(png)} bytes ---")
    return png


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        try:
            png = build_image()

            self.send_response(200)
            self.send_header("Content-Type", "image/png")
            self.send_header("Content-Length", str(len(png)))
            self.send_header("Cache-Control", "no-cache")
            self.end_headers()
            self.wfile.write(png)
        except Exception as e:
            _log(f"ERROR: {e}")
            _log(traceback.format_exc())
            msg = f"Error: {e}\n\n{traceback.format_exc()}".encode()
            self.send_response(500)
            self.send_header("Content-Type", "text/plain")
            self.send_header("Content-Length", str(len(msg)))
            self.end_headers()
            self.wfile.write(msg)

    def log_message(self, fmt, *args):
        _log(args[0])


def main():
    global MOCK
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=8080)
    parser.add_argument("--mock", action="store_true")
    args = parser.parse_args()
    MOCK = args.mock

    server = HTTPServer(("0.0.0.0", args.port), Handler)
    _log(f"Serving on http://0.0.0.0:{args.port}")
    _log(f"Mode: {'mock' if MOCK else 'google sheets'}")
    server.serve_forever()


# TODO: create an endpoint that accepts survey...
# TODO: make a shortcut on iphone that only has one menu pick which habit to add...

if __name__ == "__main__":
    main()