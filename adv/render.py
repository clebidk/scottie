"""Jinja2 rendering: page.json + facts_pack -> out/<run>/<cartridge>/index.html.

Injects byline, dates, the "Advertisement" label, the disclosure paragraph,
a Sources list (from claim_ids used), and per-cartridge JSON-LD. The model
never writes any of that -- it's all added here.
"""
import json
import urllib.request
from pathlib import Path
from urllib.parse import urlparse

import jinja2

from . import ingest
from .claims import ClaimsGateFailure, collect_claim_ids, find_forbidden_visible_text

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
_SOURCE_CATEGORY_LABELS = {"price": "product page", "spec": "product page", "policy": "policy page", "trust": "page"}


def source_label(claim):
    source = claim.get("source", "") or ""
    if source.startswith("http"):
        host = urlparse(source).netloc.replace("www.", "") or "Source"
        base = "Peak Saunas" if host == "peaksaunas.com" else host
    else:
        base = "Peak Saunas"
    return f"{base} {_SOURCE_CATEGORY_LABELS.get(claim.get('category'), 'page')}"


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

    assets_by_id = {a["id"]: dict(a) for a in facts_pack.get("assets", [])}
    used_claim_ids = collect_claim_ids(page)
    verified_by_id = {c["id"]: c for c in facts_pack.get("verified_claims", [])}
    sources = [
        {**verified_by_id[cid], "label": source_label(verified_by_id[cid])}
        for cid in sorted(used_claim_ids)
        if cid in verified_by_id
    ]

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

    # Fix cycle 2 item 9: EMF is absolute -- scan the page as a reader would
    # actually see it (tags/scripts/styles stripped, entities unescaped)
    # before writing it out. This is a backstop behind the page.json gate:
    # a citation URL that's fine sitting in a "url"/"asset_id" field can
    # still leak into visible prose (or a Sources-list link's text) once
    # rendered, and that must still STOP the run rather than publish.
    hits = find_forbidden_visible_text(html)
    if hits:
        raise ClaimsGateFailure(f"html_visible_text:{cartridge_name}", hits)

    (out_dir / "index.html").write_text(html)
    (out_dir / "page.json").write_text(json.dumps(page, indent=2))
    return out_dir / "index.html"
