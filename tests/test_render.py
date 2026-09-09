import json
from pathlib import Path

import pytest

from adv.claims import ClaimsGateFailure
from adv.render import build_sources_list, render_page, resolve_public_url

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
        # Fix cycle 3 item 3: product-benefit claims (what the sauna does),
        # distinct from the transactional price/shipping/warranty/returns ids.
        # Same ids/categories as the real claims/seed-from-gbrain.json
        # allowlist entries merged into claims/verified.json, so these page
        # fixtures also work unmodified against the real claims store in
        # tests/test_cli_run.py.
        {"id": "gbrain-allowlist-red-light", "text": "Medical-grade red light therapy (included standard).", "category": "trust", "source": "https://peaksaunas.com/products/fuji"},
        {"id": "gbrain-allowlist-360-full-spectrum", "text": "360 degree full spectrum infrared heater placement.", "category": "spec", "source": "https://peaksaunas.com/products/fuji"},
        {"id": "gbrain-allowlist-us-owned", "text": "US-owned company.", "category": "trust", "source": "https://peaksaunas.com/pages/austin-laudenslager"},
    ],
    "assets": [{"id": "asset-1", "url": "https://cdn.shopify.com/fuji-1.png", "kind": "image", "alt": "Fuji sauna"}],
}

AD_BRIEF = {
    "hook": "hook", "promise": "promise", "angle": "angle", "claims_made": [], "speaker_experience": [],
    "features_shown": [], "objections_raised": [], "cta": "See pricing", "tone": "candid",
    "speaker_pov": "first_person", "source_file": "ad.txt", "input_type": "text", "transcript_or_text": "text",
}

# Fix cycle 4 item 2: cartridges now have a hard word-range gate
# (claims.find_word_range_violation), so these fixture pages -- reused
# end-to-end through `adv run` in test_cli_run.py -- need real body length,
# not just a couple of placeholder sentences. Plain, claim-id-free filler
# (no digits, $, %, or trigger words) padded onto an existing prose field
# keeps every other assertion in this file (exact strings, CTA counts, etc.)
# unchanged.
_FILLER_SENTENCES = [
    "Buyers weighing a purchase like this tend to ask similar questions before they commit, and the answers rarely come from a single glossy photo or a catchy headline; they come from spending a few careful minutes comparing specifics side by side across the handful of options actually worth considering.",
    "A shopper who has been burned before learns to slow down and read past the marketing copy, looking instead for plain language about materials, construction, and the kind of support a company offers once the sale is already done and the box has arrived at the front door.",
    "What separates a brand worth trusting from one that just talks a good game usually shows up in the small details: how it answers a direct question, whether its claims line up with what it actually publishes, and how it treats a customer who asks something inconvenient before buying.",
    "It helps to write down the two or three things that actually matter for daily use, then check each option against that short list instead of getting pulled along by whichever page happens to have the loudest headline or the most dramatic before-and-after style photography on it.",
    "None of this is complicated, but it does take a little patience, the kind that pays off later when the choice holds up under ordinary daily use instead of just looking good for the length of a single afternoon spent comparing tabs open side by side in a browser.",
]


def _filler_paragraphs(n):
    return [{"text": _FILLER_SENTENCES[i % len(_FILLER_SENTENCES)]} for i in range(n)]


ARTICLE_PAGE = {
    "headline": "Why the checkout page decides more than the price",
    "dek": "A look at what makes people trust a purchase enough to finish it.",
    "open": [{"text": "Shopping used to mean waiting for a callback."}],
    "body_sections": [{"heading": "Why hidden pricing kills trust", "paragraphs": [{"text": "Buyers move on when the price is hidden."}] + _filler_paragraphs(22)}],
    "turn_section": {
        "heading": "What to look for",
        "intro": "A few signs.",
        "criteria": [
            {"text": "Price shown before any form."},
            {"text": "Includes medical-grade red light therapy standard.", "claim_ids": ["gbrain-allowlist-red-light"]},
        ],
    },
    "close": {"paragraphs": [{"text": "Peak Saunas is one brand that does this."}]},
    "cta": {"text": "See the models", "url": "https://peaksaunas.com/collections/all"},
    "images": [{"asset_id": "asset-1"}],
}

