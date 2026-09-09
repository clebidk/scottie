"""fix 2: live price refresh. No network -- merge_products/build_live_price_claims
are pure functions exercised directly with fake live-feed data."""
from adv.prices import build_live_price_claims, merge_products

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


def test_build_live_price_claims_omits_compare_at_unless_configured():
    products = {"peak-saunas-fuji": {"name": "Fuji", "price": "8250.00", "compare_at_price": "14032.00", "url": "https://peaksaunas.com/products/peak-saunas-fuji"}}

    claims = build_live_price_claims(products, "2026-09-09", show_compare_at_price=False)
    assert claims[0]["text"] == "The Peak Saunas Fuji is priced at $8,250.00."

    claims = build_live_price_claims(products, "2026-09-09", show_compare_at_price=True)
    assert "14,032.00" in claims[0]["text"]
