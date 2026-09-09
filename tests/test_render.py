import json
from pathlib import Path

import pytest

from adv.render import render_page

REPO_ROOT = Path(__file__).resolve().parent.parent

FACTS_PACK = {
    "product": {
        "name": "Fuji",
        "short_name": "Peak Fuji 2-Person Infrared Sauna",
        "slug": "peak-saunas-fuji-2-person-indoor-near-zero-emf-full-spectrum-infrared-sauna-with-medical-grade-red-light-therapy",
        "url": "https://peaksaunas.com/products/peak-saunas-fuji-2-person-indoor-near-zero-emf-full-spectrum-infrared-sauna-with-medical-grade-red-light-therapy",
        "price": "8250.00",
        "compare_at_price": None,
        "financing": {"available": True, "lender": None, "monthly": None},
        "image_urls": ["https://cdn.shopify.com/fuji-1.png"],
    },
    "specs": [{"label": "Capacity", "value": "2-Person"}],
    "warranty": "warranty text",
    "shipping": "shipping text",
    "returns": "returns text",
    "reviews_summary": None,
    "verified_claims": [
        {"id": "price-fuji", "text": "The Peak Saunas Fuji is priced at $8250.", "category": "price", "source": "https://peaksaunas.com/products/fuji"},
        {"id": "warranty-terms", "text": "warranty text", "category": "trust", "source": "https://peaksaunas.com/pages/warranty"},
        {"id": "shipping-policy", "text": "shipping text", "category": "trust", "source": "https://peaksaunas.com/policies/shipping-policy"},
        {"id": "returns-policy", "text": "returns text", "category": "trust", "source": "https://peaksaunas.com/policies/refund-policy"},
        {"id": "founder-ceo", "text": "Austin Laudenslager is the Founder & CEO.", "category": "trust", "source": "https://peaksaunas.com/pages/austin-laudenslager"},
    ],
    "assets": [{"id": "asset-1", "url": "https://cdn.shopify.com/fuji-1.png", "kind": "image", "alt": "Fuji sauna"}],
}

AD_BRIEF = {
    "hook": "hook", "promise": "promise", "angle": "angle", "claims_made": [], "speaker_experience": [],
    "features_shown": [], "objections_raised": [], "cta": "See pricing", "tone": "candid",
    "speaker_pov": "first_person", "source_file": "ad.txt", "input_type": "text", "transcript_or_text": "text",
}

ARTICLE_PAGE = {
    "headline": "Why the checkout page decides more than the price",
    "dek": "A look at what makes people trust a purchase enough to finish it.",
    "open": [{"text": "Shopping used to mean waiting for a callback."}],
    "body_sections": [{"heading": "Why hidden pricing kills trust", "paragraphs": [{"text": "Buyers move on when the price is hidden."}]}],
    "turn_section": {"heading": "What to look for", "intro": "A few signs.", "criteria": [{"text": "Price shown before any form."}]},
    "close": {"paragraphs": [{"text": "Peak Saunas is one brand that does this."}]},
    "cta": {"text": "See the models", "url": "https://peaksaunas.com/collections/all"},
    "images": [{"asset_id": "asset-1", "alt": "Fuji sauna"}],
}

PRODUCT_PAGE_PAGE = {
    "hero": {
        "product_name": "Fuji 2-Person Full Spectrum Infrared Sauna",
        "promise": "A two-person sauna with the price shown up front.",
        "price_line": {"text": "$8,250.", "claim_ids": ["price-fuji"]},
        "financing_line": {"text": "Financing available", "claim_ids": []},
        "cta": {"text": "Shop Fuji", "url": "https://peaksaunas.com/products/fuji"},
        "hero_image": {"asset_id": "asset-1", "alt": "Fuji sauna"},
    },
    "proof_bullets": [{"label": "Warranty", "text": "Backed by a written warranty.", "claim_ids": ["warranty-terms"]}],
    "angle_section": {"heading": "Why the price is on the page", "paragraphs": [{"text": "No form required."}]},
    "specs_table": [{"label": "Capacity", "value": "2-Person"}],
    "trust_strip": {
        "warranty": {"text": "warranty text", "claim_ids": ["warranty-terms"]},
        "shipping": {"text": "shipping text", "claim_ids": ["shipping-policy"]},
        "returns": {"text": "returns text", "claim_ids": ["returns-policy"]},
    },
    "repeat_cta": {"text": "Shop Fuji", "url": "https://peaksaunas.com/products/fuji"},
}

