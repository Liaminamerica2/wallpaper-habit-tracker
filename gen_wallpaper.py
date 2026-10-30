#!/usr/bin/env python3
"""
Glass Photo Album Wallpaper
Bento or bubbles of photos on iPhone-style glass, over a rotating color gradient, plus the daily quote.

Usage:
  python gen_wallpaper.py                          # today's wallpaper -> habit_wallpaper.png
  python gen_wallpaper.py --date 2026-11-26        # preview any date
  python gen_wallpaper.py --layout bubbles         # force a layout: bento | bubbles
  python gen_wallpaper.py --background ocean       # force a background (see BACKGROUNDS)
  python gen_wallpaper.py --photos-dir samples/    # preview with every slot filled from one folder

From Python: render() / render(now) / render(habit_data, now) -> PIL image (old call style still works).

Each day (stable all day):
  - layout: random, LAYOUT_ODDS (70% bento / 30% bubbles)
  - background: next one from BACKGROUND_ROTATION (every one shows before any repeats)
  - Unsplash photos: searched to match that background's colors
  - on weekends and DAYS_OFF: a "No work today · <reason>" reminder under the clock

Where each photo comes from (fixed per slot, see SLOTS):
  folder     ./images (or WALL_PHOTO_DIRS=dir1:dir2)
  immich     IMMICH_URL, IMMICH_API_KEY, IMMICH_ALBUM_ID (one id, or several comma-separated)
  unsplash   UNSPLASH_ACCESS_KEY   (the search comes from the background, not an env var)
  Remote picks are cached per day. A source that fails is filled from the folders, then from
  whatever else loaded, then left as an empty glass tile.
"""

import io
import json
import os
import random
import urllib.parse
import urllib.request
from collections import Counter
from datetime import datetime, timedelta
from functools import lru_cache
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np
from PIL import Image, ImageDraw, ImageEnhance, ImageFilter, ImageFont, ImageOps, ImageStat

try:  # iPhone originals are HEIC; `pip install pillow-heif` lets Pillow open them
    from pillow_heif import register_heif_opener
    register_heif_opener()
except ImportError:
    pass

HERE = Path(__file__).parent

# =============================================================================
# CONFIG
# =============================================================================

WALL_TZ = ZoneInfo("America/Los_Angeles")
WIDTH, HEIGHT = 1179, 2556  # iPhone 15 Pro native
OUTPUT_PATH = HERE / "habit_wallpaper.png"  # same file your phone script already grabs

LAYOUT_ODDS = {"bento": 0.7, "bubbles": 0.3}

# Mesh gradients: 5 colors placed top-left, top-right, middle, bottom-left, bottom-right.
# Each one also tells Unsplash what to search for so the stock photos match the colors.
# Unsplash colors: black_and_white black white yellow orange red purple magenta green teal blue
BACKGROUNDS = {
    "dusk":     dict(colors=[(38, 28, 88), (120, 62, 150), (226, 118, 96), (96, 42, 112), (24, 18, 58)],
                     query="sunset", color="purple"),
    "ocean":    dict(colors=[(8, 36, 78), (18, 112, 142), (44, 168, 176), (12, 64, 104), (4, 22, 52)],
                     query="ocean", color="teal"),
    "aurora":   dict(colors=[(8, 18, 44), (28, 150, 118), (92, 64, 168), (18, 86, 108), (8, 12, 36)],
                     query="northern lights", color="green"),
    "rose":     dict(colors=[(88, 30, 82), (214, 104, 146), (242, 156, 128), (146, 58, 108), (58, 20, 58)],
                     query="pink sunset", color="magenta"),
    "forest":   dict(colors=[(14, 44, 34), (66, 116, 58), (182, 152, 74), (28, 78, 50), (8, 28, 22)],
                     query="forest", color="green"),
    "midnight": dict(colors=[(6, 8, 28), (26, 96, 190), (34, 58, 150), (14, 24, 78), (4, 6, 22)],
                     query="city night", color="blue"),
    "ember":    dict(colors=[(66, 18, 14), (196, 86, 38), (228, 138, 56), (124, 38, 28), (36, 12, 10)],
                     query="desert", color="orange"),
    "graphite": dict(colors=[(28, 30, 34), (86, 92, 102), (132, 138, 148), (52, 56, 62), (18, 18, 22)],
                     query="architecture", color="black_and_white"),
    "lavender": dict(colors=[(64, 56, 128), (146, 126, 206), (186, 164, 228), (104, 94, 176), (44, 40, 96)],
                     query="lavender", color="purple"),
}
BACKGROUND_ROTATION = list(BACKGROUNDS)  # trim to the ones you like, e.g. ["dusk", "ocean", "rose"]

