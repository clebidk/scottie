"""`harness review`: one self-contained review.html per cartridge, images
inlined as data URIs, for sending to a reviewer who has no access to the run
directory. Simple regex on src="assets/..." -- no HTML parser needed.
"""
import base64
import mimetypes
import re
import sys
from pathlib import Path

_ASSET_SRC_RE = re.compile(r'src="assets/([^"]+)"')


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
    harness/serve.py serves a placeholder instead)."""
    run_dir = Path(run_dir)
    cartridge_dir = run_dir / page_name
    index_path = cartridge_dir / "index.html"
    if not index_path.exists():
        return None
    review_html = inline_assets_as_data_uris(index_path.read_text(), cartridge_dir / "assets")
    review_path = run_dir / f"{page_name}-review.html"
    review_path.write_text(review_html)
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


def cmd_review(args):
    run_dir = Path(args.run_dir)
    if not run_dir.is_dir():
        print(f"no such run dir: {run_dir}", file=sys.stderr)
        return 1
    written = build_reviews(run_dir)
    if not written:
        print(f"no cartridge output (index.html) found under {run_dir}", file=sys.stderr)
        return 1
    for p in written:
        print(f"Wrote {p}")
    return 0
