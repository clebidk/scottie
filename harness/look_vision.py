"""Cycle 102: import a look from screenshots (harness/look_import.py calls this).

The cycle 87/90 import read a page's HTML only. On the owner's four DTC
listicles that observer was wrong where it mattered (docs/FIXLOG.md cycle
102): a page built by script observed as five sections, CSS colour names
read two light editorial pages as dark pages, hidden helpers and wrappers
became sections and several h1s became several heroes -- while the hand
conversions, made by a person LOOKING at the page, matched. This module lets
the import look at the page too:

  - image intake: the review site's upload (save_images: PNG, JPG, WebP,
    HEIC as on the Photos page; at most MAX_FILES files of MAX_FILE_BYTES)
    and the CLI's --image files;
  - capture: a URL or an .html source is also screenshotted at 390 and 1280
    (capture_page, harness/capture.py's headless Chromium, the design gate's
    browser) from a SANITISED copy -- scripts, frames and event attributes
    dropped as in the observer, the page's own CSS and images loaded through
    a <base>;
  - prepare: every image is cut into tiles of at most TILE_ASPECT (height
    per width), the tiles are packed in reading order into at most
    MAX_IMAGES sheets, and each sheet is downscaled to LONG_SIDE px on its
    long side (the cost cap: about 1.6k input tokens a sheet);
  - observe: ONE call to MODEL with the sheets and a structured prompt; the
    answer is normalised into the SAME observation shape the HTML observer
    produces (sections with role, ground, layout, pattern names from
    look_design.PATTERNS; global palette, type scale, photo and CTA
    cadence, special elements), so exemplar choice, the brief, the
    generation, the validator and the fidelity score run unchanged;
  - merge: with both an HTML and an image observation, the image wins on
    section order, rhythm (roles, grounds, layout, patterns, cadence) and
    palette; the HTML wins on exact CSS values (type, width, padding,
    radius, scrim), DOM skeletons and font names;
  - render shots: the render of a candidate at 390 (the result card,
    and the side-by-side the revise round sends).

No model call happens in this module except `observe`.
"""
import base64
import copy
import difflib
import math
import os
import re
import secrets
import shutil
from pathlib import Path

from PIL import Image, ImageOps

from . import capture
from . import look_design
from . import pricing
from .anthropic_client import adaptive_kwargs
from .jsonutil import extract_json

MB = 1024 * 1024
# The model for the vision observation and the generation (owner rule: the
# strongest vision model the harness prices; it is the quality lever).
MODEL = "claude-opus-5-5"
# Opus 5.5 cannot run with thinking disabled; "medium" keeps an import under
# about one dollar (docs/LISTICLE-SITE.md "Import a page", the cost line).
EFFORT = "medium"
OBSERVE_MAX_TOKENS = 16000
MAX_FILES = 10
MAX_FILE_BYTES = 15 * MB
MAX_IMAGES = 6
LONG_SIDE = 1280
TILE_ASPECT = 2.2
SHEET_GAP = 16
# The HTML a URL import hands the screenshot browser (the observer's own cap
# stays look_import.MAX_SOURCE_BYTES; a bigger page is imported from its
# screenshots alone).
MAX_CAPTURE_BYTES = 10 * MB
PHONE = (390, 844)
DESKTOP = (1280, 900)
MAX_SHOT_PX = 12000
PENDING_PREFIX = "img-"
SOURCE_PREFIX = "source-"
SOURCE_THUMB = "source-thumb.jpg"
RENDER_SHOT = "render-390.png"
_BATCH_RE = re.compile(r"^img-[a-f0-9]{16}$")

ROLES = ("nav", "hero", "content", "benefits", "proof", "comparison", "gallery", "faq", "cta", "footer")
GROUNDS = ("light", "dark", "tint")
LAYOUTS = {"stack": "block", "split": "grid", "grid": "grid", "row": "flex", "overlay": "block"}
IMAGE_TREATMENTS = ("full-bleed", "contained", "masked", "overlay")
RADII = ("square", "4px", "pill", "round")
FACES = {"display-wide": "display-wide", "condensed": "display-wide", "display": "display-acid",
         "sans": "sans", "heading": "heading", "serif": "serif", "slab": "heading", "script": "serif"}
WEIGHTS = {"light": "300", "regular": "400", "medium": "500", "bold": "700", "black": "900"}
# The tenant token for each palette role (look_import_rules.md "Tokens").
PALETTE_TOKENS = {"ground": "--pk-bg", "ink": "--pk-ink", "body": "--pk-body", "muted": "--pk-muted",
                  "accent": "--pk-red", "band": "--pk-dark", "line": "--pk-line", "cta": "--pk-ink"}
