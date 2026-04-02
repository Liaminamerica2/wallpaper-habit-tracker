#!/usr/bin/env python3
"""
Habit Wallpaper Generator
Generates an iPhone lock screen wallpaper with a dot-grid habit tracker.

Usage:
  python habit_wallpaper.py --mock          # test with random data
  python habit_wallpaper.py                 # pull from Google Sheets + Tasks

Setup (for real data):
  1. pip install Pillow google-api-python-client google-auth-oauthlib
  2. Google Cloud project → enable Sheets + Tasks APIs → download credentials.json
  3. Google Sheet layout:
     Row 1: Date | Wrap Tfillin | Gym | Read 2 Pages | LeetCode | Tasks Done
     Row 2: 2026-03-18 | 1 | 0 | 1 | 1 | 3
  4. Set SPREADSHEET_ID below
"""

import os
import math
import random
import hashlib
import textwrap
from datetime import datetime, timedelta
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont

# =============================================================================
# CONFIG
# =============================================================================

SPREADSHEET_ID = "YOUR_SPREADSHEET_ID_HERE"
SHEET_NAME = "Habits"
RANGE = f"{SHEET_NAME}!A:F"

HABITS = [ # this should be pulled from the spreadsheet
    {"name": "WRAP TFILLIN", "type": "do"},
    {"name": "GYM",          "type": "do"},
    {"name": "READ 2 PAGES", "type": "do"},
    {"name": "LEETCODE",     "type": "do"},
    {"name": "COOKED FOOD AT WORK", "type": "do"},
    {"name": "POST A CLASH VIDEO", "type": "do"},
    {"name": "CALL ROBERT", "type": "do"},
    {"name": "CALL ABRAHAM", "type": "do"},
    {"name": "BH", "type": "do"}, # BH stands for bad habits
    {"name": "TASKS DONE",   "type": "count"},
]

LOOKBACK_DAYS = 14

# iPhone 15 Pro (native resolution)
WIDTH  = 1179
HEIGHT = 2556

OUTPUT_PATH = Path(__file__).parent / "habit_wallpaper.png"

# --- Colors ---
BG          = (0, 0, 0)
WHITE       = (224, 224, 224)
RED         = (226, 75, 74)
AMBER       = (245, 158, 11)
EMPTY       = (48, 48, 48)
FILLER      = (36, 36, 36)
LABEL_COLOR = (88, 88, 88)
DAY_COLOR   = (68, 68, 68)
TIME_COLOR  = (255, 255, 255)
DATE_COLOR  = (153, 153, 153)
GREEN       = (74, 222, 128)
GRAY_TEXT   = (100, 100, 100)
QUOTE_COLOR = (140, 140, 140)
QUOTE_ATTR  = (80, 80, 80)

# --- Grid layout ---
DOT_R       = 22        # dot radius
DOT_D       = DOT_R * 2 # dot diameter
DOT_GAP     = 22         # gap between dots
DOT_STEP    = DOT_D + DOT_GAP # step between dots

GRID_TOP    = 600       # top of grid
LABEL_GAP   = 60        # gap between labels
ROW_GAP     = 25        # gap between rows

# Filler dots
FILL_R      = 12
FILL_D      = FILL_R * 2
FILL_GAP    = 5
FILL_STEP   = FILL_D + FILL_GAP

# --- Image overlay ---
# OVERLAY_IMAGE   = "images/ippo_above_punchingbag.png"  # path to your image
OVERLAY_IMAGE_PATHS   = ["images/ippo_side_punchingbag.png",
#  "images/ippo_above_punchingbag.png",
 "images/ippo_front_punchingbag.png"]  # path to your image
# OVERLAY_IMAGE   = "images/ippo_front_punchingbag.png"  # path to your image
OVERLAY_SIZE    = (250, 250)    # (width, height) on the wallpaper
OVERLAY_POS1     = (int(WIDTH / 2 - 125), int(HEIGHT / 8) * 7)    # (x, y) top-left corner on the wallpaper
OVERLAY_POS2     = ((WIDTH / 2 - 282.5), (WIDTH / 2 + 37.5), int(HEIGHT / 8) * 7)    # (x1, x2, y) top-left corner on the wallpaper
OVERLAY_CROP    = None          # None = no crop, or (left, top, right, bottom) in source pixels
                                # e.g. (100, 50, 600, 550) crops a 500x500 region
