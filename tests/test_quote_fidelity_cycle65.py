"""Cycle 65: an attributed_to_customer line must say what the ad speaker
actually said. Live page listicle-test-1 (run
20260922-191713-hidden-costs-v2-55n2, $.reasons[0].proof) published "One
customer told us she almost gave up on her search entirely before finding a
brand that just showed the number." -- the speaker said nothing like it.
"""

import pytest

from harness import tenant as tenant_mod
from harness.claims import ClaimsGateFailure, find_missing_attribution, gate_page_json
from harness.quote_fidelity import (
    FIDELITY_THRESHOLD,
    check_attributed_text,
    find_unfaithful_attribution,
    score_attributed_text,
    speaker_sources,
)
from harness.repair import build_revision_note

# That run's own ad_brief, verbatim.
HIDDEN_COSTS_BRIEF = {
    "speaker_pov": "first_person",
    "speaker_experience": [
        "I hated calling for a price on websites",
        "I've been trying to find an at-home sauna and so many brands make you provide your name and "
        "number before showing the price",
        "I came across Peak saunas and had all the information laid out right there without having to "
        "talk to anyone",
    ],
    "transcript_or_text": (
        "There's nothing worse than trying to buy something online and not being able to find the price "
        "anywhere. I mean, it's making me put in my name, my number, my phone number. It's 2026. I don't "
        "want to talk to anyone. Just tell me how much this costs. I've been trying to find an at-home "
        "sauna and so many of the brands make me do this. I did come across those Peak saunas. I had all "
        "the information laid out right there. Didn't have to talk to anyone. Like that's the type of "
        "experience we're looking for these days."
    ),
}
SOURCES = speaker_sources(HIDDEN_COSTS_BRIEF)

EMBELLISHED = (
    "One customer told us she almost gave up on her search entirely before finding a brand that just "
    "showed the number."
)
PARAPHRASE = (
    "In the ad, she says she found all the information laid out right there and did not have to talk "
    "to anyone to get a price."
)
VERBATIM = 'In the ad, she says: "I don\'t want to talk to anyone. Just tell me how much this costs."'


def _proof_page(text):
    return {"reasons": [{"number": 1, "proof": {"text": text, "attributed_to_customer": True}}]}


def test_the_live_embellished_line_fails():
    problems = check_attributed_text(EMBELLISHED, SOURCES)
    assert score_attributed_text(EMBELLISHED, SOURCES) < FIDELITY_THRESHOLD
    assert any('"almost"' in p for p in problems)
    assert any('"gave up"' in p for p in problems)
    assert any('"told us"' in p for p in problems)


def test_the_embellishment_alone_fails_even_with_a_neutral_frame():
    text = "In the ad, she says she almost gave up before she found all the information laid out right there."
    problems = check_attributed_text(text, SOURCES)
    assert problems and all("frame" not in p for p in problems)


def test_a_faithful_paraphrase_passes():
    assert check_attributed_text(PARAPHRASE, SOURCES) == []


def test_a_verbatim_quote_passes():
    assert score_attributed_text(VERBATIM, SOURCES) == 1.0
    assert check_attributed_text(VERBATIM, SOURCES) == []


def test_a_quote_that_is_not_her_words_fails():
    text = 'In the ad, she says: "I almost gave up on sauna shopping until I found one honest brand."'
    problems = check_attributed_text(text, SOURCES)
    assert any("quoted words" in p for p in problems)


def test_an_invented_story_with_no_embellishment_word_still_fails_on_overlap():
    text = "As one shopper put it, the cedar scent made the sauna feel like a real room in the house."
    problems = check_attributed_text(text, SOURCES)
    assert any("not what the speaker said" in p for p in problems)


def test_author_narration_in_the_same_paragraph_is_not_scored_as_her_words():
    text = VERBATIM + " That single complaint comes up again and again when people research big purchases online."
    assert check_attributed_text(text, SOURCES) == []


@pytest.mark.parametrize("frame", [
    "One customer told us",
    "She told us",
    "A buyer said",
    "One owner said",
])
def test_misleading_frames_fail_when_the_speaker_is_not_a_verified_customer(frame):
    text = f"{frame} she found all the information laid out right there and did not have to talk to anyone."
    problems = check_attributed_text(text, SOURCES, ad_speaker_verified=False)
    assert any("misleading attribution frame" in p for p in problems)


