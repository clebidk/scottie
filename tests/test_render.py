import json

import pytest

from harness.claims import ClaimsGateFailure
from harness.render import asset_alt, build_sources_list, render_page, resolve_public_url
from tests.support import REPO_ROOT, TENANT
from PIL import Image
from harness.render import ASSET_MAX_LONG_EDGE, ASSET_PNG_MAX_BYTES, download_asset, resize_asset_bytes
from io import BytesIO


# The first asset id in the real Fuji facts_pack manifest (claims/
# products.json's image_urls[0] for the Fuji slug) -- see the comment on
# FACTS_PACK["assets"] below.
FUJI_MANIFEST_ASSET_ID = (
    "asset-peak-saunas-fuji-2-person-indoor-near-zero-emf-full-spectrum-infrared-sauna-with-medical-grade-red-light-therapy-1"
)

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
    # Kimi long-run phase 2: the asset id is the real Fuji manifest id
    # (claims/products.json image_urls[0] -> asset-<slug>-1), not a
    # fixture-only one -- the new image-allowlist gate check
    # (harness/pagechecks.py) compares page.json asset ids against the run's
    # actual facts_pack manifest, so these pages only keep working
    # unmodified against the real claims store (tests/test_cli_run.py) if
    # the id resolves there too.
    "assets": [{"id": FUJI_MANIFEST_ASSET_ID, "url": "https://cdn.shopify.com/fuji-1.png", "kind": "image", "alt": "Fuji sauna"}],
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
    "body_sections": [{"heading": "Why hidden pricing kills trust", "paragraphs": [{"text": "Buyers move on when the price is hidden."}] + _filler_paragraphs(16)}],
    # Fix cycle 16 item 8: article gained two required stages between
    # body_sections and turn_section.
    "alternatives_section": {
        "heading": "Why the usual workarounds fall short",
        "paragraphs": [{"text": "Calling in for a number rarely goes any faster."}] + _filler_paragraphs(2),
    },
    "how_it_works_section": {
        "heading": "How an upfront price actually works",
        "paragraphs": [{"text": "The page states the number and lets the buyer compare it."}] + _filler_paragraphs(2),
    },
    "turn_section": {
        "heading": "What to look for",
        "intro": "A few signs.",
        "criteria": [
            {"text": "Price shown before any form."},
            {"text": "Includes medical-grade red light therapy standard.", "claim_ids": ["gbrain-allowlist-red-light"]},
        ],
    },
    "close": {"paragraphs": [{"text": "PEAK is one brand that does this."}]},
    "cta": {"text": "See the models", "url": "https://peaksaunas.com/collections/all"},
    "images": [{"asset_id": FUJI_MANIFEST_ASSET_ID}],
}

