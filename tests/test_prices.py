"""fix 2: live price refresh. No network -- merge_products/build_live_price_claims
are pure functions exercised directly with fake live-feed data."""
import json

from harness.prices import build_live_price_claims, format_price, merge_products, refresh_price_data

OLD_PRODUCTS = {
    "peak-saunas-fuji": {
        "slug": "peak-saunas-fuji",
        "name": "Fuji",
        "short_name": "Peak Fuji 2-Person Infrared Sauna",
        "title": "old title",
        "url": "https://peaksaunas.com/products/peak-saunas-fuji",
        "price": "8000.00",
        "compare_at_price": "13000.00",
        "image_urls": ["https://cdn.shopify.com/old.png"],
        "variants_summary": [],
        "specs": [{"label": "Capacity", "value": "2-Person", "claim_id": "spec-fuji-capacity"}],
        "default": True,
    }
}


def test_merge_products_updates_price_and_images_for_a_curated_product():
    live = [
        {
            "handle": "peak-saunas-fuji",
            "title": "Fuji 2-Person Sauna",
            "variants": [{"sku": "PEAK-FUJI", "title": "Default Title", "price": "8250.00", "compare_at_price": "14032.00", "available": True}],
            "images": [{"src": "https://cdn.shopify.com/new-1.png"}, {"src": "https://cdn.shopify.com/new-2.png"}],
        }
    ]
    merged = merge_products(OLD_PRODUCTS, live)
    fuji = merged["peak-saunas-fuji"]
    assert fuji["price"] == "8250.00"
    assert fuji["compare_at_price"] == "14032.00"
    assert fuji["image_urls"] == ["https://cdn.shopify.com/new-1.png", "https://cdn.shopify.com/new-2.png"]
    # hand-curated fields survive the refresh untouched
    assert fuji["short_name"] == "Peak Fuji 2-Person Infrared Sauna"
    assert fuji["specs"] == OLD_PRODUCTS["peak-saunas-fuji"]["specs"]
    assert fuji["default"] is True


def test_merge_products_ignores_live_skus_that_were_never_curated():
    """Regression: the live feed also lists spare parts/accessories (e.g. a
    replacement heater) that aren't one of our curated saunas -- merging them
    in used to fabricate a garbage product (name "El", price "0.00", no
    images, no specs) that could get picked as the run's product."""
    live = [
        {
            "handle": "peak-saunas-fuji",
            "title": "Fuji 2-Person Sauna",
            "variants": [{"sku": "PEAK-FUJI", "price": "8250.00"}],
            "images": [],
        },
        {
            "handle": "el-capitan-full-spectrum-heater-300w-240v-25cm-2026-model-1",
            "title": "El Capitan Full-Spectrum Heater 300W 240V 25cm (2026 Model 1)",
            "variants": [{"sku": "PART-HEATER", "price": "0.00"}],
            "images": [],
        },
    ]
    merged = merge_products(OLD_PRODUCTS, live)
    assert set(merged) == {"peak-saunas-fuji"}


def test_merge_products_keeps_curated_products_missing_from_the_live_feed():
    merged = merge_products(OLD_PRODUCTS, [])
    assert merged == OLD_PRODUCTS


def test_merge_products_preserves_active_flag_through_a_live_refresh():
    """Regression (cycle 8): merge_products used to only re-attach `default`
    and `short_name` from the old entry, silently dropping `active` -- so the
    very next live price refresh (every `adv run`) would erase the
    discontinued-model exclusion the product picker depends on."""
    old = {
        "peak-saunas-crown": dict(OLD_PRODUCTS["peak-saunas-fuji"], slug="peak-saunas-crown", name="Crown", active=False),
        "peak-saunas-fuji": dict(OLD_PRODUCTS["peak-saunas-fuji"], active=True),
    }
    live = [
        {"handle": "peak-saunas-crown", "title": "Crown", "variants": [{"sku": "PEAK-CROWN", "price": "4950.00"}], "images": []},
        {"handle": "peak-saunas-fuji", "title": "Fuji", "variants": [{"sku": "PEAK-FUJI", "price": "8250.00"}], "images": []},
    ]
    merged = merge_products(old, live)
    assert merged["peak-saunas-crown"]["active"] is False
    assert merged["peak-saunas-fuji"]["active"] is True