WIDTHS = {"narrow": 720, "medium": 1024, "wide": 1280}
# Patterns the HTML reads exactly from the CSS/DOM; merged in even when the
# screenshots did not show them (a fixed bar, a <details> FAQ, text-transform).
HTML_EXACT_PATTERNS = ("sticky-bar", "faq-accordion", "caps-headline")
MAX_TEXT = 120


class VisionError(Exception):
    """An image the import cannot use, or a vision answer it cannot read."""


# ---------------------------------------------------------------------------
# Intake (the review site's upload, the CLI's files)
# ---------------------------------------------------------------------------

def _heic_opener():
    from . import photo_uploads

    return photo_uploads.heic_supported()


def pending_dir(tenant, batch):
    from . import jobs

    if not _BATCH_RE.match(str(batch or "")):
        raise VisionError(f"bad pending image batch {batch!r}")
    return Path(tenant.jobs_dir) / jobs.LOOK_IMPORT_PENDING / batch


def save_images(tenant, files):
    """Checks and keeps one upload of screenshots for a look_import job, as
    the Photos page does (photo_uploads.save_batch): `files` is [(stream,
    filename)]; the type comes from each file's first bytes and must agree
    with its extension. All or nothing. Returns the batch name."""
    from . import photo_uploads as pu

    files = [(s, n) for s, n in files if s is not None and pu._base_name(n)]
    if not files:
        raise VisionError("choose one or more screenshots")
    if len(files) > MAX_FILES:
        raise VisionError(f"at most {MAX_FILES} screenshots at once; you chose {len(files)}")
    batch = PENDING_PREFIX + secrets.token_hex(8)
    directory = pending_dir(tenant, batch)
    directory.mkdir(parents=True)
    heic_ok = None
    try:
        for index, (stream, filename) in enumerate(files, 1):
            name = pu._base_name(filename)
            given = os.path.splitext(name)[1].lower()
            if given not in pu.ALLOWED_EXTENSIONS:
                raise VisionError(f"{name}: not an image this import takes (png, jpg, webp or heic)")
            head = stream.read(64)
            ext = pu.sniff_photo(head)
            if ext is None or ext != pu.ALLOWED_EXTENSIONS[given]:
                raise VisionError(f"{name}: the content is not a valid {given[1:]} image (or does not match its "
                                  "extension)")
            if ext == ".heic":
                heic_ok = _heic_opener() if heic_ok is None else heic_ok
                if not heic_ok:
                    raise VisionError(f"{name}: this server cannot read HEIC images yet. Export it as JPEG or PNG "
                                      "and upload it again")
            dest = directory / f"{index:02d}{ext}"
            size = 0
            with open(dest, "wb") as fh:
                chunk = head
                while chunk:
                    size += len(chunk)
                    if size > MAX_FILE_BYTES:
                        raise VisionError(f"{name} is over the {MAX_FILE_BYTES // MB} MB limit for one image")
                    fh.write(chunk)
                    chunk = stream.read(MB)
    except BaseException:
        shutil.rmtree(directory, ignore_errors=True)
        raise
    return batch


def check_image_paths(paths):
    """The CLI's --image files: at most MAX_FILES, each readable, at most
    MAX_FILE_BYTES, a PNG/JPG/WebP/HEIC by its bytes. Returns [Path]."""
    from . import photo_uploads as pu

    paths = [Path(p) for p in paths or []]
    if not paths:
        raise VisionError("give one or more images")
    if len(paths) > MAX_FILES:
        raise VisionError(f"at most {MAX_FILES} images; you gave {len(paths)}")
    for path in paths:
        try:
            size = path.stat().st_size
            with open(path, "rb") as fh:
                head = fh.read(64)
        except OSError as e:
            raise VisionError(f"cannot read {path}: {e}") from e
        if size > MAX_FILE_BYTES:
            raise VisionError(f"{path.name} is over the {MAX_FILE_BYTES // MB} MB limit for one image")
        ext = pu.sniff_photo(head)
        if ext is None:
            raise VisionError(f"{path.name}: not a png, jpg, webp or heic image")
        if ext == ".heic" and not _heic_opener():
            raise VisionError(f"{path.name}: this host cannot read HEIC images (pillow-heif is not installed)")
    return paths


def open_image(path):
    """An RGB image, rotated by its EXIF orientation (a phone screenshot)."""
    path = Path(path)
    if path.suffix.lower() in (".heic", ".heif"):
        _heic_opener()
    try:
        with Image.open(path) as im:
            im = ImageOps.exif_transpose(im)
            return im.convert("RGB")
    except Exception as e:  # noqa: BLE001 -- any undecodable file is a refusal reason
        raise VisionError(f"{path.name}: cannot read this image ({type(e).__name__})") from e


