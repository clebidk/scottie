"""Cycle 87: "Import a page" -- learn how any landing page is BUILT and turn
that structure into a new look for the landing (or listicle) cartridge, in
the tenant's own design language.

Three steps, one model call (plus at most one repair call):

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
from . import css_scope, looks, page_body, pricing
from . import tenant as tenant_mod
from .anthropic_client import make_client, thinking_kwargs
from .errors import HarnessError

REPO_ROOT = Path(__file__).resolve().parent.parent
RULES_PATH = Path(__file__).resolve().parent / "look_import_rules.md"

CARTRIDGES = ("landing", "listicle")
SOURCE_KINDS = ("upload", "url", "paste")
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
MAX_OUTPUT_TOKENS = 16000
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

PROGRESS_STEPS = ("reading", "observing", "converting", "validating", "repairing", "storing")


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
    {"kind": "paste", "html": str} | {"kind": "url", "url": str} ->
    {"kind", "origin", "html", "base_url", "bytes"}."""
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
        body, headers, final_url = fetch_url(source.get("url"), transport=transport, resolver=resolver)
        return {"kind": kind, "origin": urllib.parse.urlsplit(final_url).hostname or "",
                "html": decode_html(body, headers.get("content-type", "")), "base_url": final_url,
                "bytes": len(body)}
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


def _declarations(body):
    out = {}
    for m in _DECL_RE.finditer(body or ""):
        out[m.group(1).strip().lower()] = m.group(2).replace("!important", "").strip()
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


def observe(source, *, transport=None, resolver=None):
    """The build observation (a JSON-ready dict) of a loaded source. Holds
    counts and role names only, never the page's words."""
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
    return obs


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
                "render_image_slot", "image_fit", "look", "tenant_name"),
    "listicle": ("page", "product", "assets", "cartridge_data", "first_screen", "disclosure_label", "byline_html",
                 "about_author_html", "render_image_slot", "image_fit", "look", "tenant_name", "micro_cta_after"),
}
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
                          "(var(--pk-sans), var(--pk-heading), var(--pk-display-wide)) only")
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


def static_errors(text, cartridge, *, contract_, schema):
    """Every rule a template must meet before it is rendered. Returns a list
    of error strings (empty = pass)."""
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
    for node in ast.find_all((nodes.Include, nodes.Import, nodes.FromImport)):
        errors.append(f"line {node.lineno}: {{% include %}}/{{% import %}} is not allowed in a look")
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
               "product": "product"}
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
    if len(dark) > 1:
        errors.append(f"{len(dark)} dark bands ({', '.join(sorted(dark))}): the look has ONE dark closing band")
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


def cta_errors(html_text, cartridge, page, ctx):
    """One CTA phrase: inside the page wrapper every link is the page's
    cta_url (with exactly the page's cta_text, on a CTA class the A/B beacon
    counts) or a renderer-owned link (the secondary text link, the
    listicle's model links and byline); the secondary link is never a CTA."""
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
    errors, ctas = [], 0
    for a in wrapper.iter():
        if _tag(a) != "a":
            continue
        href = a.get("href") or ""
        classes = set((a.get("class") or "").split())
        if href == cta_url:
            ctas += 1
            if _norm(_text(a)) != cta_text:
                errors.append(f"one CTA phrase: a CTA link reads {_norm(_text(a))[:40]!r}, not the page's "
                              f"cta_text {cta_text!r}")
            if not classes & set(abtest.CTA_CLASSES):
                errors.append(f"a CTA link has class {sorted(classes)} -- use {rules['cta_class']} (a class the "
                              "A/B beacon counts)")
        elif href in renderer:
            if ctx.get("secondary") and href == ctx["secondary"]["href"] and classes & set(abtest.CTA_CLASSES):
                errors.append("the secondary text link must not carry a CTA class")
        elif not href.startswith("#"):
            errors.append(f"a second link target {href[:60]!r} -- a look has ONE CTA (plus the renderer's "
                          "secondary text link)")
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


def render_errors(text, cartridge, slug, tenant, demo):
    """Renders the demo page in the candidate look (nothing stored), then
    the rendered checks. Returns (errors, html or None)."""
    from . import claims as claims_mod
    from . import landing as landing_mod
    from . import listicle as listicle_mod
    from . import vocab
    from .render import render_page

    errors = []
    prev_tenant, prev_vocab = tenant_mod.active_or_none(), vocab.active_or_none()
    tenant_mod.activate(tenant)
    try:
        with tempfile.TemporaryDirectory(prefix="look-import-") as tmp:
            tmp = Path(tmp)
            looks_root = tmp / "looks"
            (looks_root / cartridge / slug).mkdir(parents=True)
            (looks_root / cartridge / slug / "template.html").write_text(text)
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
        errors += cta_errors(html_text, cartridge, demo_page, ctx)
        visible = _norm(" ".join(_text(el) for el in [_wrapper_element(html_text)] if el is not None))
        for key, value in expected_texts(cartridge, demo_page, ctx):
            if _norm(value) not in visible:
                errors.append(f"the rendered demo page does not show its {key} ({_norm(value)[:50]!r}): a "
                              "renderer-owned block or a writer section was dropped")
    finally:
        tenant_mod.set_active(prev_tenant)
        vocab.set_active(prev_vocab)
    return list(dict.fromkeys(errors)), html_text


