"""Deterministic checks for the design-skill rules we *took*.

Same problem-dict shape as claims.gate_page_json / pagechecks
({path, issue}); empty means pass. Hard checks are wired into
repair.check_page_gates so the writer can repair them. Soft checks
are REVIEW.md advisories via repair.find_soft_check_warnings.

The tenant's own hype list (elevate / unleash / game-changer / ...) stays
the tenant's -- the existing synonym repair owns those. This gate covers
the skill's leftover cliches and the filler/dead-link tells that are
never legitimate on any tenant.
"""
import re

from ..textutil import NON_PROSE_KEYS, walk_page
from . import LANDING_CARTRIDGES

# B8 filler -- phrases, not bare tokens. "acme" alone is a plausible tenant
# name (the suite uses it as a fixture company); "Acme Corp" is the tell.
_FILLER_PHRASES = (
    "lorem ipsum",
    "john doe",
    "jane doe",
    "acme corp",
    "acme inc",
    "acme corporation",
    "smartflow",
)

# B8 cliches the tenant hype list does not already own.
_CLICHE_PHRASES = (
    "in the world of",
    "next-gen",
    "next gen",
    "seamless",
    "delve",
    "tapestry",
)

# A5 / A4 -- generic CTA phrases the skill bans. Cartridges with an
# allowlist never emit these; this is the backstop for one that doesn't.
_GENERIC_CTA = {
    "learn more",
    "submit",
    "click here",
    "read more",
    "sign up",
}

_URL_KEYS = ("url", "cta_url")
_CTA_TEXT_KEYS = ("cta_text",)

_WORD_BOUNDARY_PHRASE = {}


def _phrase_re(phrase):
    compiled = _WORD_BOUNDARY_PHRASE.get(phrase)
    if compiled is None:
        compiled = re.compile(r"\b" + re.escape(phrase) + r"\b", re.IGNORECASE)
        _WORD_BOUNDARY_PHRASE[phrase] = compiled
    return compiled


def _prose_strings(page):
    """Yield (path, text) for every writer-composed string in page.json."""
    for path, node in walk_page(page, skip_keys=NON_PROSE_KEYS):
        if isinstance(node, str) and node:
            yield path, node


def find_filler_copy_violations(page):
    """B8: Lorem Ipsum, John Doe, Acme Corp, SmartFlow. Hard gate."""
    problems = []
    for path, text in _prose_strings(page):
        lowered = text.lower()
        for phrase in _FILLER_PHRASES:
            if _phrase_re(phrase).search(lowered):
                problems.append({
                    "path": path,
                    "issue": f"design-skills B8: filler/placeholder copy {phrase!r} is never legitimate; write real draft copy",
                    "text": text[:120],
                })
                break
    return problems


def find_ai_cliche_violations(page):
    """B8: Seamless / Next Gen / Delve / Tapestry / In the world of.

    Hard gate. The overlapping tenant hype words (elevate, unleash,
    game-changer) are left to vocab + the synonym pre-repair so this
    check does not double-fire and fight the existing fix path."""
    problems = []
    for path, text in _prose_strings(page):
        for phrase in _CLICHE_PHRASES:
            if _phrase_re(phrase).search(text):
                problems.append({
                    "path": path,
                    "issue": f"design-skills B8: AI-cliche {phrase!r} -- rewrite in plain language",
                    "text": text[:120],
                })
                break
    return problems


def find_dead_link_violations(page):
    """B9: a writer-owned url/cta_url of '#' or javascript: is a dead
    control. Same-page anchors ('#faq') are fine. Hard gate."""
    problems = []
    for path, node in walk_page(page):
        if not isinstance(node, dict):
            continue
        for key in _URL_KEYS:
            url = node.get(key)
            if not isinstance(url, str):
                continue
            stripped = url.strip()
            issue = None
            if stripped == "" or stripped == "#":
                issue = "design-skills B9: dead '#' / empty link; point the CTA at a real internal path"
            elif stripped.lower().startswith("javascript:"):
                issue = "design-skills B9: javascript: urls are dead controls"
            if issue:
                problems.append({"path": f"{path}.{key}", "issue": issue, "text": url})
    return problems


def find_generic_cta_warnings(page, cartridge_name):
    """A4/A5: Learn more / Submit / Click here. Soft -- cartridges with an
    allowlist already reject these as a hard CTA-text miss; this flags the
    same tell on a page that somehow still carries one."""
    warnings = []
    seen = set()
    for path, node in walk_page(page):
        if not isinstance(node, dict):
            continue
        candidates = []
        for key in _CTA_TEXT_KEYS:
            value = node.get(key)
            if isinstance(value, str) and value:
                candidates.append((f"{path}.{key}", value))
        # article: {cta: {text, url}} -- only treat `text` as a CTA when
        # a sibling `url` is present, so a random {text: "learn more"}
        # body paragraph does not trip this.
        if "url" in node and isinstance(node.get("text"), str) and node["text"]:
            candidates.append((f"{path}.text", node["text"]))
        for cta_path, text in candidates:
            if text.lower().strip() in _GENERIC_CTA and cta_path not in seen:
                seen.add(cta_path)
                warnings.append(
                    f"{cartridge_name}: {cta_path} CTA {text!r} is a generic "
                    f"(design-skills A4) -- use a verb plus what they get"
                )
    return warnings


def find_tagline_warnings(page, cartridge_name):
    """B11: landing-style cartridges should carry a two-line tagline
    (rendered through the tagline-reveal block). Soft -- article is
    excluded (a mid-page tagline would name the offer inside the warm-up).
    Absent field = warning; present but fewer than two lines = warning."""
    if cartridge_name not in LANDING_CARTRIDGES:
        return []
    tagline = page.get("tagline")
    if tagline is None:
        return [
            f"{cartridge_name}: no tagline field (design-skills B11) -- "
            f"optional two-line benefit statement, rendered through the "
            f"tagline-reveal block, sitting after the hero not stacked under it"
        ]
    lines = tagline.get("lines") if isinstance(tagline, dict) else None
    if not isinstance(lines, list) or len(lines) < 2:
        return [
            f"{cartridge_name}: tagline.lines must be at least two lines "
            f"(design-skills B11); got {lines!r}"
        ]
    return []


def check_page(page, cartridge_name=None):
    """Hard + soft findings for one page.json. Used by the CLI and by the
    pipeline wiring (hard goes through pagechecks; soft through repair)."""
    hard = []
    hard += find_filler_copy_violations(page)
    hard += find_ai_cliche_violations(page)
    hard += find_dead_link_violations(page)
    soft = []
    if cartridge_name:
        soft += find_generic_cta_warnings(page, cartridge_name)
        soft += find_tagline_warnings(page, cartridge_name)
    return {"hard": hard, "soft": soft}
