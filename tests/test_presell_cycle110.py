"""Cycle 110: from fact-check pages to pre-sell pages (~/asset-inbox/c110/BRIEF.md,
~/asset-inbox/teardown/TEARDOWN.md).

Every gate here is driven by the real failing text the teardown quotes from
the 40 live landing arms (2026-10-15):
  - the placeholder row ("Placeholder for offer, rendered separately by the
    page." / "placeholder", aiir-300 A and aiir-fuji-price A), in the writer
    gate, the rendered visible-text gate and the render-time tidy;
  - the returns FAQ ("Can I return it if it doesn't fit my space?");
  - "The discount applies automatically at checkout" next to a coded offer;
  - "PEAK' warranty" from the retired-name pass;
  - the blank subhead (aiir-fuji-price B);
  - the ad-grading lines ("not lifetime coverage", "What the research really
    says about infrared sauna", "priced plainly", "Here is what's actually
    true", "not part of what PEAK verifies", "no separate list price");
  - "Is this a treatment for RA or lupus? No." in the FAQ;
  - a hero that shares no words with the hook (the "Women over 40" ad);
plus the reasons model, the figure tokens, the CTA phrase by angle, the
buyer FAQ, the reading level, the pre-sell look (`peak`) with and without
the grounding fields agent A adds (facts_pack contract, brief 3.2), the
design-gate checks (chrome window, CTA spacing, dead clicks), the load
budget and the lean theme layout. No model call, no network.
"""
import copy
import json
import re

import pytest

from evals import fake_run
from harness import design_gate, landing, landing_sell, offer, pagechecks, perf, persuasion, repair, theme_lean
from harness.render import render_page
from tests.support import FROZEN_VERIFIED, REPO_ROOT, TENANT
from tests.test_landing_cycle82 import CARTRIDGE_DIR, LANDING_BRIEF, _facts_pack, _gates, _keys, _page

AIIR = "$300 off every indoor sauna. $500 off outdoor. Prices go up October 16, so Thursday is your last chance"
pytestmark = pytest.mark.frozen_tenant  # Cycle 113: the claims and tenant.yaml of 9bdb996 (tests/conftest.py)
PROMO = next(c for c in json.loads(FROZEN_VERIFIED.read_text()) if c["category"] == "promo")
SRC = "https://peaksaunas.com/products/fuji"


def _offer_facts():
    fp = _facts_pack()
    brief = dict(LANDING_BRIEF, offer=offer.extract_offer(AIIR))
    assert offer.apply_to_run(brief, fp, [PROMO], today="2026-10-14")
    return fp, brief


def _violations(page, fp=None, brief=LANDING_BRIEF):
    return landing.find_landing_violations(page, fp or _facts_pack(), ad_brief=brief, tenant=TENANT)


def _render(tmp_path, page, fp=None, brief=LANDING_BRIEF, look="peak", tenant=TENANT):
    out = tmp_path / "run" / "landing"
    page = copy.deepcopy(page)
    page["look"] = look
    render_page(cartridge_name="landing", page=page, ad_brief=brief, facts_pack=fp or _facts_pack(),
                cartridges_dir=REPO_ROOT / "cartridges", brand_dir=tenant.brand_dir,
                templates_dir=REPO_ROOT / "harness" / "templates", out_dir=out, published="2026-10-15",
                updated="2026-10-15", tenant=tenant, download_assets=False)
    return (out / "index.html").read_text(), json.loads((out / "page.json").read_text())


def _visible(html):
    from harness.claims import strip_html_to_visible_text

    return re.sub(r"\s+", " ", strip_html_to_visible_text(html))


# ---------------------------------------------------------------------------
# Phase 0: hotfixes, from the live pages' own text
# ---------------------------------------------------------------------------

PLACEHOLDER_ROW = {"ad_said": "Placeholder for offer, rendered separately by the page.", "fact": "placeholder",
                   "claim_ids": [PROMO["id"]]}


def test_the_placeholder_row_fails_the_writer_gate_in_reasons():
    page = _page()
    page["reasons"][0]["body"] = "Placeholder for offer, rendered separately by the page."
    assert "landing:placeholder:$.reasons[0].body" in _keys(_violations(page))


def test_the_placeholder_row_fails_the_rendered_visible_text_gate():
    html = ("<html><body><style>.x::placeholder{color:red}</style><input placeholder=\"x\"><ol><li>"
            "<p>Placeholder for offer, rendered separately by the page.</p><p>placeholder</p></li></ol></body></html>")
    hits = pagechecks.find_placeholder_visible_text(html)
    assert hits and "Placeholder" in hits[0]["term"]
    # CSS and attributes are not visible text
    assert pagechecks.find_placeholder_visible_text('<style>.a::placeholder{}</style><input placeholder="Email">') == []


