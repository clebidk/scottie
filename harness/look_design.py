"""Cycle 90: the DESIGN level of the "Import a page" observation, the
exemplar and the fidelity score (harness/look_import.py calls this).

Cycle 87's observation held section roles and counts only ("hero >
content > cta, 2/3-column grids, 1 dark band"), so the model had nothing
to reproduce and fell back to the default look. This module adds, still
deterministic (stdlib + html5lib, nothing is run, no model):

  - a small CSS cascade over the page's own CSS (`Css`): selectors with
    tag/class/id/attribute compounds and the four combinators, specificity
    and source order, inheritance of the type properties, var() resolved
    through the page's custom properties and the tenant's --ps-* tokens
    (brand/base.css). State selectors (:hover, :checked, :has()) are
    skipped; structural pseudo-classes (:nth-of-type ...) always match --
    an approximation, written down in docs/LISTICLE-SITE.md;
  - per section: a compact DOM skeleton (tag + class names + text length,
    never the words) and a style summary (ground, text contrast, width,
    grid/flex columns, padding scale, radius class, image treatment,
    headline type);
  - distinctive PATTERNS found by rules (PATTERNS below), each tied to the
    section that holds it, and a plain-English design brief;
  - the page's CSS for the prompt, sanitised (no @font-face, @import,
    url(), comments; colour literals become the tenant token they equal,
    or a <light>/<dark>/<mid> placeholder), capped at 8 KB;
  - exemplars: hand-made looks (library.json source.kind "pack") with the
    observation of the page they were made from;
  - the fidelity score of a rendered candidate against the observation.
"""
import difflib
import json
import re
from collections import Counter
from pathlib import Path

from . import look_import as li

# The distinctive patterns, in the order the brief names them. A look
# realises each one on an element that carries data-look-pattern="<name>".
PATTERNS = {
    "display-letters": "giant display letters flanking an image",
    "pill-image": "a pill-shaped image window (a wide box with fully rounded ends)",
    "caps-headline": "an all-caps display headline",
    "dark-hero": "a dark first screen (a dark ground or a photo under a dark scrim)",
    "dark-page": "a dark page (dark ground everywhere, light text)",
    "big-numerals": "numbered items with big numerals",
    "alternating-rows": "image/text rows that alternate sides",
    "trust-bar": "a short trust bar near the top",
    "hotspots": "hotspot labels placed over a product image",
    "sticky-bar": "a sticky bottom bar with the CTA",
    "check-rows": "check-mark rows",
    "icon-row": "a row of icons with short labels",
    "stat-strip": "a strip of big numbers with small labels",
    "model-picker": "a model picker (options to choose between)",
    "product-grid": "a grid of product cards",
    "faq-accordion": "an FAQ accordion (questions that open)",
}
PATTERN_NAMES = tuple(PATTERNS)

MAX_OBSERVATION_BYTES = 12 * 1024
MAX_CSS_CHARS = 8 * 1024
SKELETON_NODES = (40, 28, 20, 12, 6, 0)
SKELETON_DEPTH = 4
FIDELITY_THRESHOLD = 0.7
INHERITED = ("color", "font-family", "font-size", "font-weight", "text-transform", "letter-spacing")
_SKEL_SKIP = frozenset({"script", "style", "br", "wbr", "source", "path", "meta", "link", "title", "template"})
_STATE_RE = re.compile(r":(?:hover|focus|focus-visible|focus-within|active|visited|checked|disabled|enabled|"
                       r"target|placeholder-shown|invalid|valid|has|open|indeterminate)\b", re.I)
_DARK_NAME_RE = re.compile(r"dark|ink|band|black|basalt|night|charcoal|onyx")
_LIGHT_NAME_RE = re.compile(r"on-dark|bg|white|light|cream|paper|surface|snow")
_NUMERAL_RE = re.compile(r"^\s*(?:no\.?\s*)?0?\d{1,2}[.)]?\s*$", re.I)
_PRICE_RE = re.compile(r"[$€£]\s?\d|\d[\d,.]*\s?(?:usd|eur|gbp)\b", re.I)
_CHECK_RE = re.compile(r"\bcheck|\btick|\bcheckmark")
_CHECK_CHARS = ("✓", "✔", "\\2713", "\\2714", "✅")


# ---------------------------------------------------------------------------
# Tokens
# ---------------------------------------------------------------------------

def tenant_tokens(tenant):
    """{--name: value} of the tenant's brand/base.css custom properties
    (first definition wins), or {} without a tenant."""
    if tenant is None:
        return {}
    try:
        css = (Path(tenant.brand_dir) / "base.css").read_text()
    except OSError:
        return {}
    out = {}
    for name, value in re.findall(r"(--[\w-]+)\s*:\s*([^;}]+)", css):
        out.setdefault(name.lower(), value.strip())
    return out


def _balanced(text, start):
    """Index just past the parenthesis that closes the one at text[start]."""
    depth = 0
    for i in range(start, len(text)):
        if text[i] == "(":
            depth += 1
        elif text[i] == ")":
            depth -= 1
            if depth == 0:
                return i + 1
    return len(text)


def _px(value, *, largest=True):
    """A length in px: px, rem/em (x16), clamp()/min()/max() (the largest
    px argument, or the smallest with largest=False); None otherwise."""
    value = (value or "").strip().lower()
    found = []
    for num, unit in re.findall(r"(-?\d+(?:\.\d+)?)(px|rem|em)\b", value):
        found.append(float(num) * (16 if unit in ("rem", "em") else 1))
    if not found:
        m = re.fullmatch(r"0+(?:\.0+)?", value)
        return 0.0 if m else None
    return max(found) if largest else min(found)


def _luma(rgb):
    r, g, b = (c / 255 for c in rgb)
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


# ---------------------------------------------------------------------------
# Selectors
# ---------------------------------------------------------------------------

def _split_selector(sel):
    """[(combinator, compound)] left to right; the first combinator is None."""
    out, cur, depth, comb, i = [], "", 0, None, 0
    s = sel.strip()
    while i < len(s):
        ch = s[i]
        if ch in "([":
            depth += 1
        elif ch in ")]":
            depth -= 1
        if depth == 0 and (ch.isspace() or ch in ">+~"):
            if cur:
                out.append((comb, cur))
                cur = ""
            c = " "
            while i < len(s) and (s[i].isspace() or s[i] in ">+~"):
                if s[i] in ">+~":
                    c = s[i]
                i += 1
            comb = c
            continue
        cur += ch
        i += 1
    if cur:
        out.append((comb, cur))
    return out


