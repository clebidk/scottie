"""`harness review`: one self-contained review.html per cartridge, images
inlined as data URIs, for sending to a reviewer who has no access to the run
directory. Simple regex on src="assets/..." -- no HTML parser needed.

Cycle 31: render_image_slot's <picture> markup (harness/render.py) puts a
WebP <source srcset="..."> ahead of the plain <img src="assets/...">
fallback -- this module's regex only ever targets `src="assets/..."`
(never `srcset`), so it inlines just that one JPEG variant (the
IMAGE_SRCSET_FALLBACK_WIDTH/1200px one, per render.render_image_slot) and
leaves the <source>'s srcset (and the <img>'s own srcset attribute)
pointing at relative assets/... paths. Those don't resolve once the file
is emailed/opened standalone (no server), so a reviewer's browser silently
falls through to the inlined <img> -- exactly the "inline only the
largest or the 1200 variant" behavior asked for, with no code change
needed here beyond this note. See docs/IMAGES.md.
"""
import base64
import mimetypes
import re
import sys
from pathlib import Path

_ASSET_SRC_RE = re.compile(r'src="assets/([^"]+)"')

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

    return _ASSET_SRC_RE.sub(replace, html_text)


def build_review_for_page(run_dir, page_name):
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


def build_reviews(run_dir):
    """Write <cartridge>-review.html for every cartridge under `run_dir` that
    has a rendered index.html. Returns the list of paths written."""
    run_dir = Path(run_dir)
    written = []
    for cartridge_dir in sorted(p for p in run_dir.iterdir() if p.is_dir()):
        path = build_review_for_page(run_dir, cartridge_dir.name)
        if path is not None:
            written.append(path)
    return written
