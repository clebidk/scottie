import pytest

from harness.claims import (
    ClaimsGateFailure,
    alias_match,
    classify_ad_claim_about,
    classify_locked_topic,
    evaluate_financing_claim,
    find_benefit_claim_shortfall,
    find_financing_violations,
    find_first_person_violations,
    find_forbidden_terms,
    find_forbidden_visible_text,
    find_leaked_claim_ids,
    find_leaked_claim_ids_visible_text,
    find_missing_attribution,
    find_proof_stats_violations,
    find_second_cta_violation,
    find_warranty_violations,
    gate_ad_brief_claims,
    gate_page_json,
    match_claim,
    speaker_numbers,
    strip_leaked_claim_ids,
    validate_page_claim_ids,
)
from harness.vocab import ALLOWED_WARRANTY_SENTENCE

VERIFIED_CLAIMS = [
    {"id": "price-fuji", "text": "The Peak Saunas Fuji is priced at $8250.00 (list/compare-at $14032.00).", "category": "price", "source": "https://peaksaunas.com/products/fuji"},
    {"id": "founder-ceo", "text": "Austin Laudenslager is the Founder & CEO of Peak Saunas.", "category": "trust", "source": "https://peaksaunas.com/pages/austin-laudenslager"},
]

# Fix cycle 10 item 2: a price ad claim now matches on the numeric anchor
# against this run's already-picked product's current price, not word
# overlap against a static claims/verified.json price entry -- these tests
# pass this fake "picked product" the same way cli.cmd_run does after fix
# cycle 10 item 1's reordering.
FUJI_PRODUCT = {"slug": "fuji", "name": "Fuji", "price": 8250.00}

# gate_page_json still takes a facts_pack dict (page.json can only cite what
# the writer was actually given).
FACTS_PACK = {"verified_claims": VERIFIED_CLAIMS}


def test_matched_ad_claim_passes():
    # Fix cycle 10 item 2: a dollar-amount ad claim matches on the numeric
    # anchor against the picked product's current price now, not word
    # overlap -- pass `product` the way cli.cmd_run does.
    ad_brief = {"claims_made": ["The Peak Saunas Fuji is priced at $8250."]}
    matched, overclaims, alt_claims = gate_ad_brief_claims(ad_brief, VERIFIED_CLAIMS, product=FUJI_PRODUCT)
    assert overclaims == []
    assert len(matched) == 1
    assert matched[0]["matched_claim_id"] == "price-fuji"


def test_matched_ad_claim_with_comma_formatted_price():
    # "$8,250" (ad phrasing) must parse the same as "$8250.00" (product price).
    ad_brief = {"claims_made": ["The Peak Saunas Fuji is priced at $8,250."]}
    matched, overclaims, alt_claims = gate_ad_brief_claims(ad_brief, VERIFIED_CLAIMS, product=FUJI_PRODUCT)
    assert overclaims == []
    assert len(matched) == 1
    assert matched[0]["matched_claim_id"] == "price-fuji"


def test_unmatched_ad_claim_stops():
    ad_brief = {"claims_made": ["Peak Saunas ships every order within two business days."]}
    with pytest.raises(ClaimsGateFailure) as exc_info:
        gate_ad_brief_claims(ad_brief, VERIFIED_CLAIMS)
    assert exc_info.value.stage == "ad_claims"
    assert len(exc_info.value.items) == 1


def test_speaker_experience_never_gated():
    # speaker_experience isn't passed to the gate at all -- claims_made only.
    ad_brief = {"claims_made": [], "speaker_experience": ["I hated calling for a price."]}
    matched, overclaims, alt_claims = gate_ad_brief_claims(ad_brief, VERIFIED_CLAIMS)
    assert matched == []
    assert overclaims == []


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


def test_trigger_word_check_ignores_hyphenated_compounds():
    # Cycle 43 regression: a plain \b boundary treats a hyphen the same as a
    # space, so "outdoor-rated"/"top-rated" tripped the "rated" trigger word
    # even though neither is the standalone word -- fixed in
    # vocab.Vocabulary.trigger_word_re. No digit in either sentence, so this
    # isolates the trigger-word check from the separate "contains a number"
    # rule.
    page = {"open": [{"text": "The cable is outdoor-rated and the fan housing is top-rated for noise."}]}
    problems = validate_page_claim_ids(page, {"price-fuji"})
    assert problems == []


def test_trigger_word_check_still_catches_a_standalone_rated():
    page = {"open": [{"text": "Owners say it is rated for daily use by the manufacturer."}]}
    problems = validate_page_claim_ids(page, {"price-fuji"})
    assert len(problems) == 1
    assert 'uses the word "rated"' in problems[0]["issue"]


def test_trigger_word_check_still_catches_top_rated_with_a_space():
    page = {"open": [{"text": "It is consistently top rated among home units like it."}]}
    problems = validate_page_claim_ids(page, {"price-fuji"})
    assert len(problems) == 1
    assert 'uses the word "rated"' in problems[0]["issue"]


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
    # a number that's neither the picked product's price -- must NOT match.
    ad_brief = {"claims_made": ["The Peak Saunas Fuji is priced at $9,750."]}
    with pytest.raises(ClaimsGateFailure) as exc_info:
        gate_ad_brief_claims(ad_brief, VERIFIED_CLAIMS, product=FUJI_PRODUCT)
    assert exc_info.value.stage == "ad_claims"
    assert exc_info.value.items[0]["message"] == "quoted price $9,750 matches no current product price"


def test_ad_claim_with_no_numbers_is_unaffected_by_numeric_check():
    ad_brief = {"claims_made": ["Austin Laudenslager is the Founder and CEO of Peak Saunas."]}
    matched, overclaims, alt_claims = gate_ad_brief_claims(ad_brief, VERIFIED_CLAIMS)
    assert overclaims == []
    assert matched[0]["matched_claim_id"] == "founder-ceo"


# ---------------------------------------------------------------------------
# Fix cycle 9 item 4: a small synonym normalization (protected -> protective,
# delivery -> shipping) so "crate-protected delivery" overlaps cleanly with
# claims/verified.json's actual wording ("... a PeakGuard custom protective
# wooden crate ... Free shipping ..."), instead of STOPping at 0.333 overlap
# (below the 0.6 threshold) purely over word choice. The numeric-token rule
# is untouched -- neither claim carries a number.
# ---------------------------------------------------------------------------

SHIPPING_CRATE_CLAIM = [
    {
        "id": "gbrain-shipping-free-crate-origin",
        "text": (
            "Free shipping, always (continental US). Ships in a PeakGuard custom protective "
            "wooden crate from the Ontario, California warehouse via freight/LTL "
            "(no single primary carrier -- a large carrier network)."
        ),
        "category": "policy",
        "source": "https://peaksaunas.com/policies/shipping-policy",
    }
]


def test_crate_protected_delivery_matches_the_verified_shipping_claim():
    ad_brief = {"claims_made": ["Crate-protected delivery"]}
    matched, overclaims, alt_claims = gate_ad_brief_claims(ad_brief, SHIPPING_CRATE_CLAIM)
    assert overclaims == []
    assert matched[0]["matched_claim_id"] == "gbrain-shipping-free-crate-origin"
    assert matched[0]["overlap"] == 1.0


def test_crate_protected_delivery_fails_without_the_synonym_mapping():
    # Sanity check on the regression itself: without the synonym mapping,
    # "protected"/"delivery" share no token with "protective"/"shipping" and
    # overlap is exactly 1/3 (only "crate" matches) -- below the 0.6 gate.
    from harness.claims import normalize, overlap_ratio

    ad_tokens = ["crate", "protected", "delivery"]  # pre-synonym tokens
    verified_tokens = normalize(SHIPPING_CRATE_CLAIM[0]["text"])
    assert overlap_ratio(ad_tokens, verified_tokens) == pytest.approx(1 / 3)


def test_synonym_normalization_does_not_create_false_matches():
    # "protected"/"delivery" only ever fold to "protective"/"shipping" -- an
    # unrelated claim about neither still doesn't match.
    ad_brief = {"claims_made": ["Crate-protected delivery"]}
    with pytest.raises(ClaimsGateFailure):
        gate_ad_brief_claims(ad_brief, VERIFIED_CLAIMS)


# Fix cycle 9 item 4 (second half): a short ad claim (<=3 content tokens) is
# held to a lower 0.5 overlap bar instead of the general 0.6 -- observed live
# on fixtures/product-features-v2.mov: "It has Bluetooth capabilities" (2
# content tokens: bluetooth, capabilities) only overlaps 0.5 against the
# verified claim's "...Two HiFi Bluetooth speakers..." (shares "bluetooth",
# not "capabilities"/"speakers") and was STOPping despite the underlying fact
# being true and sourced.
BLUETOOTH_SPEAKER_CLAIM = [
    {
        "id": "pdp-mini-speakers",
        "text": "Two HiFi Bluetooth speakers, dual-level LED accent lighting, built-in chromotherapy.",
        "category": "spec",
        "source": "https://peaksaunas.com/products/mini",
    }
]


