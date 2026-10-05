"""Cycle 74: listicle pass rate and copy quality.

Every stop cause fixed here has a regression test built from the real failing
text of the 2026-10-05 runs (tests/fixtures/listicle_c74/real_runs.json, cut
from tenants/peak-saunas/out and runs/*.log), next to a test that the true
positive still fails. The copy-quality gates are tested on the real passing
page 20261005-151302-hidden-costs-v2-2sxl, which shipped every fault they
exist to stop.
"""
import copy
import json
from pathlib import Path

import pytest

from harness import config, listicle, listicle_quality, pagepatch, quote_fidelity
from harness.budget import Budget
from harness.claims import (
    find_leaked_claim_ids,
    find_leaked_claim_ids_visible_text,
    gate_page_json,
    safe_quote_candidates,
    validate_page_claim_ids,
    ClaimsGateFailure,
)
from harness.jsonutil import extract_json_tolerant
from harness.log import RunLog
from harness.repair import (
    _brand_spelling_regex,
    _product_name_forms,
    apply_deterministic_fixes,
    build_patch_revision_note,
    check_page_gates,
    write_and_gate_page,
)
from harness.write import build_initial_write_request
from harness.vocab import ALLOWED_WARRANTY_SENTENCE
from tests.conftest import FakeClient, block_text, json_response
from tests.support import REPO_ROOT, TENANT

FIXTURE = Path(__file__).parent / "fixtures" / "listicle_c74" / "real_runs.json"
REAL = json.loads(FIXTURE.read_text())


def _real(key):
    return copy.deepcopy(REAL[key])


def _fix(page, failures, facts_pack, ad_brief=None):
    valid = {c["id"] for c in facts_pack["verified_claims"]}
    return apply_deterministic_fixes(
        page, failures, valid, cartridge_name="listicle", facts_pack=facts_pack, tenant=TENANT,
        ad_brief=ad_brief,
    )


def _uncited(page, facts_pack):
    valid = {c["id"] for c in facts_pack["verified_claims"]}
    return validate_page_claim_ids(page, valid, facts_pack["digit_exempt_terms"])


def _quality(page, facts_pack, ad_brief=None, style=None):
    forms = _product_name_forms(facts_pack, TENANT)
    return listicle_quality.find_quality_violations(
        page, style=style, ad_brief=ad_brief, facts_pack=facts_pack,
        brand_regex=_brand_spelling_regex(facts_pack, TENANT), display_name=TENANT.display_name,
        name_words=[w for f in forms for w in (f[0], f[1])],
    )


def _keys(problems):
    return [p.get("key", "") for p in problems]


# ---------------------------------------------------------------------------
# Stop cause 6: invalid page.json on a repair (tolerant parse).
# ---------------------------------------------------------------------------

def test_a_quoted_phrase_ending_in_quote_comma_parses_losslessly():
    # "Expecting property name enclosed in double quotes" ~col 5400 (runs
    # dkvo, k3ee, spyo): an ad quote inside a string whose closing mark is
    # followed by a comma ('... costs", which ...') -- the parser closes the
    # string there, reads the comma as a separator, then finds prose.
    page = _real("2sxl")["page"]
    page["faq"]["questions"][0]["answer"] = 'She put it simply: "Just tell me how much this costs", which is fair.'
    broken = json.dumps(page).replace('\\"Just tell me how much this costs\\"', '"Just tell me how much this costs"')
    with pytest.raises(json.JSONDecodeError) as exc:
        json.loads(broken)
    assert exc.value.pos > 4000
    parsed, fixes = extract_json_tolerant(broken)
    assert parsed == page
    assert set(fixes) == {"escaped quote"}


def test_a_trailing_comma_before_a_closing_brace_parses():
    page = _real("2sxl")["page"]
    text = json.dumps(page)
    broken = text.replace('"claim_ids": ["warranty-terms"]}', '"claim_ids": ["warranty-terms"],}', 1)
    with pytest.raises(json.JSONDecodeError):
        json.loads(broken)
    parsed, fixes = extract_json_tolerant(broken)
    assert parsed == page
    assert fixes == ["trailing comma"]