PRODUCT_PAGE_PAGE = {
    "cta_text": "Shop the Peak Fuji 2-Person Infrared Sauna",
    "cta_url": "https://peaksaunas.com/products/fuji",
    "hero": {
        "product_name": "Fuji 2-Person Full Spectrum Infrared Sauna",
        "promise": "A two-person sauna with the price shown up front.",
        "price_line": {"text": "$8,250.", "claim_ids": ["price-fuji"]},
        "financing_line": {"text": "Financing available", "claim_ids": []},
        "hero_image": {"asset_id": "asset-1"},
    },
    "proof_bullets": [
        {"label": "Warranty", "text": "Backed by a written warranty.", "claim_ids": ["warranty-terms"]},
        {"label": "Red light therapy", "text": "Medical-grade red light therapy is included standard.", "claim_ids": ["gbrain-allowlist-red-light"]},
        {"label": "Full spectrum infrared", "text": "360 full spectrum infrared heater placement.", "claim_ids": ["gbrain-allowlist-360-full-spectrum"]},
        {"label": "US-owned", "text": "Peak Saunas is a US-owned company.", "claim_ids": ["gbrain-allowlist-us-owned"]},
    ],
    "angle_section": {"heading": "Why the price is on the page", "paragraphs": [{"text": "No form required."}] + _filler_paragraphs(6)},
    "specs_table": [{"label": "Capacity", "value": "2-Person"}],
    "trust_strip": {
        "warranty": {"text": "warranty text", "claim_ids": ["warranty-terms"]},
        "shipping": {"text": "shipping text", "claim_ids": ["shipping-policy"]},
        "returns": {"text": "returns text", "claim_ids": ["returns-policy"]},
    },
}

LONGFORM_PAGE = {
    "cta_text": "See pricing",
    "cta_url": "https://peaksaunas.com/products/fuji",
    "hero": {
        "headline": "The hidden cost of a hidden price",
        "subhead": "Why checkout matters as much as the product.",
        "hero_image": {"asset_id": "asset-1"},
        "financing_line": {"text": "Financing available", "claim_ids": []},
    },
    "problem": {"heading": "Why shoppers give up", "paragraphs": [{"text": "A lot of sites make you call in for a number."}] + _filler_paragraphs(20)},
    "how_it_works": {
        "heading": "How Peak shows it",
        "steps": [
            {"title": "See the price", "text": "The price is on the page."},
            {"title": "Red light therapy", "text": "Medical-grade red light therapy is included standard.", "claim_ids": ["gbrain-allowlist-red-light"]},
            {"title": "Full spectrum infrared", "text": "360 full spectrum infrared heater placement.", "claim_ids": ["gbrain-allowlist-360-full-spectrum"]},
            {"title": "US-owned", "text": "Peak Saunas is a US-owned company.", "claim_ids": ["gbrain-allowlist-us-owned"]},
        ],
    },
    "specs_and_proof": {"specs_table": [{"label": "Capacity", "value": "2-Person"}], "proof_points": [{"text": "warranty text", "claim_ids": ["warranty-terms"]}]},
    "social_proof": {"reviews_summary": None, "quotes": []},
    "faq": {"questions": [{"question": "Is the price shown up front?", "text": "$8,250 is shown on the page.", "claim_ids": ["price-fuji"]}]},
    "final_cta": {
        "headline": "Ready to see the number?",
        "financing_line": {"text": "Financing available", "claim_ids": []},
        "warranty_line": {"text": "warranty text", "claim_ids": ["warranty-terms"]},
    },
    "images": [{"asset_id": "asset-1"}],
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


# ---------------------------------------------------------------------------
# fix cycle 2 item 13: the disclosure paragraph must be inside <main> or
# <article> so page-text extraction picks it up (it previously sat in a
# footer that was a sibling of the cartridge's own root element).
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "cartridge_name,page",
    [("article", ARTICLE_PAGE), ("product-page", PRODUCT_PAGE_PAGE), ("longform", LONGFORM_PAGE)],
)
def test_disclosure_paragraph_is_inside_main_or_article(tmp_path, cartridge_name, page):
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
        download_assets=False,
    )
    html = index_path.read_text()
    disclosure_pos = html.index("This page is an advertisement published by Peak Saunas")
    main_open = html.index("<main")
    main_close = html.rindex("</main>")
    assert main_open < disclosure_pos < main_close


# ---------------------------------------------------------------------------
# fix cycle 2 item 12 (FIXLOG item 3): the Reviews block is hidden in every
# template when facts_pack.reviews_summary is null, even if the writer put
# something in page.json's reviews slot anyway.
# ---------------------------------------------------------------------------

