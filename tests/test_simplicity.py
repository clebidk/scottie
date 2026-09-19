"""Cycle 32: the simplicity gate (docs/RESEARCH-HORMOZI-LANDING.md section 3
-- harness/simplicity.py). Every check here is exercised directly against a
synthetic page.json per cartridge (unit level, no model calls); the
warn-vs-enforce wiring is exercised through harness/repair.py's
check_page_gates, same pattern tests/test_warmup.py uses for the warm-up
gate. tests/test_render.py's real ARTICLE_PAGE/PRODUCT_PAGE_PAGE/
LONGFORM_PAGE fixtures already pass every check here (see the "real fixture"
section below) -- that's a deliberate cross-check that the bands/regions
chosen actually fit real cartridge output, not just synthetic pages built to
satisfy them.
"""
from harness.repair import check_page_gates
from harness.simplicity import (
    find_above_fold_link_violations,
    find_headline_band_violation,
    find_offer_element_violations,
    find_simplicity_violations,
    find_value_equation_warnings,
    headline_word_band,
    simplicity_review_lines,
)
from tests.support import TENANT as REAL_TENANT
from tests.test_render import AD_BRIEF, ARTICLE_PAGE, FACTS_PACK, LONGFORM_PAGE, PRODUCT_PAGE_PAGE
from tests.test_warmup import _FakeTenant

# ---------------------------------------------------------------------------
# headline_word_band -- read straight from each real cartridge/schema.json,
# per the task's own "Store bands in each schema.json, read from there."
# ---------------------------------------------------------------------------

def test_headline_word_band_reads_article_schema():
    assert headline_word_band("article") == (8, 14)


def test_headline_word_band_reads_listicle_schema():
    assert headline_word_band("listicle") == (8, 14)


def test_headline_word_band_reads_longform_schema():
    assert headline_word_band("longform") == (6, 12)


def test_headline_word_band_reads_product_page_schema():
    assert headline_word_band("product-page") == (4, 10)


def test_headline_word_band_none_for_an_undeclared_cartridge():
    assert headline_word_band("comparison") is None


# ---------------------------------------------------------------------------
# find_headline_band_violation
# ---------------------------------------------------------------------------

def test_headline_band_violation_passes_article_headline_in_band():
    page = {"headline": "Why the checkout page decides more than the price"}  # 9 words
    assert find_headline_band_violation(page, "article", (8, 14)) == []


def test_headline_band_violation_flags_a_short_article_headline():
    page = {"headline": "Three reasons"}  # 2 words
    violations = find_headline_band_violation(page, "article", (8, 14))
    assert violations and violations[0]["key"] == "simplicity:headline_band"
    assert "2 word" in violations[0]["issue"]


def test_headline_band_violation_reads_longforms_nested_hero_headline():
    page = {"hero": {"headline": "The hidden cost of a hidden price"}}  # 7 words
    assert find_headline_band_violation(page, "longform", (6, 12)) == []
    assert find_headline_band_violation(page, "longform", (8, 14)) != []


def test_headline_band_violation_reads_product_pages_hero_promise():
    page = {"hero": {"promise": "Relief without leaving your backyard"}}  # 5 words
    assert find_headline_band_violation(page, "product-page", (4, 10)) == []
    page_short = {"hero": {"promise": "Relief now"}}  # 2 words
    assert find_headline_band_violation(page_short, "product-page", (4, 10)) != []


def test_headline_band_violation_noop_without_a_band():
    assert find_headline_band_violation({"headline": "x"}, "article", None) == []


# ---------------------------------------------------------------------------
# find_above_fold_link_violations -- above-the-fold region per cartridge.
# ---------------------------------------------------------------------------

def test_above_fold_links_product_page_passes_with_only_the_one_cta_url():
    page = {"cta_url": "https://example.com/products/x", "hero": {"promise": "Relief tonight", "hero_image": {"asset_id": "a"}}}
    assert find_above_fold_link_violations(page, "product-page") == []


