"""Cycle 75: the tenant photo library -- one folder of the owner's own photos,
imported once, tagged once by a vision model, and then used to pick, for
every image slot on a page, a photo of the page's own product that shows
what that slot's text talks about. See docs/IMAGES.md section 9.

Data layout (tenant data; the code and the schema are in git):

  tenants/<t>/brand/photo-library.json   the manifest: one entry per photo,
                                         with its tags (committed, like
                                         brand/assets.json)
  tenants/<t>/photo-library/<id>.jpg     web-size derivatives (gitignored,
                                         rebuilt from the source folder by
                                         `harness photos import`)

Entry shape (validate_manifest is the schema):

  {"id": "asset-photo-<sha256[:12] of the original file>",
   "file": "<id>.jpg", "source_name": <original filename>,
   "drive_id": <Drive file id or null>, "sha256": <original bytes>,
   "width": int, "height": int, "ai_generated": bool, "excluded": bool,
   "tags": {"product": <model slug>|"unknown", "product_confidence": 0..1,
            "model_agnostic": bool, "setting": indoor|outdoor|studio,
            "shot": exterior|interior|detail|lifestyle|people-in-use|
                    ad-still-with-text,
            "features_visible": [<tenant photo_library.features>],
            "has_text_overlay": bool, "people": int, "description": str,
            "blurry": bool, "cluttered": bool},
   "decision": {"vision_product", "vision_confidence", "vision_reason",
                "folder_model", "rule", "model"}}

Selection rules (facts_pack_assets / assign_page_images):
  - a page about product X is offered only photos tagged X, plus photos
    tagged model_agnostic (product "unknown" AND no sauna model visible);
  - a photo with a text overlay is never offered for a slot;
  - the product's storefront (Shopify) images stay in the pool as the
    fallback, used for a slot only once the library has no unused photo
    of X left; the older Drive indexes are dropped for X.
"""
import base64
import datetime
import hashlib
import io
import json
import os
import re
from pathlib import Path

from PIL import Image, ImageOps

from .textutil import product_name_slug, walk_page

MANIFEST_VERSION = 1
MANIFEST_FILENAME = "photo-library.json"
FILES_DIRNAME = "photo-library"
ID_PREFIX = "asset-photo-"
ID_RE = re.compile(r"^asset-photo-[0-9a-f]{12}$")
UNKNOWN = "unknown"

DERIVATIVE_MAX_EDGE = 1600
DERIVATIVE_JPEG_QUALITY = 80
IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp"}

SETTINGS = ("indoor", "outdoor", "studio")
SHOTS = ("exterior", "interior", "detail", "lifestyle", "people-in-use", "ad-still-with-text")
DEFAULT_FEATURES = (
    "red-light-panel", "control-panel", "bench", "heaters", "glass-door", "wood-grain",
    "size-in-room", "outlet-plug", "assembly", "chromotherapy", "speakers", "roof-outdoor-weather",
)

# The harness's existing asset `kind` vocabulary (render._ASSET_KIND_ALT_
# SUFFIXES, ground.HERO_CANDIDATE_KINDS) for each shot, so a library photo
# gets the same alt suffix and slot policy as any other photo.
SHOT_KIND = {
    "exterior": "photo_product",
    "interior": "interior",
    "detail": "photo_product",
    "lifestyle": "lifestyle",
    "people-in-use": "lifestyle",
    "ad-still-with-text": "still_video",
}

# Filenames are never used to decide the MODEL. They are used for one thing:
# provenance. A file the owner exported from an image generator says so in
# its name, and such a photo must be labeled a rendering (render.asset_alt).
_AI_FILENAME_RE = re.compile(r"firefly|gemini|midjourney|dall-?e|gpt-image|stable.?diffusion", re.IGNORECASE)
# Lazy, so "asset-drive-<id>___MG_1.JPG" (three underscores) keeps the
# trailing underscore out of the id.
_DRIVE_ID_RE = re.compile(r"^asset-drive-([A-Za-z0-9_-]{20,}?)__")

# Under this, a vision guess is not trusted on its own (see decide_product).
MIN_VISION_CONFIDENCE = 0.7

# Model identity is the point of the tag, so the stronger vision model is the
# default (67 photos cost about $1 with the reference block cached).
DEFAULT_TAG_MODEL = "claude-sonnet-5"


# ---------------------------------------------------------------------------
# Paths, config, loading
# ---------------------------------------------------------------------------

def manifest_path(tenant):
    return Path(tenant.brand_dir) / MANIFEST_FILENAME


# Where the derivatives are, when not in the tenant tree: an operator can
# point a deploy at one shared copy; the test suite points it at an empty
# folder so whether a checkout happens to have the (gitignored) files never
# changes another test (tests/conftest.py).
FILES_DIR_ENV = "HARNESS_PHOTO_LIBRARY_DIR"


def files_dir(tenant):
    override = os.environ.get(FILES_DIR_ENV)
    if override:
        return Path(override) / Path(tenant.root).name
    return Path(tenant.root) / FILES_DIRNAME


def features_for(tenant):
    """The fixed features_visible vocabulary (tenant.yaml photo_library.features)."""
    return tuple((tenant.get("photo_library.features") if tenant else None) or DEFAULT_FEATURES)


def topic_keywords_for(tenant):
    """{topic: [keyword, ...]} (tenant.yaml photo_library.topic_keywords). A
    topic is a feature name, "shot:<shot>" or "setting:<setting>"."""
    return dict((tenant.get("photo_library.topic_keywords") if tenant else None) or {})


def model_slugs(tenant):
    """Every catalog model as product_name_slug(name), e.g. "el-capitan"."""
    products = json.loads((Path(tenant.claims_dir) / "products.json").read_text())["products"]
    return sorted({product_name_slug(p["name"]) for p in products.values()})


def load_manifest(tenant, *, log=None):
    """The manifest dict, or an empty one when the file is missing or does
    not validate (a bad manifest never fails a run -- the library is just
    not used, with a logged warning)."""
    path = manifest_path(tenant)
    if not path.exists():
        return {"version": MANIFEST_VERSION, "photos": []}
    try:
        data = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError) as e:
        if log:
            log.event("photos", f"photo-library.json unreadable, library not used: {e}")
        return {"version": MANIFEST_VERSION, "photos": []}
    errors = validate_manifest(data, features=features_for(tenant), models=model_slugs(tenant))
    if errors:
        if log:
            log.event("photos", f"photo-library.json invalid, library not used: {errors[:3]}")
        return {"version": MANIFEST_VERSION, "photos": []}
    return data


def save_manifest(tenant, data):
    path = manifest_path(tenant)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n")
    tmp.replace(path)
    return path


def photos_by_id(manifest):
    return {p["id"]: p for p in manifest.get("photos", [])}


def local_file(tenant, photo):
    return files_dir(tenant) / photo["file"]


# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------