def test_a_rerender_drops_the_live_placeholder_row_and_the_returns_faq(tmp_path):
    """aiir-300 A as it is live: the placeholder row cites the promo claim, and
    its FAQ asks "Can I return it if it doesn't fit my space?"."""
    fp, brief = _offer_facts()
    page = _page()
    del page["reasons"]
    page["checked_claims"] = [PLACEHOLDER_ROW, {"ad_said": "The ad shows a roomy two-person cabin.",
                                               "fact": "The Peak Fuji is a two-person cabin built from Canadian red "
                                                       "cedar, inside and out.", "claim_ids": ["spec-fuji-capacity"]}]
    page["faq"]["questions"].append({
        "question": "Can I return it if it doesn't fit my space?",
        "answer": "Returns require the sauna disassembled and repacked in its original crate, with a restocking fee "
                  "and return shipping paid by the customer.", "claim_ids": ["returns-policy"]})
    html, written = _render(tmp_path, page, fp, brief, look="cinematic")
    text = _visible(html)
    assert "Placeholder" not in text and "rendered separately" not in text and "placeholder" not in text.lower()
    assert "Can I return it" not in text and "restocking" not in text
    assert [c["fact"] for c in written["checked_claims"]] == [page["checked_claims"][1]["fact"]]
    assert "$300 off with code INDOOR300" in text   # the renderer's own offer row stays


def test_returns_copy_fails_the_gate():
    page = _page()
    page["faq"]["questions"][1] = {"question": "Can I return it if it doesn't fit my space?",
                                   "answer": "Returns require the sauna disassembled and repacked in its original crate.",
                                   "claim_ids": ["returns-policy"]}
    keys = _keys(_violations(page))
    assert "landing:returns:$.faq.questions[1].question" in keys and "landing:returns:$.faq.questions[1].answer" in keys


@pytest.mark.parametrize("text, bad", [
    ("The discount applies automatically at checkout, right alongside the regular price.", True),   # aiir-300 B
    ("The discount applies automatically at checkout, so the price you see is the price you pay.", True),
    ("Does the sale price apply automatically?", True),
    ("Use the code at checkout to take the money off.", False),
    ("The app starts the heat automatically at the time you set.", False),
])
def test_applies_automatically_fails_next_to_a_coded_offer(text, bad):
    fp, brief = _offer_facts()
    page = _page(cta_text="Shop the sale")
    page["hero"]["subhead"] = text
    hits = [p for p in _violations(page, fp, brief) if ":auto_offer:" in p["key"]]
    assert bool(hits) is bad
    # with no offer in the ad the word is not this gate's business
    assert not [p for p in _violations(page) if ":auto_offer:" in p["key"]]


def test_the_retired_name_pass_keeps_the_possessive():
    out = repair._replace_retired_name("Peak Saunas' warranty covers the heaters.", "Peak Saunas", "PEAK", [])
    assert out == "PEAK's warranty covers the heaters." and "PEAK'" + " " not in out
    assert repair._replace_retired_name("Peak Saunas’ support", "Peak Saunas", "PEAK", []) == "PEAK’s support"
    assert repair._replace_retired_name("the Peak Saunas app", "Peak Saunas", "PEAK", ["Peak Saunas app"]) == \
        "the Peak Saunas app"


@pytest.mark.parametrize("field", ["eyebrow", "headline", "subhead"])
def test_a_blank_first_screen_line_fails(field):
    page = _page()
    page["hero"][field] = "  "     # aiir-fuji-price B shipped a blank subhead
    assert f"landing:hero_required:{field}" in _keys(_violations(page))


def test_the_renderer_prints_the_warranty_sentence_nowhere_but_the_trust_term(tmp_path):
    from harness.vocab import ALLOWED_WARRANTY_SENTENCE

    for look in ("open", "proof", "cinematic"):
        html, _ = _render(tmp_path / look, _page(), look=look)
        # the canned page's own copy never states it, so any copy here is the renderer's
        assert ALLOWED_WARRANTY_SENTENCE not in _visible(html), look


def test_text_us_is_in_the_closing_band_and_never_beside_the_hero_pill(tmp_path):
    for look in ("open", "proof", "cinematic", "peak"):
        html, _ = _render(tmp_path / look, _page(), look=look)
        hero = html[:html.index("</header>")]
        assert "sms:" not in hero, look
        assert len(re.findall(r'<a class="ld-text" data-el="sms" href="sms:', html)) == 1, look


# ---------------------------------------------------------------------------
# Phase 2: the reasons, the ad-reference ban, the hook, the reading level
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("text, path", [
    ("Coverage is a 7-year residential warranty, with terms by component, not lifetime coverage.", "reasons"),
    ("What the research really says about infrared sauna", "headline"),
    ("A full-spectrum infrared sauna, priced plainly", "headline"),
    ("Here is what's actually true, in plain terms.", "subhead"),
    ("That wellness-club claim is not part of what PEAK verifies for this product.", "reasons"),
    ("The Peak Fuji is priced at $8,250, with no separate list price to compare it against.", "faq"),
    ("The ad said the price was lower.", "reasons"),
    ("See the real numbers behind the ad", "closing"),
])
def test_lines_that_grade_the_ad_fail(text, path):
    page = _page()
    if path == "reasons":
        page["reasons"][1]["body"] = text
    elif path == "faq":
        page["faq"]["questions"][0]["answer"] = text
    elif path == "closing":
        page["closing"]["headline"] = text
    else:
        page["hero"][path] = text
    assert any(k.startswith("landing:ad_reference:") for k in _keys(_violations(page))), text