def validate_template(text, cartridge, slug, tenant, demo, *, contract_=None, schema=None):
    """(errors, rendered review html or None). Static rules first; the render
    runs only when they pass."""
    contract_ = contract_ or contract(cartridge, tenant, demo)
    schema = schema or load_schema(cartridge, tenant)
    errors = static_errors(text, cartridge, contract_=contract_, schema=schema)
    if errors:
        return errors, None
    return render_errors(text, cartridge, slug, tenant, demo)


# ---------------------------------------------------------------------------
# Convert (the one model call, plus at most one repair)
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


def build_system_prompt(cartridge, contract_, schema):
    rules = RULES_PATH.read_text()
    default = looks.DEFAULT_LOOKS.get(cartridge, "open")
    reference = (REPO_ROOT / "cartridges" / cartridge / "looks" / default / "template.html").read_text()
    return (rules.replace("{{CONTRACT}}", contract_markdown(cartridge, contract_, schema))
            + f"\n\n## Reference look: cartridges/{cartridge}/looks/{default}/template.html\n\n"
            + "This is the tenant's approved look for this cartridge. Keep its tokens, its renderer-owned blocks "
            + "and its CTA markup; change the LAYOUT to follow the observation.\n\n" + reference)


def build_user_message(cartridge, slug, observation):
    return (f"Cartridge: {cartridge}. Look name: {slug}.\n\n"
            "Build observation of the page to learn from (layout only: the page's words, images, brand names and "
            "scripts are not included and must not be invented):\n\n"
            + json.dumps(observation, indent=1)
            + "\n\nWrite the complete template.html for this look. Output the template only: no prose, no code "
              "fence.")


def extract_template(text):
    text = (text or "").strip()
    m = re.match(r"^```[\w-]*\s*\n(.*?)\n```\s*$", text, re.S)
    return (m.group(1) if m else text).strip() + "\n"


def _call(client, model, system, messages, budget, log):
    budget.check()
    resp = client.messages.create(model=model, max_tokens=MAX_OUTPUT_TOKENS, system=system, messages=messages,
                                  **thinking_kwargs(model))
    usage = resp.usage
    cache_w = getattr(usage, "cache_creation_input_tokens", 0) or 0
    cache_r = getattr(usage, "cache_read_input_tokens", 0) or 0
    budget.record_call(usage.input_tokens, usage.output_tokens)
    if log:
        log.call("look_import.convert", model, usage.input_tokens, usage.output_tokens,
                 cache_creation_input_tokens=cache_w, cache_read_input_tokens=cache_r)
    cost = pricing.calculate_cost(model, usage.input_tokens, usage.output_tokens,
                                  cache_creation_input_tokens=cache_w, cache_read_input_tokens=cache_r)
    text = "".join(getattr(b, "text", "") for b in resp.content if getattr(b, "type", "text") == "text")
    return text, cost


def convert(observation, *, cartridge, slug, tenant, demo, client, model=None, progress=None, log=None):
    """(template text, {"attempts", "cost_usd", "model"}) or LookImportError
    with the validation errors of the last attempt."""
    from .budget import Budget, BudgetExceeded

    progress = progress or (lambda _s: None)
    model = model or tenant.model_for(MODEL_STAGE)
    contract_ = contract(cartridge, tenant, demo)
    schema = load_schema(cartridge, tenant)
    system = build_system_prompt(cartridge, contract_, schema)
    messages = [{"role": "user", "content": build_user_message(cartridge, slug, observation)}]
    budget = Budget(wall_s=600, tokens=120_000, calls=2)
    cost, errors, text = 0.0, [], ""
    for attempt in (1, 2):
        try:
            raw, call_cost = _call(client, model, system, messages, budget, log)
        except BudgetExceeded as e:
            raise LookImportError(f"import stopped by its budget: {e}", errors) from e
        cost += call_cost
        text = extract_template(raw)
        progress("validating")
        errors, review_html = validate_template(text, cartridge, slug, tenant, demo, contract_=contract_,
                                                schema=schema)
        if not errors:
            return text, review_html, {"attempts": attempt, "cost_usd": round(cost, 4), "model": model}
        if attempt == 1:
            progress("repairing")
            messages += [{"role": "assistant", "content": text},
                         {"role": "user", "content": "The harness refused this template:\n"
                          + "\n".join(f"- {e}" for e in errors[:MAX_ERRORS_SHOWN])
                          + "\n\nReturn the corrected complete template.html only."}]
    exc = LookImportError(f"import refused: the {cartridge} look failed validation after one repair "
                          f"({len(errors)} problem(s)); nothing was stored", errors)
    exc.cost_usd = round(cost, 4)
    raise exc


