"""Jinja2 rendering: page.json + facts_pack -> out/<run>/<cartridge>/index.html.

Injects byline, dates, the optional disclosure label (tenant disclosure_label), the disclosure paragraph,
a Sources list (from claim_ids used), and per-cartridge JSON-LD. The model
never writes any of that -- it's all added here.
"""
import copy
import functools
import io
import json
import math
import re
import urllib.request
from pathlib import Path
from urllib.parse import urlparse

import jinja2
from markupsafe import Markup, escape
from PIL import Image

from . import blocks
from . import ground as ground_mod
from . import ingest
from . import comparison as comparison_mod
from . import listicle as listicle_mod
from . import looks as looks_mod
from . import pagechecks
from . import quiz as quiz_mod
from . import pdp as pdp_mod
from . import quote_fidelity
from . import tenant as tenant_mod
from .textutil import safe_filename, walk_page
from .claims import (
    ClaimsGateFailure,
    collect_claim_ids,
    find_forbidden_visible_text,
    find_leaked_claim_ids_visible_text,
    find_missing_attribution,
    strip_leaked_claim_ids,
)

FALLBACK_BYLINE = """<p class="adv-byline-author">{author_line}</p>
<p class="adv-byline-contributor">{contributor_line}</p>
<p class="adv-byline-reviewer">{reviewer_line}</p>
<p class="adv-byline-dates">Published {published} &middot; Updated {updated}</p>"""

# Cycle 43: a tenant's byline.html (see load_byline_html) is free to carry a
# bottom-of-page "About the author" block after the compact top-of-page
# byline -- both rendered as one string today. Listicle is the one cartridge
# that needs them apart (the compact line stays in the header; the About
# section moves down next to the disclosure/sources footer, instead of both
# sitting above the fold) -- see split_byline_html and its one call site in
# render_page. A tenant's byline.html marks the split point with this literal
# HTML comment; one that doesn't (the _template tenant, and FALLBACK_BYLINE
# above, both carry no About section at all) simply has no about_html half.
ABOUT_AUTHOR_MARKER = "<!-- about-author -->"


def split_byline_html(byline_html):
    """(top_html, about_html): byline_html split at the tenant's own
    ABOUT_AUTHOR_MARKER comment. about_html is "" -- and top_html is
    byline_html unchanged -- when the marker isn't present, so a tenant
    whose byline.html (or the FALLBACK_BYLINE default) carries no About
    section is unaffected."""
    if ABOUT_AUTHOR_MARKER not in byline_html:
        return byline_html, ""
    top, _, about = byline_html.partition(ABOUT_AUTHOR_MARKER)
    return top, about


def byline_names(tenant=None):
    """(author, contributor, reviewer) as the tenant's own byline.html expects
    them.

    A tenant's byline.html template appends the author's title itself after
    {{ author }}, so the author slot is the bare name. Cycle 19: contributor
    is the editorial team, not a person, and (like author) has no
    template-supplied title of its own; reviewer is the named person who
    checks specifications and sources, and carries its own title the same
    way contributor's title used to before Cycle 19."""
    tenant = tenant or tenant_mod.active()
    author = tenant.author("author")
    contributor = tenant.author("contributor")
    reviewer = tenant.author("reviewer")
    author_name = author.get("name", "")
    contributor_name = contributor.get("name", "")
    if contributor.get("title"):
        contributor_name = f"{contributor_name}, {contributor['title']}"
    reviewer_name = reviewer.get("name", "")
    if reviewer.get("title"):
        reviewer_name = f"{reviewer_name}, {reviewer['title']}"
    return author_name, contributor_name, reviewer_name


# Fix cycle 23: a tenant's brand/base.css used to REPLACE harness/fallback.css
# outright, so a tenant stylesheet that never defines .adv-cta/.adv-sticky-cta/
# .adv-financing/image sizing (a real tenant's own base.css can legitimately
# style only its site's own theme classes, never touching the harness's adv-*
# ones at all) left every cartridge template's structural classes with no CSS
# rule at all. harness/structure.css (renamed from fallback.css) now always
# loads first, as the layout/component/responsive layer every cartridge
# template's classes are defined against; the tenant's own base.css, if any,
# loads second as an override layer (tokens, fonts, colors) on top of it.
def load_structure_css():
    return (Path(__file__).parent / "structure.css").read_text()


def load_tenant_css(brand_dir, log=None):
    css_path = Path(brand_dir) / "base.css"
    if css_path.exists() and css_path.read_text().strip():
        return css_path.read_text()
    if log:
        log.event("render", "tenant brand/base.css not found or empty; using harness/structure.css only")
    return ""


def load_byline_html(brand_dir, published, updated, log=None, tenant=None):
    tenant = tenant or tenant_mod.active()
    byline_path = Path(brand_dir) / "byline.html"
    author_name, contributor_name, reviewer_name = byline_names(tenant)
    context = {
        "author": author_name,
        "contributor": contributor_name,
        "reviewer": reviewer_name,
        "published": published,
        "updated": updated,
    }
    if byline_path.exists():
        raw = byline_path.read_text()
        try:
            # Cycle 22 finding R37: this was a bare jinja2.Template, whose
            # autoescape default is off -- the one unescaped render in the
            # harness, and its output is injected into the page with `| safe`.
            # Autoescape only ever escapes the SUBSTITUTED values (the byline
            # names and the two dates); the template's own markup is untouched,
            # so both tenants' byline.html render byte-identically to before --
            # tests/test_path_safety.py asserts exactly that.
            env = jinja2.Environment(autoescape=True)
            return env.from_string(raw).render(**context)
        except jinja2.TemplateError as e:
            if log:
                log.event("render", f"tenant brand/byline.html failed to render ({e}); using raw content")
            return raw
    if log:
        log.event("render", "tenant brand/byline.html not found; using fallback byline markup")
    author = tenant.author("author")
    contributor = tenant.author("contributor")
    reviewer = tenant.author("reviewer")
    return FALLBACK_BYLINE.format(
        author_line=(tenant.authors.get("byline_author_template") or "By {author_name}").format(
            author_name=context["author"], author_title=author.get("title", ""), tenant_name=tenant.display_name
        ),
        contributor_line=(
            tenant.authors.get("byline_contributor_template") or "{contributor_name}"
        ).format(contributor_name=contributor.get("name", ""), contributor_title=contributor.get("title", "")),
        reviewer_line=(
            tenant.authors.get("byline_reviewer_template") or "Reviewed by {reviewer_name}"
        ).format(reviewer_name=reviewer.get("name", ""), reviewer_title=reviewer.get("title", "")),
        published=published,
        updated=updated,
    )


# Fix cycle 2 item 9: the Sources list must show a short, human-readable
# link label -- never the raw URL as visible text (that's exactly how the
# a product URL's own handle was leaking into visible copy).
#
# Fix cycle 3 item 1: dedupe to one line per distinct source URL (several
# claims -- specs, price -- share the same product-page URL) with a specific
# label, derived from the URL path (or a claim's own "label" field, when a
# future claim needs one the path can't describe).
_URL_IN_TEXT_RE = re.compile(r"https?://\S+")