def test_an_unescaped_ad_quote_inside_a_string_parses_losslessly():
    # "Expecting ',' delimiter" (runs ubo4, k4ze): the ad speaker's words
    # quoted with bare quotation marks inside a JSON string.
    raw = '{"text": "In the ad, she says, "I don\'t want to talk to anyone."", "attributed_to_customer": true}'
    parsed, fixes = extract_json_tolerant(raw)
    assert parsed == {"text": 'In the ad, she says, "I don\'t want to talk to anyone."', "attributed_to_customer": True}
    assert set(fixes) == {"escaped quote"}


def test_json_that_is_really_broken_still_raises():
    for raw in ('{"a": "x" "b": 1}', '{"a": [1, 2', '{"a": tru}'):
        with pytest.raises(json.JSONDecodeError):
            extract_json_tolerant(raw)


def test_write_page_accepts_a_trailing_comma_page_without_a_retry_call(tmp_path):
    from harness.write import write_page

    data = _real("2sxl")
    text = json.dumps(data["page"]).replace('"claim_ids": ["warranty-terms"]}', '"claim_ids": ["warranty-terms"],}', 1)
    client = FakeClient([text])
    log = RunLog("c74", tmp_path / "run.log")
    try:
        page = write_page(
            cartridge_name="listicle", cartridges_dir=REPO_ROOT / "cartridges", ad_brief=data["ad_brief"],
            facts_pack=data["facts_pack"], client=client, model="claude-sonnet-5", budget=Budget(), log=log,
            tenant=TENANT, listicle_style="myths",
        )
    finally:
        log.close()
    assert page == data["page"]
    assert len(client.messages.calls) == 1
    assert "tolerant parse on attempt 1: trailing comma" in (tmp_path / "run.log").read_text()


# ---------------------------------------------------------------------------
# Repairs run on the writer model.
# ---------------------------------------------------------------------------

def test_repairs_default_to_the_writer_model():
    assert config.DEFAULT_MODELS["repair_first"] == config.DEFAULT_MODELS["write"] == "claude-sonnet-5"
    assert TENANT.model_for("repair_first") == TENANT.model_for("write")


# ---------------------------------------------------------------------------
# Stop cause 1: a number with no claim_id -- cited from the claim that states it.
# ---------------------------------------------------------------------------

def test_real_uncited_outlet_body_gets_the_claim_that_states_120v_15a():
    data = _real("tfzx")
    facts_pack = data["facts_pack"]
    page = {"reasons": [{
        "number": 4, "heading": "Ruling it out over the outlet", "text": data["failing_item_text"],
        "proof": {"text": "Two Bluetooth speakers are built in.", "claim_ids": ["spec-mini-audio"]},
    }]}
    failures = _uncited(page, facts_pack)
    assert [f["path"] for f in failures] == ["$.reasons[0]"]
    _fix(page, failures, facts_pack)
    assert page["reasons"][0]["claim_ids"] == ["spec-mini-electrical"]
    assert _uncited(page, facts_pack) == []


def test_real_uncited_audience_fit_line_gets_the_outlet_claim():
    data = _real("tfzx")
    facts_pack = data["facts_pack"]
    text = REAL["ubo4"]["failing_texts"]["$.audience_fit.not_for_you[1]"]
    page = {"audience_fit": {"for_you": [], "not_for_you": [{"text": "x"}, {"text": text}]}}
    failures = _uncited(page, facts_pack)
    _fix(page, failures, facts_pack)
    cited = page["audience_fit"]["not_for_you"][1]["claim_ids"]
    texts = {c["id"]: c["text"] for c in facts_pack["verified_claims"]}
    assert len(cited) == 1 and "120V" in texts[cited[0]] and "outlet" in texts[cited[0]]
    assert _uncited(page, facts_pack) == []