def validate_tags(tags, *, features, models):
    """Problems (strings) with one photo's tags; [] when valid."""
    errs = []
    if not isinstance(tags, dict):
        return ["tags is not an object"]
    product = tags.get("product")
    if product != UNKNOWN and product not in models:
        errs.append(f"product {product!r} is not a model slug or 'unknown'")
    conf = tags.get("product_confidence")
    if not isinstance(conf, (int, float)) or isinstance(conf, bool) or not 0 <= conf <= 1:
        errs.append("product_confidence must be a number 0..1")
    if not isinstance(tags.get("model_agnostic"), bool):
        errs.append("model_agnostic must be a bool")
    elif tags["model_agnostic"] and product != UNKNOWN:
        errs.append("model_agnostic photo must have product 'unknown'")
    if tags.get("setting") not in SETTINGS:
        errs.append(f"setting {tags.get('setting')!r} not in {SETTINGS}")
    if tags.get("shot") not in SHOTS:
        errs.append(f"shot {tags.get('shot')!r} not in {SHOTS}")
    fv = tags.get("features_visible")
    if not isinstance(fv, list) or any(f not in features for f in fv):
        errs.append(f"features_visible must be a list from {list(features)}")
    for key in ("has_text_overlay", "blurry", "cluttered"):
        if not isinstance(tags.get(key), bool):
            errs.append(f"{key} must be a bool")
    people = tags.get("people")
    if not isinstance(people, int) or isinstance(people, bool) or people < 0:
        errs.append("people must be a count >= 0")
    desc = tags.get("description")
    if not isinstance(desc, str) or not desc.strip():
        errs.append("description must be a non-empty string")
    if "old_logo_visible" in tags and not isinstance(tags["old_logo_visible"], bool):
        errs.append("old_logo_visible must be a bool")
    return errs


def apply_corrections(photo, tags):
    """`tags` with the photo's recorded human corrections applied (cycle 79):
    each {"field": "features_visible", "removed": [...], "added": [...]}."""
    for c in photo.get("corrections") or []:
        if not isinstance(c, dict) or c.get("field") != "features_visible":
            continue
        feats = [f for f in tags.get("features_visible") or [] if f not in set(c.get("removed") or [])]
        feats += [f for f in c.get("added") or [] if f not in feats]
        tags["features_visible"] = feats
    return tags


def validate_manifest(data, *, features, models):
    """Problems (strings) with a whole manifest; [] when valid. An untagged
    photo (tags null -- imported, not yet tagged) is valid; it is simply
    never offered to a page."""
    if not isinstance(data, dict) or data.get("version") != MANIFEST_VERSION:
        return [f"manifest must be an object with version {MANIFEST_VERSION}"]
    photos = data.get("photos")
    if not isinstance(photos, list):
        return ["photos must be a list"]
    errs, seen = [], set()
    for i, p in enumerate(photos):
        where = f"photos[{i}]"
        if not isinstance(p, dict):
            errs.append(f"{where}: not an object")
            continue
        pid = p.get("id")
        if not isinstance(pid, str) or not ID_RE.match(pid):
            errs.append(f"{where}: bad id {pid!r}")
        elif pid in seen:
            errs.append(f"{where}: duplicate id {pid}")
        seen.add(pid)
        if p.get("file") != f"{pid}.jpg":
            errs.append(f"{where}: file must be '<id>.jpg'")
        for key in ("width", "height"):
            if not isinstance(p.get(key), int) or p[key] <= 0:
                errs.append(f"{where}: {key} must be a positive int")
        for key in ("ai_generated", "excluded"):
            if not isinstance(p.get(key), bool):
                errs.append(f"{where}: {key} must be a bool")
        if p.get("tags") is not None:
            errs += [f"{where}: {e}" for e in validate_tags(p["tags"], features=features, models=models)]
    return errs


# ---------------------------------------------------------------------------
# Import: web-size derivatives, stable ids
# ---------------------------------------------------------------------------

def photo_id(data):
    return ID_PREFIX + hashlib.sha256(data).hexdigest()[:12]


def make_derivative(data):
    """(jpeg bytes, width, height): EXIF orientation applied, then every
    EXIF/ICC/XMP block dropped (a fresh RGB image is saved), long edge at
    most DERIVATIVE_MAX_EDGE, JPEG quality DERIVATIVE_JPEG_QUALITY."""
    with Image.open(io.BytesIO(data)) as im:
        im = ImageOps.exif_transpose(im)
        if im.mode in ("RGBA", "LA", "P"):
            im = im.convert("RGBA")
            bg = Image.new("RGB", im.size, (255, 255, 255))
            bg.paste(im, mask=im.split()[-1])
            im = bg
        else:
            im = im.convert("RGB")
        im.thumbnail((DERIVATIVE_MAX_EDGE, DERIVATIVE_MAX_EDGE), Image.LANCZOS)
        clean = Image.new("RGB", im.size)
        clean.paste(im)
        buf = io.BytesIO()
        clean.save(buf, format="JPEG", quality=DERIVATIVE_JPEG_QUALITY, optimize=True, progressive=True)
        return buf.getvalue(), clean.width, clean.height


def import_folder(tenant, src_dir, *, log=print):
    """Adds every image in `src_dir` to the manifest (an id already present
    keeps its tags) and writes each derivative. Byte-identical files share
    one id, so a duplicate download is imported once. Returns
    {"added", "existing", "duplicates", "skipped"}."""
    manifest = load_manifest(tenant)
    by_id = photos_by_id(manifest)
    out_dir = files_dir(tenant)
    out_dir.mkdir(parents=True, exist_ok=True)
    counts = {"added": 0, "existing": 0, "duplicates": 0, "skipped": 0}
    seen_this_run = set()
    for src in sorted(Path(src_dir).iterdir()):
        if src.suffix.lower() not in IMAGE_EXTS or not src.is_file():
            continue
        data = src.read_bytes()
        pid = photo_id(data)
        if pid in seen_this_run:
            counts["duplicates"] += 1
            log(f"duplicate of {pid}: {src.name}")
            continue
        seen_this_run.add(pid)
        try:
            jpeg, width, height = make_derivative(data)
        except Exception as e:  # not an image Pillow can open
            counts["skipped"] += 1
            log(f"skipped {src.name}: {e}")
            continue
        (out_dir / f"{pid}.jpg").write_bytes(jpeg)
        if pid in by_id:
            m = _DRIVE_ID_RE.match(by_id[pid]["source_name"])
            by_id[pid].update(width=width, height=height, drive_id=m.group(1) if m else None)
            counts["existing"] += 1
            continue
        m = _DRIVE_ID_RE.match(src.name)
        entry = {
            "id": pid,
            "file": f"{pid}.jpg",
            "source_name": src.name,
            "drive_id": m.group(1) if m else None,
            "sha256": hashlib.sha256(data).hexdigest(),
            "width": width,
            "height": height,
            "ai_generated": bool(_AI_FILENAME_RE.search(src.name)),
            "excluded": False,
            "tags": None,
            "decision": None,
        }
        manifest.setdefault("photos", []).append(entry)
        by_id[pid] = entry
        counts["added"] += 1
    manifest["version"] = MANIFEST_VERSION
    manifest["source"] = str(src_dir)
    save_manifest(tenant, manifest)
    return counts


# ---------------------------------------------------------------------------
# Vision tagging
# ---------------------------------------------------------------------------