PRODUCT_PAGE_PAGE = {
    # Fix cycle 58 item 1: the pdp/classic looks' allowed_cta_texts resolves
    # {short_name}/{model_name} to the actual short model name ("Fuji"), not
    # the long descriptive form -- see harness/repair.py's
    # cartridge_write_constraints.
    "cta_text": "Shop the Fuji",
    "cta_url": "https://peaksaunas.com/products/fuji",
    "hero": {
        "product_name": "Peak Fuji",
        "promise": "A two-person sauna with the price shown up front.",
        "price_line": {"text": "$8,250.", "claim_ids": ["price-fuji"]},
        "financing_line": {"text": "Financing is available at checkout.", "claim_ids": []},
        "hero_image": {"asset_id": FUJI_MANIFEST_ASSET_ID},
    },
    # Cycle 62 (product-page v0.3.0): one promise band (a heading and two
    # short paragraphs), a short "what's included" list and exactly five
    # FAQs. The proof tiles, proof bullets, trust strip and writer spec
    # table are gone -- the specs are renderer-owned, from facts_pack.
    "angle_section": {
        "heading": "Why the price is on the page",
        "paragraphs": [
            {"text": "Some sauna brands ask for a call before they share a number. This page shows "
                     "the price, the specs and the warranty terms, so you can decide on your own time. "
                     "Every spec is listed below, next to the questions buyers ask most."},
            {"text": "The cabin seats two. Medical-grade red light therapy is included standard, and "
                     "the heaters cover near, mid and far infrared from every side. The cabin is built for "
                     "two people sitting side by side.",
             "claim_ids": ["gbrain-allowlist-red-light", "gbrain-allowlist-360-full-spectrum"]},
        ],
    },
    "included": [
        {"text": "Medical-grade red light therapy panel", "claim_ids": ["gbrain-allowlist-red-light"]},
        {"text": "Full-spectrum heaters on every side", "claim_ids": ["gbrain-allowlist-360-full-spectrum"]},
        {"text": "Support from a US-owned company", "claim_ids": ["gbrain-allowlist-us-owned"]},
    ],
    "faq": {
        "questions": [
            {"question": "Is the price shown up front?", "answer": "Yes. The price is shown on the product page before any form or call.", "claim_ids": ["price-fuji"]},
            {"question": "Is red light therapy an add-on?", "answer": "No. Medical-grade red light therapy is included standard.", "claim_ids": ["gbrain-allowlist-red-light"]},
            {"question": "What does the warranty cover?", "answer": "Limited lifetime warranty; full terms by component are published on the warranty page.", "claim_ids": ["warranty-terms"]},
            {"question": "Where is the company based?", "answer": "PEAK is a US-owned company.", "claim_ids": ["gbrain-allowlist-us-owned"]},
            {"question": "Can I pay over time?", "answer": "Financing is offered at checkout, and the checkout page shows the terms before you commit."},
        ]
    },
}

LONGFORM_PAGE = {
    "cta_text": "See pricing",
    "cta_url": "https://peaksaunas.com/products/fuji",
    "hero": {
        "headline": "The hidden cost of a hidden price",
        "subhead": "Why checkout matters as much as the product.",
        "hero_image": {"asset_id": FUJI_MANIFEST_ASSET_ID},
        "financing_line": {"text": "Financing is available at checkout.", "claim_ids": []},
    },
    "problem": {"heading": "Why shoppers give up", "paragraphs": [{"text": "A lot of sites make you call in for a number."}] + _filler_paragraphs(20)},
    "how_it_works": {
        "heading": "How Peak shows it",
        "steps": [
            {"title": "See the price", "text": "The price is on the page."},
            {"title": "Red light therapy", "text": "Medical-grade red light therapy is included standard.", "claim_ids": ["gbrain-allowlist-red-light"]},
            {"title": "Full spectrum infrared", "text": "360 full spectrum infrared heater placement.", "claim_ids": ["gbrain-allowlist-360-full-spectrum"]},
            {"title": "US-owned", "text": "PEAK is a US-owned company.", "claim_ids": ["gbrain-allowlist-us-owned"]},
        ],
    },
    "specs_and_proof": {"specs_table": [{"label": "Capacity", "value": "2-Person"}], "proof_points": [{"text": "Limited lifetime warranty; full terms by component are published on the warranty page.", "claim_ids": ["warranty-terms"]}]},
    "social_proof": {"reviews_summary": None, "quotes": []},
    "faq": {"questions": [{"question": "Is the price shown up front?", "text": "$8,250 is shown on the page.", "claim_ids": ["price-fuji"]}]},
    "final_cta": {
        "headline": "Ready to see the number?",
        "financing_line": {"text": "Financing is available at checkout.", "claim_ids": []},
        "warranty_line": {"text": "Limited lifetime warranty; full terms by component are published on the warranty page.", "claim_ids": ["warranty-terms"]},
    },
    # cartridges/longform/schema.json requires this ("a convenience index
    # of everything used [elsewhere on the page]") -- it deliberately
    # repeats hero.hero_image's id, which is why cycle 31's
    # pagechecks.find_duplicate_asset_violations / ground.enforce_slot_plan
    # both exclude longform's own "images" key from the duplicate-asset
    # scan (see their _DUPLICATE_ASSET_SKIP_KEYS / skip_keys).
    "images": [{"asset_id": FUJI_MANIFEST_ASSET_ID}],
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
        templates_dir=REPO_ROOT / "harness" / "templates",
        out_dir=tmp_path / cartridge_name,
        published="2026-09-09",
        updated="2026-09-09",
        download_assets=False,  # no network in tests; see the dedicated download tests below
    )
    html = index_path.read_text()

    assert "This page is published by PEAK" in html
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
        templates_dir=REPO_ROOT / "harness" / "templates",
        out_dir=tmp_path / "product-page",
        published="2026-09-09",
        updated="2026-09-09",
        download_assets=False,
    )
    html = index_path.read_text()
    # Cycle 54: the default `pdp` look renders the one allowed financing
    # sentence for a run with no lender (the classic look's shorter
    # "Financing available" is covered by its own template, unchanged).
    assert "Financing is available at checkout." in html
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
        templates_dir=REPO_ROOT / "harness" / "templates",
        out_dir=tmp_path / "product-page",
        published="2026-09-09",
        updated="2026-09-09",
        download_assets=False,
    )
    html = index_path.read_text()
    # Cycle 64: the document title is the full name; only the JSON-LD keeps
    # the catalog's long title
    assert "<title>Peak Fuji</title>" in html
    assert '"name": "Peak Fuji 2-Person Infrared Sauna"' in html