# ---------------------------------------------------------------------------
# Prepare: tiles -> at most MAX_IMAGES sheets, LONG_SIDE px on the long side
# ---------------------------------------------------------------------------

def tiles(image):
    """The image cut top to bottom into pieces at most TILE_ASPECT times as
    tall as wide (equal heights)."""
    w, h = image.size
    n = max(1, math.ceil(h / max(1, int(w * TILE_ASPECT))))
    step = math.ceil(h / n)
    return [image.crop((0, i * step, w, min(h, (i + 1) * step))) for i in range(n)]


def _sheet(parts):
    height = min(p.size[1] for p in parts)
    scaled = [p if p.size[1] == height else p.resize((max(1, round(p.size[0] * height / p.size[1])), height),
                                                      Image.LANCZOS) for p in parts]
    width = sum(p.size[0] for p in scaled) + SHEET_GAP * (len(scaled) - 1)
    sheet = Image.new("RGB", (width, height), (255, 255, 255))
    x = 0
    for p in scaled:
        sheet.paste(p, (x, 0))
        x += p.size[0] + SHEET_GAP
    return sheet


def _fit(image, long_side=LONG_SIDE):
    w, h = image.size
    if max(w, h) <= long_side:
        return image
    scale = long_side / max(w, h)
    return image.resize((max(1, round(w * scale)), max(1, round(h * scale))), Image.LANCZOS)


def _groups(cuts, max_images):
    """The tiles of each image packed into sheets: [[(tile, label, i, n)]].
    With at most max_images images, each image gets its own sheets (one at
    least, the rest of the budget to the images with the most tiles per
    sheet), so a phone tile and a desktop tile never share a sheet; with
    more images, all tiles are packed in reading order."""
    if len(cuts) > max_images:
        flat = [piece for cut in cuts for piece in cut]
        per = math.ceil(len(flat) / max_images)
        return [flat[i:i + per] for i in range(0, len(flat), per)]
    alloc = [1] * len(cuts)
    for _ in range(max_images - len(cuts)):
        open_ = [k for k, cut in enumerate(cuts) if alloc[k] < len(cut)]
        if not open_:
            break
        k = max(open_, key=lambda j: len(cuts[j]) / alloc[j])
        alloc[k] += 1
    out = []
    for cut, n in zip(cuts, alloc):
        per = math.ceil(len(cut) / n)
        out += [cut[i:i + per] for i in range(0, len(cut), per)]
    return out


def prepare(images, out_dir, *, max_images=MAX_IMAGES, prefix=SOURCE_PREFIX):
    """Writes the sheets the model sees as out_dir/<prefix>N.jpg (JPEG 85).
    `images` = [(PIL image, label)] in reading order. Returns [{"file",
    "label", "width", "height"}]."""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    cuts = []
    for image, label in images:
        cut = tiles(image)
        cuts.append([(t, label, i + 1, len(cut)) for i, t in enumerate(cut)])
    out = []
    for n, group in enumerate(_groups(cuts, max_images) if cuts else [], 1):
        sheet = _fit(_sheet([g[0] for g in group]))
        name = f"{prefix}{n}.jpg"
        sheet.save(out_dir / name, format="JPEG", quality=85)
        bits = []
        for _t, label, i, total in group:
            bits.append(f"{label}, part {i} of {total}" if total > 1 else label)
        text = "; ".join(bits) + (" (side by side, left to right)" if len(group) > 1 else "")
        out.append({"file": name, "label": text, "width": sheet.size[0], "height": sheet.size[1]})
    return out


def write_thumb(image, path, size=PHONE):
    """The first phone screen of `image` at size[0] px wide (the result card
    and the Library chip show it beside the look's render)."""
    w, h = image.size
    scale = size[0] / w
    resized = image.resize((size[0], max(1, round(h * scale))), Image.LANCZOS)
    resized.crop((0, 0, size[0], min(resized.size[1], size[1]))).save(path, format="JPEG", quality=82)


def b64(path):
    return base64.standard_b64encode(Path(path).read_bytes()).decode("ascii")


def image_blocks(sheets, folder, heading):
    """Message content blocks: a heading, then each sheet's label and image."""
    blocks = [{"type": "text", "text": heading}]
    for s in sheets:
        blocks.append({"type": "text", "text": s["label"]})
        blocks.append({"type": "image", "source": {"type": "base64", "media_type": "image/jpeg",
                                                   "data": b64(Path(folder) / s["file"])}})
    return blocks