def _compound(text):
    """A parsed compound selector, or None when it can never match a static
    page (a state pseudo-class, an unknown pseudo-element)."""
    pseudo = None
    m = re.search(r"::?(before|after)\b", text, re.I)
    if m:
        pseudo = m.group(1).lower()
        text = text[:m.start()] + text[m.end():]
    if "::" in text or _STATE_RE.search(text):
        return None
    text = re.sub(r":root\b", "html", text)
    nots = []
    while True:
        m = re.search(r":(?:not|is|where|nth-[\w-]+|lang|dir)\(", text)
        if not m:
            break
        end = _balanced(text, m.end() - 1)
        inner = text[m.end():end - 1].strip()
        if m.group(0) == ":not(" and re.fullmatch(r"[\w.#-]+", inner):
            nots.append(_compound(inner))
        text = text[:m.start()] + text[end:]
    text = re.sub(r":[\w-]+", "", text)
    attrs = re.findall(r"\[\s*([\w:-]+)\s*(?:[~|^$*]?=\s*[\"']?([^\]\"']*)[\"']?)?\s*\]", text)
    text = re.sub(r"\[[^\]]*\]", "", text)
    tag = re.match(r"^([a-zA-Z][\w-]*)", text)
    return {"tag": tag.group(1).lower() if tag else None, "ids": re.findall(r"#([\w-]+)", text),
            "classes": re.findall(r"\.([\w-]+)", text), "attrs": attrs, "pseudo": pseudo,
            "nots": [n for n in nots if n]}


def _match_compound(el, comp):
    if comp["tag"] and li._tag(el) != comp["tag"]:
        return False
    if comp["ids"] and any(el.get("id") != i for i in comp["ids"]):
        return False
    if comp["classes"]:
        classes = set((el.get("class") or "").split())
        if not set(comp["classes"]) <= classes:
            return False
    for name, value in comp["attrs"]:
        got = el.get(name)
        if got is None or (value and value not in got):
            return False
    return not any(_match_compound(el, n) for n in comp.get("nots") or ())


class _Rule:
    __slots__ = ("selector", "parts", "spec", "order", "decls", "desktop", "pseudo")

    def __init__(self, selector, parts, spec, order, decls, desktop, pseudo):
        self.selector, self.parts, self.spec, self.order = selector, parts, spec, order
        self.decls, self.desktop, self.pseudo = decls, desktop, pseudo


def _expand_font(decls):
    """`font: 500 11.5px/1.4 var(--x)` -> font-weight/size/family when the
    rule does not set them on their own."""
    value = decls.get("font")
    if not value:
        return decls
    out = dict(decls)
    m = re.search(r"((?:\d+(?:\.\d+)?(?:px|rem|em|%)|clamp\([^)]*\)))(?:\s*/\s*[\w.%]+)?\s+(.+)$", value)
    if m:
        out.setdefault("font-size", m.group(1))
        out.setdefault("font-family", m.group(2).strip())
    w = re.match(r"\s*(?:italic\s+)?(\d00|bold|normal)\b", value)
    if w:
        out.setdefault("font-weight", w.group(1))
    return out


class Css:
    """The page's CSS as a cascade over one parsed tree (see the module
    docstring for what it approximates)."""

    def __init__(self, css_texts, tokens=None):
        self.vars = dict(tokens or {})
        self.raw = []
        self.rules = []
        self.index = {}
        page_vars = {}
        order = 0
        for css in css_texts:
            for sel, decls, desktop in li.css_rules(css):
                self.raw.append((sel, decls, desktop))
                for key, value in decls.items():
                    if key.startswith("--"):
                        page_vars[key] = value
                if ":has(" in sel:
                    continue
                parts = []
                for comb, text in _split_selector(sel):
                    comp = _compound(text)
                    if comp is None:
                        parts = None
                        break
                    parts.append((comb, comp))
                if not parts:
                    continue
                pseudo = parts[-1][1]["pseudo"]
                if any(c["pseudo"] for _cb, c in parts[:-1]):
                    continue
                spec = (sum(len(c["ids"]) for _cb, c in parts),
                        sum(len(c["classes"]) + len(c["attrs"]) + len(c["nots"]) for _cb, c in parts)
                        + sel.count(":nth-"),
                        sum(1 for _cb, c in parts if c["tag"]))
                order += 1
                rule = _Rule(sel, parts, spec, order, _expand_font(decls), desktop, pseudo)
                self.rules.append(rule)
                last = parts[-1][1]
                key = (("#", last["ids"][0]) if last["ids"] else (".", last["classes"][0]) if last["classes"]
                       else ("t", last["tag"]) if last["tag"] else ("*", None))
                self.index.setdefault(key, []).append(rule)
        self.vars.update(page_vars)
        self.parent, self.prev = {}, {}
        self._declared, self._computed = {}, {}

    # -- tree
    def bind(self, tree):
        for parent in tree.iter():
            kids = [k for k in parent if isinstance(k.tag, str)]
            for i, kid in enumerate(kids):
                self.parent[kid] = parent
                self.prev[kid] = kids[i - 1] if i else None
        return self

    def _candidates(self, el):
        keys = [("*", None), ("t", li._tag(el))]
        keys += [(".", c) for c in (el.get("class") or "").split()]
        if el.get("id"):
            keys.append(("#", el.get("id")))
        seen, out = set(), []
        for key in keys:
            for rule in self.index.get(key, ()):
                if rule.order not in seen:
                    seen.add(rule.order)
                    out.append(rule)
        return out

    def _matches(self, el, parts):
        def rec(node, idx):
            comb, comp = parts[idx]
            if not _match_compound(node, comp):
                return False
            if idx == 0:
                return True
            if comb == ">":
                p = self.parent.get(node)
                return p is not None and rec(p, idx - 1)
            if comb == " ":
                p = self.parent.get(node)
                while p is not None:
                    if rec(p, idx - 1):
                        return True
                    p = self.parent.get(p)
                return False
            s = self.prev.get(node)
            if comb == "+":
                return s is not None and rec(s, idx - 1)
            while s is not None:   # "~"
                if rec(s, idx - 1):
                    return True
                s = self.prev.get(s)
            return False

        return rec(el, len(parts) - 1)

    def declared(self, el, desktop=False, pseudo=None):
        key = (el, desktop, pseudo)
        if key in self._declared:
            return self._declared[key]
        matched = [r for r in self._candidates(el)
                   if r.pseudo == pseudo and (desktop or not r.desktop) and self._matches(el, r.parts)]
        matched.sort(key=lambda r: (r.spec, r.order))
        out = {}
        for rule in matched:
            out.update(rule.decls)
        if pseudo is None:
            out.update(_expand_font(li._declarations(el.get("style") or "")))
        self._declared[key] = out
        return out

    def computed(self, el, desktop=False):
        key = (el, desktop)
        if key in self._computed:
            return self._computed[key]
        out = dict(self.declared(el, desktop))
        parent = self.parent.get(el)
        if parent is not None:
            inherited = self.computed(parent, desktop)
            for prop in INHERITED:
                if prop not in out and prop in inherited:
                    out[prop] = inherited[prop]
        self._computed[key] = out
        return out

    # -- values
    def resolve(self, value, depth=0):
        """value with every var(--x, fallback) replaced (page custom
        properties, then the tenant tokens, then the fallback, then a guess
        from the name)."""
        value = value or ""
        out, i = "", 0
        while True:
            j = value.find("var(", i)
            if j < 0:
                return out + value[i:]
            end = _balanced(value, j + 3)
            inner = value[j + 4:end - 1]
            name, _, fallback = inner.partition(",")
            name = name.strip().lower()
            got = self.vars.get(name)
            if got is None:
                got = fallback.strip() if fallback.strip() else _guess_colour(name)
            out += value[i:j] + (self.resolve(got, depth + 1) if depth < 8 else got)
            i = end

    def colour(self, value):
        """(r, g, b) of a colour value, or None (transparent, not a colour)."""
        if not value:
            return None
        resolved = self.resolve(value)
        if re.search(r"\btransparent\b", resolved, re.I) and not li._HEX_RE.search(resolved):
            return None
        return li._rgb(resolved)

    def background(self, el, desktop=False):
        """The ground behind el: its own or the nearest ancestor's opaque
        background colour (white when nothing sets one)."""
        node = el
        while node is not None:
            d = self.declared(node, desktop)
            for prop in ("background-color", "background"):
                rgb = self.colour(d.get(prop))
                if rgb is not None:
                    return rgb
            node = self.parent.get(node)
        return (255, 255, 255)

    def text_colour(self, el, desktop=False):
        return self.colour(self.computed(el, desktop).get("color")) or (26, 26, 26)