def test_render_page_downloads_used_assets_and_rewrites_urls(tmp_path):
    """fix 8: assets the page actually references are downloaded into
    out/<cartridge>/assets/ and referenced by a relative path. No network --
    fetch_url is a fake."""
    downloaded = {}
    image_bytes = _make_image_bytes(600, 600, fmt="PNG")

    def fake_fetch_url(url):
        downloaded[url] = downloaded.get(url, 0) + 1
        return image_bytes

    out_dir = tmp_path / "product-page"
    index_path = render_page(
        cartridge_name="product-page",
        page=PRODUCT_PAGE_PAGE,
        ad_brief=AD_BRIEF,
        facts_pack=FACTS_PACK,
        cartridges_dir=REPO_ROOT / "cartridges",
        brand_dir=tmp_path / "brand-does-not-exist",
        templates_dir=REPO_ROOT / "harness" / "templates",
        out_dir=out_dir,
        published="2026-09-09",
        updated="2026-09-09",
        fetch_url=fake_fetch_url,
    )
    html = index_path.read_text()

    assert downloaded == {"https://cdn.shopify.com/fuji-1.png": 1}
    # Cycle 31: download_asset now writes width-variant files
    # (<id>-<width>.jpg[/.webp]) instead of a single <id>.<ext> -- the
    # fallback src is the smallest configured width not exceeding the
    # source's own (600px here, so 480).
    assert f'src="assets/{FUJI_MANIFEST_ASSET_ID}-480.jpg"' in html
    assert (out_dir / "assets" / f"{FUJI_MANIFEST_ASSET_ID}-480.jpg").exists()


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
        templates_dir=REPO_ROOT / "harness" / "templates",
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
        templates_dir=REPO_ROOT / "harness" / "templates",
        out_dir=tmp_path / cartridge_name,
        published="2026-09-09",
        updated="2026-09-09",
        download_assets=False,
    )
    html = index_path.read_text()
    disclosure_pos = html.index("This page is published by PEAK")
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
    page["trust_strip"] = {"reviews": {"text": "9,000+ reviews, 4.9 stars", "claim_ids": []}}   # a pre-cycle-62 field
    index_path = render_page(
        cartridge_name="product-page",
        page=page,
        ad_brief=AD_BRIEF,
        facts_pack=FACTS_PACK,  # reviews_summary is None
        cartridges_dir=REPO_ROOT / "cartridges",
        brand_dir=tmp_path / "brand-does-not-exist",
        templates_dir=REPO_ROOT / "harness" / "templates",
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
        templates_dir=REPO_ROOT / "harness" / "templates",
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
        templates_dir=REPO_ROOT / "harness" / "templates",
        out_dir=tmp_path / "product-page",
        published="2026-09-09",
        updated="2026-09-09",
        download_assets=False,
    )
    html = index_path.read_text()
    assert 'href="https://peaksaunas.com/products/fuji"' in html
    assert ">https://peaksaunas.com/products/fuji<" not in html
    assert ">PEAK – Fuji product page<" in html


