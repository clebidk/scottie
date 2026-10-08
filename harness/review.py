"""`harness review`: one self-contained review.html per cartridge, images
inlined as data URIs, for sending to a reviewer who has no access to the run
directory, and for the review site's iframe (which serves this single file
and nothing beside it).

Cycle 31 introduced <picture> markup with a WebP <source srcset="assets/...">
ahead of the <img src="assets/..."> fallback, plus an `srcset` on the <img>.
Cycle 35 fix: browsers do NOT fall back to the inlined <img src> when the
<picture> <source> or the <img srcset> candidate they select fails to load;
they show a broken image. Standalone and iframe reviews therefore showed no
images. The inliner now strips every <source> element and every srcset/sizes
attribute before inlining `src`, so the one inlined JPEG is the only
candidate. Production pages (index.html, shopify-body.html) are untouched.

Fix cycle 58 item 2: render_image_slot gives every non-hero image (the pdp
gallery's hidden slides and every thumbnail) `loading="lazy"` -- exactly
right for the storefront, where it's a real fetch to defer. In a review
file every image is already an inlined data: URI with no fetch to defer,
but the attribute survives inlining, so the browser's native lazy-loading
still gates *painting* the image on its own visibility/viewport heuristics.
A gallery thumbnail row is on-screen from first paint so this is usually
invisible, but a hidden slide (display:none until its thumbnail is clicked)
never has a box for the browser to consider "near the viewport", and some
renderers (and a fast scroll in any of them) leave the plain gray
`.adv-img` placeholder background showing instead of the photo. The
inliner now also forces every `loading="lazy"` to `loading="eager"` --
harmless (nothing left to defer) and it removes the gate. Production pages
(index.html, shopify-body.html) never call this function, so their real
lazy-loading is unchanged.
"""
import base64
import mimetypes
import re
import sys
from pathlib import Path

from . import fonts as fonts_mod
from . import tenant as tenant_mod

_ASSET_SRC_RE = re.compile(r'src="assets/([^"]+)"')
_PICTURE_SOURCE_RE = re.compile(r"<source\b[^>]*>\s*", re.IGNORECASE)
_SRCSET_ATTR_RE = re.compile(r'\s+srcset="[^"]*"', re.IGNORECASE)
_SIZES_ATTR_RE = re.compile(r'\s+sizes="[^"]*"', re.IGNORECASE)
_LAZY_LOADING_RE = re.compile(r'loading="lazy"', re.IGNORECASE)

# Cycle 31: docs/IMAGE-MAP.md documents a prior incident where an unresized
# Drive original inlined at full size produced a 46 MB review file. Nothing
# enforced a ceiling before this -- this doesn't shrink the file (that's
# render.py's downscale/variant job), it only makes a regression loud
# instead of silently shipping an oversized review page.
REVIEW_HTML_MAX_BYTES = 12 * 1024 * 1024


def inline_assets_as_data_uris(html_text, assets_dir):
    """Replace every `src="assets/<file>"` with a data: URI of that file's
    bytes, read from `assets_dir`. A referenced file that's missing on disk is
    left as-is (better a broken image than a crashed command)."""

    def replace(match):
        filename = match.group(1)
        asset_path = Path(assets_dir) / filename
        if not asset_path.exists():
            return match.group(0)
        mime = mimetypes.guess_type(filename)[0] or "application/octet-stream"
        b64 = base64.b64encode(asset_path.read_bytes()).decode("ascii")
        return f'src="data:{mime};base64,{b64}"'

    html_text = _PICTURE_SOURCE_RE.sub("", html_text)
    html_text = _SRCSET_ATTR_RE.sub("", html_text)
    html_text = _SIZES_ATTR_RE.sub("", html_text)
    # Fix cycle 58 item 2: every image is now a self-contained data: URI --
    # nothing left to defer -- so native lazy-loading can only gate the
    # PAINT, never the fetch, and a hidden gallery slide (display:none
    # until its thumbnail is clicked) can be left showing the plain
    # `.adv-img` placeholder background instead of the photo.
    html_text = _LAZY_LOADING_RE.sub('loading="eager"', html_text)
    return _ASSET_SRC_RE.sub(replace, html_text)


def inline_font_face_urls(css, brand_dir):
    """Cycle 95: every `@font-face` src in the review document, as a data:
    URI of the tenant's own local file -- the first url whose filename is in
    brand/fonts/, brand/fonts-display/ or (cycle 92) brand/fonts-body/.

    base.css writes brand/fonts/<file> (uploaded and rewritten at publish)
    or the theme's CDN url, and neither loads on the review page or in the
    offline library thumbnail (which screenshots this file): a relative url
    resolves against the run folder. Only the review file gets this --
    index.html and the export keep the publishable urls, and no text check
    on index.html ever reads 400 KB of base64. A face with no local file
    keeps its url. Cycle 92: the one implementation lives in harness/fonts.py
    (the design gate's screenshots use it too, with file:// urls)."""
    return fonts_mod.localize_font_faces(css, brand_dir, "inline")


def build_review_for_page(run_dir, page_name, tenant=None):
    """Write <page_name>-review.html for one cartridge under `run_dir`, if it
    has a rendered index.html. Returns the path written, or None if that
    cartridge has no index.html yet (the caller decides what that means --
    `harness review` skips it, the review site's on-demand build in
    harness/serve.py serves a placeholder instead). Logs a warning to
    stderr (never raises -- a big review file is a size problem, not a
    correctness one) if the written file exceeds REVIEW_HTML_MAX_BYTES."""
    run_dir = Path(run_dir)
    cartridge_dir = run_dir / page_name
    index_path = cartridge_dir / "index.html"
    if not index_path.exists():
        return None
    review_html = inline_assets_as_data_uris(index_path.read_text(), cartridge_dir / "assets")
    tenant = tenant or tenant_mod.active()
    brand_dir = getattr(tenant, "brand_dir", None)
    if brand_dir:
        review_html = inline_font_face_urls(review_html, brand_dir)
        # Cycle 92: the body faces the page loads from Google Fonts (a <link>,
        # not an @font-face in the document), from brand/fonts-body/ when
        # `harness fonts fetch` has filled it.
        review_html = fonts_mod.inject(review_html, fonts_mod.body_font_css(tenant, "inline"))
    review_path = run_dir / f"{page_name}-review.html"
    review_path.write_text(review_html)
    size = review_path.stat().st_size
    if size > REVIEW_HTML_MAX_BYTES:
        print(
            f"warning: {review_path} is {size / 1024 / 1024:.1f} MB, over the "
            f"{REVIEW_HTML_MAX_BYTES / 1024 / 1024:.0f} MB review-html budget",
            file=sys.stderr,
        )
    return review_path


def build_reviews(run_dir, *, tenant=None):
    """Write <cartridge>-review.html for every cartridge under `run_dir` that
    has a rendered index.html. Returns the list of paths written."""
    run_dir = Path(run_dir)
    written = []
    for cartridge_dir in sorted(p for p in run_dir.iterdir() if p.is_dir()):
        path = build_review_for_page(run_dir, cartridge_dir.name, tenant=tenant)
        if path is not None:
            written.append(path)
    return written