def test_crediting_the_speaker_is_the_one_way_to_name_the_ad():
    page = _page()
    page["hero"]["subhead"] = "In the ad, she says, \"I sleep better after an evening session.\""
    assert not [p for p in _violations(page) if p["key"].startswith("landing:ad_reference:")]


def test_not_an_add_on_at_most_once():
    page = _page()
    page["reasons"][0]["body"] += " Red light is not an add-on."
    page["reasons"][1]["body"] += " It is not an add-on."
    assert "landing:ad_reference:add_on" in _keys(_violations(page))


def test_the_tenant_banned_phrases_come_from_vocab():
    page = _page()
    page["closing"]["headline"] = "Nothing to submit, no form required"
    assert any(k.startswith("landing:ad_reference:") for k in _keys(_violations(page)))
    assert "plainly" in persuasion.banned_phrases()


def test_the_ra_lupus_faq_fails():
    page = _page()
    page["faq"]["questions"][0] = {"question": "Is this a treatment for RA or lupus?",
                                   "answer": "No. The Fuji is a sauna, not a medical device.", "claim_ids": []}
    assert "landing:faq_health:0" in _keys(_violations(page))


# the "Women over 40" ad's own brief (run 20261012-223743-...-pzg4, 0 clicks / 221 views on two arms)
WOMEN_40 = dict(LANDING_BRIEF, hook="Tired. Stressed. Aching. Women over 40, this is for you.",
                angle="A premium infrared sauna brand positions itself as the Peloton of saunas, offering "
                      "medical-grade technology plus guided wellness sessions for women over 40 seeking natural "
                      "vitality.",
                promise="Naturally youthful energy and relief from fatigue, stress, and body aches",
                audience="women over 40")


def test_a_hero_that_shares_no_words_with_the_hook_fails():
    page = _page()
    # the live page: the eyebrow echoes the hook, the headline is a spec label
    page["hero"].update(eyebrow="Tired, stressed, aching? This is for you",
                        headline="Full-spectrum heat and red light, built into one cabin", accent_phrase="red light",
                        subhead="A two-person infrared sauna with full-spectrum heat and red light therapy included "
                                "standard, so winding down doesn't mean adding another subscription.")
    hits = [p for p in _violations(page, brief=WOMEN_40) if p["key"] == "landing:hook_match"]
    assert hits and "Tired. Stressed. Aching." in hits[0]["issue"] and "(headline: none)" in hits[0]["issue"]
    page["hero"].update(eyebrow="For women over 40", headline="Tired, stressed and aching? Unwind at home tonight",
                        accent_phrase="Unwind at home")
    assert not [p for p in _violations(page, brief=WOMEN_40) if p["key"] == "landing:hook_match"]
    score = persuasion.hook_match("For women over 40 Tired, stressed and aching? Unwind at home tonight", WOMEN_40)
    assert score["ok"] and score["score"] > 0.5


def test_a_quoted_speaker_line_matches_the_hook():
    brief = dict(LANDING_BRIEF, hook="I built this company because I hated how confusing sauna shopping used to be.")
    hero = "In the ad, he says, \"I built this company because I hated how confusing sauna shopping used to be.\""
    assert persuasion.hook_match(hero, brief, quotes=[brief["hook"]])["quoted"]


def test_the_reading_level_gate():
    page = _page()
    assert not [p for p in _violations(page) if p["key"] == "landing:reading_level"]
    dense = ("Comprehensive thermoregulatory considerations notwithstanding, contemporary infrared "
             "configurations demonstrate substantially differentiated physiological characteristics.")
    for r in page["reasons"]:
        r["body"] = dense
    hits = [p for p in _violations(page) if p["key"] == "landing:reading_level"]
    assert hits and hits[0]["grade"] > persuasion.MAX_GRADE
    assert persuasion.grade_level("The cabin heats fast. You sit down. You relax.") < 4


def test_reasons_need_four_levers_and_a_known_proof_ref():
    page = _page()
    for r in page["reasons"]:
        r["lever"] = "outcome"
    assert "landing:reason_levers" in _keys(_violations(page))
    page = _page()
    page["reasons"][0]["proof_ref"] = "review-quote-99"
    page["reasons"][1]["asset_role"] = "spaceship"
    keys = _keys(_violations(page))
    assert "landing:reason_proof_ref:0" in keys and "landing:reason_asset_role:1" in keys


def test_the_benefit_minimum_counts_the_reasons():
    from harness import claims

    page = _page()
    assert claims.find_benefit_claim_shortfall(page, _facts_pack(), "landing") == []


# ---------------------------------------------------------------------------
# Figure tokens, the CTA by angle, the buyer FAQ
# ---------------------------------------------------------------------------

def test_the_sale_headline_carries_the_ads_number_through_tokens(tmp_path):
    fp, brief = _offer_facts()
    page = _page(cta_text="Get $300 off the Fuji")
    page["hero"].update(eyebrow="Community Savings Sale", headline="{offer_amount} off the Fuji, today's price ends "
                        "{deadline_day}", accent_phrase="{offer_amount} off")
    assert not [p for p in _violations(page, fp, brief) if ":hero_token:" in p["key"] or ":number:" in p["key"]]
    html, written = _render(tmp_path, page, fp, brief)
    assert "$300 off the Fuji, today's price ends Thursday" in _visible(html)   # Oct 16 raises -> Thu Oct 15
    assert written["hero"]["headline"].startswith("{offer_amount} off")           # page.json keeps the tokens
    assert "Community Savings Sale · $300 off · ends Thu, Oct 15" in _visible(html)