def test_short_ad_claim_matches_at_the_lower_threshold():
    ad_brief = {"claims_made": ["It has Bluetooth capabilities"]}
    matched, overclaims, alt_claims = gate_ad_brief_claims(ad_brief, BLUETOOTH_SPEAKER_CLAIM)
    assert overclaims == []
    assert matched[0]["matched_claim_id"] == "pdp-mini-speakers"
    assert matched[0]["overlap"] == 0.5


def test_longer_ad_claim_still_needs_the_general_threshold():
    # Same claim, worded with more (non-overlapping) content tokens -- still
    # only 1 shared token ("bluetooth") out of more than 3, so overlap drops
    # below even the 0.5 short-claim bar and this must still STOP.
    ad_brief = {"claims_made": ["It has some really nice Bluetooth capabilities apparently"]}
    with pytest.raises(ClaimsGateFailure):
        gate_ad_brief_claims(ad_brief, BLUETOOTH_SPEAKER_CLAIM)


def test_short_claim_threshold_does_not_match_on_a_single_generic_word():
    # A 1-token claim sharing one common word with an unrelated verified
    # claim must not spuriously pass at the lower bar.
    ad_brief = {"claims_made": ["Speakers"]}
    with pytest.raises(ClaimsGateFailure):
        gate_ad_brief_claims(ad_brief, VERIFIED_CLAIMS)


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
    # Cycle 18 made Bread Pay the tenant's configured lender (and dropped it
    # from vocab.yaml's forbidden list), so this now exercises a lender that
    # is still forbidden: Affirm.
    page = {"hero": {"financing_line": {"text": "Get it from est. $229/mo with Affirm"}}}
    hits = find_forbidden_terms(page, financing_lender=None)
    assert any(h["term"] == "affirm" for h in hits)


def test_find_forbidden_terms_allows_the_configured_lender_name():
    page = {"hero": {"financing_line": {"text": "Get it from est. $229/mo with Affirm"}}}
    hits = find_forbidden_terms(page, financing_lender="Affirm")
    assert hits == []


# ---------------------------------------------------------------------------
# Fix cycle 7 item 2: implied claims -- inferring a second, unverified fact
# from a verified one (e.g. "US-owned" implying "domestic support, not a
# call center"). Forbidden unless a verified claim's own text carries the
# phrase.
# ---------------------------------------------------------------------------

def test_find_forbidden_terms_catches_implied_support_location_claim():
    # The actual bug that triggered this fix: "US-owned, so the person
    # you'd reach is domestic, not a call center reading a script."
    page = {"proof_bullets": [{"text": "US-owned, so the person you'd reach is domestic, not a call center reading a script."}]}
    hits = find_forbidden_terms(page, verified_claims=VERIFIED_CLAIMS)
    assert any(h["term"] == "call center" for h in hits)


def test_find_forbidden_terms_catches_other_implied_claim_terms():
    for term in ("domestic support", "us-based support", "american-made", "made in the usa"):
        page = {"open": [{"text": f"Peak Saunas offers {term}."}]}
        hits = find_forbidden_terms(page, verified_claims=VERIFIED_CLAIMS)
        assert any(h["term"] == term for h in hits), term


def test_find_forbidden_terms_allows_an_implied_claim_term_when_a_verified_claim_states_it():
    verified = VERIFIED_CLAIMS + [
        {"id": "gbrain-support-domestic", "text": "Support calls are answered by domestic support staff.",
         "category": "trust", "source": "gbrain:policy/support"}
    ]
    page = {"open": [{"text": "Peak Saunas offers domestic support."}]}
    hits = find_forbidden_terms(page, verified_claims=verified)
    assert not any(h["term"] == "domestic support" for h in hits)


def test_find_forbidden_terms_implied_claim_terms_default_forbidden_with_no_verified_claims_arg():
    # verified_claims defaults to None -- the terms stay forbidden rather
    # than silently passing through when a caller doesn't pass it.
    page = {"open": [{"text": "Made in the USA, every unit."}]}
    hits = find_forbidden_terms(page)
    assert any(h["term"] == "made in the usa" for h in hits)


def test_find_forbidden_terms_ignores_emf_in_top_level_cta_url():
    # Regression: fix cycle 3 item 4 flattened the writer's per-block
    # {"cta": {"url": ...}} into a single top-level "cta_url" string --
    # it must stay exempt the same way the old nested "url" key was, since
    # the real Fuji product URL/handle contains "near-zero-emf".
    page = {"cta_url": "https://peaksaunas.com/products/peak-saunas-fuji-near-zero-emf-sauna"}
    assert find_forbidden_terms(page) == []


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


# ---------------------------------------------------------------------------
# fix cycle 3 item 3: product substance -- each cartridge's persuasive
# section needs a minimum count of product-benefit claim_ids, excluding
# price/shipping/warranty/returns by id even though shipping/warranty/returns
# share the "trust" category that would otherwise qualify.
# ---------------------------------------------------------------------------

BENEFIT_FACTS_PACK = {
    "verified_claims": VERIFIED_CLAIMS
    + [
        {"id": "warranty-terms", "text": "warranty text", "category": "trust", "source": "https://peaksaunas.com/pages/warranty"},
        {"id": "shipping-policy", "text": "shipping text", "category": "trust", "source": "https://peaksaunas.com/policies/shipping-policy"},
        {"id": "returns-policy", "text": "returns text", "category": "trust", "source": "https://peaksaunas.com/policies/refund-policy"},
        {"id": "benefit-red-light", "text": "Medical-grade red light therapy.", "category": "trust", "source": "https://peaksaunas.com/products/fuji"},
        {"id": "benefit-full-spectrum", "text": "Full spectrum infrared.", "category": "spec", "source": "https://peaksaunas.com/products/fuji"},
        {"id": "benefit-us-owned", "text": "US-owned company.", "category": "trust", "source": "https://peaksaunas.com/pages/austin-laudenslager"},
    ]
}


def test_find_benefit_claim_shortfall_ignores_price_shipping_warranty_returns():
    # Three bullets, but all of them are the excluded transactional ids --
    # zero benefit claims, well below product-page's minimum of 3.
    page = {
        "proof_bullets": [
            {"text": "a", "claim_ids": ["price-fuji"]},
            {"text": "b", "claim_ids": ["warranty-terms"]},
            {"text": "c", "claim_ids": ["shipping-policy"]},
        ]
    }
    problems = find_benefit_claim_shortfall(page, BENEFIT_FACTS_PACK, "product-page")
    assert len(problems) == 1
    assert "only 0 product-benefit claim_id" in problems[0]["issue"]


def test_find_benefit_claim_shortfall_passes_with_enough_benefit_claims():
    page = {
        "proof_bullets": [
            {"text": "a", "claim_ids": ["benefit-red-light"]},
            {"text": "b", "claim_ids": ["benefit-full-spectrum"]},
            {"text": "c", "claim_ids": ["benefit-us-owned"]},
        ]
    }
    assert find_benefit_claim_shortfall(page, BENEFIT_FACTS_PACK, "product-page") == []


def test_find_benefit_claim_shortfall_only_looks_at_the_persuasive_section():
    # A specs_table full of benefit claim_ids doesn't count -- only
    # proof_bullets (product-page) / how_it_works (longform) /
    # turn_section.criteria (article) do.
    page = {
        "specs_table": [{"label": "x", "value": "y", "claim_id": "benefit-red-light"}],
        "proof_bullets": [],
    }
    problems = find_benefit_claim_shortfall(page, BENEFIT_FACTS_PACK, "product-page")
    assert len(problems) == 1


def test_find_benefit_claim_shortfall_article_minimum_is_one():
    page = {"turn_section": {"criteria": [{"text": "x", "claim_ids": ["benefit-red-light"]}]}}
    assert find_benefit_claim_shortfall(page, BENEFIT_FACTS_PACK, "article") == []


def test_find_benefit_claim_shortfall_noop_for_unlisted_cartridge():
    assert find_benefit_claim_shortfall({}, BENEFIT_FACTS_PACK, "some-other-cartridge") == []


def test_gate_page_json_stops_on_benefit_claim_shortfall():
    page = {
        "proof_bullets": [{"text": "a", "claim_ids": ["warranty-terms"]}],
    }
    with pytest.raises(ClaimsGateFailure) as exc_info:
        gate_page_json(page, BENEFIT_FACTS_PACK, "product-page")
    assert exc_info.value.stage == "page_json:product-page"