def test_real_membership_math_no_claim_states_stays_uncited():
    # True positive: "$200 per month, or $2,400 a year" is the ad speaker's
    # estimate, stated as fact -- no verified claim states it, so the line
    # stays failing and goes to the writer.
    data = _real("tfzx")
    facts_pack = data["facts_pack"]
    page = {"reasons": [{"number": 3, "heading": "h", "text": REAL["ubo4"]["failing_texts"]["$.reasons[2]"]}]}
    failures = _uncited(page, facts_pack)
    _fix(page, failures, facts_pack)
    assert "claim_ids" not in page["reasons"][0]
    assert [f["path"] for f in _uncited(page, facts_pack)] == ["$.reasons[0]"]


def test_a_shared_digit_never_borrows_an_unrelated_claim():
    # "7" is in the shipping claim ("3-7 business days") but nothing else in
    # this line is about shipping.
    facts_pack = _real("tfzx")["facts_pack"]
    page = {"reasons": [{"number": 1, "heading": "h", "text": "Most people quit after 7 visits to a gym."}]}
    _fix(page, _uncited(page, facts_pack), facts_pack)
    assert "claim_ids" not in page["reasons"][0]


def test_a_faq_answer_stating_the_price_gets_the_price_claim():
    facts_pack = _real("tfzx")["facts_pack"]
    page = {"faq": {"questions": [{"question": "What does it cost?", "answer": "The Peak Mini is priced at $5,450."}]}}
    failures = [f for f in listicle.find_faq_violations(page) if f["key"].startswith("listicle:faq_claims")]
    assert len(failures) == 1
    _fix(page, failures, facts_pack)
    assert page["faq"]["questions"][0]["claim_ids"] == ["price-mini"]
    assert [f for f in listicle.find_faq_violations(page) if f["key"].startswith("listicle:faq_claims")] == []


# ---------------------------------------------------------------------------
# Stop causes 2 and 3: quotes -- select, do not generate.
# ---------------------------------------------------------------------------

def test_quote_candidates_are_the_speakers_own_sentences_and_each_passes_fidelity():
    brief = _real("2sxl")["ad_brief"]
    candidates = safe_quote_candidates(brief)
    texts = [c["text"] for c in candidates]
    assert "I had all the information laid out right there." in texts
    assert [c["id"] for c in candidates] == [f"q{i}" for i in range(1, len(candidates) + 1)]
    sources = quote_fidelity.speaker_sources(brief)
    for text in texts:
        line = f'In the ad, she says, "{text}"'
        assert quote_fidelity.check_attributed_text(line, sources) == []


def test_quote_candidates_drop_numbers_and_trigger_words():
    # Run dkvo STOPped on a faithful quote with "medical grade" in it.
    brief = _real("tfzx")["ad_brief"]
    texts = [c["text"] for c in safe_quote_candidates(brief)]
    assert texts, "the product-features ad still has quotable sentences"
    assert not any("medical" in t.lower() for t in texts)
    assert not any("lifetime warranty" in t.lower() for t in texts)
    assert not any(ch.isdigit() for t in texts for ch in t)
    assert "I'm really excited it has medical grade red light therapy." in quote_fidelity.quote_candidates(brief)


def test_a_brand_voice_ad_has_no_quote_candidates():
    brief = dict(_real("2sxl")["ad_brief"], speaker_pov="brand")
    assert safe_quote_candidates(brief) == []


def test_real_unfaithful_quote_snaps_to_the_verbatim_candidate_and_passes():
    data = _real("k4ze")
    page = {"reasons": [{"number": 1, "heading": "h", "text": "body",
                         "proof": {"text": data["failing_proof"], "attributed_to_customer": True}}]}
    failures = quote_fidelity.find_unfaithful_attribution(page, data["ad_brief"])
    assert [f["key"] for f in failures] == ["quote_fidelity:$.reasons[0].proof"]
    _fix(page, failures, _real("2sxl")["facts_pack"], ad_brief=data["ad_brief"])
    assert page["reasons"][0]["proof"]["text"] == 'As one shopper put it, "I had all the information laid out right there."'
    assert quote_fidelity.find_unfaithful_attribution(page, data["ad_brief"]) == []