def test_an_unknown_or_unfillable_token_fails():
    page = _page()
    page["hero"]["headline"] = "Save {offer_amount} on the Fuji"        # no offer on this page
    page["reasons"][0]["body"] = "Only {price} today."                    # tokens live in the first screen only
    keys = _keys(_violations(page))
    assert "landing:hero_token:$.hero.headline" in keys and "landing:hero_token:$.reasons[0].body" in keys


def test_the_cta_phrase_follows_the_ads_angle():
    schema = json.loads((CARTRIDGE_DIR / "schema.json").read_text())
    fp, brief = _offer_facts()
    assert landing.allowed_cta_texts(schema, fp, brief, TENANT) == ["Get $300 off the Fuji", "Shop the sale"]
    financing = dict(LANDING_BRIEF, hook="Major Updates: new flexible payment plans")
    assert landing.allowed_cta_texts(schema, _facts_pack(), financing, TENANT)[0] == "See monthly payments"
    story = dict(LANDING_BRIEF, speaker_pronoun="she")
    texts = landing.allowed_cta_texts(schema, _facts_pack(), story, TENANT)
    assert texts[0] in ("See the Fuji she uses", "Shop the Fuji")
    plain = dict(LANDING_BRIEF, speaker_pov="brand")
    assert landing.allowed_cta_texts(schema, _facts_pack(), plain, TENANT) == ["Shop the Fuji", "See the Fuji price"]
    assert persuasion.ad_angle(financing, _facts_pack()) == "financing"


def test_the_faq_answers_the_buyer_not_the_installer():
    page = _page()
    page["faq"]["questions"][1] = {"question": "How is the Fuji delivered and assembled?",
                                   "answer": "Shipping is free, and the cabin goes together with clasps.",
                                   "claim_ids": ["shipping-policy"]}
    assert "landing:faq_logistics" in _keys(_violations(page))
    page = _page()
    page["faq"]["questions"][1] = {"question": "Can I use my HSA or FSA?", "answer": "Yes.", "claim_ids": []}
    fp = _facts_pack()
    fp["verified_claims"] = [c for c in fp["verified_claims"] if "HSA" not in c.get("text", "")]
    assert "landing:faq_hsa" in _keys(landing.find_landing_violations(page, fp, ad_brief=LANDING_BRIEF, tenant=TENANT))


# ---------------------------------------------------------------------------
# The pre-sell look (`peak`): structure, CTA placements, fallbacks
# ---------------------------------------------------------------------------

def _rich(fp=None):
    """The canned facts pack plus every field of the facts_pack contract
    (brief 3.2) that grounding (agent A) adds."""
    fp = copy.deepcopy(fp or _facts_pack())
    add = {
        "reviews-store-live": "Rated 4.6 out of 5 from 4,260 reviews on Judge.me (fetched 2026-10-15).",
        "review-quote-1": "We use it almost every night. Setup took one afternoon and it heats up quickly.",
        "review-quote-2": "Delivery was on time and the crew was great. My wife and I love the red light panel.",
        "review-quote-3": "Best purchase we made this year. The cedar smells amazing and it feels well built.",
        "financing-monthly-fuji": "Est. $458.33/mo for 18 months at 0% APR with $0 down through Bread Pay.",
        "units-delivered-12mo": "2,000+ saunas delivered to US homes in the last 12 months (Shopify, Oct 2026).",
        "pwc-value-588": "Peak Wellness Club ($588/yr value) included free",
        "pwc-included-free": "Peak Wellness Club is included free with every Peak sauna.",
        "gbrain-warranty-lifetime-definition": "Peak Saunas 7-Year Residential Warranty: residential use only. "
                                               "Cabin and heaters are covered for 7 years from delivery; lifetime "
                                               "U.S.-based support.",
        "founder-ceo": "Austin Laudenslager is the Founder & CEO of Peak Saunas.",
        "gbrain-allowlist-us-owned": "US-owned company.",
        "hsa-fsa-redo": "Peak saunas may be eligible for HSA/FSA purchase through Redo.",
    }
    have = {c["id"] for c in fp["verified_claims"]}
    fp["verified_claims"] += [{"id": k, "text": v, "category": "trust", "source": SRC} for k, v in add.items()
                              if k not in have]
    fp["reviews_store"] = {"text": add["reviews-store-live"], "rating": 4.6, "count": 4260,
                           "claim_ids": ["reviews-store-live"]}
    fp["review_quotes"] = [{"claim_id": f"review-quote-{i}", "text": add[f"review-quote-{i}"], "author": a,
                            "date": "2026-10-01", "stars": 5, "product": "Peak Fuji"}
                           for i, a in ((1, "Susan L."), (2, "Mark T."), (3, "Dana R."))]
    fp["financing"] = {"monthly": {"text": add["financing-monthly-fuji"], "amount": 458.33, "months": 18, "apr": 0,
                                   "disclosure": "Est. $458.33/mo is based on $8,250 financed over 18 monthly "
                                                 "payments at 0% APR with $0 down; your actual rate, term, and "
                                                 "payment are set by Bread Pay when you apply.",
                                   "claim_ids": ["financing-monthly-fuji"]}}
    fp["units"] = {"last_12mo": {"text": add["units-delivered-12mo"], "claim_ids": ["units-delivered-12mo"]},
                   "all_time": None}
    fp["included"] = [{"text": "Medical-grade red light therapy, included standard",
                       "claim_ids": ["gbrain-allowlist-red-light"]},
                      {"text": "Peak Wellness Club is included free with every Peak sauna",
                       "claim_ids": ["pwc-included-free"]},
                      {"text": "Free shipping", "claim_ids": ["shipping-policy"]}]
    fp["hsa_fsa"] = {"text": add["hsa-fsa-redo"], "claim_ids": ["hsa-fsa-redo"]}
    return fp


