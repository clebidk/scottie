"""Cycle 93: images as a first-class pipeline. See docs/IMAGES.md section 11.

Three parts:

1. Richer tags. Each photo-library entry (brand/photo-library.json) may carry
   an `image_tags` object next to its cycle 75 `tags` (additive; every
   existing field stays as it is). The measured part is deterministic
   (Pillow, no model call; `harness photos measure`):
     orientation, aspect, mood (bright | warm | dark), luminance, warmth,
     white_edge, fullbleed_ok, dark_ok, focal {x, y}, sharpness
   The vision part (`harness photos tag --only-missing`, one call per photo
   through harness/asset_describe.py) adds `shot` (SHOTS below) and
   `quality` (1-5). `model` is copied from the cycle 75 product decision
   (tags.product), never guessed again by the look call: that decision has
   folder and verify rules a second call would bypass.
   `image_tags.shot` is a new vocabulary. The old `tags.shot` stays as it is;
   until the vision tagger has run, the resolver reads it through LEGACY_SHOT.

2. Slots with requirements. A look declares image slots (library.json
   `image_slots`, or SHIPPED_LOOK_SLOTS below for the cartridge's own looks):
     {"name": "hero", "want": {"shot": ["hero-wide", "room"], "mood": "any",
      "fullbleed_ok": true, "aspect_min": 1.3}, "n": 1,
      "fallback": ["detail-macro", "room"], "crop": "16:9"}
   fill_slots() returns ordered picks with scores and reasons: never the same
   photo twice on one page, the run's product model first, real photos over
   AI-generated ones, never an excluded photo, no retired-mark photo above the
   fold, down the declared fallback chain and finally to the product cut-out.
   A `bind` slot ("bind": "reasons[].image") takes what the page already has
   (the cycle 75/79 matchers chose it) and records it: that is how the shipped
   looks go through the resolver with identical results.

3. Focal crops. render.render_image_slot(asset, crop="16:9") cuts the window
   round the photo's focal point (crop_window) and writes the variants with
   render.generate_image_variants, cached by source sha + crop. "pill" is an
   11:5 window; its round ends are CSS (border-radius), not pixels.
"""
import hashlib
import io
import json
import math
import re
from pathlib import Path

from PIL import Image, ImageFilter, ImageStat

FIELD = "image_tags"
MEASURE_VERSION = "pillow-v1"

SHOTS = ("hero-wide", "room", "detail-macro", "exterior", "lifestyle-person", "cutout")
# Shots a slot may ask for that are not photo tags: they name the source.
SOURCE_SHOTS = ("ad-still", "storefront")
ALL_SHOTS = SHOTS + SOURCE_SHOTS
MOODS = ("bright", "warm", "dark")
ORIENTATIONS = ("landscape", "portrait", "square")

# The cycle 75 vocabulary (photo_library.SHOTS) read in the new one, until the
# vision tagger has set image_tags.shot. "lifestyle" there is the cabin in a
# real room or yard with no person; "ad-still-with-text" is never offered.
LEGACY_SHOT = {
    "exterior": "exterior",
    "interior": "room",
    "detail": "detail-macro",
    "lifestyle": "room",
    "people-in-use": "lifestyle-person",
}

# Measured thresholds (calibrated on the first tenant's 66-photo library,
# docs/FIXLOG.md cycle 93). Luminance is the mean of Pillow's "L" channel,
# 0-255. Wood gives almost every photo there a red cast (mean red - blue
# 15-115), so the cast decides only in the middle band: a white studio shot
# (luminance 170-207) is bright, an amber-lit cabin at 150-170 is warm.
DARK_MAX_LUMA = 85
BRIGHT_MIN_LUMA = 150
ALWAYS_BRIGHT_LUMA = 175
WARM_MIN_CAST = 50         # mean red minus mean blue
DARK_OK_MAX_LUMA = 130
WHITE_EDGE_MIN_CHANNEL = 235
WHITE_EDGE_MIN_SHARE = 0.6
FULLBLEED_MIN_WIDTH = 1600
HERO_STILL_MIN_SHORT = 720  # cycle 97: an ad still's short side for a full-bleed hero
HERO_WIDE_MIN_ASPECT = 1.3
MEASURE_EDGE = 256
CUTOUT_MAX_USES = 3        # same as photo_library.CUTOUT_MAX_USES

# Crops a slot (or a template) may ask for: name -> (w, h).
CROPS = {"16:9": (16, 9), "3:2": (3, 2), "4:3": (4, 3), "1:1": (1, 1), "4:5": (4, 5), "3:4": (3, 4),
         "2:3": (2, 3), "pill": (11, 5)}

WANT_KEYS = ("shot", "mood", "fullbleed_ok", "dark_ok", "aspect_min", "aspect_max", "orientation", "model",
             "min_quality", "product_still")
SLOT_KEYS = ("name", "want", "n", "fallback", "crop", "above_fold", "bind", "last_resort")

# Cycle 101 (owner grading of 21 real pages, 2026-10-09): the order every
# slot ranks the pictures it accepts -- hero, first-screen, item and essay
# slots alike. Most pages led with the studio cut-out or a person photo while
# tagged room and exterior photos went unused. The tier is the FIRST
# sort key in fill_slots; the score (the page's model, real over AI, quality,
# blur, clutter, crop loss, the slot's own shot order) only orders pictures
# inside one tier. Room and exterior shots (and the wide "hero-wide" view of
# them) first, then people in use, then detail macros, then the storefront's
# product images, and the studio cut-out last: a fallback, never a first
# choice when a tagged photo fits. An ad still ranks with the room shots when
# it is a product still (product_still: no face, short side >= 720px) and
# with the people otherwise. A slot's fallback stages and its last-resort
# cut-out still come after its `want` stage.
SHOT_TIERS = (
    ("hero-wide", "room", "exterior"),
    ("lifestyle-person",),
    ("detail-macro",),
    ("storefront",),
    ("cutout",),
)
SHOT_TIER = {shot: tier for tier, shots in enumerate(SHOT_TIERS) for shot in shots}


# ---------------------------------------------------------------------------
# 1. Deterministic measurements
# ---------------------------------------------------------------------------

def _rgb(im):
    """RGB copy; transparent pixels become white (render._flatten_on_white)."""
    if im.mode in ("RGBA", "LA") or (im.mode == "P" and "transparency" in im.info):
        rgba = im.convert("RGBA")
        bg = Image.new("RGB", rgba.size, (255, 255, 255))
        bg.paste(rgba, mask=rgba.getchannel("A"))
        return bg
    return im.convert("RGB")