# ---------------------------------------------------------------------------
# Capture a page (URL or .html) and a candidate render
# ---------------------------------------------------------------------------

def find_browser():
    """harness/capture.py's browser (tests replace this: no browser)."""
    return capture.find_browser()


def capture_copy(html_text, base_url=None):
    """The sanitised document the screenshot browser loads: no script,
    frame or event attribute (look_import.sanitise), lazy images made
    eager (data-src -> src), a <base> on the page's URL so its own CSS and
    images load."""
    from . import look_import as li

    tree, _dropped, _scripts = li.sanitise(li.parse_html(html_text))
    for el in tree.iter():
        if isinstance(el.tag, str) and el.tag.startswith("{"):
            # html5lib puts inline SVG in its namespace; written back as
            # "ns0:svg" a browser would not draw it
            el.tag = el.tag.split("}", 1)[1]
        for key in [k for k in el.attrib if k.startswith("{")]:
            ns, local = key[1:].split("}", 1)
            el.attrib[("xlink:" if ns.endswith("/xlink") else "") + local] = el.attrib.pop(key)
        if li._tag(el) in ("img", "source"):
            for lazy, real in (("data-src", "src"), ("data-srcset", "srcset")):
                if el.get(lazy) and not el.get(real):
                    el.set(real, el.get(lazy))
            if el.get("loading") == "lazy":
                el.set("loading", "eager")
    head = tree.find(".//head")
    if base_url and head is not None:
        import xml.etree.ElementTree as ET

        base = ET.Element("base", {"href": base_url})
        head.insert(0, base)
    return "<!doctype html>" + li.sanitised_html(tree)


def screenshot_full(browser, html_path, png_path, size, *, tenant=None):
    """A full-page shot of html_path at size[0] wide (its height measured by
    capture.probe, at most MAX_SHOT_PX). True when written."""
    probe = capture.probe(browser, html_path, width=size[0], height=size[1], tenant=tenant)
    height = min(MAX_SHOT_PX, max(size[1], int((probe or {}).get("height") or size[1])))
    return capture.screenshot(browser, html_path, png_path, width=size[0], height=height, tenant=tenant,
                              screen_height=size[1])


def capture_page(html_text, base_url, work_dir, *, browser=None):
    """[(png path, label)]: the page at 390 and at 1280, full height, from
    capture_copy. [] without a browser or when both shots fail."""
    browser = browser or find_browser()
    if not browser:
        return []
    work_dir = Path(work_dir)
    work_dir.mkdir(parents=True, exist_ok=True)
    page = work_dir / "page.html"
    page.write_text(capture_copy(html_text, base_url))
    shots = []
    for size, name, label in ((PHONE, "page-390.png", "The page on a phone, 390 px wide"),
                              (DESKTOP, "page-1280.png", "The page on a desktop, 1280 px wide")):
        if screenshot_full(browser, page, work_dir / name, size):
            shots.append((work_dir / name, label))
    return shots


# ---------------------------------------------------------------------------
# The vision observation (one model call)
# ---------------------------------------------------------------------------

def _role_lines():
    return "\n".join(f"- `{k}`: {v}" for k, v in (
        ("nav", "a header / logo / menu row at the very top"),
        ("hero", "the first screen with the page's one big headline (only one hero)"),
        ("content", "a text section: an article paragraph block, or ONE item of a list article (its number, "
                    "heading, body and photo)"),
        ("benefits", "a list or grid of three or more short repeated items in ONE section (checks, icons, cards)"),
        ("proof", "reviews, testimonials, a quote, a rating, press logos"),
        ("comparison", "a comparison table or an us-versus-them block"),
        ("gallery", "mostly images (three or more) with little text"),
        ("faq", "questions and answers"),
        ("cta", "a short band whose job is the button (an offer band, a closing CTA)"),
        ("footer", "the links and legal text at the very end"),
    ))