def test_render_page_sources_list_dedupes_by_url_and_omits_claim_text(tmp_path):
    """fix cycle 3 item 1: price-fuji, gbrain-allowlist-red-light, and
    gbrain-allowlist-360-full-spectrum all share the product page URL -- one
    line, not three -- and no claim text (only the label) appears in the
    Sources list."""
    # cycle 62: the current schema cites no shipping or returns claim, so an
    # old page's trust strip carries them here -- the Sources list is built
    # from every claim id a page.json cites, whatever the field
    page = {**PRODUCT_PAGE_PAGE, "trust_strip": {
        "shipping": {"text": "shipping text", "claim_ids": ["shipping-policy"]},
        "returns": {"text": "returns text", "claim_ids": ["returns-policy"]},
    }}
    index_path = render_page(
        cartridge_name="product-page",
        page=page,
        ad_brief=AD_BRIEF,
        facts_pack=FACTS_PACK,
        cartridges_dir=REPO_ROOT / "cartridges",
        brand_dir=tmp_path / "brand-does-not-exist",
        templates_dir=REPO_ROOT / "harness" / "templates",
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
    assert "PEAK is a US-owned company" not in sources_html
    assert ">PEAK – Warranty<" in sources_html
    assert ">PEAK – Shipping policy<" in sources_html
    assert ">PEAK – Refund policy<" in sources_html


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
    assert sources == [{"url": "https://peaksaunas.com/products/fuji", "label": "PEAK – Fuji product page"}]


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
        templates_dir=REPO_ROOT / "harness" / "templates",
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
            templates_dir=REPO_ROOT / "harness" / "templates",
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
        templates_dir=REPO_ROOT / "harness" / "templates",
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
        brand_dir=TENANT.brand_dir,
        templates_dir=REPO_ROOT / "harness" / "templates",
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
# Cycle 19 (Caleb's byline decision): three distinct roles -- author (Austin,
# responsible for every claim), contributor (the editorial team, not a named
# person), reviewer (Caleb, reviews specifications and sources) -- rendered
# as "Written by <author> · <contributor> · Reviewed by <reviewer>".
# ---------------------------------------------------------------------------

def test_byline_shows_three_distinct_roles(tmp_path):
    index_path = render_page(
        cartridge_name="article",
        page=ARTICLE_PAGE,
        ad_brief=AD_BRIEF,
        facts_pack=FACTS_PACK,
        cartridges_dir=REPO_ROOT / "cartridges",
        brand_dir=TENANT.brand_dir,
        templates_dir=REPO_ROOT / "harness" / "templates",
        out_dir=tmp_path / "article",
        published="2026-09-09",
        updated="2026-09-09",
        download_assets=False,
    )
    html = index_path.read_text()
    assert "Written by" in html
    assert "Austin Laudenslager" in html
    assert "PEAK Editorial Team" in html
    assert "Reviewed by" in html
    assert "Caleb Niednagel, Technology Lead" in html
    # never the word "credentialed" anywhere in the byline/about-author copy
    assert "credentialed" not in html.lower()
    # about-the-author block: Austin named responsible for every claim,
    # Caleb named as the reviewer of specifications and sources
    assert "Austin Laudenslager is the Founder &amp; CEO of PEAK and is responsible for every claim" in html
    assert "Caleb Niednagel, Technology Lead, reviews the specifications and sources" in html


def test_byline_names_returns_three_roles():
    from harness.render import byline_names

    author_name, contributor_name, reviewer_name = byline_names(TENANT)
    assert author_name == "Austin Laudenslager"
    assert contributor_name == "PEAK Editorial Team"
    assert reviewer_name == "Caleb Niednagel, Technology Lead"


# ---------------------------------------------------------------------------
# fix cycle 3 item 4: one CTA text/url per page, reused everywhere the
# cartridge shows a CTA -- no separate hero/repeat/final cta object to drift.
# ---------------------------------------------------------------------------

def test_product_page_cta_text_appears_exactly_twice(tmp_path):
    # The classic look's hero + repeat CTA. Cycle 54: the default `pdp` look
    # places the same one cta_text/cta_url in more spots (buy panel, closing
    # band, sticky phone bar) -- tests/test_product_page_looks.py covers it.
    index_path = render_page(
        cartridge_name="product-page",
        page={**PRODUCT_PAGE_PAGE, "look": "classic"},
        ad_brief=AD_BRIEF,
        facts_pack=FACTS_PACK,
        cartridges_dir=REPO_ROOT / "cartridges",
        brand_dir=tmp_path / "brand-does-not-exist",
        templates_dir=REPO_ROOT / "harness" / "templates",
        out_dir=tmp_path / "product-page",
        published="2026-09-09",
        updated="2026-09-09",
        download_assets=False,
    )
    html = index_path.read_text()
    assert html.count(">Shop the Fuji<") == 2
    assert html.count('href="https://peaksaunas.com/products/fuji"') >= 2


def test_longform_cta_text_appears_in_hero_sticky_and_final(tmp_path):
    index_path = render_page(
        cartridge_name="longform",
        page=LONGFORM_PAGE,
        ad_brief=AD_BRIEF,
        facts_pack=FACTS_PACK,
        cartridges_dir=REPO_ROOT / "cartridges",
        brand_dir=tmp_path / "brand-does-not-exist",
        templates_dir=REPO_ROOT / "harness" / "templates",
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
        templates_dir=REPO_ROOT / "harness" / "templates",
        out_dir=tmp_path / "product-page",
        published="2026-09-09",
        updated="2026-09-09",
        download_assets=False,
    )
    html = index_path.read_text()
    assert "a description that doesn't match the image" not in html
    assert 'alt="Peak Fuji – product photo"' in html


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
        templates_dir=REPO_ROOT / "harness" / "templates",
        out_dir=tmp_path / "article",
        published="2026-09-09",
        updated="2026-09-09",
        download_assets=False,
    )
    html = index_path.read_text()
    assert "financing estimates" not in html
    assert (
        "This page is published by PEAK, which sells the products described. "
        "Prices, specifications and policies are verified against PEAK&#39;s own "
    ) in html
    assert (
        "published sources at the time of writing. "
        "Every specific claim on this page is sourced; see Sources below. "
        "Prices were current as of the publish date above and may have changed since."
    ) in html