# Work holidays: date -> (what the reminder says, what a weekend next to it is called).
# They move every year, so these are exact dates; add next year's when you get them.
DAYS_OFF = {
    "2026-01-01": ("New Year's Day", "New Year's"),
    "2026-01-19": ("MLK Day", "MLK Day"),
    "2026-02-16": ("Presidents' Day", "Presidents' Day"),
    "2026-04-03": ("Good Friday", "Easter"),
    "2026-05-22": ("Memorial Day break", "Memorial Day"),
    "2026-05-25": ("Memorial Day", "Memorial Day"),
    "2026-06-19": ("Juneteenth", "Juneteenth"),
    "2026-07-03": ("Fourth of July", "Fourth of July"),
    "2026-09-04": ("Labor Day break", "Labor Day"),
    "2026-09-07": ("Labor Day", "Labor Day"),
    "2026-10-12": ("Columbus Day", "Columbus Day"),
    "2026-11-25": ("Thanksgiving break", "Thanksgiving"),
    "2026-11-26": ("Thanksgiving", "Thanksgiving"),
    "2026-11-27": ("Day after Thanksgiving", "Thanksgiving"),
    "2026-12-24": ("Christmas Eve", "Christmas"),
    "2026-12-25": ("Christmas", "Christmas"),
    "2026-12-31": ("New Year's Eve", "New Year's"),
    "2027-01-01": ("New Year's Day", "New Year's"),
}

# --- Photo sources ---
PHOTO_DIRS = [Path(p) for p in os.environ.get("WALL_PHOTO_DIRS", str(HERE / "images")).split(os.pathsep) if p]
PHOTO_EXTS = {".jpg", ".jpeg", ".png", ".webp", ".heic", ".heif"}
CACHE_DIR = HERE / "cache"
CACHE_KEEP_DAYS = 3
IMMICH_URL = os.environ.get("IMMICH_URL", "https://photos.liaminamerica.com").rstrip("/")
IMMICH_API_KEY = os.environ.get("IMMICH_API_KEY", "")
IMMICH_ALBUM_IDS = [a.strip() for a in os.environ.get("IMMICH_ALBUM_ID", "").split(",") if a.strip()]
UNSPLASH_API = "https://api.unsplash.com"
UNSPLASH_ACCESS_KEY = os.environ.get("UNSPLASH_ACCESS_KEY", "")

# Lock-screen zones (px): iOS clock up top, flashlight/camera in the bottom corners.
REMINDER_Y = 692   # day-off pill, just under the clock
TOP = 760          # photos start here
QUOTE_TOP = 1915   # quote card starts here and grows down
BOTTOM = 2240      # nothing below this (flashlight/camera row)
M = 56             # side margin


def now_local():
    return datetime.now(WALL_TZ)


# =============================================================================
# DAILY PICKS
# =============================================================================

def cycle_pick(items, day, salt):
    """Stable all day; every item shows once before any repeats (reshuffled each cycle)."""
    n = len(items)
    cycle, idx = divmod(day.toordinal(), n)
    order = list(range(n))
    random.Random(f"{salt}:{cycle}").shuffle(order)
    return items[order[idx]]


def pick_layout(day):
    r = random.Random(f"layout:{day}").random()
    for name, p in LAYOUT_ODDS.items():
        if r < p:
            return name
        r -= p
    return next(iter(LAYOUT_ODDS))


def pick_background(day):
    return cycle_pick([b for b in BACKGROUND_ROTATION if b in BACKGROUNDS], day, "background")


def is_day_off(day):
    return day.weekday() >= 5 or day.isoformat() in DAYS_OFF


