"""Cycle 110 integration fixes F1-F4, each from the real text of B's smoke runs
of 2026-10-15 (lreb = a PA's video ad, ddqi = the Community Savings Sale still).
"""
import datetime

from harness import landing, landing_sell, listicle_persuasion, offer, persuasion, quote_fidelity
from tests.support import TENANT
from tests.test_landing_cycle82 import _facts_pack
from tests.test_presell_cycle110 import _offer_facts, _page, _render, _rich

# lreb: ad_brief.json (hook, angle, promise; the transcript sentence the subhead quotes)
LREB_BRIEF = {
    "hook": "I'm a board certified PA and I think a lot about what medical conditions or types of people would "
            "benefit from an infrared sauna.",
    "angle": "A medical professional's evidence-based perspective on infrared sauna benefits for specific health "
             "conditions, particularly inflammatory and rheumatologic conditions.",
    "promise": "Infrared sauna as a potential adjunct to wellness and recovery routine, with emerging research "
               "support for pain, stiffness, cardiovascular health, and overall well-being",
    "transcript_or_text": "I'm a board certified PA and I think a lot about what medical conditions or types of "
                          "people would benefit from an infrared sauna. One area I found particularly interesting "
                          "was rheumatology. It feels great in her joints and there's emerging research looking at "
                          "how whole body heat exposure may influence inflammatory and cardiovascular pathways.",
    "speaker_pov": "first_person",
    "input_type": "video",
    "source_file": "meta-120259103884780746.mp4",
}
# lreb: landing/page.json hero, as the writer wrote it
LREB_HERO = {
    "eyebrow": "For anyone building a recovery routine at home",
    "headline": "A sauna built for a consistent wellness and recovery routine",
    "subhead": 'In the ad, she says heat "feels great in her joints." The Peak Fuji brings that heat home, ready '
               "in days.",
}


# ---------------------------------------------------------------------------
# F1: the speaker's pronoun comes from evidence only (landing)
# ---------------------------------------------------------------------------

def test_f1_landing_gate_refuses_the_guessed_she_in_the_lreb_subhead():
    page = {"hero": dict(LREB_HERO)}
    problems = landing.find_speaker_pronoun_violations(page, LREB_BRIEF, TENANT)
    assert [p["key"] for p in problems] == ["landing:speaker_pronoun:$.hero.subhead"]
    assert "the creator" in problems[0]["issue"]
    keys = [p["key"] for p in landing.find_landing_violations(page, _facts_pack(), ad_brief=LREB_BRIEF,
                                                              tenant=TENANT)]
    assert "landing:speaker_pronoun:$.hero.subhead" in keys
    # the neutral frame passes; evidence of "she" passes the original
    neutral = {"hero": dict(LREB_HERO, subhead=LREB_HERO["subhead"].replace("she says", "the creator says"))}
    assert landing.find_speaker_pronoun_violations(neutral, LREB_BRIEF, TENANT) == []
    assert landing.find_speaker_pronoun_violations(page, dict(LREB_BRIEF, speaker_pronoun="she"), TENANT) == []
    assert landing.find_speaker_pronoun_violations(page, dict(LREB_BRIEF, speaker_pronoun="he"), TENANT) != []


def test_f1_landing_writer_is_given_the_evidenced_pronoun_or_the_creator():
    text = " ".join(landing.writer_lines(_rich(), TENANT, look="peak", ad_brief=LREB_BRIEF))
    assert '"In the ad, the creator says"' in text and "never she/he" in text
    assert "In the ad, she" not in text and "<she/he/the creator>" not in text
    text = " ".join(landing.writer_lines(_rich(), TENANT, look="peak", ad_brief=dict(LREB_BRIEF, speaker_pronoun="she")))
    assert '"In the ad, she says"' in text and "never she/he" not in text