def _guess_colour(name):
    """A colour for an unresolved custom property, from its name only."""
    name = name.lower()
    if _LIGHT_NAME_RE.search(name):
        return "#ffffff"
    if _DARK_NAME_RE.search(name):
        return "#161817"
    return ""


def font_token(raw):
    """display-wide / display-acid / heading / sans / serif / other for a
    font-family value (the var name first, then the family names)."""
    low = (raw or "").lower()
    if not low.strip():
        return None
    if "display-wide" in low or "grotesk wide" in low or re.search(r"\bwide\b|extended|expanded", low):
        return "display-wide"
    if "acid" in low or "display" in low:
        return "display-acid"
    if "heading" in low or "poppins" in low or "montserrat" in low:
        return "heading"
    if ("serif" in low and "sans" not in low) or "epika" in low:
        return "serif"
    if re.search(r"sans|inter|helvetica|arial|system-ui|-apple-system|dm ", low):
        return "sans"
    return "other"


# ---------------------------------------------------------------------------
# Per-section summary
# ---------------------------------------------------------------------------

def _elements(el):
    return [d for d in el.iter() if isinstance(d.tag, str)]


def _depth_iter(el, max_depth):
    stack = [(el, 0)]
    while stack:
        node, depth = stack.pop(0)
        yield node, depth
        if depth < max_depth:
            stack.extend((k, depth + 1) for k in node if isinstance(k.tag, str))


def _cols(css, el, desktop):
    d = css.computed(el, desktop)
    display = (d.get("display") or "").strip()
    kids = [k for k in el if isinstance(k.tag, str) and li._tag(k) not in _SKEL_SKIP]
    if display in ("grid", "inline-grid"):
        n = li._track_count(css.resolve(d.get("grid-template-columns") or ""))
        return "grid", n or 1
    if display in ("flex", "inline-flex"):
        direction = d.get("flex-direction") or "row"
        return "flex", (1 if "column" in direction else min(len(kids), 6) or 1)
    return display or "block", 1


def _layout(css, section):
    for node, depth in _depth_iter(section, 3):
        kids = [k for k in node if isinstance(k.tag, str) and li._has_content(k)]
        if len(kids) < 2:
            continue
        display, cols = _cols(css, node, False)
        display_d, cols_d = _cols(css, node, True)
        if display_d in ("grid", "flex") or display in ("grid", "flex"):
            out = {"display": display_d if display_d in ("grid", "flex") else display, "cols": cols,
                   "cols_desktop": cols_d}
            return out
    return {"display": "block", "cols": 1, "cols_desktop": 1}


def _radius_class(value):
    value = (value or "").strip().lower()
    if not value or value in ("0", "0px", "none"):
        return "square"
    if "50%" in value:
        return "round"
    px = _px(value)
    if px is None:
        return None
    if px >= 100:
        return "pill"
    return "square" if px == 0 else ("4px" if px <= 8 else "rounded")


def _img_radius(css, img):
    for node in (img, css.parent.get(img), css.parent.get(css.parent.get(img)) if css.parent.get(img) is not None
                 else None):
        if node is None:
            continue
        r = css.resolve(css.declared(node).get("border-radius") or "")
        if r:
            return _radius_class(r)
    return "square"


