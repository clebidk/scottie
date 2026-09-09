"""Live price refresh (fix 2): at the start of `adv run`, fetch
https://peaksaunas.com/products.json (paginated), refresh claims/products.json
(keeping the `default` flag and any hand-written `short_name`), and build a
fresh, in-memory price claim per product dated today with the product URL as
source. The raw fetch is cached for 60 minutes in runs/products-cache.json;
on fetch failure the cache is used and a warning logged.
"""
import json
import time
import urllib.error
import urllib.request
from pathlib import Path

SHOPIFY_PRODUCTS_URL = "https://peaksaunas.com/products.json"
CACHE_TTL_S = 60 * 60
PAGE_LIMIT = 250


_BROWSER_HEADERS = {
    # peaksaunas.com's Cloudflare bot check can block a non-browser User-Agent.
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36",
}


def http_fetch_page(page):
    """Default `fetch_page`: one page of the live Shopify product feed, or
    None past the last page."""
    url = f"{SHOPIFY_PRODUCTS_URL}?limit={PAGE_LIMIT}&page={page}"
    req = urllib.request.Request(url, headers=_BROWSER_HEADERS)
    with urllib.request.urlopen(req, timeout=20) as resp:
        return json.loads(resp.read().decode("utf-8"))


def fetch_all_live_products(fetch_page=http_fetch_page):
    """All pages of the live Shopify product feed, concatenated."""
    products = []
    page = 1
    while True:
        data = fetch_page(page)
        page_products = (data or {}).get("products", [])
        if not page_products:
            break
        products.extend(page_products)
        page += 1
    return products


def load_cache(cache_path):
    cache_path = Path(cache_path)
    if not cache_path.exists():
        return None
    try:
        return json.loads(cache_path.read_text())
    except (json.JSONDecodeError, OSError):
        return None


def save_cache(cache_path, live_products):
    cache_path = Path(cache_path)
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    cache_path.write_text(json.dumps({"fetched_at": time.time(), "products": live_products}, indent=2))


def get_live_products(cache_path, fetch_page=http_fetch_page, log=None):
    """Live products, from the network if the 60-minute cache is stale/absent,
    else from cache. On fetch failure, falls back to the cache (however old)
    and logs a warning; if there's no cache either, re-raises."""
    cached = load_cache(cache_path)
    if cached and (time.time() - cached.get("fetched_at", 0)) < CACHE_TTL_S:
        return cached["products"]

    try:
        live_products = fetch_all_live_products(fetch_page=fetch_page)
        save_cache(cache_path, live_products)
        return live_products
    except Exception as e:
        if log:
            log.event("prices", f"live products.json fetch failed ({e}); using cache")
        if cached:
            return cached["products"]
        raise


def merge_products(old_products, live_products):
    """Refresh claims/products.json's slug -> entry map with live price/image/
    variant data, keeping each entry's hand-curated `default`, `short_name`,
    and `specs` fields. Only refreshes the curated products already in
    old_products -- the live feed also includes spare parts/accessories/other
    SKUs that were never curated (no specs, no real short_name), and those
    are intentionally left out rather than polluting the catalog."""
    merged = {}
    for lp in live_products:
        slug = lp.get("handle")
        if not slug or slug not in old_products:
            continue
        old = old_products[slug]
        variants = lp.get("variants", [])
        prices = [v["price"] for v in variants if v.get("price")]
        compare_prices = [v["compare_at_price"] for v in variants if v.get("compare_at_price")]
        image_urls = [img["src"] for img in lp.get("images", []) if img.get("src")]
        variants_summary = [
            {
                "sku": v.get("sku"),
                "title": v.get("title"),
                "price": v.get("price"),
                "available": v.get("available"),
            }
            for v in variants
        ]
        entry = {
            "slug": slug,
            "name": old["name"],
            "title": lp.get("title", old.get("title", "")),
            "url": f"https://peaksaunas.com/products/{slug}",
            "price": prices[0] if prices else old.get("price"),
            "compare_at_price": compare_prices[0] if compare_prices else old.get("compare_at_price"),
            "image_urls": image_urls or old.get("image_urls", []),
            "variants_summary": variants_summary or old.get("variants_summary", []),
            "specs": old.get("specs", []),
        }
        if old.get("default"):
            entry["default"] = True
        if old.get("short_name"):
            entry["short_name"] = old["short_name"]
        merged[slug] = entry

    for slug, old in old_products.items():
        merged.setdefault(slug, old)
    return merged


def format_price(amount):
    """Fix cycle 3 item 2: "$8,250" -- no ".00" cents suffix, comma
    thousands separator, cents kept only when non-zero (e.g. "$8,250.50")."""
    amount = float(amount)
    if amount == int(amount):
        return f"${int(amount):,}"
    return f"${amount:,.2f}"


def build_live_price_claims(products, today_iso, show_compare_at_price):
    """One in-memory price claim per product, dated today with the product
    URL as source -- never written to claims/verified.json. The compare-at
    figure is included only when show_compare_at_price is true (fix 1)."""
    claims = []
    for slug, p in products.items():
        price = p.get("price")
        if price is None:
            continue
        name_slug = p["name"].lower().replace(" ", "-")
        text = f"The Peak Saunas {p['name']} is priced at {format_price(price)}."
        compare_at = p.get("compare_at_price")
        if show_compare_at_price and compare_at:
            text = text[:-1] + f" (list/compare-at {format_price(compare_at)})."
        claims.append(
            {
                "id": f"price-{name_slug}",
                "text": text,
                "category": "price",
                "source": p["url"],
                "approved_by": "live-fetch",
                "date": today_iso,
            }
        )
    return claims


def refresh_price_data(*, products_path, cache_path, show_compare_at_price, today_iso, fetch_page=http_fetch_page, log=None):
    """Full fix-2 flow: fetch (or reuse the cache for) the live catalog,
    refresh claims/products.json on disk, and return
    (merged_products_by_slug, live_price_claims_by_slug)."""
    products_path = Path(products_path)
    old_doc = json.loads(products_path.read_text())
    old_products = old_doc.get("products", {})

    try:
        live_products = get_live_products(cache_path, fetch_page=fetch_page, log=log)
    except Exception as e:
        if log:
            log.event("prices", f"no live data and no cache available ({e}); keeping claims/products.json as-is")
        live_products = None

    if live_products is not None:
        merged = merge_products(old_products, live_products)
        products_path.write_text(
            json.dumps({"generated": today_iso, "source": SHOPIFY_PRODUCTS_URL, "products": merged}, indent=2)
            + "\n"
        )
    else:
        merged = old_products

    price_claims = build_live_price_claims(merged, today_iso, show_compare_at_price)
    price_claims_by_slug = {}
    for slug, p in merged.items():
        name_slug = p["name"].lower().replace(" ", "-")
        for c in price_claims:
            if c["id"] == f"price-{name_slug}":
                price_claims_by_slug[slug] = c
                break
    return merged, price_claims_by_slug