def test_product_page_hides_reviews_block_when_reviews_summary_is_null(tmp_path):
    page = json.loads(json.dumps(PRODUCT_PAGE_PAGE))
    page["trust_strip"]["reviews"] = {"text": "9,000+ reviews, 4.9 stars", "claim_ids": []}
    index_path = render_page(
        cartridge_name="product-page",
        page=page,
        ad_brief=AD_BRIEF,
        facts_pack=FACTS_PACK,  # reviews_summary is None
        cartridges_dir=REPO_ROOT / "cartridges",
        brand_dir=tmp_path / "brand-does-not-exist",
        templates_dir=REPO_ROOT / "adv" / "templates",
        out_dir=tmp_path / "product-page",
        published="2026-09-09",
        updated="2026-09-09",
        download_assets=False,
    )
    html = index_path.read_text()
    assert "9,000" not in html
    assert "<strong>Reviews</strong>" not in html


def test_longform_hides_reviews_block_when_reviews_summary_is_null(tmp_path):
    page = json.loads(json.dumps(LONGFORM_PAGE))
    page["social_proof"]["reviews_summary"] = {"text": "9,000+ reviews, 4.9 stars", "claim_ids": []}
    index_path = render_page(
        cartridge_name="longform",
        page=page,
        ad_brief=AD_BRIEF,
        facts_pack=FACTS_PACK,
        cartridges_dir=REPO_ROOT / "cartridges",
        brand_dir=tmp_path / "brand-does-not-exist",
        templates_dir=REPO_ROOT / "adv" / "templates",
        out_dir=tmp_path / "longform",
        published="2026-09-09",
        updated="2026-09-09",
        download_assets=False,
    )
    html = index_path.read_text()
    assert "9,000" not in html


# ---------------------------------------------------------------------------
# fix cycle 2 item 9 / fix cycle 3 item 1: Sources list link text is a short
# label, never the raw URL or a claim's full text; and a URL that leaked into
# visible prose STOPs the run at render time.
# ---------------------------------------------------------------------------

def test_render_page_sources_list_uses_label_not_raw_url_as_link_text(tmp_path):
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
    assert 'href="https://peaksaunas.com/products/fuji"' in html
    assert ">https://peaksaunas.com/products/fuji<" not in html
    assert ">Peak Saunas – Fuji product page<" in html


def test_render_page_sources_list_dedupes_by_url_and_omits_claim_text(tmp_path):
    """fix cycle 3 item 1: price-fuji, gbrain-allowlist-red-light, and
    gbrain-allowlist-360-full-spectrum all share the product page URL -- one
    line, not three -- and no claim text (only the label) appears in the
    Sources list."""
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
    sources_html = html[html.index("<h3>Sources</h3>"):]
    assert sources_html.count('href="https://peaksaunas.com/products/fuji"') == 1
    # claim text may appear elsewhere on the page (e.g. the proof bullets
    # legitimately show it as persuasive copy) -- only the Sources list
    # itself must never show claim text, just the label.
    assert "Medical-grade red light therapy" not in sources_html
    assert "Peak Saunas is a US-owned company" not in sources_html
    assert ">Peak Saunas – Warranty<" in sources_html
    assert ">Peak Saunas – Shipping policy<" in sources_html
    assert ">Peak Saunas – Refund policy<" in sources_html


# ---------------------------------------------------------------------------
# fix cycle 3 item 1 regression: some of the gbrain-seeded allowlist claims'
# "source" field is a compound string joining internal gbrain: references
# with a real URL via "; " (or, for a few, no real URL at all) -- caught by
# running the real claims/verified.json through `adv run` on the server,
# where the naive "use claim['source'] as the href verbatim" produced a
# broken link (or an un-clickable gbrain: URI) instead of a real one.
# ---------------------------------------------------------------------------

def test_resolve_public_url_extracts_http_url_from_a_compound_source():
    compound = "gbrain:competitors/overview; gbrain:policy/shipping-and-delivery; https://peaksaunas.com/policies/shipping-policy"
    assert resolve_public_url(compound) == "https://peaksaunas.com/policies/shipping-policy"


def test_resolve_public_url_falls_back_to_product_url_when_source_is_internal_only():
    assert resolve_public_url("gbrain:competitors/overview", fallback_url="https://peaksaunas.com/products/fuji") == "https://peaksaunas.com/products/fuji"