def _aspect_of(css, el):
    """Width/height of a box: its aspect-ratio, else its img's attributes."""
    for desktop in (False, True):
        m = re.match(r"\s*(\d+(?:\.\d+)?)\s*(?:/\s*(\d+(?:\.\d+)?))?", css.declared(el, desktop).get("aspect-ratio")
                     or "")
        if m:
            return float(m.group(1)) / float(m.group(2) or 1)
    img = el if li._tag(el) == "img" else next((d for d in el.iter() if li._tag(d) == "img"), None)
    return li._aspect(img) if img is not None else None


def _covers(d):
    pos = (d.get("position") or "").strip()
    inset = (d.get("inset") or "").replace(" ", "")
    full = (d.get("width") or "").strip() == "100%" and (d.get("height") or "").strip() == "100%"
    return pos == "absolute" and (inset in ("0", "0px") or full)


def _scrim(css, el):
    """True when el (or its first child) paints a ::before/::after layer
    with a background over its whole box: a scrim over a photo."""
    for node in (el,) + tuple(k for k in el if isinstance(k.tag, str))[:1]:
        for pseudo in ("after", "before"):
            d = css.declared(node, False, pseudo)
            paint = css.resolve(d.get("background") or d.get("background-color") or "").strip().lower()
            if d and _covers(d) and paint and paint not in ("none", "transparent") and "url(" not in paint:
                return True
    return False


def _image_treatment(css, section):
    imgs = [d for d in section.iter() if li._tag(d) == "img"]
    if not imgs:
        return None
    img = imgs[0]
    chain, node = [], img
    while node is not None and node is not section:
        chain.append(node)
        node = css.parent.get(node)
    chain.append(section)
    for n in chain[:3]:
        box = css.parent.get(n)
        if _covers(css.declared(n)) and box is not None and li._words(li._text(box)) > 3:
            return "overlay"
    radius = _img_radius(css, img)
    if radius in ("pill", "round"):
        return "masked"
    constrained = any(_px(css.declared(n).get("max-width") or "") for n in chain[1:])
    width = (css.declared(img).get("width") or "").strip()
    if not constrained and width in ("100%", "100vw"):
        return "full-bleed"
    return "contained"


def _pad(css, section):
    best = None
    for node in (section,) + tuple(k for k in section if isinstance(k.tag, str))[:1]:
        for desktop in (False, True):
            d = css.declared(node, desktop)
            for prop in ("padding-top", "padding-bottom", "padding"):
                value = d.get(prop)
                if value:
                    px = _px(css.resolve(value.split()[0] if prop == "padding" else value))
                    if px is not None:
                        best = max(best or 0, px)
    if best is None:
        return None
    return "tight" if best < 24 else ("normal" if best <= 64 else "roomy")


def _type(css, el):
    head = next((d for t in ("h1", "h2", "h3") for d in el.iter() if li._tag(d) == t), None)
    if head is None:
        return None
    mobile, desktop = css.computed(head), css.computed(head, True)
    out = {"el": li._tag(head)}
    size = (mobile.get("font-size") or "").strip()
    if size:
        out["size"] = css.resolve(size)
    size_d = (desktop.get("font-size") or "").strip()
    if size_d and size_d != size:
        out["size_desktop"] = css.resolve(size_d)
    for key, prop in (("transform", "text-transform"), ("spacing", "letter-spacing"), ("weight", "font-weight")):
        value = (mobile.get(prop) or "").strip()
        if value and value not in ("none", "normal", "0"):
            out[key] = css.resolve(value)
    token = font_token(mobile.get("font-family"))
    if token:
        out["font"] = token
    return out


def section_style(css, el):
    bg = css.background(el)
    first_text = next((d for t in ("h1", "h2", "p", "h3") for d in el.iter() if li._tag(d) == t), el)
    fg = css.text_colour(first_text)
    out = {"bg": "dark" if li._is_dark(bg) else "light",
           "text": "dark" if li._is_dark(fg) else "light",
           "contrast": "high" if abs(_luma(bg) - _luma(fg)) >= 0.5 else "low"}
    for node, _depth in _depth_iter(el, 3):
        for desktop in (False, True):
            px = _px(css.resolve(css.declared(node, desktop).get("max-width") or ""))
            if px and px >= 320:
                out["max_width"] = int(px)
                break
        if "max_width" in out:
            break
    out["layout"] = _layout(css, el)
    pad = _pad(css, el)
    if pad:
        out["pad"] = pad
    radii = Counter(_img_radius(css, img) for img in el.iter() if li._tag(img) == "img")
    if radii:
        out["radius"] = radii.most_common(1)[0][0]
    image = _image_treatment(css, el)
    if image:
        out["image"] = image
        if image == "overlay" and any(_scrim(css, n) for n in (el,) + tuple(_elements(el)[:4])):
            out["scrim"] = True
    position = (css.declared(el).get("position") or "").strip()
    if position in ("fixed", "sticky"):
        out["pinned"] = True
    t = _type(css, el)
    if t:
        out["type"] = t
    return out


def skeleton(el, max_nodes=40, max_depth=SKELETON_DEPTH):
    """The section's DOM as short lines: tag.class (at most 3 classes) with
    xN for N identical siblings and the text length in characters. Never
    the words."""
    def label(node):
        classes = [c for c in (node.get("class") or "").split() if len(c) <= 24][:3]
        lab = li._tag(node) + "".join(f".{c}" for c in classes)
        if li._tag(node) == "img" and li._aspect(node):
            lab += f" {li._aspect(node)}:1"
        return lab

    lines = []

    def walk(node, depth):
        kids = [k for k in node if isinstance(k.tag, str) and li._tag(k) not in _SKEL_SKIP]
        i = 0
        while i < len(kids):
            if len(lines) >= max_nodes:
                return
            kid, n = kids[i], 1
            lab = label(kid)
            while i + n < len(kids) and label(kids[i + n]) == lab:
                n += 1
            chars = len(li._text(kid))
            lines.append(" " * depth + lab + (f" x{n}" if n > 1 else "") + (f" {chars}c" if chars else ""))
            if depth < max_depth and li._tag(kid) != "svg":
                walk(kid, depth + 1)
            i += n

    if max_nodes:
        lines.append(label(el) + f" {len(li._text(el))}c")
        walk(el, 1)
    return lines


# ---------------------------------------------------------------------------
# Patterns
# ---------------------------------------------------------------------------

def _font_px(css, el):
    return _px(css.resolve(css.computed(el, True).get("font-size") or css.computed(el).get("font-size") or ""))