def day_off_note(day):
    """('Thanksgiving weekend', 'back Mon Nov 30') on a day off, else None."""
    if not is_day_off(day):
        return None
    if day.isoformat() in DAYS_OFF:
        reason = DAYS_OFF[day.isoformat()][0]
    else:  # a weekend: name it after a holiday it's attached to, if any
        reason = day.strftime("%A")
        for step in (-1, 1):
            d = day + timedelta(days=step)
            while is_day_off(d) and reason == day.strftime("%A"):
                if d.isoformat() in DAYS_OFF:
                    reason = f"{DAYS_OFF[d.isoformat()][1]} weekend"
                d += timedelta(days=step)
    back = day + timedelta(days=1)
    while is_day_off(back):
        back += timedelta(days=1)
    return reason, (f"back {back:%a %b} {back.day}" if (back - day).days > 1 else None)


# =============================================================================
# QUOTES
# =============================================================================

QUOTES = [
    ("passion is the genesis of genius!!", "ken"),
    ("I'm just doing me, and to me, that's what got me this far.", "lil uzi vert"),
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
    ("If I am not for myself, who will be for me? If I am only for myself, what am I? If not now, when?", "Hillel"),
    ("In a place where there are no men, strive to be a man.", "Pirkei Avot 2:5"),
    ("It is not your duty to finish the work, but neither are you free to neglect it.", "Pirkei Avot 2:16"),
    ("Who is mighty? One who conquers his own inclination.", "Pirkei Avot 4:1"),
    ("The day is short, the work is great, and the workers are lazy.", "Pirkei Avot 2:15"),
    ("I hated every minute of training, but I said don't quit. Suffer now and live the rest of your life as a champion.", "Muhammad Ali"),
    ("Everyone has a plan until they get punched in the mouth.", "Mike Tyson"),
    ("Hard work beats talent when talent doesn't work hard.", "Tim Notke"),
    ("The last three or four reps is what makes the muscle grow.", "Arnold Schwarzenegger"),
    ("I don't count my sit-ups. I only start counting when it starts hurting.", "Muhammad Ali"),
]


# =============================================================================
# FONTS  (drop .ttf files in ./fonts so every machine renders the same)
# =============================================================================

FONT_DIRS = [HERE / "fonts", Path("/usr/share/fonts/truetype/google-fonts"),
             Path("/usr/share/fonts/truetype/dejavu"), Path.home() / "Library/Fonts", Path("/Library/Fonts")]
FONT_FILES = {
    "regular": ["Poppins-Regular.ttf", "DejaVuSans.ttf"],
    "medium": ["Poppins-Medium.ttf", "DejaVuSans.ttf"],
    "serif_italic": ["Lora-Italic-Variable.ttf", "Lora-Italic.ttf", "Poppins-Italic.ttf",
                     "DejaVuSerif-Italic.ttf", "DejaVuSans-Oblique.ttf"],
}


@lru_cache(None)
def font(size, style="regular"):
    for name in FONT_FILES[style]:
        for d in FONT_DIRS:
            if (d / name).exists():
                try:
                    return ImageFont.truetype(str(d / name), size)
                except OSError:
                    pass
    return ImageFont.load_default(size)


# =============================================================================
# PHOTO SOURCES
# =============================================================================

def _open(src):
    img = Image.open(src)
    img.load()
    img = ImageOps.exif_transpose(img).convert("RGB")
    img.thumbnail((1600, 1600), Image.LANCZOS)  # tiles are small; keeps everything fast
    return img


def folder_photos(day, n, dirs=None):
    """n different photos from the folders, shuffled per day."""
    files = sorted(p for d in (dirs or PHOTO_DIRS) if Path(d).is_dir()
                   for p in Path(d).iterdir() if p.suffix.lower() in PHOTO_EXTS)
    random.Random(f"folder:{day}").shuffle(files)
    return [_open(p) for p in files[:max(0, n)]]


def _get(url, headers=None, data=None, method=None):
    req = urllib.request.Request(url, data=data, method=method, headers=headers or {})
    with urllib.request.urlopen(req, timeout=60) as r:
        return r.read()


def immich_photos(n, day, bg):
    if not IMMICH_API_KEY:
        raise RuntimeError("IMMICH_API_KEY not set")
    hdr = {"x-api-key": IMMICH_API_KEY, "Content-Type": "application/json"}
    rng = random.Random(f"immich:{day}")
    # several albums: spread the picks across them (one random-search request per album used)
    per_album = Counter(rng.choice(IMMICH_ALBUM_IDS) for _ in range(n)) if IMMICH_ALBUM_IDS else {None: n}
    out = []
    for album, k in per_album.items():
        body = {"size": k, "type": "IMAGE"}
        if album:
            body["albumIds"] = [album]
        else:
            body["isFavorite"] = True  # the whole library would surface screenshots and receipts
        assets = json.loads(_get(f"{IMMICH_URL}/api/search/random", hdr, json.dumps(body).encode(), "POST"))
        # "preview" is a ~1440px JPEG: plenty for tiles, and no HEIC decoding needed
        out += [_open(io.BytesIO(_get(f"{IMMICH_URL}/api/assets/{a['id']}/thumbnail?size=preview", hdr)))
                for a in assets[:k]]
    return out