def test_resolve_public_url_returns_none_with_no_source_and_no_fallback():
    assert resolve_public_url("gbrain:competitors/overview") is None


def test_build_sources_list_skips_a_claim_with_no_resolvable_url():
    verified_by_id = {"internal-only": {"text": "x", "category": "trust", "source": "gbrain:internal/only"}}
    assert build_sources_list({"internal-only"}, verified_by_id) == []


def test_build_sources_list_falls_back_to_product_url_for_internal_only_claim():
    verified_by_id = {"internal-only": {"text": "x", "category": "trust", "source": "gbrain:internal/only"}}
    sources = build_sources_list({"internal-only"}, verified_by_id, product_name="Fuji", product_url="https://peaksaunas.com/products/fuji")
    assert sources == [{"url": "https://peaksaunas.com/products/fuji", "label": "Peak Saunas – Fuji product page"}]


def test_render_page_sources_list_resolves_compound_and_internal_only_sources(tmp_path):
    facts_pack = json.loads(json.dumps(FACTS_PACK))
    by_id = {c["id"]: c for c in facts_pack["verified_claims"]}
    by_id["gbrain-allowlist-red-light"]["source"] = "gbrain:competitors/overview"
    by_id["gbrain-allowlist-us-owned"]["source"] = (
        "gbrain:competitors/overview; https://peaksaunas.com/policies/shipping-policy"
    )
    index_path = render_page(
        cartridge_name="product-page",
        page=PRODUCT_PAGE_PAGE,
        ad_brief=AD_BRIEF,
        facts_pack=facts_pack,
        cartridges_dir=REPO_ROOT / "cartridges",
        brand_dir=tmp_path / "brand-does-not-exist",
        templates_dir=REPO_ROOT / "adv" / "templates",
        out_dir=tmp_path / "product-page",
        published="2026-09-09",
        updated="2026-09-09",
        download_assets=False,
    )
    html = index_path.read_text()
    assert "gbrain:" not in html
    assert 'href="https://peaksaunas.com/products/fuji"' in html
    assert 'href="https://peaksaunas.com/policies/shipping-policy"' in html


def test_render_page_raises_on_emf_leak_into_visible_text(tmp_path):
    page = json.loads(json.dumps(ARTICLE_PAGE))
    page["body_sections"][0]["paragraphs"][0]["text"] = (
        "Read more (Peak Saunas, 2026, "
        "https://peaksaunas.com/products/peak-saunas-fuji-near-zero-emf-sauna)."
    )
    out_dir = tmp_path / "article"
    with pytest.raises(ClaimsGateFailure) as exc_info:
        render_page(
            cartridge_name="article",
            page=page,
            ad_brief=AD_BRIEF,
            facts_pack=FACTS_PACK,
            cartridges_dir=REPO_ROOT / "cartridges",
            brand_dir=tmp_path / "brand-does-not-exist",
            templates_dir=REPO_ROOT / "adv" / "templates",
            out_dir=out_dir,
            published="2026-09-09",
            updated="2026-09-09",
            download_assets=False,
        )
    assert exc_info.value.stage == "html_visible_text:article"
    assert not (out_dir / "index.html").exists()


# ---------------------------------------------------------------------------
# fix cycle 5 item 2: last line of defense -- a claim id printed in
# parentheses inline in rendered copy (e.g. "(spec-fuji-capacity)") is
# quietly stripped rather than failing an already-written run over it, and
# the removal is logged.
# ---------------------------------------------------------------------------

def test_render_page_strips_leaked_claim_id_and_logs(tmp_path):
    class FakeLog:
        def __init__(self):
            self.events = []

        def event(self, stage, message):
            self.events.append((stage, message))

    page = json.loads(json.dumps(ARTICLE_PAGE))
    page["body_sections"][0]["paragraphs"][0]["text"] = (
        "The Peak Fuji 2-Person Infrared Sauna (price-fuji), which is priced at $8,250."
    )
    log = FakeLog()
    out_dir = tmp_path / "article"
    index_path = render_page(
        cartridge_name="article",
        page=page,
        ad_brief=AD_BRIEF,
        facts_pack=FACTS_PACK,
        cartridges_dir=REPO_ROOT / "cartridges",
        brand_dir=tmp_path / "brand-does-not-exist",
        templates_dir=REPO_ROOT / "adv" / "templates",
        out_dir=out_dir,
        published="2026-09-09",
        updated="2026-09-09",
        download_assets=False,
        log=log,
    )
    html = index_path.read_text()
    assert "price-fuji" not in html
    assert "The Peak Fuji 2-Person Infrared Sauna, which is priced at $8,250." in html
    assert any("stripped leaked claim id" in msg and "price-fuji" in msg for _, msg in log.events)