def observe_prompt(cartridge):
    pats = "\n".join(f"- `{name}`: {text}" for name, text in look_design.PATTERNS.items())
    return f"""You observe the DESIGN of a web page from screenshots, for a harness that rebuilds the page's
structure as a new {cartridge} look in another brand's design language. You report how the page is BUILT:
the sections top to bottom, each section's job, ground, layout, images and buttons, the distinctive patterns,
the palette as roles, the type scale, and the rhythm (where photos and buttons recur).

Never copy the page's words, brand names, product names, prices, offers or claims. Every free-text field is
a neutral description of the layout in at most 12 words ("numbered item: heading, body, wide photo below").

Screenshots may come as several images: parts of one long page, cut top to bottom, sometimes side by side
on one sheet (read left to right). A phone and a desktop capture of the same page may both be present:
report the page ONCE, top to bottom, from the phone view; use the desktop view for desktop columns.
Skip cookie banners, chat bubbles and browser chrome.

## Section roles (use exactly these)
{_role_lines()}

## Pattern names (use only these, on the section that shows them)
{pats}

## Output
Return ONLY one JSON object, no prose, no code fence:
{{"ground": "light" | "dark",
 "content_width": "narrow" | "medium" | "wide" | "full",
 "palette": [{{"role": "ground" | "ink" | "body" | "muted" | "accent" | "band" | "line" | "cta",
              "tone": "light" | "dark" | "mid", "hue": "<one word, e.g. red, navy, cream, grey>"}}],
 "type": {{"headline": {{"case": "upper" | "sentence" | "title", "weight": "light" | "regular" | "medium" | "bold" | "black",
                       "face": "sans" | "serif" | "display-wide" | "condensed" | "slab" | "script",
                       "size_phone_px": <integer>}},
          "body": {{"face": "sans" | "serif", "size_phone_px": <integer>}},
          "accent": "<how emphasis is set, e.g. accent phrase in italic serif, accent colour>"}},
 "photo_cadence": "<where photos recur, e.g. one photo per numbered item, full width>",
 "cta_cadence": "<where buttons recur, e.g. after items 3 and 6, in a sticky bar, at the end>",
 "sections": [{{"role": "<a role above>", "what": "<neutral layout description>",
               "bg": "light" | "dark" | "tint", "heading": "h1" | "h2" | "h3" | "none",
               "layout": "stack" | "split" | "grid" | "row" | "overlay",
               "cols_phone": <integer>, "cols_desktop": <integer>, "items": <repeated items, 0 if none>,
               "numbered": true | false, "images": <integer>,
               "image": "none" | "full-bleed" | "contained" | "masked" | "overlay",
               "radius": "square" | "4px" | "pill" | "round", "cta": <buttons in the section>,
               "words": <approximate words of text>, "pinned": "none" | "top" | "bottom",
               "patterns": ["<pattern names>"]}}],
 "special": ["<other distinctive elements, at most 8 words each, e.g. reading progress bar, countdown>"]}}

Report every section in order (a list article's items are one `content` section EACH). A sticky bottom bar
is a section with "pinned": "bottom" and the pattern `sticky-bar`, listed last."""


def _call(client, model, system, content, budget, log, stage):
    budget.check()
    resp = client.messages.create(model=model, max_tokens=OBSERVE_MAX_TOKENS, system=system,
                                  messages=[{"role": "user", "content": content}], **model_kwargs(model))
    usage = resp.usage
    cache_w = getattr(usage, "cache_creation_input_tokens", 0) or 0
    cache_r = getattr(usage, "cache_read_input_tokens", 0) or 0
    budget.record_call(usage.input_tokens, usage.output_tokens)
    family = pricing.family_id(model)
    if log:
        log.call(stage, family, usage.input_tokens, usage.output_tokens,
                 cache_creation_input_tokens=cache_w, cache_read_input_tokens=cache_r)
    cost = pricing.calculate_cost(family, usage.input_tokens, usage.output_tokens,
                                  cache_creation_input_tokens=cache_w, cache_read_input_tokens=cache_r)
    if getattr(resp, "stop_reason", None) == "max_tokens":
        raise VisionError(f"the vision answer was cut off at {OBSERVE_MAX_TOKENS} tokens")
    text = "".join(getattr(b, "text", "") for b in resp.content if getattr(b, "type", "text") == "text")
    return text, cost


def model_kwargs(model):
    """Opus 5.5 runs with adaptive thinking at EFFORT (thinking cannot be
    disabled there); other models as harness/anthropic_client.py says."""
    from .anthropic_client import thinking_kwargs

    if pricing.family_id(model) == "claude-opus-5-5":
        return adaptive_kwargs(model, EFFORT)
    return thinking_kwargs(model)


def observe(sheets, folder, *, client, cartridge, origin, source_kind, budget, model=MODEL, log=None):
    """(observation, cost): one vision call on the prepared sheets, the
    answer normalised by to_observation."""
    content = image_blocks(sheets, folder, f"{len(sheets)} screenshot image(s) of the page, top to bottom:")
    content.append({"type": "text", "text": "Observe this page's design and return the JSON object."})
    text, cost = _call(client, model, observe_prompt(cartridge), content, budget, log, "look_import.observe")
    try:
        answer = extract_json(text)
    except (ValueError, TypeError) as e:
        exc = VisionError(f"the vision answer is not JSON: {e}")
        exc.cost_usd = cost
        raise exc from e
    try:
        obs = to_observation(answer, origin=origin, source_kind=source_kind, images=len(sheets))
    except VisionError as e:
        e.cost_usd = cost
        raise
    return obs, cost


