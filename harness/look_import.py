"""Cycle 87: "Import a page" -- learn how any landing page is BUILT and turn
that structure into a new look for the landing (or listicle) cartridge, in
the tenant's own design language.

Three steps, one model call (plus at most two repair calls, cycle 90):

  1. Ingest + observe (deterministic, stdlib + html5lib, no model). The
     page comes from an uploaded .html file, pasted HTML or a URL. A URL is
     fetched with a 10 s timeout and a 2 MB cap, only from a public
     address, only when the answer is an HTML page; linked CSS is fetched
     only from the same origin, capped. The document is parsed, never run:
     <script>, <iframe>, <object> and their kind, event attributes and
     javascript: URLs are dropped. What comes out is a BUILD OBSERVATION:
     section order with a role guess, heading level, word/image/list/table
     counts, CTA presence and type, light/dark background, desktop column
     count where the CSS says it, and page-wide counts (fonts, max width,
     nav, sticky elements, images, estimated weight). It holds numbers and
     role names only -- never the source page's words, images, brand names
     or scripts, so none of them can reach the model or the stored look.
  2. Convert. One call to the tenant's `write` model: the observation, the
     look-authoring rules (harness/look_import_rules.md, with the exact
     render-context contract filled in) and the cartridge's default look as
     the reference. The model returns template.html only. The harness
     validates it (validate_template): Jinja parses; every variable is in
     the renderer's contract; tokens only (no colour literal); the boxless
     and mobile-first rules; no external URL or literal link; one CTA
     phrase plus the secondary text link; every renderer-owned block kept;
     then it renders the tenant's canned demo page (tenants/<t>/look-demo/
     <cartridge>/) through harness/render.py's render_page -- whose own
     backstops run -- and checks the storefront export (`harness
     shopify-body`) and the brand lint. On failure: one repair call with the
     errors, then a refusal listing them. Nothing is stored on a refusal.
  3. Store as a TENANT look: tenants/<t>/looks/<cartridge>/<slug>/
     {template.html, library.json, observation.json, review.html}.
     harness/looks.py resolves it after the cartridge's own looks; the
     A/B/C library accepts `<cartridge>:<slug>`.

Cycle 90 (import fidelity): the observation also holds the DESIGN level --
per section a DOM skeleton and a style summary from the page's own CSS, the
distinctive patterns found by rules, a plain-English design brief
(harness/look_design.py). The prompt adds the brief, the page's sanitised
CSS and the closest hand-made exemplar. After validation a fidelity score
compares the render with the observation; below 0.7 a repair round names
the missing patterns (three model calls at most). The byline's own links
are allowed; the look may have as many dark bands as the observed page.

Cycle 102 (import from a screenshot or URL, hand-conversion grade): the
source can also be one or more images (PNG/JPG/WebP/HEIC), and a URL or an
.html source is screenshotted at 390 and 1280 too (harness/look_vision.py);
a URL page over MAX_SOURCE_BYTES is imported from its screenshots alone.
One vision call on claude-opus-5-5 turns the screenshots into an
observation of the same shape; with HTML too, the two are merged (the
images win on order, rhythm and palette, the HTML on exact CSS values and
font names). The generation runs on claude-opus-5-5 with the images, the
section-by-section observation and the closest exemplar's full template --
hand-converted tenant looks count as exemplars now -- and gets ONE revise
round (MAX_CALLS = 2) when the validator refuses the draft or its fidelity
is under look_design.FIDELITY_THRESHOLD (0.75), with the findings and the
render at 390 beside the source.
"""
import copy
import datetime
import hashlib
import html as html_mod
import http.client
import ipaddress
import json
import os
import re
import secrets
import shutil
import socket
import tempfile
import urllib.error
import urllib.parse
import urllib.request
from collections import Counter
from pathlib import Path

import html5lib
import jinja2
from jinja2 import meta, nodes

from . import budget as budget_mod
from . import css_scope, heroes, looks, page_body, pricing
from . import tenant as tenant_mod
from .anthropic_client import make_client
from .errors import HarnessError

REPO_ROOT = Path(__file__).resolve().parent.parent
RULES_PATH = Path(__file__).resolve().parent / "look_import_rules.md"

CARTRIDGES = ("landing", "listicle")
SOURCE_KINDS = ("upload", "url", "paste", "images")
MAX_SOURCE_BYTES = 2 * 1024 * 1024
MAX_URL_CHARS = 2048
FETCH_TIMEOUT_S = 10
MAX_REDIRECTS = 3
MAX_CSS_FILES = 6
MAX_CSS_BYTES = 512 * 1024
MAX_CSS_TOTAL_BYTES = 1536 * 1024
MAX_SECTIONS = 40
MAX_TEMPLATE_BYTES = 80 * 1024
MAX_TITLE_CHARS = 60
# Cycle 102: the generation runs on Opus 5.5 with adaptive thinking; the
# template (8-10k tokens) and the thinking share this.
MAX_OUTPUT_TOKENS = 32000
MODEL_STAGE = "write"
MAX_ERRORS_SHOWN = 25
# Page-weight estimate for what the import does not download (stated in
# docs/LISTICLE-SITE.md): one image, one external script, one external
# stylesheet that was not fetched.
EST_IMAGE_KB = 80
EST_SCRIPT_KB = 40
EST_CSS_KB = 30
USER_AGENT = "Mozilla/5.0 (compatible; harness-look-import/1.0)"
HTML_TYPES = ("text/html", "application/xhtml+xml")

PROGRESS_STEPS = ("reading", "capturing", "observing", "drafting", "validating", "revising", "storing")
# Cycle 102: the revise round runs only while the import has spent less than
# this (the vision call plus the draft), so an import stays near $1.
REVISE_MAX_SPENT_USD = 0.70
# The daily-cap reservation of one import (docs/LISTICLE-SITE.md, the cost line).
EST_IMPORT_USD = 1.00


class LookImportError(HarnessError):
    """An import that was refused. `errors` lists every reason (a validation
    refusal has one line per failed rule)."""

    def __init__(self, message, errors=None):
        super().__init__(message)
        self.errors = list(errors or [])


def format_errors(exc):
    """One message for a job reason or the CLI: the headline, then each error."""
    lines = [str(exc)]
    shown = exc.errors[:MAX_ERRORS_SHOWN]
    lines += [f"- {e}" for e in shown]
    if len(exc.errors) > len(shown):
        lines.append(f"- ... and {len(exc.errors) - len(shown)} more")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Names
# ---------------------------------------------------------------------------

def slugify(text):
    slug = re.sub(r"[^a-z0-9]+", "-", str(text or "").lower()).strip("-")
    slug = re.sub(r"-{2,}", "-", slug)[:40].strip("-")
    if slug and not slug[0].isalpha():
        slug = f"look-{slug}"[:40].strip("-")
    return slug


def check_name(tenant, cartridge, name):
    """The slug for `name`, or LookImportError: an unknown cartridge, a name
    that makes no valid slug, or a COLLISION -- a built-in look of any
    cartridge (a retired listicle name included) or a look this tenant
    already imported for this cartridge."""
    if cartridge not in CARTRIDGES:
        raise LookImportError(f"unknown cartridge {cartridge!r}; choose one of {list(CARTRIDGES)}")
    slug = slugify(name)
    if not looks.LOOK_SLUG_RE.match(slug):
        raise LookImportError(
            f"name {name!r} does not make a look name: use 3-40 letters, digits and hyphens, starting with a letter"
        )
    if slug in looks.all_looks():
        raise LookImportError(f"name collision: {slug!r} is a built-in look; choose another name")
    if slug in looks.imported_looks(cartridge, tenant) or (Path(tenant.looks_dir) / cartridge / slug).exists():
        raise LookImportError(
            f"name collision: tenant {tenant.name} already has a {cartridge} look {slug!r}; choose another name"
        )
    return slug


# ---------------------------------------------------------------------------
# Fetch (URL sources)
# ---------------------------------------------------------------------------

class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """Redirects are followed by fetch_url itself, so every hop's host is
    checked before it is contacted."""

    def redirect_request(self, *args, **kwargs):
        return None


def urllib_transport(url, *, timeout, max_bytes):
    """One GET, no redirect followed: (status, {lower-case header: value},
    body). The body is read to at most max_bytes + 1 bytes, so an over-cap
    answer is detected without reading it all."""
    opener = urllib.request.build_opener(_NoRedirect)
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT,
                                               "Accept": "text/html,application/xhtml+xml,text/css;q=0.9"})
    try:
        resp = opener.open(req, timeout=timeout)
    except urllib.error.HTTPError as e:   # 3xx (not followed), 4xx, 5xx
        resp = e
    with resp:
        status = getattr(resp, "status", None) or resp.code
        headers = {k.lower(): v for k, v in resp.headers.items()}
        body = resp.read(max_bytes + 1) if status == 200 else b""
    return status, headers, body


def _default_resolver(host):
    return [info[4][0] for info in socket.getaddrinfo(host, None)]


def _check_public_host(host, resolver):
    """Refuses a host that resolves to anything but a public address
    (loopback, private, link-local -- the cloud metadata address --,
    reserved): the import must never become a way into the server's own
    network."""
    if not host:
        raise LookImportError("the URL has no host")
    try:
        addresses = resolver(host)
    except (OSError, UnicodeError) as e:
        raise LookImportError(f"cannot resolve {host}: {e}") from e
    if not addresses:
        raise LookImportError(f"cannot resolve {host}")
    for address in addresses:
        ip = ipaddress.ip_address(str(address).split("%", 1)[0])
        if not ip.is_global:
            raise LookImportError(f"refused: {host} resolves to a non-public address ({ip})")


def fetch_url(url, *, transport=None, resolver=None, accept=HTML_TYPES, max_bytes=MAX_SOURCE_BYTES):
    """(body bytes, headers, final url) for an http(s) URL on a public host
    whose answer is one of `accept` and at most `max_bytes`. Follows at most
    MAX_REDIRECTS redirects, checking each hop. Raises LookImportError."""
    transport = transport or urllib_transport
    resolver = resolver or _default_resolver
    current = str(url or "").strip()
    if len(current) > MAX_URL_CHARS:
        raise LookImportError(f"the URL is longer than {MAX_URL_CHARS} characters")
    for _hop in range(MAX_REDIRECTS + 1):
        parts = urllib.parse.urlsplit(current)
        if parts.scheme.lower() not in ("http", "https"):
            raise LookImportError(f"refused: only http(s) URLs can be imported, not {parts.scheme or 'no scheme'!r}")
        if parts.username or parts.password:
            raise LookImportError("refused: a URL with a user name or password in it")
        _check_public_host(parts.hostname, resolver)
        try:
            status, headers, body = transport(current, timeout=FETCH_TIMEOUT_S, max_bytes=max_bytes)
        except (OSError, ValueError, http.client.HTTPException) as e:
            raise LookImportError(f"could not fetch {current}: {e}") from e
        headers = {str(k).lower(): str(v) for k, v in (headers or {}).items()}
        if status in (301, 302, 303, 307, 308):
            location = headers.get("location")
            if not location:
                raise LookImportError(f"{current} redirected with no Location header")
            current = urllib.parse.urljoin(current, location)
            continue
        if status != 200:
            raise LookImportError(f"{current} answered HTTP {status}")
        ctype = headers.get("content-type", "").split(";", 1)[0].strip().lower()
        if ctype not in accept:
            raise LookImportError(f"refused: {current} is {ctype or 'a response with no content type'}, "
                                  f"not {' or '.join(accept)}")
        length = headers.get("content-length", "")
        if (length.isdigit() and int(length) > max_bytes) or len(body or b"") > max_bytes:
            raise LookImportError(f"refused: {current} is larger than {max_bytes // 1024} KB")
        return bytes(body or b""), headers, current
    raise LookImportError(f"refused: more than {MAX_REDIRECTS} redirects")


# ---------------------------------------------------------------------------
# Source
# ---------------------------------------------------------------------------

_HTML_HINT_RE = re.compile(r"<(?:!doctype\s+html|html|head|body|div|section|main|header|p|h[1-6])\b", re.I)


def decode_html(data, content_type=""):
    """Text of an HTML byte string (charset from the header, else a <meta>,
    else UTF-8), or LookImportError for binary data or text that is not HTML."""
    data = bytes(data or b"")
    if not data.strip():
        raise LookImportError("the page is empty")
    if b"\x00" in data[:8192]:
        raise LookImportError("refused: binary data, not an HTML page")
    encoding = "utf-8"
    m = re.search(r"charset=[\"']?([\w.:-]+)", content_type or "", re.I) or \
        re.search(rb"<meta[^>]+charset=[\"']?([\w.:-]+)", data[:4096], re.I)
    if m:
        name = m.group(1).decode("ascii", "ignore") if isinstance(m.group(1), bytes) else m.group(1)
        try:
            "".encode(name)
            encoding = name
        except LookupError:
            encoding = "utf-8"
    text = data.decode(encoding, errors="replace")
    if not _HTML_HINT_RE.search(text):
        raise LookImportError("refused: this is not an HTML page")
    return text


def load_source(source, *, transport=None, resolver=None):
    """`source` = {"kind": "upload", "data": bytes, "filename": ...} |
    {"kind": "paste", "html": str} | {"kind": "url", "url": str} |
    {"kind": "images", "paths": [...], "names": [...]} (cycle 102) ->
    {"kind", "origin", "html", "base_url", "bytes"} (+ "over_cap" for a URL,
    "paths" for images)."""
    kind = (source or {}).get("kind")
    if kind == "upload":
        filename = os.path.basename(str(source.get("filename") or ""))
        if not filename.lower().endswith((".html", ".htm")):
            raise LookImportError("refused: upload an .html or .htm file")
        data = source.get("data") or b""
        if len(data) > MAX_SOURCE_BYTES:
            raise LookImportError(f"refused: the file is larger than {MAX_SOURCE_BYTES // 1024} KB")
        return {"kind": kind, "origin": filename[:120], "html": decode_html(data), "base_url": None,
                "bytes": len(data)}
    if kind == "paste":
        text = str(source.get("html") or "")
        size = len(text.encode("utf-8"))
        if size > MAX_SOURCE_BYTES:
            raise LookImportError(f"refused: the pasted HTML is larger than {MAX_SOURCE_BYTES // 1024} KB")
        return {"kind": kind, "origin": "pasted HTML", "html": decode_html(text.encode("utf-8")), "base_url": None,
                "bytes": size}
    if kind == "url":
        # Cycle 102: a page over MAX_SOURCE_BYTES is no longer refused here: it
        # is screenshotted and imported from its images (over_cap), up to the
        # screenshot browser's own cap.
        from .look_vision import MAX_CAPTURE_BYTES

        body, headers, final_url = fetch_url(source.get("url"), transport=transport, resolver=resolver,
                                             max_bytes=MAX_CAPTURE_BYTES)
        return {"kind": kind, "origin": urllib.parse.urlsplit(final_url).hostname or "",
                "html": decode_html(body, headers.get("content-type", "")), "base_url": final_url,
                "bytes": len(body), "over_cap": len(body) > MAX_SOURCE_BYTES}
    if kind == "images":
        from . import look_vision

        try:
            paths = look_vision.check_image_paths(source.get("paths"))
        except look_vision.VisionError as e:
            raise LookImportError(f"refused: {e}") from e
        names = [str(n) for n in (source.get("names") or [p.name for p in paths])]
        origin = names[0] if len(names) == 1 else f"{len(names)} screenshots"
        return {"kind": kind, "origin": origin[:120], "html": None, "base_url": None, "paths": paths,
                "bytes": sum(p.stat().st_size for p in paths)}
    raise LookImportError(f"unknown source kind {kind!r}; one of {list(SOURCE_KINDS)}")


# ---------------------------------------------------------------------------
# Parse + sanitise (never executes anything)
# ---------------------------------------------------------------------------

DROP_TAGS = frozenset({"script", "iframe", "object", "embed", "applet", "frame", "frameset", "noscript",
                       "template", "base", "portal"})
_URL_ATTRS = ("href", "src", "action", "formaction", "data", "xlink:href", "poster")
_BAD_URL_RE = re.compile(r"^\s*(?:javascript|vbscript|data\s*:\s*text/html)", re.I)


def _tag(el):
    tag = el.tag if isinstance(el.tag, str) else ""
    return tag.rsplit("}", 1)[-1].lower()


def parse_html(text):
    return html5lib.parse(text, treebuilder="etree", namespaceHTMLElements=False)