# ---------------------------------------------------------------------------
# fix cycle 5: a claim id belongs only in a node's own claim_ids/claim_id
# field -- never inline in prose. Observed twice in run
# 20260909-2021-hidden-costs-v2: "the Peak Fuji 2-Person Infrared Sauna
# (spec-fuji-capacity), which is priced at $8,250".
# ---------------------------------------------------------------------------

LEAK_FACTS_PACK = {
    "verified_claims": VERIFIED_CLAIMS
    + [
        {"id": "spec-fuji-capacity", "text": "The Peak Fuji seats 2 people.", "category": "spec", "source": "https://peaksaunas.com/products/fuji"},
    ]
}

_LEAKED_TEXT = "the Peak Fuji 2-Person Infrared Sauna (spec-fuji-capacity), which is priced at $8,250"


def test_find_leaked_claim_ids_catches_parenthesized_id_in_prose():
    page = {"hero": {"promise": {"text": _LEAKED_TEXT, "claim_ids": ["price-fuji"]}}}
    hits = find_leaked_claim_ids(page, {"spec-fuji-capacity", "price-fuji"})
    assert len(hits) == 1
    assert hits[0]["issue"] == "claim id leaked into copy: spec-fuji-capacity in $.hero.promise.text"


def test_find_leaked_claim_ids_ignores_ids_in_claim_ids_field():
    page = {"hero": {"promise": {"text": "The Peak Fuji, which is priced at $8,250", "claim_ids": ["price-fuji", "spec-fuji-capacity"]}}}
    assert find_leaked_claim_ids(page, {"spec-fuji-capacity", "price-fuji"}) == []


def test_gate_page_json_stops_on_leaked_claim_id_in_prose():
    # cartridge_name isn't in MIN_BENEFIT_CLAIMS/_BENEFIT_SECTION_GETTERS, so
    # find_benefit_claim_shortfall no-ops and this isolates the leak check.
    page = {"hero": {"promise": {"text": _LEAKED_TEXT, "claim_ids": ["price-fuji", "spec-fuji-capacity"]}}}
    with pytest.raises(ClaimsGateFailure) as exc_info:
        gate_page_json(page, LEAK_FACTS_PACK, "some-other-cartridge")
    assert exc_info.value.stage == "page_json:some-other-cartridge"
    assert any("claim id leaked into copy: spec-fuji-capacity" in p["issue"] for p in exc_info.value.items)


def test_gate_page_json_passes_when_claim_id_only_in_claim_ids_field():
    page = {"hero": {"promise": {"text": "The Peak Fuji, which is priced at $8,250", "claim_ids": ["price-fuji", "spec-fuji-capacity"]}}}
    # Doesn't raise.
    gate_page_json(page, LEAK_FACTS_PACK, "some-other-cartridge")


def test_find_leaked_claim_ids_catches_hallucinated_id_by_prefix_alone():
    # Not in valid_claim_ids at all, but shaped like one and carrying a
    # known prefix -- still flagged (defense against a hallucinated id in
    # the right family, not just a leak of a real one).
    page = {"hero": {"promise": {"text": "as shown in (spec-crown-capacity)", "claim_ids": []}}}
    hits = find_leaked_claim_ids(page, {"price-fuji"})
    assert any(h["path"] == "$.hero.promise.text" for h in hits)


def test_find_leaked_claim_ids_visible_text_catches_parenthesized_id():
    html = f"<p>{_LEAKED_TEXT}</p>"
    hits = find_leaked_claim_ids_visible_text(html, {"spec-fuji-capacity", "price-fuji"})
    assert any(h["term"] == "spec-fuji-capacity" for h in hits)


def test_strip_leaked_claim_ids_removes_parenthesized_id_and_reports_it():
    html = f"<p>{_LEAKED_TEXT}</p>"
    cleaned, removed = strip_leaked_claim_ids(html, {"spec-fuji-capacity", "price-fuji"})
    assert removed == ["spec-fuji-capacity"]
    assert "spec-fuji-capacity" not in cleaned
    assert "the Peak Fuji 2-Person Infrared Sauna, which is priced at $8,250" in cleaned


def test_strip_leaked_claim_ids_leaves_unrelated_parentheticals_alone():
    html = "<p>See (Peak Saunas, 2026) for details.</p>"
    cleaned, removed = strip_leaked_claim_ids(html, {"spec-fuji-capacity", "price-fuji"})
    assert removed == []
    assert cleaned == html


# ---------------------------------------------------------------------------
# Fix cycle 6 item 2: a digit inside the product's own short_name/title/
# model name, or a generic "N-Person" capacity token, isn't a number the
# writer is asserting -- it shouldn't by itself force a claim_id onto a
# sentence with nothing else to cite. Regression from the hidden-costs-v2
# verification run: attempt 2's fix for "unlock" reintroduced the product's
# own short_name in a fresh sentence and immediately failed this gate on
# the "2" in "2-Person" instead.
# ---------------------------------------------------------------------------

FUJI_DIGIT_EXEMPT_TERMS = ["Peak Fuji 2-Person Infrared Sauna", "Peak Fuji 2-Person Full Spectrum Infrared Sauna", "Fuji"]


def test_digit_exempt_product_name_needs_no_claim_id():
    page = {"open": [{"text": "The Peak Fuji 2-Person Infrared Sauna is built from cedar."}]}
    problems = validate_page_claim_ids(page, {"price-fuji"}, FUJI_DIGIT_EXEMPT_TERMS)
    assert problems == []


def test_digit_outside_exempt_terms_still_needs_a_claim_id():
    page = {"open": [{"text": "It reaches 150°F."}]}
    problems = validate_page_claim_ids(page, {"price-fuji"}, FUJI_DIGIT_EXEMPT_TERMS)
    assert len(problems) == 1
    assert "contains a number" in problems[0]["issue"]


def test_digit_exempt_generic_capacity_token_needs_no_claim_id():
    # A capacity token for a DIFFERENT product than the one in
    # digit_exempt_terms is still covered by the generic "N-Person" pattern.
    page = {"open": [{"text": "Even the 4-Person model ships free."}]}
    problems = validate_page_claim_ids(page, set(), [])
    assert problems == []


def test_digit_exempt_terms_do_not_exempt_a_real_dollar_amount():
    page = {"open": [{"text": "The Peak Fuji 2-Person Infrared Sauna costs $8,250."}]}
    problems = validate_page_claim_ids(page, {"price-fuji"}, FUJI_DIGIT_EXEMPT_TERMS)
    assert len(problems) == 1
    assert "contains a dollar amount" in problems[0]["issue"]


def test_gate_page_json_uses_facts_pack_digit_exempt_terms():
    # turn_section.criteria carries article's minimum-one benefit claim_id
    # (find_benefit_claim_shortfall) so the only thing under test here is the
    # digit exemption on the "open" paragraph.
    page = {
        "open": [{"text": "The Peak Fuji 2-Person Infrared Sauna is built from cedar."}],
        "turn_section": {"criteria": [{"text": "Backed by Austin.", "claim_ids": ["founder-ceo"]}]},
    }
    facts_pack = {"verified_claims": VERIFIED_CLAIMS, "digit_exempt_terms": FUJI_DIGIT_EXEMPT_TERMS}
    gate_page_json(page, facts_pack, "article")  # does not raise


def test_digit_exempt_terms_handles_a_non_capacity_digit_in_a_model_name():
    # The generic "N-Person" pattern only covers capacity -- a model name
    # with some other digit in it needs the explicit digit_exempt_terms
    # list, not just the capacity regex, to be exempted.
    page = {"open": [{"text": "The Peak Nova 360X sauna heats up fast."}]}
    assert validate_page_claim_ids(page, set(), ["Peak Nova 360X"]) == []
    problems = validate_page_claim_ids(page, set(), [])
    assert len(problems) == 1


# ---------------------------------------------------------------------------
# Fix cycle 6 item 4: while no lender is configured, the schema's dedicated
# financing_line field (hero.financing_line / final_cta.financing_line) must
# state exactly the sentence below. Scoped to that one field -- not scanned
# across every prose string -- after repeated live-verification failures
# (docs/FIXLOG.md Cycle 6) where a text-content-based version of this check
# kept flagging ordinary buyer-education prose that merely discussed
# financing as a topic, or that happened to also state an unrelated price/
# product-name digit in the same sentence.
# ---------------------------------------------------------------------------

def test_find_financing_violations_allows_the_exact_sentence():
    page = {"hero": {"financing_line": {"text": "Financing is available at checkout."}}}
    assert find_financing_violations(page, financing_lender=None) == []