def _int(value, lo=0, hi=999, default=0):
    try:
        return max(lo, min(hi, int(round(float(value)))))
    except (TypeError, ValueError):
        return default


def _word(value, allowed, default):
    value = str(value or "").strip().lower()
    return value if value in allowed else default


def _short(value, words=12):
    text = " ".join(str(value or "").split()[:words])
    return text[:MAX_TEXT]


def to_observation(answer, *, origin, source_kind="images", images=0):
    """The vision answer in the HTML observer's shape (look_import.observe):
    sections with index, role, counts, cta, background, style, skeleton and
    patterns; global; patterns; brief. Raises VisionError when the answer
    has no sections."""
    from . import look_import as li

    if not isinstance(answer, dict) or not isinstance(answer.get("sections"), list) or not answer["sections"]:
        raise VisionError("the vision answer has no sections")
    sections, patterns, pinned_at = [], {}, []
    raw = [s for s in answer["sections"] if isinstance(s, dict)][:li.MAX_SECTIONS]
    t_head = answer.get("type", {}).get("headline") if isinstance(answer.get("type"), dict) else None
    t_head = t_head if isinstance(t_head, dict) else {}
    for i, s in enumerate(raw):
        role = _word(s.get("role"), ROLES, "content")
        ground = _word(s.get("bg"), GROUNDS, "light")
        bg = "dark" if ground == "dark" else "light"
        layout_word = _word(s.get("layout"), LAYOUTS, "stack")
        cols, cols_d = _int(s.get("cols_phone"), 1, 6, 1), _int(s.get("cols_desktop"), 1, 6, 1)
        if layout_word == "split":
            cols_d = max(cols_d, 2)
        style = {"bg": bg, "text": "light" if bg == "dark" else "dark", "contrast": "high",
                 "layout": {"display": LAYOUTS[layout_word], "cols": cols, "cols_desktop": cols_d}}
        if ground == "tint":
            style["tint"] = True
        image = _word(s.get("image"), IMAGE_TREATMENTS, None)
        if layout_word == "overlay" and image is None:
            image = "overlay"
        if image:
            style["image"] = image
        radius = _word(s.get("radius"), RADII, None)
        if radius:
            style["radius"] = radius
        pinned = _word(s.get("pinned"), ("top", "bottom"), None)
        if pinned:
            style["pinned"] = True
            pinned_at.append(pinned)
        heading = _word(s.get("heading"), ("h1", "h2", "h3"), None)
        has_hero = any(x["role"] == "hero" for x in sections)
        if role == "hero" and has_hero:
            role = "content"   # one hero, as the HTML observer and the fidelity score count it
        elif heading == "h1" and not has_hero and role != "nav":
            role = "hero"
        if role == "hero":
            if t_head:
                style["type"] = {"el": "h1", "size": f"{_int(t_head.get('size_phone_px'), 12, 160, 40)}px",
                                 "weight": WEIGHTS.get(_word(t_head.get("weight"), WEIGHTS, "bold")),
                                 "font": FACES[_word(t_head.get("face"), FACES, "sans")]}
                if _word(t_head.get("case"), ("upper", "sentence", "title"), "sentence") == "upper":
                    style["type"]["transform"] = "uppercase"
        items = _int(s.get("items"), 0, 40)
        n_cta = _int(s.get("cta"), 0, 9)
        names = [p for p in (s.get("patterns") or []) if p in look_design.PATTERNS]
        if pinned == "bottom" and "sticky-bar" not in names and n_cta:
            names.append("sticky-bar")
        what = _short(s.get("what"))
        for name in names:
            patterns.setdefault(name, {"name": name, "section": i, "evidence": f"seen in the screenshots: {what}"})
        sections.append({
            "index": i, "role": role, "tag": "section",
            "heading_level": {"h1": 1, "h2": 2, "h3": 3}.get(heading), "headings": 1 if heading else 0,
            "heading_words": 0, "words": _int(s.get("words"), 0, 5000), "images": _int(s.get("images"), 0, 60),
            "image_aspects": [], "videos": 0, "lists": 1 if items >= 3 and role == "benefits" else 0,
            "list_items": items if role == "benefits" else 0, "table": role == "comparison", "blockquotes": 0,
            "details": 0, "question_headings": 0, "form": False,
            "cta": {"count": n_cta, "links": 0, "type": "button" if n_cta else None}, "links": n_cta,
            "background": bg, "background_source": "image", "columns_desktop": cols_d if cols_d > 1 else None,
            "repeated_items": items, "numbered": bool(s.get("numbered")), "what": what,
            "style": style, "skeleton": [], "patterns": [],
        })
    ground = _word(answer.get("ground"), ("light", "dark"), "light")
    content = [s for s in sections if s["role"] not in ("nav", "footer") and not s["style"].get("pinned")]
    if ground == "dark" and content and sum(s["background"] == "dark" for s in content) >= 0.7 * len(content):
        patterns.setdefault("dark-page", {"name": "dark-page", "section": None,
                                          "evidence": "seen in the screenshots: dark ground on most sections"})
    hero = next((s for s in sections if s["role"] == "hero"), None)
    if hero and hero["background"] == "dark":
        patterns.setdefault("dark-hero", {"name": "dark-hero", "section": hero["index"],
                                          "evidence": "seen in the screenshots: a dark first screen"})
    ordered = [patterns[n] for n in look_design.PATTERN_NAMES if n in patterns]
    for s in sections:
        s["patterns"] = [p["name"] for p in ordered if p["section"] == s["index"]]
    palette = []
    for item in answer.get("palette") or []:
        if isinstance(item, dict) and item.get("role") in PALETTE_TOKENS:
            palette.append({"role": item["role"], "tone": _word(item.get("tone"), ("light", "dark", "mid"), "mid"),
                            "hue": _short(item.get("hue"), 2), "token": PALETTE_TOKENS[item["role"]]})
    typ = answer.get("type") if isinstance(answer.get("type"), dict) else {}
    body = typ.get("body") if isinstance(typ.get("body"), dict) else {}
    type_scale = {
        "headline": {"case": _word(t_head.get("case"), ("upper", "sentence", "title"), "sentence"),
                     "weight": _word(t_head.get("weight"), WEIGHTS, "bold"),
                     "font": FACES[_word(t_head.get("face"), FACES, "sans")],
                     "size_phone_px": _int(t_head.get("size_phone_px"), 12, 160, 40)},
        "body": {"font": FACES[_word(body.get("face"), ("sans", "serif"), "sans")],
                 "size_phone_px": _int(body.get("size_phone_px"), 10, 30, 16)},
        "accent": _short(typ.get("accent")),
    }
    words = sum(s["words"] for s in sections)
    total_images = sum(s["images"] for s in sections)
    width = _word(answer.get("content_width"), ("narrow", "medium", "wide", "full"), "wide")
    obs = {
        "version": 2,
        "observed_from": "images",
        "source": {"kind": source_kind, "origin": str(origin or "")[:120], "bytes": 0, "images": images},
        "sections": sections,
        "global": {
            "sections": len(sections), "content_sections": len(content), "words": words, "images": total_images,
            "headings": {}, "fonts": [], "max_width_px": WIDTHS.get(width),
            "nav": bool(sections and sections[0]["role"] == "nav"),
            "sticky": sorted(set(pinned_at)),
            "cta_buttons": sum(s["cta"]["count"] for s in sections), "cta_phrases": 1,
            "dark_sections": sum(1 for s in sections if s["background"] == "dark"),
            "dark_page": "dark-page" in patterns,
            "words_per_section": round(words / max(1, len(content))),
            "html_bytes": 0, "css_bytes": 0, "stylesheets_not_read": 0,
            "scripts": {"inline": 0, "external": 0, "inline_bytes": 0}, "estimated_weight_kb": 0,
            "palette": palette, "type_scale": type_scale,
            "photo_cadence": _short(answer.get("photo_cadence"), 16),
            "cta_cadence": _short(answer.get("cta_cadence"), 16),
            "special": [_short(x, 8) for x in (answer.get("special") or []) if str(x or "").strip()][:8],
        },
        "dropped": {},
        "notes": [],
        "patterns": ordered,
    }
    obs["brief"] = brief(obs)
    return obs


