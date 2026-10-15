"""Cycle 110 (agent C): listicle parity with the pre-sell landing page.

Items are reasons (lever, proof_ref, 10/45 words), the first screen pays off
the ad's hook, the page never grades the ad, the copy reads at grade 7.5 or
lower, the CTA follows the ad's angle, the renderer adds a proof row + CTA
under the first screen, a value stack and a review wall from the facts_pack
contract (each with its fallback), and the quality numbers reach state.json
and the review site.

The failing text is real: ~/asset-inbox/teardown/TEARDOWN.md quotes, and the
live listicle runs of 2026-10-05 (tests/fixtures/c110c/cgyn = run
20261005-214707-...-cgyn, xlbx = 20261005-214408-...-xlbx; the 2sxl page of
tests/fixtures/listicle_c74). tests/fixtures/c110c/fuji-proof-fields-20261015
.json is agent A's grounding output for the Fuji, built offline from A's
2026-10-15 Judge.me / product-page fixtures (cycle110a/claims-data
harness/proof.py): store rating, six verbatim review quotes, units line,
included stack, financing.monthly, with their verified claims.
"""
import copy
import json

import pytest

from harness import listicle_persuasion as lp
from harness import render as render_mod
from harness import runstate, site_ui
from harness.repair import check_page_gates
from tests.support import REPO_ROOT, TENANT
from tests.test_listicle_cycle74 import _real

FX = REPO_ROOT / "tests" / "fixtures" / "c110c"
CGYN = {name: json.loads((FX / "cgyn" / f"{name}.json").read_text()) for name in ("page", "facts_pack", "ad_brief")}
XLBX_PAGE = json.loads((FX / "xlbx" / "page.json").read_text())
FIELDS = json.loads((FX / "fuji-proof-fields-20261015.json").read_text())
NAMES = ["Fuji", "Peak Fuji", "PEAK", "Peak"]
LEVERS = ("outcome", "likelihood", "effort", "time", "likelihood")


def with_a_fields(fp):
    """The run's own facts pack plus agent A's cycle 110 fields (contract
    BRIEF.md 3.2) and the claims behind them."""
    fp = copy.deepcopy(fp)
    for key in ("reviews_store", "review_quotes", "units", "included", "hsa_fsa", "support"):
        fp[key] = copy.deepcopy(FIELDS[key])
    fp["product"].setdefault("financing", {})["monthly"] = copy.deepcopy(FIELDS["financing_monthly"])
    have = {c["id"] for c in fp["verified_claims"]}
    fp["verified_claims"] += [c for c in FIELDS["claims"] if c["id"] not in have]
    return fp


def clean_cgyn(proof_refs=("review-quote-1", "units-delivered-12mo")):
    """Run cgyn's live page, with only what the cycle 110 gates flag on it
    changed: a lever per item, two items' proof lines pointing at verified
    proof elements, and the live page's own tics rewritten ("what's actually
    true" in the lede, "actually" in two FAQ questions, the returns FAQ and
    fit line)."""
    page = copy.deepcopy(CGYN["page"])
    # the rendered page.json carries the renderer's cut-out ids; the writer's
    # were facts_pack asset ids
    ids = [a["id"] for a in CGYN["facts_pack"]["assets"]]
    page["hero"]["asset_id"] = ids[0]
    for item, lever, asset_id in zip(page["reasons"], LEVERS, ids[1:]):
        item["lever"] = lever
        item["image"] = {"asset_id": asset_id}
    for item, ref in zip((page["reasons"][1], page["reasons"][4]), proof_refs):
        item["proof"] = {"proof_ref": ref}
    page["lede"] = page["lede"].replace("what's actually true", "what is true")
    for q in page["faq"]["questions"]:
        q["question"] = q["question"].replace(" actually", "")
    page["faq"]["questions"] = [q for q in page["faq"]["questions"] if "return" not in q["question"].lower()]
    page["audience_fit"]["not_for_you"] = [x for x in page["audience_fit"]["not_for_you"]
                                           if "return" not in x["text"].lower()]
    return page


