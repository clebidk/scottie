"""Cycle 92: local copies of the tenant's brand fonts, for previews and screenshots.

The review html, the Library thumbnails and the design gate's screenshots
must show the real brand faces on any host. A headless browser on a server
has none of them installed, and the page's own @font-face rules point at the
storefront CDN (display faces, brand/base.css) and at Google Fonts (body
faces, tenant.yaml brand.font_stylesheet). So the tenant keeps local copies:

    tenants/<t>/brand/fonts-display/   the display faces (files named like the
                                       basename of the base.css @font-face src)
    tenants/<t>/brand/fonts-body/      the body faces, written by
                                       `harness fonts fetch` with manifest.json
                                       and LICENCE.md

`brand_font_css` writes @font-face rules for those files (file:// urls for a
screenshot, a relative folder for `harness review`, or base64 data: urls for a
self-contained file). `inject` puts them in a <style> at the top of <head>,
BEFORE the page's own CSS: the page's own rules (CDN / Google urls) come later,
so where the network works the page loads as on the store; where it does not,
Chrome falls back to the earlier, local face of the same family (measured
2026-10-07 on Chrome 154: both orders render the local face when the CDN
fails). The storefront export never calls this module -- it keeps the CDN urls.
"""
import base64
import datetime
import json
import re
import shutil
import urllib.parse
import urllib.request
from pathlib import Path

DISPLAY_DIR = "fonts-display"
BODY_DIR = "fonts-body"
MANIFEST = "manifest.json"
LICENCE = "LICENCE.md"
FONT_SUFFIXES = (".woff2", ".woff", ".ttf", ".otf")
FORMATS = {".woff2": "woff2", ".woff": "woff", ".ttf": "truetype", ".otf": "opentype"}
MIME = {".woff2": "font/woff2", ".woff": "font/woff", ".ttf": "font/ttf", ".otf": "font/otf"}
STYLE_ATTR = "data-brand-fonts"
GOOGLE_CSS = "https://fonts.googleapis.com/css2"
# Google Fonts serves woff2 only to a browser that says it reads woff2.
GOOGLE_UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) "
             "Chrome/124.0 Safari/537.36")
FETCH_TIMEOUT_S = 30

_FONT_FACE_RE = re.compile(r"@font-face\s*\{([^}]*)\}", re.DOTALL)
_DESC_RE = re.compile(r"([a-z-]+)\s*:\s*([^;]+);?", re.IGNORECASE)
_URL_RE = re.compile(r"url\(\s*['\"]?([^'\")]+)['\"]?\s*\)")
_SUBSET_COMMENT_RE = re.compile(r"/\*\s*([a-z0-9-]+)\s*\*/\s*@font-face\s*\{([^}]*)\}", re.DOTALL | re.IGNORECASE)
_HEAD_RE = re.compile(r"<head\b[^>]*>", re.IGNORECASE)


class FontFetchError(Exception):
    """`harness fonts fetch` could not get a family; one line, exit 1."""


def _descriptors(block):
    out = {}
    for name, value in _DESC_RE.findall(block):
        out[name.strip().lower()] = value.strip()
    return out


def _unquote(value):
    return (value or "").strip().strip("'\"").strip()


def _display_faces(brand_dir):
    """base.css @font-face rules whose src file has a local copy in
    fonts-display/ (matched by file name)."""
    css_path = brand_dir / "base.css"
    folder = brand_dir / DISPLAY_DIR
    if not css_path.is_file() or not folder.is_dir():
        return []
    faces = []
    for block in _FONT_FACE_RE.findall(css_path.read_text()):
        desc = _descriptors(block)
        family = _unquote(desc.get("font-family"))
        urls = _URL_RE.findall(desc.get("src", ""))
        for url in urls:
            name = Path(urllib.parse.urlparse(url).path).name
            local = folder / name
            if family and local.is_file() and local.suffix.lower() in FONT_SUFFIXES:
                faces.append({"family": family, "weight": desc.get("font-weight", "400"),
                              "style": desc.get("font-style", "normal"), "path": local, "kind": "display"})
                break
    return faces


