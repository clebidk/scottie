"""Cycle 92: the design-quality gate (docs/DESIGN-GATE.md).

Owner rule 2026-10-07: "Visual design needs to be top tier. Better generation
is preferred over lower cost; prioritise quality while optimising both."

For a rendered page (index.html + assets/) the gate

1. takes screenshots with the headless Chromium the Library already drives
   (harness/capture.py): phone 390 wide, full page, capped at MAX_SHOT_PX
   tall; desktop 1280x900 (the fold) and 1280 full page;
2. runs HARD CHECKS with no model, from a DOM probe and the phone shot:
   horizontal overflow at 390, contrast of text over photos in the first
   screen, empty image slots, text cut off, tap targets under 44 px, page
   weight, and which brand fonts loaded;
3. asks a VISION JUDGE (tenant `design_gate.model`, default Claude Opus 5.5,
   through the same Anthropic client every stage uses, so tests inject a
   FakeClient) to score the phone page and the desktop fold against
   harness/design_rubric.md (tenant-neutral) plus tenants/<t>/design-rubric.md
   (the brand rules and its approved reference pages), and return JSON: score_0_100, seven subscores,
   top_issues [{what, where, fix_as_layout_choice}], verdict.

As a pipeline stage (after render_pages; tenant.yaml `design_gate.mode`:
off | warn | enforce) it builds up to `variants` LAYOUTS of the same
page.json -- look x first-screen style (x block set, a hook for cycle 91),
within what the tenant allows and never against an explicit --look /
--hero-style (an A/B/C arm pins both) -- scores each, keeps the best and
records every shot and score under out/<run>/<cartridge>/design/. Copy is
never rewritten here. Below `min_score`: one REVISE round of new layouts,
ranked by the judge's fixes (a different first screen, a different look, more
or fewer photographs); still below: exit 2 (enforce) or a warning (warn).
`max_usd_per_page` stops further rounds with a note in the run log and
REVIEW.md; it never lowers the bar.

Fail soft: no browser, or a judge that cannot answer, leaves the page
"unscored" (said in the log and REVIEW.md), never a crash.
"""
import base64
import copy
import datetime
import io
import json
import os
import re
import shutil
import statistics
from pathlib import Path

from . import capture, exits, heroes, looks, pricing
from .anthropic_client import adaptive_kwargs
from .errors import HarnessError
from .jsonutil import extract_json

RUBRIC_PATH = Path(__file__).resolve().parent / "design_rubric.md"
# The tenant's own part: its brand rules and its approved reference pages.
TENANT_RUBRIC = "design-rubric.md"
MODE_ENV = "HARNESS_DESIGN_GATE"
MODES = ("off", "warn", "enforce")
DEFAULTS = {
    "mode": "off",
    # The strongest vision model the harness prices (harness/pricing.py).
    "model": "claude-opus-5-5",
    "effort": "high",
    "min_score": 75,
    "variants": 3,
    "max_usd_per_page": 1.00,
}
MAX_VARIANTS = 6
EFFORTS = ("low", "medium", "high", "xhigh", "max")
SUBSCORES = ("hierarchy", "typography", "imagery", "whitespace_rhythm", "brand_fit", "mobile_craft",
             "distinctiveness")
VERDICTS = ("ship", "revise", "reject")
PHONE = (390, 844)
DESKTOP = (1280, 900)
MAX_SHOT_PX = 6000
# Opus 5.5 reads up to 2576 px on the long edge without downscaling; a
# 390 x 6000 page is sent as tiles of this height.
TILE_PX = 2400
JUDGE_MAX_TOKENS = 16000
# Before the first call of a page: input (rubric + context + images) and
# output (thinking + JSON) tokens one judge call is expected to use. After
# the first call the last call's real cost is the estimate.
EST_INPUT_TOKENS = 9000
EST_OUTPUT_TOKENS = 4000
WEIGHT_WARN_BYTES = 1_500_000
WEIGHT_FAIL_BYTES = 3_000_000
CONTRAST_NORMAL = 4.5
CONTRAST_LARGE = 3.0
SCORES_FILE = "scores.json"
DESIGN_DIR = "design"

# How much photography each first screen carries (the revise round's "more
# / fewer photos" translation). An imported (pack) look is photo-led.
HERO_PHOTO_WEIGHT = {"face": 2, "caps_over_photo": 2, "letters_pill": 2, "split": 2, "display": 1,
                     "dark_statement": 1, "ad_first": 1, "buybox": 1, "story": 0}
IMPORTED_LOOK_PHOTO_WEIGHT = 1
_HERO_WORDS = ("hero", "first screen", "above the fold", "fold", "headline area", "opening")
_LOOK_WORDS = ("layout", "look", "grid", "box", "card", "section", "structure", "template", "stacked", "rhythm")
_MORE_PHOTO_WORDS = ("more photo", "more imagery", "add a photo", "add photo", "photograph", "full-bleed",
                     "full bleed", "image-led", "tiny hero image", "small hero image", "small image", "no image",
                     "lifestyle")
_FEWER_PHOTO_WORDS = ("fewer photo", "less imagery", "too many image", "too many photo", "remove image",
                      "text-first", "text first", "cluttered with images")


class DesignGateError(HarnessError):
    """A design-gate setting or command that cannot go on; one line, exit 1."""


class DesignGateStop(HarnessError):
    """Enforce mode: a page stayed under min_score after every round."""

    exit_code = exits.GATE_STOP

    def __init__(self, cartridges, results):
        self.cartridges = list(cartridges)
        self.results = results
        detail = ", ".join(
            f"{c} {results[c].get('score') if results[c].get('score') is not None else 'unscored'}"
            f" < {results[c].get('min_score')}" for c in self.cartridges)
        super().__init__(f"design gate STOP: {detail}")


class JudgeError(Exception):
    """The judge gave no usable answer (API error, refusal, bad JSON)."""


# ---------------------------------------------------------------------------
# settings
# ---------------------------------------------------------------------------