# ---------------------------------------------------------------------------
# Fix cycle 15 item 1: every asset downloaded into out/<run>/<cartridge>/
# assets/ is downscaled to a max 1600px long edge and re-encoded (JPEG
# quality 82; PNG kept as PNG unless the re-encoded PNG would exceed 1.5 MB,
# then converted to JPEG). The Mini sample review file was 46 MB because
# Drive originals were inlined at full size.
# ---------------------------------------------------------------------------





def _make_image_bytes(width, height, fmt="PNG", color=(120, 60, 200)):
    img = Image.new("RGB", (width, height), color=color)
    buf = BytesIO()
    img.save(buf, format=fmt)
    return buf.getvalue()


def test_resize_asset_bytes_shrinks_a_large_png_to_the_max_long_edge():
    original = _make_image_bytes(4000, 3000, fmt="PNG")
    new_bytes, new_ext = resize_asset_bytes(original, ".png", asset_id="a1")
    img = Image.open(BytesIO(new_bytes))
    assert max(img.width, img.height) == ASSET_MAX_LONG_EDGE
    assert new_ext == ".png"
    assert len(new_bytes) < len(original)


def test_resize_asset_bytes_leaves_a_small_image_under_the_cap_untouched_in_size():
    original = _make_image_bytes(400, 300, fmt="PNG")
    new_bytes, new_ext = resize_asset_bytes(original, ".png", asset_id="a1")
    img = Image.open(BytesIO(new_bytes))
    assert img.width == 400 and img.height == 300
    assert new_ext == ".png"


