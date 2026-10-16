"""Cycle 110 round 4: writer-vs-gate dead ends of two fresh runs on master
(2026-10-16): zt6o (sale landing, landing:urgency) and 2jon (PA listicle,
health_words / message_match / speaker_pronoun). The runs STOPped, so they
have no page.json; the failing texts are from their run logs."""
import json

from harness import landing
from tests.support import REPO_ROOT, TENANT

FX = REPO_ROOT / "tests" / "fixtures" / "c110"


def _load(run, name):
    return json.loads((FX / run / f"{name}.json").read_text())


# ---------------------------------------------------------------------------
# W1: the two deadline wordings the urgency gate allows, said verbatim
# ---------------------------------------------------------------------------

def test_w1_the_zt6o_writer_is_told_the_verbatim_deadline_and_the_gate_still_fails_its_headline():
    brief, fp = _load("zt6o", "ad_brief"), _load("zt6o", "facts_pack")
    text = " ".join(landing.writer_lines(fp, TENANT, look="peak", ad_brief=brief))
    assert '"Prices go up on October 16, 2026."' in text
    assert "today's price ends {deadline_day}" in text
    for phrase in ("before prices go up", "today only", "last chance", "hurry", "don't miss"):
        assert f'"{phrase}"' in text
    # zt6o attempts 1 and 4, then attempt 2
    for headline, key in (("{offer_amount} off the Fuji, before prices go up", "landing:urgency:deadline"),
                          ("{offer_amount} off the Peak Fuji, today only", "landing:urgency:today only")):
        problems = [p for p in landing.find_landing_violations({"hero": {"headline": headline}}, fp, ad_brief=brief,
                                                               tenant=TENANT) if p["key"] == key]
        assert problems and "replace the sentence with: 'Prices go up on October 16, 2026.'" in problems[0]["issue"]


# ---------------------------------------------------------------------------
# W2: a medical hook is met by the speaker's own sentence, word for word
# ---------------------------------------------------------------------------

JON_HEADLINE = "He wanted relief for her. His garage had no outlet ready."      # 2jon attempt 3
JON_SENTENCE = ("It feels great in her joints and there's emerging research looking at how whole body heat "
                "exposure may influence inflammatory and cardiovascular pathways.")


def _jon_keys(headline, dek="A lot of buyers chase a feature list and miss the setup checks that matter."):
    from harness import first_screen, listicle_persuasion, listicle_quality

    brief = _load("2jon", "ad_brief")
    page = {"headline": headline, "dek": dek}
    keys = [p["key"] for p in listicle_persuasion.find_health_word_violations(page, brief)]
    keys += [p["key"] for p in listicle_quality.find_message_match_violations(page, brief)]
    keys += [p["key"] for p in first_screen.find_speaker_pronoun_violations(page, brief)]
    keys += [p["key"] for p in first_screen.find_speaker_slot_word_violations(page, brief, _load("2jon", "facts_pack"))]
    return keys


def test_w2_the_2jon_headline_fails_and_a_verbatim_transcript_sentence_passes():
    brief = _load("2jon", "ad_brief")
    assert brief["speaker_pronoun"] == "he" and JON_SENTENCE in brief["transcript_or_text"]
    assert set(_jon_keys(JON_HEADLINE)) >= {"listicle:health_words:$.headline", "listicle:message_match",
                                             "listicle:speaker_pronoun:$.headline", "listicle:speaker_slot_words"}
    # the speaker's own sentence in quotation marks: "joints" and "her" are his words
    assert _jon_keys(f'"{JON_SENTENCE}"') == []


def test_w2_the_2jon_writer_and_repair_are_told_to_quote_a_whole_transcript_sentence():
    from harness import listicle_persuasion, write

    brief, fp = _load("2jon", "ad_brief"), _load("2jon", "facts_pack")
    lines = []
    write._append_listicle_style_guidance(lines, "listicle", "reasons", tenant=TENANT, include_draft=False,
                                          ad_brief=brief, facts_pack=fp)
    text = " ".join(lines)
    assert listicle_persuasion.MEDICAL_HOOK_LINE in text and json.dumps(JON_SENTENCE) in text
    issue = listicle_persuasion.find_health_word_violations({"headline": JON_HEADLINE}, brief)[0]["issue"]
    assert "one whole sentence of the speaker's transcript, word for word, in quotation marks" in issue
    # a hook with no health word gets neither
    assert listicle_persuasion.medical_hook_lines(_load("zt6o", "ad_brief")) == []


def test_w3_the_listicle_quote_frame_example_follows_the_pronoun_evidence():
    from harness import listicle, listicle_persuasion

    brief = _load("2jon", "ad_brief")
    mine = " ".join(listicle_persuasion.writer_lines(brief, TENANT))
    assert 'the quote frame ("In the ad, he says, ...").' in mine and "she" not in mine.split("quote frame")[1]
    assert 'the quote frame ("In the ad, he says, ...")' in " ".join(listicle.writer_rules_lines(brief, TENANT))
    mine = " ".join(listicle_persuasion.writer_lines(dict(brief, speaker_pronoun=None), TENANT))
    assert ('the quote frame ("In the ad, the creator says, ..." (or the speaker\'s name); never she/he as a '
            "guess).") in mine
