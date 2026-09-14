"""Cycle 30: tenant design references (tenant.yaml's `design_reference` list).

A tenant may name one or more slugs -- each a folder under
harness/design_skills/design-md/<slug>/DESIGN.md (see design-md/README.md's
own contract) -- as design references. This module parses each referenced
DESIGN.md and derives a small, FIXED set of STRUCTURAL rules only: hero
style (photo-first vs type-first), whitespace scale, max content width
band, heading-to-body size ratio band, image aspect preference, and
section rhythm.

It never reads a DESIGN.md's fonts, colors, component names, or the
referenced brand's own name/description for anything a derived rule
carries -- those stay each tenant's own brand layer. The vendored DESIGN.md
files are NOT uniform (apple/DESIGN.md has real YAML frontmatter with
numeric typography/spacing tokens; tesla/DESIGN.md has none, only prose and
markdown tables) -- every derivation below falls back to a light text scan
of the structural sections when a numeric frontmatter token isn't there,
and degrades to None/a qualitative label rather than guessing a number.

Nothing here touches CSS or harness/render.py -- this cycle only wires the
derived rules into `harness design-skills list --tenant <t>`, the writer
prompt (design_reference_guidance_lines), and gate.py's soft checks; the
images branch owns render.py/structure.css.
"""
import re

from . import SKILLS_DIR

DESIGN_MD_DIR = SKILLS_DIR / "design-md"


class UnknownDesignReference(ValueError):
    """tenant.yaml named a design_reference slug with no design-md pack."""


def _available_slugs():
    if not DESIGN_MD_DIR.is_dir():
        return []
    return sorted(p.name for p in DESIGN_MD_DIR.iterdir() if p.is_dir() and (p / "DESIGN.md").exists())


def _load_text(slug):
    path = DESIGN_MD_DIR / slug / "DESIGN.md"
    if not path.exists():
        raise UnknownDesignReference(
            f"unknown design_reference slug {slug!r}: no {path}. "
            f"Available: {', '.join(_available_slugs()) or '(none)'}."
        )
    return path.read_text()


def _parse_frontmatter(text):
    """(frontmatter_dict, body_text). frontmatter_dict is {} when the file
    has no leading "---" block (tesla's shape) or it doesn't parse as YAML."""
    if not text.startswith("---"):
        return {}, text
    end = text.find("\n---", 3)
    if end == -1:
        return {}, text
    import yaml

    try:
        data = yaml.safe_load(text[3:end]) or {}
    except yaml.YAMLError:
        data = {}
    body = text[end + 4:]
    return (data if isinstance(data, dict) else {}), body


_PX_RE = re.compile(r"(\d{2,5})\s*px")
_ASPECT_RE = re.compile(r"\b(\d{1,2}):(\d{1,2})\b")


def _px_value(token):
    if not isinstance(token, str):
        return None
    m = _PX_RE.search(token)
    return int(m.group(1)) if m else None


def _first_px(line):
    m = _PX_RE.search(line)
    return int(m.group(1)) if m else None


def _lines_containing(body, *keywords):
    return [line for line in body.splitlines() if all(k.lower() in line.lower() for k in keywords)]


def _hero_style(body):
    """"photo-first" when the hero-related lines mention photography/
    imagery (both vendored packs today); "type-first" otherwise -- a design
    reference whose hero is headline-led rather than image-led."""
    hero_text = "\n".join(_lines_containing(body, "hero"))
    if re.search(r"photo|imagery|cinematic", hero_text, re.IGNORECASE):
        return "photo-first"
    return "type-first"


def _whitespace_scale(frontmatter, body):
    """{"ratio": section-padding / base-unit, "label": None} when both
    numeric tokens are present (apple's shape); else a qualitative label
    from the prose ("generous" / "tight" / "standard", tesla's shape)."""
    spacing = frontmatter.get("spacing") if isinstance(frontmatter, dict) else None
    section_px = _px_value(spacing.get("section")) if isinstance(spacing, dict) else None
    base_match = re.search(r"base unit[:\s]*[*_]*\s*(\d{1,3})\s*px", body, re.IGNORECASE)
    base_px = int(base_match.group(1)) if base_match else None
    if section_px and base_px:
        return {"ratio": round(section_px / base_px, 1), "label": None}
    if re.search(r"\bgenerous\b", body, re.IGNORECASE):
        return {"ratio": None, "label": "generous"}
    if re.search(r"\b(tight|dense|compact)\b", body, re.IGNORECASE):
        return {"ratio": None, "label": "tight"}
    return {"ratio": None, "label": "standard"}