def test_above_fold_links_product_page_flags_a_second_link_in_the_hero():
    page = {
        "cta_url": "https://example.com/products/x",
        "hero": {"promise": "Relief tonight", "secondary": {"url": "https://example.com/other"}},
    }
    violations = find_above_fold_link_violations(page, "product-page")
    assert violations and violations[0]["key"] == "simplicity:above_fold_links"
    assert "2 distinct links" in violations[0]["issue"]


def test_above_fold_links_longform_flags_a_second_link_in_the_hero():
    page = {
        "cta_url": "https://example.com/products/x",
        "hero": {"headline": "x", "secondary": {"cta_url": "https://example.com/other"}},
    }
    assert find_above_fold_link_violations(page, "longform") != []


def test_above_fold_links_article_passes_with_no_link_in_open():
    page = {"headline": "x", "dek": "y", "open": [{"text": "no link here"}]}
    assert find_above_fold_link_violations(page, "article") == []


def test_above_fold_links_article_flags_two_links_in_the_first_paragraph():
    page = {
        "headline": "x", "dek": "y",
        "open": [{"text": "see", "links": [{"url": "https://a"}, {"url": "https://b"}]}],
    }
    assert find_above_fold_link_violations(page, "article") != []
    # a second paragraph's link doesn't count -- only open[0] is above the fold.
    page_second_para = {
        "headline": "x", "dek": "y",
        "open": [{"text": "no link"}, {"text": "see", "links": [{"url": "https://a"}]}],
    }
    assert find_above_fold_link_violations(page_second_para, "article") == []


def test_above_fold_links_listicle_passes_with_only_the_hero_cta():
    # Cycle 41: listicle v0.2's fold is the header stack -- hero image, the
    # one primary CTA, and a renderer-built trust line that never appears in
    # page.json. One cta_url is exactly the one link allowed.
    page = {"headline": "x", "dek": "y", "hero": {"asset_id": "a"},
            "cta_url": "https://example.com/collections/all"}
    assert find_above_fold_link_violations(page, "listicle") == []


def test_above_fold_links_listicle_flags_a_second_link_in_the_header():
    page = {"headline": "x", "dek": "y", "cta_url": "https://example.com/collections/all",
            "hero": {"asset_id": "a", "url": "https://example.com/other"}}
    assert find_above_fold_link_violations(page, "listicle") != []


def test_above_fold_links_noop_for_an_undefined_cartridge():
    assert find_above_fold_link_violations({"anything": {"url": "https://a", "url2": "https://b"}}, "comparison") == []


# ---------------------------------------------------------------------------
# find_offer_element_violations -- second distinct CTA text / financing
# sentence. The SAME text repeating verbatim is fine (matches the existing
# CTA-allowlist rule's own "may repeat verbatim" language).
# ---------------------------------------------------------------------------

def test_offer_element_passes_a_single_repeated_cta_text():
    page = {"cta_text": "Shop the Fuji", "final_cta": {"cta_text": "Shop the Fuji"}}
    assert find_offer_element_violations(page, "longform") == []


def test_offer_element_flags_a_second_distinct_cta_text():
    page = {"cta_text": "Shop the Fuji", "final_cta": {"cta_text": "Buy now"}}
    violations = find_offer_element_violations(page, "longform")
    assert violations and violations[0]["key"] == "simplicity:offer_cta_text"


def test_offer_element_reads_articles_nested_cta_text():
    page = {"cta": {"text": "See the models"}, "financing_line": "unrelated string"}
    assert find_offer_element_violations(page, "article") == []
    page_bad = {"cta": {"text": "See the models"}, "close": {"cta_text": "Buy now"}}
    assert find_offer_element_violations(page_bad, "article") != []


def test_offer_element_passes_the_same_financing_sentence_repeated():
    # longform's own cartridge.md: financing_line may legitimately appear in
    # both hero and final_cta, same sentence verbatim.
    page = {
        "hero": {"financing_line": {"text": "Financing is available at checkout."}},
        "final_cta": {"financing_line": {"text": "Financing is available at checkout."}},
    }
    assert find_offer_element_violations(page, "longform") == []