def brief(observation):
    """look_design.design_brief plus the rhythm the screenshots show."""
    text = look_design.design_brief(observation)
    g = observation["global"]
    extra = []
    numbered = [s["index"] for s in observation["sections"] if s.get("numbered")]
    if numbered:
        extra.append(f"{len(numbered)} numbered section(s).")
    if g.get("photo_cadence"):
        extra.append(f"Photos: {g['photo_cadence']}.")
    if g.get("cta_cadence"):
        extra.append(f"Buttons: {g['cta_cadence']}.")
    ts = g.get("type_scale") or {}
    head = ts.get("headline") or {}
    if head:
        extra.append(f"Headline {head.get('case')} case, {head.get('weight')}, {head.get('font')} face, about "
                     f"{head.get('size_phone_px')}px on the phone.")
    if ts.get("accent"):
        extra.append(f"Emphasis: {ts['accent']}.")
    roles = [f"{p['role']} {p['tone']} {p['hue']}".strip() for p in g.get("palette") or []]
    if roles:
        extra.append("Palette roles: " + ", ".join(roles) + ".")
    if g.get("special"):
        extra.append("Also: " + "; ".join(g["special"]) + ".")
    return " ".join([text] + extra)


# ---------------------------------------------------------------------------
# Merge (HTML + images)
# ---------------------------------------------------------------------------