def _max_content_width_band(body):
    """(min_px, max_px) from every px figure on a "max width"/"max content
    width" prose line -- markdown table rows (a responsive-breakpoints
    table can carry its own unrelated "max-width container" cell) are
    excluded so a breakpoint figure never leaks in as a content-width one.
    None when no such line has a number."""
    widths = set()
    for line in _lines_containing(body, "width"):
        if line.lstrip().startswith("|"):
            continue
        if "max" in line.lower():
            widths.update(int(n) for n in _PX_RE.findall(line))
    return (min(widths), max(widths)) if widths else None


def _heading_body_ratio_band(frontmatter, body):
    """(min_ratio, max_ratio) of heading-size / body-size. Frontmatter path
    (apple): every typography token whose key looks like a heading
    ("hero"/"display" prefix -- "lead"/"tagline"/etc. are lead paragraph
    styles, not headings) over the "body" token's fontSize.
    Fallback (tesla): a "Hero Title | 40px ..." / "Body Text | 14px ..."
    markdown-table row scan. None when neither yields both a heading and a
    body figure."""
    typography = frontmatter.get("typography") if isinstance(frontmatter, dict) else None
    body_px = None
    heading_px_values = []
    if isinstance(typography, dict):
        body_token = typography.get("body")
        if isinstance(body_token, dict):
            body_px = _px_value(body_token.get("fontSize"))
        for key, token in typography.items():
            if key == "body" or not isinstance(token, dict):
                continue
            if key.startswith(("hero", "display")):
                px = _px_value(token.get("fontSize"))
                if px:
                    heading_px_values.append(px)
    if body_px is None or not heading_px_values:
        for line in body.splitlines():
            if re.search(r"\bhero title\b", line, re.IGNORECASE):
                px = _first_px(line)
                if px:
                    heading_px_values.append(px)
            elif re.search(r"\bbody text\b", line, re.IGNORECASE) and body_px is None:
                body_px = _first_px(line)
    if not body_px or not heading_px_values:
        return None
    ratios = sorted(round(h / body_px, 2) for h in heading_px_values)
    return (ratios[0], ratios[-1])


def _image_aspect_ratios(body):
    """Every "N:M" aspect ratio mentioned on a line about hero/image/crop/
    grid/card imagery, in first-seen order."""
    aspects = []
    for line in body.splitlines():
        if re.search(r"hero|image|crop|grid|card", line, re.IGNORECASE):
            for m in _ASPECT_RE.finditer(line):
                ratio = f"{m.group(1)}:{m.group(2)}"
                if ratio not in aspects:
                    aspects.append(ratio)
    return aspects


_ALTERNATING_RHYTHM_RE = re.compile(
    r"alternat(?:e|ing)\s+(?:light|dark|full-bleed|image|text|section|canvas|canvases|tile|tiles)",
    re.IGNORECASE,
)


def _section_rhythm(body):
    """"alternating" (light/dark or image/text sections alternate --
    requires "alternat(e/ing)" next to a section/canvas/light/dark word, not
    just any use of "alternate"/"alternative" -- a color palette's "alternate
    surface" or a "Secondary CTA -- the alternative action" must not count),
    "single-column-full-viewport" (one full-height section at a time, no
    alternation), or "single-column" (neither signal found)."""
    if _ALTERNATING_RHYTHM_RE.search(body):
        return "alternating"
    if re.search(r"full[- ]viewport|100vh", body, re.IGNORECASE):
        return "single-column-full-viewport"
    return "single-column"


def derive_structural_facts(slug):
    """Every structural fact this module derives from one design-md slug's
    DESIGN.md, keyed by slug. Raises UnknownDesignReference for a slug with
    no design-md pack."""
    text = _load_text(slug)
    frontmatter, body = _parse_frontmatter(text)
    return {
        "slug": slug,
        "hero_style": _hero_style(body),
        "whitespace_scale": _whitespace_scale(frontmatter, body),
        "max_content_width_px": _max_content_width_band(body),
        "heading_body_ratio": _heading_body_ratio_band(frontmatter, body),
        "image_aspect_ratios": _image_aspect_ratios(body),
        "section_rhythm": _section_rhythm(body),
    }