# ---------------------------------------------------------------------------
# fix cycle 2 item 10 (FIXLOG item 1): brand/byline.html is page-neutral --
# no more competitor-buyer's-guide copy lifted verbatim ("this ranking",
# "corrections that favor a competitor").
# ---------------------------------------------------------------------------

def test_real_byline_html_is_page_neutral(tmp_path):
    index_path = render_page(
        cartridge_name="article",
        page=ARTICLE_PAGE,
        ad_brief=AD_BRIEF,
        facts_pack=FACTS_PACK,
        cartridges_dir=REPO_ROOT / "cartridges",
        brand_dir=REPO_ROOT / "brand",
        templates_dir=REPO_ROOT / "adv" / "templates",
        out_dir=tmp_path / "article",
        published="2026-09-09",
        updated="2026-09-09",
        download_assets=False,
    )
    html = index_path.read_text()
    assert "Austin Laudenslager" in html
    assert "Caleb Niednagel" in html
    assert "this ranking" not in html.lower()
    assert "favor a competitor" not in html.lower()


# ---------------------------------------------------------------------------
# fix cycle 3 item 4: one CTA text/url per page, reused everywhere the
# cartridge shows a CTA -- no separate hero/repeat/final cta object to drift.
# ---------------------------------------------------------------------------

def test_product_page_cta_text_appears_exactly_twice(tmp_path):
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
    assert html.count(">Shop the Peak Fuji 2-Person Infrared Sauna<") == 2
    assert html.count('href="https://peaksaunas.com/products/fuji"') >= 2


def test_longform_cta_text_appears_in_hero_sticky_and_final(tmp_path):
    index_path = render_page(
        cartridge_name="longform",
        page=LONGFORM_PAGE,
        ad_brief=AD_BRIEF,
        facts_pack=FACTS_PACK,
        cartridges_dir=REPO_ROOT / "cartridges",
        brand_dir=tmp_path / "brand-does-not-exist",
        templates_dir=REPO_ROOT / "adv" / "templates",
        out_dir=tmp_path / "longform",
        published="2026-09-09",
        updated="2026-09-09",
        download_assets=False,
    )
    html = index_path.read_text()
    assert html.count(">See pricing<") == 3


# ---------------------------------------------------------------------------
# fix cycle 3 item 5: image alt text is always renderer-derived from the
# asset's kind + the product's short_name -- the writer's own "alt" field
# (even if a stray one is still sitting in page.json) is never used.
# ---------------------------------------------------------------------------

def test_hero_image_alt_is_renderer_derived_not_writer_supplied(tmp_path):
    page = json.loads(json.dumps(PRODUCT_PAGE_PAGE))
    page["hero"]["hero_image"]["alt"] = "a description that doesn't match the image"
    index_path = render_page(
        cartridge_name="product-page",
        page=page,
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
    assert "a description that doesn't match the image" not in html
    assert 'alt="Peak Fuji 2-Person Infrared Sauna – product photo"' in html


# ---------------------------------------------------------------------------
# fix cycle 3 item 6: disclosure text no longer mentions financing estimates.
# ---------------------------------------------------------------------------

def test_disclosure_text_omits_financing_estimates(tmp_path):
    index_path = render_page(
        cartridge_name="article",
        page=ARTICLE_PAGE,
        ad_brief=AD_BRIEF,
        facts_pack=FACTS_PACK,
        cartridges_dir=REPO_ROOT / "cartridges",
        brand_dir=tmp_path / "brand-does-not-exist",
        templates_dir=REPO_ROOT / "adv" / "templates",
        out_dir=tmp_path / "article",
        published="2026-09-09",
        updated="2026-09-09",
        download_assets=False,
    )
    html = index_path.read_text()
    assert "financing estimates" not in html
    assert (
        "This page is an advertisement published by Peak Saunas, which sells the products described. "
        "Every specific claim on this page is sourced; see Sources below. "
        "Prices were current as of the publish date above and may have changed since."
    ) in html