def laplacian_variance(grey):
    """Sharpness: the variance of a 3x3 Laplacian of an "L" image. Offset 128
    keeps the negative half of the response inside 0-255."""
    lap = grey.filter(ImageFilter.Kernel((3, 3), [0, 1, 0, 1, -4, 1, 0, 1, 0], scale=1, offset=128))
    w, h = lap.size
    if w > 4 and h > 4:
        lap = lap.crop((1, 1, w - 1, h - 1))   # the filter's own border is not detail
    return round(ImageStat.Stat(lap).var[0], 1)


def white_edge_share(rgb):
    """Share (0-1) of the pixels in a strip round the four edges whose every
    channel is >= WHITE_EDGE_MIN_CHANNEL: a studio shot on white scores near 1."""
    w, h = rgb.size
    strip = max(2, round(min(w, h) * 0.04))
    boxes = [(0, 0, w, strip), (0, h - strip, w, h), (0, strip, strip, h - strip), (w - strip, strip, w, h - strip)]
    white = total = 0
    for box in boxes:
        band = rgb.crop(box)
        # white when all three channels pass: the darkest channel per pixel
        darkest = Image.eval(band, lambda v: 255 if v >= WHITE_EDGE_MIN_CHANNEL else 0).convert("L", (1 / 3, 1 / 3, 1 / 3, 0))
        white += sum(1 for v in darkest.tobytes() if v >= 254)
        total += band.width * band.height
    return round(white / total, 3) if total else 0.0