OVERLAY_ROUND   = 30            # corner radius (0 = square, 125 = circle for 250x250)
OVERLAY_OPACITY = 255           # 0-255 (255 = fully opaque)


# =============================================================================
# QUOTES
# =============================================================================

QUOTES = [
    # Ken 
    ("passion is the genesis of genius!!", "ken"),

    # lil uzi vert
    ("I'm just doing me, and to me, that's what got me this far.", "lil uzi vert"),

    # --- Hajime no Ippo / Fighting Spirit ---
    ("The effort you put in every day will never betray you.", "Hajime no Ippo"),
    ("A strong will can overcome any obstacle.", "Hajime no Ippo"),
    ("If you keep your guard up you won't get hit. Simple as that.", "Kamogawa"),
    ("The one who wins is the one who's willing to take one more step.", "Kamogawa"),
    ("Effort is never wasted, even when it leads to disappointing results.", "Kamogawa"),
    ("You put in the effort but were afraid to accept the results. That is not how a man should live.", "Kamogawa"),
    ("Being strong isn't just about winning. Even if my body breaks, my will to fight must never be broken.", "Ippo"),
    ("Your fists are for protecting the things that matter.", "Kamogawa"),
    ("There is no shortcut in boxing. You can only do it the hard way.", "Takamura"),
    ("The moment you think about giving up, think about why you started.", "Ippo"),
    ("If you can't do it with effort, where does that leave those without talent?", "Takamura"),
    

    # --- Discipline / Stoic ---
    ("We suffer more in imagination than in reality.", "Seneca"),
    ("You could leave life right now. Let that determine what you do and say and think.", "Marcus Aurelius"),
    ("Waste no more time arguing about what a good man should be. Be one.", "Marcus Aurelius"),
    ("It is not that we have a short time to live, but that we waste a great deal of it.", "Seneca"),
    ("He who fears death will never do anything worthy of a man who is alive.", "Seneca"),
    ("The impediment to action advances action. What stands in the way becomes the way.", "Marcus Aurelius"),
    ("Discipline equals freedom.", "Jocko Willink"),
    ("Don't expect to be motivated every day. You have to be driven.", "David Goggins"),
    ("You are in danger of living a life so comfortable and soft that you will die without ever realizing your potential.", "David Goggins"),
    ("Comfort is a slow death.", "David Goggins"),

    # --- Jewish wisdom ---
    ("If I am not for myself, who will be for me? If I am only for myself, what am I? If not now, when?", "Hillel"),
    ("In a place where there are no men, strive to be a man.", "Pirkei Avot 2:5"),
    ("It is not your duty to finish the work, but neither are you free to neglect it.", "Pirkei Avot 2:16"),
    ("Who is mighty? One who conquers his own inclination.", "Pirkei Avot 4:1"),
    ("The day is short, the work is great, and the workers are lazy.", "Pirkei Avot 2:15"),

    # --- Other fighters / athletes ---
    ("I hated every minute of training, but I said don't quit. Suffer now and live the rest of your life as a champion.", "Muhammad Ali"),
    ("Everyone has a plan until they get punched in the mouth.", "Mike Tyson"),
    ("Hard work beats talent when talent doesn't work hard.", "Tim Notke"),
    ("The last three or four reps is what makes the muscle grow.", "Arnold Schwarzenegger"),
    ("I don't count my sit-ups. I only start counting when it starts hurting.", "Muhammad Ali"),
]


def get_daily_quote(now=None):
    """Pick a quote deterministically based on the date so it stays stable all day."""
    if now is None:
        now = datetime.now()
    day_key = now.strftime("%Y-%m-%d")
    h = int(hashlib.md5(day_key.encode()).hexdigest(), 16)
    return QUOTES[h % len(QUOTES)]


# =============================================================================
# FONTS
# =============================================================================