def resolve_public_url(source, *, fallback_url=None):
    """A claim's "source" field is sometimes a single URL, sometimes a
    compound string of internal references and a public URL joined with
    "; " (e.g. a gbrain-seeded claim: "gbrain:policy/x; https://..."), and
    occasionally purely internal with no public URL at all. Returns the
    first http(s) URL found in `source`, else `fallback_url` (the product
    page, for a product-benefit claim with no public source of its own),
    else None -- never the raw compound string as an href."""
    match = _URL_IN_TEXT_RE.search(source or "")
    if match:
        return match.group(0).rstrip(";,")
    return fallback_url


def source_label(url, *, product_name=None, explicit_label=None, tenant=None):
    """A short, human-readable label for `url` -- never the raw URL itself.
    An explicit "label" field on the claim always wins; otherwise the label is
    derived from the URL's host and path, using the tenant's own site host,
    label prefix, and source_path_labels map."""
    if explicit_label:
        return explicit_label
    tenant = tenant or tenant_mod.active()
    prefix = tenant.get("source_label_prefix") or tenant.display_name
    if not url or not url.startswith("http"):
        return prefix
    parsed = urlparse(url)
    host = parsed.netloc.replace("www.", "")
    review_host = urlparse(tenant.get("reviews.store_url") or "").netloc.replace("www.", "")
    if review_host and host == review_host:
        return tenant.format(
            "review_source_label", platform_name=tenant.get("reviews.platform_name") or host
        ) or host
    if host != (tenant.get("site_host") or ""):
        return host
    path = parsed.path.rstrip("/")
    if path.startswith("/products/"):
        product_name = tenant.display_product_name(product_name)
        return tenant.format("product_page_label", product_name=product_name or "product") or f"{prefix} – product page"
    path_labels = tenant.get("source_path_labels") or {}
    if path in path_labels:
        return f"{prefix} – {path_labels[path]}"
    fallback = path.rsplit("/", 1)[-1].replace("-", " ").title()
    return f"{prefix} – {fallback}" if fallback else prefix


def build_sources_list(used_claim_ids, verified_by_id, product_name=None, product_url=None, tenant=None,
                       fallback_url_by_claim=None, product_name_by_url=None):
    """One entry per distinct public source URL referenced by
    `used_claim_ids` (fix cycle 3 item 1) -- claim texts are never shown on
    the page, only in REVIEW.md; the page gets a label (link text) and the
    URL (href only). A claim's "source" field is resolved to a real http(s)
    URL first (resolve_public_url) -- a claim with no public URL of its own
    (some of the gbrain-seeded allowlist claims are internal-only) falls
    back to the product page rather than a broken/internal href, and is
    skipped entirely if there's no product page to fall back to either.

    Cycle 56: a page that cites several products' facts (the comparison
    table) passes `fallback_url_by_claim` -- each claim's own product page,
    so an internal-only source falls back to the model it is about, not the
    run's product -- and `product_name_by_url`, so each product page is
    labelled with its own name. Both default to the single-product
    behavior above."""
    fallback_url_by_claim = fallback_url_by_claim or {}
    product_name_by_url = product_name_by_url or {}
    seen = {}
    for cid in sorted(used_claim_ids):
        claim = verified_by_id.get(cid)
        if not claim:
            continue
        url = resolve_public_url(claim.get("source", ""), fallback_url=fallback_url_by_claim.get(cid, product_url))
        if not url:
            continue
        if url not in seen:
            seen[url] = source_label(
                url, product_name=product_name_by_url.get(url, product_name),
                explicit_label=claim.get("label"), tenant=tenant,
            )
    return [{"url": url, "label": label} for url, label in seen.items()]


def build_json_ld(cartridge_name, page, facts_pack, published, updated, tenant=None):
    tenant = tenant or tenant_mod.active()
    author_name = (tenant.author("author") or {}).get("name", "")
    publisher_name = tenant.display_name
    product = facts_pack["product"]
    if cartridge_name == "article":
        return {
            "@context": "https://schema.org",
            "@type": "Article",
            "headline": page.get("headline", ""),
            "description": page.get("dek", ""),
            "author": {"@type": "Person", "name": author_name},
            "datePublished": published,
            "dateModified": updated,
            "publisher": {"@type": "Organization", "name": publisher_name},
        }
    if cartridge_name == "product-page":
        return {
            "@context": "https://schema.org",
            "@type": "Product",
            "name": tenant.display_product_name(product.get("short_name") or product["name"]),
            "url": product["url"],
            "image": product.get("image_urls", []),
            "offers": {
                "@type": "Offer",
                "price": product.get("price"),
                "priceCurrency": "USD",
                "url": product["url"],
                "availability": "https://schema.org/InStock",
            },
        }
    if cartridge_name == "listicle":
        return {
            "@context": "https://schema.org",
            "@type": "ItemList",
            "name": page.get("headline", ""),
            "description": page.get("dek", ""),
            "itemListElement": [
                {
                    "@type": "ListItem",
                    "position": item.get("number"),
                    "name": item.get("heading", ""),
                }
                for item in page.get("reasons", [])
            ],
        }
    if cartridge_name == "longform":
        faq_items = page.get("faq", {}).get("questions", []) if isinstance(page.get("faq"), dict) else page.get("faq", [])
        mains = []
        for item in faq_items:
            q = item.get("question") if isinstance(item, dict) else None
            a = item.get("text") if isinstance(item, dict) else None
            if q and a:
                mains.append({"@type": "Question", "name": q, "acceptedAnswer": {"@type": "Answer", "text": a}})
        return {"@context": "https://schema.org", "@type": "FAQPage", "mainEntity": mains}
    if cartridge_name in ("comparison", "quiz"):
        # Kimi long-run phase 6: same FAQPage shape as longform, but the
        # comparison schema's faq items are {question, answer} (the
        # faq-accordion block's binding names).
        faq_items = page.get("faq", {}).get("questions", []) if isinstance(page.get("faq"), dict) else []
        mains = []
        for item in faq_items:
            q = item.get("question") if isinstance(item, dict) else None
            a = item.get("answer") if isinstance(item, dict) else None
            if q and a:
                mains.append({"@type": "Question", "name": q, "acceptedAnswer": {"@type": "Answer", "text": a}})
        return {"@context": "https://schema.org", "@type": "FAQPage", "mainEntity": mains}
    return {}


# Fix cycle 3 item 5: the writer may only pick an asset id -- the renderer
# derives alt text from what the asset actually is (its brand/assets.json
# "kind", plus the product name), never from a title (Drive titles are raw
# camera filenames like "_MG_3988.JPG", not descriptions) or a model-invented
# string that could describe something the image doesn't show.
_ASSET_KIND_ALT_SUFFIXES = {
    "image": "product photo",
    "render": "product photo",
    "lifestyle": "lifestyle photo",
    "interior": "interior",
    "installation": "installation photo",
    # Fix cycle 15 item 2: brand/assets-listicle-pack.json's own kind values
    # (distinct from brand/assets.json's above).
    "photo_product": "product photo",
    "photo_install": "installation photo",
    "still_video": "photo",
    "ai_render": "product photo",
}


