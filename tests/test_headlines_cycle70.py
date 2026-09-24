"""Cycle 70: the listicle headline template library (owner request
2026-09-24) -- cartridges/listicle/headlines.yaml and harness/headlines.py.

17 "pre-sell listicle" headline templates plus the five cycle 41 style
formulas, each with its styles, slots and the evidence the page must be able
to cite. Covers: the library loads and validates; every template renders from
its slots; evidence gating (a count, growth, exclusivity or an endorsement
only when verified.json holds one, never invented); the fear-word and
medical-word gates; the category-not-competitor rule; the current-year slot;
the "Game-Changer"/"Must-Have" headline exception; selection (seeded, tenant
include/exclude, a weight hook); the gate and its repair message; and the
template id recorded in page.json, state.json and the A/B/C test record.
"""
import copy
import datetime
import json

import pytest

from harness import abtest, headlines, listicle, runstate
from harness.repair import apply_deterministic_fixes
from tests.support import TENANT
from tests.test_listicle import FACTS_PACK, _gate, _items, _listicle_page

TODAY = datetime.date(2026, 9, 24)
NEW_IDS = [f"h{i:02d}" for i in range(1, 18)]

SPEC_CLAIM = {"id": "spec-fuji-heating-panels", "text": "Fuji -- Heating panels: Eight.",
              "category": "spec", "source": "https://peaksaunas.com/products/fuji"}
COUNT_CLAIM = {"id": "customers-count", "text": "PEAK has delivered saunas to 12,480 customers.",
               "category": "trust", "evidence": "customer_count", "count": 12480, "unit": "customers",
               "source": "https://peaksaunas.com/pages/about"}
COUNT_TEXT_CLAIM = {"id": "owners-count", "text": "More than 10,450 owners use a PEAK sauna at home.",
                    "category": "trust", "source": "https://peaksaunas.com/pages/about"}
GROWTH_CLAIM = {"id": "sales-growth", "text": "PEAK sauna orders grew 212% year over year in 2026.",
                "category": "trust", "source": "https://peaksaunas.com/pages/about"}
EXCLUSIVE_CLAIM = {"id": "only-small-apartments",
                   "text": "PEAK makes the only home sauna built for small apartments.",
                   "category": "trust", "evidence": "exclusivity", "source": "https://peaksaunas.com/pages/about"}
ENDORSE_CLAIM = {"id": "dr-endorsement", "text": "Dr. Jane Smith recommends PEAK home saunas to her athletes.",
                 "category": "trust", "evidence": "endorsement", "authority": "Dr. Jane Smith",
                 "source": "https://example.org/dr-smith"}
SAFETY_CLAIM = {"id": "etl-listed", "text": "Every PEAK sauna is ETL listed.", "category": "trust",
                "source": "https://peaksaunas.com/pages/safety"}

EVIDENCE_CLAIMS = [SPEC_CLAIM, COUNT_CLAIM, GROWTH_CLAIM, EXCLUSIVE_CLAIM, ENDORSE_CLAIM]


def _facts(*extra):
    fp = copy.deepcopy(FACTS_PACK)
    fp["verified_claims"] = fp["verified_claims"] + [copy.deepcopy(c) for c in extra]
    return fp


def _plan(template_id, style=None, facts_pack=None, tenant=TENANT, today=TODAY):
    tmpl = headlines.template(template_id)
    style = style or tmpl["styles"][0]
    return headlines.build_plan(template_id, style, facts_pack or _facts(*EVIDENCE_CLAIMS),
                                tenant=tenant, today=today)


def _cite_all(page, plan):
    """Every item cites one claim of every kind the template requires."""
    ids = [ids[0] for ids in plan["evidence"].values() if ids]
    for item in page["reasons"]:
        item["claim_ids"] = list(ids)
    return page


def _page_for(plan, headline=None, n_items=5):
    page = _listicle_page(plan["style"] if plan["style"] in listicle.STYLES else "reasons", n_items=n_items)
    page["headline"] = headline if headline is not None else headlines.example_headline(plan, n_items)
    return _cite_all(page, plan)


class _PinnedTenant:
    """The real tenant with one extra setting."""

    def __init__(self, setting):
        self._setting = setting
        self.display_name = TENANT.display_name
        self.name = TENANT.name

    def get(self, key, default=None):
        if key == "cartridges.listicle.headline_templates":
            return self._setting
        return TENANT.get(key, default)

    def catalog_products(self):
        return TENANT.catalog_products()


