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

from . import css_scope
from . import tenant as tenant_mod

_ROOT_BLOCK_RE = re.compile(r":root\s*\{([^}]*)\}", re.DOTALL)
_FONT_FACE_RE = re.compile(r"@font-face\s*\{[^}]*\}", re.DOTALL)


def token_css(tenant=None):
    """Cycle 48: the design tokens a rendered page gets from its document
    HEAD -- harness/structure.css's `:root{--adv-*}` defaults and the tenant's
    brand/base.css `:root{--ps-*}` brand values -- re-scoped to `.adv-wrap`,
    the export's root element, so they travel INSIDE the body `<style>` that
    a storefront keeps. Without this every `var(--ps-accent, var(--adv-accent))`
    in a cartridge resolves to nothing on the storefront: the CTA buttons on
    ten live pages rendered with a transparent background and white text
    (2026-09-19). Order matters: harness defaults first, brand values after,
    so a brand token overrides a default of the same name."""
    tenant = tenant or tenant_mod.active()
    sources = [Path(__file__).resolve().parent / "structure.css"]
    brand_dir = getattr(tenant, "brand_dir", None)
    if brand_dir:
        sources.append(Path(brand_dir) / "base.css")
    blocks = []
    for path in sources:
        if not path.exists():
            continue
        for match in _ROOT_BLOCK_RE.finditer(path.read_text()):
            declarations = " ".join(line.strip() for line in match.group(1).strip().splitlines() if line.strip())
            if declarations:
                blocks.append(".adv-wrap{" + declarations + "}")
    return "\n".join(blocks)


def font_face_css(tenant=None):
    """Cycle 52: the tenant's own `@font-face` rules, taken from
    brand/base.css and carried into the export.

    Same reason token_css exists. base.css is a document-HEAD stylesheet and
    a storefront keeps only what was inside <body>, so a brand's self-hosted
    webfont was simply absent from every published page and the fallback
    stack rendered instead. The rules are emitted verbatim -- an @font-face
    is not scoped to a selector, so unlike the :root tokens there is nothing
    to re-scope.

    Their `url(...)` values are still the repo-relative brand/fonts/... paths
    at this point; harness/publishers/shopify.py rewrites them to the
    storefront CDN at publish time (and `harness rerender` re-applies the
    cached URLs), because a relative path would 404 on a storefront."""
    tenant = tenant or tenant_mod.active()
    brand_dir = getattr(tenant, "brand_dir", None)
    if not brand_dir:
        return ""
    path = Path(brand_dir) / "base.css"
    if not path.exists():
        return ""
    return "\n".join(_FONT_FACE_RE.findall(path.read_text()))


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
    # Cycle 52: start the search after </head>. The tenant's own stylesheet
    # is inlined into the document head, and a stylesheet that so much as
    # mentions a body tag in a comment would otherwise be matched as the
    # start of the document body -- the whole head stylesheet then landed in
    # the export as raw text outside its <style>. Found rendering the
    # rebranded pages, not by reading the regex.
    head_end = html.lower().find("</head>")
    m = _BODY_RE.search(html, head_end + 1 if head_end != -1 else 0)
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


def extract_head_css(html):
    """Cycle 59: the CSS of every <style> block in the document HEAD --
    harness/structure.css and the tenant's brand/base.css, exactly as the
    review page was styled by them. A storefront keeps only the body, so
    without this the export lost every element and structure rule those
    sheets give (heading fonts and sizes, link and table styling, the
    .adv-footer band) and the theme's own rules filled the gap."""
    head_end = html.lower().find("</head>")
    if head_end == -1:
        return ""
    parts = [c.strip() for c in _STYLE_BLOCK_RE.findall(html[:head_end])]
    return "\n\n".join(p for p in parts if p)


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
    # Cycle 59: inline style attributes lose their rem too (the theme's
    # root font size must not reach any length of ours).
    body = css_scope.rem_to_px_in_style_attrs(body)

    theme_css = full_bleed_css()
    tokens = token_css()
    fonts = font_face_css()
    # Cycle 59: the storefront-fit layers (harness/css_scope.py). The fit
    # rules and the isolation reset are fixed text; the review page's head
    # sheets and the cartridge's own CSS are scoped to the wrapper with two
    # extra classes of specificity, head first (the order they had on the
    # review page). :root and @font-face are dropped from the head copy --
    # token_css and font_face_css above already carry them.
    roots = css_scope.root_classes_of(body)
    head_css = css_scope.scope_css(extract_head_css(html), roots, drop=(":root", "@font-face"))
    page_css = css_scope.scope_css(page_css, roots) if page_css else ""
    style_block = "<style>\n" + theme_css
    # theme rules first (tests and operators expect the export to open with
    # them), then the tenant's @font-face rules, then the re-scoped head
    # tokens, then the storefront fit + isolation reset, then the review
    # page's head stylesheets, then the cartridge's own CSS.
    for chunk in (fonts, tokens, css_scope.STOREFRONT_FIT_CSS, css_scope.ISOLATION_RESET_CSS, head_css, page_css):
        if chunk:
            style_block += ("\n\n" if style_block.rstrip("\n") != "<style>" else "") + chunk
    style_block += "\n</style>"
    # every rem in the export's CSS -> px at 16px (the theme sets html to 10px)
    style_block = css_scope.rem_to_px(style_block)

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