def _order(html, needles):
    at = [html.index(n) for n in needles]
    return at == sorted(at)


def test_the_look_renders_its_sections_in_order_with_six_ctas_and_the_sticky_bar(tmp_path):
    html, _ = _render(tmp_path, _page(), _rich())
    assert _order(html, ['data-look-pattern="hero:peak_first"', 'data-look-pattern="presell:stats"',
                         'data-look-pattern="presell:reasons"', 'data-look-pattern="presell:included"',
                         'data-look-pattern="presell:reviews"', 'data-look-pattern="presell:setup"',
                         'data-look-pattern="faq"', 'data-look-pattern="presell:closing"', 'class="ld-bar"'])
    els = re.findall(r'class="ld-btn[^"]*" data-el="(\w+)"', html)
    # after reasons 1 and 3 (never after the last), so no stretch passes 1.5 phone screens; no model rows here
    assert els == ["hero", "mid", "mid", "stack", "reviews", "closing", "sticky"]
    # every CTA anchor is a class the beacon counts and carries data-el
    for a in re.findall(r"<a [^>]*>", html):
        if 'href="' + _page()["cta_url"] in a:
            assert "data-el=" in a and re.search(r'class="[^"]*\b(ld-btn|ld-link|hr-cta)\b', a), a


def test_the_first_screen_carries_the_proof_row_and_the_monthly_figure(tmp_path):
    html, _ = _render(tmp_path, _page(), _rich())
    first = html[html.index('data-look-pattern="hero:peak_first"'):html.index("</header>")]
    text = _visible(first)
    assert "4.6 · 4,260 reviews" in text and "2,000+ delivered in 12 months" in text
    assert "$8,250" in text and "$458.33/mo for 18 months at 0% APR*" in text
    assert _order(first, ['class="pf-h1"', 'class="pf-proof"', 'class="pf-price"', 'data-el="hero"'])
    body = _visible(html)
    assert "Est. $458.33/mo is based on $8,250 financed" in body          # the disclosure is on the page
    assert body.count("4.6 ★") + body.count("4.6 ·") <= 3                # rating in few places
    assert "Peak Wellness Club ($588/yr value) included free" in body
    assert "Peak saunas may be eligible for HSA/FSA purchase through Redo." in body
    assert "Lifetime U.S.-based support" in body and "US-owned company" in body
    assert "Austin Laudenslager, Founder & CEO, is responsible for every claim on this page." in body
    for q in _rich()["review_quotes"]:
        assert q["text"] in body
    assert "Clasp-together" in body or "Delivery" in body


def test_the_fallbacks_when_grounding_has_not_added_its_fields(tmp_path):
    html, _ = _render(tmp_path, _page(), _facts_pack())
    first = _visible(html[html.index('data-look-pattern="hero:peak_first"'):html.index("</header>")])
    assert "4.79 · 47 reviews" in first          # the product rating stands in for the store rating
    assert "delivered" not in first and "/mo" not in first
    body = _visible(html)
    assert "What owners say" not in body              # no quotes: no wall
    assert "Financing is available through Bread Pay at checkout." in body   # price alone + the fixed sentence


def test_a_field_whose_claim_does_not_verify_never_renders(tmp_path):
    fp = _rich()
    fp["units"]["last_12mo"]["claim_ids"] = ["not-a-claim"]
    fp["financing"]["monthly"]["claim_ids"] = ["not-a-claim"]
    fp["review_quotes"][0]["claim_id"] = "not-a-claim"
    html, _ = _render(tmp_path, _page(), fp)
    body = _visible(html)
    assert "2,000+" not in body and "/mo" not in body and fp["review_quotes"][0]["text"] not in body
    fp = _rich()
    del fp["financing"]["monthly"]["disclosure"]        # never without its disclosure
    assert landing_sell.financing_monthly(fp) is None