def _own_text(el):
    return li._text(el)


def _has_img(el):
    return any(li._tag(d) in ("img", "picture", "video") for d in el.iter())


def detect_patterns(css, body, nodes_, sections):
    """[{"name", "section", "evidence"}] -- each pattern once, at the first
    section that realises it."""
    owner = {}
    for i, node in enumerate(nodes_):
        for d in node.iter():
            owner.setdefault(d, i)
    found = {}

    def add(name, el, evidence):
        if name in found:
            return
        idx = owner.get(el)
        if idx is None:
            node = el
            while node is not None and node not in owner:
                node = css.parent.get(node)
            idx = owner.get(node)
        found[name] = {"name": name, "section": idx, "evidence": evidence}

    hero_idx = next((s["index"] for s in sections if s["role"] == "hero"), None)
    all_els = _elements(body)

    # display letters: short big text, an image box, short big text -- siblings
    for el in all_els:
        kids = [k for k in el if isinstance(k.tag, str)]
        if len(kids) < 3:
            continue
        for a, b, c in zip(kids, kids[1:], kids[2:]):
            if not _has_img(b) or _has_img(a) or _has_img(c):
                continue
            ta, tc = _own_text(a), _own_text(c)
            if not (1 <= len(ta) <= 3 and 1 <= len(tc) <= 3):
                continue
            sizes = [_font_px(css, a) or 0, _font_px(css, c) or 0]
            if min(sizes) >= 96:
                add("display-letters", el, f"two {len(ta)}-letter marks at up to {int(max(sizes))}px around an "
                                           f"image")
                break

    # pill image: a wide box with radius >= 100px / 999px holding an image
    for el in all_els:
        if li._tag(el) in ("a", "button") or not _has_img(el):
            continue
        radius = _radius_class(css.resolve(css.declared(el).get("border-radius") or "")) \
            if css.declared(el).get("border-radius") else None
        if radius not in ("pill", "round"):
            continue
        aspect = _aspect_of(css, el)
        if aspect and aspect >= 1.4:
            add("pill-image", el, f"an image box {round(aspect, 1)}:1 with fully rounded ends")
            break

    # caps headline / dark hero
    h1 = next((d for d in all_els if li._tag(d) == "h1"), None)
    if h1 is not None:
        c = css.computed(h1)
        upper = (c.get("text-transform") or "").strip() == "uppercase"
        size = _font_px(css, h1) or 0
        if upper and size >= 28:
            add("caps-headline", h1, f"h1 uppercase, {font_token(c.get('font-family')) or 'inherited'} face, up to "
                                     f"{int(size)}px")
    if hero_idx is not None:
        hero = sections[hero_idx]
        st = hero.get("style") or {}
        if st.get("bg") == "dark" or (st.get("image") == "overlay" and st.get("text") == "light"):
            add("dark-hero", nodes_[hero_idx], "hero on a dark ground" + (" over a photo with a scrim"
                                                                          if st.get("scrim") else ""))

    # dark page: the page ground or 70% of the content sections
    content = [s for s in sections if s["role"] not in ("nav", "footer") and not (s.get("style") or {}).get("pinned")]
    dark = [s for s in content if (s.get("style") or {}).get("bg") == "dark"]
    if li._is_dark(css.background(body)) or (content and len(dark) / len(content) >= 0.7):
        add("dark-page", body, f"{len(dark)} of {len(content)} sections dark")

    # big numerals: >= 3 short numerals at >= 28px (or a counter() at that size)
    numerals = [d for d in all_els if li._tag(d) not in ("li", "ol", "ul") and len(d) == 0
                and _NUMERAL_RE.match(li._text(d) or "") and (_font_px(css, d) or 0) >= 28]
    if len(numerals) >= 3:
        add("big-numerals", numerals[0], f"{len(numerals)} numerals up to {int(max(_font_px(css, n) for n in numerals))}px")
    else:
        for rule in css.rules:
            if rule.pseudo and "counter(" in (rule.decls.get("content") or "") and (_px(rule.decls.get(
                    "font-size") or "") or 0) >= 28:
                target = next((d for d in all_els if css._matches(d, rule.parts)), None)
                if target is not None:
                    add("big-numerals", target, "CSS counter numerals at 28px or more")
                    break

    # alternating rows: >= 3 same-class items with an image and text, and an
    # nth-child/of-type rule that reorders, or a flip/reverse class on some
    groups = Counter()
    for el in all_els:
        cls = (el.get("class") or "").split()
        if cls and _has_img(el) and li._words(li._text(el)) >= 4:
            groups[cls[0]] += 1
    reorder = [sel for sel, decls, _d in css.raw
               if re.search(r":nth-(?:child|of-type)\((?:even|odd|2n)", sel)
               and ("order" in decls or "row-reverse" in (decls.get("flex-direction") or "")
                    or "grid-column" in decls or "direction" in decls)]
    flips = [sel for sel, decls, _d in css.raw if re.search(r"\.(?:flip|reverse|alt|swap)\b", sel)
             and ("order" in decls or "row-reverse" in (decls.get("flex-direction") or "")
                  or any("order" in k for k in decls))]
    for cls, n in groups.most_common():
        if n < 3:
            break
        if any(f".{cls}" in sel for sel in reorder) or flips:
            target = next(d for d in all_els if cls in (d.get("class") or "").split())
            add("alternating-rows", target, f"{n} image/text rows that switch sides")
            break

    # trust bar: an early strip of 3-6 short items, no h1/h2
    for s, node in zip(sections, nodes_):
        if s["index"] > 2:
            break
        if s["role"] in ("nav", "hero", "footer") or any(li._tag(d) in ("h1", "h2") for d in node.iter()):
            continue
        items = [d for d in node.iter() if li._tag(d) == "li"]
        if not 3 <= len(items) <= 6 or s["words"] > 80:
            continue
        if all(li._words(li._text(i)) <= 14 for i in items):
            add("trust-bar", node, f"{len(items)} short items in section {s['index']}")
            break

    # hotspots: >= 2 absolutely placed labels (left/top in %) over an image
    spots = []
    for el in all_els:
        d = css.declared(el)
        if (d.get("position") or "").strip() == "absolute" and "%" in (d.get("left") or "") + (d.get("top") or "") \
                and 0 < li._words(li._text(el)) <= 8:
            parent = css.parent.get(el)
            if parent is not None and _has_img(parent):
                spots.append(el)
    if len(spots) >= 2:
        add("hotspots", spots[0], f"{len(spots)} labels placed over a product image")

    # sticky bottom bar
    for el in all_els:
        d = css.declared(el)
        if (d.get("position") or "").strip() in ("fixed", "sticky") and re.match(r"0(?:px)?$",
                                                                                  (d.get("bottom") or "").strip()):
            if any(li._cta_kind(a) for a in el.iter() if isinstance(a.tag, str)):
                add("sticky-bar", el, "a bar pinned to the bottom with a CTA")
                break

    # check rows / icon row / stat strip
    for el in all_els:
        tag = li._tag(el)
        kids = [k for k in el if isinstance(k.tag, str) and li._has_content(k)]
        if tag in ("ul", "ol") and len(kids) >= 3 and "check-rows" not in found:
            cls = (el.get("class") or "").lower()
            ticks = _CHECK_RE.search(cls) or all(any(li._tag(x) == "svg" for x in k.iter()) for k in kids)
            if not ticks:
                for rule in css.rules:
                    if rule.pseudo == "before" and any(ch in (rule.decls.get("content") or "") for ch in _CHECK_CHARS) \
                            and css._matches(kids[0], rule.parts):
                        ticks = True
                        break
            if ticks and all(li._words(li._text(k)) <= 40 for k in kids):
                add("check-rows", el, f"{len(kids)} rows with a check mark")
        if len(kids) >= 3 and "icon-row" not in found:
            icons = [k for k in kids if any(li._tag(x) == "svg" or (li._tag(x) == "img" and 0 < float(
                x.get("width") or 0) <= 64) for x in k.iter()) and 0 < li._words(li._text(k)) <= 12]
            if len(icons) >= 3 and len(icons) == len(kids) and not _CHECK_RE.search((el.get("class") or "").lower()):
                add("icon-row", el, f"{len(icons)} icons with short labels")
        if 3 <= len(kids) <= 8 and "stat-strip" not in found:
            big = 0
            for k in kids:
                vals = [x for x in k.iter() if li._tag(x) in ("b", "strong", "span", "p", "dt", "dd", "div")
                        and re.search(r"\d", li._text(x)) and len(li._text(x)) <= 16
                        and ((_font_px(css, x) or 0) >= 20
                             or font_token(css.computed(x).get("font-family")) in ("display-wide", "display-acid"))]
                if vals and li._words(li._text(k)) <= 20:
                    big += 1
            if big >= 3 and big >= len(kids) - 1:
                add("stat-strip", el, f"{big} big numbers with labels")

    # model picker
    radios = [d for d in all_els if li._tag(d) == "input" and (d.get("type") or "").lower() == "radio"]
    picker = next((d for d in all_els if re.search(r"picker|variant|swatch|model-select|option-select",
                                                    (d.get("class") or "").lower())), None)
    if len(radios) >= 2 or picker is not None:
        add("model-picker", radios[0] if radios else picker, f"{max(len(radios), 2)} options to choose between")

    # product grid: >= 4 same-class items in a grid, each with an image and a price
    for el in all_els:
        kids = [k for k in el if isinstance(k.tag, str)]
        if len(kids) < 4:
            continue
        cards = [k for k in kids if _has_img(k) and _PRICE_RE.search(li._text(k))]
        if len(cards) < 4:
            continue
        display, cols = _cols(css, el, True)
        if (display == "grid" and cols >= 2) or display == "flex":
            add("product-grid", el, f"{len(cards)} product cards in {cols} columns")
            break

    # FAQ accordion
    for s, node in zip(sections, nodes_):
        if s["details"] >= 3:
            add("faq-accordion", node, f"{s['details']} questions that open")
            break

    return [found[name] for name in PATTERN_NAMES if name in found]