def test_a_verified_customer_may_be_called_a_customer_but_never_told_us():
    ok = '"I had all the information laid out right there. Didn\'t have to talk to anyone." -- PEAK customer'
    assert check_attributed_text(ok, SOURCES, ad_speaker_verified=True) == []
    told = "One customer told us she found all the information laid out right there."
    assert any('"told us"' in p for p in check_attributed_text(told, SOURCES, ad_speaker_verified=True))


def test_a_brand_voice_ad_has_no_speaker_to_quote():
    brief = {"speaker_pov": "brand", "transcript_or_text": "Unforgettable Glow", "speaker_experience": []}
    problems = check_attributed_text('In the ad, she says "Unforgettable Glow."', speaker_sources(brief))
    assert any("no speaker transcript" in p for p in problems)


def test_the_peak_tenant_does_not_mark_the_ad_speaker_as_a_verified_customer():
    assert tenant_mod.load_tenant("peak-saunas").get("ad_speaker_is_verified_customer") is False


def test_find_missing_attribution_accepts_the_neutral_frames():
    for text in (PARAPHRASE, "As one shopper put it, there is nothing worse than a hidden price."):
        assert find_missing_attribution(_proof_page(text)) == []


# --- wiring -------------------------------------------------------------

def test_gate_page_json_rejects_the_live_embellished_proof_line():
    with pytest.raises(ClaimsGateFailure) as exc_info:
        gate_page_json(_proof_page(EMBELLISHED), {"verified_claims": []}, "listicle", ad_brief=HIDDEN_COSTS_BRIEF)
    hits = [p for p in exc_info.value.items if p.get("key") == "quote_fidelity:$.reasons[0].proof"]
    assert len(hits) == 1
    assert "quote the speaker's own words" in hits[0]["issue"]
    assert "drop the attribution" in hits[0]["issue"]


def test_gate_page_json_passes_a_faithful_proof_line():
    gate_page_json(_proof_page(PARAPHRASE), {"verified_claims": []}, "listicle", ad_brief=HIDDEN_COSTS_BRIEF)


def test_gate_page_json_without_an_ad_brief_skips_the_check():
    # Same contract as the speaker-number exemption: no ad_brief, no check.
    gate_page_json(_proof_page(EMBELLISHED), {"verified_claims": []}, "listicle")


def test_gate_page_json_honors_the_verified_customer_flag():
    text = '"I had all the information laid out right there." -- PEAK customer'
    with pytest.raises(ClaimsGateFailure):
        gate_page_json(_proof_page(text), {"verified_claims": []}, "listicle", ad_brief=HIDDEN_COSTS_BRIEF)
    gate_page_json(_proof_page(text), {"verified_claims": []}, "listicle", ad_brief=HIDDEN_COSTS_BRIEF,
                   ad_speaker_verified=True)


def test_the_revision_note_tells_the_writer_to_quote_or_drop():
    hits = find_unfaithful_attribution(_proof_page(EMBELLISHED), HIDDEN_COSTS_BRIEF)
    note = build_revision_note(1, hits)
    assert "quote the speaker's own words from ad_brief.transcript_or_text" in note
    assert "never \"told us\"" in note


def _quote_fidelity_keys(tenant, text):
    from harness.repair import check_page_gates

    problems = check_page_gates(
        _proof_page(text), {"verified_claims": []}, "some-other-cartridge",
        financing_lender=None, speaker_pov="first_person", word_range=None, allowed_cta_texts=[],
        ad_brief=HIDDEN_COSTS_BRIEF, tenant=tenant,
    )
    return [p["key"] for p in problems if str(p.get("key", "")).startswith("quote_fidelity:")]


def test_the_repair_loop_gate_reads_the_tenant_flag():
    text = '"I had all the information laid out right there." -- PEAK customer'
    tenant = tenant_mod.load_tenant("peak-saunas")
    assert _quote_fidelity_keys(tenant, text) == ["quote_fidelity:$.reasons[0].proof"]
    tenant.config["ad_speaker_is_verified_customer"] = True
    assert _quote_fidelity_keys(tenant, text) == []


def test_the_writer_prompt_gives_the_neutral_frames():
    from harness.write import global_voice_block

    block = global_voice_block(tenant_mod.load_tenant("peak-saunas"))
    assert '"In the ad, she says ..."' in block
    assert "One customer told us" not in block
    assert 'never "told us"' in block