def test_the_sale_page_shows_the_net_price_struck_list_price_and_deadline(tmp_path):
    fp, brief = _offer_facts()
    fp = _rich(fp)
    fp["offer"].update(net_price="$7,950", list_price="$8,250", code="INDOOR300")   # agent A's offer_view fields
    page = _page(cta_text="Get $300 off the Fuji")
    html, written = _render(tmp_path, page, fp, brief)
    first = html[html.index('data-look-pattern="hero:peak_first"'):html.index("</header>")]
    assert "<b>$7,950</b> with code INDOOR300<s>$8,250</s>" in first
    assert "ends Thu, Oct 15" in _visible(first)
    bar = html[html.index('<div class="ld-bar">'):]
    assert "$7,950<s>$8,250</s>" in bar and "Get $300 off the Fuji" in bar
    why = html[html.index('data-look-pattern="presell:reasons"'):]
    assert why.index("$7,950 with code INDOOR300") < why.index(_page()["reasons"][0]["headline"])
    assert "Today's price ends Thu, Oct 15." in _visible(html)
    assert "countdown" not in html.lower() and "only" not in _visible(first).lower()


def test_no_net_price_means_the_list_price_and_the_claims_offer_line(tmp_path):
    # cycle 110 integration: one amount always gives a net price (agent A offer_view), so the fallback is
    # tested with a claim that has no single amount ("up to $300")
    fp, brief = _offer_facts()
    fp["offer"]["text"] = ("Fall Sale: save up to $300 on indoor saunas with code INDOOR300, applied at checkout; "
                           "prices go up on October 16, 2099.")
    for key in ("net_price", "list_price"):
        fp["offer"].pop(key, None)
    assert offer.offer_view(fp)["net_price"] is None
    html, _ = _render(tmp_path, _page(cta_text="Shop the sale"), fp, brief)
    first = html[html.index('data-look-pattern="hero:peak_first"'):html.index("</header>")]
    assert "<b>$8,250</b>" in first and "save up to $300 on indoor saunas with code INDOOR300" in first
    assert "<s>" not in first and "$7,950" not in first


def test_a_legacy_page_in_the_presell_look_shows_its_facts_never_the_ad_lines(tmp_path):
    page = _page()
    del page["reasons"]
    page["checked_claims"] = [
        {"ad_said": "The ad promised a lifetime guarantee.", "claim_ids": ["warranty-terms"],
         "fact": "Coverage is a 7-year residential warranty, with terms by component, not lifetime coverage."},
        {"ad_said": "It plugs into your home.", "fact": "The Fuji runs on a 120V / 20A dedicated outlet.",
         "claim_ids": ["gbrain-fuji-power"]}]
    html, _ = _render(tmp_path, page)
    body = _visible(html)
    assert "not lifetime" not in body and "The ad" not in body and "120V / 20A dedicated outlet." in body


def test_model_cards_link_each_model_to_its_own_page(tmp_path, monkeypatch):
    from harness import cutouts

    fp = _rich()
    fp["verified_claims"] += [{"id": f"price-{s}", "text": f"$ {p}", "category": "price", "source": SRC}
                              for s, p in (("mini", "4,995"), ("matterhorn", "9,995"))]
    fp["verified_claims"].append({"id": "financing-monthly-mini", "text": "Est. $277.50/mo for 18 months",
                                  "category": "price", "source": SRC})
    fp["models"] = [
        {"slug": "mini", "name": "Peak Mini", "url": "https://peaksaunas.com/products/mini", "price_text": "$4,995",
         "price_claim_id": "price-mini"},
        {"slug": "fuji", "name": "Peak Fuji", "url": SRC, "price_text": "$8,250", "price_claim_id": "price-fuji"},
        {"slug": "matterhorn", "name": "Peak Matterhorn", "url": "https://peaksaunas.com/products/matterhorn",
         "price_text": "$9,995", "price_claim_id": "price-matterhorn"}]
    monkeypatch.setattr(cutouts, "cutout_asset", lambda tenant, slug: None)
    html, _ = _render(tmp_path, _page(), fp)
    cards = re.findall(r'<a class="pk-model ld-link" data-el="cards" href="([^"]+)">', html)
    assert cards[0] == SRC and len(cards) == 3     # the page's own model first
    assert "$277.50/mo*" in html and "ld-link" in html
    from harness import abtest

    assert "ld-link" in abtest.CTA_CLASSES


def test_the_look_loads_no_font_outside_the_theme_pair(tmp_path):
    html, _ = _render(tmp_path, _page())
    look_css = html[html.index('<div class="pk-lp adv-landing pk-ps">') - 20000:html.index('<div class="pk-lp')]
    assert "Montserrat" not in look_css.split(".adv-landing.pk-ps{")[-1]
    assert '--ps-heading:"Poppins"' in html


def test_the_people_photo_must_match_the_ads_audience():
    shirtless = {"tags": {"description": "A shirtless man with a towel in a cedar sauna"}}
    woman = {"tags": {"description": "A woman reading in a cedar sauna"}}
    assert landing_sell.audience_gender(WOMEN_40) == "women"
    assert landing_sell.photo_mismatches_audience(shirtless, WOMEN_40, shirtless["tags"])
    assert not landing_sell.photo_mismatches_audience(woman, WOMEN_40, woman["tags"])
    assert not landing_sell.photo_mismatches_audience(shirtless, LANDING_BRIEF, shirtless["tags"])


def test_the_deadline_is_the_last_day_of_the_sale():
    fp, _ = _offer_facts()
    dl = landing_sell.deadline(fp)
    assert (dl["day"], dl["date"], dl["short"]) == ("Thursday", "Oct 15", "Today's price ends Thu, Oct 15")


