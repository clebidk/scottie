"""Jinja2 rendering: page.json + facts_pack -> out/<run>/<cartridge>/index.html.

Injects byline, dates, the "Advertisement" label, the disclosure paragraph,
a Sources list (from claim_ids used), and per-cartridge JSON-LD. The model
never writes any of that -- it's all added here.
"""
import json
from pathlib import Path

import jinja2

from .claims import collect_claim_ids

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
            "name": product["name"],
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
):
    cartridge_dir = Path(cartridges_dir) / cartridge_name
    env = jinja2.Environment(
        loader=jinja2.FileSystemLoader([str(cartridge_dir), str(templates_dir)]),
        autoescape=jinja2.select_autoescape(["html"]),
    )

    brand_css = load_brand_css(brand_dir, log)
    byline_html = load_byline_html(brand_dir, published, updated, log)

    assets_by_id = {a["id"]: a for a in facts_pack.get("assets", [])}
    used_claim_ids = collect_claim_ids(page)
    verified_by_id = {c["id"]: c for c in facts_pack.get("verified_claims", [])}
    sources = [verified_by_id[cid] for cid in sorted(used_claim_ids) if cid in verified_by_id]

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

    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "index.html").write_text(html)
    (out_dir / "page.json").write_text(json.dumps(page, indent=2))
    return out_dir / "index.html"
