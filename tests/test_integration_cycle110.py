"""Cycle 110 integration fixes F1-F4, each from the real text of B's smoke runs
of 2026-10-15 (lreb = a PA's video ad, ddqi = the Community Savings Sale still).
"""
import datetime

from harness import landing, landing_sell, listicle_persuasion, offer, persuasion, quote_fidelity
from tests.support import TENANT
from tests.test_landing_cycle82 import _facts_pack
from tests.test_presell_cycle110 import _offer_facts, _page, _render, _rich

# the real offer.render_today (tests/conftest.py pins it for every test)
REAL_RENDER_TODAY = offer.render_today

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


# ---------------------------------------------------------------------------
# F2: words in quotation marks on the first screen are the source's own words
# ---------------------------------------------------------------------------

REVIEW_FP = {"review_quotes": [{"text": "We use it every night and it is the best part of my day!",
                                "claim_id": "review-quote-1"}]}


def test_f2_the_lreb_quote_is_verbatim_in_its_transcript_and_a_changed_word_fails():
    # the real subhead quotes the transcript word for word ("It feels great in her joints and ...")
    fields = [("$.hero.subhead", LREB_HERO["subhead"])]
    assert quote_fidelity.find_unquoted_first_screen(fields, LREB_BRIEF, REVIEW_FP, "landing") == []
    changed = LREB_HERO["subhead"].replace("her joints", "my joints")
    problems = quote_fidelity.find_unquoted_first_screen([("$.hero.subhead", changed)], LREB_BRIEF, REVIEW_FP,
                                                         "landing")
    assert [p["key"] for p in problems] == ["landing:quote_fidelity:$.hero.subhead"]
    assert "restore the exact words" in problems[0]["issue"] and "feels great in my joints" in problems[0]["issue"]
    page = {"hero": dict(LREB_HERO, subhead=changed)}
    keys = [p["key"] for p in landing.find_landing_violations(page, _facts_pack(), ad_brief=LREB_BRIEF,
                                                              tenant=TENANT)]
    assert "landing:quote_fidelity:$.hero.subhead" in keys
    keys = [p["key"] for p in listicle_persuasion.find_persuasion_violations({"lede": changed}, {},
                                                                             ad_brief=LREB_BRIEF)]
    assert "listicle:quote_fidelity:$.lede" in keys


def test_f2_a_review_quote_counts_case_and_punctuation_aside():
    fields = [("$.hero.headline", "“The best part of my day,” one owner says")]
    assert quote_fidelity.find_unquoted_first_screen(fields, LREB_BRIEF, REVIEW_FP, "landing") == []
    fields = [("$.hero.headline", '"The best part of the day," one owner says')]
    assert quote_fidelity.find_unquoted_first_screen(fields, LREB_BRIEF, REVIEW_FP, "landing") != []


# ---------------------------------------------------------------------------
# F3: a spoken ad's first screen restates the HOOK, generic words aside
# ---------------------------------------------------------------------------

# ddqi: ad_brief.json and the hero as rendered (tokens filled)
DDQI_BRIEF = {
    "hook": "$300 off every indoor sauna",
    "angle": "Limited-time discount offer with an urgent price-increase deadline to drive immediate purchase.",
    "promise": "Save $300–$500 on saunas before prices increase on October 16",
    "transcript_or_text": "BEFORE PRICES GO UP OCT 16\n\n$300 off\n\nEVERY INDOOR SAUNA.\n\n$500 OFF OUTDOOR\n"
                          "TODAY'S PRICE ENDS THU, OCT 15\n\nPEAK",
    "speaker_pov": "brand",
    "input_type": "still",
}
DDQI_HERO = {
    "eyebrow": "For anyone ready to buy an indoor sauna",
    "headline": "$300 off the Fuji, today's price ends Thursday",
    "subhead": "Save on a two-person infrared sauna built for your evening reset, with heat ready in minutes from "
               "your phone.",
}


def test_f3_the_lreb_first_screen_does_not_restate_the_pa_hook():
    problems = persuasion.find_hook_match_violations({"hero": LREB_HERO}, LREB_BRIEF, exclude={"peak", "fuji"})
    assert [p["key"] for p in problems] == ["landing:hook_match"]
    # before F3 the promise's "wellness"/"recovery"/"routine" carried it
    text = " ".join(LREB_HERO.values())
    assert persuasion.hook_match(text, dict(LREB_BRIEF, input_type="still", speaker_pov="brand"))["ok"]
    # restating the hook passes; so does quoting a whole transcript sentence
    hero = dict(LREB_HERO, headline="Medical conditions that may benefit from an infrared sauna")
    assert persuasion.find_hook_match_violations({"hero": hero}, LREB_BRIEF, exclude={"peak", "fuji"}) == []
    sentence = "One area I found particularly interesting was rheumatology."
    assert persuasion.hook_match(text + ' "' + sentence + '"', LREB_BRIEF, quotes=[sentence])["ok"]


def test_f3_the_ddqi_sale_still_passes_and_generic_words_count_only_for_a_static_ad():
    assert persuasion.find_hook_match_violations({"hero": DDQI_HERO}, DDQI_BRIEF, exclude={"peak", "fuji"}) == []
    hook = {"hook": "Peak home sauna heat, infrared", "transcript_or_text": "Peak home sauna heat, infrared."}
    match = persuasion.hook_match("A home infrared sauna with heat", dict(hook, input_type="video"))
    assert match["overlap"] == [] and not match["ok"]
    assert persuasion.hook_match("A home infrared sauna with heat", dict(hook, input_type="still",
                                                                          speaker_pov="brand"))["ok"]