def focal_point(grey):
    """{x, y} in 0-1: the centre of mass of edge density (where the detail
    is), pulled half way to the centre so a busy corner never wins outright,
    clamped to 0.15-0.85. Centre when the frame has almost no edges."""
    edges = grey.filter(ImageFilter.FIND_EDGES)
    w, h = edges.size
    if w > 6 and h > 6:
        edges = edges.crop((2, 2, w - 2, h - 2))
    w, h = edges.size
    data = edges.tobytes()
    total = sx = sy = 0
    for i, v in enumerate(data):
        if v < 24:          # noise floor
            continue
        total += v
        sx += v * (i % w)
        sy += v * (i // w)
    if total < 255 * 20 or w < 2 or h < 2:
        return {"x": 0.5, "y": 0.5}

    def pull(v):
        return round(min(0.85, max(0.15, 0.5 + (v - 0.5) * 0.5)), 2)

    return {"x": pull(sx / total / (w - 1)), "y": pull(sy / total / (h - 1))}


def measure_image(im):
    """The deterministic image_tags part of one image (a PIL image)."""
    rgb = _rgb(im)
    width, height = rgb.size
    small = rgb.copy()
    small.thumbnail((MEASURE_EDGE, MEASURE_EDGE), Image.LANCZOS)
    grey = small.convert("L")
    luma = round(ImageStat.Stat(grey).mean[0], 1)
    r, _g, b = ImageStat.Stat(small).mean
    warmth = round(r - b, 1)
    white = white_edge_share(small)
    white_edge = white >= WHITE_EDGE_MIN_SHARE
    aspect = round(width / height, 3) if height else 0.0
    if aspect > 1.05:
        orientation = "landscape"
    elif aspect < 0.95:
        orientation = "portrait"
    else:
        orientation = "square"
    if luma < DARK_MAX_LUMA:
        mood = "dark"
    elif luma >= ALWAYS_BRIGHT_LUMA or (luma >= BRIGHT_MIN_LUMA and warmth < WARM_MIN_CAST):
        mood = "bright"
    else:
        mood = "warm"
    return {
        "orientation": orientation,
        "aspect": aspect,
        "mood": mood,
        "luminance": luma,
        "warmth": warmth,
        "white_edge": white_edge,
        "white_edge_share": white,
        "fullbleed_ok": width >= FULLBLEED_MIN_WIDTH and not white_edge,
        "dark_ok": (not white_edge) and luma < DARK_OK_MAX_LUMA,
        "focal": focal_point(grey),
        "sharpness": laplacian_variance(grey),
        "measured": MEASURE_VERSION,
    }


def measure_file(path):
    with Image.open(path) as im:
        im.load()
        return measure_image(im)


def merge_image_tags(photo, measured):
    """`photo`'s new image_tags: the measured part replaced, the vision part
    (shot, quality, a vision focal) kept, model from the product decision.
    Cycle 96: what a person set on the Photos page (shot_source, mood_source
    or focal_source "person") is kept too."""
    old = dict(photo.get(FIELD) or {})
    new = dict(measured)
    for key in ("shot", "quality", "tagged_by", "shot_source"):
        if old.get(key) is not None:
            new[key] = old[key]
    if old.get("mood_source") == "person" and old.get("mood") in MOODS:
        new["mood"], new["mood_source"] = old["mood"], "person"
    if old.get("focal_source") in ("vision", "person") and isinstance(old.get("focal"), dict):
        new["focal"], new["focal_source"] = old["focal"], old["focal_source"]
    else:
        new["focal_source"] = "edges"
    product = (photo.get("tags") or {}).get("product")
    new["model"] = product if product and product != "unknown" else None
    new.setdefault("shot", None)
    new.setdefault("quality", None)
    return new


def measure_manifest(manifest, files_dir, *, only_missing=False):
    """Measures every photo whose derivative is in `files_dir`; returns
    (new manifest dict, {"measured", "missing_file", "kept"}). Pure: writes
    nothing (the caller saves)."""
    files_dir = Path(files_dir)
    out = json.loads(json.dumps(manifest))
    counts = {"measured": 0, "missing_file": 0, "kept": 0}
    for photo in out.get("photos", []):
        if only_missing and (photo.get(FIELD) or {}).get("measured") == MEASURE_VERSION:
            counts["kept"] += 1
            continue
        path = files_dir / photo["file"]
        if not path.exists():
            counts["missing_file"] += 1
            continue
        photo[FIELD] = merge_image_tags(photo, measure_file(path))
        counts["measured"] += 1
    return out, counts


def validate_image_tags(it):
    """Problems with one photo's image_tags ([] when valid). The resolver
    treats an invalid object as missing; it never disables the library."""
    if not isinstance(it, dict):
        return ["image_tags is not an object"]
    errs = []
    if it.get("orientation") not in ORIENTATIONS:
        errs.append("orientation")
    if it.get("mood") not in MOODS:
        errs.append("mood")
    if not isinstance(it.get("aspect"), (int, float)) or it["aspect"] <= 0:
        errs.append("aspect")
    for key in ("fullbleed_ok", "dark_ok"):
        if not isinstance(it.get(key), bool):
            errs.append(key)
    focal = it.get("focal")
    if not (isinstance(focal, dict) and all(isinstance(focal.get(k), (int, float)) and 0 <= focal[k] <= 1
                                            for k in ("x", "y"))):
        errs.append("focal")
    if it.get("shot") is not None and it["shot"] not in SHOTS:
        errs.append("shot")
    q = it.get("quality")
    if q is not None and (not isinstance(q, int) or isinstance(q, bool) or not 1 <= q <= 5):
        errs.append("quality")
    return errs


def needs_vision(photo):
    """True when the vision part of image_tags (shot, quality) is missing."""
    it = photo.get(FIELD) or {}
    return it.get("shot") is None or it.get("quality") is None


# ---------------------------------------------------------------------------
# Audit: coverage per shot and mood, and the gaps
# ---------------------------------------------------------------------------

AUDIT_WANTS = (
    # (label, shot, mood or None, extra test) -- what richer looks ask for
    ("full-bleed hero", "hero-wide", None, "fullbleed_ok"),
    ("dark full-bleed hero", "hero-wide", "dark", "fullbleed_ok"),
    ("dark detail-macro", "detail-macro", "dark", None),
    ("bright detail-macro", "detail-macro", "bright", None),
    ("dark room", "room", "dark", None),
    ("bright room", "room", "bright", None),
    ("dark exterior", "exterior", "dark", None),
    ("lifestyle-person", "lifestyle-person", None, None),
    ("dark lifestyle-person", "lifestyle-person", "dark", None),
)


def effective_shot(photo):
    """(shot, source): image_tags.shot when the vision tagger set it, else the
    cycle 75 tags.shot read through LEGACY_SHOT ("legacy"), else (None, None)."""
    it = photo.get(FIELD) or {}
    if it.get("shot") in SHOTS:
        return it["shot"], "vision"
    legacy = LEGACY_SHOT.get((photo.get("tags") or {}).get("shot"))
    return (legacy, "legacy") if legacy else (None, None)


def photo_shots(photo):
    """Every shot a slot may find this photo under: its own, plus
    "hero-wide" for a legacy room or exterior photo that is wide and
    full-bleed (until the vision tagger says what it is)."""
    shot, source = effective_shot(photo)
    if shot is None:
        return set()
    shots = {shot}
    it = photo.get(FIELD) or {}
    if source == "legacy" and shot in ("room", "exterior") and it.get("fullbleed_ok") \
            and (it.get("aspect") or 0) >= HERO_WIDE_MIN_ASPECT:
        shots.add("hero-wide")
    return shots


def audit(manifest, *, files_dir=None):
    """{"total", "usable", "measured", "vision_tagged", "by_shot": {shot:
    {mood: n}}, "flags": {...}, "gaps": [str]} over the usable photos (not
    excluded, no text overlay). `files_dir`: photos whose derivative is
    missing there are counted as missing, not usable."""
    from .photo_library import _usable

    photos = manifest.get("photos", [])
    by_shot = {s: {m: 0 for m in MOODS + ("unmeasured",)} for s in SHOTS}
    flags = {"fullbleed_ok": 0, "dark_ok": 0, "white_edge": 0, "old_brand": 0, "ai_generated": 0,
             "landscape": 0, "portrait": 0, "square": 0, "legacy_shot": 0, "no_shot": 0}
    usable = measured = tagged = missing = 0
    rows = []
    for p in photos:
        if not _usable(p):
            continue
        if files_dir is not None and not (Path(files_dir) / p["file"]).exists():
            missing += 1
            continue
        usable += 1
        it = p.get(FIELD) or {}
        ok = not validate_image_tags(it) if it else False
        measured += ok
        tagged += not needs_vision(p)
        shot, source = effective_shot(p)
        if shot is None:
            flags["no_shot"] += 1
            continue
        flags["legacy_shot"] += source == "legacy"
        mood = it.get("mood") if ok else "unmeasured"
        for s in photo_shots(p):
            by_shot[s][mood] += 1
        for key in ("fullbleed_ok", "dark_ok", "white_edge"):
            flags[key] += bool(it.get(key))
        if it.get("orientation") in ORIENTATIONS:
            flags[it["orientation"]] += 1
        flags["old_brand"] += bool((p.get("tags") or {}).get("old_logo_visible"))
        flags["ai_generated"] += bool(p.get("ai_generated"))
        rows.append((p, it, photo_shots(p)))
    gaps, wants = [], []
    for label, shot, mood, extra in AUDIT_WANTS:
        n = sum(1 for _p, it, shots in rows
                if shot in shots and (mood is None or it.get("mood") == mood) and (extra is None or it.get(extra)))
        wants.append({"label": label, "n": n})
        if n == 0:
            gaps.append(f"no {label}")
        elif n < 3:
            gaps.append(f"only {n} {label}")
    return {"total": len(photos), "usable": usable, "missing_file": missing, "measured": measured,
            "vision_tagged": tagged, "by_shot": by_shot, "flags": flags, "gaps": gaps, "wants": wants}


def audit_lines(report):
    """The `harness photos audit` printout."""
    lines = [
        f"photos: {report['total']} in manifest, {report['usable']} usable"
        + (f", {report['missing_file']} without a file here" if report.get("missing_file") else "")
        + f", {report['measured']} measured, {report['vision_tagged']} vision-tagged (shot + quality)",
        "",
        "shot              " + "".join(f"{m:>11}" for m in MOODS + ("unmeasured",)) + "      total",
    ]
    for shot, moods in report["by_shot"].items():
        total = sum(moods.values())
        lines.append(f"{shot:<18}" + "".join(f"{moods[m]:>11}" for m in MOODS + ("unmeasured",)) + f"{total:>11}")
    f = report["flags"]
    lines += [
        "",
        f"full-bleed ok {f['fullbleed_ok']}, dark-page ok {f['dark_ok']}, white studio edge {f['white_edge']}, "
        f"landscape {f['landscape']} / portrait {f['portrait']} / square {f['square']}",
        f"old mark {f['old_brand']}, AI-generated {f['ai_generated']}, shot from cycle 75 tags {f['legacy_shot']}, "
        f"no shot {f['no_shot']}",
        "",
        "gaps:" if report["gaps"] else "gaps: none",
    ]
    lines += [f"  - {g}" for g in report["gaps"]]
    return lines


# ---------------------------------------------------------------------------
# 2. Candidates and the slot resolver
# ---------------------------------------------------------------------------

def _excluded_review_ids(tenant):
    try:
        from . import asset_review

        return asset_review.excluded_ids(asset_review.load_asset_review(tenant.brand_dir))
    except Exception:
        return set()


def _library_candidates(tenant, facts_pack, manifest, *, allow_ai_renders):
    from . import photo_library as pl

    product = pl.page_model_slug(facts_pack)
    verified = pl._verified_texts(tenant)
    own, agnostic = pl.photos_for_product(
        manifest, product, tenant=tenant,
        feature_ok=lambda f: pl.model_has_feature(tenant, product, f, verified=verified),
    )
    excluded = _excluded_review_ids(tenant)
    out = []
    for p in own + agnostic:
        if p["id"] in excluded:
            continue
        if p.get("ai_generated") and not allow_ai_renders:
            continue
        it = p.get(FIELD) or {}
        if it and validate_image_tags(it):
            it = {}
        shot, source = effective_shot(p)
        if shot is None:
            continue
        tags = p["tags"]
        out.append({
            "id": p["id"], "source": "library", "shot": shot, "shot_source": source, "shots": photo_shots(p),
            "mood": it.get("mood"), "aspect": it.get("aspect") or round(p["width"] / p["height"], 3),
            "orientation": it.get("orientation"), "fullbleed_ok": bool(it.get("fullbleed_ok")),
            "dark_ok": bool(it.get("dark_ok")), "focal": it.get("focal") or {"x": 0.5, "y": 0.5},
            "long_side": max(p["width"], p["height"]), "white_edge": it.get("white_edge"),
            "model": it.get("model") or (tags["product"] if tags["product"] != pl.UNKNOWN else None),
            "quality": it.get("quality"), "ai_generated": bool(p.get("ai_generated")),
            "old_brand": bool(tags.get("old_logo_visible")), "blurry": bool(tags.get("blurry")),
            "cluttered": bool(tags.get("cluttered")),
            "asset": dict(pl.facts_pack_asset(p), local_path=str(pl.local_file(tenant, p))),
        })
    return out


def _cutout_candidate(tenant, facts_pack):
    from . import cutouts

    asset = cutouts.cutout_for_product(tenant, facts_pack.get("product"))
    if asset is None:
        return None
    try:
        with Image.open(asset["local_path"]) as im:
            aspect = round(im.width / im.height, 3)
    except Exception:
        aspect = 1.0
    return {"id": asset["id"], "source": "cutout", "shot": "cutout", "shot_source": "file", "shots": {"cutout"},
            "mood": "bright", "aspect": aspect, "orientation": None, "fullbleed_ok": False, "dark_ok": False,
            "focal": {"x": 0.5, "y": 0.5}, "model": asset.get("model"), "quality": 4, "ai_generated": False,
            "old_brand": False, "blurry": False, "cluttered": False, "asset": asset}


def ad_still_candidates(record):
    """Candidates from the run's ad-frame record (ad_frames.load): the
    first-screen frame (face-checked when the vision call ran) and the
    extra stills a video gave (ad_frames STILLS)."""
    from . import ad_frames

    if not record:
        return []
    out = []
    focal = record.get("face_center")
    face_focal = {"x": focal[0], "y": focal[1]} if focal else {"x": 0.5, "y": 0.33}
    if record.get("frame_path"):
        w, h = record.get("width") or 4, record.get("height") or 5
        out.append({"id": ad_frames.FRAME_ID, "source": "ad-still", "shot": "ad-still", "shot_source": "ad-frame",
                    "shots": {"ad-still"}, "mood": record.get("mood"), "aspect": round(w / h, 3),
                    "orientation": None, "fullbleed_ok": w >= FULLBLEED_MIN_WIDTH, "dark_ok": False,
                    "focal": face_focal, "model": None, "quality": None, "ai_generated": False,
                    "old_brand": False, "blurry": False, "cluttered": False,
                    "face_checked": bool(record.get("face_checked")), "still_score": None,
                    # the first-screen frame is chosen for the creator's face
                    # (unchecked by the heuristic method): never a product shot
                    "face": True if record.get("face_checked") else None, "short_side": min(w, h),
                    "asset": {"id": ad_frames.FRAME_ID, "kind": "ad-frame", "local_path": record["frame_path"],
                              "url": f"{ad_frames.DIRNAME}/{ad_frames.FRAME_FILE}", "alt": "A still from the ad video"}})
    for i, still in enumerate(record.get("stills") or [], 1):
        if not still.get("path"):
            continue
        sid = f"{ad_frames.STILL_ID_PREFIX}{i}"
        w, h = still.get("width") or 4, still.get("height") or 5
        out.append({"id": sid, "source": "ad-still", "shot": "ad-still", "shot_source": "ad-frame",
                    "shots": {"ad-still"}, "mood": still.get("mood"), "aspect": round(w / h, 3),
                    "orientation": None, "fullbleed_ok": w >= FULLBLEED_MIN_WIDTH, "dark_ok": False,
                    "focal": {"x": 0.5, "y": 0.4}, "model": None, "quality": None, "ai_generated": False,
                    "old_brand": False, "blurry": False, "cluttered": False, "face_checked": False,
                    "still_score": still.get("score"), "still_rank": i,
                    "face": still.get("face"), "short_side": min(w, h),
                    "asset": {"id": sid, "kind": "ad-frame", "local_path": still["path"],
                              "url": f"{ad_frames.DIRNAME}/{still['file']}", "alt": "A still from the ad video"}})
    return out


def _storefront_candidates(facts_pack):
    slug = (facts_pack.get("product") or {}).get("slug") or ""
    out = []
    for a in facts_pack.get("assets") or []:
        if slug and str(a.get("id", "")).startswith(f"asset-{slug}-"):
            out.append({"id": a["id"], "source": "storefront", "shot": "storefront", "shot_source": "storefront",
                        "shots": {"storefront"}, "mood": None, "aspect": None, "orientation": None,
                        "fullbleed_ok": False, "dark_ok": False, "focal": {"x": 0.5, "y": 0.5},
                        "model": None, "quality": None, "ai_generated": bool(a.get("ai_generated")),
                        "old_brand": False, "blurry": False, "cluttered": False, "asset": dict(a)})
    return out


def candidates(facts_pack, tenant, *, ad_frame=None, manifest=None, allow_ai_renders=None):
    """Every image a slot on this page may use: library photos of the page's
    product (+ model-agnostic ones, the cycle 75 product rules), the product
    cut-out, the run's ad stills, the product's storefront images."""
    from . import photo_library as pl

    if allow_ai_renders is None:
        allow_ai_renders = bool((getattr(tenant, "claims_config", None) or {}).get("allow_ai_renders"))
    if manifest is None:
        manifest = pl.load_manifest(tenant)
    out = _library_candidates(tenant, facts_pack, manifest, allow_ai_renders=allow_ai_renders)
    cut = _cutout_candidate(tenant, facts_pack)
    if cut is not None:
        out.append(cut)
    out += ad_still_candidates(ad_frame)
    out += [c for c in _storefront_candidates(facts_pack) if allow_ai_renders or not c["ai_generated"]]
    return out


def validate_slot(slot):
    """Problems with one slot declaration ([] when valid)."""
    if not isinstance(slot, dict):
        return ["slot is not an object"]
    errs = [f"unknown key {k!r}" for k in slot if k not in SLOT_KEYS]
    if not isinstance(slot.get("name"), str) or not slot["name"]:
        errs.append("name must be a non-empty string")
    if "bind" in slot:
        if not isinstance(slot["bind"], str):
            errs.append("bind must be a page path")
        return errs
    want = slot.get("want")
    if not isinstance(want, dict):
        return errs + ["want must be an object (or use bind)"]
    errs += [f"unknown want key {k!r}" for k in want if k not in WANT_KEYS]
    shots = want.get("shot", "any")
    if shots != "any":
        shots = [shots] if isinstance(shots, str) else shots
        if not isinstance(shots, list) or any(s not in ALL_SHOTS for s in shots):
            errs.append(f"want.shot must be 'any' or from {list(ALL_SHOTS)}")
    mood = want.get("mood", "any")
    if mood != "any":
        moods = [mood] if isinstance(mood, str) else mood
        if not isinstance(moods, list) or any(m not in MOODS for m in moods):
            errs.append(f"want.mood must be 'any' or from {list(MOODS)}")
    n = slot.get("n", 1)
    if not isinstance(n, int) or isinstance(n, bool) or not 1 <= n <= 12:
        errs.append("n must be 1-12")
    fb = slot.get("fallback", [])
    if not isinstance(fb, list) or any(s not in ALL_SHOTS for s in fb):
        errs.append("fallback must be a list of shots")
    if slot.get("crop") is not None and slot["crop"] not in CROPS:
        errs.append(f"crop must be one of {list(CROPS)}")
    if slot.get("last_resort", "cutout") not in ("cutout", None):
        errs.append("last_resort must be 'cutout' or null")
    return errs


def _as_list(value):
    if value in (None, "any"):
        return None
    return [value] if isinstance(value, str) else list(value)


def _seed_jitter(seed, cid):
    """A tie-break in [0, 0.01): stable for one seed, different across seeds."""
    h = hashlib.sha256(f"{seed}:{cid}".encode()).hexdigest()
    return int(h[:6], 16) / 0xFFFFFF / 100


# cycle 104b: fill_slots rotates its pick by the run seed across the top
# ROTATE_TOP library photos of the winning tier whose quality is within
# ROTATE_QUALITY of the best (one point in, two points out); the crop fit and
# the portrait rank do not split them. Cycle 104's 0.5 score window was too
# narrow: one landscape people photo led 17 of 28 re-rendered first screens.
ROTATE_TOP = 4
ROTATE_QUALITY = 1.5


def _quality(c):
    """The vision quality 1-5; an unrated photo counts 3, as in score_candidate."""
    return c["quality"] if c["quality"] is not None else 3


def _seed_int(seed):
    """The seed as an int: an int seed as is, else a stable hash of its text
    (render passes the run folder's name)."""
    if isinstance(seed, int):
        return seed
    return int(hashlib.sha256(str(seed).encode()).hexdigest()[:8], 16)


def product_still(c):
    """Cycle 97: an ad still that may fill a full-bleed hero on its own merit:
    the ad_frames vision call saw NO face in it (a product or room shot, not
    the talking head) and its short side is at least HERO_STILL_MIN_SHORT px.
    A face still or a low-resolution one loses to a matching library photo."""
    return c.get("face") is False and (c.get("short_side") or 0) >= HERO_STILL_MIN_SHORT


def shot_tier(c, shots=None):
    """The candidate's place in SHOT_TIERS (0 first): its best shot among the
    slot's `shots` (all of its shots for a slot that takes any)."""
    if c["source"] == "ad-still":
        return SHOT_TIER["room"] if product_still(c) else SHOT_TIER["lifestyle-person"]
    own = c["shots"] & set(shots) if shots else c["shots"]
    return min((SHOT_TIER.get(s, len(SHOT_TIERS)) for s in own), default=len(SHOT_TIERS))


def portrait_fullbleed(c):
    """Cycle 101 (owner decision 2026-10-09): a room or exterior photo that is
    not fullbleed_ok only because it is portrait (long side >= 1600px, no
    white studio edge, e.g. 1067 x 1600) may fill a full-bleed hero; the
    hero's cover crop on the focal point handles the shape. It ranks after
    the landscape full-bleed photos of its tier (fill_slots)."""
    return (not c["fullbleed_ok"] and bool(c["shots"] & {"room", "exterior"})
            and (c.get("long_side") or 0) >= FULLBLEED_MIN_WIDTH and c.get("white_edge") is False)


def _passes(c, want, *, shots, strict, above_fold, prefer_model):
    """(ok, why-not). `strict` False on a fallback stage: the frame filters
    (full-bleed, aspect, orientation, quality) are relaxed; mood is kept."""
    if shots is not None and not (c["shots"] & set(shots)):
        return False, "shot"
    if c["model"] and prefer_model and c["model"] != prefer_model:
        return False, "another model"
    if want.get("model") == "own" and c["model"] != prefer_model:
        return False, "not the page's model"
    if above_fold and c["old_brand"]:
        return False, "retired mark above the fold"
    moods = _as_list(want.get("mood"))
    if moods is not None and c["source"] != "cutout" and c["mood"] not in moods:
        return False, "mood"
    if want.get("dark_ok") and not c["dark_ok"]:
        return False, "not dark-page ok"
    if want.get("product_still") and c["source"] == "ad-still" and not product_still(c):
        return False, "ad still is a face or too small for a full-bleed hero"
    if not strict:
        return True, ""
    if want.get("fullbleed_ok") and not (c["fullbleed_ok"] or portrait_fullbleed(c)):
        return False, "not full-bleed"
    if want.get("aspect_min") is not None and (c["aspect"] or 0) < want["aspect_min"]:
        return False, "aspect"
    if want.get("aspect_max") is not None and (c["aspect"] or 99) > want["aspect_max"]:
        return False, "aspect"
    orient = _as_list(want.get("orientation"))
    if orient is not None and c["orientation"] not in orient:
        return False, "orientation"
    if want.get("min_quality") is not None and (c["quality"] or 3) < want["min_quality"]:
        return False, "quality"
    return True, ""


def score_candidate(c, *, shots, prefer_model, crop=None, seed=0):
    """(score, reasons) of one candidate that passed the filters."""
    score, reasons = 10.0, []
    if shots:
        hit = [i for i, s in enumerate(shots) if s in c["shots"]]
        idx = hit[0] if hit else len(shots)
        score += max(0, 4 - idx)
        reasons.append(f"shot {shots[idx] if hit else c['shot']}" + (f" ({c['shot_source']})" if c.get("shot_source") == "legacy" else ""))
    if c["model"] and c["model"] == prefer_model:
        score += 5
        reasons.append("the page's model")
    elif c["model"] is None and c["source"] == "library":
        score += 1
        reasons.append("model-agnostic")
    if c["ai_generated"]:
        score -= 3
        reasons.append("AI-generated")
    else:
        score += 2
    q = c["quality"] if c["quality"] is not None else 3
    score += (q - 3) * 1.5
    if c["quality"] is not None:
        reasons.append(f"quality {q}")
    if c["blurry"]:
        score -= 3
        reasons.append("blurry")
    if c["cluttered"]:
        score -= 2
        reasons.append("cluttered")
    if c["old_brand"]:
        score -= 4
        reasons.append("retired mark (below the fold)")
    if c.get("face_checked"):
        score += 5
        reasons.append("face-checked first-screen still")
    if c.get("still_score") is not None:
        # ad_frames.still_score is about 5-15; the record's own order breaks a
        # tie (it is above the seeded jitter, which stays under 0.01)
        score += c["still_score"] / 5 - 0.02 * c.get("still_rank", 0)
    if crop in CROPS and c["aspect"]:
        cw, ch = CROPS[crop]
        loss = abs(math.log(c["aspect"] / (cw / ch)))
        score -= round(loss * 2, 2)
        if loss > 0.4:
            reasons.append(f"heavy crop to {crop}")
    score += _seed_jitter(seed, c["id"])
    return round(score, 3), reasons


def _bound_nodes(page, path):
    """[(path, node)] for a bind path: "hero", "hero.hero_image",
    "room_photo", "reasons[].image" (every item)."""
    m = re.match(r"^([a-z_]+)\[\]\.([a-z_]+)$", path)
    if m:
        items = page.get(m.group(1)) or []
        return [(f"{m.group(1)}[{i}].{m.group(2)}", it.get(m.group(2))) for i, it in enumerate(items)
                if isinstance(it, dict) and isinstance(it.get(m.group(2)), dict)]
    node = page
    for key in path.split("."):
        node = node.get(key) if isinstance(node, dict) else None
    return [(path, node)] if isinstance(node, dict) else []


def fill_slots(slots, facts_pack, tenant, *, ad_frames=None, prefer_model=None, seed=0, page=None, used=(),
               manifest=None, allow_ai_renders=None, pool=None):
    """The resolver. `slots`: slot declarations (validate_slot); `ad_frames`:
    the run's ad-frame record (ad_frames.load) or None; `prefer_model`: the
    run's product model slug (default: the facts_pack product's); `seed`:
    tie-break; `page`: for bind slots; `used`: ids already on the page.

    Returns [{"slot", "bind", "wanted", "picks": [{"id", "score", "reasons",
    "via", "asset"}], "short": k, "notes": [...]}] in slot order. A photo is
    used once per page; the cut-out may repeat up to CUTOUT_MAX_USES. Inside
    each stage the picks are ranked by SHOT_TIERS first (cycle 101), then by
    score."""
    from . import photo_library as pl

    prefer_model = prefer_model or pl.page_model_slug(facts_pack) or None
    if pool is None:
        pool = candidates(facts_pack, tenant, ad_frame=ad_frames, manifest=manifest,
                          allow_ai_renders=allow_ai_renders)
    by_id = {c["id"]: c for c in pool}
    taken = set(used)
    cutout_uses = sum(1 for i in used if str(i).startswith("asset-cutout-"))
    results = []
    for index, slot in enumerate(slots):
        errs = validate_slot(slot)
        if errs:
            results.append({"slot": slot.get("name") if isinstance(slot, dict) else None, "bind": None,
                            "wanted": 0, "picks": [], "short": 0, "notes": [f"invalid slot: {e}" for e in errs]})
            continue
        if "bind" in slot:
            picks = []
            for path, node in _bound_nodes(page or {}, slot["bind"]):
                aid = node.get("asset_id")
                if not aid:
                    continue
                c = by_id.get(aid)
                why = [f"bound to page {path}"]
                if c is not None:
                    why.append(c["shot"] if c["source"] == c["shot"] else f"{c['source']} {c['shot']}")
                picks.append({"id": aid, "score": None, "reasons": why, "via": "bind", "path": path,
                              "asset": c["asset"] if c else None})
                taken.add(aid)
            results.append({"slot": slot["name"], "bind": slot["bind"], "wanted": len(picks), "picks": picks,
                            "short": 0, "notes": []})
            continue
        want = slot["want"]
        n = slot.get("n", 1)
        above_fold = slot.get("above_fold", index == 0 or slot["name"] == "hero")
        crop = slot.get("crop")
        stages = [(_as_list(want.get("shot", "any")), True, "want")]
        stages += [([s], False, f"fallback {s}") for s in slot.get("fallback", [])]
        picks, notes = [], []
        for shots, strict, via in stages:
            if len(picks) >= n:
                break
            scored = []
            for c in pool:
                if c["id"] in taken and not (c["source"] == "cutout" and cutout_uses < CUTOUT_MAX_USES):
                    continue
                if c["source"] == "cutout" and c["id"] in taken and shots and "cutout" not in shots:
                    continue
                ok, _why = _passes(c, want, shots=shots, strict=strict, above_fold=above_fold,
                                   prefer_model=prefer_model)
                if not ok:
                    continue
                s, reasons = score_candidate(c, shots=shots, prefer_model=prefer_model, crop=crop, seed=seed)
                scored.append((s, c, reasons))
            # cycle 101: the shot tier first (SHOT_TIERS), then a landscape
            # full-bleed photo before a portrait one (portrait_fullbleed), then
            # the score
            portrait_last = strict and bool(want.get("fullbleed_ok"))
            scored.sort(key=lambda x: (shot_tier(x[1], shots), portrait_last and portrait_fullbleed(x[1]),
                                       -x[0], x[1]["id"]))
            # cycle 104b: the top ROTATE_TOP library photos of the winning tier
            # whose quality is within ROTATE_QUALITY of the tier's best rotate by
            # the run seed, so runs do not all lead with one photo; a better
            # tier always wins, a given seed gives the same pick, seed 0 leads
            # with the best-ranked of them. Library photos only (an ad still keeps the
            # ad_frames record's order); blurry, AI and retired-mark photos stay
            # in rank order behind
            tied = []
            if scored and scored[0][1]["source"] == "library":
                tier = shot_tier(scored[0][1], shots)
                pool_q = [x for x in scored if x[1]["source"] == "library" and shot_tier(x[1], shots) == tier
                          and not (x[1]["blurry"] or x[1]["ai_generated"] or x[1]["old_brand"])]
                best_q = max((_quality(x[1]) for x in pool_q), default=0)
                tied = [x for x in pool_q if best_q - _quality(x[1]) <= ROTATE_QUALITY][:ROTATE_TOP]
                # one order for every seed (the score without its seeded jitter),
                # so seeds 1..k land on k different photos
                tied.sort(key=lambda x: (-round(x[0] - _seed_jitter(seed, x[1]["id"]), 2), x[1]["id"]))
            if len(tied) > 1:
                k = _seed_int(seed) % len(tied)
                tied_ids = {x[1]["id"] for x in tied}
                scored = tied[k:] + tied[:k] + [x for x in scored if x[1]["id"] not in tied_ids]
                if not picks:
                    notes.append(f"{slot['name']}: picked {tied[k][1]['id']} by seed from {len(tied)} tied "
                                 f"({', '.join(x[1]['id'] for x in tied)})")
            for s, c, reasons in scored:
                if len(picks) >= n:
                    break
                if c["id"] in {p["id"] for p in picks}:
                    continue
                picks.append({"id": c["id"], "score": s, "reasons": reasons + ([] if strict else ["relaxed frame"]),
                              "via": via, "asset": c["asset"], "focal": c["focal"]})
                if c["source"] == "cutout":
                    cutout_uses += 1
                taken.add(c["id"])
            if via != "want" and picks:
                notes.append(f"{slot['name']}: {via}")
        if len(picks) < n and slot.get("last_resort", "cutout") == "cutout":
            cut = next((c for c in pool if c["source"] == "cutout"), None)
            if cut is not None and cutout_uses < CUTOUT_MAX_USES and cut["id"] not in {p["id"] for p in picks}:
                picks.append({"id": cut["id"], "score": 0.0, "reasons": ["last resort: product cut-out"],
                              "via": "last resort", "asset": cut["asset"], "focal": cut["focal"]})
                cutout_uses += 1
                taken.add(cut["id"])
                notes.append(f"{slot['name']}: last resort cut-out")
        short = max(0, n - len(picks))
        if short:
            notes.append(f"{slot['name']}: {short} of {n} not filled (nothing left that fits)")
        results.append({"slot": slot["name"], "bind": None, "wanted": n, "picks": picks, "short": short,
                        "crop": crop, "notes": notes})
    return results


# ---------------------------------------------------------------------------
# Slot declarations: shipped looks and tenant looks
# ---------------------------------------------------------------------------

# The cartridge's own looks: every image they show today, as bind slots (the
# cycle 75/79 matchers keep choosing them) plus the ad still. No template
# reads `image_slots` for these looks, so their HTML is unchanged
# (tests/test_images_cycle93.py pins it).
_AD_STILL = {"name": "ad-still", "want": {"shot": ["ad-still"]}, "n": 1, "last_resort": None,
             "above_fold": True}
SHIPPED_LOOK_SLOTS = {
    ("listicle", "open"): [dict(_AD_STILL), {"name": "hero", "bind": "hero"},
                           {"name": "items", "bind": "reasons[].image"}],
    ("landing", "open"): [dict(_AD_STILL), {"name": "hero", "bind": "hero.hero_image"},
                          {"name": "room", "bind": "room_photo"}],
    ("landing", "proof"): [dict(_AD_STILL), {"name": "hero", "bind": "hero.hero_image"},
                           {"name": "room", "bind": "room_photo"}],
}


def look_slots(cartridge_name, look, *, tenant=None, looks_dir=None):
    """The slot declarations of one look: a tenant look's library.json
    `image_slots`, else SHIPPED_LOOK_SLOTS, else []."""
    from . import looks as looks_mod

    if look and looks_mod.LOOK_SLUG_RE.match(look) and look not in looks_mod.cartridge_looks(cartridge_name):
        folder = looks_mod.imported_looks_dir(cartridge_name, tenant, looks_dir)
        path = folder / look / "library.json" if folder is not None else None
        if path is not None and path.is_file():
            try:
                declared = json.loads(path.read_text()).get("image_slots")
            except (OSError, ValueError, AttributeError):
                declared = None
            if isinstance(declared, list):
                return declared
    return [dict(s) for s in SHIPPED_LOOK_SLOTS.get((cartridge_name, look), [])]


def resolve_page_images(page, facts_pack, cartridge_name, look, *, tenant, run_dir, seed, looks_dir=None,
                        allow_ai_renders=None, log=None):
    """render_page's call: fills the look's slots after the existing matchers
    ran. Returns (image_slots {name: [asset dict]}, results) -- `results`
    for .image-selection.json "slots" and REVIEW.md. ([], {}) when the look
    declares nothing."""
    from . import ad_frames as ad_frames_mod
    from .render import collect_asset_ids

    slots = look_slots(cartridge_name, look, tenant=tenant, looks_dir=looks_dir)
    if not slots:
        return {}, []
    record = ad_frames_mod.load(run_dir)
    results = fill_slots(slots, facts_pack, tenant, ad_frames=record, seed=seed, page=page,
                         used=collect_asset_ids(page), allow_ai_renders=allow_ai_renders)
    image_slots = {}
    for r in results:
        if r["bind"] is not None or not r["slot"]:
            continue
        image_slots[r["slot"]] = [dict(p["asset"], slot_crop=r.get("crop"), slot_reasons=p["reasons"],
                                       focal=p.get("focal"))
                                  for p in r["picks"] if p.get("asset")]
    if log:
        for r in results:
            for p in r["picks"]:
                log.event("images", f"slot {r['slot']}: {p['id']} ({p['via']}; {', '.join(p['reasons'])})")
            for note in r["notes"]:
                log.event("images", note)
    return image_slots, results


def selection_record(results):
    """The JSON-safe part of fill_slots' results (no asset dicts)."""
    return [{"slot": r["slot"], "bind": r["bind"], "wanted": r["wanted"], "short": r["short"],
             "notes": r["notes"],
             "picks": [{k: p.get(k) for k in ("id", "score", "via", "reasons", "path") if p.get(k) is not None}
                       for p in r["picks"]]}
            for r in results]


def record_slots(run_dir, cartridge_name, results):
    """Writes the resolver's picks into <run_dir>/.image-selection.json under
    "slots" (same overwrite-only-this-cartridge contract as
    ground.record_image_matches)."""
    from . import ground as ground_mod

    path = Path(run_dir) / ground_mod._SELECTION_STATE_FILENAME
    data = ground_mod.load_used_asset_ids(run_dir)
    all_slots = dict(data.get("slots") or {})
    all_slots[cartridge_name] = selection_record(results)
    data["slots"] = all_slots
    path.write_text(json.dumps(data, indent=2) + "\n")
    return path


def review_lines(run_dir, selected):
    """REVIEW.md "## Images": every slot's picks and why, per page."""
    from . import ground as ground_mod

    data = ground_mod.load_used_asset_ids(run_dir)
    slots = data.get("slots") or {}
    lines = []
    for name in selected:
        for r in slots.get(name) or []:
            if not r.get("picks") and not r.get("notes"):
                continue
            for p in r.get("picks") or []:
                score = f", score {p['score']:g}" if isinstance(p.get("score"), (int, float)) else ""
                lines.append(f"- {name} / {r['slot']}: `{p['id']}` ({p.get('via')}{score}; "
                             f"{', '.join(p.get('reasons') or [])})")
            for note in r.get("notes") or []:
                lines.append(f"- {name} / {r['slot']}: {note}")
    return lines or ["- no declared image slots on these pages"]


# ---------------------------------------------------------------------------
# 3. Focal crops
# ---------------------------------------------------------------------------

def crop_window(width, height, crop, focal=None):
    """(left, top, right, bottom): the largest `crop`-ratio window inside a
    width x height image, centred as close to `focal` {x, y} as the frame
    allows."""
    cw, ch = CROPS[crop]
    target = cw / ch
    fx = (focal or {}).get("x", 0.5)
    fy = (focal or {}).get("y", 0.5)
    if width / height > target:
        w, h = round(height * target), height
    else:
        w, h = width, round(width / target)
    left = round(min(max(fx * width - w / 2, 0), width - w))
    top = round(min(max(fy * height - h / 2, 0), height - h))
    return left, top, left + w, top + h


def crop_tag(crop):
    return "pill" if crop == "pill" else crop.replace(":", "x")


def crop_asset(asset, crop, *, log=None):
    """A copy of a downloaded `asset` (render_page's assets_by_id entry, with
    "src_path") showing the `crop` window round its focal point, with its own
    variants written next to the originals. Cached: the files are named by
    the source's sha + crop + focal, so a second call (or a rerender) only
    reads. The asset unchanged when it has no source file or crop is unknown."""
    from .render import generate_image_variants

    src = asset.get("src_path")
    if crop not in CROPS or not src or not Path(src).is_file():
        return asset
    data = Path(src).read_bytes()
    focal = asset.get("focal") or {"x": 0.5, "y": 0.5}
    key = hashlib.sha256(data + f"{crop}:{focal.get('x')}:{focal.get('y')}".encode()).hexdigest()[:10]
    stem = f"{asset['id']}-{crop_tag(crop)}-{key}"
    dest = Path(src).parent
    cached = sorted(dest.glob(f"{stem}-*.jpg"), key=lambda p: int(p.stem.rsplit("-", 1)[1]))
    out = dict(asset, crop=crop)
    if cached:
        variants = [{"width": int(p.stem.rsplit("-", 1)[1]), "jpg": f"assets/{p.name}",
                     "webp": f"assets/{p.with_suffix('.webp').name}" if p.with_suffix(".webp").exists() else None}
                    for p in cached]
        with Image.open(cached[-1]) as im:
            out.update(variants=variants, width=im.width, height=im.height)
        return out
    with Image.open(io.BytesIO(data)) as im:
        im.load()
        box = crop_window(im.width, im.height, crop, focal)
        cropped = im.convert("RGB").crop(box)
        buf = io.BytesIO()
        cropped.save(buf, format="JPEG", quality=92)
    info = generate_image_variants(buf.getvalue(), dest, stem, log=log)
    if not info["variants"]:
        return asset
    out.update(variants=info["variants"], width=info["width"], height=info["height"])
    return out


# ---------------------------------------------------------------------------
# Vision part: `harness photos tag --only-missing`
# ---------------------------------------------------------------------------

def tag_missing(tenant, *, client, model, budget, log, ids=None, save_every=1):
    """Adds the vision part of image_tags (shot, quality, focal) to every
    usable library photo that lacks it -- one asset_describe.describe_one
    call per photo with look_fields=True. Measures a photo first when its
    measured part is missing. Saves the manifest after every `save_every`
    photos. Returns {"tagged", "failed", "skipped"}. Cycle 96: each answer is
    written as that photo's image_tags only, under the manifest lock, with
    a person's shot / mood / focal (Photos page) kept; `save_every` is
    kept for callers and no longer batches writes."""
    from . import asset_describe
    from . import photo_library as pl

    manifest = pl.load_manifest(tenant, log=log)
    forbidden = asset_describe.forbidden_terms_for(tenant)
    counts = {"tagged": 0, "failed": 0, "skipped": 0}
    done = 0
    for photo in manifest.get("photos", []):
        if ids is not None and photo["id"] not in ids:
            continue
        if not pl._usable(photo) or (ids is None and not needs_vision(photo)):
            counts["skipped"] += 1
            continue
        path = pl.local_file(tenant, photo)
        if not path.exists():
            counts["skipped"] += 1
            log.event("photos_tag", f"{photo['id']}: no file here, not tagged")
            continue
        if (photo.get(FIELD) or {}).get("measured") != MEASURE_VERSION:
            photo[FIELD] = merge_image_tags(photo, measure_file(path))
        product_slug = (photo.get(FIELD) or {}).get("model")
        brand = getattr(tenant, "display_name", None) or "the product"
        product = {"name": product_slug.replace("-", " ").title() if product_slug else brand, "title": brand}
        result = asset_describe.describe_one(
            {"id": photo["id"]}, product=product, forbidden_terms=forbidden, client=client, model=model,
            budget=budget, log=log, cache_dir=None, image_bytes=pl.vision_bytes(path), look_fields=True,
        )
        if result is None:
            counts["failed"] += 1
            continue
        measured = photo[FIELD]

        def store(row, measured=measured, result=result):
            old = row.get(FIELD) or {}
            it = merge_image_tags(row, measured) if old.get("measured") != MEASURE_VERSION else dict(old)
            if it.get("shot_source") != "person":
                it["shot"] = result["shot"]
            it["quality"], it["tagged_by"] = result["quality"], model
            if result.get("focal") and it.get("focal_source") != "person":
                it["focal"], it["focal_source"] = result["focal"], "vision"
            row[FIELD] = it

        try:
            it = pl.update_photo(tenant, photo["id"], store)[FIELD]
        except pl.PhotoNotFound:
            continue
        counts["tagged"] += 1
        done += 1
        log.event("photos_tag", f"{photo['id']}: {it['shot']}, quality {it['quality']}, mood {it['mood']}")
    return counts