LONGFORM_PAGE = {
    "hero": {
        "headline": "The hidden cost of a hidden price",
        "subhead": "Why checkout matters as much as the product.",
        "hero_image": {"asset_id": "asset-1", "alt": "Fuji sauna"},
        "cta": {"text": "See pricing", "url": "https://peaksaunas.com/products/fuji"},
        "financing_line": {"text": "Financing available", "claim_ids": []},
    },
    "problem": {"heading": "Why shoppers give up", "paragraphs": [{"text": "A lot of sites make you call in for a number."}]},
    "how_it_works": {"heading": "How Peak shows it", "steps": [{"title": "See the price", "text": "The price is on the page."}]},
    "specs_and_proof": {"specs_table": [{"label": "Capacity", "value": "2-Person"}], "proof_points": [{"text": "warranty text", "claim_ids": ["warranty-terms"]}]},
    "social_proof": {"reviews_summary": None, "quotes": []},
    "faq": {"questions": [{"question": "Is the price shown up front?", "text": "$8,250 is shown on the page.", "claim_ids": ["price-fuji"]}]},
    "final_cta": {
        "headline": "Ready to see the number?",
        "cta": {"text": "See pricing", "url": "https://peaksaunas.com/products/fuji"},
        "financing_line": {"text": "Financing available", "claim_ids": []},
        "warranty_line": {"text": "warranty text", "claim_ids": ["warranty-terms"]},
    },
    "images": [{"asset_id": "asset-1", "alt": "Fuji sauna"}],
}


@pytest.mark.parametrize(
    "cartridge_name,page,expect_byline,expect_json_ld_type",
    [
        ("article", ARTICLE_PAGE, True, "Article"),
        ("product-page", PRODUCT_PAGE_PAGE, False, "Product"),
        ("longform", LONGFORM_PAGE, True, "FAQPage"),
    ],
)
def test_render_page(tmp_path, cartridge_name, page, expect_byline, expect_json_ld_type):
    index_path = render_page(
        cartridge_name=cartridge_name,
        page=page,
        ad_brief=AD_BRIEF,
        facts_pack=FACTS_PACK,
        cartridges_dir=REPO_ROOT / "cartridges",
        brand_dir=tmp_path / "brand-does-not-exist",
        templates_dir=REPO_ROOT / "adv" / "templates",
        out_dir=tmp_path / cartridge_name,
        published="2026-09-09",
        updated="2026-09-09",
        download_assets=False,  # no network in tests; see the dedicated download tests below
    )
    html = index_path.read_text()

    assert "Advertisement" in html
    assert "is an advertisement published by Peak Saunas" in html
    assert f'"@type": "{expect_json_ld_type}"' in html
    # fix 1: no lender/monthly figure renders while financing.lender is null
    assert "Bread Pay" not in html
    assert "/mo" not in html

    if expect_byline:
        assert "Austin Laudenslager" in html
        assert "Caleb Niednagel" in html

    page_json = json.loads((tmp_path / cartridge_name / "page.json").read_text())
    assert page_json == page