def asset_alt(asset, product_short_name, tenant=None):
    tenant = tenant or tenant_mod.active()
    # Cycle 52: alt text is copy, so the storefront title prefix goes.
    product_short_name = tenant.display_product_name(product_short_name)
    product_short_name = product_short_name or tenant.get("asset_alt_fallback") or tenant.display_name
    suffix = _ASSET_KIND_ALT_SUFFIXES.get(asset.get("kind"), "photo")
    alt = f"{product_short_name} – {suffix}"
    # Fix cycle 15 item 2: an AI-composite render (brand/assets-listicle-pack
    # .json's ai_generated: true rows) must never read as a real photo of a
    # customer's home -- the alt text always says so, up front.
    if asset.get("ai_generated"):
        alt = f"Rendering: {alt}"
    return alt


def collect_asset_ids(node):
    """Every "asset_id" referenced anywhere in page.json."""
    ids = set()
    for _path, n in walk_page(node):
        if isinstance(n, dict) and n.get("asset_id"):
            ids.add(n["asset_id"])
    return ids


def _looks_like_html(data):
    head = data[:512].lstrip().lower()
    return head.startswith(b"<!doctype html") or b"<html" in data[:2000].lower()


# Cycle 33: cap remote asset downloads so a misbehaving CDN can't fill
# memory. Comfortably above ASSET_MAX_LONG_EDGE re-encodes; well below a
# multi-hundred-MB runaway response.
HTTP_FETCH_MAX_BYTES = 25 * 1024 * 1024


def http_fetch_bytes(url):
    """Fetch `url` as raw bytes. Cycle 33: only http(s) schemes are allowed
    (blocks file:// and other local schemes), and the response is capped at
    HTTP_FETCH_MAX_BYTES."""
    scheme = urlparse(url).scheme.lower()
    if scheme not in ("http", "https"):
        raise ValueError(f"http_fetch_bytes only allows http(s) URLs, got scheme={scheme!r}")
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
        },
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        chunks = []
        total = 0
        while True:
            chunk = resp.read(64 * 1024)
            if not chunk:
                break
            total += len(chunk)
            if total > HTTP_FETCH_MAX_BYTES:
                raise ValueError(
                    f"http_fetch_bytes: response exceeded {HTTP_FETCH_MAX_BYTES} bytes"
                )
            chunks.append(chunk)
        return b"".join(chunks)


# Fix cycle 15 item 1: a Drive original inlined at full size (one sample
# review file was 46 MB) blows well past anything worth emailing. Every
# asset actually downloaded into out/<run>/<cartridge>/assets/ is downscaled
# to this long edge and re-encoded before it ever reaches disk.
ASSET_MAX_LONG_EDGE = 1600
ASSET_JPEG_QUALITY = 82
ASSET_PNG_MAX_BYTES = int(1.5 * 1024 * 1024)


def resize_asset_bytes(data, ext, *, log=None, asset_id=None):
    """Downscale `data` (raw image bytes) to a max ASSET_MAX_LONG_EDGE-px
    long edge, then re-encode: JPEG at ASSET_JPEG_QUALITY, except a PNG stays
    a PNG unless the re-encoded PNG would be bigger than ASSET_PNG_MAX_BYTES,
    in which case it's converted to JPEG too. Returns (new_bytes, new_ext).
    Logs original and final byte counts. Anything Pillow can't open (not an
    image, or a corrupt/partial download) is returned unchanged -- resizing
    is a size optimization, not a correctness gate; a bad download is already
    handled by the HTML-sniffing check in download_asset."""
    original_bytes = len(data)
    try:
        img = Image.open(io.BytesIO(data))
        img.load()
    except Exception as e:
        if log:
            log.event("render", f"asset {asset_id} could not be opened as an image, skipping resize ({e})")
        return data, ext

    is_png = (img.format or "").upper() == "PNG"

    if img.width > ASSET_MAX_LONG_EDGE or img.height > ASSET_MAX_LONG_EDGE:
        img.thumbnail((ASSET_MAX_LONG_EDGE, ASSET_MAX_LONG_EDGE), Image.LANCZOS)

    new_bytes, new_ext = None, None
    if is_png:
        buf = io.BytesIO()
        img.save(buf, format="PNG", optimize=True)
        png_bytes = buf.getvalue()
        if len(png_bytes) <= ASSET_PNG_MAX_BYTES:
            new_bytes, new_ext = png_bytes, ".png"

    if new_bytes is None:
        rgb_img = img.convert("RGB") if img.mode in ("RGBA", "P", "LA") else img
        buf = io.BytesIO()
        rgb_img.save(buf, format="JPEG", quality=ASSET_JPEG_QUALITY)
        new_bytes, new_ext = buf.getvalue(), ".jpg"

    if log:
        log.event(
            "render",
            f"asset {asset_id} downscaled: {original_bytes} -> {len(new_bytes)} bytes ({ext} -> {new_ext})",
        )
    return new_bytes, new_ext


def _image_dimensions(data):
    """(width, height) of `data` if Pillow can open it as an image, else
    (None, None) -- mirrors resize_asset_bytes's own "not an image" handling
    without changing that function's existing (bytes, ext) return shape."""
    try:
        img = Image.open(io.BytesIO(data))
        img.load()
        return img.width, img.height
    except Exception:
        return None, None


# Cycle 31 (docs/IMAGES-AUDIT-2026-09-14.md problems 8/9): the widths every
# content image gets a variant generated at, so templates can emit a real
# srcset instead of shipping the same 1600px-long-edge file to every
# viewport. A width larger than the source image's own width is skipped
# (never upscaled) -- see generate_image_variants.
IMAGE_SRCSET_WIDTHS = (480, 800, 1200, 1600)
# The width variant used as the plain (no-srcset-support, and
# harness/review.py's inliner) fallback src -- deliberately not the
# largest, per the cycle 31 brief ("inline only the largest or the 1200
# variant to keep files small").
IMAGE_SRCSET_FALLBACK_WIDTH = 1200
IMAGE_SIZES_HERO = "(max-width: 768px) 100vw, 700px"
IMAGE_SIZES_DEFAULT = "(max-width: 600px) 100vw, (max-width: 1000px) 80vw, 800px"


@functools.cache
def _webp_supported():
    """Whether this Pillow build can encode WebP -- checked once (a real
    build always can; this only guards an unusual stripped-down install) so
    generate_image_variants can silently skip the format instead of failing
    the whole render."""
    try:
        buf = io.BytesIO()
        Image.new("RGB", (2, 2)).save(buf, format="WEBP")
        return True
    except Exception:
        return False