def settings(tenant):
    """tenant.yaml `design_gate:` merged over DEFAULTS. HARNESS_DESIGN_GATE
    in the environment overrides the mode (the test suite sets it to off).
    A setting that cannot work raises DesignGateError -- an unknown mode is
    never silently read as off."""
    cfg = dict(DEFAULTS)
    raw = (tenant.get("design_gate") if tenant is not None else None) or {}
    if not isinstance(raw, dict):
        raise DesignGateError("tenant.yaml design_gate must be a mapping")
    cfg.update({k: v for k, v in raw.items() if v is not None})
    if cfg["mode"] is False:   # YAML 1.1 reads a bare `off` as false
        cfg["mode"] = "off"
    env = os.environ.get(MODE_ENV)
    if env:
        cfg["mode"] = env.strip().lower()
    if cfg["mode"] not in MODES:
        raise DesignGateError(f"design_gate.mode {cfg['mode']!r}: choose one of {list(MODES)}")
    if cfg["effort"] not in EFFORTS:
        raise DesignGateError(f"design_gate.effort {cfg['effort']!r}: choose one of {list(EFFORTS)}")
    try:
        cfg["min_score"] = float(cfg["min_score"])
        cfg["variants"] = max(1, min(MAX_VARIANTS, int(cfg["variants"])))
        cfg["max_usd_per_page"] = float(cfg["max_usd_per_page"])
    except (TypeError, ValueError) as e:
        raise DesignGateError(f"design_gate: {e}") from e
    try:
        pricing.price_for(pricing.family_id(cfg["model"]))
    except pricing.UnknownModel as e:
        raise DesignGateError(f"design_gate.model: {e}") from e
    return cfg


def estimate_call_usd(model):
    price = pricing.price_for(pricing.family_id(model))
    return (EST_INPUT_TOKENS * price["input"] + EST_OUTPUT_TOKENS * price["output"]) / 1_000_000


# ---------------------------------------------------------------------------
# hard checks (no model)
# ---------------------------------------------------------------------------

_RGB_RE = re.compile(r"rgba?\(\s*([\d.]+)[,\s]+([\d.]+)[,\s]+([\d.]+)")


def _channel(c):
    c = c / 255.0
    return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4


def luminance(rgb):
    r, g, b = rgb[:3]
    return 0.2126 * _channel(r) + 0.7152 * _channel(g) + 0.0722 * _channel(b)


def contrast_ratio(l1, l2):
    hi, lo = max(l1, l2), min(l1, l2)
    return (hi + 0.05) / (lo + 0.05)


def parse_color(value):
    m = _RGB_RE.search(value or "")
    return tuple(float(x) for x in m.groups()) if m else None


def _background_luminance(image, rect, text_lum):
    """Median luminance of the pixels in `rect` that are not the text
    colour (the photo or scrim behind the words), or None."""
    x, y, w, h = rect
    x0, y0 = max(0, int(x)), max(0, int(y))
    x1, y1 = min(image.size[0], int(x + w)), min(image.size[1], int(y + h))
    if x1 - x0 < 2 or y1 - y0 < 2:
        return None
    crop = image.crop((x0, y0, x1, y1)).convert("RGB")
    crop.thumbnail((120, 60))
    lums = [luminance(p) for p in crop.getdata()]
    background = [v for v in lums if abs(v - text_lum) > 0.08]
    if not background:
        return None
    return statistics.median(background)


def contrast_issues(hero_text, phone_png):
    """[{sel, text, ratio, needed}] for first-screen text over a photo
    whose contrast is under WCAG AA. Measured on the screenshot, so a scrim
    or text shadow counts."""
    if not hero_text or not phone_png or not Path(phone_png).is_file():
        return []
    from PIL import Image

    out = []
    with Image.open(phone_png) as image:
        image.load()
        for item in hero_text:
            rgb = parse_color(item.get("color"))
            if rgb is None:
                continue
            text_lum = luminance(rgb)
            bg = _background_luminance(image, item.get("rect") or (0, 0, 0, 0), text_lum)
            if bg is None:
                continue
            ratio = contrast_ratio(text_lum, bg)
            size, weight = float(item.get("size") or 16), int(item.get("weight") or 400)
            needed = CONTRAST_LARGE if size >= 24 or (size >= 18.66 and weight >= 700) else CONTRAST_NORMAL
            if ratio < needed:
                out.append({"sel": item.get("sel"), "text": item.get("text"), "ratio": round(ratio, 2),
                            "needed": needed})
    return out


def page_weight(page_dir):
    """Bytes of index.html plus every file under assets/."""
    page_dir = Path(page_dir)
    total = (page_dir / "index.html").stat().st_size if (page_dir / "index.html").is_file() else 0
    assets = page_dir / "assets"
    if assets.is_dir():
        total += sum(p.stat().st_size for p in assets.rglob("*") if p.is_file())
    return total


def font_report(probe):
    """{"status": loaded|fallback|unknown, "loaded": [...], "fallback": [...],
    "system": [...]} for the families the page's text actually uses: a used
    family the page declares as a web font either loaded at least one face
    or fell back."""
    if not probe:
        return {"status": "unknown", "loaded": [], "fallback": [], "system": []}
    declared = probe.get("fonts") or {}
    used = probe.get("used_families") or {}
    loaded, fallback, system = [], [], []
    for family in used:
        info = declared.get(family)
        if info is None:
            system.append(family)
        elif int(info.get("loaded") or 0) > 0:
            loaded.append(family)
        else:
            fallback.append(family)
    unused_failed = [f for f, i in declared.items() if f not in used and int(i.get("loaded") or 0) == 0]
    return {"status": "fallback" if fallback else "loaded", "loaded": loaded, "fallback": fallback,
            "system": system, "declared_not_loaded": unused_failed}


def _check(status, detail, items=None):
    return {"status": status, "detail": detail, "items": items or []}


def first_screen_cta(probe_phone):
    """Cycle 97: on a 390 x 844 phone, the primary CTA's top edge is inside
    the first screen. Only for a page whose first screen carries the pill (a
    landing page, a cycle 94 composition: the probe's `cta_expected`); an
    article first screen (listicle face/story/display) passes with a note."""
    if not probe_phone.get("cta_expected"):
        return _check("pass", "no first-screen CTA by design (article first screen)")
    cta = probe_phone.get("cta")
    if not cta:
        return _check("fail", "no primary CTA found on the page")
    top = cta.get("top") or 0
    if top >= PHONE[1]:
        return _check("fail", f"the primary CTA starts at {top}px, below the first {PHONE[1]}px screen", [cta])
    return _check("pass", f"the primary CTA starts at {top}px, inside the first {PHONE[1]}px screen")


