#!/usr/bin/env python3
"""
Habit Wallpaper Server

Endpoints:
  GET  /              → returns wallpaper PNG
  GET  /mark?h=GYM    → toggles habit for today (0↔1), returns JSON
  GET  /habits        → lists available habit names, returns JSON

When HABIT_SERVER_API_KEY is set (e.g. in .env — see .env.example), /mark requires
the same value as query param ?key=... or header X-Api-Key.

Usage:
  python server.py                    # starts on port 8080
  python server.py --port 3000        # custom port
  python server.py --mock             # use mock data (read only, /mark disabled)

Public HTTPS without opening your router (Cloudflare Tunnel):
  See cloudflared/config.example.yml — copy to ~/.cloudflared/config.yml, run
  `cloudflared tunnel login`, `cloudflared tunnel create …`, route DNS, then
  `cloudflared tunnel run …` while this server is up.

Apple Shortcut for marking habits:
  1. Action: "Get Contents of URL"
  2. URL: https://habits.example.com/mark?h=GYM&key=YOUR_KEY  (or http://LAN_IP:8080/…)
  3. Done — one tap per habit
"""

import argparse
import importlib
import io
import json
import logging
import os
import secrets
import time
import traceback
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs

from dotenv import load_dotenv

import gen_wallpaper

load_dotenv()
MOCK = False
API_KEY = os.environ.get("HABIT_SERVER_API_KEY", "").strip()


def _configure_logging():
    """Root logger at DEBUG. Use force=True so a pre-existing root handler can't block our config."""
    kwargs = dict(
        level=logging.DEBUG,
        format="%(asctime)s.%(msecs)03d [%(name)s] %(levelname)s %(message)s",
        datefmt="%H:%M:%S",
    )
    try:
        logging.basicConfig(**kwargs, force=True)
    except TypeError:
        # Python < 3.8: no force=
        logging.basicConfig(**kwargs)


def _log(msg):
    # Timestamps in US Pacific to match habit "today" and wallpaper date.
    t = gen_wallpaper.habit_now().strftime("%H:%M:%S.%f")[:-3]
    print(f"[{t}] {msg}")


def _timed(label, fn):
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
    _log("--- Wallpaper request ---")

    _timed("reload module", lambda: importlib.reload(gen_wallpaper))

    now = gen_wallpaper.habit_now()

    if MOCK:
        data = _timed("generate mock data", gen_wallpaper.generate_mock_data)
    else:
        import google_data
        importlib.reload(google_data)
        data = _timed("fetch all (auth+sheets+tasks)", lambda: google_data.fetch_all(gen_wallpaper.HABITS))

    img = _timed("render image", lambda: gen_wallpaper.render(data, now))

    buf = io.BytesIO()
    _timed("encode PNG", lambda: img.save(buf, "PNG"))
    png = buf.getvalue()

    total = (time.monotonic() - total_start) * 1000
    _log(f"--- Done: {total:.0f}ms total, {len(png)} bytes ---")
    return png


def mark_habit(habit_name):
    _log(f"--- Mark habit: {habit_name} ---")
    import google_data
    importlib.reload(google_data)
    importlib.reload(gen_wallpaper)

    creds = _timed("google auth", google_data.get_google_creds)
    result = _timed("mark habit", lambda: google_data.mark_habit(creds, gen_wallpaper.HABITS, habit_name))
    _log(f"--- Marked: {result} ---")
    return result


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path.rstrip("/") or "/"
        params = parse_qs(parsed.query)

        try:
            if path == "/mark":
                self._handle_mark(params)
            elif path == "/habits":
                self._handle_habits()
            else:
                self._handle_wallpaper()
        except Exception as e:
            _log(f"ERROR: {e}")
            _log(traceback.format_exc())
            self._send_json(500, {"error": str(e)})

    def _handle_wallpaper(self):
        png = build_image()
        self.send_response(200)
        self.send_header("Content-Type", "image/png")
        self.send_header("Content-Length", str(len(png)))
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        self.wfile.write(png)

    def _handle_mark(self, params):
        if MOCK:
            self._send_json(400, {"error": "Cannot mark habits in mock mode"})
            return

        habit_name = params.get("h", [None])[0]
        key_in_query = bool(params.get("key"))
        key_header = bool(self.headers.get("X-Api-Key"))

        if API_KEY:
            supplied = (params.get("key", [None])[0] or "") or (
                self.headers.get("X-Api-Key") or ""
            )
            if not secrets.compare_digest(supplied, API_KEY):
                _log(
                    "/mark 401: habit=%r key_from_query=%s key_from_header=%s key_len_matches=%s"
                    % (
                        habit_name,
                        key_in_query,
                        key_header,
                        len(supplied) == len(API_KEY) if supplied or API_KEY else True,
                    )
                )
                self._send_json(401, {"error": "Invalid or missing API key"})
                return

        if not habit_name:
            importlib.reload(gen_wallpaper)
            available = [h["name"] for h in gen_wallpaper.HABITS]
            _log(f"/mark 400: missing ?h=; query keys={list(params.keys())}")
            self._send_json(400, {"error": "Missing ?h= parameter", "available": available})
            return

        client = self.headers.get("X-Forwarded-For") or self.client_address[0]
        _log(f"/mark: habit={habit_name!r} client={client}")
        result = mark_habit(habit_name)
        self._send_json(200, result)

    def _handle_habits(self):
        importlib.reload(gen_wallpaper)
        habits = [{"name": h["name"], "type": h["type"]} for h in gen_wallpaper.HABITS]
        self._send_json(200, {"habits": habits})

    def _send_json(self, code, data):
        body = json.dumps(data, indent=2).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, fmt, *args):
        _log(args[0])


def main():
    global MOCK
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=8080)
    parser.add_argument("--mock", action="store_true")
    args = parser.parse_args()
    MOCK = args.mock

    _configure_logging()

    server = HTTPServer(("0.0.0.0", args.port), Handler)
    _log(f"Serving on http://0.0.0.0:{args.port}")
    _log(f"Mode: {'mock' if MOCK else 'google sheets'}")
    if API_KEY:
        _log("API key: required for GET /mark (?key= or X-Api-Key)")
    else:
        _log("API key: not set (HABIT_SERVER_API_KEY empty — /mark is open)")
    _log(f"Endpoints:")
    _log(f"  GET /          → wallpaper PNG")
    _log(f"  GET /mark?h=X  → toggle habit X for today (0↔1)")
    _log(f"  GET /habits    → list available habits")
    server.serve_forever()


if __name__ == "__main__":
    main()