# ---------------------------------------------------------------------------
# 1. the library loads and validates
# ---------------------------------------------------------------------------

def test_library_has_all_17_templates_plus_the_five_style_formulas():
    ids = set(headlines.template_ids())
    assert set(NEW_IDS) <= ids
    assert {f"s-{s}" for s in listicle.STYLES} <= ids
    assert len(ids) == 22


def test_every_template_names_real_styles_slots_and_evidence_kinds():
    lib = headlines.load_library()
    for tid in headlines.template_ids():
        tmpl = headlines.template(tid)
        assert set(tmpl["styles"]) <= set(listicle.STYLES), tid
        placeholders = set(headlines.placeholders(tmpl["pattern"]))
        assert placeholders == set(tmpl["slots"]), tid
        for kind in tmpl.get("requires") or []:
            assert kind in lib["evidence"], (tid, kind)
        for slot in tmpl["slots"].values():
            assert slot["kind"] in headlines.SLOT_KINDS, tid


def test_the_style_formula_entries_are_the_live_formulas():
    for style in listicle.STYLES:
        tmpl = headlines.template(f"s-{style}")
        assert tmpl["pattern"] == listicle.HEADLINE_FORMULAS[style]
        assert tmpl["styles"] == [style]


def test_item_semantics_decide_which_styles_a_template_fits():
    for tid in NEW_IDS:
        styles = headlines.template(tid)["styles"]
        if tid in ("h11", "h17"):
            assert styles == ["mistakes"], tid
        else:
            assert styles == ["reasons"], tid
    # "Ways" templates: an item is a way the product helps
    for tid in ("h07", "h13"):
        assert "way" in headlines.template(tid)["item_pattern"].lower()


@pytest.mark.parametrize("mutate,message", [
    (lambda d: d["templates"][0].__setitem__("pattern", "N Reasons <undeclared> Win"), "undeclared"),
    (lambda d: d["templates"][0].__setitem__("styles", ["not-a-style"]), "not-a-style"),
    (lambda d: d["templates"][0].__setitem__("requires", ["telepathy"]), "telepathy"),
    (lambda d: d["templates"].append(copy.deepcopy(d["templates"][0])), "duplicate"),
    (lambda d: d["templates"][0].__setitem__("pattern", "Reasons <audience> Are Choosing <category>"), "N"),
    (lambda d: d["templates"][0]["slots"]["audience"].__setitem__("singular", None), "unknown key"),
])
def test_a_broken_library_is_refused(mutate, message):
    data = copy.deepcopy(headlines.load_library(raw=True))
    mutate(data)
    with pytest.raises(headlines.HeadlineLibraryError) as err:
        headlines.validate_library(data)
    assert message in str(err.value)


# ---------------------------------------------------------------------------
# 2. every template renders from its slots
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("tid", NEW_IDS)
def test_each_new_template_renders_from_its_slots_and_parses_back(tid):
    plan = _plan(tid)
    text = headlines.example_headline(plan, 6)
    assert "<" not in text and ">" not in text
    assert text.startswith("6 ")
    parsed = headlines.parse_headline(text, plan)
    assert parsed is not None, text
    assert parsed["n"] == "6"
    for name, value in headlines.template(tid)["example"].items():
        assert parsed[name].lower() == value.lower()


@pytest.mark.parametrize("tid", NEW_IDS)
def test_each_example_headline_passes_its_own_gate(tid):
    plan = _plan(tid)
    page = _page_for(plan)
    assert headlines.find_headline_violations(page, plan) == []


def test_render_fills_n_and_named_slots():
    tmpl = headlines.template("h04")
    assert headlines.render(tmpl, 7, {"product": "A Home Sauna", "problem": "Long Winters"}) == (
        "7 Reasons A Home Sauna Is a Must-Have for Long Winters"
    )


# ---------------------------------------------------------------------------
# 3. evidence gating
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("tid,claim", [
    ("h09", COUNT_CLAIM), ("h02", GROWTH_CLAIM), ("h05", GROWTH_CLAIM),
    ("h12", EXCLUSIVE_CLAIM), ("h15", ENDORSE_CLAIM),
])
def test_evidence_templates_are_ineligible_without_their_claim_and_eligible_with_it(tid, claim):
    style = headlines.template(tid)["styles"][0]
    ok, reason = headlines.eligibility(tid, style, _facts(SPEC_CLAIM), tenant=TENANT)
    assert not ok and reason
    assert tid not in headlines.eligible_templates(style, _facts(SPEC_CLAIM), tenant=TENANT)
    with pytest.raises(headlines.HeadlineTemplateError):
        headlines.build_plan(tid, style, _facts(SPEC_CLAIM), tenant=TENANT, today=TODAY)
    ok, _ = headlines.eligibility(tid, style, _facts(SPEC_CLAIM, claim), tenant=TENANT)
    assert ok
    assert tid in headlines.eligible_templates(style, _facts(SPEC_CLAIM, claim), tenant=TENANT)


