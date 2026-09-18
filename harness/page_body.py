"""`harness shopify-body`: a rendered index.html -> shopify-body.html, the page
body only, for pasting into a storefront page's body_html.

No storefront API calls anywhere in this module -- publishing is a separate,
not-yet-built step. This module only transforms a file already on disk.

Regex-based HTML transforms, matching the style already used elsewhere in this
codebase (claims.py's strip_html_to_visible_text / strip_leaked_claim_ids)
rather than adding a new HTML-parsing dependency -- the input is always this
harness's own Jinja output, not arbitrary HTML, so a handful of targeted
patterns are enough.
"""
import json
import re
from pathlib import Path

from . import tenant as tenant_mod

def full_bleed_css(tenant=None):
    """The tenant theme's own full-bleed rules (tenant.yaml's
    theme.full_bleed_css), pasted verbatim at the top of the output.

    A storefront theme wraps a page in its own container, padding, and title
    chrome, which would narrow and double-frame a full-bleed page. These rules
    neutralise that. They are theme-specific and derived by hand against the
    live theme, so they live in tenant data, never here -- and they stay
    targeted at the theme's configured root class (every cartridge template
    keeps that class on its root element for exactly this reason), never at a
    page's own class names."""
    tenant = tenant or tenant_mod.active()
    return (tenant.get("theme.full_bleed_css") or "").strip()


def internal_link_re(tenant=None):
    """Matches an absolute href pointing at the tenant's own site."""
    tenant = tenant or tenant_mod.active()
    host = tenant.get("site_host") or ""
    if not host:
        return re.compile(r"(?!x)x")
    return re.compile(r'href="https?://(?:www\.)?' + re.escape(host) + r'(/[^"]*)"')


def default_cta_url(tenant=None):
    tenant = tenant or tenant_mod.active()
    return tenant.get("default_cta_url") or "/"


_BODY_RE = re.compile(r"<body[^>]*>(.*)</body>", re.IGNORECASE | re.DOTALL)
# Cycle 40: captures the tag kind (opening `<header ...>`/closing `</header>`,
# same for footer/nav) plus the opening tag's own attribute text, so the
# replacement below can tell a classed cartridge element (e.g. listicle's
# <header class="lst-header">, base.html's <footer class="adv-footer">) from
# bare document chrome.
_CHROME_TAG_RE = re.compile(r"<(/?)(?:header|footer|nav)\b([^>]*)>", re.IGNORECASE)
_CLASS_ATTR_RE = re.compile(r"\bclass\s*=", re.IGNORECASE)
_STYLE_BLOCK_RE = re.compile(r"<style\b[^>]*>(.*?)</style>", re.IGNORECASE | re.DOTALL)
_SCRIPT_BLOCK_RE = re.compile(r"<script\b[^>]*>(?:(?!</script>).)*?</script>", re.IGNORECASE | re.DOTALL)
_IMG_TAG_RE = re.compile(r"<img\b[^>]*>", re.IGNORECASE)
_SRC_RE = re.compile(r'src="([^"]+)"')
_ALT_RE = re.compile(r'alt="([^"]*)"')
# Cycle 31: render.render_image_slot wraps a WebP-capable image in
# <picture><source type="image/webp" srcset="...">, <img srcset="...">.
_PICTURE_RE = re.compile(r"<picture>(.*?)</picture>", re.IGNORECASE | re.DOTALL)
_IMG_OR_SOURCE_TAG_RE = re.compile(r"<(?:img|source)\b[^>]*>", re.IGNORECASE)
_SRCSET_RE = re.compile(r'srcset="([^"]*)"')


def _slugify(text, fallback="asset"):
    slug = re.sub(r"[^a-z0-9]+", "-", (text or "").lower()).strip("-")
    return slug[:60] or fallback