def merge(image_obs, html_obs):
    """One observation from both: the IMAGE observation's section order,
    roles, grounds, layout, patterns, cadence and palette; the HTML
    observation's exact CSS values (headline type, width, padding, radius,
    scrim), DOM skeleton and image aspects for each section matched by role
    order, its font names, page width, sticky elements and weight, and the
    patterns it reads exactly (HTML_EXACT_PATTERNS)."""
    merged = copy.deepcopy(image_obs)
    img_s, html_s = merged["sections"], html_obs["sections"]
    matcher = difflib.SequenceMatcher(None, [s["role"] for s in img_s], [s["role"] for s in html_s],
                                      autojunk=False)
    pairs = {}
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == "equal" or (tag == "replace" and i2 - i1 == j2 - j1):
            pairs.update(zip(range(i1, i2), range(j1, j2)))
    for i, j in pairs.items():
        s, h = img_s[i], html_s[j]
        hst = h.get("style") or {}
        for key in ("type", "max_width", "pad", "radius", "scrim"):
            if hst.get(key) is not None:
                s["style"][key] = copy.deepcopy(hst[key])
        s["skeleton"] = list(h.get("skeleton") or [])
        s["image_aspects"] = list(h.get("image_aspects") or [])
        s["html_section"] = j
    back = {j: i for i, j in pairs.items()}
    names = {p["name"] for p in merged["patterns"]}
    for p in html_obs.get("patterns") or []:
        if p["name"] in names or p["name"] not in HTML_EXACT_PATTERNS:
            continue
        section = back.get(p["section"]) if p.get("section") is not None else None
        if p.get("section") is not None and section is None:
            continue
        merged["patterns"].append({"name": p["name"], "section": section,
                                   "evidence": f"from the HTML: {p.get('evidence', '')}"})
        names.add(p["name"])
    merged["patterns"] = [p for n in look_design.PATTERN_NAMES for p in merged["patterns"] if p["name"] == n]
    for s in img_s:
        s["patterns"] = [p["name"] for p in merged["patterns"] if p["section"] == s["index"]]
    g, hg = merged["global"], html_obs["global"]
    for key in ("fonts", "headings", "html_bytes", "css_bytes", "stylesheets_not_read", "scripts",
                "estimated_weight_kb"):
        if key in hg:
            g[key] = copy.deepcopy(hg[key])
    if hg.get("max_width_px"):
        g["max_width_px"] = hg["max_width_px"]
    g["sticky"] = sorted(set(g.get("sticky") or []) | set(hg.get("sticky") or []))
    g["dark_page"] = "dark-page" in names
    merged["source"] = {**copy.deepcopy(html_obs["source"]), "images": image_obs["source"].get("images", 0)}
    merged["dropped"] = copy.deepcopy(html_obs.get("dropped") or {})
    merged["notes"] = list(html_obs.get("notes") or [])
    merged["observed_from"] = "images+html"
    merged["merge"] = {"from_images": ["section order", "roles", "grounds", "layout", "patterns", "cadence",
                                       "palette"],
                       "from_html": ["type", "max_width", "pad", "radius", "scrim", "skeleton", "fonts"],
                       "matched_sections": len(pairs), "image_sections": len(img_s),
                       "html_sections": len(html_s)}
    merged["brief"] = brief(merged)
    return merged


# ---------------------------------------------------------------------------
# The candidate's render, side by side with the source
# ---------------------------------------------------------------------------

def side_by_side(source_path, render_path, out_path, size=PHONE):
    """The source's first phone screen (left) beside the render's (right),
    each size[0] wide, one JPEG."""
    left = open_image(source_path)
    right = open_image(render_path)
    parts = []
    for im in (left, right):
        scale = size[0] / im.size[0]
        im = im.resize((size[0], max(1, round(im.size[1] * scale))), Image.LANCZOS)
        parts.append(im.crop((0, 0, size[0], min(im.size[1], size[1]))))
    _fit(_sheet(parts)).save(out_path, format="JPEG", quality=85)
    return out_path