def _font(size, style="regular"):
    paths = {
        "thin": [
            "/usr/share/fonts/truetype/dejavu/DejaVuSans-ExtraLight.ttf",
            "/usr/share/fonts/opentype/noto/NotoSansCJK-Thin.ttc",
        ],
        "light": [
            "/usr/share/fonts/truetype/google-fonts/Poppins-Light.ttf",
            "/usr/share/fonts/truetype/dejavu/DejaVuSans-ExtraLight.ttf",
        ],
        "regular": [
            "/usr/share/fonts/truetype/google-fonts/Poppins-Regular.ttf",
            "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        ],
        "medium": [
            "/usr/share/fonts/truetype/google-fonts/Poppins-Medium.ttf",
            "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        ],
        "italic": [
            "/usr/share/fonts/truetype/google-fonts/Poppins-Italic.ttf",
            "/usr/share/fonts/truetype/dejavu/DejaVuSans-Oblique.ttf",
        ],
    }
    for p in paths.get(style, paths["regular"]):
        if os.path.exists(p):
            try:
                return ImageFont.truetype(p, size)
            except OSError:
                continue
    return ImageFont.load_default()


FONT_LABEL  = _font(32,  "medium")
FONT_DAY    = _font(30,  "regular")
FONT_COUNT  = _font(30,  "medium")
FONT_FOOTER = _font(36,  "regular")
FONT_QUOTE  = _font(36,  "italic")
FONT_ATTR   = _font(32,  "regular")


# =============================================================================
# DATA
# =============================================================================

def generate_mock_data():
    random.seed(42)
    data = {}
    today = datetime.now().date()
    for d in range(LOOKBACK_DAYS - 1, -1, -1):
        dt = today - timedelta(days=d)
        key = dt.strftime("%Y-%m-%d")
        if d == 0:
            data[key] = [-1] * len(HABITS)
        else:
            row = []
            for h in HABITS:
                if h["type"] == "count":
                    row.append(random.randint(0, 8))
                else:
                    row.append(1 if random.random() > 0.28 else 0)
            data[key] = row
    return data


def get_google_creds():
    from google.oauth2.credentials import Credentials
    from google_auth_oauthlib.flow import InstalledAppFlow
    from google.auth.transport.requests import Request
    SCOPES = [
        "https://www.googleapis.com/auth/spreadsheets.readonly",
        "https://www.googleapis.com/auth/tasks.readonly",
    ]
    token_path = Path(__file__).parent / "token.json"
    creds_path = Path(__file__).parent / "credentials.json"
    creds = None
    if token_path.exists():
        creds = Credentials.from_authorized_user_file(str(token_path), SCOPES)
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            flow = InstalledAppFlow.from_client_secrets_file(str(creds_path), SCOPES)
            creds = flow.run_local_server(port=0)
        token_path.write_text(creds.to_json())
    return creds


def fetch_sheet_data(creds):
    from googleapiclient.discovery import build
    svc = build("sheets", "v4", credentials=creds)
    result = svc.spreadsheets().values().get(
        spreadsheetId=SPREADSHEET_ID, range=RANGE
    ).execute()
    rows = result.get("values", [])
    data = {}
    for row in rows[1:]:
        if len(row) < 2:
            continue
        vals = []
        for i in range(1, len(HABITS) + 1):
            try:
                vals.append(int(row[i]) if i < len(row) else 0)
            except (ValueError, TypeError):
                vals.append(0)
        data[row[0]] = vals
    return data


def fetch_google_tasks_today(creds):
    from googleapiclient.discovery import build
    svc = build("tasks", "v1", credentials=creds)
    lists = svc.tasklists().list().execute().get("items", [])
    today_rfc = datetime.now().replace(
        hour=0, minute=0, second=0, microsecond=0
    ).strftime("%Y-%m-%dT%H:%M:%S.000Z")
    count = 0
    for tl in lists:
        tasks = svc.tasks().list(
            tasklist=tl["id"], showCompleted=True,
            showHidden=True, completedMin=today_rfc,
        ).execute().get("items", [])
        count += sum(1 for t in tasks if t.get("status") == "completed")
    return count


# =============================================================================
# IMAGE OVERLAY
# =============================================================================

def _cover_crop(img, target_w, target_h):
    """Resize + center-crop to exactly fill target_w x target_h (no distortion)."""
    src_w, src_h = img.size
    scale = max(target_w / src_w, target_h / src_h)
    new_w = int(src_w * scale)
    new_h = int(src_h * scale)
    img = img.resize((new_w, new_h), Image.LANCZOS)
    
    # Center crop to target
    left = (new_w - target_w) // 2
    top = (new_h - target_h) // 2
    return img.crop((left, top, left + target_w, top + target_h))

def load_overlay():
    images = []
    for p in OVERLAY_IMAGE_PATHS:
        path = Path(p)
        if not path.exists():
            continue

        img = Image.open(path).convert("RGBA")

        # Crop source region first if specified
        if OVERLAY_CROP:
            img = OVERLAY_CROP

        # Cover-crop to target size (no distortion, no letterboxing)
        img = _cover_crop(img, OVERLAY_SIZE[0], OVERLAY_SIZE[1])

        # Round corners
        if OVERLAY_ROUND > 0:
            w, h = img.size
            mask = Image.new("L", (w, h), 0)
            ImageDraw.Draw(mask).rounded_rectangle([0, 0, w, h], radius=OVERLAY_ROUND, fill=255)
            img.putalpha(mask)

        # Opacity
        if OVERLAY_OPACITY < 255:
            alpha = img.split()[3]
            alpha = alpha.point(lambda a: int(a * OVERLAY_OPACITY / 255))
            img.putalpha(alpha)

        images.append(img)

    return images

# =============================================================================
# RENDERING
# =============================================================================

def dot_color(value, habit_type, is_today):
    if is_today and value == -1:
        return EMPTY
    if habit_type == "count":
        if value <= 0:
            return EMPTY
        t = min(value / 7.0, 1.0)
        b = int(70 + t * 154)
        return (b, b, b)
    return WHITE if value >= 1 else EMPTY


def _center_x(draw, text, font):
    bb = draw.textbbox((0, 0), text, font=font)
    return (WIDTH - (bb[2] - bb[0])) / 2


def _wrap_text(draw, text, font, max_width):
    """Word-wrap text to fit within max_width pixels. Returns list of lines."""
    words = text.split()
    lines = []
    current = ""
    for word in words:
        test = f"{current} {word}".strip()
        bb = draw.textbbox((0, 0), test, font=font)
        if bb[2] - bb[0] > max_width and current:
            lines.append(current)
            current = word
        else:
            current = test
    if current:
        lines.append(current)
    return lines


def render(habit_data, now=None):
    if now is None:
        now = datetime.now()

    # --- Background ---
    img = Image.new("RGB", (WIDTH, HEIGHT), BG)
    draw = ImageDraw.Draw(img)

    # --- Image overlay ---
    overlay = load_overlay()
    if all(overlay):
        if len(overlay) < 2:
            img.paste(overlay, (int(OVERLAY_POS1[0]), int(OVERLAY_POS1[1])), overlay)
            draw = ImageDraw.Draw(img)  # refresh draw after paste
        elif len(overlay) == 2:
            print("here")
            img.paste(overlay[0], (int(OVERLAY_POS2[0]), int(OVERLAY_POS2[2])), overlay[0])
            img.paste(overlay[1], (int(OVERLAY_POS2[1]), int(OVERLAY_POS2[2])), overlay[1])
            draw = ImageDraw.Draw(img)  # refresh draw after paste
        elif len(overlay) == 3:
            img.paste(overlay[0], (int(OVERLAY_POS1[0]), int(OVERLAY_POS1[1])), overlay[0])
            img.paste(overlay[1], (int(OVERLAY_POS1[0]), int(OVERLAY_POS1[1])), overlay[1])
            img.paste(overlay[2], (int(OVERLAY_POS1[0]), int(OVERLAY_POS1[1])), overlay[2])
            draw = ImageDraw.Draw(img)  # refresh draw after paste
    print(overlay)
    # --- Dates ---
    today = now.date()
    dates = [today - timedelta(days=LOOKBACK_DAYS - 1 - i) for i in range(LOOKBACK_DAYS)]
    total_w = LOOKBACK_DAYS * DOT_D + (LOOKBACK_DAYS - 1) * DOT_GAP
    x0 = (WIDTH - total_w) / 2 

    # --- Habits ---
    y = GRID_TOP
    for hi, habit in enumerate(HABITS):
        draw.text((x0, y), habit["name"], fill=LABEL_COLOR, font=FONT_LABEL)
        y += LABEL_GAP

        for di, date in enumerate(dates):
            key = date.strftime("%Y-%m-%d")
            cx = x0 + di * DOT_STEP
            is_today = date == today
            val = habit_data.get(key, [None] * len(HABITS))[hi]
            color = EMPTY if val is None else dot_color(val, habit["type"], is_today)

            draw.ellipse([cx, y, cx + DOT_D, y + DOT_D], fill=color)

            if (habit["type"] == "count" and val is not None
                    and val > 0 and not (is_today and val == -1)):
                t = str(val)
                bb = draw.textbbox((0, 0), t, font=FONT_COUNT)
                tw, th = bb[2] - bb[0], bb[3] - bb[1] + 4

                draw.text(
                    (cx + (DOT_D - tw) / 2, y + (DOT_D - th) / 2 - 2),
                    t, fill=(0, 0, 0), font=FONT_COUNT,
                )

        y += DOT_D + ROW_GAP

    # --- Day numbers ---
    y += 8
    for di, date in enumerate(dates):
        t = str(date.day)
        bb = draw.textbbox((0, 0), t, font=FONT_DAY)
        tw = bb[2] - bb[0]
        cx = x0 + di * DOT_STEP
        draw.text((cx + (DOT_D - tw) / 2, y), t, fill=DAY_COLOR, font=FONT_DAY)

    # --- Quote ---
    quote_text, quote_source = get_daily_quote(now)
    quote_margin = 100
    max_quote_w = WIDTH - quote_margin * 2

    y += 80  # space below day numbers

    # Wrap and center the quote text
    lines = _wrap_text(draw, quote_text, FONT_QUOTE, max_quote_w)
    line_h = 48  # line height

    last_right = 0
    for line in lines:
        bb = draw.textbbox((0, 0), line, font=FONT_QUOTE)
        lw = bb[2] - bb[0]
        lx = (WIDTH - lw) / 2
        draw.text((lx, y), line, fill=QUOTE_COLOR, font=FONT_QUOTE)
        last_right = lx + lw
        y += line_h

    # Attribution — right edge aligned to last quote line's right edge
    y += 12
    attr = f"— {quote_source}"
    
    bb_a = draw.textbbox((0, 0), attr, font=FONT_ATTR)
    ax = last_right - (bb_a[2] / 2) + lx / 2
    print(ax)
    draw.text((ax, y), attr, fill=QUOTE_ATTR, font=FONT_ATTR)



    # --- Footer ---
    yday = now.timetuple().tm_yday
    ydays = 366 if now.year % 4 == 0 else 365
    left = ydays - yday
    pct = round(yday / ydays * 100)

    lt = f"{left}d left"
    rt = f" · {pct}%"
    bb_l = draw.textbbox((0, 0), lt, font=FONT_FOOTER)
    bb_r = draw.textbbox((0, 0), rt, font=FONT_FOOTER)
    tw = (bb_l[2] - bb_l[0]) + (bb_r[2] - bb_r[0])
    fx = (WIDTH - tw) / 2
    fy = HEIGHT - 300
    # draw.text((fx, fy), lt, fill=RED, font=FONT_FOOTER)
    # draw.text((fx + bb_l[2] - bb_l[0], fy), rt, fill=GRAY_TEXT, font=FONT_FOOTER)

    return img


# =============================================================================
# MAIN
# =============================================================================

def main():
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--mock", action="store_true")
    parser.add_argument("--output", default=str(OUTPUT_PATH))
    args = parser.parse_args()

    now = datetime.now()

    if args.mock:
        print("Mock data...")
        data = generate_mock_data()
    else:
        print("Google auth...")
        creds = get_google_creds()
        print("Fetching sheets...")
        data = fetch_sheet_data(creds)
        print("Fetching tasks...")
        tasks = fetch_google_tasks_today(creds)
        key = now.strftime("%Y-%m-%d")
        if key in data:
            data[key][-1] = tasks
        else:
            data[key] = [-1] * (len(HABITS) - 1) + [tasks]

    print("Rendering...")
    img = render(data, now)
    out = Path(args.output)
    out.parent.mkdir(parents=True, exist_ok=True)
    img.save(str(out), "PNG")
    print(f"Saved: {out}  ({img.size[0]}x{img.size[1]})")


if __name__ == "__main__":
    main()