"""Jinja2 rendering: page.json + facts_pack -> out/<run>/<cartridge>/index.html.

Injects byline, dates, the "Advertisement" label, the disclosure paragraph,
a Sources list (from claim_ids used), and per-cartridge JSON-LD. The model
never writes any of that -- it's all added here.
"""
import functools
import io
import json
import re
import urllib.request
from pathlib import Path
from urllib.parse import urlparse

import jinja2
from PIL import Image

from . import blocks
from . import ingest
from . import pagechecks
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
        return tenant.format("product_page_label", product_name=product_name or "product") or f"{prefix} – product page"
    path_labels = tenant.get("source_path_labels") or {}
    if path in path_labels:
        return f"{prefix} – {path_labels[path]}"
    fallback = path.rsplit("/", 1)[-1].replace("-", " ").title()
    return f"{prefix} – {fallback}" if fallback else prefix


def build_sources_list(used_claim_ids, verified_by_id, product_name=None, product_url=None, tenant=None):
    """One entry per distinct public source URL referenced by
    `used_claim_ids` (fix cycle 3 item 1) -- claim texts are never shown on
    the page, only in REVIEW.md; the page gets a label (link text) and the
    URL (href only). A claim's "source" field is resolved to a real http(s)
    URL first (resolve_public_url) -- a claim with no public URL of its own
    (some of the gbrain-seeded allowlist claims are internal-only) falls
    back to the product page rather than a broken/internal href, and is
    skipped entirely if there's no product page to fall back to either."""
    seen = {}
    for cid in sorted(used_claim_ids):
        claim = verified_by_id.get(cid)
        if not claim:
            continue
        url = resolve_public_url(claim.get("source", ""), fallback_url=product_url)
        if not url:
            continue
        if url not in seen:
            seen[url] = source_label(url, product_name=product_name, explicit_label=claim.get("label"), tenant=tenant)
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
            "name": product.get("short_name") or product["name"],
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


def http_fetch_bytes(url):
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
        },
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        return resp.read()


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


def download_asset(asset, dest_dir, *, log=None, fetch_url=http_fetch_bytes, drive_downloader=ingest.download_drive_file):
    """Download one asset (Shopify CDN image, or a Drive file via
    drive_downloader/ingest.download_drive_file) into dest_dir as
    <asset id>.<ext>. Returns (local Path, width, height) -- width/height are
    None when the downloaded bytes aren't an image Pillow can open -- or None
    (with a logged warning) if the download fails or the response looks like
    an HTML page instead of a file."""
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
        width, height = _image_dimensions(data)

        # The id comes from tenant data and the extension from a URL path;
        # neither is guaranteed to be a single safe path component.
        dest_path = dest_dir / safe_filename(f"{asset['id']}{ext}", fallback="asset")
        dest_path.write_bytes(data)
        return dest_path, width, height
    except Exception as e:
        if log:
            log.event("render", f"asset {asset['id']} download failed: {e}")
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

    structure_css = load_structure_css()
    tenant_css = load_tenant_css(brand_dir, log)
    byline_html = load_byline_html(brand_dir, published, updated, log, tenant=tenant)

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
    verified_by_id = {c["id"]: c for c in facts_pack.get("verified_claims", [])}
    sources = build_sources_list(used_claim_ids, verified_by_id, product_name=product_name, product_url=product.get("url"), tenant=tenant)

    # Fix 8: download each asset the page actually references, into
    # out_dir/assets/, and rewrite its url to a path relative to index.html
    # so the page folder is self-contained. An asset that fails to download
    # (or comes back as HTML) is dropped -- the template's own `{% if asset
    # %}` guards mean it's simply not rendered, with a warning logged.
    if download_assets:
        used_asset_ids = collect_asset_ids(page)
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
                local_path, width, height = downloaded
                assets_by_id[asset_id]["url"] = f"assets/{local_path.name}"
                # Fix cycle 23: known dimensions (Pillow already decoded the
                # image to downscale it) so templates can set width/height
                # attributes and avoid a layout-shift-causing unsized <img>.
                if width and height:
                    assets_by_id[asset_id]["width"] = width
                    assets_by_id[asset_id]["height"] = height

    json_ld = build_json_ld(cartridge_name, page, facts_pack, published, updated, tenant=tenant)

    template = env.get_template("template.html")
    html = template.render(
        page=page,
        ad_brief=ad_brief,
        facts_pack=facts_pack,
        product=facts_pack["product"],
        structure_css=structure_css,
        tenant_css=tenant_css,
        byline_html=byline_html,
        assets=assets_by_id,
        sources=sources,
        json_ld=json_ld,
        published=published,
        updated=updated,
        cartridge=cartridge_name,
        tenant=tenant,
        tenant_name=tenant.display_name,
        # Kimi long-run phase 3: a cartridge template composes a block with
        # {% include block_choice("slot", "default-name") ~ "/block.html" %};
        # the writer's recorded page.json "blocks" pick wins when it names a
        # registered block (blocks.choice validates, so the include path can
        # never escape the blocks directory).
        block_choice=functools.partial(blocks.choice, page),
        disclosure_text=tenant.format("disclosure_text") or tenant.get("disclosure_text", ""),
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
    if hits:
        raise ClaimsGateFailure(f"html_visible_text:{cartridge_name}", hits)

    # Kimi long-run phase 2: structural backstops on the rendered document.
    # Same fail-before-write pattern as the visible-text backstop above; a
    # failure here is a template/renderer/tenant-file bug, never something a
    # writer repair could fix (see harness/pagechecks.py's module docstring).
    structural_hits = pagechecks.find_html_validity_violations(html)
    structural_hits += pagechecks.find_rendered_json_ld_violations(html, cartridge_name)
    structural_hits += pagechecks.find_rendered_internal_link_violations(html, tenant=tenant)
    if structural_hits:
        raise ClaimsGateFailure(f"html_structure:{cartridge_name}", structural_hits)

    (out_dir / "index.html").write_text(html)
    (out_dir / "page.json").write_text(json.dumps(page, indent=2))
    return out_dir / "index.html"