def test_find_financing_violations_flags_any_other_financing_line_text():
    page = {"hero": {"financing_line": {"text": "Financing is available for as low as $75/mo."}}}
    hits = find_financing_violations(page, financing_lender=None)
    assert len(hits) == 1
    assert "Financing is available at checkout." in hits[0]["issue"]


# Fix cycle 21: once a lender is configured, find_financing_violations no
# longer no-ops -- it validates financing_line against the formatted
# with-lender sentence instead, the same way it already validated against
# the no-lender sentence. Before this fix, a page could (and did, see
# docs/FIXLOG.md Cycle 18's verification) keep rendering the no-lender
# sentence forever even with a real lender configured, because nothing
# checked financing_line at all in that case.
def test_find_financing_violations_allows_the_exact_with_lender_sentence():
    page = {"hero": {"financing_line": {"text": "Financing is available through Affirm at checkout."}}}
    assert find_financing_violations(page, financing_lender="Affirm") == []


def test_find_financing_violations_flags_the_no_lender_sentence_once_a_lender_is_configured():
    # The exact bug docs/FIXLOG.md Cycle 18 flagged and left unfixed: the
    # page renders the OLD (no-lender) sentence even though a lender is now
    # configured. That must be a violation, not a pass.
    page = {"hero": {"financing_line": {"text": "Financing is available at checkout."}}}
    hits = find_financing_violations(page, financing_lender="Affirm")
    assert len(hits) == 1
    assert "Financing is available through Affirm at checkout." in hits[0]["issue"]


def test_find_financing_violations_flags_a_monthly_figure_once_a_lender_is_configured():
    page = {"hero": {"financing_line": {"text": "Financing is available for as low as $75/mo with Affirm."}}}
    hits = find_financing_violations(page, financing_lender="Affirm")
    assert len(hits) == 1
    assert "Financing is available through Affirm at checkout." in hits[0]["issue"]


def test_find_financing_violations_ignores_pages_with_no_financing_line_field():
    page = {"open": [{"text": "The sauna is built from cedar."}]}
    assert find_financing_violations(page, financing_lender=None) == []


def test_find_financing_violations_ignores_ordinary_prose_that_discusses_financing():
    # Regression from the hidden-costs-v2 verification run: this cycle's
    # first version of the check scanned every prose string for the
    # substring "financ" and repeatedly flagged ordinary buyer-education
    # commentary (an FAQ question, a paragraph merely discussing financing
    # as a topic, a sentence stating an unrelated price/product-name digit
    # alongside the word "financing") that was never in a financing_line
    # field at all. None of that is checked any more.
    page = {
        "faq": {"questions": [{"question": "Is financing available?", "text": "Financing is available at checkout."}]},
        "open": [{"text": "Financing terms and sticker price are two separate questions worth keeping apart."}],
        "close": {"paragraphs": [{
            "text": "The Peak Fuji 2-Person Infrared Sauna lists its price, specs, and financing details on the same page."
        }]},
    }
    assert find_financing_violations(page, financing_lender=None) == []


def test_find_financing_violations_checks_financing_line_in_a_nested_final_cta_block():
    page = {"final_cta": {"headline": "Ready?", "financing_line": {"text": "Ask about our special rate!"}}}
    hits = find_financing_violations(page, financing_lender=None)
    assert len(hits) == 1
    assert hits[0]["path"] == "$.final_cta.financing_line.text"


def test_find_financing_violations_handles_a_bare_string_financing_line():
    # Some cartridges may not wrap financing_line in a {"text": ...} object.
    page = {"hero": {"financing_line": "Ask about our special rate!"}}
    hits = find_financing_violations(page, financing_lender=None)
    assert len(hits) == 1
    assert hits[0]["path"] == "$.hero.financing_line"


# ---------------------------------------------------------------------------
# Fix cycle 25: the dedicated-field walk above only ever fires for a
# cartridge whose schema has a financing_line field. article's schema
# leaves it optional, and (docs/FIXLOG.md Cycle 24 / SWEEP-2026-09-11-final.md
# "Financing sentence check") its writer twice left it unset and instead
# paraphrased the offer into ordinary body prose -- these are the two real
# examples from that sweep, verbatim. The broadened check must catch both
# without reopening Cycle 6's reverted "any text containing financ" scan --
# test_find_financing_violations_ignores_ordinary_prose_that_discusses_financing
# above still passes unchanged.
# ---------------------------------------------------------------------------

def test_find_financing_violations_flags_a_paraphrase_missing_is_in_article_prose():
    page = {
        "close": {
            "paragraphs": [{
                "text": "The Peak Fuji is priced at $5,450, with financing available through Bread "
                        "Pay at checkout, so the decision becomes easier."
            }]
        }
    }
    hits = find_financing_violations(page, financing_lender="Bread Pay")
    assert len(hits) == 1
    assert hits[0]["path"] == "$.close.paragraphs[0].text"
    assert "Financing is available through Bread Pay at checkout." in hits[0]["issue"]


def test_find_financing_violations_flags_a_paraphrase_joined_with_and_in_article_prose():
    page = {
        "close": {
            "paragraphs": [{
                "text": "The Peak Fuji is priced at $5,450, and financing is available through "
                        "Bread Pay at checkout. See the models next."
            }]
        }
    }
    hits = find_financing_violations(page, financing_lender="Bread Pay")
    assert len(hits) == 1
    assert hits[0]["path"] == "$.close.paragraphs[0].text"


def test_find_financing_violations_allows_the_exact_sentence_combined_with_unrelated_prose():
    # Mirrors Cycle 6 item 5/run 5's "combined field" exemption for the
    # dedicated financing_line field -- a prose field doesn't have to be
    # nothing but the allowed sentence, as long as the sentence appears as
    # its own whole sentence and nothing else in the field states a wrong
    # figure/lender/APR.
    page = {"close": {"paragraphs": [{
        "text": "It's priced at $8,250, the listed price on the product page. "
                "Financing is available through Bread Pay at checkout."
    }]}}
    assert find_financing_violations(page, financing_lender="Bread Pay") == []


def test_find_financing_violations_ignores_an_article_page_with_no_financing_mention():
    page = {
        "open": [{"text": "Shopping used to mean waiting for a callback."}],
        "close": {"paragraphs": [{"text": "Peak Saunas is one brand that does this."}]},
    }
    assert find_financing_violations(page, financing_lender="Bread Pay") == []


def test_gate_page_json_stops_on_financing_violation():
    page = {"hero": {"financing_line": {"text": "Financing available now, as low as $99/mo, no credit check needed!"}}}
    with pytest.raises(ClaimsGateFailure) as exc_info:
        gate_page_json(page, FACTS_PACK, "product-page")
    assert any("financing_line" in item["issue"] for item in exc_info.value.items)


# ---------------------------------------------------------------------------
# Fix cycle 7 item 1: warranty wording, same fixed-sentence pattern as
# financing above, but scoped to any text ("any text containing 'warrant'"),
# not one dedicated field -- warranty copy can legitimately appear in a proof
# bullet, a trust-strip field, or a specs-table row.
# ---------------------------------------------------------------------------

WARRANTY_VERIFIED_CLAIMS = [
    {
        "id": "warranty-terms",
        "text": "Peak Saunas warranty covers, from date of delivery: heating elements 7 years; "
                "control system and power supply 3 years; chromotherapy lighting 1 year.",
        "category": "trust",
        "source": "https://peaksaunas.com/pages/warranty",
    },
]
WARRANTY_FACTS_PACK = {"verified_claims": WARRANTY_VERIFIED_CLAIMS}


def test_find_warranty_violations_allows_the_exact_sentence():
    page = {"trust_strip": {"warranty": {"text": "Limited lifetime warranty; full terms by component are published on the warranty page."}}}
    assert find_warranty_violations(page, WARRANTY_VERIFIED_CLAIMS) == []


def test_find_warranty_violations_allows_the_spec_table_value():
    page = {"specs_table": [{"label": "Warranty", "value": "Limited lifetime warranty (terms by component)"}]}
    assert find_warranty_violations(page, WARRANTY_VERIFIED_CLAIMS) == []


def test_find_warranty_violations_allows_a_verbatim_quote_of_the_verified_claim():
    page = {"open": [{"text": (
        "As the warranty page puts it, \"Peak Saunas warranty covers, from date of delivery: "
        "heating elements 7 years; control system and power supply 3 years; chromotherapy "
        "lighting 1 year.\""
    )}]}
    assert find_warranty_violations(page, WARRANTY_VERIFIED_CLAIMS) == []


