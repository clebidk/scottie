import pytest

from adv.claims import (
    ClaimsGateFailure,
    find_first_person_violations,
    find_forbidden_terms,
    find_forbidden_visible_text,
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


def test_trigger_word_check_is_word_boundary_not_substring():
    # Regression from the hidden-costs-v2 verification run: "frustrated"
    # contains "rated" as a substring and must not require a claim_id.
    page = {"open": [{"text": "She came away more frustrated than when she started."}]}
    problems = validate_page_claim_ids(page, {"price-fuji"})
    assert problems == []


def test_digit_in_a_customer_quote_does_not_need_a_claim_id():
    # Regression from the hidden-costs-v2 verification run: the ad's own
    # dialogue ("It's 2026, I don't want to talk to anyone...") quoted
    # verbatim and attributed to a customer isn't the author's own factual
    # assertion -- it shouldn't need a claim_id just because it has a digit.
    page = {"open": [{"text": '"It\'s 2026," she told us. "I don\'t want to talk to anyone."'}]}
    problems = validate_page_claim_ids(page, {"price-fuji"})
    assert problems == []


def test_digit_outside_a_quote_still_needs_a_claim_id():
    page = {"open": [{"text": "She said, \"thanks.\" It costs $8,250 up front."}]}
    problems = validate_page_claim_ids(page, {"price-fuji"})
    assert len(problems) == 1


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


def test_find_forbidden_terms_ignores_emf_in_urls_and_asset_ids():
    # Regression: the Fuji product URL/handle and its derived asset ids
    # literally contain "emf" -- fix 4 says the URL may still contain the
    # word; that's fine, since it's never rendered as page copy.
    page = {
        "hero": {
            "cta": {"url": "https://peaksaunas.com/products/peak-saunas-fuji-near-zero-emf-sauna"},
            "hero_image": {"asset_id": "asset-peak-saunas-fuji-near-zero-emf-sauna-1"},
        }
    }
    assert find_forbidden_terms(page) == []


def test_find_forbidden_terms_ignores_emf_in_an_inline_citation_url():
    # Regression: the article cartridge cites sources inline as
    # "(source, year, <url>)" inside a prose "text" field -- the product URL
    # there also contains "emf". Only the URL substring is exempt; the
    # surrounding prose is still scanned.
    page = {
        "body_sections": [
            {
                "paragraphs": [
                    {
                        "text": "Peak runs on a 120V/20A outlet (Peak Saunas, 2026, "
                        "https://peaksaunas.com/products/peak-saunas-fuji-2-person-near-zero-emf-full-spectrum-infrared-sauna)."
                    }
                ]
            }
        ]
    }
    assert find_forbidden_terms(page) == []


def test_find_forbidden_terms_still_catches_emf_outside_a_url():
    page = {"body_sections": [{"paragraphs": [{"text": "This sauna has near-zero EMF, unlike competitors."}]}]}
    hits = find_forbidden_terms(page)
    assert any(h["term"] == "emf" for h in hits)


def test_gate_page_json_stops_on_emf_even_with_valid_claim_ids():
    page = {"proof_bullets": [{"label": "EMF", "text": "Near-zero EMF.", "claim_ids": ["price-fuji"]}]}
    with pytest.raises(ClaimsGateFailure) as exc_info:
        gate_page_json(page, FACTS_PACK, "product-page")
    assert exc_info.value.stage == "page_json:product-page"


# ---------------------------------------------------------------------------
# fix cycle 2 item 9: rendered-HTML visible-text EMF gate -- href/src are
# exempt (they disappear with the tag), but visible link/prose text is not.
# ---------------------------------------------------------------------------

def test_find_forbidden_visible_text_passes_when_emf_only_in_an_href():
    html = (
        '<p>See the <a href="https://peaksaunas.com/products/peak-saunas-fuji-near-zero-emf-sauna">'
        "Peak Saunas product page</a> for specs.</p>"
    )
    assert find_forbidden_visible_text(html) == []


def test_find_forbidden_visible_text_fails_when_emf_is_in_visible_text():
    html = (
        '<p>Source: (Peak Saunas, 2026, '
        "https://peaksaunas.com/products/peak-saunas-fuji-near-zero-emf-sauna)</p>"
    )
    hits = find_forbidden_visible_text(html)
    assert any(h["term"] == "emf" for h in hits)


def test_find_forbidden_visible_text_ignores_script_and_style_content():
    html = (
        '<script type="application/ld+json">{"emf": "electromagnetic test data"}</script>'
        "<style>.emf-badge { color: red; }</style>"
        "<p>Clean visible copy with no forbidden terms.</p>"
    )
    assert find_forbidden_visible_text(html) == []


def test_find_forbidden_visible_text_catches_electromagnetic():
    html = "<p>Our sauna emits almost no electromagnetic field.</p>"
    hits = find_forbidden_visible_text(html)
    assert any(h["term"] == "electromagnetic" for h in hits)


# ---------------------------------------------------------------------------
# fix cycle 2 item 11: first-person attribution -- the page author never
# speaks in the ad speaker's first person when speaker_pov is first_person.
# ---------------------------------------------------------------------------

def test_find_first_person_violations_catches_i_verb_construction():
    page = {"open": [{"text": "I ran into this over and over."}]}
    hits = find_first_person_violations(page, "first_person")
    assert len(hits) == 1
    assert hits[0]["path"] == "$.open[0].text"


def test_find_first_person_violations_noop_when_not_first_person():
    page = {"open": [{"text": "I ran into this over and over."}]}
    assert find_first_person_violations(page, "brand") == []


def test_find_first_person_violations_allows_attributed_customer_story():
    page = {"open": [{"text": "One customer told us she ran into this over and over."}]}
    assert find_first_person_violations(page, "first_person") == []


def test_find_first_person_violations_ignores_quoted_testimonial():
    page = {"social_proof": {"quotes": ["I ran into this over and over, until Peak."]}}
    assert find_first_person_violations(page, "first_person") == []


def test_find_first_person_violations_ignores_inline_quotation_mark_span():
    # Regression from the hidden-costs-v2 verification run: a customer's
    # attributed, quoted line inside an ordinary paragraph (not a structural
    # "quotes" container) must not be flagged -- only the author speaking
    # outside quotation marks should be.
    page = {
        "open": [
            {
                "text": (
                    '"It\'s 2026," she said. "I don\'t want to talk to anyone. '
                    'Just tell me how much this costs." She wasn\'t shopping for a car.'
                )
            }
        ]
    }
    assert find_first_person_violations(page, "first_person") == []


def test_find_first_person_violations_still_catches_i_verb_outside_quotes():
    page = {"open": [{"text": 'She said, "thanks." I ran into this over and over.'}]}
    hits = find_first_person_violations(page, "first_person")
    assert len(hits) == 1


def test_gate_page_json_stops_on_first_person_leak():
    page = {"open": [{"text": "I ran into this over and over."}]}
    with pytest.raises(ClaimsGateFailure) as exc_info:
        gate_page_json(page, FACTS_PACK, "article", speaker_pov="first_person")
    assert exc_info.value.stage == "page_json:article"
