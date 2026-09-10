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
_HEADER_FOOTER_NAV_TAG_RE = re.compile(r"</?(?:header|footer|nav)\b[^>]*>", re.IGNORECASE)
_STYLE_BLOCK_RE = re.compile(r"<style\b[^>]*>(.*?)</style>", re.IGNORECASE | re.DOTALL)
_SCRIPT_BLOCK_RE = re.compile(r"<script\b[^>]*>(?:(?!</script>).)*?</script>", re.IGNORECASE | re.DOTALL)
_IMG_TAG_RE = re.compile(r"<img\b[^>]*>", re.IGNORECASE)
_SRC_RE = re.compile(r'src="([^"]+)"')
_ALT_RE = re.compile(r'alt="([^"]*)"')


def _slugify(text, fallback="asset"):
    slug = re.sub(r"[^a-z0-9]+", "-", (text or "").lower()).strip("-")
    return slug[:60] or fallback


def strip_document_chrome(html):
    """Rendered index.html -> the <body> content only, with <header>,
    <footer>, and <nav> TAGS removed (unwrapped -- their content, e.g. the
    "Advertisement" label, byline, disclosure paragraph, and Sources list,
    stays in place; only the structural tags themselves go, since the
    storefront theme supplies its own page chrome and a bare <header>/<footer>
    inside body_html would just be inert, confusing markup). <html>/<head>
    never reach this function's output at all -- only what was inside
    <body> is extracted in the first place."""
    m = _BODY_RE.search(html)
    body = m.group(1) if m else html
    return _HEADER_FOOTER_NAV_TAG_RE.sub("", body)


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


def build_asset_manifest(html, cartridge_name):
    """Every relative assets/... image the shopify-body html references,
    with an intended storefront CDN filename derived from its
    renderer-generated alt text (already a meaningful, human-readable
    description -- see render.asset_alt) so a later publish step has a
    real name to upload under instead of the bare local asset id."""
    manifest = []
    seen = set()
    idx = 0
    for tag in _IMG_TAG_RE.findall(html):
        src_m = _SRC_RE.search(tag)
        if not src_m or not src_m.group(1).startswith("assets/"):
            continue
        local_path = src_m.group(1)
        if local_path in seen:
            continue
        seen.add(local_path)
        idx += 1
        alt_m = _ALT_RE.search(tag)
        alt = alt_m.group(1) if alt_m else ""
        ext = Path(local_path).suffix or ".jpg"
        cdn_filename = f"pk-{cartridge_name}-{idx:02d}-{_slugify(alt, fallback=Path(local_path).stem)}{ext}"
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