def test_find_warranty_violations_flags_a_false_per_component_summary():
    # The actual bug: "Limited lifetime warranty on the cabin, heating
    # elements, and electronics" -- electronics are 3yr/1yr in the verified
    # claim, not lifetime, so this is a false, writer-composed summary.
    page = {"proof_bullets": [{
        "label": "Warranty",
        "text": "Limited lifetime warranty on the cabin, heating elements, and electronics.",
    }]}
    hits = find_warranty_violations(page, WARRANTY_VERIFIED_CLAIMS)
    assert len(hits) == 1
    assert "warranty wording must be exactly" in hits[0]["issue"]


def test_find_warranty_violations_ignores_pages_with_no_warranty_mention():
    page = {"open": [{"text": "The sauna is built from cedar."}]}
    assert find_warranty_violations(page, WARRANTY_VERIFIED_CLAIMS) == []


# Regression from the real out/20260909-2233-hidden-costs-v2 verification
# run: article STOPped, exhausting the repair loop, on ordinary buyer-
# education prose that only discusses warranty as a policy topic (asserts
# no specific coverage) and on the allowed sentence combined with unrelated
# surrounding content in the same field -- the same two false-positive
# shapes the financing gate already hit in cycle 6.
def test_find_warranty_violations_ignores_topical_mentions_with_no_lifetime_claim():
    page = {"body_sections": [{"paragraphs": [
        {"text": "At minimum, a shopper should be able to find the price, specs, and the return or warranty terms without submitting anything."},
        {"text": "It also helps to know what the warranty actually covers component by component before you buy."},
    ]}]}
    assert find_warranty_violations(page, WARRANTY_VERIFIED_CLAIMS) == []


def test_find_warranty_violations_allows_the_exact_sentence_combined_with_unrelated_content():
    page = {"turn_section": {"criteria": [{
        "text": (
            "Clear policies on shipping, warranty, and returns published where you can read them "
            "before you buy. Limited lifetime warranty; full terms by component are published on "
            "the warranty page."
        )
    }]}}
    assert find_warranty_violations(page, WARRANTY_VERIFIED_CLAIMS) == []


def test_find_warranty_violations_allows_the_sentence_re_cased_mid_sentence():
    # Second real-run regression (out/20260909-2238-hidden-costs-v2): a
    # writer naturally lowercases "Limited" -> "limited" when the sentence
    # isn't the first word of its own sentence -- same wording, same
    # meaning, still the allowed sentence.
    page = {"turn_section": {"criteria": [{
        "text": "A warranty that's actually written down. Peak Saunas offers a limited lifetime "
                "warranty; full terms by component are published on the warranty page."
    }]}}
    assert find_warranty_violations(page, WARRANTY_VERIFIED_CLAIMS) == []


def test_find_warranty_violations_allows_minor_connector_drift_around_the_core_disclaimer():
    # Third real-run regression (out/20260909-2241-hidden-costs-v2): the
    # writer drops "are" and swaps ";" for ", with" while weaving the
    # sentence into a bigger one -- the specific "full terms by component
    # ... published on the warranty page" disclaimer is still there and
    # still true, just reworded around the edges.
    page = {"specs_and_proof": {"proof_points": [{
        "text": "The sauna carries a limited lifetime warranty, with full terms by component "
                "published on the warranty page. You can read exactly what's covered before you buy."
    }]}}
    assert find_warranty_violations(page, WARRANTY_VERIFIED_CLAIMS) == []


def test_find_warranty_violations_allows_the_sentence_continued_with_a_comma():
    # Same real-run regression, second occurrence: the writer keeps "are"
    # and the semicolon but continues the sentence with a comma clause
    # instead of ending it with a period.
    page = {"faq": {"questions": [{"text":
        "The sauna carries a limited lifetime warranty; full terms by component are published "
        "on the warranty page, so you can check exactly what's covered before buying."
    }]}}
    assert find_warranty_violations(page, WARRANTY_VERIFIED_CLAIMS) == []


def test_find_warranty_violations_ignores_lifetime_and_warranty_used_separately():
    # Fourth real-run regression (out/20260909-2248-hidden-costs-v2): both
    # words appear but never adjacent -- this is commentary contrasting a
    # vague "lifetime promise" against reading real per-component terms,
    # not a claim that Peak's warranty is blanket lifetime coverage.
    page = {"turn_section": {"criteria": [{
        "text": "A warranty document you can actually read component by component, not just a "
                "one-line lifetime promise."
    }]}}
    assert find_warranty_violations(page, WARRANTY_VERIFIED_CLAIMS) == []


def test_find_warranty_violations_still_flags_a_claim_with_no_disclaimer_phrase_at_all():
    # The core-pattern tolerance above must not swallow the original false
    # claim, which never states "full terms by component ... published".
    page = {"proof_bullets": [{
        "text": "Limited lifetime warranty on the cabin, heating elements, and electronics, "
                "with full terms published on our website.",
    }]}
    hits = find_warranty_violations(page, WARRANTY_VERIFIED_CLAIMS)
    assert len(hits) == 1


def test_find_warranty_violations_allows_bare_spec_label():
    page = {"specs_table": [{"label": "Warranty", "value": "See warranty page"}]}
    # The label alone is fine; the bad value is still flagged on its own.
    hits = find_warranty_violations(page, WARRANTY_VERIFIED_CLAIMS)
    assert all(h["text"] != "Warranty" for h in hits)


def test_gate_page_json_stops_on_warranty_violation():
    page = {"trust_strip": {"warranty": {"text": "Limited lifetime warranty on the cabin, heating elements, and electronics."}}}
    with pytest.raises(ClaimsGateFailure) as exc_info:
        gate_page_json(page, WARRANTY_FACTS_PACK, "product-page")
    assert any("warranty wording" in item["issue"] for item in exc_info.value.items)


# ---------------------------------------------------------------------------
# Fix cycle 10 items 2-4: locked-topic ad claims (warranty/reviews/financing/
# price) never match by word overlap -- each is checked against its own
# locked fact, and a failure is reported as an AD OVERCLAIM, never silently
# folded into "matched". Problem B: "free lifetime warranty if it doesn't
# work" cleared the general 0.6 word-overlap bar against
# gbrain-allowlist-lifetime-warranty's "Limited Lifetime warranty." at 0.667
# even though the real warranty is per-component, not an unconditional
# money-back guarantee -- that's the bug these tests pin down.
# ---------------------------------------------------------------------------

WARRANTY_TERMS_CLAIM = [
    {
        "id": "warranty-terms",
        "text": (
            "Peak Saunas warranty covers, from date of delivery: heating elements 7 years; "
            "cabinetry and structure 7 years; control system and power supply 3 years; red "
            "light therapy panels 3 years; chromotherapy lighting 1 year; audio system 1 year; "
            "WiFi/app connectivity 1 year; accessories 1 year. Coverage applies to the original "
            "purchaser only and requires installation within 6 months of delivery."
        ),
        "category": "trust",
        "source": "https://peaksaunas.com/policies/warranty-policy",
    }
]


def test_false_warranty_claim_is_ad_overclaim_never_matched():
    ad_brief = {"claims_made": ["It includes a free lifetime warranty if it doesn't work"]}
    with pytest.raises(ClaimsGateFailure) as exc_info:
        gate_ad_brief_claims(ad_brief, WARRANTY_TERMS_CLAIM, policy="stop")
    items = exc_info.value.items
    assert len(items) == 1
    assert items[0]["topic"] == "warranty"
    assert items[0]["message"].startswith("AD OVERCLAIM:")
    assert "verified fact" in items[0]["message"]


def test_exact_allowed_warranty_sentence_matches():
    ad_brief = {"claims_made": [ALLOWED_WARRANTY_SENTENCE]}
    matched, overclaims, alt_claims = gate_ad_brief_claims(ad_brief, WARRANTY_TERMS_CLAIM, policy="stop")
    assert overclaims == []
    assert matched[0]["matched_claim_id"] == "warranty-terms"


REVIEWS_LIVE_CLAIM = {
    "id": "reviews-live",
    "text": "Rated 4.6 out of 5 across 8,200 reviews on Judge.me (fetched 2026-09-09).",
    "category": "trust",
    "source": "https://judge.me/reviews/stores/peaksaunas.com",
}


def test_false_review_stats_claim_is_ad_overclaim():
    ad_brief = {"claims_made": ["We're rated 4.9 out of 5 with 9,000 reviews"]}
    with pytest.raises(ClaimsGateFailure) as exc_info:
        gate_ad_brief_claims(ad_brief, [], policy="stop", reviews_claim=REVIEWS_LIVE_CLAIM)
    items = exc_info.value.items
    assert len(items) == 1
    assert items[0]["topic"] == "reviews"
    assert "AD OVERCLAIM" in items[0]["message"]