@pytest.mark.slow
def test_resize_asset_bytes_converts_a_png_over_1_5mb_to_jpeg():
    # A large, low-compressibility PNG (random-ish per-pixel noise defeats
    # PNG's lossless compression) that stays over ASSET_PNG_MAX_BYTES even
    # after the long-edge downscale.
    import random

    random.seed(0)
    img = Image.new("RGB", (ASSET_MAX_LONG_EDGE, ASSET_MAX_LONG_EDGE))
    img.putdata([(random.randint(0, 255), random.randint(0, 255), random.randint(0, 255)) for _ in range(img.width * img.height)])
    buf = BytesIO()
    img.save(buf, format="PNG")
    original = buf.getvalue()
    assert len(original) > ASSET_PNG_MAX_BYTES

    new_bytes, new_ext = resize_asset_bytes(original, ".png", asset_id="a1")
    assert new_ext == ".jpg"
    assert Image.open(BytesIO(new_bytes)).format == "JPEG"


def test_resize_asset_bytes_reencodes_a_jpeg_at_quality_82():
    original = _make_image_bytes(4000, 2000, fmt="JPEG")
    new_bytes, new_ext = resize_asset_bytes(original, ".jpg", asset_id="a1")
    assert new_ext == ".jpg"
    img = Image.open(BytesIO(new_bytes))
    assert max(img.width, img.height) == ASSET_MAX_LONG_EDGE
    assert img.format == "JPEG"


def test_resize_asset_bytes_logs_original_and_final_byte_counts():
    class FakeLog:
        def __init__(self):
            self.events = []

        def event(self, stage, message):
            self.events.append((stage, message))

    log = FakeLog()
    original = _make_image_bytes(4000, 3000, fmt="PNG")
    new_bytes, _ = resize_asset_bytes(original, ".png", log=log, asset_id="a1")
    assert any(
        f"{len(original)}" in msg and f"{len(new_bytes)}" in msg and "downscaled" in msg for _, msg in log.events
    )


def test_resize_asset_bytes_leaves_non_image_bytes_unchanged():
    # download_asset's own HTML-sniff already filters out an HTML error page
    # before resize_asset_bytes ever runs, but a corrupt/partial download
    # should degrade gracefully (original bytes kept) rather than crash the
    # run -- this is the same shape as tests/test_render.py's existing fake
    # "\xff\xd8\xff\xe0fake-jpeg-bytes" download fixtures.
    original = b"\xff\xd8\xff\xe0fake-jpeg-bytes"
    new_bytes, new_ext = resize_asset_bytes(original, ".png", asset_id="a1")
    assert new_bytes == original
    assert new_ext == ".png"


def test_download_asset_resizes_a_real_downloaded_image(tmp_path):
    large = _make_image_bytes(4000, 3000, fmt="PNG")

    def fake_fetch_url(url):
        return large

    dest_dir = tmp_path / "assets"
    result = download_asset({"id": "hero", "url": "https://example.com/hero.png"}, dest_dir, fetch_url=fake_fetch_url)
    assert result is not None
    # result["width"]/["height"] describe the downscaled source (<=1600px
    # long edge); result["path"] is the fallback srcset variant, a further
    # (usually smaller) resize of that -- both are asserted separately.
    assert max(result["width"], result["height"]) == ASSET_MAX_LONG_EDGE
    assert result["variants"], "expected at least one srcset variant"
    img = Image.open(result["path"])
    assert img.width in {v["width"] for v in result["variants"]}
    assert result["path"].stat().st_size < len(large)