TAG_SYSTEM = """You tag photos for a sauna company's internal photo library. Each photo will be placed on web pages about ONE specific sauna model, so the model identity must be right. A wrong model is much worse than "unknown".

You get, first, reference images of every model in the catalog (from the company's own store), each labeled with the model slug and its specs. Then you get ONE photo to tag.

How to identify the model in the photo:
- Compare the cabin against the references: width vs height (1, 2, 3, 4 or 5 person), indoor cabin vs outdoor cabin with a roof/canopy and weather siding, wood colour (pale hemlock vs reddish cedar), exterior finish (black side panels vs wood), door and window shapes, glass-panel layout, where the red light panels sit and how many there are.
- Ignore the file name and any caption. Decide from what you see.
- If two or more models fit (for example two 1-person indoor cabins that differ only in wood), or the cabin is only partly visible, answer "unknown" and say which models were possible.
- If the photo shows no sauna cabin at all, or only a part common to every model (a generic wood wall, a towel, a person), answer "unknown" and set model_agnostic true. model_agnostic is false whenever any cabin or cabin part that could belong to a specific model is visible.

Describe only what is visible. No health claims.{forbidden_line}

Output ONLY one JSON object, no markdown fences, with exactly these keys:
- product: one of {models}, or "unknown"
- product_confidence: number 0 to 1, how sure you are of product (for "unknown", how sure you are that it cannot be identified)
- product_reason: one short sentence naming the visual evidence and any models you ruled out
- candidates: array of every model slug the photo could show (one item when you are sure; [] when no cabin is visible)
- model_agnostic: true or false (see above)
- setting: one of {settings}  ("studio" = plain or seamless background, no real room)
- shot: one of {shots}  ("exterior" = the whole cabin seen from outside, including a front view where the inside shows through the glass door or the door is open; "interior" = the camera is inside the cabin, or only the inside is in frame; "detail" = close-up of one part; "lifestyle" = the cabin placed in a real room or yard, no person using it; "people-in-use" = a person in or at the sauna; "ad-still-with-text" = a frame with words or graphics laid over it; a cabin being built or delivered is "people-in-use" when people are visible, else "detail", with assembly in features_visible)
- features_visible: array, ONLY from this list, only features clearly visible: {features}
  (red-light-panel = a panel of red LEDs; heaters = the infrared carbon/ceramic heater panels; size-in-room = the whole cabin shown in a room so its size is clear; outlet-plug = a wall outlet, plug or power cord; assembly = the cabin being built or unpacked; chromotherapy = coloured mood lighting; roof-outdoor-weather = a roof or canopy, snow, rain, or an outdoor weather-proof cabin)
- has_text_overlay: true if words, prices or graphics are laid over the photo (a logo printed on the cabin itself does not count)
- people: number of people visible
- description: one sentence, at most 20 words, of what is visible
- blurry: true or false
- cluttered: true if the frame is busy enough that the sauna is hard to see"""


def _verified_texts(tenant):
    path = Path(tenant.claims_dir) / "verified.json"
    return {c["id"]: c["text"] for c in json.loads(path.read_text())} if path.exists() else {}


def _spec_line(product, verified=None):
    """A short, visual spec line for one model, from products.json and the
    model's own verified spec claims (spec-<model>-red-light, -electrical)."""
    handle = product.get("slug", "")
    model = product_name_slug(product.get("name"))
    verified = verified or {}
    specs = {s.get("label", "").lower(): str(s.get("value", "")) for s in product.get("specs", [])}
    parts = []
    if specs.get("capacity"):
        parts.append(specs["capacity"])
    placement = specs.get("placement") or ("Outdoor" if "outdoor" in handle else "Indoor" if "indoor" in handle else "")
    if placement:
        parts.append(placement.lower())
    if specs.get("cabin material"):
        parts.append(specs["cabin material"])
    red = verified.get(f"spec-{model}-red-light")
    if red:
        m = re.search(r"(\d+) RLT panels?", red)
        parts.append(f"{m.group(1)} red light panel(s)" if m else "red light panel")
    elif "red-light" in handle:
        parts.append("red light panel")
    else:
        parts.append("no red light panel listed")
    if specs.get("heating panels"):
        parts.append(f"{specs['heating panels']} heating panels")
    return ", ".join(parts)


def model_has_feature(tenant, model, feature, *, verified=None, products=None):
    """False only when the catalog says the model cannot show `feature`: a
    red light panel on a model with no spec-<model>-red-light claim, or
    outdoor weather on an indoor model (red light: a spec-<model>-red-light
    claim, else "red-light" in the storefront handle). Used to keep a model-agnostic
    detail photo off a page whose product does not have that part."""
    if products is None:
        products = json.loads((Path(tenant.claims_dir) / "products.json").read_text())["products"]
    if feature == "red-light-panel":
        verified = verified if verified is not None else _verified_texts(tenant)
        handles = [h for h, p in products.items() if product_name_slug(p["name"]) == model]
        return f"spec-{model}-red-light" in verified or any("red-light" in h for h in handles)
    if feature == "roof-outdoor-weather":
        for handle, p in products.items():
            if product_name_slug(p["name"]) == model:
                specs = {s.get("label", "").lower(): str(s.get("value", "")).lower() for s in p.get("specs", [])}
                return specs.get("placement") == "outdoor" or "outdoor" in handle
        return False
    return True