def test_review_stats_claim_matching_live_figures_passes():
    ad_brief = {"claims_made": ["We're rated 4.6 out of 5 with 8,200 reviews"]}
    matched, overclaims, alt_claims = gate_ad_brief_claims(ad_brief, [], policy="stop", reviews_claim=REVIEWS_LIVE_CLAIM)
    assert overclaims == []
    assert matched[0]["matched_claim_id"] == "reviews-live"


def test_review_stats_claim_with_no_live_data_is_ad_overclaim():
    ad_brief = {"claims_made": ["We're rated 4.9 out of 5"]}
    with pytest.raises(ClaimsGateFailure) as exc_info:
        gate_ad_brief_claims(ad_brief, [], policy="stop", reviews_claim=None)
    assert exc_info.value.items[0]["topic"] == "reviews"


def test_financing_claim_is_always_an_overclaim_with_no_configured_lender():
    ad_brief = {"claims_made": ["Financing available from est. $257/mo through Bread Pay"]}
    with pytest.raises(ClaimsGateFailure) as exc_info:
        gate_ad_brief_claims(ad_brief, [], policy="stop")
    assert exc_info.value.items[0]["topic"] == "financing"


# ---------------------------------------------------------------------------
# Fix cycle 21: once a lender is configured, a financing ad claim that names
# it with no dollar figure/monthly payment/APR matches the formatted
# with-lender sentence; a claim carrying any of those figures is still an
# overclaim -- no real lender quote exists anywhere in this codebase to check
# a specific figure against (unchanged gap from fix cycle 10).
# ---------------------------------------------------------------------------

def test_evaluate_financing_claim_matches_when_the_claim_names_the_lender_with_no_figure():
    ok, fact = evaluate_financing_claim("Financing available through Bread Pay", "Bread Pay")
    assert ok is True
    assert fact == "Financing is available through Bread Pay at checkout."


def test_evaluate_financing_claim_is_an_overclaim_when_it_carries_a_dollar_figure():
    ok, fact = evaluate_financing_claim("Financing available from est. $257/mo through Bread Pay", "Bread Pay")
    assert ok is False
    assert fact == "Financing is available through Bread Pay at checkout."


def test_evaluate_financing_claim_is_an_overclaim_when_it_carries_an_apr():
    ok, fact = evaluate_financing_claim("0% APR financing through Bread Pay", "Bread Pay")
    assert ok is False
    assert fact == "Financing is available through Bread Pay at checkout."


def test_evaluate_financing_claim_is_an_overclaim_when_it_names_no_lender_at_all():
    ok, fact = evaluate_financing_claim("Financing is available", "Bread Pay")
    assert ok is False
    assert fact == "Financing is available through Bread Pay at checkout."


def test_classify_locked_topic_routes_the_configured_lender_name_with_no_figure_to_financing():
    # Before fix cycle 21, a claim naming only the configured lender (no
    # dollar/monthly figure) fell through to the ordinary word-overlap path
    # -- vocab.LENDER_NAME_RE only matches a *forbidden* lender name, and the
    # configured lender is deliberately not on that list (fix cycle 18).
    assert classify_locked_topic("Financing available through Bread Pay", "Bread Pay") == "financing"
    assert classify_locked_topic("Financing available through Bread Pay", None) is None


def test_financing_claim_naming_the_configured_lender_matches_in_the_full_gate():
    ad_brief = {"claims_made": ["Financing available through Bread Pay"]}
    matched, overclaims, alt_claims = gate_ad_brief_claims(
        ad_brief, [], policy="stop", financing_lender="Bread Pay",
    )
    assert overclaims == []
    assert matched[0]["claim"] == "Financing available through Bread Pay"


def test_financing_claim_with_a_dollar_figure_still_overclaims_once_a_lender_is_configured():
    ad_brief = {"claims_made": ["Financing available from est. $257/mo through Bread Pay"]}
    with pytest.raises(ClaimsGateFailure) as exc_info:
        gate_ad_brief_claims(ad_brief, [], policy="stop", financing_lender="Bread Pay")
    item = exc_info.value.items[0]
    assert item["topic"] == "financing"
    assert item["verified_fact"] == "Financing is available through Bread Pay at checkout."


def test_price_claim_matches_current_product_price_regardless_of_wording():
    # "on sale right now for" shares almost no words with a verified price
    # claim's own text -- the numeric anchor matches on the dollar amount
    # alone (fix cycle 10 item 2, replacing word-overlap price matching).
    ad_brief = {"claims_made": ["Infrared sauna is on sale right now for $8,250"]}
    matched, overclaims, alt_claims = gate_ad_brief_claims(ad_brief, [], policy="stop", product=FUJI_PRODUCT)
    assert overclaims == []
    assert matched[0]["matched_claim_id"] == "price-fuji"


def test_price_claim_matching_no_product_price_is_unmatched_with_specific_message():
    ad_brief = {"claims_made": ["Infrared sauna is on sale right now for $5,450"]}
    with pytest.raises(ClaimsGateFailure) as exc_info:
        gate_ad_brief_claims(ad_brief, [], policy="stop", product=FUJI_PRODUCT)
    item = exc_info.value.items[0]
    assert item["message"] == "quoted price $5,450 matches no current product price"


# ---------------------------------------------------------------------------
# Fix cycle 10 item 4 / fix cycle 12 item 3: ad_overclaim_policy -- "stop"
# (default) stops the run on ANY unmatched or overclaimed claim, plain or
# locked-topic, unchanged since cycle 10. "warn" (broadened in cycle 12) no
# longer stops the run for EITHER kind -- every failed claim (plain-unmatched
# or locked-topic overclaim) is dropped from what the writer may use and
# returned in `not_repeated` instead of raised.
# ---------------------------------------------------------------------------

def test_warn_policy_does_not_stop_on_locked_topic_overclaim():
    ad_brief = {"claims_made": ["It includes a free lifetime warranty if it doesn't work"]}
    matched, not_repeated, alt_claims = gate_ad_brief_claims(ad_brief, WARRANTY_TERMS_CLAIM, policy="warn")
    assert matched == []
    assert len(not_repeated) == 1
    assert not_repeated[0]["topic"] == "warranty"


def test_warn_policy_does_not_stop_on_plain_unmatched_claim_either():
    # Fix cycle 12 item 3: this used to be the one case "warn" didn't cover
    # -- a plain (non-locked-topic) unmatched claim always stopped the run
    # regardless of policy. Now it doesn't: it's dropped from what the
    # writer may use and reported, same as a locked-topic overclaim.
    ad_brief = {"claims_made": ["Peak Saunas ships every order within two business days."]}
    matched, not_repeated, alt_claims = gate_ad_brief_claims(ad_brief, WARRANTY_TERMS_CLAIM, policy="warn")
    assert matched == []
    assert len(not_repeated) == 1
    assert "topic" not in not_repeated[0]
    assert not_repeated[0]["claim"] == "Peak Saunas ships every order within two business days."
    assert not_repeated[0]["message"].startswith("AD CLAIM NOT REPEATED:")


def test_stop_policy_folds_overclaims_and_unmatched_into_one_failure():
    ad_brief = {
        "claims_made": [
            "It includes a free lifetime warranty if it doesn't work",
            "Peak Saunas ships every order within two business days.",
        ]
    }
    with pytest.raises(ClaimsGateFailure) as exc_info:
        gate_ad_brief_claims(ad_brief, WARRANTY_TERMS_CLAIM, policy="stop")
    assert len(exc_info.value.items) == 2


# ---------------------------------------------------------------------------
# Fix cycle 12 item 3 (second half): a claim about the alternative/comparison
# option (the red-X column of a comparative still) is never matched, never
# usable on the page, and never stops the run under either policy.
# ---------------------------------------------------------------------------

def test_alternative_claim_never_matched_never_stops_under_stop_policy():
    ad_brief = {"claims_made": ["Competing products have only basic manual controls"]}
    matched, not_repeated, alt_claims = gate_ad_brief_claims(ad_brief, VERIFIED_CLAIMS, policy="stop")
    assert matched == []
    assert not_repeated == []
    assert len(alt_claims) == 1
    assert alt_claims[0]["about"] == "alternative"
    assert alt_claims[0]["claim"] == "Competing products have only basic manual controls"


def test_alternative_claim_never_stops_under_warn_policy_either():
    ad_brief = {"claims_made": ["Comparison option takes 3 hours to do"]}
    matched, not_repeated, alt_claims = gate_ad_brief_claims(ad_brief, VERIFIED_CLAIMS, policy="warn")
    assert matched == []
    assert not_repeated == []
    assert len(alt_claims) == 1