def unsplash_photos(n, day, bg):
    """Stock photos that match today's background: its search term + Unsplash's color filter."""
    if not UNSPLASH_ACCESS_KEY:
        raise RuntimeError("UNSPLASH_ACCESS_KEY not set")
    auth = {"Authorization": f"Client-ID {UNSPLASH_ACCESS_KEY}", "Accept-Version": "v1"}
    rng = random.Random(f"unsplash:{day}")
    params = {"query": bg["query"], "color": bg["color"], "per_page": 30,
              "page": rng.randint(1, 3), "content_filter": "high"}

    def search():
        url = f"{UNSPLASH_API}/search/photos?{urllib.parse.urlencode(params)}"
        return json.loads(_get(url, auth)).get("results", [])

    results = search()
    if len(results) < n and params["page"] != 1:
        params["page"] = 1
        results = search()
    out = []
    for p in rng.sample(results, min(n, len(results))):
        out.append(_open(io.BytesIO(_get(p["urls"]["regular"]))))  # ~1080px wide
        try:  # Unsplash's API guidelines ask apps to report each download
            _get(p["links"]["download_location"], auth)
        except Exception:
            pass
    return out


REMOTE = {"immich": immich_photos, "unsplash": unsplash_photos}


def gather(day, needs, bg, preview_dir=None):
    """needs {'folder': 2, 'immich': 2, 'unsplash': 4} -> {source: [photos]}, topped up from folders."""
    if preview_dir:  # preview: every slot from one folder
        pool = folder_photos(day, sum(needs.values()), [preview_dir])
        out, i = {}, 0
        for src, k in needs.items():
            out[src], i = pool[i:i + k], i + k
        return out

    day_dir = CACHE_DIR / day.isoformat()
    out = {}
    for src, k in needs.items():
        if src not in REMOTE:
            continue
        cached = sorted(day_dir.glob(f"{src}_*.jpg"))
        if len(cached) >= k:
            out[src] = [_open(p) for p in cached[:k]]
            continue
        try:
            got = REMOTE[src](k, day, bg)[:k]
        except Exception as e:
            print(f"[photos] {src} failed ({e}); filling from folders")
            got = []
        if got:
            day_dir.mkdir(parents=True, exist_ok=True)
            for i, p in enumerate(got):
                p.save(day_dir / f"{src}_{i}.jpg", quality=90)
        out[src] = got

    own = needs.get("folder", 0)
    short = sum(k - len(out[s]) for s, k in needs.items() if s in REMOTE)
    local = folder_photos(day, own + short)
    out["folder"], spare = local[:own], local[own:]
    for src, k in needs.items():
        while src in REMOTE and len(out[src]) < k and spare:
            out[src].append(spare.pop(0))

    if CACHE_DIR.is_dir():
        for old in sorted(d for d in CACHE_DIR.iterdir() if d.is_dir())[:-CACHE_KEEP_DAYS]:
            for f in old.iterdir():
                f.unlink()
            old.rmdir()
    return out


def assign(photos, sizes):
    """Give tall slots the tallest photos and wide slots the widest (less gets cropped)."""
    if not photos:
        return [None] * len(sizes)
    pool = [photos[i % len(photos)] for i in range(len(sizes))]
    slot_order = sorted(range(len(sizes)), key=lambda i: sizes[i][1] / sizes[i][0])
    pool.sort(key=lambda p: p.height / p.width)
    out = [None] * len(sizes)
    for slot, photo_ in zip(slot_order, pool):
        out[slot] = photo_
    return out


def fill(slots, groups):
    """Photo per slot from its own source; borrow from the others if that source came back empty."""
    everything = [p for g in groups.values() for p in g]
    result = [None] * len(slots)
    for src in dict.fromkeys(s["src"] for s in slots):
        idx = [i for i, s in enumerate(slots) if s["src"] == src]
        sizes = [(s["box"][2] - s["box"][0], s["box"][3] - s["box"][1]) for s in (slots[i] for i in idx)]
        for i, img in zip(idx, assign(groups.get(src) or everything, sizes)):
            result[i] = img
    return result


