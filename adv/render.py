"""Jinja2 rendering: page.json + facts_pack -> out/<run>/<cartridge>/index.html.

Injects byline, dates, the "Advertisement" label, the disclosure paragraph,
a Sources list (from claim_ids used), and per-cartridge JSON-LD. The model
never writes any of that -- it's all added here.
"""
import json
import re
import urllib.request
from pathlib import Path
from urllib.parse import urlparse

import jinja2

from . import ingest
from .claims import (
    ClaimsGateFailure,
    collect_claim_ids,
    find_forbidden_visible_text,
    find_leaked_claim_ids_visible_text,
    strip_leaked_claim_ids,
)

FALLBACK_BYLINE = """<p class="adv-byline-author">By {author}, Founder &amp; CEO, Peak Saunas</p>
<p class="adv-byline-contributor">Reviewed by {contributor}</p>
<p class="adv-byline-dates">Published {published} &middot; Updated {updated}</p>"""

# Bare name for author: brand/byline.html's own template text appends
# ", Founder & CEO, Peak Saunas" after {{author}} -- passing the full title
# here would duplicate it. contributor keeps its title since no template
# appends one for that slot.
AUTHOR_NAME = "Austin Laudenslager"
CONTRIBUTOR_NAME = "Caleb Niednagel, Technology Lead"


def load_brand_css(brand_dir, log=None):
    css_path = Path(brand_dir) / "base.css"
    if css_path.exists():
        return css_path.read_text()
    if log:
        log.event("render", "brand/base.css not found; using adv/fallback.css")
    return (Path(__file__).parent / "fallback.css").read_text()


def load_byline_html(brand_dir, published, updated, log=None):
    byline_path = Path(brand_dir) / "byline.html"
    context = {
        "author": AUTHOR_NAME,
        "contributor": CONTRIBUTOR_NAME,
        "published": published,
        "updated": updated,
    }
    if byline_path.exists():
        raw = byline_path.read_text()
        try:
            return jinja2.Template(raw).render(**context)
        except jinja2.TemplateError as e:
            if log:
                log.event("render", f"brand/byline.html failed to render ({e}); using raw content")
            return raw
    if log:
        log.event("render", "brand/byline.html not found; using fallback byline markup")
    return FALLBACK_BYLINE.format(**context)


# Fix cycle 2 item 9: the Sources list must show a short, human-readable
# link label -- never the raw URL as visible text (that's exactly how the
# Fuji product URL's "near-zero-emf" handle was leaking into visible copy).
#
# Fix cycle 3 item 1: dedupe to one line per distinct source URL (several
# claims -- specs, price -- share the same product-page URL) with a specific
# label, derived from the URL path (or a claim's own "label" field, when a
# future claim needs one the path can't describe).
_SOURCE_PATH_LABELS = {
    "/pages/warranty": "Warranty",
    "/policies/shipping-policy": "Shipping policy",
    "/policies/refund-policy": "Refund policy",
    "/pages/austin-laudenslager": "Austin Laudenslager",
}

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


def source_label(url, *, product_name=None, explicit_label=None):
    """A short, human-readable label for `url` -- never the raw URL itself.
    An explicit "label" field on the claim always wins; otherwise the label
    is derived from the URL's host and path."""
    if explicit_label:
        return explicit_label
    if not url or not url.startswith("http"):
        return "Peak Saunas"
    parsed = urlparse(url)
    host = parsed.netloc.replace("www.", "")
    if host == "judge.me":
        return "Judge.me reviews for Peak Saunas"
    if host != "peaksaunas.com":
        return host
    path = parsed.path.rstrip("/")
    if path.startswith("/products/"):
        return f"Peak Saunas – {product_name or 'product'} product page"
    if path in _SOURCE_PATH_LABELS:
        return f"Peak Saunas – {_SOURCE_PATH_LABELS[path]}"
    fallback = path.rsplit("/", 1)[-1].replace("-", " ").title()
    return f"Peak Saunas – {fallback}" if fallback else "Peak Saunas"