# ---------------------------------------------------------------------------
# F4: the sale's last day against the render date
# ---------------------------------------------------------------------------

def _render_on(monkeypatch, day):
    monkeypatch.setattr(offer, "render_today", lambda tenant=None: datetime.date(2026, 10, day))


def _first_screen_text(html):
    import html as html_mod
    import re

    first = html[html.index('data-look-pattern="hero:peak_first"'):html.index("</header>")]
    return " ".join(html_mod.unescape(re.sub(r"<[^>]+>", " ", first)).split())


def test_f4_the_ddqi_headline_rendered_on_thursday_oct_15_says_tonight(monkeypatch, tmp_path):
    fp, brief = _offer_facts()          # the ddqi claim: "... prices go up on October 16, 2026."
    assert fp["offer"]["text"].endswith("prices go up on October 16, 2026.")
    page = _page(cta_text="Shop the sale")
    page["hero"]["headline"] = "{offer_amount} off the Fuji, today's price ends {deadline_day}"   # ddqi H1
    _render_on(monkeypatch, 14)
    assert landing_sell.deadline(fp)["day"] == "Thursday"
    _render_on(monkeypatch, 15)
    dl = landing_sell.deadline(fp)
    assert (dl["day"], dl["short"]) == ("tonight", "Today's price ends tonight")
    assert landing_sell.token_values(fp)["deadline_day"]["text"] == "tonight"
    text = _first_screen_text(_render(tmp_path, page, fp, brief)[0])
    assert "today's price ends tonight" in text and "ends Thursday" not in text


def test_f4_a_sale_past_its_deadline_is_not_rendered(monkeypatch, tmp_path):
    fp, brief = _offer_facts()
    _render_on(monkeypatch, 16)
    assert offer.offer_view(fp) is None and landing_sell.deadline(fp) is None
    html, _ = _render(tmp_path, _page(cta_text="Shop the Fuji"), fp, brief)
    assert "INDOOR300" not in html and "$300 off" not in html and "Community Savings Sale" not in html


def test_f4_grounding_uses_its_own_date_not_the_wall_clock(monkeypatch):
    # grounding on Oct 14 (the sale is live) must not fail when the render clock is already past the deadline
    _render_on(monkeypatch, 20)
    fp, _brief = _offer_facts()
    assert fp["offer"]["net_price"] == "$7,950" and offer.offer_view(fp) is None


# ---------------------------------------------------------------------------
# D1: "really" is banned only in the ad-reference framing
# ---------------------------------------------------------------------------

def test_d1_a_buyers_really_mean_question_passes_and_the_ad_reference_framing_fails():
    # cgyn FAQ (a real buyer question) passes
    assert persuasion.AD_REFERENCE_RE.search("Does free shipping really mean no extra fees?") is None
    for text in ("What the research really says about infrared sauna",       # B's teardown headline
                 "Here is what that claim really means for you",
                 "What a sauna really is, in plain terms",
                 "Really means nothing to a buyer.",
                 "Skip the noise. Really says it all."):
        assert persuasion.AD_REFERENCE_RE.search(text), text


# ---------------------------------------------------------------------------
# D4: the face first screen puts the whole CTA above the fold
# ---------------------------------------------------------------------------

def test_d4_a_cta_whose_bottom_edge_is_below_844_is_not_on_the_first_screen():
    from harness import design_gate

    # cgyn face, merged before D4: top 806, bottom 861 (38 px of the button showed)
    cgyn = {"cta_expected": True, "cta": {"sel": "a.op-btn", "top": 806, "bottom": 861}}
    assert design_gate.first_screen_cta(cgyn)["status"] == "fail"
    assert design_gate.first_screen_cta(dict(cgyn, cta={"sel": "a.op-btn", "top": 640, "bottom": 695}))["status"] \
        == "pass"
    assert listicle_persuasion.first_cta_top(cgyn) == 806


def test_d4_listicle_first_screen_ok_uses_the_bottom_edge(tmp_path):
    from tests.test_listicle_cycle110 import CGYN, clean_cgyn, with_a_fields
    from tests.test_listicle_cycle110 import _render as render_listicle

    fp = with_a_fields(CGYN["facts_pack"])
    page = dict(clean_cgyn(), hero_style="face")
    html = render_listicle(tmp_path, page, fp)
    for top, bottom, ok in ((806, 861, False), (788, 843, True)):
        layout = {"cta": {"sel": "a.op-btn", "top": top, "bottom": bottom}}
        assert listicle_persuasion.quality(page, html, CGYN["ad_brief"], fp, TENANT, layout=layout)["first_screen_ok"] is ok


def test_d4_the_face_composition_orders_proof_row_cta_and_price_before_the_lede():
    from pathlib import Path

    face = (Path(__file__).resolve().parent.parent / "harness" / "heroes" / "face.html").read_text()
    order = [face.index(k) for k in ("<figcaption>", 'class="op-proofrow"', 'data-el="hero"', 'class="op-fine op-pl"',
                                     'class="op-lede"', 'class="op-cue"')]
    assert order == sorted(order)


# ---------------------------------------------------------------------------
# D5: the render date is the tenant's local day
# ---------------------------------------------------------------------------

def test_d5_the_render_date_is_peaks_mountain_time_day():
    from zoneinfo import ZoneInfo

    assert TENANT.get("timezone") == "America/Denver"
    assert REAL_RENDER_TODAY(TENANT) == datetime.datetime.now(ZoneInfo("America/Denver")).date()

    class NoZone:
        def get(self, key, default=None):
            return default

    assert REAL_RENDER_TODAY(NoZone()) == datetime.datetime.now(datetime.timezone.utc).date()