# =============================================================================
# GLASS TOOLKIT
# =============================================================================

SS = 4  # supersampling for smooth edges


@lru_cache(None)
def shape_mask(w, h, r, shape="rr"):
    big = Image.new("L", (w * SS, h * SS), 0)
    d = ImageDraw.Draw(big)
    if shape == "circle":
        d.ellipse([0, 0, w * SS - 1, h * SS - 1], fill=255)
    else:
        d.rounded_rectangle([0, 0, w * SS - 1, h * SS - 1], r * SS, fill=255)
    return big.resize((w, h), Image.LANCZOS)


@lru_cache(None)
def ring_mask(w, h, r, shape, width):
    big = Image.new("L", (w * SS, h * SS), 0)
    d = ImageDraw.Draw(big)
    t = round(width * SS)
    if shape == "circle":
        d.ellipse([0, 0, w * SS - 1, h * SS - 1], fill=255)
        d.ellipse([t, t, w * SS - 1 - t, h * SS - 1 - t], fill=0)
    else:
        d.rounded_rectangle([0, 0, w * SS - 1, h * SS - 1], r * SS, fill=255)
        d.rounded_rectangle([t, t, w * SS - 1 - t, h * SS - 1 - t], max(0, r * SS - t), fill=0)
    return big.resize((w, h), Image.LANCZOS)


@lru_cache(None)
def light_falloff(w, h):
    """Light from above: bright top edge, faint sides, a small glint bottom-right."""
    y = np.linspace(0, 1, h)[:, None]
    x = np.linspace(0, 1, w)[None, :]
    return np.clip(0.2 + 0.8 * (1 - y) ** 2.4 + 0.45 * np.clip((x + y - 1.3) / 0.7, 0, 1), 0, 1)


def _box(box):
    x0, y0, x1, y1 = (round(v) for v in box)
    return x0, y0, x1 - x0, y1 - y0


def comp(base, layer, x, y):
    """alpha_composite that tolerates layers hanging off any edge."""
    x, y = round(x), round(y)
    l, t = max(0, -x), max(0, -y)
    r, b = min(layer.width, base.width - x), min(layer.height, base.height - y)
    if r <= l or b <= t:
        return
    if (l, t, r, b) != (0, 0, layer.width, layer.height):
        layer = layer.crop((l, t, r, b))
    base.alpha_composite(layer, (x + l, y + t))


def shadow(base, box, r=40, shape="rr", alpha=0.32, blur=26, dy=18):
    x, y, w, h = _box(box)
    pad = blur * 3
    m = Image.new("L", (w + 2 * pad, h + 2 * pad), 0)
    m.paste(shape_mask(w, h, r, shape), (pad, pad))
    m = m.filter(ImageFilter.GaussianBlur(blur)).point(lambda v: round(v * alpha))
    layer = Image.new("RGBA", m.size, (0, 0, 0, 255))
    layer.putalpha(m)
    comp(base, layer, x - pad, y - pad + dy)


def rim(base, box, r=40, shape="rr", width=2.2, strength=0.85):
    """The bright edge that makes glass read as glass, plus a soft inner glow."""
    x, y, w, h = _box(box)
    ring = np.asarray(ring_mask(w, h, r, shape, width), float) / 255
    glow = np.asarray(ring_mask(w, h, r, shape, width * 5).filter(ImageFilter.GaussianBlur(8)), float) / 255
    inside = np.asarray(shape_mask(w, h, r, shape), float) / 255
    a = (ring + 0.35 * glow * inside) * light_falloff(w, h) * strength
    layer = Image.new("RGBA", (w, h), (255, 255, 255, 0))
    layer.putalpha(Image.fromarray(np.clip(a * 255, 0, 255).astype(np.uint8)))
    comp(base, layer, x, y)