# ---------------------------------------------------------------------------
# Design brief
# ---------------------------------------------------------------------------

def _type_phrase(t):
    if not t:
        return ""
    bits = [t.get("size_desktop") or t.get("size") or "", t.get("font") or "", t.get("transform") or ""]
    return " ".join(b for b in bits if b).strip()


def design_brief(observation):
    """One plain-English paragraph written from the observation (no model)."""
    sections = observation["sections"]
    names = {p["name"] for p in observation.get("patterns") or []}
    content = [s for s in sections if s["role"] not in ("nav", "footer") and not (s.get("style") or {}).get("pinned")]
    g = observation["global"]
    ground = "dark" if "dark-page" in names else "light"
    width = f", content up to {g['max_width_px']}px wide" if g.get("max_width_px") else ""
    out = [f"A {ground} page in {len(content)} content sections{width}."]
    hero = next((s for s in sections if s["role"] == "hero"), None)
    if hero:
        st = hero.get("style") or {}
        bits = []
        if "display-letters" in names and observation["patterns"][[p["name"] for p in observation["patterns"]].index(
                "display-letters")]["section"] == hero["index"]:
            bits.append("giant display letters flank" + (" a pill-shaped image window" if "pill-image" in names
                                                         else " the image"))
        elif st.get("image") == "overlay":
            bits.append("the headline sits over a full-bleed photo" + (" under a dark scrim" if st.get("scrim") else ""))
        elif st.get("layout", {}).get("cols_desktop", 1) > 1:
            bits.append(f"a {st['layout']['cols_desktop']}-column split of text and image")
        if st.get("bg") == "dark":
            bits.append("on a dark ground")
        head = _type_phrase(st.get("type"))
        if head:
            bits.append(f"headline {head}")
        pats = [p["name"] for p in observation.get("patterns") or [] if p["section"] == hero["index"]]
        out.append("First screen: " + (", ".join(bits) if bits else "a plain headline block")
                   + (" [" + ", ".join(pats) + "]" if pats else "") + ".")
    above, rest = [], []
    for s in content:
        if hero and s["index"] == hero["index"]:
            continue
        st = s.get("style") or {}
        lay = st.get("layout") or {}
        desc = s["role"]
        if lay.get("cols_desktop", 1) > 1:
            desc += f" ({lay['cols_desktop']} columns)"
        if st.get("bg") == "dark" and ground == "light":
            desc += " on dark"
        pats = [p["name"] for p in observation.get("patterns") or [] if p["section"] == s["index"]]
        if pats:
            desc += " [" + ", ".join(pats) + "]"
        (above if hero and s["index"] < hero["index"] else rest).append(desc)
    if above:
        out.insert(1, "Above the first screen: " + "; ".join(above) + ".")
    if rest:
        out.append("Then: " + "; ".join(rest) + ".")
    if names:
        out.append("Distinctive: " + "; ".join(PATTERNS[n] for n in PATTERN_NAMES if n in names) + ".")
    radii = Counter((s.get("style") or {}).get("radius") for s in content if (s.get("style") or {}).get("radius"))
    if radii:
        out.append(f"Images mostly {radii.most_common(1)[0][0]} corners.")
    return " ".join(out)