def hard_checks(probe_phone, phone_png, page_dir):
    """Every hard check, each {"status": pass|warn|fail|unknown, "detail",
    "items"}. A missing probe makes the DOM checks "unknown"."""
    checks = {}
    weight = page_weight(page_dir)
    mb = weight / 1_000_000
    checks["page_weight"] = _check(
        "fail" if weight > WEIGHT_FAIL_BYTES else "warn" if weight > WEIGHT_WARN_BYTES else "pass",
        f"{mb:.2f} MB (aim under {WEIGHT_WARN_BYTES / 1e6:.1f} MB on a phone)")
    if not probe_phone or probe_phone.get("error"):
        why = (probe_phone or {}).get("error") or "no DOM probe (browser missing or failed)"
        for name in ("overflow_390", "first_screen_cta", "contrast_hero", "empty_images", "text_cut", "tap_targets"):
            checks[name] = _check("unknown", why)
        checks["fonts"] = _check("unknown", why)
        return checks
    vw, sw = probe_phone.get("viewport_width") or PHONE[0], probe_phone.get("scroll_width") or 0
    checks["overflow_390"] = _check(
        "fail" if sw > vw + 1 else "pass",
        f"page is {sw}px wide in a {vw}px viewport" if sw > vw + 1 else "no horizontal scroll at 390",
        probe_phone.get("overflow") if sw > vw + 1 else [])
    checks["first_screen_cta"] = first_screen_cta(probe_phone)
    low = contrast_issues(probe_phone.get("hero_text"), phone_png)
    checks["contrast_hero"] = _check(
        "fail" if low else "pass",
        (f"{len(low)} first-screen text block(s) over a photo under WCAG AA" if low else
         f"{len(probe_phone.get('hero_text') or [])} first-screen text block(s) over a photo, all AA"), low)
    broken = [i for i in probe_phone.get("images") or [] if i.get("empty") or not i.get("loaded")]
    holders = probe_phone.get("placeholders") or []
    checks["empty_images"] = _check(
        "fail" if broken or holders else "pass",
        f"{len(broken)} image(s) did not load, {len(holders)} placeholder(s)" if broken or holders
        else f"{len(probe_phone.get('images') or [])} image(s), all loaded", broken + holders)
    cut = probe_phone.get("cut") or []
    checks["text_cut"] = _check("fail" if cut else "pass",
                                f"{len(cut)} text block(s) cut off" if cut else "no text cut off", cut)
    taps = probe_phone.get("taps") or []
    small_cta = [t for t in taps if t.get("cta")]
    checks["tap_targets"] = _check(
        "fail" if small_cta else "warn" if taps else "pass",
        (f"{len(small_cta)} button(s) under 44px" if small_cta else
         f"{len(taps)} link(s) under 44px (not buttons)" if taps else "every tap target is 44px or more"), taps)
    fonts = font_report(probe_phone)
    checks["fonts"] = _check(
        "warn" if fonts["status"] == "fallback" else "pass",
        ("fallback for " + ", ".join(fonts["fallback"])) if fonts["fallback"]
        else "loaded: " + (", ".join(fonts["loaded"]) or "no web fonts used"), [fonts])
    return checks


def hard_fail(checks):
    return [name for name, c in checks.items() if c["status"] == "fail"]


# ---------------------------------------------------------------------------
# screenshots
# ---------------------------------------------------------------------------

def take_shots(page_dir, design_dir, n, *, tenant, browser):
    """Phone full page, desktop fold and desktop full page of
    page_dir/index.html, into design_dir/variant-<n>*.png. Returns
    (shots {name: file name}, probe at 390, probe at 1280)."""
    html = Path(page_dir) / "index.html"
    shots = {}
    probe_phone = capture.probe(browser, html, width=PHONE[0], height=PHONE[1], tenant=tenant)
    probe_desk = capture.probe(browser, html, width=DESKTOP[0], height=DESKTOP[1], tenant=tenant)
    phone_h = min(MAX_SHOT_PX, max(PHONE[1], int((probe_phone or {}).get("height") or PHONE[1])))
    desk_h = min(MAX_SHOT_PX, max(DESKTOP[1], int((probe_desk or {}).get("height") or DESKTOP[1])))
    plan = (("phone", f"variant-{n}.png", PHONE[0], phone_h),
            ("desktop", f"variant-{n}-desktop.png", DESKTOP[0], DESKTOP[1]),
            ("desktop_full", f"variant-{n}-desktop-full.png", DESKTOP[0], desk_h))
    for name, filename, w, h in plan:
        screen = PHONE[1] if name == "phone" else DESKTOP[1]
        if capture.screenshot(browser, html, Path(design_dir) / filename, width=w, height=h, tenant=tenant,
                              screen_height=screen):
            shots[name] = filename
    return shots, probe_phone, probe_desk


def _jpeg_b64(image):
    buf = io.BytesIO()
    image.convert("RGB").save(buf, format="JPEG", quality=85)
    return base64.standard_b64encode(buf.getvalue()).decode("ascii")