def test_count_template_uses_the_verified_count_rounded_down_never_a_made_up_one():
    plan = _plan("h09", facts_pack=_facts(COUNT_CLAIM))
    assert plan["fixed"]["count"] == "12,000+"
    assert plan["fixed"]["count_unit"] == "Customers"
    assert plan["evidence"]["customer_count"] == ["customers-count"]
    text = headlines.example_headline(plan, 5)
    assert "12,000+ Customers" in text
    assert "100,000" not in text
    assert "100,000" not in " ".join(listicle.writer_style_lines("reasons", headline=plan))


def test_count_is_read_from_the_claim_text_when_it_has_no_count_field():
    plan = _plan("h09", facts_pack=_facts(COUNT_TEXT_CLAIM))
    assert plan["fixed"]["count"] == "10,000+"
    assert plan["fixed"]["count_unit"] == "Owners"


@pytest.mark.parametrize("count,shown", [(12480, "12,000+"), (100000, "100,000+"), (157300, "150,000+"),
                                         (999, "990+"), (4321, "4,300+")])
def test_counts_round_down_honestly(count, shown):
    assert headlines.round_down_count(count) == shown


def test_a_review_count_is_not_a_customer_count():
    fp = _facts(SPEC_CLAIM)
    fp["verified_claims"].append({"id": "reviews-live", "text": "Rated 4.79 out of 5 across 47 reviews on Judge.me.",
                                  "category": "trust"})
    ok, _ = headlines.eligibility("h09", "reasons", fp, tenant=TENANT)
    assert not ok


def test_a_changed_count_in_the_headline_is_rejected():
    plan = _plan("h09", facts_pack=_facts(COUNT_CLAIM))
    page = _page_for(plan, headline="5 Reasons Why 100,000+ Customers Switched to This Home Sauna")
    problems = headlines.find_headline_violations(page, plan)
    assert problems and any("12,000+" in p["issue"] for p in problems)


def test_evidence_templates_require_the_items_to_cite_the_claim():
    for tid, kind in (("h02", "growth"), ("h15", "endorsement"), ("h14", "feature"), ("h09", "customer_count")):
        plan = _plan(tid)
        page = _page_for(plan)
        for item in page["reasons"]:
            item.pop("claim_ids", None)
        problems = headlines.find_headline_violations(page, plan)
        assert any(p["key"] == f"listicle:headline_evidence:{kind}" for p in problems), tid


def test_the_authority_comes_from_the_endorsement_claim_never_invented():
    plan = _plan("h15")
    assert plan["fixed"]["authority"] == "Dr. Jane Smith"
    page = _page_for(plan, headline="5 Reasons Why Dr. John Doe Loves Home Infrared Saunas for Busy Training Weeks")
    assert headlines.find_headline_violations(page, plan)


def test_exclusivity_niche_must_be_the_one_the_claim_names():
    plan = _plan("h12")
    ok = _page_for(plan, headline="5 Reasons This Is the Only Home Sauna Built for Small Apartments")
    assert headlines.find_headline_violations(ok, plan) == []
    bad = _page_for(plan, headline="5 Reasons This Is the Only Home Sauna Built for Busy Parents")
    assert any(p["key"] == "listicle:headline_slots" for p in headlines.find_headline_violations(bad, plan))


def test_peak_today_has_no_count_growth_exclusivity_or_endorsement_evidence():
    claims = json.loads((TENANT.claims_dir / "verified.json").read_text())
    fp = {"verified_claims": claims}
    for tid in ("h02", "h05", "h09", "h12", "h15"):
        style = headlines.template(tid)["styles"][0]
        assert not headlines.eligibility(tid, style, fp, tenant=TENANT)[0], tid
    for tid in ("h01", "h03", "h04", "h06", "h07", "h08", "h10", "h13", "h14", "h16"):
        assert headlines.eligibility(tid, "reasons", fp, tenant=TENANT)[0], tid
    for tid in ("h11", "h17"):
        assert headlines.eligibility(tid, "mistakes", fp, tenant=TENANT)[0], tid