def strip_document_chrome(html):
    """Rendered index.html -> the <body> content only, with every bare
    <header>/<footer>/<nav> TAG removed (unwrapped -- their content, e.g. the
    "Advertisement" label, byline, disclosure paragraph, and Sources list,
    stays in place; only the structural tags themselves go, since the
    storefront theme supplies its own page chrome and a bare <header>/<footer>
    inside body_html would just be inert, confusing markup). <html>/<head>
    never reach this function's output at all -- only what was inside
    <body> is extracted in the first place.

    A header/footer/nav tag that carries a `class` attribute is not document
    chrome -- it's a cartridge's own styled element (e.g.
    cartridges/listicle/template.html's <header class="lst-header">,
    harness/templates/base.html's <footer class="adv-footer">) whose class
    is what gives it its width/background/padding band. That tag becomes a
    <div ...> with its attributes carried over verbatim instead of being
    stripped, so the band survives into shopify-body.html. A small stack,
    keyed on whether each opener had a class, tracks openers in order so a
    closing tag becomes the matching </div> or is dropped to match its
    (classless) opener."""
    m = _BODY_RE.search(html)
    body = m.group(1) if m else html

    stack = []

    def _convert(match):
        closing, attrs = match.group(1), match.group(2)
        if closing:
            classed = stack.pop() if stack else False
            return "</div>" if classed else ""
        classed = bool(_CLASS_ATTR_RE.search(attrs))
        stack.append(classed)
        return f"<div{attrs}>" if classed else ""

    return _CHROME_TAG_RE.sub(_convert, body)


def extract_page_css(body_html):
    """(remaining_html, joined_css) -- every <style> block's own CSS text
    (a cartridge template's own scoped rules; the tenant's brand/base.css full
    stylesheet lives in <head> and was never part of `body_html` to begin
    with), pulled out so it can be combined with the theme's full-bleed rules
    and placed at the very top of shopify-body.html instead of wherever it
    happened to render inline."""
    css_parts = [c.strip() for c in _STYLE_BLOCK_RE.findall(body_html)]
    remaining = _STYLE_BLOCK_RE.sub("", body_html)
    return remaining, "\n\n".join(p for p in css_parts if p)


def extract_motion_script(body_html):
    """(remaining_html, script_html_or_None) -- the IntersectionObserver
    reveal-on-scroll script (present on any cartridge whose template
    includes one, e.g. listicle's fade-up motion), moved to the very end
    of the output instead of wherever base.html's <main> happened to place
    it relative to the disclosure/sources footer."""
    for m in _SCRIPT_BLOCK_RE.finditer(body_html):
        if "IntersectionObserver" in m.group(0):
            return body_html[: m.start()] + body_html[m.end() :], m.group(0)
    return body_html, None


def relativize_internal_links(html, tenant=None):
    """Every absolute href pointing at the tenant's own site becomes a relative
    path (a storefront page should link internally, not back out to the full
    domain) -- e.g. a product-specific CTA becomes href="/products/<slug>".
    A CTA that resolves to an empty path (shouldn't happen; cta_url is a
    required page.json field) falls back to the tenant's default_cta_url."""
    tenant = tenant or tenant_mod.active()
    html = internal_link_re(tenant).sub(lambda m: f'href="{m.group(1)}"', html)
    return html.replace('href=""', f'href="{default_cta_url(tenant)}"')


def _srcset_entries(value):
    """'assets/a-480.jpg 480w, assets/a-800.jpg 800w' ->
    [("assets/a-480.jpg", "480w"), ("assets/a-800.jpg", "800w")]."""
    entries = []
    for chunk in value.split(","):
        chunk = chunk.strip()
        if not chunk:
            continue
        path, _, descriptor = chunk.rpartition(" ")
        entries.append((path.strip(), descriptor.strip()) if path else (descriptor, ""))
    return entries


def _group_variant_paths(group_html):
    """[(local_path, width_descriptor_or_""), ...] for every assets/... path
    referenced by a src= or srcset= attribute anywhere in `group_html` --
    one <picture>...</picture> block's inner content, or a single
    standalone <img> tag -- in document order, de-duplicated within the
    group (an <img>'s own src is normally one of its srcset entries too,
    the fallback width; listed once)."""
    found, seen = [], set()
    for tag in (_IMG_OR_SOURCE_TAG_RE.findall(group_html) or [group_html]):
        src_m = _SRC_RE.search(tag)
        if src_m and src_m.group(1).startswith("assets/") and src_m.group(1) not in seen:
            seen.add(src_m.group(1))
            found.append((src_m.group(1), ""))
        srcset_m = _SRCSET_RE.search(tag)
        if srcset_m:
            for path, descriptor in _srcset_entries(srcset_m.group(1)):
                if path.startswith("assets/") and path not in seen:
                    seen.add(path)
                    found.append((path, descriptor))
    return found


