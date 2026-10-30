#!/usr/bin/env python3
"""
Glass Photo Album Wallpaper
Lots of small photos from your library on iPhone-style glass, plus the daily quote.

Usage:
  python gen_wallpaper.py                          # today's wallpaper -> habit_wallpaper.png
  python gen_wallpaper.py --date 2026-11-26        # preview any date (Thanksgiving -> bubbles)
  python gen_wallpaper.py --layout bubbles         # force a layout: bento | album | stack | bubbles | carousel
  python gen_wallpaper.py --photos-dir samples/    # use a different folder

Schedule: WORKDAY_LAYOUT on workdays, DAY_OFF_LAYOUT on weekends and DAYS_OFF (see CONFIG).

Photo sources (env var, comma-separated to mix, e.g. WALL_PHOTO_SOURCE=immich,unsplash):
  folder     pick from ./images (default)
  immich     your own photos from Immich
      IMMICH_URL=https://photos.liaminamerica.com
      IMMICH_API_KEY=...         (Immich > Account Settings > API Keys)
      IMMICH_ALBUM_ID=...        (album to pull from; if unset, uses your Immich favorites)
  unsplash   free stock photos from Unsplash
      UNSPLASH_ACCESS_KEY=...    (unsplash.com/developers > New Application > Access Key)
      UNSPLASH_QUERY=landscape   (any search: "mountains", "ocean", "city night", ...)
      UNSPLASH_COLLECTIONS=...   (optional: collection IDs instead of a search, for a hand-picked set)
  Remote picks are cached per day. Any failure falls back to ./images, then to a plain gradient.
"""

import io
import json
import os
import random
import urllib.parse
import urllib.request
from datetime import datetime
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
# --- Which layout when ---
WORKDAY_LAYOUT = "bento"
DAY_OFF_LAYOUT = "bubbles"
# Work holidays. They move every year, so these are exact dates; add next year's when you get them.
DAYS_OFF = {
    "2026-01-01", "2026-01-19", "2026-02-16", "2026-04-03", "2026-05-22", "2026-05-25",
    "2026-06-19", "2026-07-03", "2026-09-04", "2026-09-07", "2026-10-12",
    "2026-11-25", "2026-11-26", "2026-11-27", "2026-12-24", "2026-12-25", "2026-12-31",
    "2027-01-01",
}


def is_day_off(day):
    return day.weekday() >= 5 or day.isoformat() in DAYS_OFF  # Sat/Sun or a listed holiday


def layout_for(day):
    return DAY_OFF_LAYOUT if is_day_off(day) else WORKDAY_LAYOUT


# --- Photos ---
PHOTO_SOURCES = [s.strip() for s in os.environ.get("WALL_PHOTO_SOURCE", "folder").split(",") if s.strip()]
PHOTO_DIR = HERE / "images"
PHOTO_EXTS = {".jpg", ".jpeg", ".png", ".webp", ".heic", ".heif"}
CACHE_DIR = HERE / "cache"
CACHE_KEEP_DAYS = 3
IMMICH_URL = os.environ.get("IMMICH_URL", "https://photos.liaminamerica.com").rstrip("/")
IMMICH_API_KEY = os.environ.get("IMMICH_API_KEY", "")
IMMICH_ALBUM_ID = os.environ.get("IMMICH_ALBUM_ID", "")
UNSPLASH_API = "https://api.unsplash.com"
UNSPLASH_ACCESS_KEY = os.environ.get("UNSPLASH_ACCESS_KEY", "")
UNSPLASH_QUERY = os.environ.get("UNSPLASH_QUERY", "landscape")
UNSPLASH_COLLECTIONS = os.environ.get("UNSPLASH_COLLECTIONS", "")

# Lock-screen zones (px): iOS clock up top, flashlight/camera in the bottom corners.
TOP = 760          # photos start below the clock
QUOTE_TOP = 1915   # quote card starts here and grows down
BOTTOM = 2240      # nothing below this (flashlight/camera row)
M = 56             # side margin