def design_reference_facts(tenant):
    """derive_structural_facts(slug) for every slug in tenant.yaml's
    design_reference list, in order, de-duplicated. [] for a tenant that
    sets no design_reference."""
    slugs = tenant.get("design_reference") or []
    seen = []
    for slug in slugs:
        if slug not in seen:
            seen.append(slug)
    return [derive_structural_facts(slug) for slug in seen]


def _band(bands):
    values = [b for b in bands if b is not None]
    if not values:
        return None
    return (min(v[0] for v in values), max(v[1] for v in values))


def design_reference_rules(tenant):
    """rules.json-style entries (id/title/kind/measurable/action/check/
    harness), derived from this tenant's design_reference DESIGN.md packs
    -- structural only. [] when the tenant sets no design_reference.

    Every entry's action is "adapt" (the same decision harness/design_skills
    /rules.json already uses for "same intent, harness shape") -- a design
    reference informs the writer prompt and, where measurable on page.json,
    a soft gate.py check; it is never a hard visual requirement (fonts,
    colors, and CSS stay out of scope this cycle -- the images branch owns
    render.py/structure.css).

    Only "DR-hero-style" carries a "check" today (gate.py's
    find_design_reference_warnings) -- the other five are informational
    only in this cycle (writer guidance + `list` output), not yet
    measurable against page.json."""
    facts = design_reference_facts(tenant)
    if not facts:
        return []

    sources = [f["slug"] for f in facts]

    hero_styles = {f["hero_style"] for f in facts}
    hero_style = next(iter(hero_styles)) if len(hero_styles) == 1 else "mixed"

    rhythms = {f["section_rhythm"] for f in facts}
    rhythm = next(iter(rhythms)) if len(rhythms) == 1 else "mixed"

    whitespace_labels = sorted({
        f["whitespace_scale"].get("label") or "custom" for f in facts
    })

    width_band = _band([f["max_content_width_px"] for f in facts])
    ratio_band = _band([f["heading_body_ratio"] for f in facts])
    aspects = sorted({a for f in facts for a in f["image_aspect_ratios"]})

    return [
        {
            "id": "DR-hero-style",
            "title": "Hero style",
            "kind": "layout",
            "measurable": True,
            "action": "adapt",
            "check": "hero_image_present_when_photo_first" if hero_style == "photo-first" else None,
            "value": hero_style,
            "guidance": "photo-first hero" if hero_style == "photo-first" else (
                "type-led hero" if hero_style == "type-first" else None
            ),
            "harness": "Derived from this tenant's design_reference packs' hero treatment.",
            "sources": sources,
        },
        {
            "id": "DR-whitespace",
            "title": "Whitespace scale",
            "kind": "layout",
            "measurable": False,
            "action": "adapt",
            "check": None,
            "value": whitespace_labels,
            "guidance": "generous whitespace" if "generous" in whitespace_labels else None,
            "harness": "Derived from this tenant's design_reference packs' section padding.",
            "sources": sources,
        },
        {
            "id": "DR-content-width",
            "title": "Max content width band",
            "kind": "layout",
            "measurable": False,
            "action": "adapt",
            "check": None,
            "value": width_band,
            "guidance": None,
            "harness": "Derived from this tenant's design_reference packs' max content width.",
            "sources": sources,
        },
        {
            "id": "DR-heading-body-ratio",
            "title": "Heading-to-body size ratio band",
            "kind": "layout",
            "measurable": False,
            "action": "adapt",
            "check": None,
            "value": ratio_band,
            "guidance": None,
            "harness": "Derived from this tenant's design_reference packs' type scale.",
            "sources": sources,
        },
        {
            "id": "DR-image-aspect",
            "title": "Image aspect preference",
            "kind": "layout",
            "measurable": False,
            "action": "adapt",
            "check": None,
            "value": aspects,
            "guidance": None,
            "harness": "Derived from this tenant's design_reference packs' image crops.",
            "sources": sources,
        },
        {
            "id": "DR-section-rhythm",
            "title": "Section rhythm",
            "kind": "layout",
            "measurable": False,
            "action": "adapt",
            "check": None,
            "value": rhythm,
            "guidance": "one column" if rhythm != "alternating" else "alternating image/text sections",
            "harness": "Derived from this tenant's design_reference packs' section pattern.",
            "sources": sources,
        },
    ]


def design_reference_guidance_lines(tenant):
    """Short, tenant-neutral guidance lines for the writer prompt, e.g.
    ["photo-first hero", "generous whitespace", "one column"]. [] when the
    tenant sets no design_reference."""
    return [rule["guidance"] for rule in design_reference_rules(tenant) if rule.get("guidance")]