def _body_faces(brand_dir):
    """fonts-body/manifest.json faces whose file exists."""
    folder = brand_dir / BODY_DIR
    try:
        data = json.loads((folder / MANIFEST).read_text())
    except (OSError, ValueError):
        return []
    faces = []
    for face in (data.get("faces") or []) if isinstance(data, dict) else []:
        path = folder / str(face.get("file") or "")
        if not face.get("family") or not path.is_file() or path.parent != folder:
            continue
        faces.append({"family": face["family"], "weight": str(face.get("weight") or "400"),
                      "style": face.get("style") or "normal", "unicode_range": face.get("unicode_range"),
                      "path": path, "kind": "body"})
    return faces


def local_faces(tenant):
    """Every brand face with a local file: display faces first, then body."""
    brand_dir = getattr(tenant, "brand_dir", None)
    if not brand_dir:
        return []
    brand_dir = Path(brand_dir)
    return _display_faces(brand_dir) + _body_faces(brand_dir)


def _rule(face, url):
    ext = face["path"].suffix.lower()
    parts = [f'font-family:"{face["family"]}"', f"font-style:{face['style']}", f"font-weight:{face['weight']}",
             "font-display:swap", f'src:url("{url}") format("{FORMATS.get(ext, "woff2")}")']
    if face.get("unicode_range"):
        parts.append(f"unicode-range:{face['unicode_range']}")
    return "@font-face{" + ";".join(parts) + "}"


def brand_font_css(faces, mode="file", *, relative_dir=None):
    """@font-face rules for `faces`. mode "file": file:// urls (a screenshot
    in a local browser); "relative": url("<relative_dir>/<file name>") (the
    caller copies the files there); "inline": data: urls."""
    rules = []
    for face in faces:
        path = face["path"]
        if mode == "file":
            url = path.resolve().as_uri()
        elif mode == "relative":
            url = f"{relative_dir}/{path.name}"
        elif mode == "inline":
            url = f"data:{MIME.get(path.suffix.lower(), 'font/woff2')};base64," + \
                  base64.b64encode(path.read_bytes()).decode("ascii")
        else:
            raise ValueError(f"unknown font css mode {mode!r}")
        rules.append(_rule(face, url))
    return "\n".join(rules)


def inject(html_text, css):
    """`html_text` with a <style data-brand-fonts> holding `css` at the top
    of <head> (before the page's own stylesheets). Unchanged when css is
    empty or the page already carries the block."""
    if not css or STYLE_ATTR in html_text:
        return html_text
    block = f"<style {STYLE_ATTR}>\n{css}\n</style>"
    m = _HEAD_RE.search(html_text)
    if m:
        return html_text[: m.end()] + block + html_text[m.end():]
    return block + html_text


def copy_faces(faces, dest_dir):
    """Copy each face's file into dest_dir (once). Returns dest_dir."""
    dest_dir = Path(dest_dir)
    dest_dir.mkdir(parents=True, exist_ok=True)
    for face in faces:
        target = dest_dir / face["path"].name
        if not target.exists() or target.stat().st_size != face["path"].stat().st_size:
            shutil.copy2(face["path"], target)
    return dest_dir


def families(faces):
    """The distinct family names, in order."""
    seen = []
    for face in faces:
        if face["family"] not in seen:
            seen.append(face["family"])
    return seen


# ---------------------------------------------------------------------------
# harness fonts fetch
# ---------------------------------------------------------------------------

def google_css_url(tenant):
    """The Google Fonts css2 url the review page already loads (tenant.yaml
    brand.font_stylesheet), else one built from brand.fonts body/headings
    at weights 400, 500, 600."""
    url = tenant.get("brand.font_stylesheet")
    if url and "fonts.googleapis.com" in url:
        return url
    names = [n for n in (tenant.get("brand.fonts.body"), tenant.get("brand.fonts.headings")) if n]
    if not names:
        return None
    query = "&".join(f"family={urllib.parse.quote_plus(n)}:wght@400;500;600" for n in dict.fromkeys(names))
    return f"{GOOGLE_CSS}?{query}&display=swap"