def build_reference_images(tenant, *, indices=(1, 2, 4, 5), cache_dir=None, log=print):
    """[(model_slug, spec_line, jpeg_bytes)] -- one labeled strip of the
    model's storefront images at `indices` (1-based; image 3 is usually the
    same red light panel close-up on every model, so it is skipped).
    Images come from `cache_dir` (<asset id>-480.jpg files, as
    render.download_asset writes them) when present, else are downloaded."""
    from PIL import ImageDraw

    from .render import download_asset

    products = json.loads((Path(tenant.claims_dir) / "products.json").read_text())["products"]
    verified = _verified_texts(tenant)
    cache_dir = Path(cache_dir or (Path(tenant.runs_dir) / "asset-cache"))
    refs = []
    for handle, p in products.items():
        slug = product_name_slug(p["name"])
        thumbs = []
        urls = p.get("image_urls") or []
        for i in indices:
            if i > len(urls):
                continue
            url = urls[i - 1]
            asset_id = f"asset-{handle}-{i}"
            f = cache_dir / f"{asset_id}-480.jpg"
            if not f.exists():
                got = download_asset({"id": asset_id, "url": url}, cache_dir)
                f = cache_dir / f"{asset_id}-480.jpg"
                if got is None or not f.exists():
                    log(f"reference image missing: {asset_id}")
                    continue
            with Image.open(f) as im:
                im = im.convert("RGB")
                im.thumbnail((300, 300))
                thumbs.append(im.copy())
        if not thumbs:
            continue
        spec = _spec_line(dict(p, slug=handle), verified)
        strip = Image.new("RGB", (300 * len(thumbs), 330), "white")
        for c, im in enumerate(thumbs):
            strip.paste(im, (c * 300 + (300 - im.width) // 2, 30 + (300 - im.height) // 2))
        ImageDraw.Draw(strip).text((6, 8), f"{slug}: {spec}", fill="black")
        buf = io.BytesIO()
        strip.save(buf, format="JPEG", quality=82)
        refs.append((slug, spec, buf.getvalue()))
    return refs


def _b64_image(data):
    return {"type": "image", "source": {"type": "base64", "media_type": "image/jpeg",
                                        "data": base64.standard_b64encode(data).decode("ascii")}}


def build_tag_request(photo_bytes, refs, *, features, models, forbidden_terms=()):
    """(system, messages) for one tag call. The reference block is identical
    for every photo and carries the cache breakpoint, so photos 2..N read it
    from the prompt cache."""
    forbidden_line = ""
    if forbidden_terms:
        forbidden_line = f" Never use any of these words: {', '.join(forbidden_terms)}."
    system = TAG_SYSTEM.format(
        models=", ".join(models), settings=", ".join(SETTINGS), shots=", ".join(SHOTS),
        features=", ".join(features), forbidden_line=forbidden_line,
    )
    ref_content = [{"type": "text", "text": "REFERENCE IMAGES (the company's own store photos, one strip per model):"}]
    for slug, spec, data in refs:
        ref_content.append({"type": "text", "text": f"Model {slug}: {spec}"})
        ref_content.append(_b64_image(data))
    ref_content[-1] = dict(ref_content[-1], cache_control={"type": "ephemeral"})
    content = ref_content + [
        {"type": "text", "text": "PHOTO TO TAG:"},
        _b64_image(photo_bytes),
        {"type": "text", "text": "Return the JSON object described in your instructions."},
    ]
    return system, [{"role": "user", "content": content}]


class TagResponseInvalid(Exception):
    pass


def parse_tag_response(text, *, features, models, forbidden_terms=()):
    """The model's JSON -> (tags dict, vision dict). Invalid enum values are
    a hard error (the photo stays untagged and is retried), except
    features_visible, where an unknown feature is dropped."""
    from .asset_describe import strip_forbidden_terms
    from .jsonutil import extract_json

    try:
        data = extract_json(text)
    except Exception as e:
        raise TagResponseInvalid(f"not JSON: {e}") from None
    if not isinstance(data, dict):
        raise TagResponseInvalid("not a JSON object")
    product = str(data.get("product") or UNKNOWN).strip().lower()
    if product not in models:
        product = UNKNOWN
    try:
        conf = float(data.get("product_confidence"))
    except (TypeError, ValueError):
        raise TagResponseInvalid("product_confidence is not a number") from None
    try:
        people = int(data.get("people") or 0)
    except (TypeError, ValueError):
        people = 0
    shot = data.get("shot")
    features_seen = set(data.get("features_visible") or [])
    if shot == "assembly":  # a feature, not a shot -- see TAG_SYSTEM
        shot = "people-in-use" if people else "detail"
        features_seen.add("assembly")
    tags = {
        "product": product,
        "product_confidence": round(max(0.0, min(1.0, conf)), 2),
        "model_agnostic": bool(data.get("model_agnostic")) and product == UNKNOWN,
        "setting": data.get("setting"),
        "shot": shot,
        "features_visible": sorted({f for f in features_seen if f in features}),
        "has_text_overlay": bool(data.get("has_text_overlay")),
        "people": max(0, people),
        "description": strip_forbidden_terms(str(data.get("description") or "").strip(), forbidden_terms),
        "blurry": bool(data.get("blurry")),
        "cluttered": bool(data.get("cluttered")),
    }
    if tags["setting"] not in SETTINGS or tags["shot"] not in SHOTS or not tags["description"]:
        raise TagResponseInvalid(f"bad setting/shot/description: {data!r}")
    candidates = sorted({str(c).strip().lower() for c in data.get("candidates") or [] if str(c).strip().lower() in models})
    if product != UNKNOWN and product not in candidates:
        candidates = sorted(set(candidates) | {product})
    vision = {
        "product": product,
        "confidence": tags["product_confidence"],
        "candidates": candidates,
        "agnostic": tags["model_agnostic"],
        "reason": strip_forbidden_terms(str(data.get("product_reason") or "").strip(), forbidden_terms),
    }
    return tags, vision


MIN_AGREED_CONFIDENCE = 0.5
MAX_FOLDER_CANDIDATES = 3
MIN_VERIFY_CONFIDENCE = 0.7
# Pass 1 this sure of a different model is not overruled by a folder check.
PASS1_VETO_CONFIDENCE = 0.8
# A close-up that pass 1 says fits this many models shows a part every model shares.
AGNOSTIC_MIN_CANDIDATES = 4
# Only a close-up: a person shot can still show a whole cabin.
AGNOSTIC_SHOTS = ("detail",)


def decide_product(vision, folder_model, verify=None):
    """(product, confidence, rule). Two vision calls and one non-filename
    label decide it:

      pass 1  -- open identification against every model's references
                 (vision: product, confidence, candidates)
      folder  -- the model of the Drive folder the photo was filed in by the
                 owner (brand/assets.json), when it came from that library.
                 Never the file name.
      verify  -- only when pass 1 does not already agree with the folder:
                 a second call asking whether the photo is consistent with
                 the folder's model (verdict match/mismatch/cannot-tell)

      folder X, pass 1 X (conf >= 0.5)                     -> X
      folder X, verify match (>= 0.7), and pass 1 not
        sure (>= 0.8) of another model                     -> X
      folder X, pass 1 unknown, X among 1-3 candidates     -> X
      no folder, pass 1 X (conf >= 0.7)                    -> X
      anything else                                        -> unknown
    """
    vp, vc = vision["product"], vision["confidence"]
    candidates = vision.get("candidates") or []
    if folder_model:
        if vp == folder_model and vc >= MIN_AGREED_CONFIDENCE:
            return vp, max(vc, 0.85), "vision and drive folder agree"
        if verify and verify.get("verdict") == "match" and verify.get("confidence", 0) >= MIN_VERIFY_CONFIDENCE \
                and not (vp not in (UNKNOWN, folder_model) and vc >= PASS1_VETO_CONFIDENCE):
            first = "unknown" if vp == UNKNOWN else f"{vp} ({vc})"
            return folder_model, round(min(verify["confidence"], 0.85), 2), \
                f"drive folder {folder_model} confirmed by vision check (pass 1 said {first})"
        if vp == UNKNOWN and folder_model in candidates and len(candidates) <= MAX_FOLDER_CANDIDATES:
            return folder_model, 0.7, f"vision narrowed to {candidates}; drive folder picked {folder_model}"
        if verify and verify.get("verdict") == "mismatch":
            return UNKNOWN, 0.0, f"drive folder {folder_model} rejected by vision check: {verify.get('reason', '')}"[:200]
        if vp not in (UNKNOWN, folder_model):
            return UNKNOWN, 0.0, f"conflict: vision {vp} ({vc}) vs drive folder {folder_model}"
        return UNKNOWN, vc, f"drive folder {folder_model} not confirmed (vision {vp} {vc})"
    if vp != UNKNOWN and vc >= MIN_VISION_CONFIDENCE:
        return vp, vc, "vision only (no folder label)"
    if vp != UNKNOWN:
        return UNKNOWN, vc, f"vision {vp} too unsure ({vc}), no folder label"
    return UNKNOWN, vc, "vision: cannot identify" + (f" (candidates {candidates})" if candidates else "")


def derive_agnostic(tags, vision):
    """model_agnostic for a photo with no product: pass 1 said so (no cabin
    at all), or it is a close-up that pass 1 says fits AGNOSTIC_MIN_CANDIDATES
    or more models -- a part every model shares. (photos_for_product still
    keeps it off a page whose model pass 1 left out of its candidates.)"""
    if tags["product"] != UNKNOWN:
        return False
    if vision.get("agnostic"):
        return True
    return tags["shot"] in AGNOSTIC_SHOTS and len(vision.get("candidates") or []) >= AGNOSTIC_MIN_CANDIDATES


VERIFY_SYSTEM = """You check one photo against one sauna model for a sauna company's photo library. The photo was filed by the company under the model named at the end of the message. Filing mistakes happen, so check it.

You get the reference images of every model (the company's own store photos), then the photo, then the model to check.

Answer "match" if everything visible in the photo is consistent with that model (size, indoor/outdoor, roof, wood colour, door and window layout, red light panels) and it fits that model at least as well as any other model. Answer "mismatch" if the photo clearly shows a different model. Answer "cannot-tell" if too little of the cabin is visible to judge. Ignore any file name.

Output ONLY one JSON object, no markdown fences: {"verdict": "match"|"mismatch"|"cannot-tell", "confidence": 0..1, "reason": "one short sentence"}"""


def folder_models(tenant):
    """{drive file id: model slug} from brand/assets.json + the listicle pack."""
    out = {}
    for name in ("assets.json", "assets-listicle-pack.json"):
        path = Path(tenant.brand_dir) / name
        if path.exists():
            for a in json.loads(path.read_text()).get("assets", []):
                if a.get("model"):
                    out[a["id"]] = a["model"]
    return out


def vision_bytes(path, max_edge=1200):
    with Image.open(path) as im:
        im = im.convert("RGB")
        im.thumbnail((max_edge, max_edge), Image.LANCZOS)
        buf = io.BytesIO()
        im.save(buf, format="JPEG", quality=85)
        return buf.getvalue()


def _call(client, model, system, messages, budget, log, *, max_tokens=600):
    from .anthropic_client import thinking_kwargs
    from .asset_describe import _pricing_model_id

    budget.check()
    response = client.messages.create(
        model=model, max_tokens=max_tokens, system=system, messages=messages, **thinking_kwargs(model),
    )
    usage = response.usage
    budget.record_call(usage.input_tokens, usage.output_tokens)
    log.call(
        "photos_tag", _pricing_model_id(model), usage.input_tokens, usage.output_tokens,
        cache_creation_input_tokens=getattr(usage, "cache_creation_input_tokens", 0) or 0,
        cache_read_input_tokens=getattr(usage, "cache_read_input_tokens", 0) or 0,
    )
    return "".join(b.text for b in response.content if getattr(b, "type", None) == "text")


def parse_verify_response(text):
    from .jsonutil import extract_json

    try:
        data = extract_json(text)
    except Exception:
        return None
    if not isinstance(data, dict) or data.get("verdict") not in ("match", "mismatch", "cannot-tell"):
        return None
    try:
        conf = round(max(0.0, min(1.0, float(data.get("confidence")))), 2)
    except (TypeError, ValueError):
        return None
    return {"verdict": data["verdict"], "confidence": conf, "reason": str(data.get("reason") or "")[:300]}


def tag_photos(tenant, *, client, model, budget, log, refs, ids=None, force=False, forbidden_terms=(),
               reuse_vision=False):
    """Tags every untagged photo (or `ids`, or all with `force`): pass 1 (one
    call), then -- only for a photo filed under a Drive model folder that
    pass 1 did not already confirm -- the verify call; decide_product turns
    the answers into the product tag. `reuse_vision` re-decides from the
    stored pass-1 answer (no pass-1 call). Saves after each photo. Returns
    {"tagged", "failed", "verified"}."""
    manifest = load_manifest(tenant)
    features, models = features_for(tenant), model_slugs(tenant)
    folders = folder_models(tenant)
    products = json.loads((Path(tenant.claims_dir) / "products.json").read_text())["products"]
    verified_claims = _verified_texts(tenant)
    spec_by_model = {product_name_slug(p["name"]): _spec_line(dict(p, slug=h), verified_claims) for h, p in products.items()}
    tagged = failed = verified = 0
    for photo in manifest.get("photos", []):
        if ids is not None and photo["id"] not in ids:
            continue
        if photo.get("tags") and not force and ids is None:
            continue
        photo_bytes = vision_bytes(local_file(tenant, photo))
        if reuse_vision and photo.get("decision") and photo.get("tags"):
            tags = dict(photo["tags"])
            d = photo["decision"]
            vision = {"product": d["vision_product"], "confidence": d["vision_confidence"],
                      "reason": d["vision_reason"], "candidates": d.get("vision_candidates") or [],
                      "agnostic": d.get("vision_agnostic", tags.get("model_agnostic", False))}
        else:
            system, messages = build_tag_request(
                photo_bytes, refs, features=features, models=models, forbidden_terms=forbidden_terms,
            )
            text = _call(client, model, system, messages, budget, log)
            try:
                tags, vision = parse_tag_response(text, features=features, models=models, forbidden_terms=forbidden_terms)
            except TagResponseInvalid as e:
                failed += 1
                log.event("photos_tag", f"{photo['id']}: invalid response, left untagged: {e}")
                continue
        folder = folders.get(photo.get("drive_id") or "")
        verify = (photo.get("decision") or {}).get("verify") if reuse_vision else None
        if folder and not (vision["product"] == folder and vision["confidence"] >= MIN_AGREED_CONFIDENCE) and verify is None:
            _ref_system, ref_messages = build_tag_request(photo_bytes, refs, features=features, models=models)
            content = list(ref_messages[0]["content"])
            content[-1] = {"type": "text", "text": (
                f"Model to check: {folder} ({spec_by_model.get(folder, '')}). Return the JSON object.")}
            verify = parse_verify_response(_call(client, model, VERIFY_SYSTEM, [{"role": "user", "content": content}],
                                                 budget, log, max_tokens=200))
            verified += 1
        product, conf, rule = decide_product(vision, folder, verify)
        tags["product"], tags["product_confidence"] = product, round(conf, 2)
        tags["model_agnostic"] = derive_agnostic(tags, vision)
        if tags["model_agnostic"] and not vision.get("agnostic"):
            rule += f"; agnostic: {tags['shot']} fits {len(vision.get('candidates') or [])} models"
        # Cycle 78: old_logo_visible is a human decision, not a model tag --
        # a re-tag keeps it.
        if "old_logo_visible" in (photo.get("tags") or {}):
            tags["old_logo_visible"] = photo["tags"]["old_logo_visible"]
        # Cycle 79: a person's correction of a feature tag (photo
        # "corrections") is kept too.
        tags = apply_corrections(photo, tags)
        photo["tags"] = tags
        photo["decision"] = {
            "vision_product": vision["product"], "vision_confidence": vision["confidence"],
            "vision_reason": vision["reason"], "vision_candidates": vision.get("candidates") or [],
            "vision_agnostic": bool(vision.get("agnostic")),
            "folder_model": folder, "verify": verify, "rule": rule, "model": model,
            "at": datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds"),
        }
        save_manifest(tenant, manifest)
        tagged += 1
        log.event("photos_tag", f"{photo['id']}: {product} ({rule}); {tags['shot']}; {','.join(tags['features_visible'])}")
    return {"tagged": tagged, "failed": failed, "verified": verified}


# ---------------------------------------------------------------------------
# Selection: what a page about product X is offered
# ---------------------------------------------------------------------------

def _usable(photo):
    tags = photo.get("tags")
    return bool(tags) and not photo.get("excluded") and not tags["has_text_overlay"] \
        and tags["shot"] != "ad-still-with-text"


def shows_old_logo(photo):
    """Cycle 78: the photo shows the retired brand mark (the old logo on
    the glass, a control panel or a red light panel). Set by
    a person looking at the photo (tags.old_logo_visible). Such a photo may
    fill a lower slot but never the hero or the first item image -- above
    the fold the page must show the current brand."""
    return bool((photo.get("tags") or {}).get("old_logo_visible"))


def photos_for_product(manifest, model_slug, *, tenant=None, require_file=True, feature_ok=None):
    """(own, agnostic): usable photos tagged `model_slug`, and usable
    model-agnostic photos -- each list best-first (hero_rank). With
    `require_file`, a photo whose derivative is missing on this machine is
    left out (the manifest is in git; the files are not). `feature_ok(f)`
    keeps an agnostic photo off the page when it shows a part the product
    does not have (model_has_feature); an agnostic photo whose pass-1
    candidates leave this model out is never offered either."""
    own, agnostic = [], []
    for p in manifest.get("photos", []):
        if not _usable(p):
            continue
        if require_file and tenant is not None and not local_file(tenant, p).exists():
            continue
        if p["tags"]["product"] == model_slug:
            own.append(p)
        elif p["tags"]["product"] == UNKNOWN and p["tags"]["model_agnostic"]:
            candidates = (p.get("decision") or {}).get("vision_candidates") or []
            if candidates and model_slug not in candidates:
                continue  # pass 1 saw enough to rule this model out
            if feature_ok is None or all(feature_ok(f) for f in p["tags"]["features_visible"]):
                agnostic.append(p)
    own.sort(key=hero_rank, reverse=True)
    agnostic.sort(key=hero_rank, reverse=True)
    return own, agnostic


def hero_rank(photo):
    """Higher is a better hero: a clean exterior of the product first."""
    t = photo["tags"]
    shot_score = {"exterior": 6, "lifestyle": 4, "people-in-use": 3, "interior": 2, "detail": 0}.get(t["shot"], -5)
    return (
        shot_score
        + (0 if photo.get("ai_generated") else 1)
        - 3 * t["blurry"] - 2 * t["cluttered"]
        + t["product_confidence"],
        photo["id"],
    )


def quality_rank(photo):
    t = photo["tags"]
    return (
        -3 * t["blurry"] - 2 * t["cluttered"] + (0 if photo.get("ai_generated") else 0.5)
        + (1 if t["product"] != UNKNOWN else 0)
    )


def attach_local_paths(assets_by_id, tenant):
    """Points every library asset in `assets_by_id` at its derivative on
    disk, so render.download_asset reads it instead of fetching a url."""
    lib = [i for i in assets_by_id if i.startswith(ID_PREFIX)]
    if not lib:
        return
    manifest = photos_by_id(load_manifest(tenant))
    for asset_id in lib:
        photo = manifest.get(asset_id)
        if photo is not None:
            assets_by_id[asset_id]["local_path"] = str(local_file(tenant, photo))


def facts_pack_asset(photo):
    """The lean entry the writer sees (facts_pack has almost no size
    headroom -- tests/test_ground.py::test_facts_pack_stays_small). No alt:
    render.render_page derives every alt itself (render.asset_alt), and the
    renderer, not the writer, matches photos to text (assign_page_images)."""
    t = photo["tags"]
    # "url" is the derivative's path under the tenant root (every asset
    # carries one; review_md lists it). render reads the file via
    # attach_local_paths, never by fetching this.
    asset = {"id": photo["id"], "url": f"{FILES_DIRNAME}/{photo['file']}", "kind": SHOT_KIND[t["shot"]]}
    if photo.get("ai_generated"):
        asset["ai_generated"] = True
    return asset


def facts_pack_assets(tenant, model_slug, *, allow_ai_renders, log=None):
    """The library part of facts_pack.assets for a page about `model_slug`,
    hero candidate first; [] when the library has no usable photo of it
    (the caller then keeps the old pools unchanged)."""
    manifest = load_manifest(tenant, log=log)
    verified = _verified_texts(tenant)
    own, agnostic = photos_for_product(
        manifest, model_slug, tenant=tenant,
        feature_ok=lambda f: model_has_feature(tenant, model_slug, f, verified=verified),
    )
    if not allow_ai_renders:
        own = [p for p in own if not p.get("ai_generated")]
        agnostic = [p for p in agnostic if not p.get("ai_generated")]
    if not own:
        return []
    return [facts_pack_asset(p) for p in own + pick_agnostic(agnostic)]


# facts_pack has almost no size headroom, so a page is offered at most this
# many model-agnostic photos -- chosen to cover as many features as possible.
AGNOSTIC_MAX = 8


def pick_agnostic(agnostic, limit=AGNOSTIC_MAX):
    """Up to `limit` of `agnostic` (best-first), greedily maximizing the
    features (and shots) covered, so topic matching has the widest choice."""
    chosen, covered = [], set()
    rest = list(agnostic)
    while rest and len(chosen) < limit:
        def gain(p):
            keys = set(p["tags"]["features_visible"]) | {"shot:" + p["tags"]["shot"]}
            return (len(keys - covered), quality_rank(p))
        best = max(rest, key=gain)
        chosen.append(best)
        covered |= set(best["tags"]["features_visible"]) | {"shot:" + best["tags"]["shot"]}
        rest.remove(best)
    return chosen


def library_drive_ids(tenant):
    """Drive ids of every photo in the library -- the old Drive index rows
    for the same files are never offered again (they carry an older, less
    certain model label and would show the same photo under a second id)."""
    return {p["drive_id"] for p in load_manifest(tenant).get("photos", []) if p.get("drive_id")}


# ---------------------------------------------------------------------------
# Topic matching: which photo shows what this slot talks about
# ---------------------------------------------------------------------------

_WORD_RE = re.compile(r"[a-z0-9]+(?:[-'][a-z0-9]+)*")


def slot_topics(text, keywords):
    """{topic: hits} for the slot's own text: each keyword (a word or a
    phrase) counted once per topic."""
    lowered = " " + " ".join(_WORD_RE.findall((text or "").lower())) + " "
    topics = {}
    for topic, words in keywords.items():
        hits = sum(1 for w in words if f" {w.lower()} " in lowered)
        if hits:
            topics[topic] = hits
    return topics


HEADING_WEIGHT = 3


def slot_topics_weighted(heading, context, keywords):
    """slot_topics over the whole slot text, with every topic the HEADING
    asks for counted HEADING_WEIGHT more times: an item's heading is what it
    is about; its body mentions many things in passing."""
    topics = slot_topics(context, keywords)
    for topic, hits in slot_topics(heading, keywords).items():
        topics[topic] = topics.get(topic, 0) + HEADING_WEIGHT * hits
    return topics


def topic_score(photo, topics):
    """(score, matched topics) of one photo against one slot's topics."""
    t = photo["tags"]
    matched, score = [], 0.0
    for topic, hits in topics.items():
        weight = min(hits, 2 + HEADING_WEIGHT)
        if topic.startswith("shot:"):
            ok = t["shot"] == topic[5:]
            w = 2
        elif topic.startswith("setting:"):
            ok = t["setting"] == topic[8:]
            w = 2
        else:
            ok = topic in t["features_visible"]
            w = 3
        if ok:
            score += w * weight
            matched.append(topic)
    return score, matched


def topic_share(photo, topics):
    """The share of the photo's own features that the slot asks for (1.0 for
    a photo with no features listed): the tie-break between two photos that
    both match -- the one that shows mostly the topic wins."""
    feats = set(photo["tags"]["features_visible"])
    if not feats:
        return 1.0
    return len(feats & set(topics)) / len(feats)


def _library_slots(page, cartridge_name):
    """(path, node, context, is_hero, heading) for every image slot -- ground's own
    per-cartridge slot knowledge, plus every other asset_id node."""
    from . import ground as ground_mod

    hero = ground_mod.hero_container(page, cartridge_name)
    slots, seen = [], set()
    for path, node, context in ground_mod._match_slots(page, cartridge_name):
        slots.append((path, node, context, node is hero, _slot_heading(page, path)))
        seen.add(id(node))
    skip = {"images"} if cartridge_name == "longform" else ()
    for path, node in walk_page(page, skip_keys=skip):
        if isinstance(node, dict) and "asset_id" in node and id(node) not in seen:
            slots.append((path, node, "", node is hero, ""))
            seen.add(id(node))
    if hero is not None and id(hero) not in seen:
        slots.insert(0, ("hero", hero, "", True, ""))
    return slots


_SECTION_PATH_RE = re.compile(r"^(reasons|how_it_works\.steps|images)\[(\d+)\]")


def _slot_heading(page, path):
    """The heading of the section an image slot belongs to ("" if none):
    reasons[i] / how_it_works.steps[i] own heading or title; article's
    images[i] pairs with body_sections[i] (ground._match_slots' pairing)."""
    m = _SECTION_PATH_RE.match(path or "")
    if not m:
        return ""
    i = int(m.group(2))
    if m.group(1) == "reasons":
        items = page.get("reasons") or []
    elif m.group(1) == "images":
        items = page.get("body_sections") or []
        i = min(i, len(items) - 1)
    else:
        items = (page.get("how_it_works") or {}).get("steps") or []
    if 0 <= i < len(items) and isinstance(items[i], dict):
        return str(items[i].get("heading") or items[i].get("title") or "")
    return ""


def page_model_slug(facts_pack):
    return product_name_slug((facts_pack.get("product") or {}).get("name") or "")


DEFAULT_WHOLE_UNIT_TOPICS = ("unit", "size-in-room")
CUTOUT_MAX_USES = 3   # the hero plus two items


def whole_unit_topics_for(tenant):
    """Topics that are about the whole cabin (tenant.yaml
    photo_library.whole_unit_topics): a slot about one of them shows the
    product's cut-out, never a part close-up (cycle 79 review)."""
    return tuple((tenant.get("photo_library.whole_unit_topics") if tenant else None) or DEFAULT_WHOLE_UNIT_TOPICS)


def _feature_topics(topics):
    return {t for t in topics if not t.startswith(("shot:", "setting:"))}


def assign_page_images(page, facts_pack, cartridge_name, *, tenant, exclude_ids=frozenset(),
                       allow_ai_renders, log=None, keep_hero=False, cutout_id=None):
    """Render-time: rewrites every image slot's asset_id from the library.
    The hero gets the best clean exterior of the page's product; every
    other slot gets the unused photo whose tags best match its own text,
    the most specific slot choosing first. A slot with no topic match gets
    the best unused photo of the same product (logged "no topic match").
    Once the library has no unused photo left, the product's storefront
    images are the fallback. A no-op (returns None) when facts_pack offers
    no library photo. Returns {"assignments": [...], "notes": [...]}.

    Cycle 79: `keep_hero` leaves the hero slot as it is (the renderer has
    put the product's cut-out there). A photo whose derivative is not on
    this machine is never assigned (a checkout without the files used to
    assign it and then drop the image at download). A content slot ranks
    equal topic scores by how much of the photo is the topic (a close-up of
    the one thing beats a wide shot that also shows it), and a slot with no
    topic gets a general photo (fewest feature close-ups) rather than a
    close-up of something the slot never mentions.

    Cycle 79 review (run ...-65ji): a slot about the whole unit (cabin, size,
    footprint, capacity -- whole_unit_topics_for) shows the product cut-out
    (`cutout_id`; up to CUTOUT_MAX_USES on a page, the hero included); a part
    close-up (shot "detail") only goes on a slot whose topics name one of
    its parts; any other photo needs a feature or shot topic in common (a
    shared indoor setting is not enough); and a slot nothing matches gets NO
    image (its asset_id is set to None and the item renders without one)
    rather than an unrelated photo."""
    allowed = {a["id"]: a for a in facts_pack.get("assets", [])}
    lib_ids = [a for a in allowed if a.startswith(ID_PREFIX)]
    if not lib_ids:
        return None
    manifest = photos_by_id(load_manifest(tenant, log=log))
    pool = [manifest[i] for i in lib_ids if i in manifest and _usable(manifest[i])
            and local_file(tenant, manifest[i]).exists()]
    if not allow_ai_renders:
        pool = [p for p in pool if not p.get("ai_generated")]
    product = page_model_slug(facts_pack)
    pool = [p for p in pool if p["tags"]["product"] == product
            or (p["tags"]["product"] == UNKNOWN and p["tags"]["model_agnostic"]
                and product in ((p.get("decision") or {}).get("vision_candidates") or [product]))]
    slug = (facts_pack.get("product") or {}).get("slug") or ""
    storefront = [a for a in allowed if slug and a.startswith(f"asset-{slug}-")]
    keywords = topic_keywords_for(tenant)

    slots = _library_slots(page, cartridge_name)
    used = set()
    assignments, notes = [], []

    def take(path, node, new_id, why):
        assignments.append({"path": path, "old_id": node.get("asset_id"), "new_id": new_id, "why": why})
        node["asset_id"] = new_id
        used.add(new_id)

    def free(photos, *, avoid_run=True):
        out = [p for p in photos if p["id"] not in used]
        if avoid_run:
            fresh = [p for p in out if p["id"] not in exclude_ids]
            return fresh or out
        return out

    def fallback(path, node, why):
        rest = [s for s in storefront if s not in used]
        fresh = [s for s in rest if s not in exclude_ids] or rest
        if fresh:
            take(path, node, fresh[0], why + "; storefront fallback")
            notes.append(f"{path}: storefront fallback")
        else:
            notes.append(f"{path}: no unused image left; kept {node.get('asset_id')}")
            used.add(node.get("asset_id"))

    # 1. Hero: best clean exterior of the product itself (never unknown,
    # never a photo that shows the retired mark).
    for path, node, _ctx, is_hero, _heading in slots:
        if not is_hero:
            continue
        if keep_hero:
            used.add(node.get("asset_id"))
            continue
        own = [p for p in free(pool) if p["tags"]["product"] == product and not shows_old_logo(p)]
        exteriors = [p for p in own if p["tags"]["shot"] == "exterior"]
        if exteriors:
            take(path, node, max(exteriors, key=hero_rank)["id"], "hero: best exterior")
        elif storefront and [s for s in storefront if s not in used]:
            fallback(path, node, "hero: library has no clean exterior of this product")
        elif own:
            take(path, node, max(own, key=hero_rank)["id"], "hero: best photo of product (no exterior)")
        else:
            fallback(path, node, "hero")

    # 2. Content slots, most specific first. The first item image (the
    # first content slot in page order) is above the fold too: no photo
    # with the retired mark there.
    whole_unit = set(whole_unit_topics_for(tenant))
    cutout_uses = 1 if (keep_hero and cutout_id) else 0
    content = [(i, s) for i, s in enumerate(slots) if not s[3]]
    first_content_path = content[0][1][0] if content else None
    scored = []
    for order, (path, node, ctx, _h, heading) in content:
        topics = slot_topics_weighted(heading, ctx, keywords)
        heading_feats = _feature_topics(slot_topics(heading, keywords))
        best = max((topic_score(p, topics)[0] for p in pool), default=0)
        scored.append((-best, order, path, node, topics, heading_feats))

    # whole-unit topics go to the cut-out; with no cut-out a room photo may
    # still show them
    unit_only = whole_unit if cutout_id else set()

    def matches(p, topics, wanted):
        """`wanted`: the parts the slot is about -- its heading's when the
        heading names any (a passing "your door" in the body is not a topic),
        else the body's."""
        score, matched = topic_score(p, topics)
        if score <= 0:
            return False
        parts = (_feature_topics(matched) - unit_only) & wanted
        if p["tags"]["shot"] == "detail":
            return bool(parts)
        return bool(parts or [m for m in matched if m.startswith("shot:")])

    for _neg, order, path, node, topics, heading_feats in sorted(scored, key=lambda s: (s[0], s[1])):
        feats = _feature_topics(topics)
        unit_slot = (heading_feats and heading_feats <= whole_unit) or (not heading_feats and feats and feats <= whole_unit)
        if unit_slot and cutout_id and cutout_uses < CUTOUT_MAX_USES:
            take(path, node, cutout_id, f"whole unit ({','.join(sorted(feats))}): product cut-out")
            used.discard(cutout_id)
            cutout_uses += 1
            continue
        wanted = (heading_feats or feats) - unit_only
        candidates = [p for p in free(pool) if matches(p, topics, wanted)]
        if path == first_content_path:
            candidates = [p for p in candidates if not shows_old_logo(p)]
        if candidates:
            # rank on the slot's own parts first (run ...-zpbm: "Ready the same
            # day it's plugged in" went to a heater close-up because the body
            # said "space heater")
            focus = {t: n for t, n in topics.items() if t in wanted} or topics
            best = max(candidates, key=lambda p: (topic_score(p, focus)[0], topic_share(p, focus),
                                                  topic_score(p, topics)[0], quality_rank(p),
                                                  p["tags"]["product"] == product, p["id"]))
            score, matched = topic_score(best, topics)
            take(path, node, best["id"], f"topic match {','.join(matched)} (score {score:g})")
            continue
        if feats & whole_unit and cutout_id and cutout_uses < CUTOUT_MAX_USES:
            take(path, node, cutout_id, "whole unit mentioned, no part photo: product cut-out")
            used.discard(cutout_id)
            cutout_uses += 1
            continue
        assignments.append({"path": path, "old_id": node.get("asset_id"), "new_id": None,
                            "why": "no photo shows this item's topic: no image"})
        node["asset_id"] = None
        notes.append(f"no matching photo for {path}: rendered without an image")
    if log:
        for a in assignments:
            log.event("photos", f"image: {a['path']}: {a['old_id']} -> {a['new_id']} ({a['why']})")
        for n in notes:
            log.event("photos", n)
    return {"assignments": assignments, "notes": notes}


# ---------------------------------------------------------------------------
# Gate: no image of a different product on the page
# ---------------------------------------------------------------------------

def find_wrong_product_image_violations(page, facts_pack, cartridge_name, *, tenant, products=None):
    """Every asset_id on the page that shows a DIFFERENT product than the
    page's own: a library photo tagged another model, or another product's
    storefront image (asset-<other handle>-N). A library photo tagged
    "unknown" may not be the hero. Returns pagechecks-style problem dicts."""
    from . import ground as ground_mod

    product = page_model_slug(facts_pack)
    slug = (facts_pack.get("product") or {}).get("slug") or ""
    if products is None:
        products = json.loads((Path(tenant.claims_dir) / "products.json").read_text())["products"]
    other_handles = {h: product_name_slug(p["name"]) for h, p in products.items() if h != slug}
    manifest = photos_by_id(load_manifest(tenant))
    hero = ground_mod.hero_container(page, cartridge_name)
    problems = []
    for path, node in walk_page(page):
        if not isinstance(node, dict) or not isinstance(node.get("asset_id"), str):
            continue
        asset_id = node["asset_id"]
        photo = manifest.get(asset_id)
        if photo is not None and photo.get("tags"):
            tagged = photo["tags"]["product"]
            if tagged not in (product, UNKNOWN):
                problems.append({"path": f"{path}.asset_id", "issue": (
                    f"image {asset_id} is tagged as the {tagged} model but this page is about {product}; "
                    "use a photo of this page's own product")})
            elif tagged == UNKNOWN and node is hero:
                problems.append({"path": f"{path}.asset_id", "issue": (
                    f"hero image {asset_id} is not tagged as any product; the hero must be a photo of {product}")})
            continue
        for handle, model in other_handles.items():
            if asset_id.startswith(f"asset-{handle}-") and asset_id[len(handle) + 7:].isdigit():
                problems.append({"path": f"{path}.asset_id", "issue": (
                    f"image {asset_id} is a storefront photo of the {model} model but this page is about {product}")})
    return problems


# ---------------------------------------------------------------------------
# Unknown asset id: replaced before the gate, never shipped
# ---------------------------------------------------------------------------

def replace_unknown_asset_id(page, path, facts_pack, *, tenant):
    """The writer named an asset id that is not in facts_pack.assets (the
    2026-10-05 STOP: `...-mini-...-9` on a run whose manifest skipped 9).
    Rewrites it to the best allowed, unused image for that slot -- the
    library photo whose tags best match the slot's own text, else the first
    unused allowed image of the page's product -- and returns
    (old_id, new_id); None (page untouched) when `path` is not an asset_id
    or nothing unused is allowed."""
    segs = re.findall(r"\.([A-Za-z_][\w-]*)|\[(\d+)\]", path or "")
    node, parents = page, []
    try:
        for key, idx in segs:
            parents.append(node)
            node = node[key] if key else node[int(idx)]
    except (KeyError, IndexError, TypeError):
        return None
    if not segs or segs[-1][0] != "asset_id" or not isinstance(node, str):
        return None
    holder = parents[-1]
    allowed_ids = [a["id"] for a in facts_pack.get("assets", [])]
    if node in allowed_ids:
        return None
    on_page = {n["asset_id"] for _p, n in walk_page(page) if isinstance(n, dict) and n.get("asset_id")}
    free_ids = [i for i in allowed_ids if i not in on_page]
    if not free_ids:
        return None
    # The slot's own text: the nearest dict above the image that has a heading/text.
    context = heading = ""
    for parent in reversed(parents[:-1]):
        if isinstance(parent, dict) and (parent.get("heading") or parent.get("text") or parent.get("title")):
            context = " ".join(str(parent.get(k) or "") for k in ("heading", "title", "text"))
            heading = str(parent.get("heading") or parent.get("title") or "")
            break
    manifest = photos_by_id(load_manifest(tenant))
    product = page_model_slug(facts_pack)
    keywords = topic_keywords_for(tenant)
    topics = slot_topics_weighted(heading, context, keywords)
    lib = [manifest[i] for i in free_ids if i in manifest and manifest[i].get("tags")
           and manifest[i]["tags"]["product"] in (product, UNKNOWN)]
    if lib:
        new_id = max(lib, key=lambda p: (topic_score(p, topics)[0], p["tags"]["product"] == product,
                                         quality_rank(p), p["id"]))["id"]
    else:
        new_id = free_ids[0]
    old_id = node
    holder["asset_id"] = new_id
    return old_id, new_id


# ---------------------------------------------------------------------------
# Contact sheet
# ---------------------------------------------------------------------------

def contact_sheet(tenant, out_path, *, cols=6, thumb=260):
    """One JPEG of every photo: thumbnail, id, product tag, shot."""
    from PIL import ImageDraw, ImageFont

    photos = load_manifest(tenant).get("photos", [])
    photos = sorted(photos, key=lambda p: ((p.get("tags") or {}).get("product", "~"), p["id"]))
    rows = (len(photos) + cols - 1) // cols
    cell_h = thumb + 58
    sheet = Image.new("RGB", (cols * thumb, rows * cell_h), "white")
    draw = ImageDraw.Draw(sheet)
    try:
        font = ImageFont.truetype("DejaVuSans.ttf", 13)
    except OSError:
        font = ImageFont.load_default()
    for i, p in enumerate(photos):
        x, y = (i % cols) * thumb, (i // cols) * cell_h
        with Image.open(local_file(tenant, p)) as im:
            im = im.convert("RGB")
            im.thumbnail((thumb - 6, thumb - 6))
            sheet.paste(im, (x + (thumb - im.width) // 2, y + (thumb - im.height) // 2))
        t = p.get("tags") or {}
        line1 = p["id"].replace(ID_PREFIX, "")
        prod = t.get("product", "untagged")
        if prod == UNKNOWN and t.get("model_agnostic"):
            prod = "unknown (agnostic)"
        line2 = f"{prod} {t.get('product_confidence', '')}"
        line3 = f"{t.get('shot', '')}{' AI' if p.get('ai_generated') else ''}{' TEXT' if t.get('has_text_overlay') else ''}"
        draw.text((x + 4, y + thumb), line1, fill="black", font=font)
        draw.text((x + 4, y + thumb + 16), line2, fill="red" if prod.startswith(UNKNOWN) else "darkgreen", font=font)
        draw.text((x + 4, y + thumb + 32), line3, fill="black", font=font)
    sheet.save(out_path, quality=78)
    return out_path