def _gate(page, fp=None, brief=None, **kw):
    kw.setdefault("allowed_cta_texts", [CGYN["page"]["cta_text"]])
    return check_page_gates(page, fp or with_a_fields(CGYN["facts_pack"]), "listicle", financing_lender="Bread Pay",
                            speaker_pov="first_person", word_range=None, ad_brief=brief or CGYN["ad_brief"],
                            tenant=TENANT, listicle_style=page.get("style"), **kw)


def _keys(problems):
    return sorted(p.get("key") or p["issue"] for p in problems)


def test_the_cleaned_live_page_passes_every_gate():
    assert _gate(clean_cgyn()) == []


# ---------------------------------------------------------------------------
# Items as reasons
# ---------------------------------------------------------------------------

def test_a_proof_line_that_restates_its_body_fails_and_a_proof_ref_adds_proof():
    page = clean_cgyn()
    # docs/FIXLOG.md cycle 74, run 2sxl: the proof line repeats the body
    page["reasons"][0]["text"] = "Two Bluetooth speakers are built into the cabin, so your music comes with you."
    page["reasons"][0]["proof"] = {"text": "The cabin includes two built-in Bluetooth speakers.",
                                   "claim_ids": ["spec-fuji-audio"]}
    page["reasons"][0]["claim_ids"] = ["spec-fuji-audio"]
    assert "listicle:proof_restates_body:0" in _keys(_gate(page))
    # the same item proving itself with a verified review instead
    page["reasons"][0]["proof"] = {"proof_ref": "review-quote-3"}
    assert _gate(page) == []


def test_items_need_a_lever_each_and_all_four_levers():
    page = clean_cgyn()
    del page["reasons"][2]["lever"]
    for item in page["reasons"]:
        item["lever"] = "outcome" if "lever" in item else None
    keys = _keys(_gate(page))
    assert "listicle:item_lever:2" in keys and "listicle:item_levers" in keys


def test_heading_over_10_and_body_over_45_words_fail():
    page = clean_cgyn()
    # run i6zr, live: an 11-word item heading
    page["reasons"][0]["heading"] = "What happens if it ships damaged or you need to swap it?"
    page["reasons"][0]["text"] = " ".join([CGYN["page"]["reasons"][0]["text"]] * 2)
    keys = _keys(_gate(page))
    assert "listicle:item_heading_words:0" in keys and "listicle:item_body_words:0" in keys


def test_proof_refs_must_be_used_when_the_facts_offer_them_and_must_exist():
    page = clean_cgyn()
    for item in page["reasons"]:
        item["proof"] = copy.deepcopy(CGYN["page"]["reasons"][0]["proof"]) if "proof_ref" in item["proof"] else \
            item["proof"]
    page["reasons"][1]["proof"] = {"text": "Free shipping applies to every order of the cabin, door to door.",
                                   "claim_ids": ["shipping-policy"]}
    page["reasons"][4]["proof"] = {"proof_ref": "review-quote-99"}
    keys = _keys(_gate(page))
    assert "listicle:item_proof_refs" in keys and "listicle:item_proof_ref:4" in keys
    # without agent A's fields the only proof element is the product rating:
    # one item must point at it
    menu = lp.proof_refs_for_writer(CGYN["facts_pack"])
    assert [(m["id"], m["kind"]) for m in menu] == [("reviews-live", "rating")]


# ---------------------------------------------------------------------------
# Hook match
# ---------------------------------------------------------------------------

def test_an_item_heading_that_is_a_spec_label_fails_hook_match():
    page = clean_cgyn()
    # TEARDOWN 2.2: the H1 of the "Women over 40" arms (0 / 221 views)
    page["reasons"][3]["heading"] = "Full-spectrum heat and red light, built into one cabin"
    # a figure is a spec label too
    page["reasons"][0]["heading"] = "Runs on a 120V / 20A outlet"
    keys = _keys(_gate(page))
    assert "listicle:hook_match:item:3" in keys and "listicle:hook_match:item:0" in keys
    # the outcome first passes
    page["reasons"][3]["heading"] = "Heat that reaches your whole body"
    page["reasons"][0]["heading"] = CGYN["page"]["reasons"][0]["heading"]
    assert not [k for k in _keys(_gate(page)) if k.startswith("listicle:hook_match")]