# ---------------------------------------------------------------------------
# Design gate additions and the load budget
# ---------------------------------------------------------------------------

def _probe(**kw):
    base = {"cta_expected": True, "viewport_width": 390, "scroll_width": 390, "height": 5000,
            "cta": {"top": 300}, "cta_tops": [300, 1400, 2400, 3300, 4200], "footer_top": 4500,
            "first_screen": {"eyebrow": {"top": 50, "bottom": 67}, "h1": {"top": 73, "bottom": 170},
                             "dek": {"top": 178, "bottom": 220}, "proof": {"top": 230, "bottom": 250},
                             "price": {"top": 256, "bottom": 280}, "cta": {"top": 290, "bottom": 342},
                             "photo": {"top": 354, "bottom": 774}},
            "dead_bait": []}
    base.update(kw)
    return base


def test_the_chrome_window_check():
    good = {s: _probe() for s in design_gate.CHROME_SCREENS}
    assert design_gate.chrome_window(good)["status"] == "pass"
    low = copy.deepcopy(good)
    low[(360, 780)]["first_screen"]["cta"] = {"top": 420, "bottom": 472}   # 472 + 116 > 780 - 212
    check = design_gate.chrome_window(low)
    assert check["status"] == "fail" and check["items"][0]["screen"] == "360x780"
    assert design_gate.chrome_window({(390, 844): {"cta_expected": True}})["status"] == "pass"   # not the pre-sell screen


def test_a_cta_within_every_one_and_a_half_screens():
    assert design_gate.cta_spacing(_probe(), presell=True)["status"] == "pass"
    gap = design_gate.cta_spacing(_probe(cta_tops=[300, 2400]), presell=True)          # cycle 82's 11% and 94%
    assert gap["status"] == "fail" and design_gate.cta_spacing(_probe(cta_tops=[300, 2400]))["status"] == "warn"


def test_dead_click_bait_fails_on_the_presell_look():
    bait = _probe(dead_bait=[{"sel": "li.pk-stat", "text": "4.6"}])
    assert design_gate.dead_clicks(bait, presell=True)["status"] == "fail"
    assert design_gate.dead_clicks(_probe(), presell=True)["status"] == "pass"


def test_the_static_load_budget(tmp_path):
    html, _ = _render(tmp_path, _page())
    page_dir = tmp_path / "run" / "landing"
    static = perf.static_budget(page_dir)
    assert static["html_bytes"] <= perf.HTML_MAX_BYTES and static["script_src"] == 0
    check = perf.budget_check(static, {"lcp_ms": 3100, "cls": 0.02, "lcp_element": "img", "transfer_bytes": 90000})
    assert check["status"] == "fail" and "LCP 3.10 s over 2.5 s" in check["detail"]
    # a dry render has no measured image sizes; with none of the static problems a fast page passes
    assert perf.budget_check(dict(static, problems=[]), {"lcp_ms": 900, "cls": 0.01,
                                                         "transfer_bytes": 1})["status"] == "pass"
    assert perf.measure(None, page_dir / "index.html") is None


# ---------------------------------------------------------------------------
# The lean theme layout
# ---------------------------------------------------------------------------

class _FakeShopify:
    def __init__(self, assets=None):
        self.assets = dict(assets or {})
        self.calls = []

    def _request(self, method, path, payload=None):
        self.calls.append((method, path))
        key = re.search(r"asset%5Bkey%5D=([^&]+)", path)
        key = key.group(1).replace("%2F", "/") if key else None
        if method == "GET":
            return (200, {"asset": {"key": key, "value": self.assets[key]}}) if key in self.assets else (404, {})
        if method == "PUT":
            self.assets[payload["asset"]["key"]] = payload["asset"]["value"]
            return 200, {"asset": {"key": payload["asset"]["key"], "updated_at": "now"}}
        return 500, {}


def test_push_landing_layout_dry_run_writes_nothing_and_apply_backs_up_first(tmp_path):
    client = _FakeShopify({"layout/landing.liquid": "old layout"})
    lines = []
    assert theme_lean.push(client, [1], dry_run=True, backup_dir=tmp_path, out=lines.append) == 0
    assert not [c for c in client.calls if c[0] == "PUT"] and any("would back up" in line for line in lines)
    assert theme_lean.push(client, [1], dry_run=False, backup_dir=tmp_path, out=lines.append, now="T") == 2
    assert (tmp_path / "1" / "layout__landing.liquid.T.bak").read_text() == "old layout"
    puts = [c for c in client.calls if c[0] == "PUT"]
    assert len(puts) == 2 and client.assets["layout/landing.liquid"] == theme_lean.local_text("layout/landing.liquid")


def test_the_lean_files_drop_the_chrome_and_keep_content_for_header():
    layout = theme_lean.local_text("layout/landing.liquid")
    tpl = theme_lean.local_text("templates/page.landing.liquid")
    assert "{{ content_for_header }}" in layout and "{{ content_for_layout }}" in layout
    assert "{%- layout 'landing' -%}" in tpl and "{{ page.content }}" in tpl
    for gone in ("section 'header'", "announcement", "Montserrat", "Inter", "Figtree", "judge.me", "sales-pop"):
        assert gone.lower() not in layout.lower().split("{%- endcomment -%}", 1)[1], gone