def test_offer_element_flags_two_distinct_financing_sentences():
    page = {
        "hero": {"financing_line": {"text": "Financing is available at checkout."}},
        "final_cta": {"financing_line": "Ask about our 0% APR plan."},
    }
    violations = find_offer_element_violations(page, "longform")
    assert violations and violations[0]["key"] == "simplicity:offer_financing"


def test_offer_element_passes_a_page_with_no_financing_mention():
    assert find_offer_element_violations({"cta_text": "Shop the Fuji"}, "longform") == []


# ---------------------------------------------------------------------------
# find_value_equation_warnings -- soft, product-page only, never gates.
# ---------------------------------------------------------------------------

_VALUE_EQ_FACTS_PACK = {
    "verified_claims": [
        {"id": "gbrain-allowlist-red-light", "category": "trust"},
        {"id": "gbrain-allowlist-360-full-spectrum", "category": "spec"},
        {"id": "gbrain-allowlist-us-owned", "category": "trust"},
        {"id": "price-fuji", "category": "price"},
        {"id": "warranty-terms", "category": "trust"},
    ]
}


def test_value_equation_passes_three_distinct_non_transactional_claims():
    page = {"proof_bullets": [
        {"claim_ids": ["gbrain-allowlist-red-light"]},
        {"claim_ids": ["gbrain-allowlist-360-full-spectrum"]},
        {"claim_ids": ["gbrain-allowlist-us-owned"]},
    ]}
    assert find_value_equation_warnings(page, "product-page", _VALUE_EQ_FACTS_PACK) == []


def test_value_equation_flags_fewer_than_three_distinct_claim_ids():
    page = {"proof_bullets": [
        {"claim_ids": ["gbrain-allowlist-red-light"]},
        {"claim_ids": ["gbrain-allowlist-red-light"]},
        {"claim_ids": ["gbrain-allowlist-360-full-spectrum"]},
    ]}
    warnings = find_value_equation_warnings(page, "product-page", _VALUE_EQ_FACTS_PACK)
    assert any("distinct claim_id" in w for w in warnings)


def test_value_equation_flags_a_price_claim():
    page = {"proof_bullets": [
        {"claim_ids": ["price-fuji"]},
        {"claim_ids": ["gbrain-allowlist-360-full-spectrum"]},
        {"claim_ids": ["gbrain-allowlist-us-owned"]},
    ]}
    warnings = find_value_equation_warnings(page, "product-page", _VALUE_EQ_FACTS_PACK)
    assert any("price/warranty/shipping" in w for w in warnings)


def test_value_equation_flags_a_warranty_claim():
    page = {"proof_bullets": [
        {"claim_ids": ["warranty-terms"]},
        {"claim_ids": ["gbrain-allowlist-360-full-spectrum"]},
        {"claim_ids": ["gbrain-allowlist-us-owned"]},
    ]}
    warnings = find_value_equation_warnings(page, "product-page", _VALUE_EQ_FACTS_PACK)
    assert any("price/warranty/shipping" in w for w in warnings)


def test_value_equation_noop_for_non_product_page_cartridges():
    assert find_value_equation_warnings({"proof_bullets": []}, "longform", _VALUE_EQ_FACTS_PACK) == []


def test_value_equation_never_included_in_find_simplicity_violations():
    # item 4 stays soft in every mode -- find_simplicity_violations (the
    # hard-gate combiner) must never surface it.
    page = {
        "cta_text": "Shop the Fuji", "cta_url": "https://example.com/x",
        "hero": {"promise": "Relief tonight without leaving home"},
        "proof_bullets": [{"claim_ids": ["price-fuji"]}],
    }
    problems = find_simplicity_violations(page, "product-page")
    assert not any("value-equation" in p["issue"] for p in problems)


# ---------------------------------------------------------------------------
# find_simplicity_violations against the real fixtures (tests/test_render.py)
# -- confirms the bands/regions chosen actually fit real cartridge output.
# ---------------------------------------------------------------------------

def test_real_article_page_passes_the_simplicity_gate():
    assert find_simplicity_violations(ARTICLE_PAGE, "article") == []


def test_real_product_page_passes_the_simplicity_gate():
    assert find_simplicity_violations(PRODUCT_PAGE_PAGE, "product-page") == []