def test_render_page_shows_financing_available_with_no_lender(tmp_path):
    """fix 1: when facts_pack.product.financing.lender is null, the rendered
    financing line is always exactly "Financing available" -- the writer's
    own text (which could name a lender) is never trusted for rendering."""
    index_path = render_page(
        cartridge_name="product-page",
        page=PRODUCT_PAGE_PAGE,
        ad_brief=AD_BRIEF,
        facts_pack=FACTS_PACK,
        cartridges_dir=REPO_ROOT / "cartridges",
        brand_dir=tmp_path / "brand-does-not-exist",
        templates_dir=REPO_ROOT / "adv" / "templates",
        out_dir=tmp_path / "product-page",
        published="2026-09-09",
        updated="2026-09-09",
        download_assets=False,
    )
    html = index_path.read_text()
    assert "Financing available" in html
    assert "Bread Pay" not in html
    assert "/mo" not in html


def test_render_page_uses_short_name_in_title_and_json_ld(tmp_path):
    index_path = render_page(
        cartridge_name="product-page",
        page=PRODUCT_PAGE_PAGE,
        ad_brief=AD_BRIEF,
        facts_pack=FACTS_PACK,
        cartridges_dir=REPO_ROOT / "cartridges",
        brand_dir=tmp_path / "brand-does-not-exist",
        templates_dir=REPO_ROOT / "adv" / "templates",
        out_dir=tmp_path / "product-page",
        published="2026-09-09",
        updated="2026-09-09",
        download_assets=False,
    )
    html = index_path.read_text()
    assert "<title>Peak Fuji 2-Person Infrared Sauna</title>" in html
    assert '"name": "Peak Fuji 2-Person Infrared Sauna"' in html


def test_render_page_downloads_used_assets_and_rewrites_urls(tmp_path):
    """fix 8: assets the page actually references are downloaded into
    out/<cartridge>/assets/ and referenced by a relative path. No network --
    fetch_url is a fake."""
    downloaded = {}

    def fake_fetch_url(url):
        downloaded[url] = downloaded.get(url, 0) + 1
        return b"\xff\xd8\xff\xe0fake-jpeg-bytes"  # looks nothing like HTML

    out_dir = tmp_path / "product-page"
    index_path = render_page(
        cartridge_name="product-page",
        page=PRODUCT_PAGE_PAGE,
        ad_brief=AD_BRIEF,
        facts_pack=FACTS_PACK,
        cartridges_dir=REPO_ROOT / "cartridges",
        brand_dir=tmp_path / "brand-does-not-exist",
        templates_dir=REPO_ROOT / "adv" / "templates",
        out_dir=out_dir,
        published="2026-09-09",
        updated="2026-09-09",
        fetch_url=fake_fetch_url,
    )
    html = index_path.read_text()

    assert downloaded == {"https://cdn.shopify.com/fuji-1.png": 1}
    assert 'src="assets/asset-1.png"' in html
    assert (out_dir / "assets" / "asset-1.png").read_bytes() == b"\xff\xd8\xff\xe0fake-jpeg-bytes"


def test_render_page_skips_asset_that_downloads_as_html(tmp_path):
    """fix 8: a download that looks like an HTML page (e.g. a broken link) is
    skipped with a warning, not written as a broken image."""

    class FakeLog:
        def __init__(self):
            self.events = []

        def event(self, stage, message):
            self.events.append((stage, message))

    def fake_fetch_url(url):
        return b"<!doctype html><html>not an image</html>"

    log = FakeLog()
    out_dir = tmp_path / "product-page"
    index_path = render_page(
        cartridge_name="product-page",
        page=PRODUCT_PAGE_PAGE,
        ad_brief=AD_BRIEF,
        facts_pack=FACTS_PACK,
        cartridges_dir=REPO_ROOT / "cartridges",
        brand_dir=tmp_path / "brand-does-not-exist",
        templates_dir=REPO_ROOT / "adv" / "templates",
        out_dir=out_dir,
        published="2026-09-09",
        updated="2026-09-09",
        fetch_url=fake_fetch_url,
        log=log,
    )
    html = index_path.read_text()
    assert not (out_dir / "assets").exists() or not list((out_dir / "assets").iterdir())
    assert 'class="adv-hero-image"' not in html
    assert any("looked like HTML" in msg for _, msg in log.events)