def glass(base, box, r=48, shape="rr", blur=30, tint=0.10, shade=None, shadow_a=0.28):
    """Frosted panel: blurs + brightens whatever is behind it, then adds rim light."""
    x, y, w, h = _box(box)
    if shadow_a:
        shadow(base, box, r, shape, alpha=shadow_a)
    pad = blur * 2
    cx0, cy0 = max(0, x - pad), max(0, y - pad)
    cx1, cy1 = min(base.width, x + w + pad), min(base.height, y + h + pad)
    region = base.crop((cx0, cy0, cx1, cy1)).filter(ImageFilter.GaussianBlur(blur))
    region = region.crop((x - cx0, y - cy0, x - cx0 + w, y - cy0 + h))
    region = ImageEnhance.Color(region.convert("RGB")).enhance(1.35).convert("RGBA")
    if shade is None:  # darken more over bright stuff so white text stays readable
        lum = ImageStat.Stat(region.convert("L")).mean[0] / 255
        shade = float(np.clip((lum - 0.42) * 0.9, 0, 0.38))
    region.alpha_composite(Image.new("RGBA", (w, h), (0, 0, 0, round(shade * 255))))
    region.alpha_composite(Image.new("RGBA", (w, h), (255, 255, 255, round(tint * 255))))
    sheen = (np.clip(1 - np.linspace(0, 1, h) / 0.45, 0, 1) ** 2 * 0.10 * 255).astype(np.uint8)
    sheen_l = Image.new("RGBA", (w, h), (255, 255, 255, 0))
    sheen_l.putalpha(Image.fromarray(np.repeat(sheen[:, None], w, 1)))
    region.alpha_composite(sheen_l)
    region.putalpha(shape_mask(w, h, r, shape))
    comp(base, region, x, y)
    rim(base, box, r, shape)


def photo(base, img, box, r=40, shape="rr", rim_strength=0.7, shadow_a=0.35):
    x, y, w, h = _box(box)
    if img is None:  # nothing loaded for this slot: leave an empty glass tile
        glass(base, box, r, shape, tint=0.12, shadow_a=shadow_a)
        return
    if shadow_a:
        shadow(base, box, r, shape, alpha=shadow_a)
    tile = ImageOps.fit(img, (w, h), Image.LANCZOS, centering=(0.5, 0.45)).convert("RGBA")
    tile.putalpha(shape_mask(w, h, r, shape))
    comp(base, tile, x, y)
    rim(base, box, r, shape, width=2.0, strength=rim_strength)


def glass_photo(base, img, box, r=48, bezel=14, shape="rr"):
    """Photo sitting in a frosted-glass frame."""
    glass(base, box, r, shape, tint=0.16)
    x0, y0, x1, y1 = box
    photo(base, img, (x0 + bezel, y0 + bezel, x1 - bezel, y1 - bezel),
          max(4, r - bezel), shape, rim_strength=0.35, shadow_a=0)


def backdrop(colors, day):
    """Mesh gradient from the background's 5 colors; the blobs drift a little each day."""
    cw, ch = 120, 260
    rng = random.Random(f"drift:{day}")
    spots = [(0.15, 0.08), (0.92, 0.22), (0.45, 0.5), (0.05, 0.78), (0.9, 0.95)]
    spots = [(x + rng.uniform(-0.06, 0.06), y + rng.uniform(-0.04, 0.04)) for x, y in spots]
    yy, xx = np.mgrid[0:ch, 0:cw]
    acc, wsum = np.zeros((ch, cw, 3)), np.zeros((ch, cw, 1))
    for (sx, sy), c in zip(spots, colors):
        w = np.exp(-(((xx / cw - sx) / 0.55) ** 2 + ((yy / ch - sy) / 0.32) ** 2))[..., None] + 1e-6
        acc += w * np.array(c, float)
        wsum += w
    small = Image.fromarray((acc / wsum).clip(0, 255).astype(np.uint8))
    bg = small.resize((WIDTH, HEIGHT), Image.BICUBIC).filter(ImageFilter.GaussianBlur(20))
    noise = np.random.default_rng(0).normal(0, 2.5, (HEIGHT, WIDTH, 1))  # grain: no banding
    arr = np.clip(np.asarray(bg, float) + noise, 0, 255).astype(np.uint8)
    base = Image.fromarray(arr).convert("RGBA")
    # gentle darkening up top so the iOS clock stays readable
    shade = (np.clip(1 - np.linspace(0, 1, 700), 0, 1) ** 1.5 * 0.25 * 255).astype(np.uint8)
    top = Image.new("RGBA", (WIDTH, 700), (0, 0, 0, 0))
    top.putalpha(Image.fromarray(np.repeat(shade[:, None], WIDTH, 1)))
    base.alpha_composite(top)
    return base