def test_real_narrated_attribution_with_no_matching_quote_still_fails():
    # True positive: no single ad sentence matches, so nothing is snapped
    # and the line goes to the writer with the candidate list.
    data = _real("k3ee")
    page = {"reasons": [{"number": 2, "heading": "h", "text": "body",
                         "proof": {"text": data["failing_proof"], "attributed_to_customer": True}}]}
    failures = quote_fidelity.find_unfaithful_attribution(page, data["ad_brief"])
    _fix(page, failures, _real("2sxl")["facts_pack"], ad_brief=data["ad_brief"])
    assert page["reasons"][0]["proof"]["text"] == data["failing_proof"]
    assert quote_fidelity.find_unfaithful_attribution(page, data["ad_brief"]) != []


def test_real_medical_quote_still_needs_a_claim_id():
    # True positive kept: attribution never excuses a trigger word.
    proof = REAL["dkvo"]["failing_proof"]
    facts_pack = _real("tfzx")["facts_pack"]
    page = {"reasons": [{"number": 3, "heading": "h", "text": "Red light comes standard.",
                         "claim_ids": ["spec-mini-red-light"],
                         "proof": {"text": proof, "attributed_to_customer": True}}]}
    failures = _uncited(page, facts_pack)
    assert [f["path"] for f in failures] == ["$.reasons[0].proof"]
    assert 'uses the word "medical"' in failures[0]["issue"]


# ---------------------------------------------------------------------------
# Stop cause 4: "session by session" as a billing cadence.
# ---------------------------------------------------------------------------

def test_real_pay_session_by_session_is_not_a_fake_test():
    page = {"audience_fit": {"for_you": [{"text": REAL["lnp5"]["failing_text"]}]}}
    assert listicle.find_fake_test_violations(page) == []


@pytest.mark.parametrize("text", [
    "We noticed the heat build session by session.",
    "Session by session, the cabin warmed faster.",
    "We used it session by session for a month, and you pay once.",
])
def test_session_by_session_as_a_usage_period_still_fails(text):
    assert listicle.find_fake_test_violations({"x": text})


# ---------------------------------------------------------------------------
# Stop cause 5: an English compound read as a leaked claim id.
# ---------------------------------------------------------------------------

def test_real_founder_led_is_not_a_leaked_claim_id():
    data = _real("y6pz")
    page = {"reasons": [{"text": data["failing_text"]}]}
    assert find_leaked_claim_ids(page, set(data["valid_claim_ids"])) == []
    assert find_leaked_claim_ids_visible_text(f"<p>{data['failing_text']}</p>", set(data["valid_claim_ids"])) == []


@pytest.mark.parametrize("text, token", [
    ("Our founder-ceo says so.", "founder-ceo"),           # an exact id
    ("See founder-story for details.", "founder-story"),   # an id-shaped prefix match
    ("It seats two (spec-fuji-capacity).", "spec-fuji-capacity"),
])
def test_a_real_leaked_id_still_fails(text, token):
    valid = set(REAL["y6pz"]["valid_claim_ids"]) | {"spec-fuji-capacity"}
    hits = find_leaked_claim_ids({"t": text}, valid)
    assert [h["issue"].split(": ")[1].split(" in ")[0] for h in hits] == [token]


# ---------------------------------------------------------------------------
# The warranty fix never makes a heading into the fixed sentence.
# ---------------------------------------------------------------------------

def test_warranty_fix_leaves_a_heading_for_the_writer_and_fixes_the_body():
    from harness.claims import find_warranty_violations

    facts_pack = _real("2sxl")["facts_pack"]
    page = {"reasons": [{
        "number": 6, "heading": "A lifetime warranty covers everything",
        "text": "Coverage matters. A lifetime warranty covers the whole cabin.",
        "claim_ids": [],
        "proof": {"text": ALLOWED_WARRANTY_SENTENCE, "claim_ids": ["warranty-terms"]},
    }]}
    failures = find_warranty_violations(page, facts_pack["verified_claims"])
    assert {f["path"] for f in failures} >= {"$.reasons[0].heading", "$.reasons[0].text"}
    _fix(page, failures, facts_pack)
    item = page["reasons"][0]
    assert item["heading"] == "A lifetime warranty covers everything"
    assert item["text"] == "Coverage matters. " + ALLOWED_WARRANTY_SENTENCE
    remaining = find_warranty_violations(page, facts_pack["verified_claims"])
    assert [f["path"] for f in remaining] == ["$.reasons[0].heading"]