def judge_images(design_dir, shots):
    """[(label, base64 jpeg)]: the phone page in tiles of at most TILE_PX,
    then the desktop fold."""
    from PIL import Image

    out = []
    phone = shots.get("phone")
    if phone:
        with Image.open(Path(design_dir) / phone) as im:
            im.load()
            tiles = max(1, -(-im.size[1] // TILE_PX))
            for i in range(tiles):
                tile = im.crop((0, i * TILE_PX, im.size[0], min(im.size[1], (i + 1) * TILE_PX)))
                out.append((f"Phone, 390 px wide, full page part {i + 1} of {tiles} (top to bottom)", _jpeg_b64(tile)))
    desk = shots.get("desktop")
    if desk:
        with Image.open(Path(design_dir) / desk) as im:
            im.load()
            out.append(("Desktop, 1280x900, the first screen", _jpeg_b64(im)))
    return out


# ---------------------------------------------------------------------------
# the vision judge
# ---------------------------------------------------------------------------

OUTPUT_SPEC = """
## Output

Return ONLY one JSON object, no prose before or after, no markdown fences:

{"score_0_100": <integer 0-100>,
 "subscores": {"hierarchy": <0-100>, "typography": <0-100>, "imagery": <0-100>, "whitespace_rhythm": <0-100>,
               "brand_fit": <0-100>, "mobile_craft": <0-100>, "distinctiveness": <0-100>},
 "top_issues": [{"what": "<the problem, one sentence>", "where": "<section, e.g. first screen, item 3, closing band>",
                 "fix_as_layout_choice": "<a layout change: a different first screen, a different look, more or fewer
                 photographs, a different section order -- never a copy rewrite>"}],
 "verdict": "ship" | "revise" | "reject"}

At most five top_issues, worst first. Judge only what the screenshots show.
"""


def _brand_section(tenant):
    """A brand section from tenant.yaml (palette, fonts, radius) for a
    tenant with no design-rubric.md of its own."""
    lines = ["## The brand (from tenant.yaml)", ""]
    palette = (tenant.get("brand.palette") if tenant is not None else None) or {}
    fonts = (tenant.get("brand.fonts") if tenant is not None else None) or {}
    if palette:
        lines.append("- Palette: " + ", ".join(f"{k} {v}" for k, v in palette.items()) + ".")
    if fonts:
        lines.append("- Fonts: " + ", ".join(f"{k} {v}" for k, v in fonts.items()) + ".")
    radius = tenant.get("brand.radius") if tenant is not None else None
    if radius is not None:
        lines.append(f"- Box radius {radius}px.")
    if len(lines) == 2:
        lines.append("- No brand rules on file: judge brand fit by internal consistency.")
    return "\n".join(lines) + "\n"


def rubric_text(tenant=None):
    """The judge's system prompt: the engine rubric (harness/design_rubric.md),
    the tenant's brand and reference pages (tenants/<t>/design-rubric.md, or
    a brand section from tenant.yaml), and the output spec."""
    brand = (getattr(tenant, "display_name", None) or "this brand") if tenant is not None else "this brand"
    text = RUBRIC_PATH.read_text().replace("{brand}", brand)
    root = getattr(tenant, "root", None)
    own = Path(root) / TENANT_RUBRIC if root is not None else None
    text += "\n" + (own.read_text() if own is not None and own.is_file() else _brand_section(tenant))
    return text + "\n" + OUTPUT_SPEC


def _context_text(*, cartridge, layout, checks, tenant_name):
    fonts = (checks.get("fonts") or {}).get("items") or [{}]
    fonts = fonts[0] if fonts else {}
    lines = [f"Brand: {tenant_name}. Page type: {cartridge}."]
    bits = [f"look {layout.get('look')}" if layout.get("look") else "",
            f"first screen {layout.get('hero_style')}" if layout.get("hero_style") else ""]
    if any(bits):
        lines.append("Layout: " + ", ".join(b for b in bits if b) + ".")
    if fonts.get("fallback"):
        lines.append("Rendering note: in the screenshot browser these fonts did not load and a fallback face shows: "
                     + ", ".join(fonts["fallback"]) + ". Judge typography by scale, weight and hierarchy, "
                     "not by the face itself.")
    measured = "; ".join(f"{name}: {c['status']} ({c['detail']})" for name, c in checks.items())
    lines.append(f"Hard checks measured without a model: {measured}.")
    lines.append("Score this page design against the rubric and return the JSON object.")
    return "\n".join(lines)


def parse_judge(text):
    """The judge's JSON as {score, subscores, top_issues, verdict}. Raises
    JudgeError for an answer that is not that shape."""
    try:
        data = extract_json(text)
    except (ValueError, TypeError) as e:
        raise JudgeError(f"judge answer is not JSON: {e}") from e
    if not isinstance(data, dict):
        raise JudgeError("judge answer is not a JSON object")
    try:
        score = float(data["score_0_100"])
    except (KeyError, TypeError, ValueError) as e:
        raise JudgeError("judge answer has no numeric score_0_100") from e
    verdict = str(data.get("verdict") or "").strip().lower()
    if verdict not in VERDICTS:
        raise JudgeError(f"judge verdict {data.get('verdict')!r} is not one of {list(VERDICTS)}")
    raw_sub = data.get("subscores") if isinstance(data.get("subscores"), dict) else {}
    subscores = {}
    for key in SUBSCORES:
        try:
            subscores[key] = max(0, min(100, round(float(raw_sub[key]))))
        except (KeyError, TypeError, ValueError):
            subscores[key] = None
    issues = []
    for item in data.get("top_issues") or []:
        if isinstance(item, dict) and item.get("what"):
            issues.append({k: str(item.get(k) or "").strip() for k in ("what", "where", "fix_as_layout_choice")})
    return {"score": max(0, min(100, round(score))), "subscores": subscores, "top_issues": issues[:5],
            "verdict": verdict}


def judge(client, *, model, effort, images, context, log=None, stage="design_gate", tenant=None):
    """One vision-judge call. Returns (parsed result, cost in USD). Raises
    JudgeError for anything the judge cannot be read from."""
    content = []
    for label, data in images:
        content.append({"type": "text", "text": label})
        content.append({"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": data}})
    content.append({"type": "text", "text": context})
    try:
        response = client.messages.create(
            model=model, max_tokens=JUDGE_MAX_TOKENS, system=rubric_text(tenant),
            messages=[{"role": "user", "content": content}], **adaptive_kwargs(model, effort),
        )
    except Exception as e:  # noqa: BLE001 -- the judge is fail-soft; any API failure is "unscored"
        raise JudgeError(f"judge call failed: {type(e).__name__}: {e}") from e
    usage = response.usage
    family = pricing.family_id(model)
    cost = pricing.calculate_cost(
        family, usage.input_tokens, usage.output_tokens,
        cache_creation_input_tokens=getattr(usage, "cache_creation_input_tokens", 0) or 0,
        cache_read_input_tokens=getattr(usage, "cache_read_input_tokens", 0) or 0,
    )
    if log is not None:
        log.call(stage, family, usage.input_tokens, usage.output_tokens,
                 cache_creation_input_tokens=getattr(usage, "cache_creation_input_tokens", 0) or 0,
                 cache_read_input_tokens=getattr(usage, "cache_read_input_tokens", 0) or 0)
    stop = getattr(response, "stop_reason", None)
    if stop == "refusal":
        raise JudgeError("judge refused the request (stop_reason refusal)")
    if stop == "max_tokens":
        raise JudgeError(f"judge answer cut off at max_tokens ({JUDGE_MAX_TOKENS})")
    text = "".join(b.text for b in response.content if getattr(b, "type", None) == "text")
    result = parse_judge(text)
    return result, cost


# ---------------------------------------------------------------------------
# one page folder -> one scored variant
# ---------------------------------------------------------------------------

class PageScorer:
    """Scores page folders for one page, keeping the page's running judge
    cost against max_usd_per_page."""

    def __init__(self, *, tenant, client, cfg, log=None, browser=None, stage="design_gate"):
        self.tenant = tenant
        self.client = client
        self.cfg = cfg
        self.log = log
        self.browser = browser if browser is not None else capture.find_browser()
        self.stage = stage
        self.cost = 0.0
        self.calls = 0
        self.last_call_usd = None
        self.notes = []

    def _event(self, message):
        if self.log is not None:
            self.log.event(self.stage, message)

    def can_afford_call(self):
        est = self.last_call_usd if self.last_call_usd is not None else estimate_call_usd(self.cfg["model"])
        return self.cost + est <= self.cfg["max_usd_per_page"] + 1e-9

    def score(self, page_dir, design_dir, n, *, cartridge, layout):
        """Shots, hard checks and (when the cap allows) one judge call for
        page_dir. Returns the variant record."""
        record = {"n": n, "look": layout.get("look"), "hero_style": layout.get("hero_style"),
                  "block_set": layout.get("block_set"), "round": layout.get("round", 1), "shots": {},
                  "judge": None, "judge_error": None, "cost_usd": 0.0}
        if not self.browser:
            record["hard_checks"] = hard_checks(None, None, page_dir)
            record["judge_error"] = "no headless chromium/google-chrome found: unscored"
            record["hard_fail"] = hard_fail(record["hard_checks"])
            return record
        Path(design_dir).mkdir(parents=True, exist_ok=True)
        shots, probe_phone, _probe_desk = take_shots(page_dir, design_dir, n, tenant=self.tenant, browser=self.browser)
        record["shots"] = shots
        phone_png = Path(design_dir) / shots["phone"] if shots.get("phone") else None
        record["hard_checks"] = hard_checks(probe_phone, phone_png, page_dir)
        record["hard_fail"] = hard_fail(record["hard_checks"])
        record["fonts"] = font_report(probe_phone)
        if record["hard_checks"].get("first_screen_cta", {}).get("status") == "fail":
            # cycle 97: a variant whose pill is below the first phone screen
            # can never be chosen, so it is not worth a judge call
            record["judge_error"] = f"not judged: {record['hard_checks']['first_screen_cta']['detail']}"
            self._event(f"{cartridge} variant {n}: {record['judge_error']}")
            return record
        if not shots.get("phone"):
            record["judge_error"] = "the phone screenshot failed: unscored"
            return record
        if not self.can_afford_call():
            record["judge_error"] = (f"max_usd_per_page ${self.cfg['max_usd_per_page']:.2f} reached "
                                     f"(${self.cost:.4f} spent): not judged")
            record["budget_stop"] = True
            return record
        context = _context_text(cartridge=cartridge, layout=layout, checks=record["hard_checks"],
                                tenant_name=getattr(self.tenant, "display_name", "") or "")
        try:
            result, cost = judge(self.client, model=self.cfg["model"], effort=self.cfg["effort"],
                                 images=judge_images(design_dir, shots), context=context, log=self.log,
                                 stage=f"{self.stage}.{cartridge}", tenant=self.tenant)
        except JudgeError as e:
            self.calls += 1
            record["judge_error"] = str(e)
            self._event(f"{cartridge} variant {n}: judge unavailable: {e}")
            return record
        self.calls += 1
        self.cost += cost
        self.last_call_usd = cost
        record["judge"] = result
        record["cost_usd"] = round(cost, 6)
        return record


# Cycle 97: what the revise round is told when no variant was judged because
# every pill was below the first phone screen (ranks a different first screen).
CTA_BELOW_FOLD_ISSUE = {"what": "the primary CTA is below the first phone screen", "where": "first screen",
                        "fix_as_layout_choice": "a different first screen"}


def _cta_below_fold(records):
    return bool(records) and all("first_screen_cta" in (r.get("hard_fail") or []) for r in records)


def eligible(record):
    return bool(record.get("judge")) and not record.get("hard_fail")


def best_variant(records):
    """The eligible record with the highest score, else the highest-scored
    one at all, else None."""
    scored = [r for r in records if r.get("judge")]
    pool = [r for r in scored if eligible(r)] or scored
    if not pool:
        return None
    return max(pool, key=lambda r: (r["judge"]["score"], -r["n"]))


def passes(record, cfg):
    return bool(record) and eligible(record) and record["judge"]["score"] >= cfg["min_score"] \
        and record["judge"]["verdict"] != "reject"


# ---------------------------------------------------------------------------
# layouts: what a variant may change
# ---------------------------------------------------------------------------

def block_sets(cartridge, tenant=None):
    """Cycle 91 hook: the block sets a variant may try. None = the page's
    own blocks; cycle 91 returns its set names here."""
    return (None,)


def layout_key(layout):
    return (layout.get("look"), layout.get("hero_style"), layout.get("block_set"))


def _heroes(cartridge, look, tenant, pinned, *, every=False):
    """The hero compositions (cycle 94) a variant may use in `look`: the
    pinned one when the look renders it (else none: that look is skipped),
    else the look's default pool (look.json default_heroes, narrowed by
    tenant.yaml cartridges.<c>.hero_styles) -- or, with every=True (the
    revise round), every composition the look.json declares."""
    if not heroes.has_heroes(cartridge):
        return [None]
    supported = heroes.look_heroes(cartridge, look, tenant)
    if pinned.get("hero_style"):
        return [pinned["hero_style"]] if pinned["hero_style"] in supported else []
    return list(supported if every else heroes.default_pool(cartridge, look, tenant))


def _look_list(cartridge, tenant, pinned):
    if not looks.has_looks(cartridge):
        return [None]
    if pinned.get("look"):
        return [pinned["look"]]
    return list(looks.tenant_looks(cartridge, tenant))


def layout_space(cartridge, tenant, pinned=None, *, every=False):
    """Every layout the first round may try: the tenant's looks for this
    cartridge (or the pinned one) x each look's hero compositions (its
    look.json default pool) x block sets. A look's own blocks (cycle 91
    look.json "blocks") come with the look."""
    pinned = pinned or {}
    return [{"look": lk, "hero_style": h, "block_set": b}
            for lk in _look_list(cartridge, tenant, pinned)
            for h in _heroes(cartridge, lk, tenant, pinned, every=every)
            for b in block_sets(cartridge, tenant)]


def extra_layouts(cartridge, tenant, pinned=None):
    """Layouts only the revise round tries: every other hero composition
    each look's look.json declares, and the tenant's imported looks (cycle
    87, e.g. the owner's pack looks) for this cartridge."""
    pinned = pinned or {}
    out = layout_space(cartridge, tenant, pinned, every=True)
    if pinned.get("look") or not looks.has_looks(cartridge):
        return out
    for slug in looks.imported_looks(cartridge, tenant):
        try:
            if looks.resolve_look(cartridge, slug, tenant=tenant) != slug:
                continue
        except ValueError:
            continue
        out += [{"look": slug, "hero_style": h, "block_set": b, "imported": True}
                for h in _heroes(cartridge, slug, tenant, pinned, every=True) for b in block_sets(cartridge, tenant)]
    return out


def first_round(current, space, count):
    """The page's own layout first, then the rest of the space, `count` in all."""
    out = [dict(current)]
    for layout in space:
        if len(out) >= count:
            break
        if layout_key(layout) != layout_key(current):
            out.append(dict(layout))
    return out


def _photo_weight(layout):
    weight = HERO_PHOTO_WEIGHT.get(layout.get("hero_style"), 0)
    return weight + (IMPORTED_LOOK_PHOTO_WEIGHT if layout.get("imported") else 0)


def revise_choices(candidates, issues, best, count):
    """The judge's fixes as concrete layout choices: up to `count`
    untried layouts, ranked by what the issues ask for. Returns
    [(layout, [reasons])]."""
    text = " ".join(f"{i.get('what', '')} {i.get('where', '')} {i.get('fix_as_layout_choice', '')}"
                    for i in issues or []).lower()
    wants_hero = any(w in text for w in _HERO_WORDS)
    wants_look = any(w in text for w in _LOOK_WORDS)
    more = any(w in text for w in _MORE_PHOTO_WORDS)
    fewer = any(w in text for w in _FEWER_PHOTO_WORDS)
    base_weight = _photo_weight(best or {})
    ranked = []
    for i, layout in enumerate(candidates):
        points, reasons = 0, []
        if wants_hero and layout.get("hero_style") != (best or {}).get("hero_style"):
            points += 2
            reasons.append(f"a different first screen ({layout.get('hero_style')})")
        if wants_look and layout.get("look") != (best or {}).get("look"):
            points += 2
            reasons.append(f"a different look ({layout.get('look')})")
        delta = _photo_weight(layout) - base_weight
        if more and delta > 0:
            points += delta
            reasons.append("more photography")
        if fewer and delta < 0:
            points += -delta
            reasons.append("fewer photographs")
        ranked.append((-points, i, layout, reasons or ["next untried layout"]))
    ranked.sort(key=lambda t: (t[0], t[1]))
    return [(layout, reasons) for _p, _i, layout, reasons in ranked[:count]]


# ---------------------------------------------------------------------------
# the stage: one page, N variants, one revise round
# ---------------------------------------------------------------------------

def design_dir_for(page_dir):
    return Path(page_dir) / DESIGN_DIR


def _snapshot(page_dir, dest):
    """index.html + assets/ of page_dir copied to dest (shots are taken
    there, so a later render of the same folder cannot change them)."""
    if dest.exists():
        shutil.rmtree(dest)
    dest.mkdir(parents=True)
    shutil.copy2(Path(page_dir) / "index.html", dest / "index.html")
    if (Path(page_dir) / "assets").is_dir():
        shutil.copytree(Path(page_dir) / "assets", dest / "assets")


def current_layout(cartridge, page):
    return {"look": page.get("look") if looks.has_looks(cartridge) else None,
            "hero_style": page.get("hero_style") if heroes.has_heroes(cartridge) else None,
            "block_set": None}


def run_for_page(*, cartridge, page_dir, base_page, render, tenant, client, cfg, log=None, pinned=None,
                 browser=None):
    """The gate for one rendered page. `render(page)` renders a page dict into
    page_dir (the pipeline's render_page call) and returns the page.json it
    wrote. Returns the result dict that is also written to
    page_dir/design/scores.json."""
    page_dir = Path(page_dir)
    design_dir = design_dir_for(page_dir)
    if design_dir.exists():
        shutil.rmtree(design_dir)
    design_dir.mkdir(parents=True)
    work = design_dir / "work"
    scorer = PageScorer(tenant=tenant, client=client, cfg=cfg, log=log, browser=browser)
    current = current_layout(cartridge, base_page)
    records, rendered_pages, notes, rounds = [], {}, [], []
    last_rendered = layout_key(current)

    seen = set()

    def build(layout):
        """Render `layout` (unless it is the page as it stands), snapshot
        and score it. None when the render landed on a layout already tried
        (the face first screen falls back when the ad has no still)."""
        nonlocal last_rendered
        n = len(records) + 1
        if not records and layout_key(layout) == layout_key(current):
            written = json.loads((page_dir / "page.json").read_text())
        else:
            page = copy.deepcopy(base_page)
            if layout.get("look"):
                page["look"] = layout["look"]
            if layout.get("hero_style"):
                page["hero_style"] = layout["hero_style"]
            written = render(page)
        rendered = current_layout(cartridge, written)
        actual = dict(layout, look=rendered["look"], hero_style=rendered["hero_style"])
        last_rendered = layout_key(actual)
        if layout_key(actual) in seen:
            notes.append(f"{_layout_label(layout)} rendered as {_layout_label(actual)} (already tried): skipped")
            return None
        seen.add(layout_key(actual))
        _snapshot(page_dir, work / f"variant-{n}")
        rec = scorer.score(work / f"variant-{n}", design_dir, n, cartridge=cartridge, layout=actual)
        rendered_pages[n] = written
        records.append(rec)
        return rec

    if not scorer.browser:
        notes.append("no headless chromium/google-chrome on this host: the page is UNSCORED")
    plan = first_round(current, layout_space(cartridge, tenant, pinned), cfg["variants"])
    for layout in plan:
        layout["round"] = 1
        rec = build(layout)
        if rec is None:
            continue
        if rec.get("budget_stop"):
            notes.append(f"max_usd_per_page ${cfg['max_usd_per_page']:.2f} reached after "
                         f"{scorer.calls} judge call(s): round 1 stopped at variant {rec['n']}")
            break
        if not scorer.browser:
            break
    rounds.append({"round": 1, "variants": [r["n"] for r in records]})
    best = best_variant(records)
    revise = []
    if (best is not None and not passes(best, cfg) or _cta_below_fold(records)) \
            and not any(r.get("budget_stop") for r in records):
        tried = {layout_key(r) for r in records} | {layout_key(p) for p in plan}
        candidates = []
        for lay in layout_space(cartridge, tenant, pinned) + extra_layouts(cartridge, tenant, pinned):
            if layout_key(lay) not in tried:
                tried.add(layout_key(lay))
                candidates.append(lay)
        if not candidates:
            notes.append("revise round skipped: no untried layout left in this page's supported set")
        elif not scorer.can_afford_call():
            notes.append(f"revise round skipped: max_usd_per_page ${cfg['max_usd_per_page']:.2f} "
                         f"(${scorer.cost:.4f} spent)")
        else:
            issues = best["judge"]["top_issues"] if best else [CTA_BELOW_FOLD_ISSUE]
            revise = revise_choices(candidates, issues, best, cfg["variants"])
            start = len(records)
            for layout, reasons in revise:
                rec = build(dict(layout, round=2))
                if rec is None:
                    continue
                rec["chosen_because"] = reasons
                if rec.get("budget_stop"):
                    notes.append(f"max_usd_per_page ${cfg['max_usd_per_page']:.2f} reached in the revise round "
                                 f"at variant {rec['n']}")
                    break
            rounds.append({"round": 2, "variants": [r["n"] for r in records[start:]],
                           "choices": [{"look": lay.get("look"), "hero_style": lay.get("hero_style"),
                                        "reasons": why} for lay, why in revise]})
            best = best_variant(records)

    # Leave the best layout in page_dir (the page every later stage reads).
    final_page = None
    if best is not None:
        final_page = rendered_pages[best["n"]]
        if layout_key(best) != last_rendered:
            page = copy.deepcopy(base_page)
            if best.get("look"):
                page["look"] = best["look"]
            if best.get("hero_style"):
                page["hero_style"] = best["hero_style"]
            final_page = render(page)
    elif last_rendered != layout_key(current):
        final_page = render(copy.deepcopy(base_page))
    shutil.rmtree(work, ignore_errors=True)

    if best is None:
        status = "unscored"
    elif passes(best, cfg):
        status = "pass"
    else:
        status = "stop" if cfg["mode"] == "enforce" else "warn"
    if status == "unscored" and any(r.get("budget_stop") for r in records):
        # A page never judged because of the money cap is not a pass.
        status = "stop" if cfg["mode"] == "enforce" else "warn"
        notes.append("not one variant was judged within max_usd_per_page")
    if status == "unscored" and _cta_below_fold(records):
        # cycle 97: never judged because every pill sat below the first screen
        status = "stop" if cfg["mode"] == "enforce" else "warn"
        notes.append("every variant has its primary CTA below the first phone screen (first_screen_cta)")
    result = {
        "cartridge": cartridge, "mode": cfg["mode"], "model": cfg["model"], "effort": cfg["effort"],
        "min_score": cfg["min_score"], "variants_asked": cfg["variants"],
        "max_usd_per_page": cfg["max_usd_per_page"], "status": status,
        "chosen": best["n"] if best else None, "score": best["judge"]["score"] if best else None,
        "subscores": best["judge"]["subscores"] if best else None,
        "top_issues": best["judge"]["top_issues"] if best else [],
        "verdict": best["judge"]["verdict"] if best else None,
        "hard_checks": best["hard_checks"] if best else (records[0]["hard_checks"] if records else {}),
        "fonts": (best or (records[0] if records else {})).get("fonts"),
        "look": best.get("look") if best else None, "hero_style": best.get("hero_style") if best else None,
        "variants": records, "rounds": rounds, "judge_calls": scorer.calls, "cost_usd": round(scorer.cost, 6),
        "notes": notes, "pinned": {k: v for k, v in (pinned or {}).items() if v},
        "scored_at": datetime.datetime.now().isoformat(timespec="seconds"),
    }
    (design_dir / SCORES_FILE).write_text(json.dumps(result, indent=2) + "\n")
    if log is not None:
        log.event("design_gate", f"{cartridge}: {summary_line(result)}")
        for note in notes:
            log.event("design_gate", f"{cartridge}: {note}")
    result["final_page"] = final_page
    return result


def score_folder(page_dir, *, cartridge, tenant, client, cfg, log=None, browser=None, layout=None):
    """`harness design score` and the Library: one variant, no re-render.
    Writes page_dir/design/ and returns the result."""
    page_dir = Path(page_dir)
    if not (page_dir / "index.html").is_file():
        raise DesignGateError(f"no index.html under {page_dir}")
    design_dir = design_dir_for(page_dir)
    if design_dir.exists():
        shutil.rmtree(design_dir)
    design_dir.mkdir(parents=True)
    work = design_dir / "work"
    _snapshot(page_dir, work / "variant-1")
    page = {}
    try:
        page = json.loads((page_dir / "page.json").read_text())
    except (OSError, ValueError):
        pass
    layout = dict(layout or current_layout(cartridge, page), round=1)
    scorer = PageScorer(tenant=tenant, client=client, cfg=cfg, log=log, browser=browser)
    try:
        rec = scorer.score(work / "variant-1", design_dir, 1, cartridge=cartridge, layout=layout)
    finally:
        shutil.rmtree(work, ignore_errors=True)
    scored = rec.get("judge") is not None
    status = ("unscored" if not scored else "pass" if passes(rec, cfg) else "below")
    result = {
        "cartridge": cartridge, "mode": "score", "model": cfg["model"], "effort": cfg["effort"],
        "min_score": cfg["min_score"], "variants_asked": 1, "max_usd_per_page": cfg["max_usd_per_page"],
        "status": status, "chosen": 1 if scored else None,
        "score": rec["judge"]["score"] if scored else None,
        "subscores": rec["judge"]["subscores"] if scored else None,
        "top_issues": rec["judge"]["top_issues"] if scored else [],
        "verdict": rec["judge"]["verdict"] if scored else None,
        "hard_checks": rec.get("hard_checks") or {}, "fonts": rec.get("fonts"),
        "look": rec.get("look"), "hero_style": rec.get("hero_style"), "variants": [rec],
        "rounds": [{"round": 1, "variants": [1]}], "judge_calls": scorer.calls, "cost_usd": round(scorer.cost, 6),
        "notes": [rec["judge_error"]] if rec.get("judge_error") else [],
        "scored_at": datetime.datetime.now().isoformat(timespec="seconds"),
    }
    (design_dir / SCORES_FILE).write_text(json.dumps(result, indent=2) + "\n")
    if log is not None:
        log.event("design_gate", f"{cartridge}: {summary_line(result)}")
    return result


def load_scores(page_dir):
    """page_dir/design/scores.json, or None."""
    try:
        data = json.loads((design_dir_for(page_dir) / SCORES_FILE).read_text())
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def summary_line(result):
    """design gate: N variants, M judge calls, $X -- plus the outcome."""
    score = result.get("score")
    return (f"design gate: {len(result.get('variants') or [])} variants, {result.get('judge_calls', 0)} judge calls, "
            f"${float(result.get('cost_usd') or 0):.4f}; {result.get('status', '').upper()} "
            + (f"score {score} (min {result.get('min_score'):g}), variant {result.get('chosen')}"
               if score is not None else "no score"))


# ---------------------------------------------------------------------------
# REVIEW.md
# ---------------------------------------------------------------------------

STATUS_WORDS = {"pass": "PASS", "warn": "WARN (below min_score; mode warn, the run went on)",
                "stop": "STOP (below min_score; mode enforce)", "unscored": "UNSCORED",
                "below": "below min_score"}


def _layout_label(rec):
    bits = [f"look {rec.get('look')}" if rec.get("look") else "",
            f"{rec.get('hero_style')} first screen" if rec.get("hero_style") else ""]
    return ", ".join(b for b in bits if b) or "the page's own layout"


def review_lines(design):
    """The REVIEW.md "## Design" section for {cartridge: result}. Empty when
    the gate did not run (mode off)."""
    if not design:
        return []
    lines = ["", "## Design",
             "(cycle 92 design gate: screenshots, hard checks without a model, and a vision judge scoring the",
             "phone page and the desktop first screen against harness/design_rubric.md plus the tenant's",
             "design-rubric.md; Jev's copy score is separate)"]
    total = 0.0
    for cartridge, r in design.items():
        total += float(r.get("cost_usd") or 0)
        lines += ["", f"### {cartridge}",
                  f"- Result: {STATUS_WORDS.get(r.get('status'), r.get('status'))}"]
        if r.get("score") is not None:
            lines.append(f"- Chosen: variant {r['chosen']} ({_layout_label(r)}), score {r['score']} "
                         f"(min {r['min_score']:g}), verdict {r.get('verdict')}")
            subs = r.get("subscores") or {}
            lines.append("- Subscores: " + ", ".join(f"{k} {subs.get(k) if subs.get(k) is not None else '-'}"
                                                      for k in SUBSCORES))
        lines.append(f"- Judge: {r.get('model')} (effort {r.get('effort')}); {r.get('judge_calls', 0)} call(s); "
                     f"cost ${float(r.get('cost_usd') or 0):.4f} (cap ${float(r.get('max_usd_per_page') or 0):.2f}/page)")
        fonts = r.get("fonts") or {}
        if fonts:
            fallback = fonts.get("fallback") or []
            lines.append(f"- Fonts: {'fallback (' + ', '.join(fallback) + ')' if fallback else 'loaded'}"
                         + (f"; loaded {', '.join(fonts.get('loaded') or [])}" if fonts.get("loaded") else ""))
        for note in r.get("notes") or []:
            lines.append(f"- **Note:** {note}")
        if r.get("top_issues"):
            lines.append("- Top issues (judge):")
            for issue in r["top_issues"]:
                lines.append(f"  - {issue.get('what')} ({issue.get('where')}). Fix: {issue.get('fix_as_layout_choice')}")
        checks = r.get("hard_checks") or {}
        if checks:
            lines.append("- Hard checks: " + "; ".join(f"{k} {c['status']} ({c['detail']})" for k, c in checks.items()))
        lines += ["", "| Variant | Round | Layout | Score | Verdict | Hard-check fails | Cost | Shot |",
                  "|---|---|---|---|---|---|---|---|"]
        for v in r.get("variants") or []:
            judged = v.get("judge") or {}
            shot = v.get("shots", {}).get("phone")
            link = f"[phone]({cartridge}/design/{shot})" if shot else "-"
            lines.append(
                f"| {v['n']}{' (chosen)' if v['n'] == r.get('chosen') else ''} | {v.get('round', 1)} | "
                f"{_layout_label(v)} | {judged.get('score', '-') if judged else '-'} | "
                f"{judged.get('verdict', '-') if judged else (v.get('judge_error') or '-')} | "
                f"{', '.join(v.get('hard_fail') or []) or 'none'} | ${float(v.get('cost_usd') or 0):.4f} | {link} |")
        chosen = next((v for v in r.get("variants") or [] if v["n"] == r.get("chosen")), None)
        if chosen and chosen.get("shots", {}).get("phone"):
            lines += ["", f"![{cartridge} chosen variant, phone]({cartridge}/design/{chosen['shots']['phone']})"]
            if chosen["shots"].get("desktop"):
                lines.append(f"![{cartridge} chosen variant, desktop first screen]"
                             f"({cartridge}/design/{chosen['shots']['desktop']})")
    lines += ["", f"- Design gate total cost: ${total:.4f}"]
    return lines


# ---------------------------------------------------------------------------
# harness design report
# ---------------------------------------------------------------------------

def _all_scores(tenant):
    roots = [Path(tenant.out_dir), Path(tenant.library_dir)]
    for root in roots:
        if not root.is_dir():
            continue
        for path in sorted(root.rglob(f"{DESIGN_DIR}/{SCORES_FILE}")):
            try:
                data = json.loads(path.read_text())
            except (OSError, ValueError):
                continue
            if isinstance(data, dict):
                yield path, data


def report(tenant):
    """Scores by look and by first screen over every judged variant in the
    tenant's runs and Library, the average gate cost per page, and the most
    common top issues."""
    by_look, by_hero, costs, issues = {}, {}, [], {}
    pages = 0
    for _path, data in _all_scores(tenant):
        pages += 1
        costs.append(float(data.get("cost_usd") or 0))
        for v in data.get("variants") or []:
            judged = v.get("judge")
            if not judged:
                continue
            key_look = f"{data.get('cartridge')}:{v.get('look') or 'default'}"
            by_look.setdefault(key_look, []).append(judged["score"])
            if v.get("hero_style"):
                by_hero.setdefault(v["hero_style"], []).append(judged["score"])
            for issue in judged.get("top_issues") or []:
                what = (issue.get("what") or "").strip()
                if what:
                    issues[what] = issues.get(what, 0) + 1

    def table(groups):
        return sorted(({"key": k, "n": len(v), "mean": round(statistics.mean(v), 1), "min": min(v), "max": max(v)}
                       for k, v in groups.items()), key=lambda row: -row["mean"])

    return {"pages": pages, "by_look": table(by_look), "by_hero": table(by_hero),
            "avg_cost_usd": round(statistics.mean(costs), 4) if costs else 0.0,
            "worst_issues": sorted(({"what": k, "count": n} for k, n in issues.items()),
                                   key=lambda row: -row["count"])[:10]}


def format_report(data):
    lines = [f"design report: {data['pages']} scored page(s), average gate cost ${data['avg_cost_usd']:.4f} per page"]
    for title, rows in (("by look", data["by_look"]), ("by first screen", data["by_hero"])):
        lines.append(f"\n{title}:")
        lines += [f"  {r['key']:<40} n={r['n']:<3} mean {r['mean']:<5} min {r['min']:<3} max {r['max']}" for r in rows] \
            or ["  (none)"]
    lines.append("\nmost common issues:")
    lines += [f"  {r['count']}x {r['what']}" for r in data["worst_issues"]] or ["  (none)"]
    return "\n".join(lines)
