"""The storefront's public product feed (`/products.json`).

Read-only and unauthenticated: this is the same JSON any browser can fetch. No
Admin API call is made anywhere in this harness -- publishing is a separate,
not-yet-built step.

The feed is where a product's live price, images, variants, and `body_html`
come from. `body_html` matters beyond pricing: harness/pdp_claims.py harvests
facts from it that a hand-curated claims store often never carried.
"""
import json
import urllib.request

from .. import tenant as tenant_mod

PAGE_LIMIT = 250

_BROWSER_HEADERS = {
    # A storefront bot check can block a non-browser User-Agent.
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36",
}


def products_json_url(tenant=None):
    """The tenant's public product feed URL (tenant.yaml's
    shopify.products_json)."""
    tenant = tenant or tenant_mod.active()
    return tenant.get("shopify.products_json") or ""


def http_fetch_page(page):
    """Default `fetch_page`: one page of the live Shopify product feed, or
    None past the last page."""
    url = f"{products_json_url()}?limit={PAGE_LIMIT}&page={page}"
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