def build_asset_manifest(html, cartridge_name):
    """Every relative assets/... file the shopify-body html references --
    the plain src, every srcset width variant (JPEG and, inside a
    render_image_slot <picture>, WebP), each listed once -- with an
    intended storefront CDN filename derived from its renderer-generated
    alt text (already a meaningful, human-readable description -- see
    render.asset_alt) plus its width descriptor (kept distinct: a 480w and
    a 1600w variant of the same image must not collide on one filename) so
    a later publish step has a real name to upload every variant under
    instead of the bare local filename. See docs/IMAGES.md for how
    harness/publishers/shopify.py is expected to consume this: it should
    upload each local_path under its cdn_filename, then rewrite that exact
    string wherever it appears in a src or srcset attribute (a `src` gets
    swapped outright; a `srcset` entry keeps its own width descriptor,
    e.g. "<cdn-url> 480w")."""
    manifest = []
    seen_paths = set()
    idx = 0

    groups = [m.group(1) for m in _PICTURE_RE.finditer(html)]
    groups += _IMG_TAG_RE.findall(_PICTURE_RE.sub("", html))

    for group in groups:
        variants = [(p, d) for p, d in _group_variant_paths(group) if p not in seen_paths]
        if not variants:
            continue
        idx += 1
        alt_m = _ALT_RE.search(group)
        alt = alt_m.group(1) if alt_m else ""
        base_slug = _slugify(alt, fallback=Path(variants[0][0]).stem)
        for local_path, descriptor in variants:
            seen_paths.add(local_path)
            ext = Path(local_path).suffix or ".jpg"
            if not descriptor:
                # The bare src= of an <img> that also carries a matching
                # srcset entry (the common case -- render_image_slot's
                # fallback img is always one of its own srcset widths) is
                # seen via src first, with no "NNNw" descriptor of its own;
                # fall back to the width already embedded in the filename
                # itself (generate_image_variants names every file
                # "<id>-<width>.<ext>") so every variant's cdn_filename
                # carries a width suffix consistently, not just the ones
                # first encountered inside a srcset attribute.
                width_m = re.search(r"-(\d+)\.\w+$", local_path)
                descriptor = f"{width_m.group(1)}w" if width_m else ""
            suffix = f"-{descriptor}" if descriptor else ""
            cdn_filename = f"pk-{cartridge_name}-{idx:02d}-{base_slug}{suffix}{ext}"
            manifest.append({"local_path": local_path, "alt": alt, "cdn_filename": cdn_filename})
    return manifest


def build_shopify_body(cartridge_dir):
    """out/<run>/<cartridge>/index.html -> (shopify_body_html, assets_manifest).
    Pure transform, no file I/O -- see write_shopify_body for the CLI-facing
    version that reads index.html and writes the two output files."""
    cartridge_dir = Path(cartridge_dir)
    cartridge_name = cartridge_dir.name
    html = (cartridge_dir / "index.html").read_text()

    body = strip_document_chrome(html)
    body, page_css = extract_page_css(body)
    body, motion_script = extract_motion_script(body)
    body = relativize_internal_links(body).strip()

    theme_css = full_bleed_css()
    style_block = "<style>\n" + theme_css
    if page_css:
        style_block += ("\n\n" if theme_css else "") + page_css
    style_block += "\n</style>"

    parts = [style_block, body]
    if motion_script:
        parts.append(motion_script)
    shopify_body_html = "\n\n".join(parts) + "\n"

    assets_manifest = build_asset_manifest(shopify_body_html, cartridge_name)
    return shopify_body_html, assets_manifest


def write_shopify_body(cartridge_dir):
    """Writes shopify-body.html and shopify-body.assets.json next to
    index.html. Returns (shopify_body_path, assets_manifest_path)."""
    cartridge_dir = Path(cartridge_dir)
    shopify_body_html, assets_manifest = build_shopify_body(cartridge_dir)

    shopify_body_path = cartridge_dir / "shopify-body.html"
    shopify_body_path.write_text(shopify_body_html)

    assets_manifest_path = cartridge_dir / "shopify-body.assets.json"
    assets_manifest_path.write_text(json.dumps(assets_manifest, indent=2) + "\n")

    return shopify_body_path, assets_manifest_path