# =============================================================================
# TEXT: quote card, day-off reminder, source tags
# =============================================================================

def wrap(draw, text, f, max_w):
    lines, cur = [], ""
    for word in text.split():
        test = f"{cur} {word}".strip()
        if draw.textlength(test, font=f) > max_w and cur:
            lines.append(cur)
            cur = word
        else:
            cur = test
    return lines + [cur] if cur else lines


def quote_card(base, quote, top=QUOTE_TOP, x0=M, x1=WIDTH - M, pad=46):
    d = ImageDraw.Draw(Image.new("L", (1, 1)))
    for size in (42, 38, 34, 30):
        f = font(size, "serif_italic")
        lines = wrap(d, quote[0], f, x1 - x0 - 2 * pad)
        if len(lines) <= 3:
            break
    lh = round(size * 1.38)
    glass(base, (x0, top, x1, top + 2 * pad + len(lines) * lh + 48), r=56)
    text = Image.new("RGBA", base.size, (0, 0, 0, 0))
    sh = Image.new("RGBA", base.size, (0, 0, 0, 0))
    td, sd = ImageDraw.Draw(text), ImageDraw.Draw(sh)
    cx, y = (x0 + x1) / 2, top + pad
    for line in lines:
        td.text((cx, y), line, font=f, fill=(255, 255, 255, 245), anchor="mt")
        sd.text((cx, y + 2), line, font=f, fill=(0, 0, 0, 110), anchor="mt")
        y += lh
    td.text((cx, y + 18), f"— {quote[1]}", font=font(26), fill=(255, 255, 255, 175), anchor="mt")
    base.alpha_composite(sh.filter(ImageFilter.GaussianBlur(6)))
    base.alpha_composite(text)