@pytest.mark.slow
def test_render_page_then_review_stays_under_12mb_with_a_large_fake_image(tmp_path):
    """Regression for the 46 MB Mini sample review file: a large Drive
    original downloaded at render time must be downscaled before it ever
    reaches out/<run>/<cartridge>/assets/, so `adv review`'s data-URI-inlined
    HTML stays well under the 12 MB per-page ceiling."""
    from harness.cli import cmd_review
    import argparse

    large = _make_image_bytes(6000, 4000, fmt="PNG")

    def fake_fetch_url(url):
        return large

    run_dir = tmp_path / "run"
    out_dir = run_dir / "product-page"
    render_page(
        cartridge_name="product-page",
        page=PRODUCT_PAGE_PAGE,
        ad_brief=AD_BRIEF,
        facts_pack=FACTS_PACK,
        cartridges_dir=REPO_ROOT / "cartridges",
        brand_dir=tmp_path / "brand-does-not-exist",
        templates_dir=REPO_ROOT / "harness" / "templates",
        out_dir=out_dir,
        published="2026-09-09",
        updated="2026-09-09",
        fetch_url=fake_fetch_url,
    )

    exit_code = cmd_review(argparse.Namespace(run_dir=str(run_dir)))
    assert exit_code == 0
    review_path = run_dir / "product-page-review.html"
    assert review_path.stat().st_size < 12 * 1024 * 1024


# ---------------------------------------------------------------------------
# Fix cycle 15 item 2: an asset marked ai_generated (brand/assets-listicle-
# pack.json rows) must always get "Rendering:" prefixed to its alt text.
# ---------------------------------------------------------------------------

def test_asset_alt_prefixes_rendering_for_ai_generated_assets():
    alt = asset_alt({"kind": "ai_render", "ai_generated": True}, "Peak Mini")
    assert alt.startswith("Rendering:")


def test_asset_alt_does_not_prefix_rendering_for_a_real_photo():
    alt = asset_alt({"kind": "photo_product", "ai_generated": False}, "Peak Mini")
    assert not alt.startswith("Rendering:")


# ---------------------------------------------------------------------------
# Fix cycle 16 item 3 (design note 7): longform's optional hero.proof_stats
# row, rendered directly under the hero subhead.
# ---------------------------------------------------------------------------


def test_render_page_shows_longform_proof_stats_row_when_present(tmp_path):
    page = dict(LONGFORM_PAGE, hero=dict(LONGFORM_PAGE["hero"], proof_stats=[
        {"value": "4.8/5", "label": "from 1,200+ reviews", "claim_ids": ["reviews-live"]},
        {"value": "Free", "label": "shipping, always", "claim_ids": ["shipping-policy"]},
    ]))
    index_path = render_page(
        cartridge_name="longform",
        page=page,
        ad_brief=AD_BRIEF,
        facts_pack=FACTS_PACK,
        cartridges_dir=REPO_ROOT / "cartridges",
        brand_dir=tmp_path / "brand-does-not-exist",
        templates_dir=REPO_ROOT / "harness" / "templates",
        out_dir=tmp_path / "longform",
        published="2026-09-09",
        updated="2026-09-09",
        download_assets=False,
    )
    html = index_path.read_text()
    assert "4.8/5" in html
    assert "from 1,200+ reviews" in html
    assert "adv-proof-stats" in html


def test_render_page_omits_longform_proof_stats_row_when_absent(tmp_path):
    index_path = render_page(
        cartridge_name="longform",
        page=LONGFORM_PAGE,  # no proof_stats key
        ad_brief=AD_BRIEF,
        facts_pack=FACTS_PACK,
        cartridges_dir=REPO_ROOT / "cartridges",
        brand_dir=tmp_path / "brand-does-not-exist",
        templates_dir=REPO_ROOT / "harness" / "templates",
        out_dir=tmp_path / "longform",
        published="2026-09-09",
        updated="2026-09-09",
        download_assets=False,
    )
    html = index_path.read_text()
    # Fix cycle 23: structure.css now defines a rule for every class any
    # cartridge template can emit (including adv-proof-stats), so the bare
    # class name is always present in the inlined <style> block -- the real
    # assertion is that the markup itself never uses the class.
    assert 'class="adv-proof-stats"' not in html