def now_local():
    return datetime.now(WALL_TZ)


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


def get_daily_quote(now):
    """Stable all day; every quote shows once before any repeats."""
    n = len(QUOTES)
    cycle, idx = divmod(now.date().toordinal(), n)
    order = list(range(n))
    random.Random(f"quote:{cycle}").shuffle(order)
    return QUOTES[order[idx]]


# =============================================================================
# FONTS  (drop .ttf files in ./fonts so every machine renders the same)
# =============================================================================

FONT_DIRS = [HERE / "fonts", Path("/usr/share/fonts/truetype/google-fonts"),
             Path("/usr/share/fonts/truetype/dejavu"), Path.home() / "Library/Fonts", Path("/Library/Fonts")]
FONT_FILES = {
    "regular": ["Poppins-Regular.ttf", "DejaVuSans.ttf"],
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
# PHOTOS
# =============================================================================

def _open(src):
    img = Image.open(src)
    img.load()
    img = ImageOps.exif_transpose(img).convert("RGB")
    img.thumbnail((1600, 1600), Image.LANCZOS)  # tiles are small; keeps everything fast
    return img


def folder_photos(now, n, folder=None):
    folder = Path(folder) if folder else PHOTO_DIR
    if n <= 0 or not folder.is_dir():
        return []
    files = sorted(p for p in folder.iterdir() if p.suffix.lower() in PHOTO_EXTS)
    if not files:
        return []
    rng = random.Random(f"photos:{now:%Y-%m-%d}")
    picks = rng.sample(files, min(n, len(files)))
    return [_open(p) for p in picks]


def _immich(method, path, body=None):
    req = urllib.request.Request(
        IMMICH_URL + path, method=method,
        data=json.dumps(body).encode() if body is not None else None,
        headers={"x-api-key": IMMICH_API_KEY, "Content-Type": "application/json",
                 "Accept": "application/json, image/*"})
    with urllib.request.urlopen(req, timeout=60) as r:
        return r.read()


def immich_photos(n):
    if not IMMICH_API_KEY:
        raise RuntimeError("IMMICH_API_KEY not set")
    body = {"size": n, "type": "IMAGE"}
    if IMMICH_ALBUM_ID:
        body["albumIds"] = [IMMICH_ALBUM_ID]
    else:
        body["isFavorite"] = True  # the whole library would surface screenshots and receipts
    assets = json.loads(_immich("POST", "/api/search/random", body))
    # "preview" is a ~1440px JPEG: plenty for small tiles, and no HEIC decoding needed
    return [_open(io.BytesIO(_immich("GET", f"/api/assets/{a['id']}/thumbnail?size=preview")))
            for a in assets[:n]]


def _get(url, headers=None):
    req = urllib.request.Request(url, headers=headers or {})
    with urllib.request.urlopen(req, timeout=60) as r:
        return r.read()


def unsplash_photos(n):
    if not UNSPLASH_ACCESS_KEY:
        raise RuntimeError("UNSPLASH_ACCESS_KEY not set")
    auth = {"Authorization": f"Client-ID {UNSPLASH_ACCESS_KEY}", "Accept-Version": "v1"}
    params = {"count": min(n, 30), "content_filter": "high"}
    if UNSPLASH_COLLECTIONS:
        params["collections"] = UNSPLASH_COLLECTIONS
    else:
        params["query"] = UNSPLASH_QUERY
    picks = json.loads(_get(f"{UNSPLASH_API}/photos/random?{urllib.parse.urlencode(params)}", auth))
    out = []
    for p in picks[:n]:
        out.append(_open(io.BytesIO(_get(p["urls"]["regular"]))))  # ~1080px wide
        try:  # Unsplash's API guidelines ask apps to report each download
            _get(p["links"]["download_location"], auth)
        except Exception:
            pass
    return out


FETCHERS = {
    "folder": lambda n, now: folder_photos(now, n),
    "immich": lambda n, now: immich_photos(n),
    "unsplash": lambda n, now: unsplash_photos(n),
}
REMOTE = {"immich", "unsplash"}


def todays_photos(now, n, folder=None):
    """n photos for today, split evenly across the configured sources. Remote picks are cached per day."""
    if folder:
        return folder_photos(now, n, folder)
    sources = [s for s in PHOTO_SOURCES if s in FETCHERS] or ["folder"]
    remote = any(s in REMOTE for s in sources)
    day_dir = CACHE_DIR / f"{now:%Y-%m-%d}"
    if remote and day_dir.is_dir():
        cached = [_open(p) for p in sorted(day_dir.glob("*.jpg"))]
        if cached:
            return (cached + folder_photos(now, n - len(cached)))[:n]

    photos = []
    for i, src in enumerate(sources):
        share = -(-(n - len(photos)) // (len(sources) - i))  # ceil: a failed source's share rolls forward
        try:
            photos += FETCHERS[src](share, now)[:share]
        except Exception as e:
            print(f"[photos] {src} failed ({e})")
    if len(photos) < n and "folder" not in sources:
        photos += folder_photos(now, n - len(photos))
    random.Random(f"order:{now:%Y-%m-%d}").shuffle(photos)  # mix sources across the layout

    if remote and photos:
        day_dir.mkdir(parents=True, exist_ok=True)
        for i, p in enumerate(photos):
            p.save(day_dir / f"{i:02d}.jpg", quality=90)
        for old in sorted(d for d in CACHE_DIR.iterdir() if d.is_dir())[:-CACHE_KEEP_DAYS]:
            for f in old.iterdir():
                f.unlink()
            old.rmdir()
    return photos


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


def photo(base, img, box, r=40, shape="rr", rim_strength=0.7, shadow_a=0.35, dim=0.0):
    x, y, w, h = _box(box)
    if shadow_a:
        shadow(base, box, r, shape, alpha=shadow_a)
    if img is None:
        tile = Image.new("RGBA", (w, h), (60, 60, 70, 255))
    else:
        tile = ImageOps.fit(img, (w, h), Image.LANCZOS, centering=(0.5, 0.45)).convert("RGBA")
    if dim:
        tile.alpha_composite(Image.new("RGBA", (w, h), (0, 0, 0, round(dim * 255))))
    tile.putalpha(shape_mask(w, h, r, shape))
    comp(base, tile, x, y)
    rim(base, box, r, shape, width=2.0, strength=rim_strength)


def glass_photo(base, img, box, r=48, bezel=14, shape="rr"):
    """Photo sitting in a frosted-glass frame."""
    glass(base, box, r, shape, tint=0.16)
    x0, y0, x1, y1 = box
    photo(base, img, (x0 + bezel, y0 + bezel, x1 - bezel, y1 - bezel),
          max(4, r - bezel), shape, rim_strength=0.35, shadow_a=0)


def _accent(img):
    """A photo's most colorful average color, pushed toward vivid-but-dark."""
    small = img.resize((40, 40))
    hsv = np.asarray(small.convert("HSV"), float).reshape(-1, 3)
    rgb = np.asarray(small.convert("RGB"), float).reshape(-1, 3)
    score = hsv[:, 1] * hsv[:, 2]
    c = rgb[score >= np.percentile(score, 70)].mean(0)
    h, s, v = Image.new("RGB", (1, 1), tuple(int(x) for x in c)).convert("HSV").getpixel((0, 0))
    s = int(np.clip(s * 1.5, 90, 215))
    v = int(np.clip(v * 1.1, 110, 200))
    return np.array(Image.new("HSV", (1, 1), (h, s, v)).convert("RGB").getpixel((0, 0)), float)


def backdrop(photos):
    """Soft mesh gradient in the day's photo colors, like an iOS color wallpaper."""
    cw, ch = 120, 260
    spots = [(0.15, 0.08), (0.92, 0.22), (0.45, 0.5), (0.05, 0.78), (0.9, 0.95)]
    colors = [_accent(p) for p in photos[:len(spots)]] or [np.array([40.0, 40, 60])]
    yy, xx = np.mgrid[0:ch, 0:cw]
    acc, wsum = np.zeros((ch, cw, 3)), np.zeros((ch, cw, 1))
    for i, (sx, sy) in enumerate(spots):
        d2 = ((xx / cw - sx) / 0.55) ** 2 + ((yy / ch - sy) / 0.32) ** 2
        w = np.exp(-d2)[..., None] + 1e-6
        acc += w * colors[i % len(colors)]
        wsum += w
    small = Image.fromarray((acc / wsum).clip(0, 255).astype(np.uint8))
    bg = small.resize((WIDTH, HEIGHT), Image.BICUBIC).filter(ImageFilter.GaussianBlur(20))
    bg = ImageEnhance.Brightness(bg).enhance(0.78)
    noise = np.random.default_rng(0).normal(0, 2.5, (HEIGHT, WIDTH, 1))
    arr = np.clip(np.asarray(bg, float) + noise, 0, 255).astype(np.uint8)
    return Image.fromarray(arr).convert("RGBA")


# =============================================================================
# QUOTE CARD
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


def measure_quote(quote, width, pad=46):
    d = ImageDraw.Draw(Image.new("L", (1, 1)))
    for size in (42, 38, 34, 30):
        f = font(size, "serif_italic")
        lines = wrap(d, quote[0], f, width - 2 * pad)
        if len(lines) <= 3:
            break
    lh = round(size * 1.38)
    return dict(lines=lines, f=f, lh=lh, fa=font(26), pad=pad, h=2 * pad + len(lines) * lh + 18 + 30)


def draw_quote_text(base, quote, q, x0, x1, top):
    text = Image.new("RGBA", base.size, (0, 0, 0, 0))
    sh = Image.new("RGBA", base.size, (0, 0, 0, 0))
    td, sd = ImageDraw.Draw(text), ImageDraw.Draw(sh)
    cx, y = (x0 + x1) / 2, top + q["pad"]
    for line in q["lines"]:
        td.text((cx, y), line, font=q["f"], fill=(255, 255, 255, 245), anchor="mt")
        sd.text((cx, y + 2), line, font=q["f"], fill=(0, 0, 0, 110), anchor="mt")
        y += q["lh"]
    td.text((cx, y + 18), f"— {quote[1]}", font=q["fa"], fill=(255, 255, 255, 175), anchor="mt")
    base.alpha_composite(sh.filter(ImageFilter.GaussianBlur(6)))
    base.alpha_composite(text)


def quote_card(base, quote, top=QUOTE_TOP, x0=M, x1=WIDTH - M):
    q = measure_quote(quote, x1 - x0)
    glass(base, (x0, top, x1, top + q["h"]), r=56)
    draw_quote_text(base, quote, q, x0, x1, top)


# =============================================================================
# LAYOUTS
# =============================================================================

def layout_bento(base, photos, quote):
    """Mixed big/small tiles, like a widget page."""
    g, cols, rows = 16, 4, 4
    area_h = QUOTE_TOP - 30 - TOP
    uw = (WIDTH - 2 * M - (cols - 1) * g) / cols
    uh = (area_h - (rows - 1) * g) / rows
    spec = [(0, 0, 2, 2), (2, 0, 2, 1), (2, 1, 1, 1), (3, 1, 1, 2),
            (0, 2, 1, 1), (1, 2, 2, 2), (0, 3, 1, 1), (3, 3, 1, 1)]
    boxes = [(M + c * (uw + g), TOP + r * (uh + g), M + c * (uw + g) + w * uw + (w - 1) * g,
              TOP + r * (uh + g) + h * uh + (h - 1) * g) for c, r, w, h in spec]
    for img, b in zip(assign(photos, [(b[2] - b[0], b[3] - b[1]) for b in boxes]), boxes):
        photo(base, img, b, r=44)
    quote_card(base, quote)


def layout_album(base, photos, quote):
    """One big glass album card: a 3x3 photo grid with the quote underneath."""
    pad, g, cols, rows = 30, 12, 3, 3
    inner_w = WIDTH - 2 * M - 2 * pad
    cell = (inner_w - (cols - 1) * g) / cols
    grid_h = rows * cell + (rows - 1) * g
    q = measure_quote(quote, WIDTH - 2 * M)
    panel_h = pad + grid_h + q["h"] - 10
    top = max(TOP, BOTTOM - 20 - panel_h)
    glass(base, (M, top, WIDTH - M, top + panel_h), r=64, tint=0.12)
    boxes = [(M + pad + c * (cell + g), top + pad + r * (cell + g),
              M + pad + c * (cell + g) + cell, top + pad + r * (cell + g) + cell)
             for r in range(rows) for c in range(cols)]
    for i, b in enumerate(boxes):
        photo(base, photos[i % len(photos)] if photos else None, b, r=30, rim_strength=0.45, shadow_a=0.12)
    draw_quote_text(base, quote, q, M, WIDTH - M, top + pad + grid_h - 16)


def layout_stack(base, photos, quote):
    """A loose pile of glass-framed prints."""
    cards = [  # (center x, center y, w, h, angle)
        (330, 1085, 450, 560, -11),
        (850, 1060, 450, 560, 10),
        (320, 1560, 480, 380, 8),
        (860, 1575, 440, 560, -7),
        (590, 1330, 560, 700, -2),
    ]
    imgs = assign(photos, [(c[2], c[3]) for c in cards])
    for (cx, cy, w, h, ang), img in zip(cards, imgs):
        card = Image.new("RGBA", (w, h), (0, 0, 0, 0))
        # sample the backdrop under the card so the glass frame matches what's behind it
        under = base.crop((round(cx - w / 2), round(cy - h / 2), round(cx + w / 2), round(cy + h / 2)))
        card.alpha_composite(under)
        glass_photo(card, img, (0, 0, w, h), r=40, bezel=16)
        card.putalpha(Image.fromarray(np.minimum(np.asarray(card.getchannel("A")),
                                                 np.asarray(shape_mask(w, h, 40)))))
        rot = card.rotate(ang, resample=Image.BICUBIC, expand=True)
        sh = rot.getchannel("A").point(lambda v: round(v * 0.45)).filter(ImageFilter.GaussianBlur(24))
        sl = Image.new("RGBA", rot.size, (0, 0, 0, 255))
        sl.putalpha(sh)
        comp(base, sl, cx - rot.width / 2, cy - rot.height / 2 + 22)
        comp(base, rot, cx - rot.width / 2, cy - rot.height / 2)
    quote_card(base, quote)


def layout_bubbles(base, photos, quote):
    """Round photos in a loose cluster, each in a glass ring."""
    bubbles = [  # (center x, center y, diameter)
        (560, 1300, 500),
        (950, 1000, 290), (230, 980, 260), (640, 900, 190),
        (980, 1430, 270), (190, 1420, 240),
        (760, 1720, 250), (360, 1730, 200), (1030, 1760, 150),
    ]
    imgs = assign(photos, [(d, d) for _, _, d in bubbles])
    for (cx, cy, d), img in zip(bubbles, imgs):
        b = (cx - d / 2, cy - d / 2, cx + d / 2, cy + d / 2)
        glass_photo(base, img, b, shape="circle", bezel=max(8, d // 28))
    quote_card(base, quote)


def layout_carousel(base, photos, quote):
    """Cover-flow: one hero card with neighbours peeking, plus a glass dock of thumbnails."""
    hero_w, hero_h, hero_top = 600, 780, TOP + 20
    side_w, side_h = 430, 580
    side_top = hero_top + (hero_h - side_h) / 2
    imgs = assign(photos, [(hero_w, hero_h), (side_w, side_h), (side_w, side_h)] + [(150, 150)] * 5)
    for cx, img in ((WIDTH / 2 - 400, imgs[1]), (WIDTH / 2 + 400, imgs[2])):
        soft = img.filter(ImageFilter.GaussianBlur(4)) if img else None
        photo(base, soft, (cx - side_w / 2, side_top, cx + side_w / 2, side_top + side_h), r=52,
              dim=0.4, rim_strength=0.4)
    hx = (WIDTH - hero_w) / 2
    shadow(base, (hx, hero_top, hx + hero_w, hero_top + hero_h), 56, alpha=0.5, blur=40, dy=30)
    photo(base, imgs[0], (hx, hero_top, hx + hero_w, hero_top + hero_h), r=56, rim_strength=0.9)
    d = ImageDraw.Draw(base)  # page dots
    dots_y = hero_top + hero_h + 42
    for i in range(5):
        x = WIDTH / 2 + (i - 2) * 36
        d.ellipse([x - 9, dots_y - 9, x + 9, dots_y + 9], fill=(255, 255, 255, 235 if i == 2 else 90))
    dock_top, dock_h = dots_y + 50, 196  # glass dock of thumbnails
    glass(base, (M, dock_top, WIDTH - M, dock_top + dock_h), r=72, tint=0.14)
    icon, n = 150, 5
    gap = (WIDTH - 2 * M - n * icon) / (n + 1)
    for i in range(n):
        x = M + gap + i * (icon + gap)
        photo(base, imgs[3 + i], (x, dock_top + (dock_h - icon) / 2, x + icon, dock_top + (dock_h + icon) / 2),
              r=40, rim_strength=0.6, shadow_a=0.2)
    quote_card(base, quote, top=max(QUOTE_TOP, dock_top + dock_h + 28))


LAYOUTS = {"bento": (layout_bento, 8), "album": (layout_album, 9), "stack": (layout_stack, 5),
           "bubbles": (layout_bubbles, 9), "carousel": (layout_carousel, 8)}


# =============================================================================
# RENDER
# =============================================================================

def render(now=None, layout=None, photos_dir=None):
    now = now or now_local()
    fn, n = LAYOUTS[layout or layout_for(now.date())]
    photos = todays_photos(now, n, photos_dir)
    base = backdrop(photos)
    # gentle darkening up top so the iOS clock stays readable
    top_shade = np.clip(1 - np.linspace(0, 1, 700), 0, 1) ** 1.5 * 0.25 * 255
    tl = Image.new("RGBA", (WIDTH, 700), (0, 0, 0, 0))
    tl.putalpha(Image.fromarray(np.repeat(top_shade.astype(np.uint8)[:, None], WIDTH, 1)))
    base.alpha_composite(tl)
    fn(base, photos, get_daily_quote(now))
    return base.convert("RGB")


def main():
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--layout", choices=list(LAYOUTS), help="override the workday/day-off schedule")
    ap.add_argument("--date", help="render as if it were this date (YYYY-MM-DD)")
    ap.add_argument("--photos-dir", help="pick from this folder instead of the configured sources")
    ap.add_argument("--output", default=str(OUTPUT_PATH))
    ap.add_argument("--mock", action="store_true", help=argparse.SUPPRESS)  # old flag, ignored
    args = ap.parse_args()
    now = now_local()
    if args.date:
        now = datetime.strptime(args.date, "%Y-%m-%d").replace(hour=12, tzinfo=WALL_TZ)
    layout = args.layout or layout_for(now.date())
    img = render(now, layout, args.photos_dir)
    out = Path(args.output)
    out.parent.mkdir(parents=True, exist_ok=True)
    img.save(out, "PNG")
    print(f"Saved: {out}  ({img.size[0]}x{img.size[1]}, {now:%a %b %d} -> {layout})")


if __name__ == "__main__":
    main()