# ---------------------------------------------------------------------------
# Copy-quality gates, on the real page 2sxl.
# ---------------------------------------------------------------------------

def test_real_page_2sxl_fails_the_quality_gates_it_should():
    data = _real("2sxl")
    problems = _quality(data["page"], data["facts_pack"], data["ad_brief"], style="myths")
    keys = _keys(problems)
    assert "listicle:item_repeats:5" in keys                       # item 6: one sentence three times
    assert "listicle:myth_item:5" in keys                          # ... and it is not a myth
    assert "listicle:meta_reference:$.reasons[4].text" in keys     # "As the ad speaker found"
    assert "brand_spelling:$.reasons[4].text" in keys              # "Peak saunas" next to "PEAK"
    assert any(k.startswith("listicle:repeat_number:20:") for k in keys)  # 120V/20A in six fields
    assert "listicle:message_match" not in keys                    # its headline does carry the hook


def test_item_with_three_different_lines_passes_the_repeat_check():
    page = {"reasons": [{"heading": "You have to call to get a price",
                         "text": "Plenty of sites hide the number.",
                         "proof": {"text": "The price is on the product page.", "claim_ids": ["price-fuji"]}}]}
    assert listicle_quality.find_item_repeat_violations(page) == []


def test_real_restating_proof_fails_and_a_proof_that_adds_the_fact_passes():
    item = _real("nsrb")["item"]
    score = listicle_quality.restate_score(item["proof"]["text"], item["text"],
                                           listicle_quality._exempt_stems(["PEAK", "Fuji", "Peak Fuji"]))
    assert score >= listicle_quality.RESTATE_THRESHOLD
    page = {"reasons": [item]}
    assert _keys(listicle_quality.find_proof_restates_body_violations(page)) == ["listicle:proof_restates_body:0"]
    # The 2sxl item 1 shape: body sets up the point, proof adds the price.
    good = _real("2sxl")["page"]["reasons"][0]
    assert listicle_quality.find_proof_restates_body_violations({"reasons": [good]}) == []


def test_a_number_in_three_fields_passes_and_in_four_fails():
    def page(n):
        return {"reasons": [{"text": f"Line {i} about a 120V outlet.", "proof": {"text": "x"}} for i in range(n)]}
    assert listicle_quality.find_repetition_violations(page(3)) == []
    assert _keys(listicle_quality.find_repetition_violations(page(4))) == ["listicle:repeat_number:120:4"]


def test_the_faq_repeating_an_item_sentence_fails():
    page = {
        "reasons": [{"text": "The Peak Fuji is priced at $8,250 on its own page.", "proof": {"text": "p"}}],
        "faq": {"questions": [{"question": "q", "answer": "Yes. The Peak Fuji is priced at $8,250 on its own page."}]},
    }
    assert _keys(listicle_quality.find_repetition_violations(page)) == [
        "listicle:repeat_sentence:$.faq.questions[0].answer"
    ]


def test_brand_spelling_is_fixed_deterministically_and_leaves_names_and_exceptions_alone():
    data = _real("2sxl")
    facts_pack = data["facts_pack"]
    regex = _brand_spelling_regex(facts_pack, TENANT)
    assert regex is not None
    for ok in ("The Peak Fuji is priced openly.", "Preheat it in the Peak Saunas app.", "Every PEAK cabin ships free."):
        assert not regex.search(ok), ok
    page = data["page"]
    failures = [p for p in _quality(page, facts_pack) if p["key"].startswith("brand_spelling:")]
    _fix(page, failures, facts_pack)
    assert "came across PEAK saunas" in page["reasons"][4]["text"]
    assert [p for p in _quality(page, facts_pack) if p["key"].startswith("brand_spelling:")] == []


@pytest.mark.parametrize("text", [
    'In the ad, she says, "I had all the information laid out right there."',
    '"Didn\'t have to talk to anyone," she says in the ad.',
    "In the ad, Jane Doe says he built the company to fix this.",
    "Look at the price before anything else.",
])
def test_allowed_quote_frames_are_not_meta_references(text):
    assert listicle_quality.find_meta_reference_violations({"x": text}) == []