# ---------------------------------------------------------------------------
# Store + the whole import
# ---------------------------------------------------------------------------

def look_folder(tenant, cartridge, slug):
    return Path(tenant.looks_dir) / cartridge / slug


def store_look(tenant, cartridge, slug, *, template, library, observation, review_html):
    """Writes the four files into a hidden staging folder, then renames it
    to looks/<cartridge>/<slug> -- a half-written look is never visible. A
    name taken meanwhile is a collision."""
    root = Path(tenant.looks_dir) / cartridge
    root.mkdir(parents=True, exist_ok=True)
    final = root / slug
    staging = root / f".staging-{slug}-{secrets.token_hex(4)}"
    staging.mkdir()
    try:
        (staging / "template.html").write_text(template)
        (staging / "library.json").write_text(json.dumps(library, indent=2) + "\n")
        (staging / "observation.json").write_text(json.dumps(observation, indent=2) + "\n")
        (staging / "review.html").write_text(review_html)
        if final.exists():
            raise LookImportError(f"name collision: tenant {tenant.name} already has a {cartridge} look {slug!r}")
        os.rename(staging, final)
    finally:
        if staging.exists():
            shutil.rmtree(staging, ignore_errors=True)
    return final


def import_look(tenant, *, cartridge, name, source, title=None, client=None, model=None, dry_run=False,
                progress=None, log=None, transport=None, resolver=None):
    """The whole import. dry_run: the observation only (no model call,
    nothing written). Returns a JSON-ready result dict; raises
    LookImportError (nothing stored) on any refusal."""
    progress = progress or (lambda _s: None)
    slug = check_name(tenant, cartridge, name)
    title = " ".join(str(title or "").split())[:MAX_TITLE_CHARS] or slug.replace("-", " ").capitalize()
    progress("reading")
    src = load_source(source, transport=transport, resolver=resolver)
    progress("observing")
    observation = observe(src, transport=transport, resolver=resolver)
    sha = observation_sha(observation)
    result = {"slug": slug, "cartridge": cartridge, "title": title, "observation": observation,
              "observation_sha": sha, "summary": summarise(observation)}
    if dry_run:
        return result
    demo = load_demo(tenant, cartridge)
    client = client or make_client()
    today = datetime.date.today().isoformat()
    run_id = f"look-import-{slug}-{datetime.datetime.now().strftime('%Y%m%d-%H%M%S')}"
    try:
        budget_mod.reserve_spend(tenant, run_id=run_id, today_iso=today, log=log)
    except budget_mod.BudgetExceeded as e:
        raise LookImportError(f"import not started: {e}") from e
    progress("converting")
    cost = 0.0
    try:
        template, review_html, outcome = convert(observation, cartridge=cartridge, slug=slug, tenant=tenant,
                                                 demo=demo, client=client, model=model, progress=progress, log=log)
        cost = outcome["cost_usd"]
    except LookImportError as e:
        cost = getattr(e, "cost_usd", 0.0)
        raise
    finally:
        budget_mod.record_spend(tenant, run_id=run_id, cost=cost, today_iso=today, log=log)
    progress("storing")
    roles = [s["role"] for s in observation["sections"] if s["role"] not in ("nav", "footer")]
    library = {
        "name": slug,
        "title": title,
        "summary": result["summary"],
        "source": {"kind": src["kind"], "origin": src["origin"],
                   "imported_at": datetime.datetime.now().isoformat(timespec="seconds"), "observation_sha": sha},
        "cartridge": cartridge,
        "derived_from_sections": roles,
        "abtest_entry": f"{cartridge}:{slug}",
        "validation": {"result": "PASS", "attempts": outcome["attempts"]},
        "model": outcome["model"],
        "cost_usd": outcome["cost_usd"],
    }
    folder = store_look(tenant, cartridge, slug, template=template, library=library, observation=observation,
                        review_html=review_html)
    result.update({"library": library, "folder": str(folder), "attempts": outcome["attempts"],
                   "cost_usd": outcome["cost_usd"]})
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
    }


def queue_payload(tenant, *, name, cartridge, by, title=None, url=None, html_text=None, file_bytes=None,
                  filename=None):
    """The quick checks of a site request (name, collision, exactly one
    source, size, an HTML upload/paste, an http(s) URL) and the look_import
    job payload. An upload or paste is written to
    tenants/<t>/jobs/look-imports/<16 hex>.html for the worker, which
    deletes it when the job ends. Raises LookImportError."""
    from . import jobs

    slug = check_name(tenant, cartridge, name)
    url = (url or "").strip()
    given = [kind for kind, value in (("upload", file_bytes), ("url", url), ("paste", (html_text or "").strip()))
             if value]
    if len(given) != 1:
        raise LookImportError("give exactly one source: an .html file, a page URL, or pasted HTML")
    kind = given[0]
    payload = {"kind": kind, "slug": slug, "cartridge": cartridge, "by": by,
               "title": " ".join(str(title or name or "").split())[:MAX_TITLE_CHARS]}
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