def sanitise(tree):
    """Drops every element that can run or embed something (DROP_TAGS, a
    meta refresh), every on* event attribute, srcdoc, and every javascript:
    / vbscript: / data:text/html URL, in place. Returns (tree, counts,
    scripts) -- scripts = {"inline": n, "external": n, "inline_bytes": n},
    measured before they are dropped, for the weight estimate."""
    counts = Counter()
    scripts = {"inline": 0, "external": 0, "inline_bytes": 0}
    parents = {child: parent for parent in tree.iter() for child in parent}
    for el in list(tree.iter()):
        tag = _tag(el)
        if tag in DROP_TAGS or (tag == "meta" and (el.get("http-equiv") or "").lower() == "refresh"):
            if tag == "script":
                if el.get("src"):
                    scripts["external"] += 1
                else:
                    scripts["inline"] += 1
                    scripts["inline_bytes"] += len((el.text or "").encode("utf-8"))
            counts[tag] += 1
            parent = parents.get(el)
            if parent is not None and el in list(parent):
                tail = el.tail
                index = list(parent).index(el)
                parent.remove(el)
                if tail:   # keep the text that followed the dropped element
                    if index > 0:
                        prev = parent[index - 1]
                        prev.tail = (prev.tail or "") + tail
                    else:
                        parent.text = (parent.text or "") + tail
            continue
        for attr in list(el.attrib):
            name = str(attr).lower()
            if name.startswith("on"):
                del el.attrib[attr]
                counts["event_attributes"] += 1
            elif name in ("srcdoc", "formaction"):
                del el.attrib[attr]
                counts[name] += 1
            elif name in _URL_ATTRS and _BAD_URL_RE.match(str(el.attrib.get(attr) or "")):
                del el.attrib[attr]
                counts["javascript_urls"] += 1
    return tree, dict(counts), scripts


def sanitised_html(tree):
    import xml.etree.ElementTree as ET

    return ET.tostring(tree, encoding="unicode", method="html")


# ---------------------------------------------------------------------------
# CSS helpers (read, never applied)
# ---------------------------------------------------------------------------

_DECL_RE = re.compile(r"([-\w]+)\s*:\s*([^;]+)")


class _Decls(dict):
    """{prop: value} with `important`: the props declared !important
    (cycle 106: the fidelity cascade honours them)."""
    important = frozenset()


def _declarations(body):
    out, important = _Decls(), set()
    for m in _DECL_RE.finditer(body or ""):
        key = m.group(1).strip().lower()
        out[key] = m.group(2).replace("!important", "").strip()
        if "!important" in m.group(2):
            important.add(key)
    out.important = frozenset(important)
    return out


def _media_min_width(prelude):
    m = re.search(r"min-width\s*:\s*(\d+(?:\.\d+)?)(px|em|rem)", prelude, re.I)
    if not m:
        return None
    value = float(m.group(1))
    return value * 16 if m.group(2).lower() in ("em", "rem") else value


def css_rules(css, desktop=False, _depth=0):
    """[(selector, {prop: value}, desktop)] for every style rule in `css`;
    rules under @media (min-width >= 600px) are `desktop`; rules under a
    max-width-only query are skipped (they describe the phone layout)."""
    out = []
    if _depth > 4:
        return out
    for item in css_scope.parse_stylesheet(css or ""):
        if item[0] != "block":
            continue
        prelude, body = item[1].strip(), item[2]
        if prelude.startswith("@"):
            word = prelude.split(None, 1)[0].split("(")[0].lower()
            if word == "@media":
                minw = _media_min_width(prelude)
                if minw is None and re.search(r"max-width", prelude, re.I):
                    continue
                out += css_rules(body, desktop or (minw is not None and minw >= 600), _depth + 1)
            elif word in ("@supports", "@layer", "@container"):
                out += css_rules(body, desktop, _depth + 1)
            continue
        decls = _declarations(body)
        for sel in css_scope._split_top_level(prelude):
            out.append((sel.strip(), decls, desktop))
    return out


def _last_compound(selector):
    last = re.split(r"[\s>+~]+", selector.strip())[-1] if selector.strip() else ""
    return re.sub(r"::?[\w-]+(\([^)]*\))?", "", last)


class _CssIndex:
    """Rules keyed by the classes and ids of their selector's last compound,
    so an element finds the rules that may style it (an approximation: no
    cascade, no combinators)."""

    def __init__(self, css_texts):
        self.by_class, self.by_id = {}, {}
        self.rules = []
        for css in css_texts:
            for sel, decls, desktop in css_rules(css):
                last = _last_compound(sel)
                classes = frozenset(re.findall(r"\.([\w-]+)", last))
                ident = re.findall(r"#([\w-]+)", last)
                rule = (sel, classes, decls, desktop)
                self.rules.append(rule)
                for c in classes:
                    self.by_class.setdefault(c, []).append(rule)
                for i in ident:
                    self.by_id.setdefault(i, []).append(rule)

    def for_element(self, el):
        classes = set((el.get("class") or "").split())
        found = []
        for c in classes:
            for rule in self.by_class.get(c, ()):
                if rule[1] <= classes and rule not in found:
                    found.append(rule)
        for rule in self.by_id.get(el.get("id") or "", ()):
            if rule not in found:
                found.append(rule)
        return found


_NAMED = {"white": (255, 255, 255), "black": (0, 0, 0), "red": (255, 0, 0), "navy": (0, 0, 128),
          "gray": (128, 128, 128), "grey": (128, 128, 128), "silver": (192, 192, 192), "maroon": (128, 0, 0),
          "green": (0, 128, 0), "blue": (0, 0, 255), "purple": (128, 0, 128), "teal": (0, 128, 128)}


def _rgb(value):
    value = (value or "").strip().lower()
    m = re.search(r"#([0-9a-f]{3,8})\b", value)
    if m:
        h = m.group(1)
        if len(h) in (3, 4):
            return tuple(int(c * 2, 16) for c in h[:3])
        if len(h) in (6, 8):
            return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))
    m = re.search(r"rgba?\(\s*(\d+)[\s,]+(\d+)[\s,]+(\d+)(?:[\s,/]+([\d.]+%?))?", value)
    if m:
        alpha = m.group(4)
        if alpha is not None:
            a = float(alpha[:-1]) / 100 if alpha.endswith("%") else float(alpha)
            if a < 0.5:
                return None   # mostly transparent: the parent shows through
        return tuple(int(m.group(i)) for i in (1, 2, 3))
    for word, rgb in _NAMED.items():
        if re.search(rf"(?<![-\w]){word}(?![-\w])", value):
            return rgb
    return None


def _is_dark(rgb):
    r, g, b = (c / 255 for c in rgb)
    return 0.2126 * r + 0.7152 * g + 0.0722 * b < 0.4


_TW_DARK_RE = re.compile(r"(?:^|:)(?:bg-(?:black|(?:gray|slate|zinc|neutral|stone|neutral)-(?:7|8|9)\d0|"
                         r"(?:blue|indigo|red|green|emerald)-9\d0)|text-white|dark)$")
_TW_LIGHT_RE = re.compile(r"(?:^|:)bg-(?:white|(?:gray|slate|zinc|neutral|stone)-(?:50|100|200))$")


def _background(el, index):
    """("dark" | "light", "explicit" | "default") for a section: its own or
    its single wrapper's background, from an inline style, a matching CSS
    rule, or a utility class name."""
    candidates = [el]
    kids = [c for c in el if isinstance(c.tag, str)]
    if len(kids) == 1:
        candidates.append(kids[0])
    for node in candidates:
        style = _declarations(node.get("style") or "")
        values = [style.get("background-color"), style.get("background")]
        for _sel, _cls, decls, _desk in index.for_element(node):
            values += [decls.get("background-color"), decls.get("background")]
        for value in values:
            rgb = _rgb(value) if value else None
            if rgb is not None:
                return ("dark" if _is_dark(rgb) else "light"), "explicit"
        for cls in (node.get("class") or "").split():
            if _TW_DARK_RE.search(cls):
                return "dark", "explicit"
            if _TW_LIGHT_RE.search(cls):
                return "light", "explicit"
    return "light", "default"


def _track_count(value):
    value = re.sub(r"\[[^\]]*\]", " ", value or "")
    m = re.search(r"repeat\(\s*(\d+)\s*,", value)
    if m:
        return int(m.group(1))
    if "repeat(" in value:
        return None   # auto-fit / auto-fill: depends on the viewport
    tracks, depth, cur = [], 0, ""
    for ch in value:
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
        if ch.isspace() and depth == 0:
            if cur:
                tracks.append(cur)
            cur = ""
        else:
            cur += ch
    if cur:
        tracks.append(cur)
    return len(tracks) or None


_TW_COLS_RE = re.compile(r"^(?:(?:sm|md|lg|xl|2xl):)?grid-cols-(\d+)$")
_BS_COLS_RE = re.compile(r"^col-(?:md|lg|xl)-(\d+)$")