@pytest.mark.parametrize("text", [
    "As the ad speaker found, some brands hide the price.",
    "The speaker in the video found it easy.",
    "This ad shows a better way to shop.",
])
def test_narration_about_the_source_fails(text):
    assert listicle_quality.find_meta_reference_violations({"x": text})


def test_a_dek_narrating_the_speaker_fails_and_a_dek_to_the_reader_passes():
    # Smoke run 20261005-162336-product-features-v2-xkkd's dek.
    bad = {"dek": "She ordered the Peak Mini because it fits a small footprint and plugs into a normal outlet, "
                  "no electrician involved."}
    assert _keys(listicle_quality.find_meta_reference_violations(bad)) == ["listicle:meta_reference:$.dek"]
    good = {"dek": "A small footprint and a normal outlet: what to check before you rule out a home sauna.",
            "reasons": [{"text": "In the ad, she says the outlet was the deciding detail."}]}
    assert listicle_quality.find_meta_reference_violations(good) == []


def test_real_myth_headings_pass_and_a_spec_or_product_heading_fails():
    data = _real("2sxl")
    page = data["page"]
    names = [TENANT.display_name, "Fuji", "Peak Fuji"]
    texts = [c["text"] for c in data["facts_pack"]["verified_claims"]]
    keys = _keys(listicle_quality.find_myth_item_violations(page, "myths", texts, names))
    assert keys == ["listicle:myth_item:5"]
    page["reasons"][0]["heading"] = "The Peak Fuji is easy to install"
    assert "listicle:myth_item:0" in _keys(listicle_quality.find_myth_item_violations(page, "myths", texts, names))
    assert listicle_quality.find_myth_item_violations(page, "reasons", texts, names) == []


def test_message_match_fails_a_generic_top_and_passes_one_that_carries_the_hook():
    brief = _real("2sxl")["ad_brief"]
    generic = {"headline": "5 Mistakes Careful Buyers Make When Buying Home Saunas",
               "dek": "Avoid these common oversights before you invest in a sauna for your home."}
    assert _keys(listicle_quality.find_message_match_violations(generic, brief)) == ["listicle:message_match"]
    hooked = dict(generic, dek="Not being able to find the price online is the first mistake.")
    assert listicle_quality.find_message_match_violations(hooked, brief) == []


def test_check_page_gates_runs_the_quality_gates_for_the_listicle_only():
    data = _real("2sxl")
    problems = check_page_gates(
        data["page"], data["facts_pack"], "listicle", financing_lender=None, speaker_pov="first_person",
        word_range=None, allowed_cta_texts=["Shop the Fuji"], ad_brief=data["ad_brief"], tenant=TENANT,
        listicle_style="myths",
    )
    assert "listicle:item_repeats:5" in _keys(problems)


# ---------------------------------------------------------------------------
# Allowed numbers and the writer prompt.
# ---------------------------------------------------------------------------

def test_allowed_numbers_list_each_verified_number_with_its_claims():
    facts_pack = _real("tfzx")["facts_pack"]
    numbers = {n["number"]: n["claim_ids"] for n in listicle_quality.allowed_numbers(facts_pack)}
    assert "spec-mini-electrical" in numbers["120V"]
    assert numbers["$5,450"] == ["price-mini"]
    assert not any("Person" in n for n in numbers)


def test_the_listicle_prompt_carries_quotes_numbers_and_the_quality_rules():
    data = _real("2sxl")
    _schema, kwargs = build_initial_write_request(
        cartridge_name="listicle", cartridges_dir=REPO_ROOT / "cartridges", ad_brief=data["ad_brief"],
        facts_pack=data["facts_pack"], model="claude-sonnet-5", tenant=TENANT, listicle_style="myths",
    )
    system = "".join(b["text"] for b in kwargs["system"])
    user = block_text(kwargs["messages"][0]["content"])
    assert '"ad_quotes"' in user and "I had all the information laid out right there." in user
    assert '"allowed_numbers"' in user and "$8,250" in user
    assert "three different lines" in system
    assert f"written exactly {TENANT.display_name!r}" in system
    assert "Myths: every item heading is a belief" in system
    assert "the ad speaker" in system