def reminder(base, note):
    """Glass pill under the clock: '☀ No work today · Thanksgiving · back Mon Nov 30'."""
    reason, back = note
    parts = [("No work today", font(32, "medium"), 250), (f"  ·  {reason}", font(32), 215)]
    if back:
        parts.append((f"  ·  {back}", font(32), 160))
    d = ImageDraw.Draw(base)
    text_w = sum(d.textlength(t, font=f) for t, f, _ in parts)
    icon, pad_x, h = 30, 34, 76
    w = pad_x * 2 + icon + 18 + text_w
    x0, y0 = (WIDTH - w) / 2, REMINDER_Y - h / 2
    glass(base, (x0, y0, x0 + w, y0 + h), r=h // 2, tint=0.14, shadow_a=0.2)
    d = ImageDraw.Draw(base)
    sx, sy = x0 + pad_x + icon / 2, REMINDER_Y  # little sun
    d.ellipse([sx - 8, sy - 8, sx + 8, sy + 8], fill=(255, 205, 95, 255))
    for k in range(8):
        a = k * np.pi / 4
        d.line([(sx + 12 * np.cos(a), sy + 12 * np.sin(a)), (sx + 16 * np.cos(a), sy + 16 * np.sin(a))],
               fill=(255, 205, 95, 255), width=3)
    x = x0 + pad_x + icon + 18
    for t, f, alpha in parts:
        d.text((x, REMINDER_Y), t, font=f, fill=(255, 255, 255, alpha), anchor="lm")
        x += d.textlength(t, font=f)


def source_tag(base, box, text):
    """Preview-only label showing where a slot's photo comes from."""
    d = ImageDraw.Draw(base)
    f = font(24, "medium")
    w = d.textlength(text, font=f) + 28
    x0, y0, x1, y1 = box
    cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
    d.rounded_rectangle([cx - w / 2, cy - 22, cx + w / 2, cy + 22], 22, fill=(0, 0, 0, 170))
    d.text((cx, cy), text, font=f, fill=(255, 255, 255, 255), anchor="mm")


# =============================================================================
# LAYOUTS: each slot = box + where its photo comes from
# =============================================================================

def bento_slots():
    """8 tiles: the 2 big ones from Immich, the 2 small bottom-left from your folders, rest Unsplash."""
    g, cols, rows = 16, 4, 4
    uw = (WIDTH - 2 * M - (cols - 1) * g) / cols
    uh = (QUOTE_TOP - 30 - TOP - (rows - 1) * g) / rows
    spec = [  # (col, row, width, height, source)
        (0, 0, 2, 2, "immich"), (2, 0, 2, 1, "unsplash"), (2, 1, 1, 1, "unsplash"), (3, 1, 1, 2, "unsplash"),
        (0, 2, 1, 1, "folder"), (1, 2, 2, 2, "immich"), (0, 3, 1, 1, "folder"), (3, 3, 1, 1, "unsplash"),
    ]
    return [dict(src=src, shape="rr", box=(M + c * (uw + g), TOP + r * (uh + g),
                                           M + c * (uw + g) + w * uw + (w - 1) * g,
                                           TOP + r * (uh + g) + h * uh + (h - 1) * g))
            for c, r, w, h, src in spec]


def bubbles_slots():
    """9 circles: the 2 biggest from Immich, the 2 bottom-left from your folders, rest Unsplash."""
    spec = [  # (center x, center y, diameter, source)
        (560, 1300, 500, "immich"), (950, 1000, 290, "immich"),
        (230, 980, 260, "unsplash"), (640, 900, 190, "unsplash"), (980, 1430, 270, "unsplash"),
        (760, 1720, 250, "unsplash"), (1030, 1760, 150, "unsplash"),
        (190, 1420, 240, "folder"), (360, 1730, 200, "folder"),
    ]
    return [dict(src=src, shape="circle", box=(cx - d / 2, cy - d / 2, cx + d / 2, cy + d / 2))
            for cx, cy, d, src in spec]


SLOTS = {"bento": bento_slots, "bubbles": bubbles_slots}


# =============================================================================
# RENDER
# =============================================================================

def render(*args, now=None, layout=None, background=None, photos_dir=None, show_sources=False):
    """render(), render(now), and the old habit-tracker call render(habit_data, now) all work."""
    for a in args:  # positional args: keep the datetime, ignore old habit data
        if isinstance(a, datetime):
            now = a
    day = (now or now_local()).date()
    layout = layout or pick_layout(day)
    bg = BACKGROUNDS[background or pick_background(day)]
    slots = SLOTS[layout]()
    groups = gather(day, Counter(s["src"] for s in slots), bg, photos_dir)

    base = backdrop(bg["colors"], day)
    for slot, img in zip(slots, fill(slots, groups)):
        if slot["shape"] == "circle":
            d = slot["box"][2] - slot["box"][0]
            glass_photo(base, img, slot["box"], shape="circle", bezel=max(8, round(d) // 28))
        else:
            photo(base, img, slot["box"], r=44)
        if show_sources:
            source_tag(base, slot["box"], {"folder": "Folder", "immich": "Immich",
                                           "unsplash": "Unsplash"}[slot["src"]])
    note = day_off_note(day)
    if note:
        reminder(base, note)
    quote_card(base, cycle_pick(QUOTES, day, "quote"))
    return base.convert("RGB")


# Old habit-tracker names, kept so existing server code that imports them keeps working.
habit_now = now_local
HABITS = []


def generate_mock_data():
    return {}


def get_daily_quote(now=None):
    return cycle_pick(QUOTES, (now or now_local()).date(), "quote")


def main():
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", help="render as if it were this date (YYYY-MM-DD)")
    ap.add_argument("--layout", choices=list(SLOTS), help="override the daily 70/30 pick")
    ap.add_argument("--background", choices=list(BACKGROUNDS), help="override the daily rotation")
    ap.add_argument("--photos-dir", help="preview: fill every slot from this folder")
    ap.add_argument("--show-sources", action="store_true", help="preview: label each slot's photo source")
    ap.add_argument("--output", default=str(OUTPUT_PATH))
    ap.add_argument("--mock", action="store_true", help=argparse.SUPPRESS)  # old flag, ignored
    args = ap.parse_args()
    now = now_local()
    if args.date:
        now = datetime.strptime(args.date, "%Y-%m-%d").replace(hour=12, tzinfo=WALL_TZ)
    day = now.date()
    layout, background = args.layout or pick_layout(day), args.background or pick_background(day)
    img = render(now=now, layout=layout, background=background, photos_dir=args.photos_dir,
                 show_sources=args.show_sources)
    out = Path(args.output)
    out.parent.mkdir(parents=True, exist_ok=True)
    img.save(out, "PNG")
    note = day_off_note(day)
    print(f"Saved: {out}  ({day:%a %b %d}: {layout}, {background}"
          + (f", day off: {note[0]}" if note else "") + ")")


if __name__ == "__main__":
    main()