def http_get(url, *, headers=None):
    request = urllib.request.Request(url, headers=headers or {})
    with urllib.request.urlopen(request, timeout=FETCH_TIMEOUT_S) as resp:  # noqa: S310 -- fixed https hosts
        return resp.read()


def parse_google_css(css_text):
    """[{family, weight, style, unicode_range, url, subset}] from a Google
    Fonts css2 response (each @font-face is preceded by a /* subset */
    comment)."""
    faces = []
    for subset, block in _SUBSET_COMMENT_RE.findall(css_text):
        desc = _descriptors(block)
        urls = _URL_RE.findall(desc.get("src", ""))
        if not urls:
            continue
        faces.append({"family": _unquote(desc.get("font-family")), "weight": desc.get("font-weight", "400"),
                      "style": desc.get("font-style", "normal"), "unicode_range": desc.get("unicode-range"),
                      "url": urls[0], "subset": subset.lower()})
    return faces


def _slug(text):
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")


def fetch_body_fonts(tenant, *, get=http_get, subsets=("latin", "latin-ext"), today=None):
    """Download the tenant's Google Fonts families into brand/fonts-body/
    (woff2), write manifest.json and LICENCE.md. Returns the manifest.
    Raises FontFetchError when there is nothing to fetch or a download
    fails. `get(url, headers=...)` is injectable for tests."""
    url = google_css_url(tenant)
    if not url:
        raise FontFetchError("tenant.yaml names no Google Fonts family (brand.font_stylesheet or brand.fonts.body)")
    try:
        css_text = get(url, headers={"User-Agent": GOOGLE_UA}).decode("utf-8")
    except Exception as e:  # noqa: BLE001 -- any network failure is one line
        raise FontFetchError(f"could not read {url}: {e}") from e
    faces = [f for f in parse_google_css(css_text) if f["subset"] in subsets]
    if not faces:
        raise FontFetchError(f"{url} returned no @font-face for subsets {list(subsets)}")
    folder = Path(tenant.brand_dir) / BODY_DIR
    folder.mkdir(parents=True, exist_ok=True)
    downloaded = {}
    entries = []
    for face in faces:
        ext = Path(urllib.parse.urlparse(face["url"]).path).suffix.lower() or ".woff2"
        if ext not in FONT_SUFFIXES:
            ext = ".woff2"
        name = f"{_slug(face['family'])}-{face['weight']}-{face['style']}-{face['subset']}{ext}"
        if face["url"] not in downloaded:
            try:
                data = get(face["url"], headers={"User-Agent": GOOGLE_UA})
            except Exception as e:  # noqa: BLE001
                raise FontFetchError(f"could not download {face['family']} {face['weight']}: {e}") from e
            (folder / name).write_bytes(data)
            downloaded[face["url"]] = name
        entries.append({"family": face["family"], "weight": face["weight"], "style": face["style"],
                        "subset": face["subset"], "unicode_range": face["unicode_range"],
                        "file": downloaded[face["url"]], "source_url": face["url"]})
    manifest = {"source": url, "fetched_at": (today or datetime.date.today().isoformat()),
                "licence": "SIL Open Font License 1.1 (Google Fonts)", "faces": entries}
    (folder / MANIFEST).write_text(json.dumps(manifest, indent=2) + "\n")
    names = sorted({e["family"] for e in entries})
    (folder / LICENCE).write_text(
        "# Body fonts (local copies)\n\n"
        f"Downloaded by `harness fonts fetch` from {url} on {manifest['fetched_at']}.\n\n"
        "These families are published on Google Fonts under the SIL Open Font License 1.1, which allows\n"
        "copying and redistribution with the software, but not selling the fonts by themselves.\n"
        "Licence text for each family: https://fonts.google.com/specimen/<Family>/license\n\n"
        + "".join(f"- {n}: https://fonts.google.com/specimen/{n.replace(' ', '+')}/license\n" for n in names)
        + "\nUsed only for previews and screenshots (review html, Library thumbnails, the design gate).\n"
        "The storefront export keeps the theme's own font urls.\n"
    )
    return manifest