def _columns(el, index):
    """The most columns any grid in this section has at desktop width, from
    grid-template-columns / columns in the CSS, or grid-cols-N / col-md-N
    class names; None when nothing says."""
    best = None
    for node in el.iter():
        if not isinstance(node.tag, str):
            continue
        found = []
        for cls in (node.get("class") or "").split():
            m = _TW_COLS_RE.match(cls)
            if m:
                found.append(int(m.group(1)))
            m = _BS_COLS_RE.match(cls)
            if m and 0 < int(m.group(1)) <= 12:
                found.append(12 // int(m.group(1)))
        for _sel, _cls, decls, _desk in index.for_element(node):
            if decls.get("grid-template-columns"):
                n = _track_count(decls["grid-template-columns"])
                if n:
                    found.append(n)
            for prop in ("columns", "column-count"):
                m = re.match(r"\s*(\d+)\b", decls.get(prop) or "")
                if m:
                    found.append(int(m.group(1)))
        style = _declarations(node.get("style") or "")
        if style.get("grid-template-columns"):
            n = _track_count(style["grid-template-columns"])
            if n:
                found.append(n)
        for n in found:
            if 1 <= n <= 6:
                best = max(best or 0, n)
    return best


# ---------------------------------------------------------------------------
# Observe
# ---------------------------------------------------------------------------

SKIP_TEXT_TAGS = frozenset({"style", "script", "head", "title", "svg", "template", "noscript"})
NON_SECTION_TAGS = frozenset({"style", "link", "meta", "br", "hr", "input", "title", "head"})
WRAPPER_TAGS = frozenset({"div", "main", "article", "span", "section", "form"})
_CTA_CLASS_RE = re.compile(r"btn|button|cta|shop|buy|order|checkout|add-to-cart", re.I)
_FAQ_RE = re.compile(r"\bfaq\b|frequently asked|questions", re.I)
_PROOF_RE = re.compile(r"review|testimonial|customers? (?:say|love)|rated|stars?\b|★", re.I)
_COMPARE_RE = re.compile(r"\bvs\.?\b|\bversus\b|compar", re.I)
_GENERIC_FONTS = {"serif", "sans-serif", "monospace", "cursive", "fantasy", "system-ui", "ui-sans-serif",
                  "ui-serif", "ui-monospace", "inherit", "initial", "unset", "-apple-system",
                  "blinkmacsystemfont", "emoji", "math", "ui-rounded"}
_TW_MAXW = {"max-w-7xl": 1280, "max-w-6xl": 1152, "max-w-5xl": 1024, "max-w-4xl": 896, "max-w-screen-xl": 1280,
            "max-w-screen-lg": 1024, "max-w-screen-2xl": 1536}


def _text(el):
    """The element's visible text (style/script/svg text excluded)."""
    parts = []

    def walk(node):
        if not isinstance(node.tag, str):
            if node.tail:
                parts.append(node.tail)
            return
        if _tag(node) in SKIP_TEXT_TAGS:
            if node.tail:
                parts.append(node.tail)
            return
        if node.text:
            parts.append(node.text)
        for child in node:
            walk(child)
        if node is not el and node.tail:
            parts.append(node.tail)

    walk(el)
    return " ".join(" ".join(parts).split())


def _words(text):
    return len(re.findall(r"\w[\w'’-]*", text or ""))


def _hidden(el):
    style = (el.get("style") or "").replace(" ", "").lower()
    return el.get("hidden") is not None or el.get("aria-hidden") == "true" or "display:none" in style


def _children(el):
    return [c for c in el if isinstance(c.tag, str) and _tag(c) not in NON_SECTION_TAGS and not _hidden(c)]


def _unwrap(el):
    seen = 0
    while seen < 30:
        kids = _children(el)
        if len(kids) == 1 and _tag(kids[0]) in WRAPPER_TAGS:
            el = kids[0]
            seen += 1
            continue
        return el
    return el


def _heading_count(el):
    return sum(1 for d in el.iter() if _tag(d) in ("h1", "h2", "h3"))


def _is_container(el):
    """A wrapper that holds several sections of its own: three or more
    children each carrying a heading or being a <section>."""
    kids = _children(el)
    return _tag(el) in WRAPPER_TAGS | {"main"} and len(
        [k for k in kids if _tag(k) == "section" or _heading_count(k)]) >= 3


def _has_content(el):
    if _hidden(el):
        return False
    if _words(_text(el)):
        return True
    return any(_tag(d) in ("img", "picture", "video", "svg", "a", "button") for d in el.iter())


def section_nodes(body):
    root = _unwrap(body)
    out = []
    for child in _children(root):
        if _tag(child) == "main" or child.get("role") == "main" or _is_container(child):
            out.extend(_children(_unwrap(child)))
        else:
            out.append(child)
    for _ in range(3):   # a page with one big wrapper section: look one level deeper
        containers = [n for n in out if _is_container(n)]
        if len(out) > 2 or not containers:
            break
        expanded = []
        for n in out:
            expanded.extend(_children(_unwrap(n)) if n in containers else [n])
        out = expanded
    return [n for n in out if _has_content(n)][:MAX_SECTIONS]


def _aspect(img):
    try:
        w, h = float(img.get("width") or 0), float(img.get("height") or 0)
    except ValueError:
        return None
    return round(w / h, 2) if w > 0 and h > 0 else None


def _cta_kind(a):
    if _tag(a) == "button" or (_tag(a) == "input" and (a.get("type") or "").lower() in ("submit", "button")):
        return "button"
    if _tag(a) != "a":
        return None
    href = (a.get("href") or "").strip()
    if not href or href.startswith("#"):
        return None
    if a.get("role") == "button" or _CTA_CLASS_RE.search(a.get("class") or ""):
        return "button"
    return "link"


def _repeated_items(el):
    """The largest group of sibling elements sharing one tag + class (at
    least 3, each with content): a card grid, a list of rows."""
    best = 0
    for node in el.iter():
        if not isinstance(node.tag, str):
            continue
        groups = Counter((_tag(k), k.get("class") or "") for k in _children(node) if _has_content(k))
        for (tag, _cls), n in groups.items():
            if tag not in ("li", "tr", "p", "span", "a") and n >= 3:
                best = max(best, n)
    return best


def _role(i, n, el, m, text):
    tag = _tag(el)
    if i == 0 and (tag in ("header", "nav") or el.find(".//nav") is not None) and not m["heading_level"] == 1 \
            and m["words"] < 60:
        return "nav"
    if tag == "footer" or (i == n - 1 and m["links"] >= 5 and (m["heading_level"] or 9) > 2):
        return "footer"
    if m["heading_level"] == 1:
        return "hero"
    if m["details"] >= 2 or (_FAQ_RE.search(m["_heading_text"]) or m["question_headings"] >= 3):
        return "faq"
    if m["table"] or _COMPARE_RE.search(m["_heading_text"]):
        return "comparison"
    if m["blockquotes"] or _PROOF_RE.search(m["_heading_text"]) or "★" in text:
        return "proof"
    if m["images"] >= 3 and m["words"] < m["images"] * 15:
        return "gallery"
    if m["repeated_items"] >= 3 or m["list_items"] >= 3:
        return "benefits"
    if m["cta"]["count"] and m["words"] < 60:
        return "cta"
    return "content"


def _measure(el, index):
    tags = Counter(_tag(d) for d in el.iter() if isinstance(d.tag, str))
    headings = [d for d in el.iter() if re.fullmatch(r"h[1-6]", _tag(d))]
    heading_text = " ".join(_text(h) for h in headings)
    images = [d for d in el.iter() if _tag(d) == "img"]
    bg_images = sum(1 for d in el.iter() if isinstance(d.tag, str) and "url(" in (d.get("style") or ""))
    kinds = [k for k in (_cta_kind(d) for d in el.iter() if isinstance(d.tag, str)) if k]
    buttons = kinds.count("button")
    links = kinds.count("link")
    background, background_source = _background(el, index)
    aspects = sorted({a for a in (_aspect(i) for i in images) if a})[:6]
    text = _text(el)
    m = {
        "tag": _tag(el),
        "heading_level": min((int(_tag(h)[1]) for h in headings), default=None),
        "headings": len(headings),
        "heading_words": _words(heading_text),
        "words": _words(text),
        "images": len(images) + bg_images + tags["video"],
        "image_aspects": aspects,
        "videos": tags["video"],
        "lists": tags["ul"] + tags["ol"],
        "list_items": tags["li"],
        "table": tags["table"] > 0,
        "blockquotes": tags["blockquote"],
        "details": tags["details"],
        "question_headings": sum(1 for h in headings if _text(h).rstrip().endswith("?")),
        "form": tags["form"] > 0,
        "cta": {"count": buttons, "links": links,
                "type": "button" if buttons else ("form" if tags["form"] else ("link" if links else None))},
        "links": links + buttons,
        "background": background,
        "background_source": background_source,
        "columns_desktop": _columns(el, index),
        "repeated_items": _repeated_items(el),
        "_heading_text": heading_text,
    }
    return m, text


def _css_texts(tree):
    out = []
    for el in tree.iter():
        if _tag(el) == "style" and el.text:
            out.append(el.text)
    return out


def _stylesheet_hrefs(tree):
    out = []
    for el in tree.iter():
        if _tag(el) == "link" and "stylesheet" in (el.get("rel") or "").lower() and el.get("href"):
            out.append(el.get("href"))
    return out


def fetch_same_origin_css(hrefs, base_url, *, transport=None, resolver=None):
    """CSS texts of the same-origin stylesheets among `hrefs` (at most
    MAX_CSS_FILES, each at most MAX_CSS_BYTES, MAX_CSS_TOTAL_BYTES in all).
    Returns (texts, notes, fetched_bytes, not_fetched)."""
    texts, notes, total, not_fetched = [], [], 0, 0
    if not base_url:
        return texts, notes, 0, len(hrefs)
    origin = urllib.parse.urlsplit(base_url)
    for href in hrefs:
        url = urllib.parse.urljoin(base_url, href)
        parts = urllib.parse.urlsplit(url)
        if (parts.scheme, parts.hostname, parts.port) != (origin.scheme, origin.hostname, origin.port):
            not_fetched += 1
            continue
        if len(texts) >= MAX_CSS_FILES or total >= MAX_CSS_TOTAL_BYTES:
            notes.append("more same-origin stylesheets than the cap; the rest were not read")
            not_fetched += 1
            continue
        try:
            body, headers, _final = fetch_url(url, transport=transport, resolver=resolver, accept=("text/css",),
                                              max_bytes=min(MAX_CSS_BYTES, MAX_CSS_TOTAL_BYTES - total))
        except LookImportError as e:
            notes.append(f"a stylesheet was not read: {e}")
            not_fetched += 1
            continue
        total += len(body)
        texts.append(body.decode("utf-8", errors="replace"))
    if not_fetched:
        notes.append(f"{not_fetched} stylesheet(s) not read (other origin or over the cap); "
                     "column counts and backgrounds from them are unknown")
    return texts, notes, total, not_fetched


def _fonts(css_texts, tree, hrefs):
    families = set()
    for css in css_texts:
        for m in re.finditer(r"font-family\s*:\s*([^;}]+)", css, re.I):
            first = re.sub(r"var\([^)]*\)", "", m.group(1)).split(",")[0].strip().strip("\"'").lower()
            if first and first not in _GENERIC_FONTS and not first.startswith("--"):
                families.add(first)
    for href in hrefs:
        for fam in urllib.parse.parse_qs(urllib.parse.urlsplit(href).query).get("family", []):
            families.add(fam.split(":", 1)[0].replace("+", " ").lower())
    for el in tree.iter():
        m = re.search(r"font-family\s*:\s*([^;]+)", el.get("style") or "", re.I) if isinstance(el.tag, str) else None
        if m:
            first = m.group(1).split(",")[0].strip().strip("\"'").lower()
            if first and first not in _GENERIC_FONTS:
                families.add(first)
    return len(families)


def _max_width(index, tree):
    widths = Counter()
    for sel, _cls, decls, _desk in index.rules:
        m = re.match(r"\s*(\d+(?:\.\d+)?)px\s*$", decls.get("max-width") or "")
        if m and 640 <= float(m.group(1)) <= 1920:
            widths[int(float(m.group(1)))] += 1
    if widths:
        return widths.most_common(1)[0][0]
    for el in tree.iter():
        if isinstance(el.tag, str):
            for cls in (el.get("class") or "").split():
                if cls in _TW_MAXW:
                    widths[_TW_MAXW[cls]] += 1
    return widths.most_common(1)[0][0] if widths else None


def _sticky(tree, index):
    """Where the page pins something: "top" (a header/nav), "bottom" (a bar)
    or "other", once each."""
    found = set()
    for el in tree.iter():
        if not isinstance(el.tag, str):
            continue
        decl_sets = [_declarations(el.get("style") or "")] + [r[2] for r in index.for_element(el)]
        classes = set((el.get("class") or "").split())
        pinned = any(d.get("position") in ("sticky", "fixed") for d in decl_sets) or classes & {"sticky", "fixed"}
        if not pinned:
            continue
        bottom = any(re.match(r"0(?:px)?$", d.get("bottom") or "") for d in decl_sets) or "bottom-0" in classes
        top = _tag(el) in ("header", "nav") or any(re.match(r"0(?:px)?$", d.get("top") or "") for d in decl_sets) \
            or "top-0" in classes
        found.add("bottom" if bottom else ("top" if top else "other"))
    return sorted(found)


def observe(source, *, transport=None, resolver=None, tenant=None):
    """The build observation (a JSON-ready dict) of a loaded source. Holds
    counts, role names, class names and style values only, never the page's
    words (cycle 90: plus the design level, harness/look_design.py)."""
    return observe_with_css(source, transport=transport, resolver=resolver, tenant=tenant)[0]


def observe_with_css(source, *, transport=None, resolver=None, tenant=None):
    """(observation, the page's sanitised CSS for the prompt)."""
    from . import look_design

    tree = parse_html(source["html"])
    hrefs = _stylesheet_hrefs(tree)
    external_css, css_notes, css_bytes, not_fetched = fetch_same_origin_css(
        hrefs, source.get("base_url"), transport=transport, resolver=resolver)
    tree, dropped, scripts = sanitise(tree)
    css_texts = _css_texts(tree) + external_css
    index = _CssIndex(css_texts)
    body = tree.find(".//body")
    if body is None:
        raise LookImportError("refused: the page has no <body>")
    nodes_ = section_nodes(body)
    if not nodes_:
        raise LookImportError("refused: no visible sections found on the page")
    sections = []
    for i, el in enumerate(nodes_):
        m, text = _measure(el, index)
        m["role"] = _role(i, len(nodes_), el, m, text)
        m.pop("_heading_text")
        sections.append({"index": i, "role": m.pop("role"), **m})
    total_images = sum(s["images"] for s in sections)
    words = sum(s["words"] for s in sections)
    inline_css_bytes = sum(len(t.encode("utf-8")) for t in _css_texts(tree))
    cta_texts = set()
    for el in body.iter():
        if isinstance(el.tag, str) and _cta_kind(el) == "button":
            cta_texts.add(" ".join(_text(el).lower().split()))
    weight = (source.get("bytes") or 0) + css_bytes + not_fetched * EST_CSS_KB * 1024 \
        + total_images * EST_IMAGE_KB * 1024 + scripts["external"] * EST_SCRIPT_KB * 1024
    content = [s for s in sections if s["role"] not in ("nav", "footer")]
    headings = Counter()
    for el in body.iter():
        if re.fullmatch(r"h[1-6]", _tag(el)):
            headings[_tag(el)] += 1
    obs = {
        "version": 1,
        "source": {"kind": source["kind"], "origin": source["origin"], "bytes": source.get("bytes") or 0},
        "sections": sections,
        "global": {
            "sections": len(sections),
            "content_sections": len(content),
            "words": words,
            "images": total_images,
            "headings": dict(sorted(headings.items())),
            "fonts": _fonts(css_texts, tree, hrefs),
            "max_width_px": _max_width(index, tree),
            "nav": any(_tag(e) == "nav" for e in body.iter()) or bool(sections and sections[0]["role"] == "nav"),
            "sticky": _sticky(tree, index),
            "cta_buttons": sum(s["cta"]["count"] for s in sections),
            "cta_phrases": len(cta_texts),
            "dark_sections": sum(1 for s in sections if s["background"] == "dark"),
            "words_per_section": round(words / max(1, len(content))),
            "html_bytes": source.get("bytes") or 0,
            "css_bytes": css_bytes + inline_css_bytes,
            "stylesheets_not_read": not_fetched,
            "scripts": scripts,
            "estimated_weight_kb": round(weight / 1024),
        },
        "dropped": dropped,
        "notes": css_notes,
    }
    look_design.annotate(obs, tree, body, nodes_, css_texts, tenant=tenant)
    return obs, look_design.sanitised_css(css_texts, look_design.tenant_tokens(tenant))


def observation_sha(observation):
    return hashlib.sha256(json.dumps(observation, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def summarise(observation):
    """The two sentences of library.json `summary`: what was learned."""
    sections = observation["sections"]
    g = observation["global"]
    roles = [s["role"] for s in sections if s["role"] not in ("nav", "footer")]
    order = " > ".join(roles) or "one block"
    first = (f"Learned a {len(roles)}-section layout from {observation['source']['origin'] or 'the page'}: "
             f"{order}.")
    grids = sorted({s["columns_desktop"] for s in sections if s.get("columns_desktop") and s["columns_desktop"] > 1})
    bits = [f"{'/'.join(map(str, grids))}-column grids at desktop" if grids else "single-column sections",
            f"{g['dark_sections']} dark band(s)",
            f"a CTA in {sum(1 for s in sections if s['cta']['count'])} section(s)"]
    if g.get("sticky"):
        bits.append("a sticky " + " and ".join(g["sticky"]) + " element")
    if g.get("max_width_px"):
        bits.append(f"a {g['max_width_px']}px content width")
    second = "It uses " + ", ".join(bits[:-1]) + (" and " if len(bits) > 1 else "") + bits[-1] + "."
    return f"{first} {second}"


# ---------------------------------------------------------------------------
# The look contract (what a look template may read)
# ---------------------------------------------------------------------------

# Top-level names a look template may use, per cartridge -- a subset of what
# harness/render.py's render_page passes to the template (tests check that
# every one of them really is passed). facts_pack, ad_brief and tenant are
# left out on purpose: a look prints only renderer-checked values.
CONTRACT_NAMES = {
    "landing": ("page", "product", "assets", "cartridge_data", "disclosure_label", "byline_html",
                "render_image_slot", "image_fit", "look", "tenant_name", "blocks"),
    "listicle": ("page", "product", "assets", "cartridge_data", "first_screen", "disclosure_label", "byline_html",
                 "about_author_html", "render_image_slot", "image_fit", "look", "tenant_name", "micro_cta_after",
                 "blocks"),
}
# Cycle 91: a look may include a catalog block (harness/blocks/catalog/) as
# {% include "blocks/<id>.html" %}; its rendered context is `blocks.<id>`.
_BLOCK_INCLUDE_RE = re.compile(r"^blocks/([a-z][a-z0-9_]*)\.html$")


def included_blocks(text):
    """The catalog block ids a template includes, in order (no repeats);
    [] when it does not parse."""
    try:
        ast = _jinja_env().parse(text)
    except jinja2.TemplateSyntaxError:
        return []
    out = []
    for node in ast.find_all(nodes.Include):
        m = _BLOCK_INCLUDE_RE.match(node.template.value) if isinstance(node.template, nodes.Const) and \
            isinstance(node.template.value, str) else None
        if m and m.group(1) not in out:
            out.append(m.group(1))
    return out


def look_spec_for(text):
    """The look.json an imported look gets: the catalog blocks it includes
    (None when it includes none)."""
    ids = included_blocks(text)
    return {"blocks": ids} if ids else None
JINJA_NAMES = frozenset({"loop", "range", "namespace", "super", "caller", "varargs", "kwargs"})
# Keys of the renderer's own context that are bookkeeping, not page content.
HIDDEN_DATA_KEYS = frozenset({"accent_note", "claim_ids", "pronoun"})

CARTRIDGE_RULES = {
    "landing": {
        "wrapper": ("pk-lp", "adv-landing"),
        "root_class": "adv-landing",
        "cta_class": "ld-btn",
        "secondary_class": "ld-text",
        "data_alias": "ld",
        # (root, first attribute) pairs and names every landing look must use:
        # the renderer-owned blocks and the writer's sections.
        "required": {
            # Cycle 82e: the review quote is the renderer-checked
            # cartridge_data.review_quote, not page.proof.quote.
            "cartridge_data": ("eyebrow", "headline_html", "price", "financing", "secondary", "trust", "specs",
                               "rating", "warranty", "review_quote"),
            "page": ("hero", "checked_claims", "faq", "closing", "cta_url", "cta_text", "room_photo"),
            "names": ("disclosure_label", "render_image_slot", "byline_html", "product"),
        },
    },
    "listicle": {
        "wrapper": ("pk-lp", "adv-listicle"),
        "root_class": "adv-listicle",
        "cta_class": "op-btn",
        "secondary_class": "op-link",
        "data_alias": "fs",
        "required": {
            "cartridge_data": ("model_options",),
            "first_screen": ("headline_html", "byline", "stats", "value_stack", "eyebrow", "hero_style"),
            "page": ("reasons", "faq", "closing", "cta_url", "cta_text", "audience_fit", "hero"),
            "names": ("disclosure_label", "render_image_slot", "about_author_html"),
        },
    },
}
ALLOWED_BLOCKS = frozenset({"title", "byline", "content"})
ALLOWED_CALL_NAMES = frozenset({"render_image_slot", "image_fit", "range", "namespace"})
ALLOWED_METHODS = frozenset({"get", "items", "keys", "values", "cycle"})
ALLOWED_FILTERS = frozenset({"safe", "format", "length", "count", "default", "d", "join", "lower", "upper", "round",
                             "int", "e", "escape", "first", "last", "trim", "truncate", "replace", "list", "batch",
                             "slice", "abs", "string", "title", "capitalize", "wordcount", "min", "max"})
SAFE_NAMES = frozenset({"byline_html", "about_author_html"})


def demo_dir(tenant, cartridge):
    return Path(tenant.root) / "look-demo" / cartridge


def load_demo(tenant, cartridge):
    """The tenant's canned demo page for this cartridge: {page, facts_pack,
    ad_brief} from tenants/<t>/look-demo/<cartridge>/ (made once from an
    offline `python -m evals.fake_run ... --cartridges <cartridge>` run)."""
    folder = demo_dir(tenant, cartridge)
    try:
        return {name: json.loads((folder / f"{name}.json").read_text())
                for name in ("page", "facts_pack", "ad_brief")}
    except (OSError, ValueError) as e:
        raise LookImportError(
            f"no demo page for {cartridge} at {folder} ({e}); make one with `python -m evals.fake_run "
            f"<fixture> --tenant {tenant.name} --cartridges {cartridge}` and copy its page.json, "
            "facts_pack.json and ad_brief.json there") from e


def demo_context(cartridge, tenant, demo):
    """The renderer's cartridge_data for the demo page (cycle 106: which
    patterns the demo can show at all)."""
    from . import landing as landing_mod
    from . import listicle as listicle_mod

    if cartridge == "landing":
        return landing_mod.render_context(demo["page"], demo["facts_pack"], ad_brief=demo["ad_brief"], tenant=tenant)
    return dict(listicle_mod.render_context(demo["facts_pack"], tenant=tenant))


def contract(cartridge, tenant, demo):
    """{"names": (...), "data": {root: sorted keys}, "product": sorted keys}
    -- the exact context a look of this cartridge may read, computed from
    the renderer's own functions on the demo page."""
    from . import first_screen as first_screen_mod
    from . import landing as landing_mod
    from . import listicle as listicle_mod

    page, fp, brief = demo["page"], demo["facts_pack"], demo["ad_brief"]
    data = {}
    if cartridge == "landing":
        ctx = landing_mod.render_context(page, fp, ad_brief=brief, tenant=tenant)
        data["cartridge_data"] = sorted((set(ctx) | {"frame", "made_for"}) - HIDDEN_DATA_KEYS)
    else:
        data["cartridge_data"] = sorted(set(listicle_mod.render_context(fp, tenant=tenant)) - HIDDEN_DATA_KEYS)
        fs = first_screen_mod.render_context(copy.deepcopy(page), ad_brief=brief, facts_pack=fp, tenant=tenant,
                                             hero_style="display", frame=None, updated="2026-01-01")
        data["first_screen"] = sorted(set(fs) - HIDDEN_DATA_KEYS)
    from .blocks import catalog as block_catalog

    data["blocks"] = sorted(block_catalog.ids())
    product = sorted(set((fp.get("product") or {})) | {"full_name", "descriptor"})
    return {"names": CONTRACT_NAMES[cartridge], "data": data, "product": product}


def load_schema(cartridge, tenant):
    return json.loads(tenant.render((REPO_ROOT / "cartridges" / cartridge / "schema.json").read_text()))


# ---------------------------------------------------------------------------
# Template validation -- static
# ---------------------------------------------------------------------------

_COLOUR_FN_RE = re.compile(r"\b(?:rgba?|hsla?|hwb|lab|lch|oklab|oklch|color)\s*\(", re.I)
_HEX_RE = re.compile(r"#[0-9a-fA-F]{3,8}\b")
NAMED_COLOURS = frozenset("""aliceblue antiquewhite aqua aquamarine azure beige bisque black blanchedalmond blue
blueviolet brown burlywood cadetblue chartreuse chocolate coral cornflowerblue cornsilk crimson cyan darkblue
darkcyan darkgoldenrod darkgray darkgreen darkgrey darkkhaki darkmagenta darkolivegreen darkorange darkorchid
darkred darksalmon darkseagreen darkslateblue darkslategray darkturquoise darkviolet deeppink deepskyblue dimgray
dodgerblue firebrick floralwhite forestgreen fuchsia gainsboro ghostwhite gold goldenrod gray green greenyellow grey
honeydew hotpink indianred indigo ivory khaki lavender lawngreen lemonchiffon lightblue lightcoral lightcyan
lightgray lightgreen lightgrey lightpink lightsalmon lightseagreen lightskyblue lightslategray lightsteelblue
lightyellow lime limegreen linen magenta maroon mediumblue mediumorchid mediumpurple mediumseagreen navy oldlace
olive olivedrab orange orangered orchid palegreen paleturquoise palevioletred peachpuff peru pink plum powderblue
purple rebeccapurple red rosybrown royalblue saddlebrown salmon sandybrown seagreen seashell sienna silver skyblue
slateblue slategray snow springgreen steelblue tan teal thistle tomato turquoise violet wheat white whitesmoke
yellow yellowgreen""".split())
COLOUR_PROPS_RE = re.compile(r"^(?:color|background(?:-color)?|border(?:-[a-z]+)*|outline(?:-color)?|fill|stroke|"
                             r"text-decoration(?:-color)?|caret-color|column-rule(?:-color)?|accent-color|"
                             r"box-shadow|text-shadow|text-emphasis-color)$")
# <img> is allowed only with a Jinja src (the listicle byline's avatar); the
# literal-src rule below refuses any other.
_FORBIDDEN_TAG_RE = re.compile(r"<\s*(link|iframe|object|embed|form|input|button|textarea|select|base|meta|frame|"
                               r"frameset|applet|picture|source|video|audio|image|use|foreignobject)\b", re.I)
_SCRIPT_BAD_RE = re.compile(r"\b(?:fetch|XMLHttpRequest|eval|Function|import|cookie|localStorage|sessionStorage|"
                            r"innerHTML|outerHTML|insertAdjacentHTML|write|location|open|postMessage|"
                            r"setTimeout|setInterval|WebSocket|sendBeacon|src|href)\b|https?:|//")
MAX_SCRIPT_CHARS = 800
MAX_LITERAL_WORDS = 80
MAX_LITERAL_RUN_WORDS = 10
MOBILE_WIDTH = 390
DARK_TOKENS = ("--pk-dark", "--pk-ink", "--ps-ink-dark", "--ps-text-strong")


def _jinja_env():
    return jinja2.Environment(autoescape=jinja2.select_autoescape(["html"]))


def _chain(node):
    """(root name, [attr, ...]) for a Getattr/Getitem chain down to a Name,
    else None."""
    attrs = []
    while isinstance(node, (nodes.Getattr, nodes.Getitem)):
        if isinstance(node, nodes.Getattr):
            attrs.append(node.attr)
        elif isinstance(node.arg, nodes.Const) and isinstance(node.arg.value, str):
            attrs.append(node.arg.value)
        else:
            return None
        node = node.node
    if isinstance(node, nodes.Name):
        return node.name, list(reversed(attrs))
    return None


def _schema_walk(schema, attrs):
    """None when `attrs` is a valid path in the page schema (or leaves it at
    an array / a leaf), else the first unknown attribute."""
    node = schema
    for attr in attrs:
        if attr in ALLOWED_METHODS:
            return None
        props = node.get("properties") if isinstance(node, dict) else None
        if props is None:
            return None
        if attr not in props:
            return attr
        node = props[attr]
    return None


def _items_schema(schema, attrs):
    node = schema
    for attr in attrs:
        props = node.get("properties") if isinstance(node, dict) else None
        if not props or attr not in props:
            return None
        node = props[attr]
    return node.get("items") if isinstance(node, dict) and node.get("type") == "array" else None


def _strip_jinja(text, expr="§"):
    text = re.sub(r"\{#.*?#\}", "", text, flags=re.S)
    text = re.sub(r"\{%.*?%\}", "", text, flags=re.S)
    return re.sub(r"\{\{.*?\}\}", expr, text, flags=re.S)


def _css_value_errors(prop, value, where, defined, cartridge_rules, in_desktop):
    errors = []
    raw = value
    value = value.replace("!important", "").strip()
    low = value.lower()
    if _HEX_RE.search(value) or _COLOUR_FN_RE.search(value):
        errors.append(f"{where}: colour literal in `{prop}:{raw}` -- use a token, var(--pk-*)")
    elif COLOUR_PROPS_RE.match(prop):
        words = set(re.findall(r"(?<![-\w(])([a-z]+)(?![-\w(])", re.sub(r"var\([^)]*\)", "", low)))
        named = words & NAMED_COLOURS
        if named:
            errors.append(f"{where}: colour literal {sorted(named)[0]!r} in `{prop}` -- use a token, var(--pk-*)")
    if "gradient(" in low:
        errors.append(f"{where}: gradient in `{prop}` -- the boxless look has no gradients")
    if prop in ("box-shadow", "text-shadow") and low not in ("none", "0"):
        errors.append(f"{where}: `{prop}:{value}` -- no shadows (boxless rules)")
    if prop == "filter" and "drop-shadow" in low:
        errors.append(f"{where}: drop-shadow filter -- no shadows (boxless rules)")
    if prop == "border" and low not in ("0", "none", "0 none"):
        errors.append(f"{where}: four-sided `border:{value}` makes a box -- use a hairline border-top or "
                      "border-bottom (boxless rules)")
    if prop in ("background", "background-color", "background-image"):
        rest = re.sub(r"var\([^)]*\)", "", low).strip()
        if rest not in ("", "transparent", "none", "inherit", "initial"):
            errors.append(f"{where}: `{prop}:{value}` -- a background is a token or transparent only")
    if prop == "border-radius":
        for token in re.sub(r"var\([^)]*\)", "var", low).split():
            m = re.fullmatch(r"(\d+(?:\.\d+)?)px", token)
            if token in ("var", "0", "50%", "999px", "9999px", "/") or (m and float(m.group(1)) <= 4):
                continue
            errors.append(f"{where}: `border-radius:{value}` -- radii are var(--pk-radius), 0, 4px or a pill")
            break
    if prop in ("width", "min-width", "flex-basis") and not in_desktop:
        m = re.search(r"(\d+(?:\.\d+)?)px", low)
        if m and float(m.group(1)) > MOBILE_WIDTH:
            errors.append(f"{where}: `{prop}:{value}` is wider than a {MOBILE_WIDTH}px phone outside a min-width "
                          "query (mobile first)")
    if prop == "font-family":
        rest = re.sub(r"var\([^)]*\)", "", low).replace(",", " ").strip()
        if rest not in ("", "inherit", "initial"):
            errors.append(f"{where}: `font-family:{value}` -- fonts come from the tenant tokens "
                          "(var(--pk-sans), var(--pk-heading), var(--pk-display-wide), var(--ps-display-acid), "
                          "var(--ps-serif)) only")
    if prop == "font" and low not in ("inherit", "initial") and ("var(" not in low or re.search(r"[\"']", low)):
        errors.append(f"{where}: `font:{value}` -- the family must be a tenant token, var(--pk-*)")
    if "url(" in low:
        errors.append(f"{where}: url() in `{prop}` -- a look loads nothing; fonts come from the tenant's own "
                      "base.css")
    for name in re.findall(r"var\(\s*(--[\w-]+)", value):
        if not (name.startswith(("--ps-", "--adv-")) or name in defined):
            errors.append(f"{where}: var({name}) is not a tenant token (--ps-*) and is not defined in the look")
    return errors


def _css_errors(css, cartridge_rules, defined, *, desktop=False, depth=0):
    errors, dark = [], set()
    root_class = cartridge_rules["root_class"]
    for item in css_scope.parse_stylesheet(css):
        if item[0] == "statement":
            errors.append(f"CSS: statement {item[1][:60]!r} -- no @import/@charset/@namespace in a look")
            continue
        if item[0] != "block":
            continue
        prelude, body = item[1].strip(), item[2]
        if prelude.startswith("@"):
            word = prelude.split(None, 1)[0].split("(")[0].lower()
            if word == "@media":
                if re.search(r"max-width", prelude, re.I):
                    errors.append(f"CSS: `{prelude}` -- mobile first: widen with min-width queries only")
                minw = _media_min_width(prelude)
                sub_errors, sub_dark = _css_errors(body, cartridge_rules, defined,
                                                   desktop=desktop or bool(minw and minw > MOBILE_WIDTH),
                                                   depth=depth + 1)
                errors += sub_errors
                dark |= sub_dark
            elif word in ("@supports", "@keyframes"):
                if word == "@supports":
                    sub_errors, sub_dark = _css_errors(body, cartridge_rules, defined, desktop=desktop,
                                                       depth=depth + 1)
                    errors += sub_errors
                    dark |= sub_dark
            else:
                errors.append(f"CSS: `{word}` is not allowed in a look (no @font-face, @import or @layer)")
            continue
        selectors = [s.strip() for s in css_scope._split_top_level(prelude)]
        for sel in selectors:
            if root_class not in sel:
                errors.append(f"CSS: selector `{sel}` is not scoped under .{root_class}")
        decls = [(m.group(1).strip().lower(), m.group(2).strip()) for m in _DECL_RE.finditer(body)]
        for prop, value in decls:
            errors += _css_value_errors(prop, value, f"CSS `{prelude[:70]}`", defined, cartridge_rules, desktop)
            if prop in ("background", "background-color") and any(t in value for t in DARK_TOKENS):
                for sel in selectors:
                    last = _last_compound(sel)
                    if ":hover" in sel or ":focus" in sel or cartridge_rules["cta_class"] in last \
                            or cartridge_rules["secondary_class"] in last or "bar" in last:
                        continue
                    dark.add(last)
    return errors, dark


def _literal_text_errors(static_html):
    body = re.sub(r"<(style|script)\b.*?</\1>", " ", static_html, flags=re.S | re.I)
    body = re.sub(r"<!--.*?-->", " ", body, flags=re.S)
    chunks = [html_mod.unescape(c).replace("§", " ") for c in re.split(r"<[^>]*>", body)]
    errors, total = [], 0
    for chunk in chunks:
        words = re.findall(r"[^\W\d_][\w'’-]*|\d+", chunk)
        if not words:
            continue
        total += len(words)
        if re.search(r"\d", chunk):
            errors.append(f"literal text {chunk.strip()[:50]!r} has a number -- numbers come from the renderer")
        if len(words) > MAX_LITERAL_RUN_WORDS:
            errors.append(f"literal text {chunk.strip()[:50]!r} is {len(words)} words -- a look may only carry "
                          f"short section labels (at most {MAX_LITERAL_RUN_WORDS} words); copy comes from page.json")
    if total > MAX_LITERAL_WORDS:
        errors.append(f"the look carries {total} words of its own text (at most {MAX_LITERAL_WORDS}); copy comes "
                      "from page.json")
    return errors


def static_errors(text, cartridge, *, contract_, schema, max_dark=1):
    """Every rule a template must meet before it is rendered. Returns a list
    of error strings (empty = pass). `max_dark`: how many dark bands the
    look may have (cycle 90: as many as the observed page; None = a dark
    page, no limit)."""
    rules = CARTRIDGE_RULES[cartridge]
    errors = []
    if len(text.encode("utf-8")) > MAX_TEMPLATE_BYTES:
        return [f"the template is larger than {MAX_TEMPLATE_BYTES // 1024} KB"]
    try:
        ast = _jinja_env().parse(text)
    except jinja2.TemplateSyntaxError as e:
        return [f"Jinja syntax error on line {e.lineno}: {e.message}"]

    # -- structure
    extends = list(ast.find_all(nodes.Extends))
    if len(extends) != 1 or not (isinstance(extends[0].template, nodes.Const)
                                 and extends[0].template.value == "base.html"):
        errors.append('the template must start with {% extends "base.html" %} (once)')
    from .blocks import catalog as block_catalog

    included = []
    for node in ast.find_all((nodes.Include, nodes.Import, nodes.FromImport)):
        # Cycle 91: the one allowed include -- a catalog block's partial, by
        # its literal name.
        m = (_BLOCK_INCLUDE_RE.match(node.template.value) if isinstance(node, nodes.Include)
             and isinstance(node.template, nodes.Const) and isinstance(node.template.value, str) else None)
        if m and block_catalog.get(m.group(1)) is not None:
            included.append(m.group(1))
            continue
        errors.append(f"line {node.lineno}: {{% include %}}/{{% import %}} is not allowed in a look, except "
                      f"{{% include \"blocks/<id>.html\" %}} for a catalog block ({', '.join(block_catalog.ids())})")
    blocks = [b.name for b in ast.find_all(nodes.Block)]
    for name in blocks:
        if name not in ALLOWED_BLOCKS:
            errors.append(f"block {name!r} is not one of base.html's blocks {sorted(ALLOWED_BLOCKS)}")
    if blocks.count("content") != 1:
        errors.append("the template must define {% block content %} exactly once")

    # -- names and attributes
    assigned = set()
    for node in ast.find_all(nodes.Assign):
        assigned |= {n.name for n in node.target.find_all(nodes.Name)} if not isinstance(node.target, nodes.Name) \
            else {node.target.name}
    for node in ast.find_all((nodes.For, nodes.AssignBlock)):
        assigned |= {n.name for n in node.target.find_all(nodes.Name)} | (
            {node.target.name} if isinstance(node.target, nodes.Name) else set())
    macros = {m.name for m in ast.find_all(nodes.Macro)}
    for m in ast.find_all(nodes.Macro):
        assigned |= {a.name for a in m.args}
    allowed_names = set(contract_["names"]) | JINJA_NAMES
    for name in sorted(meta.find_undeclared_variables(ast) - assigned - macros):
        if name not in allowed_names:
            errors.append(f"unknown variable {name!r}: a look may read only {sorted(contract_['names'])}")
    for node in ast.find_all(nodes.Name):
        if node.name.startswith("_"):
            errors.append(f"line {node.lineno}: name {node.name!r} starts with an underscore")
    for node in ast.find_all(nodes.Getattr):
        if node.attr.startswith("_"):
            errors.append(f"line {node.lineno}: attribute {node.attr!r} starts with an underscore")
    for node in ast.find_all(nodes.Const):
        if isinstance(node.value, str) and "__" in node.value:
            errors.append(f"line {node.lineno}: a string with '__' in it")

    aliases = {"cartridge_data": "cartridge_data", "first_screen": "first_screen", "page": "page",
               "product": "product", "blocks": "blocks"}
    for node in ast.find_all(nodes.Assign):
        if isinstance(node.target, nodes.Name) and isinstance(node.node, nodes.Name) and node.node.name in aliases:
            aliases[node.target.name] = aliases[node.node.name]
    loop_schemas, ambiguous = {}, set()
    for node in ast.find_all(nodes.For):
        if not isinstance(node.target, nodes.Name):
            continue
        ch = _chain(node.iter)
        items = _items_schema(schema, ch[1]) if ch and aliases.get(ch[0]) == "page" else None
        name = node.target.name
        if items is not None:
            if name in loop_schemas and loop_schemas[name] is not items:
                ambiguous.add(name)
            loop_schemas[name] = items
        else:
            ambiguous.add(name)
    used = set()
    for node in ast.find_all((nodes.Getattr, nodes.Getitem)):
        ch = _chain(node)
        if not ch:
            continue
        root, attrs = ch
        if not attrs:
            continue
        real = aliases.get(root)
        used.add((real or root, attrs[0]))
        if real == "page":
            bad = _schema_walk(schema, attrs)
            if bad:
                errors.append(f"line {node.lineno}: page.{'.'.join(attrs)} -- {bad!r} is not in the "
                              f"{cartridge} page schema")
        elif real in contract_["data"]:
            if attrs[0] not in contract_["data"][real] and attrs[0] not in ALLOWED_METHODS:
                errors.append(f"line {node.lineno}: {root}.{attrs[0]} -- {attrs[0]!r} is not in the renderer's "
                              f"{real} ({', '.join(contract_['data'][real])})")
        elif real == "product":
            if attrs[0] not in contract_["product"]:
                errors.append(f"line {node.lineno}: product.{attrs[0]} is not a product field")
        elif root in loop_schemas and root not in ambiguous:
            bad = _schema_walk(loop_schemas[root], attrs)
            if bad:
                errors.append(f"line {node.lineno}: {root}.{'.'.join(attrs)} -- {bad!r} is not a field of the "
                              "items it loops over")
    errors = list(dict.fromkeys(errors))

    # -- calls and filters
    for node in ast.find_all(nodes.Call):
        if isinstance(node.node, nodes.Name):
            if node.node.name not in ALLOWED_CALL_NAMES | macros:
                errors.append(f"line {node.lineno}: call of {node.node.name!r} is not allowed")
        elif isinstance(node.node, nodes.Getattr):
            if node.node.attr not in ALLOWED_METHODS:
                errors.append(f"line {node.lineno}: method .{node.node.attr}() is not allowed")
        else:
            errors.append(f"line {node.lineno}: this call is not allowed")
    for node in ast.find_all(nodes.Filter):
        if node.name not in ALLOWED_FILTERS:
            errors.append(f"line {node.lineno}: filter {node.name!r} is not allowed")
        elif node.name == "safe":
            target = node.node
            ok = (isinstance(target, nodes.Name) and target.name in SAFE_NAMES) or (
                isinstance(target, nodes.Getattr) and target.attr.endswith("_html"))
            if not ok:
                errors.append(f"line {node.lineno}: `| safe` only on byline_html, about_author_html or a "
                              "renderer *_html value -- never on page copy")
    for node in ast.find_all(nodes.Test):
        if node.name not in ("defined", "undefined", "none", "string", "number", "mapping", "sequence",
                             "iterable", "odd", "even", "divisibleby", "in", "eq", "ne", "lt", "gt", "le", "ge"):
            errors.append(f"line {node.lineno}: test {node.name!r} is not allowed")

    # Cycle 91: an included catalog block prints what it covers (the faq
    # block prints page.faq, the trust bar the trust facts, ...).
    for block_id in included:
        used |= set(block_catalog.get(block_id).covers)

    # -- required references (the renderer-owned blocks and the writer's sections)
    for root, keys in rules["required"].items():
        if root == "names":
            names = meta.find_undeclared_variables(ast)
            for key in keys:
                if key not in names:
                    errors.append(f"the look drops a required block: it never uses `{key}`")
            continue
        for key in keys:
            if (root, key) not in used:
                errors.append(f"the look drops a required block: it never uses `{root}.{key}`")

    # -- markup
    static = _strip_jinja(text)
    styles = re.findall(r"<style\b[^>]*>(.*?)</style>", text, re.S | re.I)
    if len(styles) != 1:
        errors.append(f"the look must have exactly one <style> block (it has {len(styles)})")
    for m in _FORBIDDEN_TAG_RE.finditer(static):
        errors.append(f"<{m.group(1).lower()}> is not allowed in a look (images come through "
                      "render_image_slot; no forms, buttons, frames or external resources)")
    # Cycle 102: the one literal <img> is the byline avatar, on its chrome class
    # (pagechecks.py does not count it as a content image slot).
    for m in re.finditer(r"<img\b([^>]*)>", static, re.I):
        if "adv-brand-logo" not in m.group(1):
            errors.append('a literal <img> must be the byline avatar with class="adv-brand-logo" (every other '
                          "image goes through render_image_slot)")
    scripts = re.findall(r"<script\b([^>]*)>(.*?)</script>", text, re.S | re.I)
    if len(scripts) > 1:
        errors.append("at most one inline <script> (the sticky bar's observer)")
    for attrs, body in scripts:
        if "src" in attrs.lower():
            errors.append("<script src> is not allowed")
        if "{{" in body or "{%" in body:
            errors.append("the <script> may not contain Jinja")
        if len(body) > MAX_SCRIPT_CHARS:
            errors.append(f"the <script> is longer than {MAX_SCRIPT_CHARS} characters")
        bad = _SCRIPT_BAD_RE.search(body)
        if bad:
            errors.append(f"the <script> uses {bad.group(0)!r}: only a small observer that toggles a class is "
                          "allowed")
    for m in re.finditer(r"\b(href|src|action|srcset|xlink:href|poster|data)\s*=\s*([\"'])(.*?)\2", static, re.I | re.S):
        value = m.group(3).strip()
        if value == "§" or (m.group(1).lower() == "href" and value.startswith("#")):
            continue
        errors.append(f"literal {m.group(1)}={value[:60]!r}: every link comes from the page or the renderer "
                      "({{ page.cta_url }}, the secondary link)")
    if re.search(r"(?:https?:)?//[a-z0-9.-]+\.[a-z]{2,}", re.sub(r"<style\b.*?</style>", "", static, flags=re.S | re.I),
                 re.I):
        errors.append("an external URL in the markup -- a look links and loads nothing of its own")
    if re.search(r"\son[a-z]+\s*=", static, re.I):
        errors.append("an inline event handler (on...=) -- not allowed")
    wrapper = [m for m in re.finditer(r'class\s*=\s*"([^"]*)"', static) if "pk-lp" in m.group(1).split()]
    want = rules["wrapper"]
    if len(wrapper) != 1 or any(c not in wrapper[0].group(1).split() for c in want):
        errors.append(f'the look needs exactly one wrapper element with class="{" ".join(want)}"')
    errors += _literal_text_errors(static)
    for m in re.finditer(r'\b(fill|stroke|color|bgcolor|stop-color)\s*=\s*"([^"]*)"', static, re.I):
        value = m.group(2).strip().lower()
        if value not in ("none", "currentcolor", "§", "transparent", "inherit"):
            errors.append(f"colour literal {m.group(1)}={value!r} in the markup -- use currentColor or a token")

    # -- CSS (style block + style attributes)
    css = _strip_jinja(styles[0], expr="0") if styles else ""
    attr_css = [_strip_jinja(v, expr="0") for v in re.findall(r'\bstyle\s*=\s*"([^"]*)"', text)]
    defined = set(re.findall(r"(--[\w-]+)\s*:", css + ";".join(attr_css)))
    css_errs, dark = _css_errors(css, rules, defined)
    errors += css_errs
    if max_dark is not None and len(dark) > max_dark:
        errors.append(f"{len(dark)} dark bands ({', '.join(sorted(dark))}): the look has ONE dark closing band"
                      if max_dark == 1 else
                      f"{len(dark)} dark bands ({', '.join(sorted(dark))}): the observed page has {max_dark}; "
                      "the look may not have more")
    for value in attr_css:
        for prop, val in ((m.group(1).strip().lower(), m.group(2)) for m in _DECL_RE.finditer(value)):
            errors += _css_value_errors(prop, val, "a style attribute", defined, rules, False)
    return list(dict.fromkeys(errors))


# ---------------------------------------------------------------------------
# Template validation -- rendered
# ---------------------------------------------------------------------------

_STYLE_RE = re.compile(r"<style\b[^>]*>(.*?)</style>", re.S | re.I)
_HEADING_RE = re.compile(r"<(h[123])\b([^>]*)>", re.I)
_PAGE_SCOPED = re.compile(r":has\((?:~ \* > )?\.(?:adv-wrap|pk-lp)\)|~ \* > \.adv-wrap")


def _rules_of(css):
    out = []
    for item in css_scope.parse_stylesheet(css):
        if item[0] != "block":
            continue
        prelude, body = item[1].strip(), item[2]
        if prelude.startswith("@"):
            if prelude.split(None, 1)[0].split("(")[0].lower() in ("@media", "@supports", "@container", "@layer"):
                out += _rules_of(body)
            continue
        out += [(sel.strip(), body) for sel in css_scope._split_top_level(prelude)]
    return out


def _uppercase_heading_hits(doc):
    """Every way an h1/h2/h3 could render uppercase (the cycle 78 brand lint
    rule): a rule that sets it on a heading's type or class, unless it uses
    the approved display family, or an inline style."""
    heading_classes, hits = set(), []
    for tag, attrs in _HEADING_RE.findall(doc):
        m = re.search(r'class="([^"]*)"', attrs)
        if m:
            heading_classes.update(m.group(1).split())
        s = re.search(r'style="([^"]*)"', attrs)
        if s and "uppercase" in s.group(1).lower():
            hits.append(f"inline style on <{tag}>")
    wrapper_upper = 'class="adv-wrap adv-case-upper' in doc
    for sel, body in _rules_of("\n".join(_STYLE_RE.findall(doc))):
        if not re.search(r"text-transform\s*:\s*uppercase", body, re.I):
            continue
        if re.search(r"font-family\s*:\s*var\(--pk-display-wide\)", body):
            continue
        if ".adv-case-upper" in sel and not wrapper_upper:
            continue
        last = _last_compound(sel)
        type_sel = re.match(r"^[a-zA-Z][\w-]*", last)
        classes = set(re.findall(r"\.([\w-]+)", last))
        if (type_sel and type_sel.group(0).lower() in ("h1", "h2", "h3")) or (classes and classes <= heading_classes):
            hits.append(sel)
    return hits


def brand_lint(doc, tenant):
    """The cycle 78/79 brand lint in tenant-neutral form: no gradient, no
    shadow, no uppercase heading outside the display family, exactly one
    h1, and the tenant's theme marker when it sets one."""
    problems = []
    css = "\n".join(_STYLE_RE.findall(doc))
    if re.search(r"gradient\s*\(", css, re.I):
        problems.append("brand lint: a gradient")
    for value in re.findall(r"box-shadow\s*:\s*([^;}\"]+)", doc, re.I):
        if value.replace("!important", "").strip().lower() != "none":
            problems.append(f"brand lint: box-shadow {value.strip()[:40]}")
    problems += [f"brand lint: uppercase heading ({h})" for h in _uppercase_heading_hits(doc)]
    if len(re.findall(r"<h1[\s>]", doc, re.I)) != 1:
        problems.append("brand lint: the page must have exactly one <h1>")
    marker = tenant.get("brand.theme_marker")
    if marker and f'data-pk-theme="{marker}"' not in doc:
        problems.append("brand lint: the theme marker is missing")
    return list(dict.fromkeys(problems))


def export_errors(export, tenant):
    """The `harness shopify-body` invariants (tests/test_landing_cycle82.py):
    one <style> at the top, every rule scoped to the page wrapper, one
    wrapper, no absolute link to the tenant's own site."""
    errors = []
    if not export.startswith("<style>\n") or export.count("</style>") != 1:
        errors.append("export: the storefront body must open with its one <style> block")
    unscoped = [sel for sel, _b in _rules_of("\n".join(_STYLE_RE.findall(export)))
                if not sel.startswith(".adv-wrap") and not _PAGE_SCOPED.search(sel)]
    if unscoped:
        errors.append(f"export: {len(unscoped)} CSS rule(s) not scoped to the page wrapper, e.g. {unscoped[0][:60]}")
    if export.count('class="pk-lp') != 1:
        errors.append('export: the body must have exactly one class="pk-lp ..." wrapper')
    if page_body.internal_link_re(tenant).search(export):
        errors.append("export: an absolute link to the tenant's own site survived (links must be relative)")
    return errors


def _wrapper_element(html_text):
    tree = parse_html(html_text)
    for el in tree.iter():
        if isinstance(el.tag, str) and "pk-lp" in (el.get("class") or "").split():
            return el
    return None


def _norm(text):
    return " ".join(html_mod.unescape(str(text or "")).replace("→", " ").split()).strip()


# Cycle 97 (owner, 2026-10-08): the landing page's sticky bar (`ld-bar`) names
# the model and its button reads exactly this, like the store's own product
# page bar; every other CTA keeps the page's cta_text.
BAR_CTA_TEXT = "Shop now"
BAR_CLASS = "ld-bar"


def byline_hrefs(tenant, cartridge):
    """Every link target the tenant's byline markup renders (byline_html,
    and about_author_html on the listicle): a look may print them through
    `byline_html | safe`, and they never count as a second CTA (cycle 90:
    page 03 was refused for the founder link its own byline carries)."""
    from .render import load_byline_html

    today = datetime.date.today().isoformat()
    try:
        markup = load_byline_html(tenant.brand_dir, today, today, tenant=tenant)
    except Exception:  # noqa: BLE001 -- no byline file: no byline links
        return set()
    return {a.get("href") for a in parse_html(markup or "").iter() if _tag(a) == "a" and a.get("href")}


def cta_errors(html_text, cartridge, page, ctx, byline_links=()):
    """One CTA phrase: inside the page wrapper every link is the page's
    cta_url (with exactly the page's cta_text, on a CTA class the A/B beacon
    counts) or a renderer-owned link (the secondary text link, the
    listicle's model links and byline, the byline_html links); the
    secondary link is never a CTA."""
    from . import abtest

    rules = CARTRIDGE_RULES[cartridge]
    wrapper = _wrapper_element(html_text)
    if wrapper is None:
        return ["render: the page wrapper (class pk-lp) is missing"]
    cta_url, cta_text = page.get("cta_url"), _norm(page.get("cta_text"))
    renderer = set()
    if ctx.get("secondary"):
        renderer.add(ctx["secondary"]["href"])
    for option in ctx.get("model_options") or []:
        if option.get("url"):
            renderer.add(option["url"])
    if (ctx.get("byline") or {}).get("url"):
        renderer.add(ctx["byline"]["url"])
    byline_links = set(byline_links or ())
    renderer |= byline_links
    allowed = [f"page.cta_url ({cta_url})"]
    if ctx.get("secondary"):
        allowed.append(f"the secondary text link cartridge_data.secondary.href ({ctx['secondary']['href']})")
    if cartridge == "listicle":
        allowed.append("the renderer's model links and byline link")
    if byline_links:
        allowed.append("the byline's own links, printed only through byline_html ("
                       + ", ".join(sorted(byline_links)) + ")")
    errors, ctas = [], 0
    in_bar = {id(a) for bar in wrapper.iter() if BAR_CLASS in (bar.get("class") or "").split()
              for a in bar.iter() if _tag(a) == "a"}
    for a in wrapper.iter():
        if _tag(a) != "a":
            continue
        href = a.get("href") or ""
        classes = set((a.get("class") or "").split())
        if href == cta_url:
            ctas += 1
            if _norm(_text(a)) != cta_text and not (id(a) in in_bar and _norm(_text(a)) == BAR_CTA_TEXT):
                errors.append(f"one CTA phrase: a CTA link reads {_norm(_text(a))[:40]!r}, not the page's "
                              f"cta_text {cta_text!r}")
            if not classes & set(abtest.CTA_CLASSES):
                errors.append(f"a CTA link has class {sorted(classes)} -- use {rules['cta_class']} (a class the "
                              "A/B beacon counts)")
        elif href in renderer:
            if ctx.get("secondary") and href == ctx["secondary"]["href"] and classes & set(abtest.CTA_CLASSES):
                errors.append("the secondary text link must not carry a CTA class")
        elif not href.startswith("#"):
            errors.append(f"a second link target {href[:60]!r} -- a look has ONE CTA; the only hrefs allowed are "
                          + "; ".join(allowed))
    if not ctas:
        errors.append("the rendered page has no CTA link to page.cta_url")
    if ctx.get("secondary"):
        n = sum(1 for a in wrapper.iter() if _tag(a) == "a" and a.get("href") == ctx["secondary"]["href"])
        if n != 1:
            errors.append(f"the secondary text link appears {n} times (exactly once)")
    for tag in ("button", "form", "input", "iframe"):
        if any(_tag(e) == tag for e in wrapper.iter()):
            errors.append(f"the rendered page has a <{tag}> inside the wrapper")
    return list(dict.fromkeys(errors))


def expected_texts(cartridge, page, ctx):
    """Values that must show on the rendered demo page: the renderer-owned
    blocks the demo has, and the writer's sections."""
    out = []
    if cartridge == "landing":
        for key in ("price", "warranty", "rating"):
            if ctx.get(key):
                out.append((key, ctx[key].get("text") or ctx[key].get("value")))
        if ctx.get("financing"):
            out.append(("financing", ctx["financing"]))
        out += [("trust", t["text"]) for t in ctx.get("trust") or []]
        out += [("specs", s["value"]) for s in ctx.get("specs") or []]
        out += [("checked_claims", c.get("fact")) for c in page.get("checked_claims") or []]
        out += [("faq", q.get("question")) for q in (page.get("faq") or {}).get("questions") or []]
        out.append(("closing", (page.get("closing") or {}).get("headline")))
        out.append(("hero.subhead", (page.get("hero") or {}).get("subhead")))
    else:
        out += [("reasons", r.get("heading")) for r in page.get("reasons") or []]
        out += [("faq", q.get("question")) for q in (page.get("faq") or {}).get("questions") or []]
        out.append(("closing", (page.get("closing") or {}).get("headline")))
    return [(k, v) for k, v in out if v]


def render_errors(text, cartridge, slug, tenant, demo, shot=None):
    """Renders the demo page in the candidate look (nothing stored), then
    the rendered checks. Returns (errors, html or None). `shot` (cycle 102):
    called with the rendered index.html while its assets still exist (the
    render at 390 for the result card and the revise round)."""
    from . import claims as claims_mod
    from . import landing as landing_mod
    from . import listicle as listicle_mod
    from . import vocab
    from .render import render_page

    errors = []
    prev_tenant, prev_vocab = tenant_mod.active_or_none(), vocab.active_or_none()
    tenant_mod.activate(tenant)
    try:
        with tempfile.TemporaryDirectory(prefix="look-import-", dir=work_root(tenant)) as tmp:
            tmp = Path(tmp)
            looks_root = tmp / "looks"
            (looks_root / cartridge / slug).mkdir(parents=True)
            (looks_root / cartridge / slug / "template.html").write_text(text)
            spec = look_spec_for(text)
            if spec:
                (looks_root / cartridge / slug / "look.json").write_text(json.dumps(spec) + "\n")
            page = copy.deepcopy(demo["page"])
            page["look"] = slug
            out = tmp / f"look-{slug}" / cartridge
            today = datetime.date.today().isoformat()
            try:
                render_page(cartridge_name=cartridge, page=page, ad_brief=copy.deepcopy(demo["ad_brief"]),
                            facts_pack=copy.deepcopy(demo["facts_pack"]), cartridges_dir=REPO_ROOT / "cartridges",
                            brand_dir=tenant.brand_dir, templates_dir=REPO_ROOT / "harness" / "templates",
                            out_dir=out, published=today, updated=today, tenant=tenant, download_assets=False,
                            looks_dir=looks_root)
            except claims_mod.ClaimsGateFailure as e:
                return [f"render backstop {e.stage}: {item.get('issue') or item}" for item in e.items][:20], None
            except jinja2.TemplateError as e:
                return [f"render failed: {type(e).__name__}: {e}"], None
            except Exception as e:  # noqa: BLE001 -- any crash of a candidate template is a refusal reason
                return [f"render failed: {type(e).__name__}: {e}"], None
            html_text = (out / "index.html").read_text()
            if shot is not None:
                shot(out / "index.html")
            export, _manifest = page_body.build_shopify_body(out)
        demo_page = demo["page"]
        if cartridge == "landing":
            ctx = landing_mod.render_context(demo_page, demo["facts_pack"], ad_brief=demo["ad_brief"], tenant=tenant)
        else:
            from . import first_screen as first_screen_mod

            ctx = dict(listicle_mod.render_context(demo["facts_pack"], tenant=tenant))
            ctx["byline"] = first_screen_mod.render_context(
                copy.deepcopy(demo_page), ad_brief=demo["ad_brief"], facts_pack=demo["facts_pack"], tenant=tenant,
                hero_style="display", frame=None, updated=datetime.date.today().isoformat())["byline"]
        errors += [f"review page {p}" for p in brand_lint(html_text, tenant)]
        errors += [f"export {p}" for p in brand_lint(export, tenant)]
        errors += export_errors(export, tenant)
        errors += cta_errors(html_text, cartridge, demo_page, ctx, byline_hrefs(tenant, cartridge))
        visible = _norm(" ".join(_text(el) for el in [_wrapper_element(html_text)] if el is not None))
        for key, value in expected_texts(cartridge, demo_page, ctx):
            if _norm(value) not in visible:
                errors.append(f"the rendered demo page does not show its {key} ({_norm(value)[:50]!r}): a "
                              "renderer-owned block or a writer section was dropped")
    finally:
        tenant_mod.set_active(prev_tenant)
        vocab.set_active(prev_vocab)
    return list(dict.fromkeys(errors)), html_text


def _no_network(url):
    raise ConnectionError(f"the look's review render runs offline: {url} was not fetched")


def review_document(text, cartridge, slug, tenant, demo, shot=None):
    """Cycle 102e: the review.html an imported look stores -- the demo page
    rendered in the look the way the Library build renders a version
    (offline: photo-library photos and cut-outs from their local files into
    assets/; an image with no local copy is dropped, never fetched), then
    made self-contained as `harness review` does (review.self_contained:
    every image a data: URI, the brand fonts inlined). The validation
    render (render_errors) keeps download_assets=False, so its paths
    ("photo-library/...", CDN urls) never resolve beside a stored file.
    `shot` is called with the written document, so the render at 390 shows
    exactly what the review link shows. Returns the html, or None when the
    render fails (the caller keeps the validation html)."""
    from . import review as review_mod
    from . import vocab
    from .render import render_page

    prev_tenant, prev_vocab = tenant_mod.active_or_none(), vocab.active_or_none()
    tenant_mod.activate(tenant)
    try:
        with tempfile.TemporaryDirectory(prefix="look-review-", dir=work_root(tenant)) as tmp:
            tmp = Path(tmp)
            looks_root = tmp / "looks"
            (looks_root / cartridge / slug).mkdir(parents=True)
            (looks_root / cartridge / slug / "template.html").write_text(text)
            spec = look_spec_for(text)
            if spec:
                (looks_root / cartridge / slug / "look.json").write_text(json.dumps(spec) + "\n")
            page = copy.deepcopy(demo["page"])
            page["look"] = slug
            out = tmp / f"look-{slug}" / cartridge
            today = datetime.date.today().isoformat()
            try:
                render_page(cartridge_name=cartridge, page=page, ad_brief=copy.deepcopy(demo["ad_brief"]),
                            facts_pack=copy.deepcopy(demo["facts_pack"]), cartridges_dir=REPO_ROOT / "cartridges",
                            brand_dir=tenant.brand_dir, templates_dir=REPO_ROOT / "harness" / "templates",
                            out_dir=out, published=today, updated=today, tenant=tenant, download_assets=True,
                            fetch_url=_no_network, looks_dir=looks_root)
            except Exception:  # noqa: BLE001 -- the template passed validation; keep its validation html
                return None
            html_text = review_mod.self_contained((out / "index.html").read_text(), out / "assets", tenant)
            path = out.parent / "review.html"
            path.write_text(html_text)
            if shot is not None:
                shot(path)
            return html_text
    finally:
        tenant_mod.set_active(prev_tenant)
        vocab.set_active(prev_vocab)


def validate_template(text, cartridge, slug, tenant, demo, *, contract_=None, schema=None, max_dark=1, shot=None):
    """(errors, rendered review html or None). Static rules first; the render
    runs only when they pass."""
    contract_ = contract_ or contract(cartridge, tenant, demo)
    schema = schema or load_schema(cartridge, tenant)
    errors = static_errors(text, cartridge, contract_=contract_, schema=schema, max_dark=max_dark)
    if errors:
        return errors, None
    return render_errors(text, cartridge, slug, tenant, demo, shot=shot)


# ---------------------------------------------------------------------------
# Convert (one model call, plus at most two repairs)
# ---------------------------------------------------------------------------

def contract_markdown(cartridge, contract_, schema):
    rules = CARTRIDGE_RULES[cartridge]

    def schema_lines(node, path, depth=0):
        lines = []
        for key, sub in (node.get("properties") or {}).items():
            kind = sub.get("type", "") if isinstance(sub, dict) else ""
            lines.append(f"{'  ' * depth}- `{path}.{key}` ({kind})")
            if isinstance(sub, dict) and sub.get("type") == "object":
                lines += schema_lines(sub, f"{path}.{key}", depth + 1)
            if isinstance(sub, dict) and sub.get("type") == "array" and isinstance(sub.get("items"), dict):
                lines += schema_lines(sub["items"], f"{path}.{key}[]", depth + 1)
        return lines

    parts = [f"## The {cartridge} contract (exact; anything else is refused)", "",
             "Top-level names: " + ", ".join(f"`{n}`" for n in contract_["names"]) + ".", ""]
    for root, keys in contract_["data"].items():
        parts.append(f"`{root}` keys: " + ", ".join(f"`{k}`" for k in keys) + ".")
    parts.append("`product` keys: " + ", ".join(f"`{k}`" for k in contract_["product"]) + ".")
    parts += ["", "page.json (the writer's fields):"] + schema_lines(schema, "page")
    req = rules["required"]
    parts += ["", "Required (the look must use every one): "
              + ", ".join(f"`{n}`" for n in req["names"]) + "; "
              + "; ".join(f"`{root}.{k}`" for root, keys in req.items() if root != "names" for k in keys) + ".",
              f'Wrapper: one element with class="{" ".join(rules["wrapper"])}"; every CSS selector contains '
              f'`.{rules["root_class"]}`. CTA class `{rules["cta_class"]}`; secondary text link class '
              f'`{rules["secondary_class"]}`.']
    return "\n".join(parts)


CROSS_MAP = {
    ("listicle", "landing"): """- `first_screen.headline_html` -> `cartridge_data.headline_html`; `page.dek` / `first_screen.lede` ->
  `page.hero.subhead`; `first_screen.eyebrow` -> `cartridge_data.eyebrow`.
- `page.reasons[]` {heading, text, image} -> `page.checked_claims[]` {ad_said, fact} (no photo per item: place
  the landing photos -- hero photo, room photo, ad still -- between sections where the source has photos).
- `first_screen.byline` -> nothing in the content: the landing byline is `byline_html` in the `byline` block.
- `cartridge_data.rating_line` -> `cartridge_data.rating` {value, label}; `cartridge_data.pull_quote` ->
  `cartridge_data.review_quote` {text, author}.
- `cartridge_data.trust_items` -> `cartridge_data.trust`; `first_screen.stats` -> `cartridge_data.specs`.
- `first_screen.value_stack` / the model option price -> `cartridge_data.price`, `cartridge_data.financing`,
  `cartridge_data.warranty`; `cartridge_data.model_options`, `page.audience_fit`, `about_author_html` -> none.
- `page.closing.*` -> `page.closing.headline`; the FAQ is `page.faq` in both.
- classes: wrapper `pk-lp adv-landing`, CTA `ld-btn`, secondary `ld-text` (once, `cartridge_data.secondary`),
  sticky bar `ld-bar`; every selector holds `.adv-landing`.""",
}


def build_system_prompt(cartridge, contract_, schema, exemplar=None, reference=True):
    """The rules file with the contract filled in, then ONE look to learn
    the contract's use from: the closest hand-made exemplar (its page's
    observation + its template) when there is one, else the cartridge's
    default look."""
    rules = RULES_PATH.read_text().replace("{{CONTRACT}}", contract_markdown(cartridge, contract_, schema))
    if exemplar and exemplar.get("kind") == "hand" and exemplar.get("cartridge", cartridge) != cartridge:
        # Cycle 106: a story page imported as a landing look learns from a
        # hand-made LISTICLE look (no landing look was made by hand); its
        # data names belong to the listicle contract.
        return (rules + f"\n\n## Exemplar: the hand-made {exemplar['cartridge']} look `{exemplar['slug']}`\n\n"
                + "A person converted a story/list page into a look by hand, LOOKING at the page; this is the "
                  "standard of craft to meet: the thin top strip, the byline and rating elements, the photo and "
                  "button cadence, the type scale in tokens. It is a " + exemplar["cartridge"] + " look: its "
                  "data names and classes are NOT valid in this " + cartridge + " look. Use ONLY the " + cartridge
                + " contract above; translate with this map:\n\n" + CROSS_MAP.get((exemplar["cartridge"], cartridge), "")
                + "\n\n### What was kept and dropped\n\n" + (exemplar.get("summary") or "(no summary)")
                + "\n\n### Its template.html (listicle names: translate, never copy a name)\n\n"
                + exemplar["template"])
    if exemplar and exemplar.get("kind") == "hand":
        # Cycle 102: a hand conversion -- its summary says what was kept and
        # dropped; its stored HTML observation was wrong, so it is not shown.
        return (rules + f"\n\n## Exemplar: the hand-made look `{exemplar['slug']}`\n\n"
                + "A person converted a page into a look by hand, LOOKING at the page; this is the standard to "
                  "meet. First what the person kept and dropped, then the template they made. Notice how each "
                  "section of the source became renderer data, how the photo and CTA cadence was kept, how the "
                  "grounds and the type scale became token-only CSS.\n\n### What was kept and dropped\n\n"
                + (exemplar.get("summary") or "(no summary)")
                + "\n\n### Its template.html\n\n" + exemplar["template"])
    if exemplar:
        return (rules + f"\n\n## Exemplar: the hand-made look `{exemplar['slug']}`\n\n"
                + "A person converted a page into a look by hand; this is the standard to meet. First the build "
                  "observation of the page it was made from, then the template made from it. Notice how each "
                  "observed section, the hero composition, the dark/light treatment and the type scale became "
                  "token-only CSS and renderer data. It may lack the data-look-pattern markers: yours must have "
                  "them.\n\n### Its page's observation\n\n"
                + json.dumps(exemplar["observation"], separators=(",", ":"))
                + "\n\n### Its template.html\n\n" + exemplar["template"])
    if not reference:   # cycle 102d: a revise -- the look being revised is the reference
        return rules
    default = looks.DEFAULT_LOOKS.get(cartridge, "open")
    # Cycle 94: the shipped look includes the shared hero partial; an imported
    # look is one file, so the model sees the look with its first screen inline.
    reference = heroes.standalone_source(
        (REPO_ROOT / "cartridges" / cartridge / "looks" / default / "template.html").read_text(), cartridge, default)
    return (rules + f"\n\n## Reference look: cartridges/{cartridge}/looks/{default}/template.html\n\n"
            + "This is the tenant's approved look for this cartridge. Keep its tokens, its renderer-owned blocks "
            + "and its CTA markup; change the LAYOUT and the design to follow the observation.\n\n" + reference)


def allowed_links_text(cartridge):
    """The one-CTA rule with the exact list of allowed hrefs."""
    rules = CARTRIDGE_RULES[cartridge]
    text = (f"Links (the one-CTA rule): the ONLY hrefs allowed are (1) `{{{{ page.cta_url }}}}` on every CTA, "
            f"class `{rules['cta_class']}`, text `{{{{ page.cta_text }}}}`; (2) the renderer's secondary text link "
            f"`{{{{ cartridge_data.secondary.href }}}}`, exactly once, class `{rules['secondary_class']}`, never a "
            "CTA class; (3) the links inside `{{ byline_html | safe }}` -- byline_html is the ONLY way to mention "
            "or link the founder or the author.")
    if cartridge == "landing":
        text += (f" The one exception to the CTA text: the CTA inside the sticky bar `{BAR_CLASS}` reads exactly "
                 f"`{BAR_CTA_TEXT}`, beside the model name (`product.full_name`) with the price beneath it.")
    if cartridge == "listicle":
        text += (f" The one exception to the CTA text: the CTA inside the sticky bar (class `{BAR_CLASS} op-bar`) "
                 f"reads exactly `{BAR_CTA_TEXT}`, beside the model name with the price beneath it.")
    if cartridge == "listicle":
        text += (" On the listicle also the renderer's model links (`cartridge_data.model_options[].url`) and "
                 "the first-screen byline link.")
    return text + " No other href: no founder, about, review, phone or shop link of your own."


# Cycle 102: what a hand conversion did that the automatic import did not
# (docs/FIXLOG.md cycle 102), asked of the model when it sees the page.
IMAGE_INSTRUCTIONS = """You also SEE the page: the screenshots above are the source. Convert it the way a person converts a
page by hand:
- Walk the source top to bottom and map every section onto the renderer data that fills it (the contract
  above). Keep the source's section ORDER, its PHOTO CADENCE (where photos recur: one per item, every other
  item, full width or inset) and its CTA CADENCE (after which items a button band sits, a sticky bar, the
  closing band). A section with no tenant data (an offer, a countdown, a bundle picker, a supplement panel) is
  dropped, not invented.
- Reproduce the composition in the tenant's tokens: the grounds band by band, the type scale (the headline size,
  case and weight; an accent phrase), the numerals, the grids, the image treatments.
- The sticky bar: the model name with the price beneath it and a CTA reading exactly `Shop now`. The
  listicle byline avatar is `<img class="adv-brand-logo" ...>` from `first_screen.byline.avatar_url`.
- Never copy a word, name, price, offer or claim you read in the screenshots: every word comes from
  page.json and the renderer context; your own labels stay short and generic.
- Right after the first line, write one Jinja comment
  `{# look-notes: Kept: <what you reproduced>. Dropped: <what had no tenant data>. #}` -- at most 120 words,
  layout words only. It becomes the look's summary in the Library."""


# Cycle 106: where each element of a story or list article comes from in
# the renderer's data. The first real imports fell back to the exemplar's
# stack because the prompt never said which data fills an author card, a
# rating line or a photo per item (docs/FIXLOG.md cycle 106).
DATA_MAP = {
    "landing": """- disclosure / kicker strip at the very top (promo-bar): `disclosure_label` + `ld.eyebrow`, one thin line.
- headline (h1): `ld.headline_html`; deck: `page.hero.subhead`.
- press logos or trust strip (trust-bar): no press data -- `ld.trust` items in that slot, a thin hairline row.
- author row: the landing byline is the whole About block (`byline_html`, printed in the `byline` block at the
  foot); do not move it up. Leave the author row out (it is not scored on the landing page).
- star rating line (rating-line): `ld.rating` {value, label} with inline SVG stars, under `{% if ld.rating %}`.
- review / pull quote (pull-quote): `ld.review_quote` {text, author} in `var(--ps-serif)`, under `{% if %}`.
- story sections or numbered items: `page.checked_claims[]` -- `ad_said` is the section's subhead (set it as the
  source sets its subheads: serif caps, heavy sans, a bold lead-in), `fact` the paragraph. Number them only if
  the source numbers them.
- photos: the hero product photo `assets.get(page.hero.hero_image.asset_id)`, the room photo (see above), the
  ad still `assets.get("asset-adframe")`; place them where the source's photos sit (after the intro, between
  story sections), each at most once. The checked claims carry no photo of their own.
- spec list or key facts (info-box / stat-strip): `ld.specs` [{label, value}].
- CTA band between sections (cta-band): `ld.price` + the CTA, after the section where the source puts its first
  button; a model/product section (product-card): `product.full_name`, the hero photo, `ld.price`,
  `ld.financing`, `ld.warranty`, the CTA.
- questions: `page.faq.questions[]`; closing band: `page.closing.headline` + the CTA + `ld.secondary`.""",
    "listicle": """- promo / guarantee / disclosure strip at the very top (promo-bar): `disclosure_label`, `first_screen.eyebrow`
  and `cartridge_data.trust_items` texts, one thin line (never an offer or a discount).
- headline (h1): `first_screen.headline_html`; deck: `page.dek` and/or `first_screen.lede`.
- author or doctor byline card (author-card): `first_screen.byline` {name, title, url, verified, updated,
  minutes, avatar_url, avatar_alt} -- the avatar `<img class="adv-brand-logo" src="{{ first_screen.byline.avatar_url }}"
  alt="{{ first_screen.byline.avatar_alt }}" width="44" height="44">`, the name (linked to `byline.url` when
  set), the title, "Updated {{ first_screen.byline.updated }}" and the minutes. Never invent a credential.
- star rating line (rating-line): `cartridge_data.rating_line.text` with inline SVG stars under
  `{% if cartridge_data.rating_line %}`; in its `{% else %}` the `first_screen.stats` with check marks.
- press logos: no press data -- `cartridge_data.trust_items` in that slot (trust-bar).
- pull quote (pull-quote): `cartridge_data.pull_quote` {text, attribution} in `var(--ps-serif)`, under `{% if %}`.
- numbered items: `page.reasons[]` {number, heading, text, image.asset_id, proof.text}; the item's own photo is
  `assets.get(item.image.asset_id)` through `render_image_slot` (photo-per-item, mark each item); big numerals
  (`{{ "%02d" | format(loop.index) }}` or `{{ loop.index }}`) when the source sets them big (big-numerals).
- CTA band between items (cta-band): `{% if loop.index == N and not loop.last %}` after item N where the source
  has its band: `page.display_headline` or `page.closing.headline` + the CTA.
- science / key-facts box (info-box): `first_screen.stats` [{value, label}] as label/value rows under a rule
  and a short label.
- product card inside the article (product-card): the hero photo `assets.get(page.hero.asset_id)`,
  `product.full_name`, `product.descriptor`, `first_screen.value_stack` (items, price, financing) or the
  model option's `price_text`, and the CTA.
- who it is for: `page.audience_fit`; questions: `page.faq.questions[]`; the models: `cartridge_data.model_options`;
  closing band: `page.closing` (headline, recap, warranty_line, financing_line) + the CTA.""",
}


def rhythm_table(observation):
    """One line per source section, top to bottom: its role, ground,
    photos, buttons, numbering, patterns and the observer's description --
    the order and rhythm the look must keep (cycle 106)."""
    lines = []
    for s in observation.get("sections") or []:
        st = s.get("style") or {}
        bits = [f"S{s['index']}", s["role"], "dark" if s.get("background") == "dark" else "light"]
        if st.get("pinned"):
            bits.append("pinned")
        if s.get("images"):
            bits.append(f"{s['images']} photo" + ("s" if s["images"] > 1 else ""))
        if (s.get("cta") or {}).get("count"):
            bits.append("button")
        if s.get("numbered"):
            bits.append("numbered")
        if s.get("patterns"):
            bits.append("[" + ", ".join(s["patterns"]) + "]")
        what = s.get("what") or ""
        lines.append(" | ".join(bits) + (f" -- {what}" if what else ""))
    return "\n".join(lines)


def build_user_message(cartridge, slug, observation, css_text="", images=False):
    names = [p["name"] for p in observation.get("patterns") or []]
    parts = [f"Cartridge: {cartridge}. Look name: {slug}.", ""]
    if observation.get("brief"):
        parts += ["Design brief (written by the harness from the observation):", observation["brief"], ""]
    if observation.get("observed_from"):
        parts += [f"Observed from: {observation['observed_from']} (the section-by-section observation below).", ""]
    parts += ["Build observation of the page to learn from (layout and style values only: the page's words, "
              "images, brand names and scripts are not included and must not be invented):", "",
              json.dumps(observation, separators=(",", ":")), ""]
    if css_text:
        parts += ["The page's own CSS, sanitised (no fonts, URLs or scripts; a colour literal is shown as the "
                  "tenant token it equals, var(--ps-...), or as a <light>/<dark>/<mid> placeholder -- write a "
                  "token, never a placeholder or a literal):", "", css_text, ""]
    parts.append("Reproduce the composition faithfully: the same section order, the same hero composition, the "
                 "same dark/light treatment, the same numerals, grids and type scale. Express every value with "
                 "tokens.")
    table = rhythm_table(observation)
    if table:
        g = observation.get("global") or {}
        parts += ["", "## The source, section by section (keep this ORDER and this RHYTHM)", "", table, ""]
        if g.get("photo_cadence") or g.get("cta_cadence"):
            parts.append(f"Photo cadence: {g.get('photo_cadence') or 'n/a'}. Button cadence: "
                         f"{g.get('cta_cadence') or 'n/a'}.")
        parts.append("Build the page from THIS table, not from the exemplar's or the reference's stack: the same "
                     "order, a photo where the rows have photos, a button where they have buttons. Rows of one kind "
                     "that repeat (story sections, numbered items, questions) are the writer's repeated items "
                     "(the checked claims, the reasons, the FAQ): their COUNT follows the data, their look and "
                     "rhythm follow the rows (a photo after every second one when the source does that). Never fill "
                     "a row with an unrelated renderer value (a spec list, the warranty, a quote) to keep the count: "
                     "put each renderer element where the source has that KIND of element, and drop rows with no "
                     "tenant data (name them in look-notes). The exemplar teaches the contract and the craft; its "
                     "section order is not yours.")
    parts += ["", f"## Which renderer data fills which source element ({cartridge})", "", DATA_MAP[cartridge], ""]
    if names:
        parts.append("Distinctive patterns to reproduce, each on the element that realises it with "
                     "data-look-pattern=\"<name>\": " + ", ".join(names) + ". Mark ONLY an element that really is "
                     "the pattern: the harness checks what is inside it (an author card needs its avatar, a product "
                     "card its photo and price or button, a CTA band its button, a rating line its stars).")
    parts.append("Right after the look-notes comment, write one more Jinja comment, "
                 "`{# section-map: S1 -> <your element and its data>; S2 -> ...; Sn -> dropped (why) #}`, one entry "
                 "per row of the table above, then build exactly that map.")
    if images:
        parts += ["", IMAGE_INSTRUCTIONS, ""]
    parts += [allowed_links_text(cartridge), "",
              "Write the complete template.html for this look. Output the template only: no prose, no code fence."]
    return "\n".join(parts)


def dark_allowance(observation):
    """How many dark bands an imported look may have: None (no limit) for a
    dark page, else as many as the page's content sections that are dark,
    at least one (the closing band)."""
    if (observation.get("global") or {}).get("dark_page"):
        return None
    dark = [s for s in observation["sections"] if s.get("background") == "dark"
            and s["role"] not in ("nav", "footer") and not (s.get("style") or {}).get("pinned")]
    return max(1, len(dark))


def fidelity_feedback(fid, observation):
    """The revise round's findings (cycle 106): each miss NAMED against the
    source -- the missing elements with the section that shows them, the
    sections where the source has photos and buttons and where the render
    has them, the role order and the grounds -- not only the score."""
    from . import look_design

    lines = [f"The harness accepted this template, but its fidelity to the observed page is {fid['score']} (below "
             f"{look_design.FIDELITY_THRESHOLD}). Compare the source screenshots (first message) with your render "
             "(below) section by section, fix every miss named here and keep what is already right."]
    by_name = {p["name"]: p for p in observation.get("patterns") or []}
    src = [s for s in observation["sections"]
           if s["role"] not in ("nav", "footer") and not (s.get("style") or {}).get("pinned")]
    if fid["missing"]:
        lines.append("Missing elements -- build each one where the source has it, from the data the data map names, "
                     "and mark it data-look-pattern=\"<name>\" (dark-page / dark-hero on a dark ground; "
                     "caps-headline on the upper-case h1; the marked element must hold what the pattern is):")
        for name in fid["missing"]:
            p = by_name.get(name) or {}
            where = f"section S{p['section']}" if p.get("section") is not None else "the whole page"
            lines.append(f"- {name}: {look_design.PATTERNS.get(name, name)} (the source shows it in {where}: "
                         f"{p.get('evidence', '')})")
            if name == "caps-headline":
                lines.append("  (the tenant's base.css sets `.adv-wrap h1{text-transform:none!important}`: write "
                             "`text-transform:uppercase!important` on the h1, or it renders in sentence case)")
    cad = fid.get("cadence") or {}

    def at(flags, sections=None):
        hits = [i for i, c in enumerate(flags) if c != "-"]
        if sections is not None:
            return ", ".join(f"S{sections[i]['index']}" for i in hits) or "none"
        return ", ".join(str(i + 1) for i in hits) or "none"

    if cad and cad.get("source_photos") != cad.get("render_photos"):
        lines.append(f"Photos: the source has a photo in {cad['source_photos'].count('p')} of its {len(src)} "
                     f"sections ({at(cad['source_photos'], src)}); your render has one in "
                     f"{cad['render_photos'].count('p')} of its {len(cad['render_photos'])} sections (number "
                     f"{at(cad['render_photos'])}). Put the photos where the source has them.")
    if cad and cad.get("source_ctas") != cad.get("render_ctas"):
        lines.append(f"Buttons: the source has a button in sections {at(cad['source_ctas'], src)}; your render in "
                     f"sections number {at(cad['render_ctas'])} of {len(cad['render_ctas'])}. Match the button "
                     "cadence (the sticky bar is not counted).")
    secs = fid.get("sections") or {}
    if fid.get("order", 1) < 0.85:
        lines.append("Section order -- the source: " + " > ".join(secs.get("source") or []) + "; your render: "
                     + " > ".join(secs.get("render") or []) + ".")
    if fid.get("bands", 1) < 0.85:
        dark = [f"S{s['index']}" for s in src if s.get("background") == "dark"]
        lines.append("Grounds: the source is dark in " + (", ".join(dark) or "no section") + " and light elsewhere; "
                     "follow it section by section.")
    if fid.get("unscored"):
        lines.append("Not scored (the demo page has no data for them; keep them under their {% if %}): "
                     + ", ".join(fid["unscored"]) + ".")
    lines.append("Return the corrected complete template.html only.")
    return "\n".join(lines)


def extract_template(text):
    text = (text or "").strip()
    m = re.match(r"^```[\w-]*\s*\n(.*?)\n```\s*$", text, re.S)
    return (m.group(1) if m else text).strip() + "\n"


def _call(client, model, system, messages, budget, log):
    from . import look_vision

    budget.check()
    # Cycle 102b: streamed. The Anthropic SDK refuses a non-streaming call
    # whose max_tokens may run past 10 minutes ("Streaming is required ..."),
    # and the drafting/revise calls ask for MAX_OUTPUT_TOKENS (template +
    # thinking). The final message carries the same usage and content.
    with client.messages.stream(model=model, max_tokens=MAX_OUTPUT_TOKENS, system=system, messages=messages,
                                **look_vision.model_kwargs(model)) as stream:
        resp = stream.get_final_message()
    usage = resp.usage
    cache_w = getattr(usage, "cache_creation_input_tokens", 0) or 0
    cache_r = getattr(usage, "cache_read_input_tokens", 0) or 0
    budget.record_call(usage.input_tokens, usage.output_tokens)
    family = pricing.family_id(model)
    if log:
        log.call("look_import.convert", family, usage.input_tokens, usage.output_tokens,
                 cache_creation_input_tokens=cache_w, cache_read_input_tokens=cache_r)
    cost = pricing.calculate_cost(family, usage.input_tokens, usage.output_tokens,
                                  cache_creation_input_tokens=cache_w, cache_read_input_tokens=cache_r)
    text = "".join(getattr(b, "text", "") for b in resp.content if getattr(b, "type", "text") == "text")
    return text, cost


# Cycle 102: a draft and ONE revise round (was three calls; the Opus calls
# with images cost more, and the revise round sees the render).
MAX_CALLS = 2


def _first_message(cartridge, slug, observation, css_text, sheets, sheets_dir):
    """The first user message: a plain string without images (as before),
    else the screenshots then the text, with a prompt-cache breakpoint on the
    last block so the revise round reads system + images + observation from
    the cache."""
    from . import look_vision

    text = build_user_message(cartridge, slug, observation, css_text, images=bool(sheets))
    if not sheets:
        return text
    blocks = look_vision.image_blocks(sheets, sheets_dir, "The source page, as screenshots (top to bottom):")
    blocks.append({"type": "text", "text": text, "cache_control": {"type": "ephemeral"}})
    return blocks


def _revise_content(feedback, *, sheets_dir, render_png, work_dir):
    """The revise round's user message: the findings, and with a render
    shot the render at 390 (as sheets) plus the source's first screen beside
    the render's."""
    from . import look_vision

    if render_png is None or not Path(render_png).is_file():
        return feedback
    work_dir = Path(work_dir)
    blocks = [{"type": "text", "text": feedback}]
    thumb = Path(sheets_dir) / look_vision.SOURCE_THUMB
    if thumb.is_file():
        pair = look_vision.side_by_side(thumb, render_png, work_dir / "side-by-side.jpg")
        blocks.append({"type": "text", "text": "Side by side, the first phone screen: the source (left), your "
                                               "render of the demo page (right)."})
        blocks.append({"type": "image", "source": {"type": "base64", "media_type": "image/jpeg",
                                                   "data": look_vision.b64(pair)}})
    shots = look_vision.prepare([(look_vision.open_image(render_png), "Your render at 390 px")], work_dir,
                                max_images=3, prefix="render-sheet-")
    blocks += look_vision.image_blocks(shots, work_dir, "Your whole render at 390 px, top to bottom. Compare its "
                                                        "order, grounds, photo and CTA cadence with the source:")
    return blocks


def convert(observation, *, cartridge, slug, tenant, demo, client, model=None, progress=None, log=None, css_text="",
            exemplar=None, sheets=None, sheets_dir=None, browser=None, work_dir=None, spent_usd=0.0):
    """(template text, review html, {"attempts", "cost_usd", "model",
    "fidelity", "render_shot"}) or LookImportError with the validation
    errors of the last attempt. A draft, then (cycle 102) ONE revise round
    when the validator refuses it or its fidelity is below
    look_design.FIDELITY_THRESHOLD, while the import has spent less than
    REVISE_MAX_SPENT_USD (`spent_usd`: the vision call before this). With
    `sheets` the model sees the screenshots; with `browser` each valid
    candidate is screenshotted at 390 (render_shot) and the revise round
    sees it beside the source. The valid template with the best fidelity is
    kept."""
    from . import look_design
    from . import look_vision
    from .budget import Budget, BudgetExceeded

    progress = progress or (lambda _s: None)
    model = model or look_vision.MODEL
    contract_ = contract(cartridge, tenant, demo)
    schema = load_schema(cartridge, tenant)
    system = build_system_prompt(cartridge, contract_, schema, exemplar=exemplar)
    messages = [{"role": "user", "content": _first_message(cartridge, slug, observation, css_text, sheets or [],
                                                           sheets_dir)}]
    budget = Budget(wall_s=900, tokens=400_000, calls=MAX_CALLS)
    max_dark = dark_allowance(observation)
    unscored = look_design.unscored_patterns(cartridge, demo_context(cartridge, tenant, demo))
    cost, errors, text, best, attempt = 0.0, [], "", None, 0
    work_dir = Path(work_dir) if work_dir else None
    for attempt in range(1, MAX_CALLS + 1):
        try:
            raw, call_cost = _call(client, model, system, messages, budget, log)
        except BudgetExceeded as e:
            if best:
                attempt -= 1
                break
            raise LookImportError(f"import stopped by its budget: {e}", errors) from e
        cost += call_cost
        text = extract_template(raw)
        progress("validating")
        render_png = None
        shot = None
        if browser and work_dir is not None:
            render_png = work_dir / f"render-{attempt}.png"

            def shot(index_html, _png=render_png):
                look_vision.screenshot_full(browser, index_html, _png, look_vision.PHONE, tenant=tenant)
        errors, review_html = validate_template(text, cartridge, slug, tenant, demo, contract_=contract_,
                                                schema=schema, max_dark=max_dark)
        if not errors:
            fid = look_design.fidelity(observation, review_html, tenant, unscored=unscored)
            # cycle 102e: the stored review.html and the shot are the self-contained document
            document = review_document(text, cartridge, slug, tenant, demo, shot=shot)
            shot_file = render_png if render_png is not None and render_png.is_file() else None
            if best is None or fid["score"] > best["fidelity"]["score"]:
                best = {"template": text, "review_html": document or review_html, "fidelity": fid,
                        "render_shot": shot_file}
            if fid["score"] >= look_design.FIDELITY_THRESHOLD:
                break
            feedback = fidelity_feedback(fid, observation)
        else:
            shot_file = None
            feedback = ("The harness refused this template:\n" + "\n".join(f"- {e}" for e in errors[:MAX_ERRORS_SHOWN])
                        + "\n\nReturn the corrected complete template.html only.")
        if attempt >= MAX_CALLS:
            break
        if spent_usd + cost >= REVISE_MAX_SPENT_USD and best is not None:
            if log:
                log.event("look_import", f"revise round skipped: ${spent_usd + cost:.2f} spent "
                                         f"(cap ${REVISE_MAX_SPENT_USD:.2f})")
            break
        progress("revising")
        content = feedback
        if sheets and work_dir is not None:
            content = _revise_content(feedback, sheets_dir=sheets_dir, render_png=shot_file, work_dir=work_dir)
        messages += [{"role": "assistant", "content": text}, {"role": "user", "content": content}]
    if best is None:
        exc = LookImportError(f"import refused: the {cartridge} look failed validation after {attempt - 1} "
                              f"revise round(s) ({len(errors)} problem(s)); nothing was stored", errors)
        exc.cost_usd = round(cost, 4)
        raise exc
    return best["template"], best["review_html"], {"attempts": attempt, "cost_usd": round(cost, 4), "model": model,
                                                   "fidelity": best["fidelity"], "render_shot": best["render_shot"]}


_LOOK_NOTES_RE = re.compile(r"\{#\s*look-notes:\s*(.*?)\s*#\}", re.S)


def look_notes(template):
    """The model's `{# look-notes: ... #}` (cycle 102): what it kept and
    dropped, at most 120 words; "" when there is none."""
    m = _LOOK_NOTES_RE.search(template or "")
    return " ".join(m.group(1).split()[:120]) if m else ""


# ---------------------------------------------------------------------------
# Store + the whole import
# ---------------------------------------------------------------------------

def look_folder(tenant, cartridge, slug):
    return Path(tenant.looks_dir) / cartridge / slug


def store_look(tenant, cartridge, slug, *, template, library, observation, review_html, files=None):
    """Writes the four files (cycle 102: plus `files`, {name: source path} --
    the screenshots the model saw, the source thumbnail, the render at 390)
    into a hidden staging folder, then renames it to looks/<cartridge>/<slug>
    -- a half-written look is never visible. A name taken meanwhile is a
    collision."""
    root = Path(tenant.looks_dir) / cartridge
    root.mkdir(parents=True, exist_ok=True)
    final = root / slug
    staging = root / f".staging-{slug}-{secrets.token_hex(4)}"
    staging.mkdir()
    try:
        (staging / "template.html").write_text(template)
        spec = look_spec_for(template)
        if spec:   # cycle 91: the catalog blocks it includes
            (staging / "look.json").write_text(json.dumps(spec, indent=2) + "\n")
        (staging / "library.json").write_text(json.dumps(library, indent=2) + "\n")
        (staging / "observation.json").write_text(json.dumps(observation, indent=2) + "\n")
        (staging / "review.html").write_text(review_html)
        for name, src in (files or {}).items():
            shutil.copyfile(src, staging / name)
        if final.exists():
            raise LookImportError(f"name collision: tenant {tenant.name} already has a {cartridge} look {slug!r}")
        os.rename(staging, final)
    finally:
        if staging.exists():
            shutil.rmtree(staging, ignore_errors=True)
    return final


DISCARDED_DIR = ".discarded"


def import_state(tenant, cartridge, slug):
    """Cycle 102, the import job page: "pending" (waits for Add to Library),
    "added", "discarded" (the folder is gone) or "stored" (an import made
    before cycle 102, or from the CLI: in the Library already)."""
    path = look_folder(tenant, cartridge, slug) / "library.json"
    if not path.is_file():
        return "discarded"
    try:
        data = json.loads(path.read_text())
    except ValueError:
        return "stored"
    return {"pending": "pending", "added": "added"}.get(data.get("library"), "stored") if isinstance(data, dict) \
        else "stored"


def add_to_library(tenant, cartridge, slug, *, by):
    """"Add to Library": library.json `library: "added"` (+ added_at,
    added_by). The caller queues the Library build (the existing path).
    Raises LookImportError unless the look is pending."""
    if import_state(tenant, cartridge, slug) != "pending":
        raise LookImportError(f"the {cartridge} look {slug!r} is not waiting to be added")
    path = look_folder(tenant, cartridge, slug) / "library.json"
    data = json.loads(path.read_text())
    data.update({"library": "added", "added_at": datetime.datetime.now().isoformat(timespec="seconds"),
                 "added_by": by})
    tmp = path.with_name(f".library-{secrets.token_hex(4)}.json")
    tmp.write_text(json.dumps(data, indent=2) + "\n")
    os.replace(tmp, path)
    return data


def discard(tenant, cartridge, slug):
    """"Discard": the pending look's folder moves to
    looks/<cartridge>/.discarded/<slug>-<time> (kept on disk, out of every
    listing; its name is free again). Raises LookImportError unless the
    look is pending. Returns the new folder."""
    if import_state(tenant, cartridge, slug) != "pending":
        raise LookImportError(f"the {cartridge} look {slug!r} is not waiting to be added")
    root = Path(tenant.looks_dir) / cartridge / DISCARDED_DIR
    root.mkdir(parents=True, exist_ok=True)
    dest = root / f"{slug}-{datetime.datetime.now().strftime('%Y%m%d-%H%M%S')}-{secrets.token_hex(2)}"
    os.rename(look_folder(tenant, cartridge, slug), dest)
    return dest


# Cycle 102c: the import's scratch folders (the page copy the screenshot
# browser loads, the demo render the render shot is taken of) live under the
# tenant's jobs folder, never under TMPDIR or /tmp: a snap-packaged Chromium
# (prod) has a private /tmp and could not read them, so a worker import came
# out "observed_from: html" with no screenshots (harness/capture.py keeps its
# own frame and profile beside the PNG for the same reason).
WORK_DIR = "work"


def work_root(tenant):
    """tenants/<t>/jobs/look-imports/work/ (created)."""
    from .jobs import LOOK_IMPORT_PENDING

    root = Path(tenant.jobs_dir) / LOOK_IMPORT_PENDING / WORK_DIR
    root.mkdir(parents=True, exist_ok=True)
    return root


def _source_images(src, work, *, browser, progress):
    """[(PIL image, label)] of the source: the uploaded images, or (cycle
    102) the page's screenshots at 390 and 1280 when a browser is here."""
    from . import look_vision

    try:
        if src["kind"] == "images":
            names = [p.name for p in src["paths"]]
            n = len(src["paths"])
            return [(look_vision.open_image(path), f"Screenshot {k} of {n}" + (f" ({names[k - 1]})" if n > 1 else ""))
                    for k, path in enumerate(src["paths"], 1)]
        if not src.get("html") or not browser:
            return []
        progress("capturing")
        shots = look_vision.capture_page(src["html"], src.get("base_url"), work / "capture", browser=browser)
        return [(look_vision.open_image(path), label) for path, label in shots]
    except look_vision.VisionError as e:
        raise LookImportError(f"refused: {e}") from e


def import_look(tenant, *, cartridge, name, source, title=None, client=None, model=None, dry_run=False,
                progress=None, log=None, transport=None, resolver=None, exemplar=None, review=False):
    """The whole import. dry_run: the observation only (no model call,
    nothing written; with images, the sheets that would be sent and the
    HTML observation when there is one). `exemplar`: a hand-made look's slug
    to show the model instead of the closest one. `review` (cycle 102, the
    site's job): the stored look waits for "Add to Library" (library.json
    `library: "pending"`). Returns a JSON-ready result dict; raises
    LookImportError (nothing stored) on any refusal."""
    from . import look_design
    from . import look_vision

    progress = progress or (lambda _s: None)
    slug = check_name(tenant, cartridge, name)
    title = " ".join(str(title or "").split())[:MAX_TITLE_CHARS] or slug.replace("-", " ").capitalize()
    progress("reading")
    src = load_source(source, transport=transport, resolver=resolver)
    browser = look_vision.find_browser()
    with tempfile.TemporaryDirectory(prefix="look-import-", dir=work_root(tenant)) as tmp:
        work = Path(tmp)
        html_obs, css_text = None, ""
        if src.get("html") and not src.get("over_cap"):
            progress("observing")
            html_obs, css_text = observe_with_css(src, transport=transport, resolver=resolver, tenant=tenant)
        images = _source_images(src, work, browser=browser, progress=progress)
        # Cycle 102c: why an HTML source has no screenshots, for the job page
        capture_note = ("taken" if images else
                        "no headless browser on this host" if src["kind"] != "images" and not browser else
                        "the browser wrote no screenshot of the page")
        if log and src["kind"] != "images":
            log.event("look_import", f"screenshots: {capture_note}")
        if src.get("over_cap") and not images:
            raise LookImportError(f"refused: {src['origin']} is larger than {MAX_SOURCE_BYTES // 1024} KB and no "
                                  "screenshot of it could be taken here (no headless browser, or the capture "
                                  "failed); upload screenshots of the page instead")
        sheets_dir = work / "sheets"
        sheets = look_vision.prepare(images, sheets_dir) if images else []
        if images:
            look_vision.write_thumb(images[0][0], sheets_dir / look_vision.SOURCE_THUMB)
        result = {"slug": slug, "cartridge": cartridge, "title": title, "images": sheets, "capture": capture_note,
                  "observed_from": "images+html" if html_obs and sheets else ("images" if sheets else "html")}
        if dry_run:
            chosen = (look_design.choose_exemplar(html_obs, look_design.exemplars(tenant, cartridge), exemplar)
                      if html_obs else None)
            result.update({"observation": html_obs, "summary": summarise(html_obs) if html_obs else "",
                           "brief": (html_obs or {}).get("brief"),
                           "patterns": [p["name"] for p in (html_obs or {}).get("patterns") or []],
                           "exemplar": chosen["slug"] if chosen else None})
            return result
        candidates = look_design.exemplars(tenant, cartridge)
        if exemplar:   # an unknown exemplar is refused before any model call
            look_design.choose_exemplar({"patterns": [], "sections": []}, candidates, exemplar)
        demo = load_demo(tenant, cartridge)
        client = client or make_client()
        today = datetime.date.today().isoformat()
        run_id = f"look-import-{slug}-{datetime.datetime.now().strftime('%Y%m%d-%H%M%S')}"
        try:
            budget_mod.reserve_spend(tenant, run_id=run_id, today_iso=today, log=log, extra_usd=EST_IMPORT_USD)
        except budget_mod.BudgetExceeded as e:
            raise LookImportError(f"import not started: {e}") from e
        cost = 0.0
        try:
            observation = html_obs
            if sheets:
                progress("observing")
                from .budget import Budget

                try:
                    image_obs, cost = look_vision.observe(
                        sheets, sheets_dir, client=client, cartridge=cartridge, origin=src["origin"],
                        source_kind=src["kind"], budget=Budget(wall_s=600, tokens=120_000, calls=1),
                        model=model or look_vision.MODEL, log=log)
                except look_vision.VisionError as e:
                    cost = getattr(e, "cost_usd", 0.0)
                    raise LookImportError(f"refused: {e}") from e
                observation = look_vision.merge(image_obs, html_obs) if html_obs else image_obs
            else:
                observation["observed_from"] = "html"
            pool = candidates
            if not exemplar and cartridge == "landing" and look_design.is_story(observation) \
                    and not any(c["kind"] == "hand" for c in candidates):
                # Cycle 106: a story page learns from a hand-made listicle look
                pool = [dict(c, cartridge="listicle") for c in look_design.exemplars(tenant, "listicle")
                        if c["kind"] == "hand"] or candidates
            chosen = look_design.choose_exemplar(observation, pool, exemplar)
            progress("drafting")
            template, review_html, outcome = convert(
                observation, cartridge=cartridge, slug=slug, tenant=tenant, demo=demo, client=client, model=model,
                progress=progress, log=log, css_text=css_text, exemplar=chosen, sheets=sheets,
                sheets_dir=sheets_dir, browser=browser, work_dir=work, spent_usd=cost)
            cost += outcome["cost_usd"]
        except LookImportError as e:
            cost += getattr(e, "cost_usd", 0.0)
            e.cost_usd = round(cost, 4)
            raise
        finally:
            budget_mod.record_spend(tenant, run_id=run_id, cost=cost, today_iso=today, log=log)
        progress("storing")
        sha = observation_sha(observation)
        files = {s["file"]: sheets_dir / s["file"] for s in sheets}
        if (sheets_dir / look_vision.SOURCE_THUMB).is_file():
            files[look_vision.SOURCE_THUMB] = sheets_dir / look_vision.SOURCE_THUMB
        if outcome.get("render_shot"):
            files[look_vision.RENDER_SHOT] = outcome["render_shot"]
        roles = [s["role"] for s in observation["sections"] if s["role"] not in ("nav", "footer")]
        notes = look_notes(template)
        summary = summarise(observation)
        library = {
            "name": slug,
            "title": title,
            "summary": (f"{notes} ({summary})" if notes else summary),
            "source": {"kind": src["kind"], "origin": src["origin"],
                       "imported_at": datetime.datetime.now().isoformat(timespec="seconds"), "observation_sha": sha,
                       "images": [s["file"] for s in sheets]},
            "observed_from": observation["observed_from"],
            "capture": capture_note,
            "cartridge": cartridge,
            "derived_from_sections": roles,
            "abtest_entry": f"{cartridge}:{slug}",
            "validation": {"result": "PASS", "attempts": outcome["attempts"]},
            "model": outcome["model"],
            "cost_usd": round(cost, 4),
            "fidelity": outcome["fidelity"]["score"],
            "patterns": [p["name"] for p in observation.get("patterns") or []],
            "missing_patterns": outcome["fidelity"]["missing"],
            "exemplar": chosen["slug"] if chosen else None,
        }
        if look_vision.SOURCE_THUMB in files:
            library["source_thumb"] = look_vision.SOURCE_THUMB
        if look_vision.RENDER_SHOT in files:
            library["render_shot"] = look_vision.RENDER_SHOT
        if review:
            library["library"] = "pending"
        folder = store_look(tenant, cartridge, slug, template=template, library=library, observation=observation,
                            review_html=review_html, files=files)
    result.update({"observation": observation, "observation_sha": sha, "summary": library["summary"],
                   "brief": observation.get("brief"), "patterns": library["patterns"],
                   "exemplar": library["exemplar"], "library": library, "folder": str(folder),
                   "attempts": outcome["attempts"], "cost_usd": library["cost_usd"],
                   "fidelity": outcome["fidelity"]["score"], "fidelity_detail": outcome["fidelity"],
                   "missing_patterns": outcome["fidelity"]["missing"]})
    return result


def job_summary(result):
    """The JSON a look_import job keeps as its result (no observation dump)."""
    obs = result["observation"]
    g = obs["global"]
    return {
        "slug": result["slug"], "cartridge": result["cartridge"], "title": result["title"],
        "summary": result["summary"],
        "sections": [s["role"] for s in obs["sections"]],
        "observation": {"sections": g["sections"], "words": g["words"], "images": g["images"],
                        "fonts": g["fonts"], "max_width_px": g["max_width_px"], "sticky": g["sticky"],
                        "estimated_weight_kb": g["estimated_weight_kb"], "dropped": obs["dropped"]},
        "validation": f"PASS on attempt {result.get('attempts', 1)}",
        "cost_usd": result.get("cost_usd"),
        "abtest_entry": f"{result['cartridge']}:{result['slug']}",
        "brief": result.get("brief"),
        "patterns": result.get("patterns") or [],
        "fidelity": result.get("fidelity"),
        "missing_patterns": result.get("missing_patterns") or [],
        "exemplar": result.get("exemplar"),
        # cycle 102: what the observation came from, and the result card's pictures
        "observed_from": result.get("observed_from") or obs.get("observed_from") or "html",
        "capture": result.get("capture"),
        "images": len(result.get("images") or []),
        "source_thumb": bool((result.get("library") or {}).get("source_thumb")),
        "render_shot": bool((result.get("library") or {}).get("render_shot")),
    }


def queue_payload(tenant, *, name, cartridge, by, title=None, url=None, html_text=None, file_bytes=None,
                  filename=None, exemplar=None, images=None):
    """The quick checks of a site request (name, collision, exactly one
    source, size, an HTML upload/paste, an http(s) URL, cycle 102: images)
    and the look_import job payload. An upload or paste is written to
    tenants/<t>/jobs/look-imports/<16 hex>.html, images to
    tenants/<t>/jobs/look-imports/img-<16 hex>/ (look_vision.save_images),
    for the worker, which deletes them when the job ends. `images`:
    [(stream, filename)]. Raises LookImportError."""
    from . import jobs
    from . import look_vision

    slug = check_name(tenant, cartridge, name)
    url = (url or "").strip()
    images = [(s, n) for s, n in (images or []) if s is not None and n]
    given = [kind for kind, value in (("upload", file_bytes), ("url", url), ("paste", (html_text or "").strip()),
                                      ("images", images)) if value]
    if len(given) != 1:
        raise LookImportError("give exactly one source: screenshots, a page URL, an .html file, or pasted HTML")
    kind = given[0]
    payload = {"kind": kind, "slug": slug, "cartridge": cartridge, "by": by,
               "title": " ".join(str(title or name or "").split())[:MAX_TITLE_CHARS]}
    exemplar = (exemplar or "").strip()
    if exemplar:
        if not looks.LOOK_SLUG_RE.match(exemplar):
            raise LookImportError(f"exemplar {exemplar!r} is not a look name")
        payload["exemplar"] = exemplar
    if kind == "images":
        try:
            payload["images"] = look_vision.save_images(tenant, images)
        except look_vision.VisionError as e:
            raise LookImportError(str(e)) from e
        payload["files"] = [os.path.basename(str(n))[:120] for _s, n in images]
        return payload
    if kind == "url":
        parts = urllib.parse.urlsplit(url)
        if parts.scheme.lower() not in ("http", "https") or not parts.hostname:
            raise LookImportError("the URL must start with http:// or https://")
        if len(url) > MAX_URL_CHARS:
            raise LookImportError(f"the URL is longer than {MAX_URL_CHARS} characters")
        payload["url"] = url
        return payload
    if kind == "upload":
        filename = os.path.basename(str(filename or ""))
        if not filename.lower().endswith((".html", ".htm")):
            raise LookImportError("upload an .html or .htm file")
        data = bytes(file_bytes)
        payload["filename"] = filename[:120]
    else:
        data = html_text.encode("utf-8")
    if len(data) > MAX_SOURCE_BYTES:
        raise LookImportError(f"the page is larger than {MAX_SOURCE_BYTES // 1024} KB")
    decode_html(data)
    folder = Path(tenant.jobs_dir) / jobs.LOOK_IMPORT_PENDING
    folder.mkdir(parents=True, exist_ok=True)
    pending = f"{secrets.token_hex(8)}.html"
    (folder / pending).write_bytes(data)
    payload["pending"] = pending
    return payload