# ---------------------------------------------------------------------------
# Observation (the design part)
# ---------------------------------------------------------------------------

def annotate(observation, tree, body, nodes_, css_texts, tenant=None):
    """Adds the design level to a cycle 87 observation in place: per section
    `skeleton` and `style` (and `background` from the cascade), page
    `patterns`, `global.dark_page`, `brief`; then trims the skeletons until
    the JSON is at most MAX_OBSERVATION_BYTES. Returns the Css."""
    css = Css(css_texts, tenant_tokens(tenant)).bind(tree)
    for s, node in zip(observation["sections"], nodes_):
        s["style"] = section_style(css, node)
        s["background"] = s["style"]["bg"]
        s["skeleton"] = skeleton(node, SKELETON_NODES[0])
    observation["patterns"] = detect_patterns(css, body, nodes_, observation["sections"])
    for s in observation["sections"]:
        s["patterns"] = [p["name"] for p in observation["patterns"] if p["section"] == s["index"]]
    g = observation["global"]
    g["dark_sections"] = sum(1 for s in observation["sections"] if s["background"] == "dark")
    g["dark_page"] = any(p["name"] == "dark-page" for p in observation["patterns"])
    observation["version"] = 2
    observation["brief"] = design_brief(observation)
    for limit in SKELETON_NODES[1:]:
        if len(json.dumps(observation, separators=(",", ":"))) <= MAX_OBSERVATION_BYTES:
            break
        for s, node in zip(observation["sections"], nodes_):
            s["skeleton"] = skeleton(node, limit)
    return css


# ---------------------------------------------------------------------------
# The page's CSS for the prompt
# ---------------------------------------------------------------------------

def _colour_key(value):
    """((r, g, b), alpha text) of a hex or rgb()/rgba() literal, whatever
    its alpha; None for anything else."""
    value = value.strip().lower()
    m = re.fullmatch(r"#([0-9a-f]{3,8})", value)
    if m and len(m.group(1)) in (3, 4, 6, 8):
        h = m.group(1)
        if len(h) in (3, 4):
            h = "".join(c * 2 for c in h)
        a = "1" if len(h) == 6 else str(round(int(h[6:8], 16) / 255, 2))
        return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4)), a
    m = re.fullmatch(r"rgba?\(\s*(\d+)[\s,]+(\d+)[\s,]+(\d+)(?:[\s,/]+([\d.]+%?))?\s*\)", value)
    if m:
        return tuple(int(m.group(i)) for i in (1, 2, 3)), (m.group(4) or "1")
    return None


def sanitised_css(css_texts, tokens=None, limit=MAX_CSS_CHARS):
    """The page's own CSS without @font-face/@import/comments, without any
    declaration holding url(), every colour literal written as the tenant
    token it equals (var(--ps-...)) or a <light>/<dark>/<mid> placeholder;
    whitespace collapsed; cut at a rule boundary at `limit` characters."""
    tokens = tokens or {}
    by_colour = {}
    for name, value in tokens.items():
        key = _colour_key(value)
        if key and re.fullmatch(r"\s*(?:#[0-9a-fA-F]{3,8}|rgba?\([^)]*\))\s*", value):
            by_colour.setdefault(key, name)

    def colour(m):
        text = m.group(0)
        key = _colour_key(text)
        if key is None:
            return text
        if key in by_colour:
            return f"var({by_colour[key]})"
        rgb, a = key
        shade = "light" if _luma(rgb) >= 0.75 else ("dark" if _luma(rgb) < 0.25 else "mid")
        return f"<{shade}{'' if a == '1' else ' ' + a}>"

    text = "\n".join(css_texts)
    text = re.sub(r"/\*.*?\*/", "", text, flags=re.S)
    text = re.sub(r"@font-face\s*\{[^}]*\}", "", text, flags=re.I)
    text = re.sub(r"@import[^;]*;", "", text, flags=re.I)
    text = re.sub(r"[\w-]+\s*:[^;{}]*url\([^)]*\)[^;{}]*;?", "", text, flags=re.I)
    text = re.sub(r"#[0-9a-fA-F]{3,8}\b|rgba?\([^)]*\)|hsla?\([^)]*\)", colour, text)
    text = re.sub(r"(?<=[:\s,])(?:white|black)(?=[\s;}!,])", lambda m: "<light>" if m.group(0) == "white"
                  else "<dark>", text)
    text = re.sub(r"\s+", " ", text)
    text = re.sub(r"\s*([{};:,>])\s*", r"\1", text).strip()
    if len(text) <= limit:
        return text
    cut = text.rfind("}", 0, limit - 40)
    dropped = text[cut + 1:].count("}")
    return text[:cut + 1] + f"\n/* ... {dropped} more rules not shown */"


# ---------------------------------------------------------------------------
# Exemplars
# ---------------------------------------------------------------------------

# The folder of hand-made looks and the pages they were made from: tenant.yaml
# `look_import.pack_dir` (relative to the repo). Tests set PACK_DIR, which
# wins over the tenant setting.
PACK_DIR = None


def pack_dir(tenant):
    if PACK_DIR is not None:
        return Path(PACK_DIR)
    value = tenant.get("look_import.pack_dir") if tenant is not None else None
    return (li.REPO_ROOT / str(value)).resolve() if value else None


def _pack_observation(origin, tenant):
    root = pack_dir(tenant)
    if root is None:
        return None
    path = root / Path(str(origin or "")).name
    if not path.is_file() or path.suffix.lower() not in (".html", ".htm"):
        return None
    src = li.load_source({"kind": "upload", "data": path.read_bytes(), "filename": path.name})
    return li.observe(src, tenant=tenant)