def test_a_questions_page_may_ask_about_a_spec():
    page = clean_cgyn()
    page["style"] = "questions"
    # live run 2gn3, a questions page
    assert lp.find_item_spec_label_violations(
        {"style": "questions", "reasons": [{"heading": "Is red light therapy included or an add-on?"}]},
        CGYN["facts_pack"], NAMES) == []


def test_a_first_screen_that_misses_the_hook_fails():
    brief = {"hook": "Tired. Stressed. Aching. Women over 40, this is for you.", "angle": "", "promise": "",
             "speaker_pov": "brand", "transcript_or_text": ""}
    page = {"eyebrow": "Full-spectrum infrared", "headline": "Full-spectrum heat and red light, built into one cabin",
            "dek": "Near, mid and far infrared in one cedar cabin.", "reasons": []}
    problems = lp.find_hook_match_violations(page, brief, CGYN["facts_pack"], NAMES)
    assert _keys(problems) == ["listicle:hook_match"]
    page["headline"] = "Tired and stressed? An evening ritual for women over 40"
    assert lp.find_hook_match_violations(page, brief, CGYN["facts_pack"], NAMES) == []


def test_the_speakers_own_sentence_on_the_first_screen_matches_the_hook():
    result, _text = lp.hook_match(CGYN["page"], CGYN["ad_brief"], CGYN["facts_pack"], NAMES)
    assert result["ok"] and result["quoted"]


# ---------------------------------------------------------------------------
# Never grade the ad
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("field,text", [
    # live listicle 2sxl (myths, 2026-10-05)
    ("dek", "Before you spend a five-figure amount of time chasing a number, here's what actually holds up when "
            "you shop for a home sauna."),
    ("heading", "You have to call to get a real price"),
    ("faq", "No. The price shown on the Peak Fuji product page is the price, with nothing to submit first."),
    # TEARDOWN 2.3 / D10, live landing H1s and closings
    ("dek", "What that sauna ad really offers, explained plainly"),
    ("dek", "Here is what's actually true, in plain terms"),
])
def test_actually_plainly_and_ad_grading_phrases_fail(field, text):
    page = clean_cgyn()
    target = {"dek": (page, "dek"), "heading": (page["reasons"][0], "heading"),
              "faq": (page["faq"]["questions"][0], "answer")}[field]
    target[0][target[1]] = text
    assert any(k.startswith("listicle:ad_reference") for k in _keys(_gate(page)))


def test_the_ad_in_narration_fails_once_and_the_quote_frame_passes():
    page = clean_cgyn()
    # TEARDOWN 2.3: "See the real numbers behind the ad"
    page["dek"] = "See the numbers behind the ad."
    keys = _keys(_gate(page))
    assert "listicle:meta_reference:$.dek" in keys
    assert not [k for k in keys if k.startswith("listicle:ad_reference")]   # reported once, not twice
    # the speaker's own words are hers: "actually" inside her quote passes
    page["dek"] = CGYN["page"]["dek"]
    assert lp.find_ad_reference_violations(
        {"lede": 'In the ad, he says, "I actually use it every day."'}) == []


# ---------------------------------------------------------------------------
# Reading level
# ---------------------------------------------------------------------------

def test_grade_over_7_5_fails_on_the_live_page_and_the_hardest_line_is_named():
    grade, _fields = lp.page_grade(XLBX_PAGE, ["Mini", "Peak", "PEAK"])
    assert grade > 7.5
    problems = lp.find_reading_level_violations(XLBX_PAGE, ["Mini", "Peak", "PEAK"])
    assert _keys(problems) == ["listicle:reading_level"]
    assert problems[0]["path"].startswith("$.") and problems[0]["path"] != "$"
    # run cgyn reads at 7.4: passes
    assert lp.page_grade(CGYN["page"], NAMES)[0] <= 7.5
    assert lp.find_reading_level_violations(CGYN["page"], NAMES) == []


