"""fix 3 / fix cycle 3 item 1: reviews-live is scraped off the product page,
but sourced to Judge.me itself, so it gets its own distinct "Judge.me
reviews for Peak Saunas" line in the Sources list instead of colliding with
the product page's own line."""
from harness.sources.judgeme import build_reviews_claim, parse_review_data, store_url


def test_build_reviews_claim_sources_judgeme_not_the_product_page():
    claim = build_reviews_claim(
        "https://peaksaunas.com/products/peak-saunas-fuji",
        {"rating": 4.8, "count": 3958},
        "2026-09-09",
    )
    assert claim["source"] == store_url()
    assert claim["source"] != "https://peaksaunas.com/products/peak-saunas-fuji"
    assert "4.8" in claim["text"]
    assert "3,958" in claim["text"]


def test_parse_review_data_reads_jsonld_aggregate_rating():
    html = (
        '<script type="application/ld+json">'
        '{"@type": "Product", "aggregateRating": {"ratingValue": "4.76", "reviewCount": "3958"}}'
        "</script>"
    )
    data = parse_review_data(html)
    assert data == {"rating": 4.76, "count": 3958}


def test_parse_review_data_falls_back_to_judgeme_widget_attributes():
    html = '<div data-average-rating="4.76" data-number-of-reviews="3,958"></div>'
    data = parse_review_data(html)
    assert data == {"rating": 4.76, "count": 3958}


def test_parse_review_data_returns_none_when_nothing_found():
    assert parse_review_data("<p>no review data here</p>") is None