def exemplars(tenant, cartridge):
    """Hand-made looks to show the model: [{slug, origin, observation,
    template}]. From the tenant's looks whose library.json says
    source.kind "pack" (observation.json beside it, else the pack page in
    the pack folder, pack_dir), then <pack folder>/looks/<cartridge>/<slug>/
    (template.html + library.json naming its page in the pack folder)."""
    out, seen = [], set()
    folders = []
    if tenant is not None:
        root = Path(tenant.looks_dir) / cartridge
        folders += [root / slug for slug in li.looks.imported_looks(cartridge, tenant)]
    root = pack_dir(tenant)
    pack_root = root / "looks" / cartridge if root is not None else None
    if pack_root is not None and pack_root.is_dir():
        folders += sorted(p for p in pack_root.iterdir() if p.is_dir())
    for folder in folders:
        slug = folder.name
        if slug in seen or not (folder / "template.html").is_file():
            continue
        try:
            lib = json.loads((folder / "library.json").read_text())
        except (OSError, ValueError):
            continue
        source = lib.get("source") if isinstance(lib.get("source"), dict) else {}
        if source.get("kind") != "pack":
            continue
        observation = None
        if (folder / "observation.json").is_file():
            try:
                observation = json.loads((folder / "observation.json").read_text())
            except ValueError:
                observation = None
        if not observation or observation.get("version", 1) < 2:
            try:
                observation = _pack_observation(source.get("origin"), tenant)
            except li.LookImportError:
                observation = None
        if not observation:
            continue
        seen.add(slug)
        out.append({"slug": slug, "origin": source.get("origin"), "observation": observation,
                    "template": (folder / "template.html").read_text()})
    return out


def _roles(observation):
    return [s["role"] for s in observation["sections"]
            if s["role"] not in ("nav", "footer") and not (s.get("style") or {}).get("pinned")]


def closeness(a, b):
    """0-1: how alike two observations are (shared patterns, role order)."""
    pa = {p["name"] for p in a.get("patterns") or []}
    pb = {p["name"] for p in b.get("patterns") or []}
    jaccard = len(pa & pb) / len(pa | pb) if pa | pb else 1.0
    order = difflib.SequenceMatcher(None, _roles(a), _roles(b)).ratio()
    return round(0.7 * jaccard + 0.3 * order, 4)


def choose_exemplar(observation, candidates, slug=None):
    """The closest candidate (or the one named `slug`), or None."""
    if slug:
        for c in candidates:
            if c["slug"] == slug:
                return c
        raise li.LookImportError(f"no exemplar {slug!r} for this cartridge; available: "
                                 f"{sorted(c['slug'] for c in candidates) or 'none'}")
    if not candidates:
        return None
    return max(candidates, key=lambda c: (closeness(observation, c["observation"]), c["slug"]))


# ---------------------------------------------------------------------------
# Fidelity
# ---------------------------------------------------------------------------

def _marker_ok(name, el, css, wrapper):
    if name in ("dark-page", "dark-hero"):
        return li._is_dark(css.background(el))
    if name == "caps-headline":
        heads = [el] if li._tag(el) == "h1" else [d for d in el.iter() if li._tag(d) == "h1"]
        return any((css.computed(h).get("text-transform") or "").strip() == "uppercase" for h in heads)
    return True


def fidelity(observation, review_html, tenant=None):
    """How faithfully a rendered candidate reproduces the observation:
    0.5 x the share of observed patterns realised (an element with
    data-look-pattern="<name>" in the rendered page; dark-page/dark-hero
    must sit on a dark ground, caps-headline must be an upper-case h1)
    + 0.2 x role-order similarity + 0.1 x section-count similarity
    + 0.2 x dark/light band similarity. Returns a dict with `score`."""
    tree = li.parse_html(review_html)
    css = Css(li._css_texts(tree), tenant_tokens(tenant)).bind(tree)
    wrapper = li._wrapper_element(review_html)
    expected = [p["name"] for p in observation.get("patterns") or []]
    if wrapper is None:
        return {"score": 0.0, "missing": expected, "present": [], "order": 0.0, "count": 0.0, "bands": 0.0}
    # the wrapper found by _wrapper_element belongs to another parse; find it in this tree
    wrapper = next(el for el in tree.iter() if isinstance(el.tag, str) and "pk-lp" in (el.get("class") or "").split())
    rendered = [el for el in li._children(wrapper) if li._has_content(el)
                and (css.declared(el).get("position") or "").strip() not in ("fixed", "sticky")]
    index = li._CssIndex(li._css_texts(tree))
    roles, bands = [], ""
    for i, el in enumerate(rendered):
        m, text = li._measure(el, index)
        roles.append(li._role(i + 1, len(rendered) + 2, el, m, text))
        bands += "d" if li._is_dark(css.background(el)) else "l"
    src = [s for s in observation["sections"]
           if s["role"] not in ("nav", "footer") and not (s.get("style") or {}).get("pinned")]
    src_roles = [s["role"] for s in src]
    src_bands = "".join("d" if s.get("background") == "dark" else "l" for s in src)
    order = difflib.SequenceMatcher(None, src_roles, roles).ratio() if src_roles or roles else 1.0
    count = 1 - abs(len(src) - len(rendered)) / max(len(src), len(rendered), 1)
    band_score = difflib.SequenceMatcher(None, src_bands, bands).ratio() if src_bands or bands else 1.0
    markers = {}
    for el in wrapper.iter():
        if isinstance(el.tag, str) and el.get("data-look-pattern"):
            for name in el.get("data-look-pattern").split():
                markers.setdefault(name, []).append(el)
    present = [n for n in expected if any(_marker_ok(n, el, css, wrapper) for el in markers.get(n, []))]
    share = len(present) / len(expected) if expected else 1.0
    score = round(0.5 * share + 0.2 * order + 0.1 * count + 0.2 * band_score, 2)
    return {"score": score, "present": present, "missing": [n for n in expected if n not in present],
            "order": round(order, 2), "count": round(count, 2), "bands": round(band_score, 2),
            "sections": {"source": src_roles, "render": roles}}