def test_verify_landing_layout_reports_chrome_markup_and_the_transfer(tmp_path):
    pages = {"https://x.test/pages/lp": b'<html><body><div class="lp-top"></div><img src="/a.png"></body></html>',
             "https://x.test/a.png": b"x" * 1000}
    lines = []
    assert theme_lean.verify("https://x.test/pages/lp", fetch=lambda u: pages[u], out=lines.append) == []
    pages["https://x.test/pages/lp"] = b'<div id="shopify-section-announcement-bar"></div><script src="https://cdn.judge.me/w.js"></script>'
    pages["https://cdn.judge.me/w.js"] = b"y" * 900_000
    failures = theme_lean.verify("https://x.test/pages/lp", fetch=lambda u: pages[u], out=lines.append)
    assert any("promo bar" in f for f in failures) and any("review widget" in f for f in failures)
    assert any("transfer" in f for f in failures)
    client = _FakeShopify({})
    failures = theme_lean.verify("https://x.test/pages/lp", fetch=lambda u: pages[u], client=client,
                                 theme_ids=[1], out=lines.append)
    assert any("theme 1 has no layout/landing.liquid" in f for f in failures)


def test_the_lean_flag_sets_the_landing_template_suffix(monkeypatch):
    from harness import cli

    monkeypatch.setitem(TENANT.config, "landing_lean_layout", True)
    pub = cli._lean_suffix(cli._make_publisher(TENANT, export_dir=REPO_ROOT), TENANT, "landing")
    assert pub.template_suffix == "landing"
    other = cli._lean_suffix(cli._make_publisher(TENANT, export_dir=REPO_ROOT), TENANT, "listicle")
    assert other.template_suffix == TENANT.get("shopify_publish.template_suffix")
    monkeypatch.setitem(TENANT.config, "landing_lean_layout", False)
    off = cli._lean_suffix(cli._make_publisher(TENANT, export_dir=REPO_ROOT), TENANT, "landing")
    assert off.template_suffix == TENANT.get("shopify_publish.template_suffix")


# ---------------------------------------------------------------------------
# The canned reasons page passes everything; a fake run in the peak look
# ---------------------------------------------------------------------------

def test_the_canned_page_passes_every_gate_and_the_writer_is_told_the_model():
    assert _gates(_page()) == []
    text = " ".join(landing.writer_lines(_rich(), TENANT, look="peak"))
    assert "review-quote-1" in text and "units-delivered-12mo" in text and "reviews-store-live" in text
    assert "{monthly} = $458.33/mo" in text and "{rating} = 4.6" in text


def test_a_fake_run_in_the_peak_look_exits_zero_and_renders_offline(tmp_path):
    rc, run_dir, _pages = fake_run.run_once(str(TENANT.fixtures_dir / "founder-warranty-demo.txt"),
                                            tenant="peak-saunas", cartridges="landing", look="peak")
    assert rc == 0
    page = json.loads((run_dir / "landing" / "page.json").read_text())
    html = (run_dir / "landing" / "index.html").read_text()
    assert page["look"] == "peak" and page["hero_style"] == "peak_first"
    assert 'data-look-pattern="presell:reasons"' in html


def test_a_proof_ref_is_a_claim_id_field_not_copy():
    """Smoke run 20261015-230453-...-gcmk STOPped four times on
    "claim id leaked into copy: reviews-live in $.reasons[1].proof_ref"."""
    page = _page()
    page["reasons"][1]["proof_ref"] = "reviews-live"
    assert _gates(page) == []


def test_pay_monthly_matches_a_payment_plans_hook():
    """The financing smoke run (20261015-230849-...-nik3) STOPped on attempt 4:
    "Pay monthly for the Fuji instead of all at once" shared no stem with
    "Major Updates: new flexible payment plans" before the suffix strip."""
    brief = dict(LANDING_BRIEF, hook="Major Updates: new flexible payment plans",
                 promise="Skip large upfront costs with flexible financing",
                 angle="Low monthly payment options instead of a full upfront investment.")
    hero = "For anyone putting off a Fuji over cost Pay monthly for the Fuji instead of all at once"
    result = persuasion.hook_match(hero, brief, exclude={"fuji"}, headline="Pay monthly for the Fuji instead of all at once")
    assert result["ok"] and "pay" in result["headline_overlap"]
    assert persuasion.content_words("shopping payments") == {"shopp", "pay"}


def test_a_review_quote_keeps_its_exclamation_mark(tmp_path):
    """Review quotes are verbatim (agent A's rule): the renderer prints them as
    written, and no rendered-text check refuses their "!"."""
    fp = _rich()
    loud = "Love it! Heats up fast and the cedar smells great every single night."
    fp["verified_claims"] = [dict(c, text=loud) if c["id"] == "review-quote-1" else c for c in fp["verified_claims"]]
    fp["review_quotes"][0]["text"] = loud
    html, _ = _render(tmp_path, _page(), fp)
    assert "&ldquo;" + loud + "&rdquo;" in html or loud in _visible(html)
    assert pagechecks.find_placeholder_visible_text(html) == []