def test_alternative_claim_mixed_with_a_plain_unmatched_claim_under_stop():
    # An alternative claim never contributes to a STOP -- only the plain
    # unmatched claim alongside it does.
    ad_brief = {
        "claims_made": [
            "Competitor products have weak far-infrared only",
            "It's only 31 by 32 inches",
        ]
    }
    with pytest.raises(ClaimsGateFailure) as exc_info:
        gate_ad_brief_claims(ad_brief, VERIFIED_CLAIMS, policy="stop")
    assert len(exc_info.value.items) == 1
    assert exc_info.value.items[0]["claim"] == "It's only 31 by 32 inches"


def test_classify_ad_claim_about_recognizes_the_real_fixture_phrasings():
    # docs/SWEEP-2026-09-10.md fixtures 5-7's actual claims_made strings.
    for text in (
        "Product includes 4-in-1: Near, Mid, Far IR + Red Light",
        "Competing products lack red light therapy",
        "Competitor products have basic manual controls",
        "Comparison option leaves skin dull",
    ):
        if "Competing" in text or "Competitor" in text or "Comparison" in text:
            assert classify_ad_claim_about(text) == "alternative", text
    assert classify_ad_claim_about("Product includes 4-in-1: Near, Mid, Far IR + Red Light") is None


def test_classify_ad_claim_about_recognizes_competing_saunas_phrasing():
    # A real Cycle 12 sweep run (docs/FIXLOG.md Cycle 12) produced this exact
    # claim -- "sauna(s)" is a real competitor-noun choice in production ad
    # copy, not just "product"/"model". Without it in the noun list, this
    # claim fell through to word-overlap matching and false-MATCHED Peak's
    # own warranty-terms claim at 0.667 overlap.
    assert classify_ad_claim_about("Competing saunas lack red light therapy") == "alternative"
    assert classify_ad_claim_about("Competitor saunas have only basic manual controls") == "alternative"


def test_classify_ad_claim_about_recognizes_comparison_product_phrasing():
    # Also found live in the same Cycle 12 sweep (still-infraredglow-4x5.png):
    # "comparison" was only ever paired with "option" in the noun list, so
    # "Comparison product has no red light therapy" fell through to
    # word-overlap and false-MATCHED warranty-terms at 0.6 overlap.
    assert classify_ad_claim_about("Comparison product has no red light therapy") == "alternative"
    assert classify_ad_claim_about("Comparison product has basic manual controls only") == "alternative"


def test_classify_ad_claim_about_leaves_a_specific_named_rival_claim_alone():
    # A claim with no "competitor"/"competing" grammatical subject at all --
    # still a plain unmatched claim (never matches, never stops under
    # "warn"), just not classified "about: alternative".
    assert classify_ad_claim_about("A review site rated Peak below every major competitor") is None


# ---------------------------------------------------------------------------
# Fix cycle 11 problem A: attributed_to_customer. The writer reproduces the
# ad speaker's own cost math (price-comparison-v2.mov: "she put memberships
# at around $200 a month, $2,400 a year") in prose; the plain digit rule
# demands a claim_id no source exists for, and the repair loop thrashes.
# A narrative paragraph marked attributed_to_customer is exempt from the
# claim_id rule for a number ONLY when that number also appears in the ad
# speaker's own words (ad_brief.speaker_experience / transcript_or_text),
# and only when the sentence itself visibly reads as attributed and the
# node sits somewhere a narrative paragraph is actually allowed to be.
# ---------------------------------------------------------------------------

SPEAKER_AD_BRIEF = {
    "speaker_experience": [
        "I've been seeing that the average unlimited sauna membership is around $200 a month. "
        "So say $2,400 a year."
    ],
    "transcript_or_text": "",
}


def test_speaker_numbers_extracts_dollar_amounts_from_speaker_experience():
    assert speaker_numbers(SPEAKER_AD_BRIEF) == {"$200", "$2,400".replace(",", "")}


def test_attributed_paragraph_with_speaker_numbers_needs_no_claim_id():
    page = {
        "close": {
            "paragraphs": [
                {
                    "text": "One customer told us she put her studio memberships at around $200 a "
                             "month, or about $2,400 a year.",
                    "attributed_to_customer": True,
                }
            ]
        }
    }
    problems = validate_page_claim_ids(page, set(), speaker_number_set=speaker_numbers(SPEAKER_AD_BRIEF))
    assert problems == []


def test_attributed_paragraph_with_a_number_the_speaker_never_said_still_needs_a_claim_id():
    # $5,450 is the sauna's own price, not anything the speaker said -- the
    # exemption only ever covers numbers that trace back to her own words.
    page = {
        "close": {
            "paragraphs": [
                {
                    "text": "One customer told us she estimated the sauna itself runs about $5,450.",
                    "attributed_to_customer": True,
                }
            ]
        }
    }
    problems = validate_page_claim_ids(page, set(), speaker_number_set=speaker_numbers(SPEAKER_AD_BRIEF))
    assert len(problems) == 1
    assert "not in the ad speaker's own words" in problems[0]["issue"]
    assert "$5450" in problems[0]["issue"]


def test_attributed_to_customer_rejected_outside_a_narrative_paragraph():
    # Same well-attributed sentence, same in-speaker numbers -- but sitting
    # on a proof bullet (turn_section.criteria) instead of a plain
    # paragraph. The flag itself is rejected regardless of phrasing; per
    # fix cycle 11 problem A, headings/proof/specs/FAQ can never be marked
    # attributed.
    page = {
        "turn_section": {
            "criteria": [
                {
                    "text": "One customer told us she put memberships at around $200 a month.",
                    "attributed_to_customer": True,
                }
            ]
        }
    }
    problems = validate_page_claim_ids(page, set(), speaker_number_set=speaker_numbers(SPEAKER_AD_BRIEF))
    assert len(problems) == 2  # the rejected flag, and the now-unexempt number
    issues = [p["issue"] for p in problems]
    assert any("only allowed on a narrative paragraph" in i for i in issues)
    assert any("contains a dollar amount" in i for i in issues)


def test_find_missing_attribution_flags_a_marked_item_with_no_visible_attribution():
    page = {"close": {"paragraphs": [{"text": "Memberships run about $200 a month.", "attributed_to_customer": True}]}}
    hits = find_missing_attribution(page)
    assert len(hits) == 1
    assert "no visible attribution" in hits[0]["issue"]


def test_find_missing_attribution_passes_with_the_word_customer():
    page = {"close": {"paragraphs": [{"text": "One customer estimated memberships at $200 a month.", "attributed_to_customer": True}]}}
    assert find_missing_attribution(page) == []


def test_find_missing_attribution_passes_with_pronoun_plus_verb():
    page = {"close": {"paragraphs": [{"text": "She told us memberships run about $200 a month.", "attributed_to_customer": True}]}}
    assert find_missing_attribution(page) == []


def test_find_missing_attribution_ignores_unmarked_items():
    page = {"close": {"paragraphs": [{"text": "Memberships run about $200 a month."}]}}
    assert find_missing_attribution(page) == []


def test_gate_page_json_passes_the_real_price_comparison_scenario_end_to_end():
    # The scenario fix cycle 11 problem A is fixing, exercised through the
    # actual gate entry point: the writer restates the ad speaker's own cost
    # math, attributed and phrased as her own estimate, in a narrative
    # paragraph -- no claim_id, no STOP.
    page = {
        "close": {
            "paragraphs": [
                {
                    # Cycle 65: her own words, framed neutrally -- "One
                    # customer told us she put her studio memberships..."
                    # now fails the quote-fidelity gate.
                    "text": "In the ad, she says the average unlimited sauna membership is around $200 a "
                             "month, so say $2,400 a year.",
                    "attributed_to_customer": True,
                }
            ]
        }
    }
    facts_pack = {"verified_claims": VERIFIED_CLAIMS}
    gate_page_json(page, facts_pack, "some-other-cartridge", ad_brief=SPEAKER_AD_BRIEF)  # does not raise


def test_gate_page_json_still_stops_on_the_same_page_without_ad_brief():
    # Regression guard: an existing caller that doesn't pass ad_brief (every
    # gate_page_json call before this fix) gets the pre-fix behavior --
    # no exemption is possible without speaker numbers to check against.
    page = {
        "close": {
            "paragraphs": [
                {
                    "text": "One customer told us she put her studio memberships at around $200 a "
                             "month, or about $2,400 a year.",
                    "attributed_to_customer": True,
                }
            ]
        }
    }
    facts_pack = {"verified_claims": VERIFIED_CLAIMS}
    with pytest.raises(ClaimsGateFailure):
        gate_page_json(page, facts_pack, "some-other-cartridge")


