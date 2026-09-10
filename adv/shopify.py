"""`adv shopify-body`: rendered out/<run>/<cartridge>/index.html ->
shopify-body.html, the page body only, for pasting into a Shopify page's
body_html.

No Shopify API calls anywhere in this module -- publishing is a separate,
not-yet-built step (see cartridges/listicle/README's "Shopify traps"
section). This module only transforms a file already on disk.

Regex-based HTML transforms, matching the style already used elsewhere in
this codebase (adv/claims.py's strip_html_to_visible_text /
strip_leaked_claim_ids) rather than adding a new HTML-parsing dependency --
the input is always this harness's own Jinja output, not arbitrary HTML, so
a handful of targeted patterns are enough.
"""
import json
import re
from pathlib import Path

# ---------------------------------------------------------------------------
# Aurora full-bleed :has() rules, copied verbatim from the live, shipped
# reference/peak-listicle-lp/shopify-body.html. These neutralise the Aurora
# page template's .container padding, its per-section spacing variables, and
# the duplicate .page__title/.page__content chrome that would otherwise
# narrow and double-frame a full-bleed page -- see that file's own README
# ("Traps") for how they were derived. Never edit these to match a page's
# own class names; they must stay targeted at ".pk-lp" (every cartridge
# template's shopify-body-eligible markup keeps that class on its root
# element for exactly this reason).
# ---------------------------------------------------------------------------
AURORA_FULL_BLEED_RULES = """/* full-bleed: break out of the Aurora page container (copied from reference/peak-listicle-lp/shopify-body.html) */
.section:has(.pk-lp) .container{max-width:100%;width:100%;padding:0;--gsc-section-spacing-top:0px;--gsc-section-spacing-bottom:0px}
.section:has(.pk-lp) .page__title{display:none}
.section:has(.pk-lp) .page__content{margin:0;opacity:1;transform:none;animation:none}"""

_BODY_RE = re.compile(r"<body[^>]*>(.*)</body>", re.IGNORECASE | re.DOTALL)
_HEADER_FOOTER_NAV_TAG_RE = re.compile(r"</?(?:header|footer|nav)\b[^>]*>", re.IGNORECASE)
_STYLE_BLOCK_RE = re.compile(r"<style\b[^>]*>(.*?)</style>", re.IGNORECASE | re.DOTALL)
_SCRIPT_BLOCK_RE = re.compile(r"<script\b[^>]*>(?:(?!</script>).)*?</script>", re.IGNORECASE | re.DOTALL)
_IMG_TAG_RE = re.compile(r"<img\b[^>]*>", re.IGNORECASE)
_SRC_RE = re.compile(r'src="([^"]+)"')
_ALT_RE = re.compile(r'alt="([^"]*)"')
_INTERNAL_LINK_RE = re.compile(r'href="https?://(?:www\.)?peaksaunas\.com(/[^"]*)"')


def _slugify(text, fallback="asset"):
    slug = re.sub(r"[^a-z0-9]+", "-", (text or "").lower()).strip("-")
    return slug[:60] or fallback


def strip_document_chrome(html):
    """Rendered index.html -> the <body> content only, with <header>,
    <footer>, and <nav> TAGS removed (unwrapped -- their content, e.g. the
    "Advertisement" label, byline, disclosure paragraph, and Sources list,
    stays in place; only the structural tags themselves go, since Shopify's
    Aurora theme supplies its own page chrome and a bare <header>/<footer>
    inside body_html would just be inert, confusing markup). <html>/<head>
    never reach this function's output at all -- only what was inside
    <body> is extracted in the first place."""
    m = _BODY_RE.search(html)
    body = m.group(1) if m else html
    return _HEADER_FOOTER_NAV_TAG_RE.sub("", body)


def extract_page_css(body_html):
    """(remaining_html, joined_css) -- every <style> block's own CSS text
    (a cartridge template's own scoped rules; brand/base.css's full
    stylesheet lives in <head> and was never part of `body_html` to begin
    with), pulled out so it can be combined with AURORA_FULL_BLEED_RULES
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


def relativize_internal_links(html):
    """Every absolute https://peaksaunas.com/... href becomes a relative
    path (Shopify pages should link internally, not back out to the full
    domain) -- e.g. a product-specific CTA becomes href="/products/fuji".
    A CTA that resolves to an empty path (shouldn't happen; cta_url is a
    required page.json field) falls back to "/collections/all" -- the
    task's documented default for a page that isn't product-specific."""
    html = _INTERNAL_LINK_RE.sub(lambda m: f'href="{m.group(1)}"', html)
    return html.replace('href=""', 'href="/collections/all"')


def build_asset_manifest(html, cartridge_name):
    """Every relative assets/... image the shopify-body html references,
    with an intended Shopify Files CDN filename derived from its
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

    style_block = "<style>\n" + AURORA_FULL_BLEED_RULES
    if page_css:
        style_block += "\n\n" + page_css
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