# ---------------------------------------------------------------------------
# 4. template 11: fear words, "Safe" only with a cited claim
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("word", ["Toxins", "EMF", "Radiation", "Chemicals", "Off-Gassing"])
def test_template_11_rejects_fear_words_in_the_headline(word):
    plan = _plan("h11")
    page = _page_for(plan, headline=f"5 Concerning {word} in Gym Saunas, and a Better Alternative")
    problems = headlines.find_headline_violations(page, plan)
    assert any(p["key"] == "listicle:headline_fear" for p in problems), word


@pytest.mark.parametrize("field", ["heading", "text"])
def test_template_11_rejects_fear_words_in_the_items(field):
    plan = _plan("h11")
    page = _page_for(plan)
    page["reasons"][2][field] = page["reasons"][2][field] + " The cabin gives off radiation and toxins."
    problems = headlines.find_headline_violations(page, plan)
    assert any(p["key"] == "listicle:item_fear:2" for p in problems)


def test_template_11_says_better_unless_a_safety_claim_is_verified():
    plan = _plan("h11")
    assert plan["fixed"]["alternative"] == "Better"
    assert "Better Alternative" in headlines.example_headline(plan, 5)
    page = _page_for(plan, headline="5 Concerning Hidden Costs in Gym Sauna Memberships, and a Safe Alternative")
    assert headlines.find_headline_violations(page, plan)

    safe = _plan("h11", facts_pack=_facts(SAFETY_CLAIM))
    assert safe["fixed"]["alternative"] == "Safe"
    assert safe["evidence"]["safety"] == ["etl-listed"]
    page = _page_for(safe)
    assert "Safe Alternative" in page["headline"]
    assert headlines.find_headline_violations(page, safe) == []


def test_fear_words_are_rejected_in_any_templates_headline():
    plan = _plan("h04")
    page = _page_for(plan, headline="5 Reasons A Home Infrared Sauna Is a Must-Have for Toxic Workdays")
    assert any(p["key"] == "listicle:headline_fear" for p in headlines.find_headline_violations(page, plan))


# ---------------------------------------------------------------------------
# 5. templates 7, 13, 17: everyday, non-medical problems
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("tid,headline", [
    ("h07", "5 Ways A Home Sauna Helps Solve Chronic Back Pain, Without Leaving the House"),
    ("h07", "5 Ways A Home Sauna Helps Solve Arthritis Symptoms, Without Leaving the House"),
    ("h13", "5 Ways A Home Sauna Removes Embarrassing Acne, Without a Gym Membership"),
    ("h17", "5 Reasons Why You're Still Fighting Insomnia, Even After Trying Gym Passes"),
    ("h17", "5 Reasons Why You're Still Waiting to Heal, Even After Trying Gym Passes"),
    ("h07", "5 Ways A Home Sauna Helps Solve Clinical Fatigue, Without Leaving the House"),
])
def test_medical_problems_are_rejected(tid, headline):
    plan = _plan(tid)
    page = _page_for(plan, headline=headline)
    problems = headlines.find_headline_violations(page, plan)
    assert any(p["key"] == "listicle:headline_medical" for p in problems), headline


def test_medical_words_in_the_items_of_a_problem_template_are_rejected():
    plan = _plan("h13")
    page = _page_for(plan)
    page["reasons"][0]["text"] += " It can cure a disease."
    assert any(p["key"] == "listicle:item_medical:0" for p in headlines.find_headline_violations(page, plan))


def test_ordinary_english_in_items_is_not_a_health_claim():
    plan = _plan("h08")
    page = _page_for(plan)
    page["reasons"][0]["text"] += (" Watch how it treats a customer, read the terms and conditions, and skip"
                                   " anything that is a pain to install.")
    assert headlines.find_headline_violations(page, plan) == []


def test_target_age_is_a_plain_age():
    plan = _plan("h08")
    ok = _page_for(plan, headline="5 Reasons People Over 50 Are Obsessed With PEAK")
    assert headlines.find_headline_violations(ok, plan) == []
    for bad in ("5 Reasons People Over Fifty-Something Are Obsessed With PEAK",
                "5 Reasons People Over 5000 Are Obsessed With PEAK"):
        page = _page_for(plan, headline=bad)
        assert any(p["key"] == "listicle:headline_slots" for p in headlines.find_headline_violations(page, plan))