def test_real_longform_page_passes_the_simplicity_gate():
    assert find_simplicity_violations(LONGFORM_PAGE, "longform") == []


# ---------------------------------------------------------------------------
# warn vs enforce, wired into check_page_gates (same pattern
# tests/test_warmup.py uses for the warm-up gate).
# ---------------------------------------------------------------------------

def _gate_simplicity_problems(page, tenant, cartridge_name="product-page"):
    problems = check_page_gates(
        page, FACTS_PACK, cartridge_name,
        financing_lender=None, speaker_pov=AD_BRIEF["speaker_pov"],
        word_range=None, allowed_cta_texts=None, ad_brief=AD_BRIEF,
        tenant=tenant,
    )
    return [p for p in problems if p.get("key", "").startswith("simplicity:")]


def test_warn_mode_never_fails_check_page_gates_on_a_simplicity_violation():
    tenant = _FakeTenant(config={"simplicity_mode": "warn"})
    bad_page = dict(PRODUCT_PAGE_PAGE, hero=dict(PRODUCT_PAGE_PAGE["hero"], promise="Relief"))  # 1 word, band is 4-10
    assert _gate_simplicity_problems(bad_page, tenant) == []


def test_enforce_mode_is_a_hard_gate_failure_for_a_simplicity_violation():
    tenant = _FakeTenant(config={"simplicity_mode": "enforce"})
    bad_page = dict(PRODUCT_PAGE_PAGE, hero=dict(PRODUCT_PAGE_PAGE["hero"], promise="Relief"))  # 1 word, band is 4-10
    problems = _gate_simplicity_problems(bad_page, tenant)
    assert problems and problems[0]["key"] == "simplicity:headline_band"


def test_enforce_mode_passes_a_clean_page():
    tenant = _FakeTenant(config={"simplicity_mode": "enforce"})
    assert _gate_simplicity_problems(PRODUCT_PAGE_PAGE, tenant) == []


def test_default_mode_without_any_tenant_yaml_config_is_warn():
    # no simplicity_mode key at all -- the documented default (tenants/
    # _template/tenant.yaml) is "warn", same fallback find_warmup_violations
    # already uses for warmup_mode.
    tenant = _FakeTenant(config={})
    bad_page = dict(PRODUCT_PAGE_PAGE, hero=dict(PRODUCT_PAGE_PAGE["hero"], promise="Relief"))
    assert _gate_simplicity_problems(bad_page, tenant) == []


# ---------------------------------------------------------------------------
# simplicity_review_lines -- REVIEW.md's "Simplicity" section.
# ---------------------------------------------------------------------------

def test_simplicity_review_lines_reports_pass_for_clean_pages():
    pages = {"product-page": PRODUCT_PAGE_PAGE, "article": ARTICLE_PAGE, "longform": LONGFORM_PAGE}
    lines = simplicity_review_lines(pages, REAL_TENANT, FACTS_PACK)
    text = "\n".join(lines)
    assert text.startswith("Mode: warn")
    assert "### product-page" in text and "### article" in text and "### longform" in text
    assert "WARN" not in "\n".join(
        line for line in lines if "Links above the fold" in line or "Headline word band" in line or "One offer element" in line
    )


def test_simplicity_review_lines_reports_warn_for_a_violation():
    bad_page = dict(PRODUCT_PAGE_PAGE, hero=dict(PRODUCT_PAGE_PAGE["hero"], promise="Relief"))
    lines = simplicity_review_lines({"product-page": bad_page}, REAL_TENANT, FACTS_PACK)
    band_line = next(line for line in lines if line.startswith("- Headline word band"))
    assert "WARN" in band_line


def test_simplicity_review_lines_only_shows_value_equation_for_product_page():
    lines = simplicity_review_lines({"article": ARTICLE_PAGE}, REAL_TENANT, FACTS_PACK)
    assert not any("Value-equation" in line for line in lines)
    lines = simplicity_review_lines({"product-page": PRODUCT_PAGE_PAGE}, REAL_TENANT, FACTS_PACK)
    assert any("Value-equation" in line for line in lines)