def test_other_cartridges_get_no_listicle_payload():
    data = _real("2sxl")
    _schema, kwargs = build_initial_write_request(
        cartridge_name="article", cartridges_dir=REPO_ROOT / "cartridges", ad_brief=data["ad_brief"],
        facts_pack=data["facts_pack"], model="claude-sonnet-5", tenant=TENANT,
    )
    assert '"ad_quotes"' not in block_text(kwargs["messages"][0]["content"])


# ---------------------------------------------------------------------------
# Patch repairs.
# ---------------------------------------------------------------------------

def test_apply_edits_replaces_only_allowed_nodes():
    page = {"headline": "h", "reasons": [{"text": "a", "proof": {"text": "p"}}, {"text": "b"}], "faq": {"questions": []}}
    out = pagepatch.apply_edits(page, [{"path": "$.reasons[0].proof.text", "value": "new"}], ["$.reasons[0]"])
    assert out["reasons"][0]["proof"]["text"] == "new" and page["reasons"][0]["proof"]["text"] == "p"
    for edits, msg in (
        ([{"path": "$.reasons[1].text", "value": "x"}], "outside"),
        ([{"path": "$.reasons[5]", "value": {}}], "outside"),
        ([{"path": "$", "value": {}}], "whole page"),
        ([{"path": "reasons[0]", "value": {}}], "bad path"),
        ([], "non-empty"),
    ):
        with pytest.raises(pagepatch.PatchError, match=msg):
            pagepatch.apply_edits(page, edits, ["$.reasons[0]"])
    with pytest.raises(pagepatch.PatchError, match="does not resolve"):
        pagepatch.apply_edits(page, [{"path": "$.reasons[2]", "value": {}}], ["$.reasons"])


def test_an_edit_must_keep_the_type_of_the_node_it_replaces():
    # Live smoke run 20261005-162640-hidden-costs-v2-3rtc: an object written
    # onto an FAQ "answer" string passed the schema check and crashed the
    # FAQ gate. Now the edit list is rejected, and write_page asks again.
    page = {"faq": {"questions": [{"question": "q", "answer": "It plugs into a dedicated 120V / 20A outlet."}]}}
    bad = [{"path": "$.faq.questions[0].answer",
            "value": {"answer": "A standard outlet.", "claim_ids": ["spec-fuji-electrical-requirement"]}}]
    with pytest.raises(pagepatch.PatchError, match="must be a string"):
        pagepatch.apply_edits(page, bad, ["$.faq.questions[0]"])
    good = [{"path": "$.faq.questions[0]",
             "value": {"question": "q", "answer": "A standard outlet.", "claim_ids": ["x"]}}]
    assert pagepatch.apply_edits(page, good, ["$.faq.questions[0]"])["faq"]["questions"][0]["claim_ids"] == ["x"]
    # a key the node does not have yet may be added with any type
    added = pagepatch.apply_edits(page, [{"path": "$.faq.questions[0].claim_ids", "value": ["x"]}],
                                  ["$.faq.questions[0]"])
    assert added["faq"]["questions"][0]["claim_ids"] == ["x"]


def test_write_page_asks_again_after_a_mistyped_patch(tmp_path):
    from harness.write import write_page

    data = _real("2sxl")
    page = data["page"]
    bad = {"edits": [{"path": "$.faq.questions[1].answer", "value": {"answer": "x"}}]}
    good = {"edits": [{"path": "$.faq.questions[1].answer", "value": "Confirm the outlet before you buy."}]}
    client = FakeClient([json_response(bad), json_response(good)])
    log = RunLog("c74", tmp_path / "run.log")
    try:
        out = write_page(
            cartridge_name="listicle", cartridges_dir=REPO_ROOT / "cartridges", ad_brief=data["ad_brief"],
            facts_pack=data["facts_pack"], client=client, model="claude-sonnet-5", budget=Budget(), log=log,
            tenant=TENANT, listicle_style="myths", revision_note="## REVISION REQUIRED",
            current_page=page, patch_roots=["$.faq.questions[1]"],
        )
    finally:
        log.close()
    assert out["faq"]["questions"][1]["answer"] == "Confirm the outlet before you buy."
    assert len(client.messages.calls) == 2
    assert "must be a string" in client.messages.calls[1]["messages"][-1]["content"]


