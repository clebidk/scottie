import pytest

from adv.claims import (
    ClaimsGateFailure,
    find_forbidden_terms,
    gate_ad_brief_claims,
    gate_page_json,
    validate_page_claim_ids,
)

VERIFIED_CLAIMS = [
    {"id": "price-fuji", "text": "The Peak Saunas Fuji is priced at $8250.00 (list/compare-at $14032.00).", "category": "price", "source": "https://peaksaunas.com/products/fuji"},
    {"id": "founder-ceo", "text": "Austin Laudenslager is the Founder & CEO of Peak Saunas.", "category": "trust", "source": "https://peaksaunas.com/pages/austin-laudenslager"},
]

# gate_page_json still takes a facts_pack dict (page.json can only cite what
# the writer was actually given).
FACTS_PACK = {"verified_claims": VERIFIED_CLAIMS}


def test_matched_ad_claim_passes():
    ad_brief = {"claims_made": ["The Peak Saunas Fuji is priced at $8250."]}
    matched = gate_ad_brief_claims(ad_brief, VERIFIED_CLAIMS)
    assert len(matched) == 1
    assert matched[0]["matched_claim_id"] == "price-fuji"


def test_matched_ad_claim_with_comma_formatted_price():
    # "$8,250" (ad phrasing) must normalize the same as "$8250.00" (verified.json).
    ad_brief = {"claims_made": ["The Peak Saunas Fuji is priced at $8,250."]}
    matched = gate_ad_brief_claims(ad_brief, VERIFIED_CLAIMS)
    assert len(matched) == 1
    assert matched[0]["matched_claim_id"] == "price-fuji"


def test_unmatched_ad_claim_stops():
    ad_brief = {"claims_made": ["Competitor saunas leak dangerous levels of EMF radiation."]}
    with pytest.raises(ClaimsGateFailure) as exc_info:
        gate_ad_brief_claims(ad_brief, VERIFIED_CLAIMS)
    assert exc_info.value.stage == "ad_claims"
    assert len(exc_info.value.items) == 1


def test_speaker_experience_never_gated():
    # speaker_experience isn't passed to the gate at all -- claims_made only.
    ad_brief = {"claims_made": [], "speaker_experience": ["I hated calling for a price."]}
    matched = gate_ad_brief_claims(ad_brief, VERIFIED_CLAIMS)
    assert matched == []


def test_page_json_text_with_dollar_and_no_claim_ids_rejected():
    page = {"hero": {"price_line": {"text": "$8,250, compare at $14,032."}}}
    problems = validate_page_claim_ids(page, {"price-fuji"})
    assert len(problems) == 1
    assert "needs at least one claim_id" in problems[0]["issue"]


def test_page_json_text_with_dollar_and_claim_ids_accepted():
    page = {"hero": {"price_line": {"text": "$8,250, compare at $14,032.", "claim_ids": ["price-fuji"]}}}
    problems = validate_page_claim_ids(page, {"price-fuji"})
    assert problems == []


def test_page_json_unknown_claim_id_rejected():
    page = {"hero": {"price_line": {"text": "$8,250.", "claim_ids": ["price-does-not-exist"]}}}
    problems = validate_page_claim_ids(page, {"price-fuji"})
    assert len(problems) == 1
    assert "does not exist" in problems[0]["issue"]


def test_gate_page_json_raises_claims_gate_failure():
    page = {"hero": {"price_line": {"text": "$8,250."}}}
    with pytest.raises(ClaimsGateFailure) as exc_info:
        gate_page_json(page, FACTS_PACK, "product-page")
    assert exc_info.value.stage == "page_json:product-page"


def test_plain_text_without_trigger_needs_no_claim_ids():
    page = {"open": [{"text": "Shopping used to mean waiting for a callback."}]}
    problems = validate_page_claim_ids(page, {"price-fuji"})
    assert problems == []


# ---------------------------------------------------------------------------
# fix 7: an ad claim's numeric tokens must also appear in the verified claim
# ---------------------------------------------------------------------------

def test_ad_claim_with_wrong_number_does_not_match_despite_word_overlap():
    # Same words as price-fuji ("The Peak Saunas Fuji is priced at $...."),
    # different number -- must NOT match on word overlap alone.
    ad_brief = {"claims_made": ["The Peak Saunas Fuji is priced at $9,750."]}
    with pytest.raises(ClaimsGateFailure) as exc_info:
        gate_ad_brief_claims(ad_brief, VERIFIED_CLAIMS)
    assert exc_info.value.stage == "ad_claims"


def test_ad_claim_with_no_numbers_is_unaffected_by_numeric_check():
    ad_brief = {"claims_made": ["Austin Laudenslager is the Founder and CEO of Peak Saunas."]}
    matched = gate_ad_brief_claims(ad_brief, VERIFIED_CLAIMS)
    assert matched[0]["matched_claim_id"] == "founder-ceo"


# ---------------------------------------------------------------------------
# fix 4 / fix 6: forbidden terms in page.json (EMF, competitor trademark,
# discontinued models, financing lender names when none is configured)
# ---------------------------------------------------------------------------

def test_find_forbidden_terms_catches_emf_case_insensitive():
    page = {"body_sections": [{"paragraphs": [{"text": "Our sauna has Near-Zero emf, tested internally."}]}]}
    hits = find_forbidden_terms(page)
    assert any(h["term"] == "emf" for h in hits)


def test_find_forbidden_terms_catches_sunlighten_and_discontinued_models():
    page = {"open": [{"text": "Unlike Sunlighten, unlike the Crown, unlike Olympus or Aspen, we ship free."}]}
    hits = find_forbidden_terms(page)
    terms_hit = {h["term"] for h in hits}
    assert terms_hit == {"sunlighten", "crown", "olympus", "aspen"}


def test_find_forbidden_terms_catches_lender_name_when_lender_not_configured():
    page = {"hero": {"financing_line": {"text": "Get it from est. $229/mo with Bread Pay"}}}
    hits = find_forbidden_terms(page, financing_lender=None)
    assert any(h["term"] == "bread pay" for h in hits)


def test_find_forbidden_terms_allows_the_configured_lender_name():
    page = {"hero": {"financing_line": {"text": "Get it from est. $229/mo with Affirm"}}}
    hits = find_forbidden_terms(page, financing_lender="Affirm")
    assert hits == []


def test_gate_page_json_stops_on_emf_even_with_valid_claim_ids():
    page = {"proof_bullets": [{"label": "EMF", "text": "Near-zero EMF.", "claim_ids": ["price-fuji"]}]}
    with pytest.raises(ClaimsGateFailure) as exc_info:
        gate_page_json(page, FACTS_PACK, "product-page")
    assert exc_info.value.stage == "page_json:product-page"
