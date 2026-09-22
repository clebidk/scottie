"""Cycle 65: retired-brand graphics filter. The tenant's 2026 brand forbids
green, but its product galleries still carry infographics in the retired
look -- "Modern Infrared Luxury" (mint background, pale green icon chips)
and "The essentials" (a dark green power panel) -- and listicle pages were
placing them. `harness images brandcheck` scores every asset in every
active product's pool on its pixels, stores `old_brand: true` (with the
scores) on the flagged ones in tenants/<t>/brand/asset-review.json, and
asset_review.excluded_ids/apply_asset_review keep a flagged asset out of
every page's pool, the same way a human "exclude" does. No image file is
ever deleted.

Pixel rule, on a THUMB-pixel copy:
- green: share of saturated green pixels (hue 90-170 deg, saturation and
  value >= 0.25) -- the dark green panel;
- mint: share of pale green-tint pixels (hue 90-170 deg, saturation
  0.04-0.25, value >= 0.75) -- the mint background and chips;
- flat: share of the 6 most common colors (4 bits per channel) -- a flat
  graphic scores high, a photo low.
Green alone cannot decide it: product photos with the cabin's green
chromotherapy light on score up to 0.97 green. So an asset is old-brand
only when it is ALSO flat (a graphic): flat >= MIN_FLAT and mint >=
MINT_CUT, or flat >= GREEN_FLAT and green >= GREEN_CUT. Tuned on the whole
pool (1,029 images, cycle 65 table in docs/FIXLOG.md): the 20 flagged
infographics score mint >= 0.26 or green >= 0.069 at flat >= 0.87; the
closest photo scores mint 0.18 at flat 0.61, or green 0.11 at flat 0.76.
"""

import glob
from collections import Counter
from pathlib import Path

from PIL import Image

from . import asset_review
from .render import download_asset

THUMB = 128
HUE_RANGE = (90, 170)
MIN_FLAT = 0.7
MINT_CUT = 0.2
GREEN_FLAT = 0.85
GREEN_CUT = 0.04
FLAGGED_BY = "harness images brandcheck"


def image_scores(path):
    """{"green", "mint", "flat"} shares (0..1) for the image at `path`."""
    with Image.open(path) as im:
        im = im.convert("RGB")
        im.thumbnail((THUMB, THUMB))
        rgb = im.tobytes()
        hsv = im.convert("HSV").tobytes()
    n = len(rgb) // 3
    lo, hi = HUE_RANGE[0] * 255 / 360, HUE_RANGE[1] * 255 / 360
    green = mint = 0
    for i in range(0, len(hsv), 3):
        h, s, v = hsv[i], hsv[i + 1], hsv[i + 2]
        if not lo <= h <= hi:
            continue
        if s >= 64 and v >= 64:
            green += 1
        elif 10 <= s < 64 and v >= 191:
            mint += 1
    colors = Counter((rgb[i] >> 4, rgb[i + 1] >> 4, rgb[i + 2] >> 4) for i in range(0, len(rgb), 3))
    flat = sum(c for _, c in colors.most_common(6))
    return {"green": round(green / n, 3), "mint": round(mint / n, 3), "flat": round(flat / n, 3)}


def is_old_brand(scores):
    flat = scores["flat"]
    return (flat >= MIN_FLAT and scores["mint"] >= MINT_CUT) or (flat >= GREEN_FLAT and scores["green"] >= GREEN_CUT)


def cached_image(asset, cache_dirs):
    """A downloaded copy of `asset` from any of `cache_dirs` (the
    render.download_asset `<id>-<width>.jpg` layout the describe step and
    the /images site already fill), smallest width first; None if none."""
    for cache_dir in cache_dirs:
        paths = glob.glob(str(Path(cache_dir) / f"{glob.escape(asset['id'])}-*.jpg"))
        widths = []
        for p in paths:
            tail = Path(p).stem.rsplit("-", 1)[-1]
            if tail.isdigit():
                widths.append((int(tail), p))
        if widths:
            return Path(min(widths)[1])
    return None


def check_assets(assets, cache_dirs, *, download=download_asset, log=None):
    """[{"asset", "scores", "old_brand"}] for each of `assets` whose image
    could be read (from a cache, else downloaded into cache_dirs[0]), and
    the list of assets it could not read."""
    results, unreadable = [], []
    for asset in assets:
        path = cached_image(asset, cache_dirs)
        if path is None:
            downloaded = download(asset, cache_dirs[0], log=log)
            path = downloaded and downloaded.get("path")
        try:
            scores = image_scores(path) if path else None
        except OSError:
            scores = None
        if scores is None:
            unreadable.append(asset)
            continue
        results.append({"asset": asset, "scores": scores, "old_brand": is_old_brand(scores)})
    return results, unreadable


def apply_flags(review, results, now=None):
    """`review` (asset-review.json's dict) with every checked asset's
    old_brand flag brought up to date: a flagged asset gets `old_brand:
    true` and its scores (a new entry when it has none -- alt "", not
    excluded, so a human can still see and act on it); an entry flagged by
    an earlier check that no longer scores as old-brand has the flag
    removed. Every other key of an entry is left as it was. Returns (new
    review, flagged ids, cleared ids)."""
    overrides = dict((review or {}).get("assets") or {})
    now = now or asset_review.now_iso()
    flagged, cleared = [], []
    for result in results:
        asset = result["asset"]
        entry = overrides.get(asset["id"])
        if entry and "url" in entry and entry["url"] != asset.get("url"):
            entry = None  # stale Shopify override (image list changed) -- never re-point it
        if result["old_brand"]:
            entry = dict(entry or {"alt": "", "excluded": False, "note": "", "by": FLAGGED_BY, "at": now})
            entry["old_brand"] = True
            entry["old_brand_scores"] = result["scores"]
            if asset.get("source") == "shopify":
                entry["url"] = asset.get("url")
            overrides[asset["id"]] = entry
            flagged.append(asset["id"])
        elif entry and entry.get("old_brand"):
            entry = {k: v for k, v in entry.items() if k not in ("old_brand", "old_brand_scores")}
            overrides[asset["id"]] = entry
            cleared.append(asset["id"])
    return {**(review or {}), "version": 1, "assets": overrides}, flagged, cleared