def test_reading_level_reuses_the_shared_flesch_kincaid_helper():
    from harness import persuasion

    assert lp.grade_level is persuasion.grade_level


# ---------------------------------------------------------------------------
# Review quotes: verbatim, "!" allowed
# ---------------------------------------------------------------------------

def test_a_review_quote_with_an_exclamation_mark_passes_and_renders_verbatim(tmp_path):
    fp = with_a_fields(CGYN["facts_pack"])
    quote = next(q for q in FIELDS["review_quotes"] if q["claim_id"] == "review-quote-1")
    assert "!" in quote["text"]
    page = clean_cgyn(proof_refs=("review-quote-1", "units-delivered-12mo"))
    assert _gate(page, fp) == []
    html = _render(tmp_path, page, fp)
    assert html.count(quote["text"].replace("'", "&#39;")) == 1 or html.count(quote["text"]) == 1
    # the writer's own exclamation mark still fails
    page["reasons"][0]["text"] += " It heats fast!"
    assert any("!" in p["issue"] or p.get("term") == "!" for p in _gate(page, fp))


def test_a_quote_naming_a_banned_term_is_never_shown():
    fp = with_a_fields(CGYN["facts_pack"])
    fp["review_quotes"][0] = dict(fp["review_quotes"][0], text="We picked it for the low EMF design and love it.")
    for c in fp["verified_claims"]:
        if c["id"] == fp["review_quotes"][0]["claim_id"]:
            c["text"] = fp["review_quotes"][0]["text"]
    assert fp["review_quotes"][0]["claim_id"] not in lp.proof_elements(fp)


# ---------------------------------------------------------------------------
# Rendering: proof row, CTA, value stack, review wall -- with and without A
# ---------------------------------------------------------------------------

def _render(tmp_path, page, fp, brief=None):
    out = tmp_path / "run" / "listicle"
    out.mkdir(parents=True, exist_ok=True)
    return render_mod.render_page(
        cartridge_name="listicle", page=copy.deepcopy(page), ad_brief=brief or CGYN["ad_brief"], facts_pack=fp,
        cartridges_dir=REPO_ROOT / "cartridges", brand_dir=TENANT.brand_dir,
        templates_dir=REPO_ROOT / "harness" / "templates", out_dir=out, published="2026-10-05",
        updated="2026-10-05", tenant=TENANT, download_assets=False).read_text()


def _text(html):
    from harness.claims import strip_html_to_visible_text

    return " ".join(strip_html_to_visible_text(html).split())


def test_with_agent_a_fields_the_page_shows_store_rating_units_stack_monthly_and_a_wall(tmp_path):
    fp = with_a_fields(CGYN["facts_pack"])
    page = dict(clean_cgyn(), hero_style="story")
    html = _render(tmp_path, page, fp)
    text = _text(html)
    first = html[:html.index('class="op-c op-items"')]
    assert "4.6 · 4,260 reviews" in _text(first) and "2,000+ delivered in 12 months" in _text(first)
    assert 'data-el="hero"' in first and "$458.33/mo*" in _text(first)
    for row in FIELDS["included"]:
        if row["claim_ids"] != ["pwc-value-588"]:
            assert row["text"] in text
    assert "Peak Wellness Club, a $588-per-year membership, is included free with every Peak sauna" in text
    assert "Or $458.33/mo for 18 months at 0% APR*" in text
    assert FIELDS["financing_monthly"]["disclosure"][:60] in text      # the disclosure always shows with it
    wall = html[html.index("What owners say"):]
    shown = [q for q in FIELDS["review_quotes"] if q["text"][:40].replace("'", "&#39;") in wall
             or q["text"][:40] in wall]
    assert len(shown) >= 3
    for el in ("hero", "mid", "stack", "reviews", "closing"):
        assert f'data-el="{el}"' in html
    # the units line's source note stays in Sources, never in the copy
    assert "(Shopify, Oct 2026)" not in text.split("Sources")[0]