# ---------------------------------------------------------------------------
# 6. template 14: a category, never a competitor brand
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("rival", ["Sunlighten Saunas", "Sun Home Saunas", "Sun Saunas"])
def test_template_14_rejects_a_competitor_brand(rival):
    plan = _plan("h14")
    page = _page_for(plan, headline=f"5 Reasons Why This Breakthrough Home Sauna Crushes {rival}")
    problems = headlines.find_headline_violations(page, plan)
    assert any(p["key"] == "listicle:headline_slots" and "competitor" in p["issue"] for p in problems), rival


def test_template_14_accepts_a_category():
    plan = _plan("h14")
    for category in ("Gym Saunas", "Traditional Saunas", "Sauna Blankets"):
        page = _page_for(plan, headline=f"5 Reasons Why This Breakthrough Home Sauna Crushes {category}")
        assert headlines.find_headline_violations(page, plan) == [], category


def test_template_14_needs_a_feature_claim():
    ok, _ = headlines.eligibility("h14", "reasons", {"verified_claims": []}, tenant=TENANT)
    assert not ok


# ---------------------------------------------------------------------------
# 7. the current year, from the run date
# ---------------------------------------------------------------------------

def test_the_year_slot_is_the_run_year_never_hard_coded():
    assert "2026" not in headlines.template("h02")["pattern"]
    plan = _plan("h02", today=datetime.date(2027, 1, 5))
    assert plan["fixed"]["year"] == "2027"
    assert headlines.example_headline(plan, 5).endswith("in 2027")
    page = _page_for(plan, headline="5 Reasons Why Everyone's Switching to PEAK for Home Heat Sessions in 2026")
    problems = headlines.find_headline_violations(page, plan)
    assert any("2027" in p["issue"] for p in problems)


def test_a_number_other_than_n_year_or_count_is_rejected():
    plan = _plan("h04")
    page = _page_for(plan, headline="5 Reasons A Home Infrared Sauna Is a Must-Have for 3 Cold Months")
    problems = headlines.find_headline_violations(page, plan)
    assert any("number" in p["issue"] for p in problems)


# ---------------------------------------------------------------------------
# 8. "Game-Changer" / "Must-Have" in these templates' headlines only
# ---------------------------------------------------------------------------

def test_game_changer_passes_the_full_gate_in_template_16s_headline():
    plan = _plan("h16", facts_pack=FACTS_PACK)
    page = _page_for(plan)
    assert "Game-Changer" in page["headline"]
    assert _gate(page, listicle_style="reasons", listicle_headline=plan) == []


def test_game_changer_is_still_banned_in_body_copy_under_template_16():
    plan = _plan("h16", facts_pack=FACTS_PACK)
    page = _page_for(plan)
    page["reasons"][1]["text"] = page["reasons"][1]["text"] + " It is a game-changer."
    problems = _gate(page, listicle_style="reasons", listicle_headline=plan)
    assert any(p.get("term") == "game-changer" and p["path"] != "$.headline" for p in problems)


def test_game_changer_is_still_banned_in_any_other_templates_headline():
    page = _listicle_page("reasons")
    page["headline"] = "5 Reasons Busy Parents Are Choosing a Game-Changer Home Sauna"
    assert any(p.get("term") == "game-changer" for p in _gate(page, listicle_style="reasons"))


def test_must_have_passes_the_full_gate_in_template_4s_headline():
    plan = _plan("h04", facts_pack=FACTS_PACK)
    page = _page_for(plan)
    assert "Must-Have" in page["headline"]
    assert _gate(page, listicle_style="reasons", listicle_headline=plan) == []


def test_brand_slot_may_name_the_tenant_in_the_full_gate():
    plan = _plan("h08", facts_pack=FACTS_PACK)
    page = _page_for(plan)
    assert page["headline"].endswith("With PEAK")
    assert _gate(page, listicle_style="reasons", listicle_headline=plan) == []


def test_a_model_name_is_its_full_name_in_a_product_slot():
    plan = _plan("h16", facts_pack=FACTS_PACK)
    ok = _page_for(plan, headline="5 Reasons Why the Peak Fuji Is a Game-Changer for Busy Parents")
    assert headlines.find_headline_violations(ok, plan) == []
    bad = _page_for(plan, headline="5 Reasons Why Fuji Is a Game-Changer for Busy Parents")
    assert any(p["key"] == "listicle:headline_slots" for p in headlines.find_headline_violations(bad, plan))
    brand_in_audience = _page_for(plan, headline="5 Reasons Why A Home Sauna Is a Game-Changer for PEAK Owners")
    assert any(p["key"] == "listicle:headline_slots"
               for p in headlines.find_headline_violations(brand_in_audience, plan))