def build_sources_list(used_claim_ids, verified_by_id, product_name=None, product_url=None):
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
            seen[url] = source_label(url, product_name=product_name, explicit_label=claim.get("label"))
    return [{"url": url, "label": label} for url, label in seen.items()]


def build_json_ld(cartridge_name, page, facts_pack, published, updated):
    product = facts_pack["product"]
    if cartridge_name == "article":
        return {
            "@context": "https://schema.org",
            "@type": "Article",
            "headline": page.get("headline", ""),
            "description": page.get("dek", ""),
            "author": {"@type": "Person", "name": "Austin Laudenslager"},
            "datePublished": published,
            "dateModified": updated,
            "publisher": {"@type": "Organization", "name": "Peak Saunas"},
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
}


def asset_alt(asset, product_short_name):
    product_short_name = product_short_name or "Peak Saunas"
    suffix = _ASSET_KIND_ALT_SUFFIXES.get(asset.get("kind"), "photo")
    return f"{product_short_name} – {suffix}"


def collect_asset_ids(node):
    """Every "asset_id" referenced anywhere in page.json."""
    ids = set()

    def walk(n):
        if isinstance(n, dict):
            if n.get("asset_id"):
                ids.add(n["asset_id"])
            for v in n.values():
                walk(v)
        elif isinstance(n, list):
            for v in n:
                walk(v)

    walk(node)
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


def download_asset(asset, dest_dir, *, log=None, fetch_url=http_fetch_bytes, drive_downloader=ingest.download_drive_file):
    """Download one asset (Shopify CDN image, or a Drive file via
    drive_downloader/ingest.download_drive_file) into dest_dir as
    <asset id>.<ext>. Returns the local Path, or None (with a logged
    warning) if the download fails or the response looks like an HTML
    page instead of a file."""
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

        dest_path = dest_dir / f"{asset['id']}{ext}"
        dest_path.write_bytes(data)
        return dest_path
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
):
    cartridge_dir = Path(cartridges_dir) / cartridge_name
    env = jinja2.Environment(
        loader=jinja2.FileSystemLoader([str(cartridge_dir), str(templates_dir)]),
        autoescape=jinja2.select_autoescape(["html"]),
    )

    brand_css = load_brand_css(brand_dir, log)
    byline_html = load_byline_html(brand_dir, published, updated, log)

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
        asset["alt"] = asset_alt(asset, product_short_name)
    used_claim_ids = collect_claim_ids(page)
    verified_by_id = {c["id"]: c for c in facts_pack.get("verified_claims", [])}
    sources = build_sources_list(used_claim_ids, verified_by_id, product_name=product_name, product_url=product.get("url"))

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
            local_path = download_asset(
                assets_by_id[asset_id], assets_dir, log=log, fetch_url=fetch_url, drive_downloader=drive_downloader
            )
            if local_path is None:
                del assets_by_id[asset_id]
            else:
                assets_by_id[asset_id]["url"] = f"assets/{local_path.name}"

    json_ld = build_json_ld(cartridge_name, page, facts_pack, published, updated)

    template = env.get_template("template.html")
    html = template.render(
        page=page,
        ad_brief=ad_brief,
        facts_pack=facts_pack,
        product=facts_pack["product"],
        brand_css=brand_css,
        byline_html=byline_html,
        assets=assets_by_id,
        sources=sources,
        json_ld=json_ld,
        published=published,
        updated=updated,
        cartridge=cartridge_name,
    )

    # Fix cycle 5 item 2: last line of defense -- a claim id printed in
    # parentheses inline in rendered copy (e.g. "(spec-fuji-capacity)")
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
    if hits:
        raise ClaimsGateFailure(f"html_visible_text:{cartridge_name}", hits)

    (out_dir / "index.html").write_text(html)
    (out_dir / "page.json").write_text(json.dumps(page, indent=2))
    return out_dir / "index.html"