def test_edit_root_is_the_flagged_fields_list_item():
    assert pagepatch.edit_root("$.reasons[2].proof.text") == "$.reasons[2]"
    assert pagepatch.edit_root("$.faq.questions[1].answer") == "$.faq.questions[1]"
    assert pagepatch.edit_root("$.headline") == "$.headline"


def test_a_listicle_repair_is_a_patch_of_the_flagged_item_only(tmp_path):
    data = _real("2sxl")
    good = copy.deepcopy(data["page"])
    # Make the real page pass everything except one flagged line.
    good["reasons"][5] = {
        "number": 6, "heading": "A warranty only matters once you read it",
        "text": "Coverage that is only explained after you pay is not much use, so read it first.",
        "image": data["page"]["reasons"][5]["image"],
        "proof": {"text": ALLOWED_WARRANTY_SENTENCE, "claim_ids": ["warranty-terms"]},
    }
    good["reasons"][4]["text"] = ("Some brands ask for a name and a phone number before they show anything. "
                                  "Here the information is on the page.")
    for entry in good["audience_fit"]["for_you"] + good["audience_fit"]["not_for_you"]:
        entry["text"] = entry["text"].replace("120V/20A", "standard")
    good["faq"]["questions"][1]["answer"] = "Confirm the outlet near your install spot before you buy."
    good["faq"]["questions"][1]["claim_ids"] = []
    # cycle 79 first-screen fields
    good.update(eyebrow="For careful buyers", display_headline="Myths, checked against the specs", accent_phrase=" ".join(good["headline"].split()[-2:]),
                lede="What would you want to know before you buy?", scroll_cue="Start with the first myth",
                hero_quote_id="q1")
    bad = copy.deepcopy(good)
    bad["reasons"][4]["text"] = "As the ad speaker found, some brands hide everything. " + good["reasons"][4]["text"]

    gate_args = dict(financing_lender="Bread Pay", speaker_pov="first_person", word_range=None,
                     allowed_cta_texts=["Shop the Fuji"], ad_brief=data["ad_brief"], tenant=TENANT,
                     listicle_style="myths", listicle_headline=None)
    assert check_page_gates(good, data["facts_pack"], "listicle", **gate_args) == []
    assert _keys(check_page_gates(bad, data["facts_pack"], "listicle", **gate_args)) == [
        "listicle:meta_reference:$.reasons[4].text"
    ]

    edit = {"edits": [{"path": "$.reasons[4].text", "value": good["reasons"][4]["text"]}]}
    client = FakeClient([json_response(bad), json_response(edit)])
    log = RunLog("c74", tmp_path / "run.log")
    try:
        page, attempts, _fixes = write_and_gate_page(
            cartridge_name="listicle", cartridges_dir=REPO_ROOT / "cartridges", ad_brief=data["ad_brief"],
            facts_pack=data["facts_pack"], client=client, model="claude-sonnet-5", budget=Budget(), log=log,
            financing_lender="Bread Pay", speaker_pov="first_person", tenant=TENANT, listicle_style="myths",
            repair_first_model="claude-sonnet-5",
        )
    finally:
        log.close()
    assert page == good
    assert len(attempts) == 2 and attempts[1] == []
    repair_msg = block_text(client.messages.calls[1]["messages"][0]["content"])
    assert '"current_page"' in repair_msg
    assert "patch the flagged fields only" in repair_msg
    assert "$.reasons[4]" in repair_msg
    assert "patch: $.reasons[4].text" in (tmp_path / "run.log").read_text()


def test_patch_note_names_the_roots_and_the_answer_shape():
    note = build_patch_revision_note(1, [{"path": "$.reasons[2].proof", "issue": "x"}], ["$.reasons[2]"])
    assert "REVISION REQUIRED" in note
    assert '{"edits": [{"path":' in note
    assert "$.reasons[2]" in note