# ---------------------------------------------------------------------------
# 9. selection: seeded, tenant include/exclude, weight hook
# ---------------------------------------------------------------------------

def test_selection_is_deterministic_from_the_seed_and_covers_the_eligible_set():
    fp = _facts(SPEC_CLAIM)
    eligible = headlines.eligible_templates("reasons", fp, tenant=TENANT)
    assert "s-reasons" in eligible and "h04" in eligible and "h09" not in eligible
    picks = {headlines.resolve_plan("reasons", fp, tenant=TENANT, seed=s, today=TODAY)["id"] for s in range(300)}
    assert picks == set(eligible)
    assert (headlines.resolve_plan("reasons", fp, tenant=TENANT, seed=11, today=TODAY)["id"]
            == headlines.resolve_plan("reasons", fp, tenant=TENANT, seed=11, today=TODAY)["id"])


def test_questions_myths_and_tested_keep_their_own_formula():
    fp = _facts(*EVIDENCE_CLAIMS)
    for style in ("questions", "myths", "tested"):
        assert headlines.eligible_templates(style, fp, tenant=TENANT) == [f"s-{style}"]


def test_mistakes_style_gets_templates_11_and_17():
    assert set(headlines.eligible_templates("mistakes", _facts(), tenant=TENANT)) == {"s-mistakes", "h11", "h17"}


def test_weights_hook_steers_the_pick():
    fp = _facts(SPEC_CLAIM)
    weights = {tid: 0.0 for tid in headlines.eligible_templates("reasons", fp, tenant=TENANT)}
    weights["h16"] = 1.0
    for seed in range(20):
        assert headlines.resolve_plan("reasons", fp, tenant=TENANT, seed=seed, today=TODAY,
                                      weights=weights)["id"] == "h16"


def test_tenant_include_pins_templates():
    tenant = _PinnedTenant({"include": ["h04", "h16", "h09"]})
    fp = _facts(SPEC_CLAIM)
    assert set(headlines.eligible_templates("reasons", fp, tenant=tenant)) == {"h04", "h16"}
    # nothing pinned fits "mistakes": the style's own formula, never no headline
    assert headlines.eligible_templates("mistakes", fp, tenant=tenant) == ["s-mistakes"]


def test_tenant_exclude_drops_templates():
    tenant = _PinnedTenant({"exclude": ["h04", "s-reasons"]})
    eligible = headlines.eligible_templates("reasons", _facts(SPEC_CLAIM), tenant=tenant)
    assert "h04" not in eligible and "s-reasons" not in eligible and "h16" in eligible


def test_an_explicit_template_wins_over_the_tenant_pin_but_never_over_evidence():
    tenant = _PinnedTenant({"exclude": ["h04"]})
    fp = _facts(SPEC_CLAIM)
    assert headlines.resolve_plan("reasons", fp, tenant=tenant, requested="h04", today=TODAY)["id"] == "h04"
    with pytest.raises(headlines.HeadlineTemplateError):
        headlines.resolve_plan("reasons", fp, tenant=tenant, requested="h09", today=TODAY)
    with pytest.raises(headlines.HeadlineTemplateError):
        headlines.resolve_plan("mistakes", fp, tenant=tenant, requested="h04", today=TODAY)
    with pytest.raises(headlines.HeadlineTemplateError):
        headlines.resolve_plan("reasons", fp, tenant=tenant, requested="h99", today=TODAY)


def test_harness_run_takes_a_headline_template_flag():
    from harness import cli

    args = cli.build_parser().parse_args(["run", "ad.txt", "--cartridges", "listicle", "--headline-template", "h04"])
    assert args.headline_template == "h04"


# ---------------------------------------------------------------------------
# 10. the gate: the chosen template, with a repair message naming it
# ---------------------------------------------------------------------------

def test_gate_accepts_the_chosen_templates_headline():
    plan = _plan("h06")
    page = _page_for(plan)
    assert listicle.find_listicle_violations(page, style="reasons", headline=plan) == []