def test_build_live_price_claims_omits_compare_at_unless_configured():
    products = {"peak-saunas-fuji": {"name": "Fuji", "price": "8250.00", "compare_at_price": "14032.00", "url": "https://peaksaunas.com/products/peak-saunas-fuji"}}

    # fix cycle 3 item 2: "$8,250" -- no ".00" cents suffix on a whole dollar amount.
    claims = build_live_price_claims(products, "2026-09-09", show_compare_at_price=False)
    assert claims[0]["text"] == "The PEAK Fuji is priced at $8,250."

    claims = build_live_price_claims(products, "2026-09-09", show_compare_at_price=True)
    assert "(list/compare-at $14,032)" in claims[0]["text"]


# ---------------------------------------------------------------------------
# fix cycle 3 item 2: format_price -- "$8,250" (no cents), "$8,250.50" (cents
# kept only when non-zero).
# ---------------------------------------------------------------------------

def test_format_price_omits_cents_for_a_whole_dollar_amount():
    assert format_price("8250.00") == "$8,250"
    assert format_price(8250) == "$8,250"


def test_format_price_keeps_cents_when_non_zero():
    assert format_price("8250.50") == "$8,250.50"


# ---------------------------------------------------------------------------
# Fix cycle 11 problem B: every real `adv run` was rewriting
# claims/products.json with refreshed prices/images and leaving the git tree
# dirty. The live refresh now writes only to runs/products-cache.json; the
# in-memory product list merges cache prices over claims/products.json at
# run time; claims/products.json changes only by hand.
# ---------------------------------------------------------------------------

def _fake_fetch_page(live_products):
    def fetch_page(page):
        return {"products": live_products if page == 1 else []}
    return fetch_page


def test_refresh_price_data_never_writes_claims_products_json(tmp_path):
    products_path = tmp_path / "products.json"
    products_path.write_text(
        json.dumps({"generated": "2026-01-01", "source": "x", "products": OLD_PRODUCTS}, indent=2) + "\n"
    )
    original_bytes = products_path.read_bytes()
    cache_path = tmp_path / "products-cache.json"

    live = [
        {
            "handle": "peak-saunas-fuji",
            "title": "Fuji 2-Person Sauna",
            "variants": [{"sku": "PEAK-FUJI", "title": "Default Title", "price": "9999.00", "available": True}],
            "images": [{"src": "https://cdn.shopify.com/new.png"}],
        }
    ]
    merged, price_claims_by_slug, live_products = refresh_price_data(
        products_path=products_path,
        cache_path=cache_path,
        show_compare_at_price=False,
        today_iso="2026-09-10",
        fetch_page=_fake_fetch_page(live),
    )

    # claims/products.json is never rewritten -- untouched byte-for-byte --
    # even though a live refresh with a changed price just happened.
    assert products_path.read_bytes() == original_bytes

    # ...but the in-memory result for THIS run reflects the live price: the
    # merge (old_products loaded from claims/products.json + live_products)
    # still happens, it's just never persisted back to disk.
    assert merged["peak-saunas-fuji"]["price"] == "9999.00"
    assert price_claims_by_slug["peak-saunas-fuji"]["text"] == "The PEAK Fuji is priced at $9,999."
    assert live_products == live

    # The live refresh's only write anywhere is the existing raw-feed cache.
    assert cache_path.exists()
    cached = json.loads(cache_path.read_text())
    assert cached["products"] == live


def test_refresh_price_data_reflects_a_price_change_on_the_very_next_call_too(tmp_path):
    # "the in-memory product list merges cache prices over
    # claims/products.json at run time" holds across two separate calls
    # (two separate `adv run` invocations), not just within the return
    # value of one -- and the second call proves the merge is coming from
    # the on-disk raw-feed cache (still within its TTL), not a second fetch,
    # since fetch_page here raises if it's ever called again.
    products_path = tmp_path / "products.json"
    products_path.write_text(
        json.dumps({"generated": "2026-01-01", "source": "x", "products": OLD_PRODUCTS}, indent=2) + "\n"
    )
    cache_path = tmp_path / "products-cache.json"
    live = [{"handle": "peak-saunas-fuji", "title": "Fuji", "variants": [{"sku": "PEAK-FUJI", "price": "9999.00"}], "images": []}]

    refresh_price_data(
        products_path=products_path, cache_path=cache_path, show_compare_at_price=False,
        today_iso="2026-09-10", fetch_page=_fake_fetch_page(live),
    )

    def _no_refetch(page):
        raise AssertionError("should not re-fetch: the raw-feed cache is still fresh")

    merged, price_claims_by_slug, _ = refresh_price_data(
        products_path=products_path, cache_path=cache_path, show_compare_at_price=False,
        today_iso="2026-09-10", fetch_page=_no_refetch,
    )
    assert merged["peak-saunas-fuji"]["price"] == "9999.00"
    assert price_claims_by_slug["peak-saunas-fuji"]["text"] == "The PEAK Fuji is priced at $9,999."