def detect_near_white_border(img, *, threshold=245, strip_px=8):
    """True if `img`'s outer border (a strip_px-wide strip on all four
    edges) averages near-white (mean channel value >= threshold) -- the
    signature of a studio product-cutout shot on a white background, as
    opposed to a lifestyle/installation photo with a real background.
    Approximate by design (no attempt at real background segmentation);
    used only as generate_image_variants' "cutout" flag, which picks
    object-fit -- never the box ratio, and never which asset to use."""
    rgb = img.convert("RGB")
    w, h = rgb.size
    strip = max(1, min(strip_px, w // 2, h // 2))
    edges = [rgb.crop((0, 0, w, strip)), rgb.crop((0, h - strip, w, h)),
             rgb.crop((0, 0, strip, h)), rgb.crop((w - strip, 0, w, h))]
    means = []
    for band in edges:
        pixels = list(band.getdata())
        if pixels:
            means.append(sum(sum(p) / 3 for p in pixels) / len(pixels))
    return bool(means) and (sum(means) / len(means)) >= threshold


def generate_image_variants(data, dest_dir, asset_id, *, widths=IMAGE_SRCSET_WIDTHS, quality=ASSET_JPEG_QUALITY, log=None):
    """From already-downscaled `data` (<=ASSET_MAX_LONG_EDGE px long edge),
    writes one JPEG (+ WebP, when this Pillow build supports it -- see
    _webp_supported) per width in `widths` that does not exceed the
    source's own width (never upscaled), named
    <asset_id>-<width>.<jpg|webp>, into dest_dir. Always re-encodes (a PNG
    source becomes JPEG/WebP too -- docs/IMAGES-AUDIT-2026-09-14.md problem
    4's oversized PNG heroes only exist because the old single-file path
    let a PNG through un-converted). Returns
    {"variants": [{"width": int, "jpg": "assets/<name>", "webp":
    "assets/<name>"|None}, ...], "width": int, "height": int,
    "cutout": bool} -- variants is [] and width/height/cutout are
    None if `data` isn't an image Pillow can open.

    Cycle 45: "cutout" replaces the old "aspect" ("1x1"|"4x3"). That value
    was a near-white-border verdict -- a statement about the image's
    BACKGROUND -- but render_image_slot used it as the box's RATIO, so a
    1067x1600 portrait cutout was cropped into a 1:1 box and a 1024x1024
    square into a 4:3 one. The box ratio now comes from width/height
    (render_image_slot); "cutout" only picks object-fit. Deliberately NOT
    called "kind": a facts_pack asset already has a "kind"
    (logo/lifestyle/installation/render/...) and these must not collide."""
    try:
        img = Image.open(io.BytesIO(data))
        img.load()
    except Exception as e:
        if log:
            log.event("render", f"asset {asset_id} could not be opened for variant generation ({e})")
        return {"variants": [], "width": None, "height": None, "cutout": None}

    rgb = img.convert("RGB") if img.mode in ("RGBA", "P", "LA") else img.convert("RGB")
    cutout = detect_near_white_border(img)
    dest_dir = Path(dest_dir)
    dest_dir.mkdir(parents=True, exist_ok=True)
    webp_ok = _webp_supported()

    def _encode(im, w):
        # The asset id comes from tenant data, not guaranteed to be a safe
        # single path component (see tests/test_path_safety.py) -- sanitize
        # once and reuse the same sanitized name for both the file actually
        # written to disk and the "jpg"/"webp" path string returned to the
        # caller (render_image_slot puts that string directly into the
        # rendered <img>'s src/srcset), so the two can never disagree.
        jpg_name = safe_filename(f"{asset_id}-{w}.jpg", fallback="asset")
        buf = io.BytesIO()
        im.save(buf, format="JPEG", quality=quality)
        (dest_dir / jpg_name).write_bytes(buf.getvalue())
        webp_name = None
        if webp_ok:
            webp_name = safe_filename(f"{asset_id}-{w}.webp", fallback="asset")
            wbuf = io.BytesIO()
            im.save(wbuf, format="WEBP", quality=quality)
            (dest_dir / webp_name).write_bytes(wbuf.getvalue())
        return {"width": w, "jpg": f"assets/{jpg_name}", "webp": f"assets/{webp_name}" if webp_name else None}

    variants = []
    for w in widths:
        if w > rgb.width:
            continue
        scale = w / rgb.width
        size = (w, max(1, round(rgb.height * scale)))
        variants.append(_encode(rgb.resize(size, Image.LANCZOS), w))
    if not variants:
        # Source is narrower than the smallest configured width -- still
        # emit one variant at native size so srcset/picture always has
        # something to reference.
        variants.append(_encode(rgb, rgb.width))

    return {"variants": variants, "width": rgb.width, "height": rgb.height, "cutout": cutout}


def download_asset(asset, dest_dir, *, log=None, fetch_url=http_fetch_bytes, drive_downloader=ingest.download_drive_file):
    """Download one asset (Shopify CDN image, or a Drive file via
    drive_downloader/ingest.download_drive_file) into dest_dir as
    <asset id>-<width>.<jpg|webp> variants (generate_image_variants).
    Returns a dict:
    {"path": Path (the IMAGE_SRCSET_FALLBACK_WIDTH-or-largest jpg variant),
     "width": int|None, "height": int|None,
     "variants": [...] (generate_image_variants' list), "cutout": bool|None}
    -- width/height/variants/cutout are None/[] when the downloaded bytes
    aren't an image Pillow can open. Returns None (with a logged warning) if
    the download fails or the response looks like an HTML page instead of a
    file."""
    dest_dir = Path(dest_dir)
    dest_dir.mkdir(parents=True, exist_ok=True)
    try:
        if asset.get("drive_id"):
            tmp_dir = dest_dir / f"_tmp-{asset['id']}"
            tmp_path = drive_downloader(asset["drive_id"], tmp_dir)
            data = Path(tmp_path).read_bytes()
            ext = Path(tmp_path).suffix or ".bin"
            Path(tmp_path).unlink(missing_ok=True)
            try:
                tmp_dir.rmdir()
            except OSError:
                pass
        else:
            url = asset.get("url")
            if not url:
                return None
            data = fetch_url(url)
            ext = Path(url.split("?")[0]).suffix or ".jpg"

        if _looks_like_html(data):
            if log:
                log.event("render", f"asset {asset['id']} download looked like HTML; skipping")
            return None

        data, ext = resize_asset_bytes(data, ext, log=log, asset_id=asset["id"])
        variant_info = generate_image_variants(data, dest_dir, asset["id"], log=log)
        variants = variant_info["variants"]
        if not variants:
            # Cycle 31: not an image Pillow could open. Previously this
            # still wrote the raw bytes to disk and rendered an unsized
            # <img> -- exactly the kind of gap
            # docs/IMAGES-AUDIT-2026-09-14.md's markup checks now catch
            # (every img must have width/height). Treated the same as the
            # _looks_like_html case above: dropped, with a warning logged;
            # the template's `{% if asset %}` guard means it's simply not
            # rendered rather than shipping unsized.
            if log:
                log.event("render", f"asset {asset['id']} was not a decodable image; dropping")
            return None

        fallback = next((v for v in variants if v["width"] == IMAGE_SRCSET_FALLBACK_WIDTH), variants[-1])
        return {
            "path": dest_dir / Path(fallback["jpg"]).name,
            "width": variant_info["width"],
            "height": variant_info["height"],
            "variants": variants,
            "cutout": variant_info["cutout"],
        }
    except Exception as e:
        if log:
            log.event("render", f"asset {asset['id']} download failed: {e}")
        return None


def aspect_ratio_css(width, height):
    """"<w> / <h>", reduced by the greatest common divisor, for an inline
    `aspect-ratio` declaration -- 900x1200 becomes "3 / 4". None when
    either dimension is missing or non-positive."""
    if not width or not height or width <= 0 or height <= 0:
        return None
    divisor = math.gcd(int(width), int(height)) or 1
    return f"{int(width) // divisor} / {int(height) // divisor}"


# Fixed frames a slot may ask for by name when it genuinely needs every
# image in a row to share one box (`frame=` below). Cycle 51: the listicle
# cartridge's `pillars` look asks for "3x2" (its edge-to-edge item bands)
# and its `lander` look for "4x3" (the small thumbnail on each grid panel) --
# both are slots where every image must share one box or the grid steps. A
# slot that does not need a uniform box asks for no frame and keeps the
# image's own measured ratio.
IMAGE_FRAMES = {"4x3": (4, 3), "3x2": (3, 2), "1x1": (1, 1), "3x4": (3, 4), "16x9": (16, 9)}


def image_fit(asset, *, frame=None):
    """"contain" or "cover": the object-fit render_image_slot gives `asset`
    in a slot with this `frame` (None when there is no asset).

    A studio cutout on white (generate_image_variants' border test) is
    always "contain". In a named frame (IMAGE_FRAMES) only a lifestyle/
    installation photo by its facts_pack "kind" may fill the frame; with no
    frame the box is the image's own ratio, so anything that is not a
    cutout is "cover". Cycle 55: a Jinja global too, so a look can pick its
    LAYOUT from the same answer -- a cutout is placed contained on a panel,
    never as a full-bleed cover band or behind an overlay."""
    if not asset:
        return None
    is_cutout = bool(asset.get("cutout"))
    if frame in IMAGE_FRAMES:
        lifestyle = asset.get("kind") in ("lifestyle", "installation")
        return "cover" if (lifestyle and not is_cutout) else "contain"
    return "contain" if is_cutout else "cover"


def render_image_slot(asset, *, hero=False, css_class="", caption=None, sizes=None, aspect_box=True, frame=None):
    """The one function that builds an <img>/<picture> tag from an asset
    dict -- registered as a Jinja global in render_page's env (see below) so
    every cartridge template and every image-bearing block calls this
    instead of hand-writing markup, which is exactly how
    docs/IMAGES-AUDIT-2026-09-14.md problem 6 happened (one call site simply
    forgot loading="lazy"). `asset` is one of render_page's assets_by_id
    entries after download_asset has run (url/alt/width/height/variants/
    aspect all set); a falsy asset (missing/failed download) renders
    nothing, same as the templates' own `{% if asset %}` guards did before.

    Every image gets width/height (when known) and decoding="async". Unless
    `aspect_box` is False (press-logo-strip's own call -- a brand mark
    should never be boxed at all), it also gets an inline `aspect-ratio`
    reserving the box before the image decodes, so the layout never jumps.

    Cycle 45: that box is now the image's OWN ratio, taken from the
    width/height Pillow measured on the downloaded original
    (aspect_ratio_css). Before this, the box was one of two hardcoded
    ratios picked by a near-white-border test -- a background check, not a
    shape check -- so a 1067x1600 portrait cutout got `aspect-ratio: 1/1;
    object-fit: cover` and was cropped to a square, and a 1024x1024 square
    got 4/3. Nothing is stretched or cropped now: the box matches the
    source, and `height: auto` (structure.css) keeps it that way.

    `frame="4x3"` (IMAGE_FRAMES) is the explicit opt-in for a slot that
    really does need a uniform box. Only then does the source ratio and
    the box ratio differ, and only then does object-fit matter: a
    lifestyle/installation photo fills the frame with `cover`, while a
    product cutout is shown whole with `contain` on a neutral band rather
    than having the product sliced.

    The hero gets loading="eager",
    fetchpriority="high", and the wider IMAGE_SIZES_HERO `sizes`; every
    other slot gets loading="lazy" and IMAGE_SIZES_DEFAULT. When Pillow
    could generate WebP variants, the tag is wrapped in a <picture> with a
    WebP <source>, JPEG <img> fallback (harness/review.py's inliner only
    ever needs to touch that <img>'s plain src -- see docs/IMAGES.md)."""
    if not asset:
        return Markup("")
    variants = asset.get("variants") or []
    width, height = asset.get("width"), asset.get("height")
    alt = asset.get("alt", "")
    sizes = sizes or (IMAGE_SIZES_HERO if hero else IMAGE_SIZES_DEFAULT)

    jpg_srcset = ", ".join(f"{v['jpg']} {v['width']}w" for v in variants)
    webp_srcset = ", ".join(f"{v['webp']} {v['width']}w" for v in variants if v.get("webp"))
    fallback_src = next((v["jpg"] for v in variants if v["width"] == IMAGE_SRCSET_FALLBACK_WIDTH), None)
    if fallback_src is None:
        fallback_src = variants[-1]["jpg"] if variants else asset.get("url", "")

    # The box: a named frame when one was asked for, otherwise the image's
    # own ratio. `fit` only ever bites in the frame case (in the default
    # case the box already IS the source's ratio, so cover and contain are
    # the same picture) -- a cutout is contained on a neutral band, a
    # lifestyle photo fills the frame.
    ratio = fit = None
    if aspect_box:
        if frame in IMAGE_FRAMES:
            fw, fh = IMAGE_FRAMES[frame]
            ratio = f"{fw} / {fh}"
        else:
            ratio = aspect_ratio_css(width, height)
        fit = image_fit(asset, frame=frame)

    classes = " ".join(
        c for c in (
            "adv-img",
            f"adv-img--{fit}" if fit else "",
            "adv-img--hero" if hero else "",
            css_class,
        )
        if c
    )
    attrs = [f'src="{escape(fallback_src)}"', f'alt="{escape(alt)}"']
    if jpg_srcset:
        attrs += [f'srcset="{escape(jpg_srcset)}"', f'sizes="{escape(sizes)}"']
    if width and height:
        attrs += [f'width="{width}"', f'height="{height}"']
    attrs.append('loading="eager"' if hero else 'loading="lazy"')
    attrs.append('decoding="async"')
    if hero:
        attrs.append('fetchpriority="high"')
    if classes:
        attrs.append(f'class="{classes}"')
    if ratio:
        # Inline rather than a stylesheet rule on purpose: the ratio is
        # per-image, and inline wins over a storefront theme's own `img`
        # rules once `harness shopify-body` drops this markup into a
        # Shopify page body.
        attrs.append(f'style="aspect-ratio:{ratio}"')
    img_tag = f"<img {' '.join(attrs)}>"

    if webp_srcset:
        img_tag = f'<picture><source type="image/webp" srcset="{escape(webp_srcset)}" sizes="{escape(sizes)}">{img_tag}</picture>'
    if caption:
        img_tag = f"<figure>{img_tag}<figcaption>{escape(caption)}</figcaption></figure>"
    return Markup(img_tag)


# Cycle 52 (rebrand): a brand may ask for uppercase headlines. That is a
# CASE rule, not a copy rule -- the writer's own words are never rewritten,
# and nothing downstream (the claims gate, the visible-text scans, REVIEW.md)
# sees a different string. It is carried as one opt-in class on the page
# wrapper, which harness/structure.css and every listicle look have a rule
# for; a tenant that does not set it renders exactly as before.
HEADLINE_CASE_CLASS = "adv-case-upper"


def wrap_class(tenant=None):
    """The class list for base.html's page wrapper: "adv-wrap", plus the
    uppercase-headline class when the tenant's own brand asks for it
    (tenant.yaml `brand.headline_case: upper`). Any other value, or no
    brand section at all, leaves the wrapper exactly as it was."""
    tenant = tenant or tenant_mod.active()
    case = str(tenant.get("brand.headline_case") or "none").strip().lower()
    return f"adv-wrap {HEADLINE_CASE_CLASS}" if case == "upper" else "adv-wrap"


# Cycle 27 (brand import): a tenant's brand/logo.<ext>, if `harness brand
# import` (or a hand-placed file) has put one there. Checked in this fixed
# extension order so a tenant with both an .svg and a .png (the import
# writes only one, but a hand-edit could add a second) gets a deterministic
# choice -- vector first, same preference the importer itself uses.
LOGO_EXTENSIONS = (".svg", ".png", ".jpg", ".jpeg", ".webp")


def find_tenant_logo(brand_dir):
    """tenants/<t>/brand/logo.<ext>, the first extension in LOGO_EXTENSIONS
    that exists, or None."""
    brand_dir = Path(brand_dir)
    for ext in LOGO_EXTENSIONS:
        candidate = brand_dir / f"logo{ext}"
        if candidate.exists():
            return candidate
    return None


def render_page(
    *,
    cartridge_name,
    page,
    ad_brief,
    facts_pack,
    cartridges_dir,
    brand_dir,
    templates_dir,
    out_dir,
    published,
    updated,
    log=None,
    download_assets=True,
    fetch_url=http_fetch_bytes,
    drive_downloader=ingest.download_drive_file,
    tenant=None,
):
    tenant = tenant or tenant_mod.active()
    cartridge_dir = Path(cartridges_dir) / cartridge_name
    env = jinja2.Environment(
        # blocks.BLOCKS_DIR last: a cartridge's own template.html and the
        # shared templates win; block partials resolve as "<name>/block.html".
        loader=jinja2.FileSystemLoader([str(cartridge_dir), str(templates_dir), str(blocks.BLOCKS_DIR)]),
        autoescape=jinja2.select_autoescape(["html"]),
    )
    # Cycle 31: the one shared image-markup helper (see render_image_slot's
    # docstring) -- a Jinja global rather than a per-template import so
    # block.html partials (loaded via {% include %}, not Python) can call
    # it too.
    env.globals["render_image_slot"] = render_image_slot
    env.globals["image_fit"] = image_fit

    structure_css = load_structure_css()
    tenant_css = load_tenant_css(brand_dir, log)
    byline_html = load_byline_html(brand_dir, published, updated, log, tenant=tenant)
    # Cycle 43: listicle keeps the compact byline in the header and moves the
    # "About the author" block down next to the disclosure/sources footer
    # (see split_byline_html's docstring) -- every other cartridge keeps
    # today's single combined byline_html, byte-for-byte.
    about_author_html = ""
    if cartridge_name in ("listicle", "quiz"):
        byline_html, about_author_html = split_byline_html(byline_html)

    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    product = facts_pack.get("product", {})
    product_name = product.get("name")
    product_short_name = product.get("short_name") or product_name
    assets_by_id = {a["id"]: dict(a) for a in facts_pack.get("assets", [])}
    for asset in assets_by_id.values():
        # Fix cycle 3 item 5: alt text is always renderer-derived from the
        # asset's own kind + the product's short_name -- never the writer's
        # invented "alt" field (the template no longer reads it), since a
        # model-invented alt can describe something that isn't in the image.
        asset["alt"] = asset_alt(asset, product_short_name, tenant=tenant)
    used_claim_ids = collect_claim_ids(page)
    # Cycle 56: the comparison cartridge's renderer-owned sections (the model
    # table and its column images, trust/rating lines, fixed warranty and
    # financing sentences) come from facts_pack alone. Their claim ids join
    # the Sources list and their column images join the download set like
    # any writer-picked asset; alt text uses each column's own model name.
    comparison_context = None
    comparison_asset_ids = set()
    if cartridge_name == "comparison":
        comparison_context = comparison_mod.render_context(
            facts_pack, page, financing_lender=(product.get("financing") or {}).get("lender"),
            tenant_name=tenant.display_name,
        )
        used_claim_ids |= comparison_context["claim_ids"]
        for asset in comparison_mod.column_assets(facts_pack):
            assets_by_id.setdefault(asset["id"], dict(asset, alt=asset_alt(asset, asset["model_title"], tenant=tenant)))
            comparison_asset_ids.add(asset["id"])
    verified_by_id = {c["id"]: c for c in facts_pack.get("verified_claims", [])}
    # Cycle 57: the quiz's renderer-owned sections (result cards, trust,
    # financing, HSA and warranty lines -- harness/quiz.py's render_context)
    # cite claims page.json never carries, so they join the Sources list
    # here; each card's own product image joins the asset map (with the same
    # renderer-derived alt text as every other asset) and is downloaded below.
    quiz_data = None
    quiz_asset_ids = set()
    if cartridge_name == "quiz":
        from .write import resolve_allowed_cta_texts

        quiz_schema = json.loads(tenant.render((cartridge_dir / "schema.json").read_text()))

        def _quiz_cta_text(model_name, short_name):
            texts = resolve_allowed_cta_texts(
                quiz_schema, short_name, model_name=model_name, tenant=tenant,
                ad_angle=(ad_brief or {}).get("angle"),
            )
            return texts[0] if texts else ""

        warranty_id = tenant.get("warranty_claim_id")
        quiz_data = quiz_mod.render_context(
            facts_pack, page, cta_text_for=_quiz_cta_text,
            warranty_claim_id=warranty_id if warranty_id in verified_by_id else None,
            # Cycle 61: card titles lead with the tenant's short name;
            # the result's "See all models" link is the tenant's own
            # default_cta_url, omitted when the tenant sets none.
            brand=tenant.get("tenant_short_name") or tenant.display_name,
            all_models_url=tenant.get("default_cta_url"),
        )
        used_claim_ids |= quiz_data["claim_ids"]
        for card in quiz_data["cards"]:
            if card.get("image"):
                card_asset = dict(card["image"])
                card_asset["alt"] = asset_alt(card_asset, card["name"], tenant=tenant)
                assets_by_id.setdefault(card_asset["id"], card_asset)
                quiz_asset_ids.add(card_asset["id"])
    sources = build_sources_list(
        used_claim_ids, verified_by_id, product_name=product_name, product_url=product.get("url"), tenant=tenant,
        fallback_url_by_claim=(comparison_context or {}).get("source_url_by_claim"),
        product_name_by_url=(comparison_context or {}).get("model_title_by_url"),
    )

    # Cycle 31 (docs/IMAGES-AUDIT-2026-09-14.md problems 1-3): a render-time
    # selection-policy backstop over the writer's own asset_id picks, plus
    # cross-cartridge dedupe across the run this cartridge is part of.
    # Gated the same as the download block below (only a real run mutates
    # page.json or touches the run-dir's .image-selection.json) so a dry
    # run (download_assets=False, used by the eval baseline fixtures) is
    # byte-for-byte unaffected -- see docs/IMAGES.md.
    if download_assets:
        # ground.enforce_slot_plan mutates its `page` argument's asset_id
        # fields in place -- deep-copy first so render_page never mutates
        # the caller's own page object (pipeline.py's state.pages holds the
        # same dict across every stage after write_pages, and several test
        # suites share one module-level page fixture dict by reference
        # across many render_page calls; either would otherwise leak one
        # render's asset substitution into the next).
        page = copy.deepcopy(page)
        run_dir = out_dir.parent
        allow_ai_renders = bool(tenant.claims_config.get("allow_ai_renders"))
        exclude_ids = ground_mod.all_used_asset_ids(run_dir, exclude_cartridge=cartridge_name)
        final_used_ids = ground_mod.enforce_slot_plan(
            page, facts_pack.get("assets", []), cartridge_name,
            allow_ai_renders=allow_ai_renders, exclude_ids=exclude_ids,
        )
        # Cycle 42: deterministic paragraph-to-image matching, over whatever
        # enforce_slot_plan left in place -- a no-op for a tenant with no
        # reviewed (human or vision-drafted) alt text at all, see
        # ground.match_images_to_text's own docstring.
        match_result = ground_mod.match_images_to_text(
            page, facts_pack.get("assets", []), cartridge_name=cartridge_name,
            exclude_ids=exclude_ids, allow_ai_renders=allow_ai_renders,
        )
        if match_result.get("matches"):
            ground_mod.record_image_matches(run_dir, cartridge_name, match_result["matches"])
            final_used_ids = collect_asset_ids(page)
            for m in match_result["matches"]:
                if log:
                    log.event(
                        "ground",
                        f"image match: {m['path']}: {m['old_id']} -> {m['new_id']} "
                        f"(score={m['score']}, tokens={','.join(m['matched_tokens'])})",
                    )
        ground_mod.record_used_asset_ids(run_dir, cartridge_name, final_used_ids)

    # Cycle 51: the cartridge's LOOK -- which template under
    # cartridges/<cartridge>/looks/<look>/ renders this page (listicle since
    # cycle 51, product-page since cycle 54). Resolved here, in Python, so
    # every caller (a run, `harness rerender`, the eval baseline) goes
    # through the one rule (harness/looks.py's resolve_look): the page's own
    # recorded "look" when it has one, else the listicle's style pairing or
    # the cartridge's default, within the tenant's pinned looks. The
    # cartridge's template.html is a dispatcher that {% extends %} the
    # resolved path; the value is always one of the cartridge's own looks, so
    # no page.json field can steer that path out of the looks folder. Empty
    # for a cartridge with no looks, whose template never reads it.
    look = ""
    if looks_mod.has_looks(cartridge_name):
        try:
            look = looks_mod.resolve_look(cartridge_name, page.get("look"), style=page.get("style"), tenant=tenant)
        except ValueError as e:
            # Only reachable from a hand-edited page.json: `harness run
            # --look` / `harness rerender --look` are checked first. A page
            # still renders, in its default look.
            look = looks_mod.resolve_look(cartridge_name, None, style=page.get("style"), tenant=tenant)
            if log:
                log.event("render", f"{e}; falling back to {look!r}")

    # Cycle 54: the product-page `pdp` look's gallery (the product's own
    # storefront images) and the promise band's one image, chosen from the
    # page's FINAL asset picks (after the slot plan and the paragraph-image
    # matcher above) so they are downloaded with everything else just
    # below. Never written into page.json: the gallery repeats the hero on
    # purpose, which the duplicate-asset check would read as a mistake.
    gallery_ids, promise_id = [], None
    if cartridge_name == "product-page" and look == "pdp":
        pdp_ai = bool(tenant.claims_config.get("allow_ai_renders"))
        gallery_ids = pdp_mod.gallery_asset_ids(page, facts_pack, allow_ai_renders=pdp_ai)
        promise_id = pdp_mod.promise_asset_id(page, facts_pack, gallery_ids, allow_ai_renders=pdp_ai)

    # Fix 8: download each asset the page actually references, into
    # out_dir/assets/, and rewrite its url to a path relative to index.html
    # so the page folder is self-contained. An asset that fails to download
    # (or comes back as HTML) is dropped -- the template's own `{% if asset
    # %}` guards mean it's simply not rendered, with a warning logged.
    if download_assets:
        used_asset_ids = (
            collect_asset_ids(page) | comparison_asset_ids | set(gallery_ids)
            | ({promise_id} if promise_id else set()) | quiz_asset_ids
        )
        assets_dir = out_dir / "assets"
        for asset_id in list(assets_by_id):
            if asset_id not in used_asset_ids:
                continue
            downloaded = download_asset(
                assets_by_id[asset_id], assets_dir, log=log, fetch_url=fetch_url, drive_downloader=drive_downloader
            )
            if downloaded is None:
                del assets_by_id[asset_id]
            else:
                assets_by_id[asset_id]["url"] = f"assets/{downloaded['path'].name}"
                # Fix cycle 23: known dimensions (Pillow already decoded the
                # image to downscale it) so templates can set width/height
                # attributes and avoid a layout-shift-causing unsized <img>.
                if downloaded["width"] and downloaded["height"]:
                    assets_by_id[asset_id]["width"] = downloaded["width"]
                    assets_by_id[asset_id]["height"] = downloaded["height"]
                # Cycle 31: srcset variants render_image_slot needs -- see
                # docs/IMAGES.md. Cycle 45: "cutout" picks object-fit; the
                # box ratio comes from the width/height just above. Never
                # written to "kind" -- that is the facts_pack asset's own
                # semantic kind (lifestyle/installation/logo/...), which
                # asset_alt and ground.enforce_slot_plan both read.
                assets_by_id[asset_id]["variants"] = downloaded["variants"]
                if downloaded["cutout"] is not None:
                    assets_by_id[asset_id]["cutout"] = downloaded["cutout"]

    json_ld = build_json_ld(cartridge_name, page, facts_pack, published, updated, tenant=tenant)

    # Cycle 41 (listicle v0.2): the sections a reader sees but the writer
    # never writes -- the header's trust line, the pull-quote band, the model
    # picker, the closing HSA/FSA line and the sticky bar's rating line.
    # Every one is derived from facts_pack alone (harness/listicle.py), so
    # "omitted when this run verified nothing" is structural: there is no
    # page.json field to invent one in. Empty for every other cartridge, whose
    # templates never read it.
    cartridge_data = listicle_mod.render_context(facts_pack) if cartridge_name == "listicle" else {}
    if comparison_context is not None:
        cartridge_data = comparison_context
    if quiz_data is not None:
        cartridge_data = quiz_data

    # Cycle 54: the product-page `pdp` look's renderer-owned sections, the
    # same "from facts_pack alone" rule (harness/pdp.py). The claims they
    # cite join the page's Sources list, since the writer never cites them.
    if cartridge_name == "product-page" and look == "pdp":
        cartridge_data = pdp_mod.render_context(page, facts_pack, assets_by_id, gallery_ids, promise_id, tenant=tenant)
        sources = build_sources_list(
            used_claim_ids | pdp_mod.context_claim_ids(cartridge_data), verified_by_id,
            product_name=product_name, product_url=product.get("url"), tenant=tenant,
        )

    # Cycle 27: same self-contained-folder treatment as an ad asset (Fix 8
    # above) -- the logo is brand data, not something download_asset's
    # facts_pack.assets loop ever sees, so it's copied in on its own.
    logo_url = None
    logo_w = logo_h = None
    logo_path = find_tenant_logo(brand_dir)
    if logo_path is not None:
        logo_dest = out_dir / "assets" / f"brand-logo{logo_path.suffix}"
        logo_dest.parent.mkdir(parents=True, exist_ok=True)
        logo_dest.write_bytes(logo_path.read_bytes())
        logo_url = f"assets/{logo_dest.name}"
        try:
            from PIL import Image as _Img
            with _Img.open(logo_dest) as _im:
                logo_w, logo_h = _im.size
        except Exception:  # svg or unreadable: leave unsized
            logo_w = logo_h = None

    template = env.get_template("template.html")
    html = template.render(
        page=page,
        ad_brief=ad_brief,
        facts_pack=facts_pack,
        product=facts_pack["product"],
        structure_css=structure_css,
        tenant_css=tenant_css,
        byline_html=byline_html,
        about_author_html=about_author_html,
        assets=assets_by_id,
        sources=sources,
        json_ld=json_ld,
        logo_url=logo_url,
        logo_w=logo_w,
        logo_h=logo_h,
        published=published,
        updated=updated,
        cartridge=cartridge_name,
        cartridge_data=cartridge_data,
        look=look,
        micro_cta_after=listicle_mod.MICRO_CTA_AFTER_ITEMS,
        tenant=tenant,
        tenant_name=tenant.display_name,
        wrap_class=wrap_class(tenant),
        # Kimi long-run phase 3: a cartridge template composes a block with
        # {% include block_choice("slot", "default-name") ~ "/block.html" %};
        # the writer's recorded page.json "blocks" pick wins when it names a
        # registered block (blocks.choice validates, so the include path can
        # never escape the blocks directory).
        block_choice=functools.partial(blocks.choice, page),
        disclosure_text=tenant.format("disclosure_text") or tenant.get("disclosure_text", ""),
        # Cycle 55: the header label ("Advertisement", "Sponsored", ...) is a
        # tenant decision, not a template literal. Empty or unset renders no
        # label at all; see docs/TENANT-ONBOARDING.md.
        disclosure_label=str(tenant.get("disclosure_label") or "").strip(),
    )

    # Fix cycle 5 item 2: last line of defense -- a claim id printed in
    # parentheses inline in rendered copy (e.g. "(spec-<model>-capacity)")
    # reads as an internal SKU to a reader. write_and_gate_page's repair
    # loop (claims.find_leaked_claim_ids) and the post-render scan just
    # below (find_leaked_claim_ids_visible_text) should already have caught
    # this -- this only fires if both missed it, so it quietly removes the
    # parenthetical and logs a warning instead of failing an
    # already-written run over it.
    valid_claim_ids = {c["id"] for c in facts_pack.get("verified_claims", [])}
    html, stripped_ids = strip_leaked_claim_ids(html, valid_claim_ids)
    for token in stripped_ids:
        if log:
            log.event("render", f"stripped leaked claim id from copy: {token!r} ({cartridge_name})")

    # Fix cycle 2 item 9 / cycle 5 item 2: EMF and a leaked claim id are both
    # absolute -- scan the page as a reader would actually see it (tags/
    # scripts/styles stripped, entities unescaped) before writing it out.
    # This is a backstop behind the page.json gate: a citation URL or a
    # claim id that's fine sitting in a "url"/"asset_id" field can still
    # leak into visible prose (or a Sources-list link's text) once
    # rendered, and that must still STOP the run rather than publish.
    hits = find_forbidden_visible_text(html)
    hits += find_leaked_claim_ids_visible_text(html, valid_claim_ids)
    # Fix cycle 11 problem A item 3: post-render backstop for the same check
    # gate_page_json already ran pre-write -- an attributed_to_customer item
    # should never reach render_page without a visible attribution (the
    # writer repair loop would have caught it), but this mirrors the
    # EMF/leaked-claim-id defense-in-depth pattern rather than trusting the
    # earlier gate alone.
    hits += find_missing_attribution(page)
    # Cycle 65: same backstop for the quote-fidelity gate -- an attributed
    # line that embellishes what the ad speaker said never reaches a page.
    if ad_brief is not None:
        hits += quote_fidelity.find_unfaithful_attribution(
            page, ad_brief, ad_speaker_verified=tenant.get("ad_speaker_is_verified_customer") is True
        )
    if hits:
        raise ClaimsGateFailure(f"html_visible_text:{cartridge_name}", hits)

    # Kimi long-run phase 2: structural backstops on the rendered document.
    # Same fail-before-write pattern as the visible-text backstop above; a
    # failure here is a template/renderer/tenant-file bug, never something a
    # writer repair could fix (see harness/pagechecks.py's module docstring).
    structural_hits = pagechecks.find_html_validity_violations(html)
    structural_hits += pagechecks.find_rendered_json_ld_violations(html, cartridge_name)
    structural_hits += pagechecks.find_rendered_internal_link_violations(html, tenant=tenant)
    # Cycle 31 (docs/IMAGES-AUDIT-2026-09-14.md): no duplicate asset id on
    # the page, and a hero present where the cartridge requires one -- both
    # page.json-level concerns, independent of whether assets were actually
    # downloaded, so these run on every render.
    structural_hits += pagechecks.find_duplicate_asset_violations(page, cartridge_name)
    structural_hits += pagechecks.find_hero_requirement_violations(page, cartridge_name)
    if comparison_context is not None:
        structural_hits += comparison_mod.find_table_violations(comparison_context, facts_pack)
    # Cycle 57: the quiz script is inline and self-contained, and the result
    # cards are exactly the active models -- both renderer-owned.
    if cartridge_name == "quiz":
        structural_hits += quiz_mod.find_rendered_quiz_violations(html, cartridge_data)
    # Width/height/alt/favicon-size only exist on the rendered <img> once an
    # asset has actually been downloaded (download_asset is what measures
    # them) -- a dry run (download_assets=False, used by fast local
    # iteration and the eval baseline fixtures) never has them and must
    # stay exactly as gate-permissive as before this cycle.
    if download_assets:
        structural_hits += pagechecks.find_image_markup_violations(html)
    if structural_hits:
        raise ClaimsGateFailure(f"html_structure:{cartridge_name}", structural_hits)

    (out_dir / "index.html").write_text(html)
    (out_dir / "page.json").write_text(json.dumps(page, indent=2))
    return out_dir / "index.html"