def test_gate_rejects_another_headline_and_names_the_template():
    plan = _plan("h06")
    page = _page_for(plan, headline="5 Reasons Busy Parents Are Choosing Home Infrared Saunas")
    problems = listicle.find_listicle_violations(page, style="reasons", headline=plan)
    formula = [p for p in problems if p["key"] == "listicle:headline_formula"]
    assert formula
    assert "h06" in formula[0]["issue"]
    assert plan["headline_pattern"] in formula[0]["issue"]


def test_gate_checks_n_against_the_item_count_and_fixes_it_deterministically():
    plan = _plan("h07")
    page = _page_for(plan, n_items=6)
    page["headline"] = headlines.example_headline(plan, 5)
    problems = listicle.find_listicle_violations(page, style="reasons", headline=plan)
    assert any(p["key"] == "listicle:headline_formula" and "6 items" in p["issue"] for p in problems)
    assert apply_deterministic_fixes(page, problems, set(), cartridge_name="listicle",
                                     listicle_headline=plan) == 1
    assert page["headline"].startswith("6 Ways")
    assert listicle.find_listicle_violations(page, style="reasons", headline=plan) == []


def test_a_spelled_out_count_is_fixed_under_a_template():
    plan = _plan("h13")
    page = _page_for(plan)
    page["headline"] = "Five" + page["headline"][1:]
    assert listicle.fix_headline_number(page, headline=plan) == headlines.example_headline(plan, 5)


def test_audience_slot_still_rejects_a_bare_generic_word():
    plan = _plan("h06")
    page = _page_for(plan, headline="5 Reasons People Started Switching to Home Infrared Saunas")
    assert any(p["key"] == "listicle:headline_slots" for p in headlines.find_headline_violations(page, plan))


def test_the_style_formula_plan_uses_the_existing_checks():
    plan = _plan("s-reasons", facts_pack=FACTS_PACK)
    assert headlines.is_legacy(plan)
    page = _listicle_page("reasons")
    assert listicle.find_listicle_violations(page, style="reasons", headline=plan) == []
    page["headline"] = "5 Reasons Are Choosing Home Infrared Saunas"
    assert any(p["key"] == "listicle:headline_slots"
               for p in listicle.find_listicle_violations(page, style="reasons", headline=plan))
    assert listicle.writer_style_lines("reasons", headline=plan) == listicle.writer_style_lines("reasons")


def test_revise_reads_the_template_back_from_page_json():
    plan = _plan("h04", facts_pack=FACTS_PACK)
    page = _page_for(plan)
    page["headline_template_id"] = "h04"
    again = headlines.plan_for_page(page, FACTS_PACK, tenant=TENANT, today=TODAY)
    assert again["id"] == "h04" and again["headline_pattern"] == plan["headline_pattern"]
    assert headlines.plan_for_page(_listicle_page("reasons"), FACTS_PACK, tenant=TENANT) is None


# ---------------------------------------------------------------------------
# 11. the writer is told the template, its slots and the evidence to cite
# ---------------------------------------------------------------------------

def test_writer_lines_state_the_template_slots_and_evidence():
    plan = _plan("h14")
    text = " ".join(listicle.writer_style_lines("reasons", headline=plan))
    assert '"h14"' in text
    assert plan["headline_pattern"] in text
    assert "<product>" in text and "<common_solution>" in text
    assert "never a competitor" in text.lower() or "never a brand" in text.lower()
    assert SPEC_CLAIM["id"] in text
    assert headlines.template("h14")["item_pattern"] in text
    assert 'Set page.json\'s "style" field to "reasons"' in text


def test_writer_lines_never_offer_a_banned_name_as_the_full_name_example():
    plan = _plan("h04", facts_pack=FACTS_PACK)
    text = " ".join(listicle.writer_style_lines("reasons", headline=plan))
    assert '"Peak Fuji"' in text          # the run's own product
    assert "Crown" not in text             # a PEAK model whose name vocab.yaml bans


def test_every_slot_description_survives_yaml_whole():
    for tid in headlines.template_ids():
        for slot in headlines.template(tid)["slots"].values():
            assert set(slot) <= {"kind", "max_words", "description", "options"}, (tid, slot)


def test_writer_lines_for_template_16_allow_game_changer_in_the_headline_only():
    text = " ".join(listicle.writer_style_lines("reasons", headline=_plan("h16")))
    assert "Game-Changer" in text and "headline only" in text


def test_writer_lines_for_template_11_ban_fear_words():
    text = " ".join(listicle.writer_style_lines("mistakes", headline=_plan("h11")))
    for word in ("EMF", "radiation", "toxins", "off-gassing"):
        assert word in text