def test_without_agent_a_fields_every_piece_falls_back(tmp_path):
    page = dict(clean_cgyn(proof_refs=("reviews-live", "reviews-live")), hero_style="story")
    page["reasons"][4]["proof"] = copy.deepcopy(CGYN["page"]["reasons"][4]["proof"])
    html = _render(tmp_path, page, CGYN["facts_pack"])
    text = _text(html)
    first = _text(html[:html.index('class="op-c op-items"')])
    assert "4.8 · 49 reviews" in first                                  # the product rating
    assert "delivered in 12 months" not in text                         # no units line
    assert "What owners say" not in text                                # no review wall
    assert "/mo" not in text                                            # no monthly figure
    assert "Financing is available through Bread Pay at checkout." in text
    assert "$8,250" in first
    assert "Free shipping" in text                                      # the fallback stack


def test_quality_numbers_are_recorded_in_state_json(tmp_path):
    fp = with_a_fields(CGYN["facts_pack"])
    run_dir = tmp_path / "run"
    page = dict(clean_cgyn(), hero_style="story")
    _render(tmp_path, page, fp)
    (run_dir / "facts_pack.json").write_text(json.dumps(fp))
    (run_dir / "ad_brief.json").write_text(json.dumps(CGYN["ad_brief"]))
    runstate.init_state(run_dir, pages=["listicle"])
    q = lp.record_quality(run_dir, "listicle", tenant=TENANT)
    assert set(q) == set(lp.QUALITY_KEYS)
    assert q["hook_match"] == 1.0 and q["grade_level"] <= 7.5 and q["ad_reference_rendered"] == []
    assert q["cta_count"] >= 5 and q["proof_count"] >= 5 and q["lcp_ms"] is None
    # no browser measured the layout: unknown, never a guess
    assert q["first_screen_ok"] is None
    assert runstate.load_state(run_dir)["listicle"]["quality"] == q


@pytest.mark.parametrize("top,ok", [(560, True), (843, True), (844, False), (1210, False), (None, False)])
def test_first_screen_ok_is_positional(tmp_path, top, ok):
    """capture.probe's first CTA top edge at 390 x 844 (run cgyn's face
    screen put it at about 1,210 px: the ad still fills the screen)."""
    fp = with_a_fields(CGYN["facts_pack"])
    page = dict(clean_cgyn(), hero_style="face")
    html = _render(tmp_path, page, fp)
    layout = {"cta": {"top": top, "sel": "a.op-btn"}} if top is not None else {"cta": None}
    q = lp.quality(page, html, CGYN["ad_brief"], fp, TENANT, layout=layout)
    assert q["first_screen_ok"] is ok and q["first_cta_top"] == top


def test_a_layout_measured_earlier_is_kept_when_the_next_record_has_none(tmp_path):
    fp = with_a_fields(CGYN["facts_pack"])
    run_dir = tmp_path / "run"
    _render(tmp_path, dict(clean_cgyn(), hero_style="face"), fp)
    (run_dir / "facts_pack.json").write_text(json.dumps(fp))
    (run_dir / "ad_brief.json").write_text(json.dumps(CGYN["ad_brief"]))
    runstate.init_state(run_dir, pages=["listicle"])
    assert lp.record_quality(run_dir, tenant=TENANT, layout={"cta": {"top": 1210}})["first_screen_ok"] is False
    assert lp.record_quality(run_dir, tenant=TENANT)["first_screen_ok"] is False


# ---------------------------------------------------------------------------
# The rendered page: fixed copy and old pages cannot slip past the ban
# ---------------------------------------------------------------------------

def test_the_rendered_scan_catches_a_banned_phrase_page_json_gates_never_saw(tmp_path):
    fp = with_a_fields(CGYN["facts_pack"])
    page = dict(clean_cgyn(), hero_style="display")
    # live run zhp2 (2026-10-05, before the gate): its scroll cue
    page["scroll_cue"] = "6 things the Fuji actually gives you"
    hits = lp.find_rendered_ad_reference_violations(_render(tmp_path, page, fp))
    assert [h["term"] for h in hits] == ["actually"]


