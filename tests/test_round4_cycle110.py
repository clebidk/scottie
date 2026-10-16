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
