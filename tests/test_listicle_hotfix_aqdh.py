"""Listicle hotfix after cycle 110 (2026-10-16): three gates from the first
fresh listicle on master, run 20261016-005716-meta-120259103884780746-aqdh
(a recovery video ad by a physician assistant), which shipped as a
fact-check page. Fixtures are that run's own page.json, ad_brief.json and
facts_pack.json (tests/fixtures/c110c/aqdh/)."""
import copy
import json

from harness import listicle_persuasion as lp
from tests.support import REPO_ROOT

FX = REPO_ROOT / "tests" / "fixtures" / "c110c" / "aqdh"
PAGE, BRIEF, FACTS = (json.loads((FX / f"{n}.json").read_text()) for n in ("page", "ad_brief", "facts_pack"))


def _keys(problems):
    return sorted(p["key"] for p in problems)


def test_the_real_page_fails_exactly_the_three_new_gates():
    keys = _keys(lp.find_page_voice_violations(PAGE) + lp.find_health_word_violations(PAGE, BRIEF)
                 + lp.find_not_for_you_violations(PAGE, FACTS))
    assert keys == ["listicle:health_words:$.display_headline", "listicle:not_for_you:2",
                    "listicle:page_voice:$.dek", "listicle:page_voice:$.lede"]


# L1 -- the page's own voice never names the ad or calls a claim (un)verified

def test_l1_naming_the_ad_and_cant_verify_fail_in_page_voice():
    page = {"dek": PAGE["dek"]}   # "A sauna ad made health claims we can't verify, so here's what ..."
    assert _keys(lp.find_page_voice_violations(page)) == ["listicle:page_voice:$.dek"]
    for text in ("We could not verify that claim.", "A sauna commercial says otherwise.", "Unverified claims aside."):
        assert lp.find_page_voice_violations({"dek": text}), text


def test_l1_quotes_the_frame_and_the_verified_review_label_pass():
    ok = {"lede": 'In the ad, she says, "I verified it with my doctor."',
          "dek": "Five owners in verified reviews say it was easy to set up.",
          "scroll_cue": "Start with the first question"}
    assert lp.find_page_voice_violations(ok) == []
    html = ('<main><p>Kristi W. &middot; verified review</p><blockquote>"Best ad for a sauna!"</blockquote>'
            "<p>A sauna ad made health claims we can't verify.</p></main>")
    assert [h["term"] for h in lp.find_rendered_ad_reference_violations(html)] == ["ad", "can't verify"]


# L2 -- no health word at the top of the page or in an item heading

def test_l2_a_health_headline_fails_wherever_it_sits():
    assert _keys(lp.find_health_word_violations(PAGE, BRIEF)) == ["listicle:health_words:$.display_headline"]
    page = copy.deepcopy(PAGE)
    page["display_headline"] = "Recovery heat, without the guesswork"
    page["reasons"][0]["heading"] = "Does it relieve joint pain?"
    page["lede"] = "Some buyers want symptom relief. Ask the seller these questions."
    assert _keys(lp.find_health_word_violations(page, BRIEF)) == [
        "listicle:health_words:$.lede", "listicle:health_words:$.reasons[0].heading"]


def test_l2_the_speakers_own_sentence_word_for_word_is_the_exception():
    page = {"headline": '"I think a lot about what medical conditions or types of people would benefit from an '
                        'infrared sauna."', "reasons": []}
    assert lp.find_health_word_violations(page, BRIEF) == []
    page["headline"] = "Which medical conditions benefit from an infrared sauna"   # paraphrase
    assert lp.find_health_word_violations(page, BRIEF)


# L3 -- a not-for-you line is a checkable product limit with its claim id

def test_l3_a_jab_at_the_brand_or_the_page_fails():
    bad = PAGE["audience_fit"]["not_for_you"][2]
    assert bad["text"] == "You want a brand with a visible track record, not just a sales page."
    assert _keys(lp.find_not_for_you_violations(PAGE, FACTS)) == ["listicle:not_for_you:2"]
    # the same words citing a product fact still fail; a policy claim is no product fact
    for row in ({"text": bad["text"], "claim_ids": ["spec-fuji-capacity"]},
                {"text": "You want an easy way to send it back.", "claim_ids": ["returns-policy"]},
                {"text": "You want something that folds away."}):
        page = {"audience_fit": {"not_for_you": [row]}}
        assert _keys(lp.find_not_for_you_violations(page, FACTS)) == ["listicle:not_for_you:0"], row


def test_l3_product_limits_with_their_claims_pass():
    page = {"audience_fit": {"not_for_you": PAGE["audience_fit"]["not_for_you"][:2]}}
    assert lp.find_not_for_you_violations(page, FACTS) == []