def test_the_writer_prompt_carries_the_template(tmp_path):
    from harness.write import _append_listicle_style_guidance

    lines = []
    _append_listicle_style_guidance(lines, "listicle", "reasons", headline=_plan("h04"))
    assert any("h04" in line for line in lines)
    lines = []
    _append_listicle_style_guidance(lines, "article", "reasons", headline=_plan("h04"))
    assert lines == []


# ---------------------------------------------------------------------------
# 12. the chosen template id is recorded: page.json, state.json, A/B/C test
# ---------------------------------------------------------------------------

def test_record_listicle_choice_keeps_the_template_id(tmp_path):
    runstate.init_state(tmp_path, pages=["listicle"])
    runstate.record_listicle_choice(tmp_path, style="reasons", look="cards", headline_template_id="h04")
    assert runstate.load_state(tmp_path)["listicle"] == {"style": "reasons", "look": "cards",
                                                         "headline_template_id": "h04"}


def test_a_fake_run_records_the_chosen_template():
    from evals import fake_run

    fixture = TENANT.fixtures_dir / "founder-warranty-demo.txt"
    code, run_dir, pages = fake_run.run_once(str(fixture), tenant="peak-saunas", cartridges="listicle",
                                             style="reasons", headline_template="h04")
    assert code == 0
    page = json.loads(pages[0].read_text())
    assert page["headline_template_id"] == "h04"
    assert "Must-Have" in page["headline"]
    assert runstate.load_state(run_dir)["listicle"]["headline_template_id"] == "h04"


def test_a_default_fake_run_records_the_style_formula():
    from evals import fake_run

    fixture = TENANT.fixtures_dir / "founder-warranty-demo.txt"
    code, run_dir, pages = fake_run.run_once(str(fixture), tenant="peak-saunas", cartridges="listicle",
                                             style="myths")
    assert code == 0
    assert json.loads(pages[0].read_text())["headline_template_id"] == "s-myths"


def test_abtest_variant_records_the_template_and_results_group_by_it(monkeypatch):
    from tests.test_abtest_cycle67 import _insert, _write_fake_run

    def runner(tenant, input_path, arm, seed):
        run_dir = _write_fake_run(tenant, arm, seed)
        page_path = run_dir / arm.cartridge / "page.json"
        page = json.loads(page_path.read_text())
        page["headline_template_id"] = {"A": "h04", "B": "h16"}.get(chr(ord("A") + (seed // 1000) - 1), "h04")
        page_path.write_text(json.dumps(page))
        return 0, run_dir

    rc, rec = abtest.create_test(TENANT, input_path=__file__, name="Headline Test", seed=5, runner=runner)
    assert rc == 0
    ids = [v.get("headline_template_id") for v in rec["variants"]]
    listicle_variants = [v for v in rec["variants"] if v["arm"].startswith("listicle:")]
    for v in listicle_variants:
        assert v["headline_template_id"] in ("h04", "h16")
    for v in rec["variants"]:
        if not v["arm"].startswith("listicle:"):
            assert v.get("headline_template_id") is None
    assert ids

    rec["status"] = "live"
    abtest.save_test(TENANT, rec)
    db = abtest.abevents.db_path(TENANT)
    for v in rec["variants"]:
        _insert(db, rec["test_id"], v["key"], "view", 100)
        _insert(db, rec["test_id"], v["key"], "cta", 10)
    by_headline = abtest.pooled_arm_stats(TENANT, by="headline")
    expected = {}
    for v in listicle_variants:
        slot = expected.setdefault(v["headline_template_id"], {"views": 0, "clicks": 0, "tests": 0})
        slot["views"] += 100
        slot["clicks"] += 10
        slot["tests"] += 1
    assert by_headline == expected


def test_abtest_library_by_headline_prints_template_rows(monkeypatch, capsys):
    from harness import cli

    rec = {"test_id": "hl-ab12", "status": "live", "created_at": "2026-09-24T00:00:00",
           "variants": [{"key": "A", "arm": "listicle:reasons:cards", "headline_template_id": "h04"},
                        {"key": "B", "arm": "listicle:mistakes:editorial", "headline_template_id": "h11"},
                        {"key": "C", "arm": "quiz"}]}
    abtest.save_test(TENANT, rec)
    assert cli.main(["abtest", "library", "--by", "headline", "--tenant", TENANT.name]) == 0
    out = capsys.readouterr().out
    assert "h04" in out and "h11" in out
    assert "listicle:reasons:cards" not in out