def test_the_rendered_scan_allows_quote_frames_captions_and_customer_words(tmp_path):
    fp = with_a_fields(CGYN["facts_pack"])
    fp["review_quotes"][1] = dict(fp["review_quotes"][1])
    for style in ("face", "story", "display"):
        html = _render(tmp_path / style, dict(clean_cgyn(), hero_style=style), fp)
        assert lp.find_rendered_ad_reference_violations(html) == [], style
    assert lp.find_rendered_ad_reference_violations(
        '<main><blockquote>"It actually heats fast!"</blockquote><p>In the ad, he says, "I love it."</p></main>') == []


def test_the_face_caption_credits_the_speaker_without_naming_the_ad(tmp_path):
    from harness import first_screen

    # live run cgyn printed "In the ad he made for PEAK"
    assert first_screen.speaker_caption(CGYN["ad_brief"], CGYN["facts_pack"], TENANT) == "A PEAK creator"
    named = dict(CGYN["ad_brief"], speaker_name="Austin")
    assert first_screen.speaker_caption(named, CGYN["facts_pack"], TENANT) == "Austin, for PEAK"
    assert "customer" not in first_screen.speaker_caption(named, CGYN["facts_pack"], TENANT)   # not verified


# ---------------------------------------------------------------------------
# The CTA by the ad's angle
# ---------------------------------------------------------------------------

def _schema():
    return json.loads((REPO_ROOT / "cartridges" / "listicle" / "schema.json").read_text())


def test_cta_text_follows_the_ads_angle():
    fp = CGYN["facts_pack"]
    # TEARDOWN 2.2: the financing ad's hook
    financing = {"hook": "Major Updates: new flexible payment plans", "speaker_pov": "brand"}
    assert lp.allowed_cta_texts(_schema(), fp, financing, TENANT)[0] == "See monthly payments"
    story = dict(CGYN["ad_brief"], speaker_pronoun="he")
    assert lp.allowed_cta_texts(_schema(), fp, story, TENANT)[0] == "See the Fuji he uses"
    # no evidenced pronoun: the default phrases
    story_unknown = dict(CGYN["ad_brief"], speaker_pronoun=None)
    assert lp.allowed_cta_texts(_schema(), fp, story_unknown, TENANT)[0] == "Shop the Fuji"


def test_a_sale_ad_gets_the_offer_figure_in_the_cta():
    from tests.test_offer_cycle109 import _offer_facts

    fp, brief = _offer_facts()
    texts = lp.allowed_cta_texts(_schema(), fp, brief, TENANT)
    assert texts[:2] == [f"Get $300 off the {fp['product']['name']}", "Shop the sale"]


# ---------------------------------------------------------------------------
# The review site
# ---------------------------------------------------------------------------

def test_the_site_shows_quality_numbers_and_a_dash_for_each_missing_one():
    row = site_ui.quality_facts({"hook_match": 0.75, "grade_level": 6.1, "cta_count": 6, "proof_count": 1,
                                 "first_screen_ok": True})
    assert "Hook <b>0.75</b>" in row and "Grade <b>6.1</b>" in row and "CTAs <b>6</b>" in row
    assert "LCP <b>&mdash;</b>" in row and "CLS <b>&mdash;</b>" in row and "First screen <b>&#10003;</b>" in row
    empty = site_ui.quality_facts(None)
    assert empty.count("&mdash;") == len(site_ui.QUALITY_FIELDS)
    assert site_ui.quality_compact({}) == "&mdash;"


def test_results_show_clicks_by_element_and_lcp_when_the_beacon_has_them():
    from harness import site

    assert site._clicks_by_element({"engagement": {"by_el": {"hero": 4, "mid": 1}}}) == "hero 4, mid 1"
    assert site._clicks_by_element({}) == "&mdash;"
    assert site._lcp_p75({"engagement": {"lcp_p75": 2310.4, "lcp_n": 12}}).startswith("2310 ms")
    assert site._lcp_p75({"clicks": 3}) == "&mdash;"