# ---------------------------------------------------------------------------
# Fix cycle 16 item 8: article's two new stages between body_sections and
# turn_section.
# ---------------------------------------------------------------------------


def test_render_page_shows_article_alternatives_and_how_it_works_sections(tmp_path):
    index_path = render_page(
        cartridge_name="article",
        page=ARTICLE_PAGE,
        ad_brief=AD_BRIEF,
        facts_pack=FACTS_PACK,
        cartridges_dir=REPO_ROOT / "cartridges",
        brand_dir=tmp_path / "brand-does-not-exist",
        templates_dir=REPO_ROOT / "harness" / "templates",
        out_dir=tmp_path / "article",
        published="2026-09-09",
        updated="2026-09-09",
        download_assets=False,
    )
    html = index_path.read_text()
    assert ARTICLE_PAGE["alternatives_section"]["heading"] in html
    assert ARTICLE_PAGE["how_it_works_section"]["heading"] in html
    assert "adv-alternatives" in html
    assert "adv-how-it-works" in html
    # ordered between body_sections and turn_section
    assert html.index(ARTICLE_PAGE["alternatives_section"]["heading"]) < html.index(ARTICLE_PAGE["turn_section"]["heading"])


# ---------------------------------------------------------------------------
# Cycle 33: http_fetch_bytes scheme allowlist + size cap
# ---------------------------------------------------------------------------

def test_http_fetch_bytes_rejects_non_http_schemes(monkeypatch):
    from harness.render import http_fetch_bytes

    called = {"n": 0}

    def _boom(*_a, **_k):
        called["n"] += 1
        raise AssertionError("urlopen must not be called for non-http schemes")

    monkeypatch.setattr("harness.render.urllib.request.urlopen", _boom)
    with pytest.raises(ValueError, match="http\\(s\\)"):
        http_fetch_bytes("file:///etc/passwd")
    with pytest.raises(ValueError, match="http\\(s\\)"):
        http_fetch_bytes("ftp://example.com/x")
    assert called["n"] == 0


def test_http_fetch_bytes_caps_response_size(monkeypatch):
    from harness import render as render_mod
    from harness.render import HTTP_FETCH_MAX_BYTES, http_fetch_bytes

    class _Resp:
        def __init__(self, payload):
            self._payload = payload
            self._pos = 0

        def read(self, n=-1):
            if self._pos >= len(self._payload):
                return b""
            if n is None or n < 0:
                n = len(self._payload) - self._pos
            chunk = self._payload[self._pos : self._pos + n]
            self._pos += len(chunk)
            return chunk

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    oversized = b"x" * (HTTP_FETCH_MAX_BYTES + 1)

    def _open(_req, timeout=30):
        return _Resp(oversized)

    monkeypatch.setattr(render_mod.urllib.request, "urlopen", _open)
    with pytest.raises(ValueError, match="exceeded"):
        http_fetch_bytes("https://cdn.example.com/huge.bin")


def test_http_fetch_bytes_returns_small_http_response(monkeypatch):
    from harness import render as render_mod
    from harness.render import http_fetch_bytes

    class _Resp:
        def __init__(self, payload):
            self._payload = payload
            self._pos = 0

        def read(self, n=-1):
            if self._pos >= len(self._payload):
                return b""
            if n is None or n < 0:
                n = len(self._payload) - self._pos
            chunk = self._payload[self._pos : self._pos + n]
            self._pos += len(chunk)
            return chunk

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    def _open(_req, timeout=30):
        return _Resp(b"ok-bytes")

    monkeypatch.setattr(render_mod.urllib.request, "urlopen", _open)
    assert http_fetch_bytes("https://cdn.example.com/small.bin") == b"ok-bytes"