# ---------------------------------------------------------------------------
# Fix cycle 16 item 11 (Thursday queue item 1): deterministic alias matching,
# checked before any semantic-match model call.
# ---------------------------------------------------------------------------

ALIAS_VERIFIED_CLAIMS = [
    {
        "id": "gbrain-allowlist-360-full-spectrum",
        "text": "360 degree full spectrum infrared heater placement.",
        "category": "spec",
        "source": "https://peaksaunas.com/products/fuji",
        "aliases": ["4-in-1", "near, mid, far infrared plus red light"],
    },
    {
        "id": "gbrain-allowlist-red-light",
        "text": "Medical-grade red light therapy (included standard).",
        "category": "trust",
        "source": "https://peaksaunas.com/products/fuji",
        "aliases": ["medical-grade panel", "medical grade red light panel"],
    },
]


def test_alias_match_matches_4in1_alias_to_full_spectrum_claim():
    best, ratio = alias_match("4-in-1: near, mid, far infrared + red light", ALIAS_VERIFIED_CLAIMS)
    assert best is not None
    assert best["id"] == "gbrain-allowlist-360-full-spectrum"


def test_alias_match_matches_medical_grade_panel_alias_to_red_light_claim():
    best, ratio = alias_match("medical-grade panel", ALIAS_VERIFIED_CLAIMS)
    assert best is not None
    assert best["id"] == "gbrain-allowlist-red-light"


def test_alias_match_returns_none_when_no_alias_is_close_enough():
    best, ratio = alias_match("free two-day shipping on every order", ALIAS_VERIFIED_CLAIMS)
    assert best is None


def test_alias_match_respects_numeric_guard():
    # A claim's own aliases still can't be used to smuggle past a wrong
    # number -- same numeric-token guard as ordinary word-overlap matching.
    claims = [
        {
            "id": "price-fuji",
            "text": "The Peak Saunas Fuji is priced at $8250.",
            "category": "price",
            "source": "https://peaksaunas.com/products/fuji",
            "aliases": ["an unbeatable price"],
        }
    ]
    best, ratio = alias_match("an unbeatable price of $9999", claims)
    assert best is None


def test_match_claim_uses_alias_before_semantic_mapping():
    # Even when a (wrong) semantic mapping is offered for this exact text,
    # the deterministic alias match still wins -- fix cycle 16 item 11:
    # "checked deterministically before the model call."
    best, ratio = match_claim(
        "medical-grade panel", ALIAS_VERIFIED_CLAIMS,
        semantic_mapping={"medical-grade panel": None},
    )
    assert best is not None
    assert best["id"] == "gbrain-allowlist-red-light"


def test_gate_ad_brief_claims_matches_4in1_claim_via_alias_with_no_semantic_mapping():
    ad_brief = {"claims_made": ["4-in-1: near, mid, far infrared + red light"]}
    matched, overclaims, alt = gate_ad_brief_claims(ad_brief, ALIAS_VERIFIED_CLAIMS, policy="stop")
    assert overclaims == []
    assert matched[0]["matched_claim_id"] == "gbrain-allowlist-360-full-spectrum"


# ---------------------------------------------------------------------------
# Fix cycle 16 item 12 (Thursday queue item 2): narrow the warranty heuristic
# trigger to a sentence that actually asserts coverage.
# ---------------------------------------------------------------------------


def test_find_warranty_violations_ignores_descriptive_prose_with_no_coverage_assertion():
    page = {"body_sections": [{"paragraphs": [
        {"text": "Read the warranty terms before you buy, not just the marketing page."}
    ]}]}
    assert find_warranty_violations(page, WARRANTY_VERIFIED_CLAIMS) == []


def test_find_warranty_violations_ignores_a_bare_mention_with_no_assertion_word():
    page = {"faq": {"questions": [{"text": "What does the warranty page actually say?"}]}}
    assert find_warranty_violations(page, WARRANTY_VERIFIED_CLAIMS) == []


def test_find_warranty_violations_still_catches_a_false_coverage_assertion():
    # A sentence that DOES assert coverage ("covers", and the "lifetime
    # warranty" bigram the underlying check keys on) but isn't one of the
    # allowed forms must still be caught -- the narrowed trigger only
    # exempts descriptive prose with no coverage assertion at all, it never
    # widens what is_allowed() itself accepts.
    page = {"proof_bullets": [{"text": "Our lifetime warranty covers every part of the sauna, forever."}]}
    hits = find_warranty_violations(page, WARRANTY_VERIFIED_CLAIMS)
    assert len(hits) == 1


def test_find_warranty_violations_still_catches_backed_by_wording():
    page = {"proof_bullets": [{"text": "Backed by a lifetime warranty on absolutely everything."}]}
    hits = find_warranty_violations(page, WARRANTY_VERIFIED_CLAIMS)
    assert len(hits) == 1


def test_find_warranty_violations_still_allows_the_exact_sentence_with_narrowed_trigger():
    # Regression: the fixed allowed sentence itself contains "lifetime", so
    # the narrowed trigger still reaches it and is_allowed() still passes it.
    page = {"trust_strip": {"warranty": {
        "text": "Limited lifetime warranty; full terms by component are published on the warranty page."
    }}}
    assert find_warranty_violations(page, WARRANTY_VERIFIED_CLAIMS) == []


# ---------------------------------------------------------------------------
# Fix cycle 16 item 3 (design note 7): longform's optional hero.proof_stats.
# ---------------------------------------------------------------------------


def test_find_proof_stats_violations_noop_when_absent():
    assert find_proof_stats_violations({"hero": {}}) == []
    assert find_proof_stats_violations({}) == []


def test_find_proof_stats_violations_passes_when_every_stat_has_a_claim_id():
    page = {"hero": {"proof_stats": [
        {"value": "4.8/5", "label": "from 1,200+ reviews", "claim_ids": ["reviews-live"]},
        {"value": "Free", "label": "shipping, always", "claim_ids": ["shipping-policy"]},
    ]}}
    assert find_proof_stats_violations(page) == []


def test_find_proof_stats_violations_flags_a_stat_with_no_claim_id():
    page = {"hero": {"proof_stats": [
        {"value": "4.8/5", "label": "from 1,200+ reviews", "claim_ids": ["reviews-live"]},
        {"value": "Free", "label": "shipping, always"},
    ]}}
    hits = find_proof_stats_violations(page)
    assert len(hits) == 1
    assert "proof_stats[1]" in hits[0]["path"]


def test_gate_page_json_stops_on_proof_stats_with_no_claim_id():
    page = {"hero": {"proof_stats": [{"value": "4.8/5", "label": "reviews"}]}}
    facts_pack = {"verified_claims": []}
    with pytest.raises(ClaimsGateFailure) as exc_info:
        gate_page_json(page, facts_pack, "longform")
    assert any("proof_stats" in item["path"] for item in exc_info.value.items)


# ---------------------------------------------------------------------------
# Fix cycle 16 item 5 (design note 9): never more than one CTA/offer card.
# ---------------------------------------------------------------------------


def test_find_second_cta_violation_passes_a_single_top_level_cta_url():
    page = {"cta_text": "Shop the Fuji", "cta_url": "https://peaksaunas.com/products/fuji", "hero": {"headline": "x"}}
    assert find_second_cta_violation(page) == []


def test_find_second_cta_violation_catches_a_nested_offer_card():
    page = {
        "cta_text": "Shop the Fuji",
        "cta_url": "https://peaksaunas.com/products/fuji",
        "hero": {"headline": "x"},
        "final_cta": {"headline": "Limited offer", "cta_url": "https://peaksaunas.com/pages/special-offer"},
    }
    hits = find_second_cta_violation(page)
    assert len(hits) == 1
    assert "second CTA url" in hits[0]["issue"]


def test_gate_page_json_stops_on_second_cta_violation():
    page = {
        "cta_text": "Shop the Fuji",
        "cta_url": "https://peaksaunas.com/products/fuji",
        "extra_offer": {"cta_url": "https://peaksaunas.com/pages/other-offer"},
    }
    facts_pack = {"verified_claims": []}
    with pytest.raises(ClaimsGateFailure) as exc_info:
        gate_page_json(page, facts_pack, "longform")
    assert any("second CTA url" in item["issue"] for item in exc_info.value.items)


def test_find_second_cta_violation_noop_for_article_shaped_page():
    # article's single CTA lives at cta.text/cta.url, never a top-level
    # "cta_url" key -- this check never fires on article's own shape.
    page = {"cta": {"text": "See the models", "url": "https://peaksaunas.com/collections/all"}}
    assert find_second_cta_violation(page) == []
