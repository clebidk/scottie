import pytest

from adv.claims import ClaimsGateFailure, gate_ad_brief_claims, gate_page_json, validate_page_claim_ids

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
