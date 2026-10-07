"""Cycle 82: the landing cartridge (cartridges/landing, harness/landing.py).

What is asserted here, offline:
  - the gate: the canned page passes every page-level gate; it fails for a
    checked claim with no claim_ids, 7 checked claims, a second cta_url or
    any other link field, a price or phone number typed by the writer, a
    room photo that is not a real-room photo, a quote that is not verbatim,
    too few product-benefit claims, power wording that contradicts the
    product's electrical claim, a renderer-owned key;
  - the renderer: price, financing, trust line and spec strip come from
    facts_pack only (a page.json value never reaches the page); the spec
    strip's priority order; the CTA phrase exactly three times; the
    secondary sms: link from tenant.yaml (present iff configured, never a
    CTA class, the phone number nowhere else); FAQPage JSON-LD;
  - the export: one <style> at the top, every rule scoped to the wrapper, no
    absolute internal link, sms:/tel: hrefs untouched, the brand lint;
  - `evals.fake_run ... --cartridges landing` exits 0.
"""
import copy
import json
import re

import pytest

from evals import fake_run
from harness import abtest, claims, landing, pagechecks, repair, write
from harness.page_body import build_shopify_body, relativize_internal_links
from harness.render import render_page
from tests.support import REPO_ROOT, TENANT
from tests.test_brand_lint_cycle78 import _PAGE_SCOPED, _css, _lint, _rules
from tests.test_product_page_looks import _rich_facts_pack
from tests.test_render import AD_BRIEF

CARTRIDGE_DIR = REPO_ROOT / "cartridges" / "landing"
PHONE = "+14706653747"
SRC = "https://peaksaunas.com/products/fuji"


def _facts_pack():
    """The product page suite's rich facts pack plus every claim the canned
    landing page cites, a Bread Pay lender and the spec rows the strip reads."""
    fp = _rich_facts_pack()
    fp["product"]["financing"] = {"available": True, "lender": "Bread Pay", "monthly": None}
    fp["specs"] = [
        {"label": "Capacity", "value": "2-Person", "claim_id": "spec-fuji-capacity"},
        {"label": "Cabin material", "value": "Canadian red cedar", "claim_id": "spec-fuji-cabin-material"},
        {"label": "Max temperature", "value": "150°F", "claim_id": "spec-fuji-max-temperature"},
        {"label": "Infrared wavelength range", "value": "700–25,000nm (near, mid, far)",
         "claim_id": "spec-fuji-infrared-wavelength-range"},
        {"label": "Electrical requirement", "value": "120V / 20A outlet", "claim_id": "spec-fuji-electrical-requirement"},
        {"label": "Audio", "value": "Two Bluetooth speakers", "claim_id": "spec-fuji-audio"},
    ]
    have = {c["id"] for c in fp["verified_claims"]}
    extra = {
        "spec-fuji-infrared-wavelength-range": ("spec", "Fuji -- Infrared wavelength range: 700–25,000nm (near, mid, far)."),
        "spec-fuji-electrical-requirement": ("spec", "Fuji -- Electrical requirement: 120V / 20A outlet."),
        "gbrain-fuji-power": ("spec", "Fuji electrical: 120V/20A, dedicated outlet required — a standard 15A outlet is NOT sufficient."),
        "gbrain-fuji-dimensions": ("spec", 'Fuji dimensions: exterior (assembled) 53"W x 44"D x 75"H.'),
        "gbrain-allowlist-red-light": ("trust", "Medical-grade red light therapy (included standard)."),
        "gbrain-fuji-rlt": ("spec", "Fuji red light therapy: included standard, 1 RLT panel, seated distance 7–8 in from panel."),
        "gbrain-allowlist-360-full-spectrum": ("spec", "360° full spectrum infrared heater placement."),
        "shipping-policy": ("trust", "Peak Saunas offers free shipping on all orders. In-stock items have a 2-4 business day handling time."),
        "returns-policy": ("trust", "Peak Saunas returns require the sauna to be disassembled and repacked in its original crate."),
    }
    for cid, (category, text) in extra.items():
        if cid not in have:
            fp["verified_claims"].append({"id": cid, "text": text, "category": category, "source": SRC})
    return fp


def _page(**changes):
    page = copy.deepcopy(fake_run.LANDING_PAGE)
    page.update(changes)
    return page


def _gates(page, facts_pack=None):
    facts_pack = facts_pack or _facts_pack()
    md = (CARTRIDGE_DIR / "cartridge.md").read_text()
    schema = json.loads(TENANT.render((CARTRIDGE_DIR / "schema.json").read_text()))
    ctas = write.resolve_allowed_cta_texts(schema, "Fuji", model_name="Fuji", tenant=TENANT)
    return repair.check_page_gates(
        page, facts_pack, "landing", financing_lender="Bread Pay", speaker_pov="first_person",
        word_range=write.parse_word_range(md), allowed_cta_texts=ctas, ad_brief=AD_BRIEF, tenant=TENANT,
    )


def _keys(problems):
    return {p.get("key") or p.get("path") for p in problems}


def _render(tmp_path, page=None, facts_pack=None, tenant=TENANT):
    out = tmp_path / "landing"
    render_page(
        cartridge_name="landing", page=copy.deepcopy(page or _page()), ad_brief=AD_BRIEF,
        facts_pack=facts_pack or _facts_pack(), cartridges_dir=REPO_ROOT / "cartridges",
        brand_dir=TENANT.brand_dir, templates_dir=REPO_ROOT / "harness" / "templates", out_dir=out,
        published="2026-10-07", updated="2026-10-07", tenant=tenant, download_assets=False,
    )
    return out


# ---------------------------------------------------------------------------
# Gate
# ---------------------------------------------------------------------------

def test_the_word_range_is_parsed_from_cartridge_md():
    assert write.parse_word_range((CARTRIDGE_DIR / "cartridge.md").read_text()) == (250, 450)


def test_the_canned_page_passes_every_gate():
    assert _gates(_page()) == []


def test_a_checked_claim_with_no_claim_ids_fails():
    page = _page()
    page["checked_claims"][0]["claim_ids"] = []
    assert "landing:checked_claims_ids:0" in _keys(_gates(page))


def test_seven_checked_claims_fail():
    page = _page()
    page["checked_claims"] = (page["checked_claims"] * 2)[:7]
    assert "landing:checked_count" in _keys(landing.find_landing_violations(page, _facts_pack()))


@pytest.mark.parametrize("where", [
    lambda p: p["closing"].__setitem__("cta_url", "/collections/all"),
    lambda p: p["hero"].__setitem__("url", "/pages/contact"),
    lambda p: p["checked_claims"][0].__setitem__("button", "Book a call"),
    lambda p: p["faq"]["questions"][0].__setitem__("cta_text", "Text us"),
])
def test_a_page_can_never_carry_a_second_cta(where):
    page = _page()
    where(page)
    keys = _keys(landing.find_landing_violations(page, _facts_pack()))
    assert any(k.startswith("landing:second_cta:") for k in keys)
    assert _gates(page)


def test_the_one_cta_gate_passes_the_top_level_cta():
    assert not [p for p in landing.find_one_cta_violations(_page()) if p]


def test_a_price_typed_into_the_first_screen_fails():
    page = _page()
    page["hero"]["subhead"] = "All of it for $8,250, delivered to your door."
    assert "landing:number:$.hero.subhead" in _keys(_gates(page))


def test_a_phone_number_anywhere_fails():
    page = _page()
    page["checked_claims"][4]["fact"] = "Text us at +1 (470) 665-3747 with any question."
    assert any(k.startswith("landing:phone:") for k in _keys(_gates(page)))


def test_the_room_photo_must_be_a_real_room_photo():
    fp = _facts_pack()
    lifestyle = "asset-drive-lifestyle-1"
    render_id = fp["assets"][0]["id"]
    assert landing.find_room_photo_violations(_page(room_photo={"asset_id": lifestyle}), fp) == []
    assert landing.find_room_photo_violations(_page(room_photo={"asset_id": render_id}), fp)
    assert landing.find_room_photo_violations(_page(), fp) == []      # omitted: fine


def test_a_quote_must_be_verbatim_in_the_reviews_summary():
    fp = _facts_pack()
    good = {"quote": {"text": "Rated 4.79 out of 5 across 47 reviews", "author": "Judge.me", "claim_ids": ["reviews-live"]}}
    bad = {"quote": {"text": "Best sauna I ever owned", "author": "A buyer", "claim_ids": ["reviews-live"]}}
    assert landing.find_quote_violations(_page(proof=good), fp) == []
    assert _keys(landing.find_quote_violations(_page(proof=bad), fp)) == {"landing:quote_verbatim"}


def test_fewer_than_three_benefit_claims_fail():
    page = _page()
    for item in page["checked_claims"]:
        item["claim_ids"] = ["shipping-policy"]
    problems = claims.find_benefit_claim_shortfall(page, _facts_pack(), "landing")
    assert problems and problems[0]["path"] == "$.landing-benefit-claims"


def test_power_wording_must_match_the_electrical_claim():
    page = _page()
    page["checked_claims"][1]["fact"] = "It plugs into any standard outlet, no electrician needed."
    assert any(k.startswith("landing:power:") for k in _keys(landing.find_landing_violations(page, _facts_pack())))


def test_a_renderer_owned_key_fails():
    page = _page(price_line={"text": "$1", "claim_ids": ["price-fuji"]})
    assert "landing:renderer_owned:$.price_line" in _keys(landing.find_landing_violations(page, _facts_pack()))


def test_the_writer_lines_ask_for_logistics_and_a_real_room_photo():
    text = " ".join(landing.writer_lines(_facts_pack(), TENANT))
    for word in ("electrical requirement", "footprint", "delivery", "assembly", "warranty"):
        assert word in text
    assert "asset-drive-lifestyle-1" in text            # the one real-room candidate
    no_room = _facts_pack()
    no_room["assets"] = [a for a in no_room["assets"] if a["kind"] != "lifestyle"]
    assert "omit room_photo" in " ".join(landing.writer_lines(no_room, TENANT))


# ---------------------------------------------------------------------------
# Renderer
# ---------------------------------------------------------------------------

def test_price_financing_trust_and_specs_come_from_facts_pack(tmp_path):
    html = (_render(tmp_path) / "index.html").read_text()
    assert "<b>$8,250</b>" in html
    assert "Financing is available through Bread Pay at checkout." in html
    assert "<li>Free shipping</li>" in html
    assert "4.79 ★ · 47 reviews" in html
    assert "Limited lifetime warranty</li>" in html
    strip = re.findall(r'<li class="ld-spec"><b>([^<]*)</b><span>([^<]*)</span>', html)
    assert [label for _v, label in strip] == ["Capacity", "Max temperature", "Infrared wavelength range", "Cabin material"]


def test_a_page_json_price_never_reaches_the_page(tmp_path):
    html = (_render(tmp_path, page=_page(price={"text": "$1"}, price_line={"text": "$1"})) / "index.html").read_text()
    assert "$1<" not in html and "<b>$8,250</b>" in html


def test_no_price_claim_means_no_price(tmp_path):
    fp = _facts_pack()
    fp["verified_claims"] = [c for c in fp["verified_claims"] if c["id"] != "price-fuji"]
    assert landing.price(fp) is None


def test_the_cta_phrase_appears_three_times(tmp_path):
    html = (_render(tmp_path) / "index.html").read_text()
    assert html.count(">Shop the Fuji<") == 3
    assert html.count('class="ld-btn"') == 3


def test_the_secondary_link_is_an_sms_link_from_tenant_config(tmp_path):
    html = (_render(tmp_path) / "index.html").read_text()
    hrefs = re.findall(r'<a class="ld-text" href="([^"]*)">([^<]*)</a>', html)
    assert hrefs == [(f"sms:{PHONE}?&amp;body=Hi%2C%20I%20have%20a%20question%20about%20the%20Peak%20Fuji.", "Text us")]
    # the number appears once: in that href, nowhere else on the page
    assert html.count("4706653747") == 1


def test_the_secondary_link_is_never_a_cta_class():
    assert "ld-text" not in abtest.CTA_CLASSES
    assert "ld-btn" in abtest.CTA_CLASSES


class _Tenant:
    def __init__(self, cfg):
        self.cfg = cfg

    def get(self, key, default=None):
        return self.cfg if key == "cartridges.landing.secondary_cta" else default

    def product_names(self, product):
        return {"full_name": "Peak Fuji", "short_name": "Fuji", "descriptor": ""}


def test_the_secondary_link_forms():
    fp = _facts_pack()
    assert landing.secondary_link(_Tenant(None), fp) is None
    assert landing.secondary_link(_Tenant({"text": "", "sms": PHONE}), fp) is None
    assert landing.secondary_link(_Tenant({"text": "Talk to us", "url": "/pages/contact", "sms": PHONE}), fp) == {
        "text": "Talk to us", "href": "/pages/contact"}
    assert landing.secondary_link(_Tenant({"text": "Text us", "sms": PHONE}), fp) == {"text": "Text us", "href": f"sms:{PHONE}"}


def test_no_secondary_link_without_tenant_config(tmp_path):
    # a shallow copy: patching the shared TENANT would leak into other tests
    tenant = copy.copy(TENANT)
    real_get = TENANT.get
    tenant.get = lambda key, default=None: None if key == "cartridges.landing.secondary_cta" else real_get(key, default)
    html = (_render(tmp_path, tenant=tenant) / "index.html").read_text()
    assert 'class="ld-text"' not in html and "sms:" not in html


def test_faqpage_json_ld_and_the_rendered_page_checks(tmp_path):
    html = (_render(tmp_path) / "index.html").read_text()
    ld = json.loads(re.search(r'<script type="application/ld\+json">(.*?)</script>', html, re.S).group(1))
    assert ld["@type"] == "FAQPage" and len(ld["mainEntity"]) == len(fake_run.LANDING_PAGE["faq"]["questions"])
    assert pagechecks.find_html_validity_violations(html) == []
    assert pagechecks.find_rendered_json_ld_violations(html, "landing") == []
    assert pagechecks.find_rendered_internal_link_violations(html, tenant=TENANT) == []


def test_at_most_six_images(tmp_path):
    html = (_render(tmp_path, page=_page(room_photo={"asset_id": "asset-drive-lifestyle-1"})) / "index.html").read_text()
    assert len(re.findall(r"<img\b", html)) <= 6
    assert 'class="ld-room"' in html


def test_a_render_in_the_room_slot_is_not_shown(tmp_path):
    fp = _facts_pack()
    html = (_render(tmp_path, page=_page(room_photo={"asset_id": fp["assets"][1]["id"]}), facts_pack=fp) / "index.html").read_text()
    assert 'class="ld-room"' not in html


# ---------------------------------------------------------------------------
# Export and brand
# ---------------------------------------------------------------------------

def test_the_storefront_export_keeps_its_invariants(tmp_path):
    out = _render(tmp_path)
    export, _manifest = build_shopify_body(out)
    assert export.startswith("<style>\n")
    assert export.count("</style>") == 1
    assert "peaksaunas.com" not in export.split("</style>", 1)[1].replace(SRC, "")
    assert not re.search(r'href="https?://(?:www\.)?peaksaunas\.com', export)
    unscoped = [sel for sel, _b in _rules(_css(export)) if not sel.startswith(".adv-wrap") and not _PAGE_SCOPED.search(sel)]
    assert unscoped == []
    assert f'href="sms:{PHONE}?&amp;body=' in export
    assert export.count('class="pk-lp') == 1


def test_relativize_never_touches_sms_or_tel_links():
    html = f'<a href="sms:{PHONE}?&amp;body=Hi">x</a><a href="tel:{PHONE}">y</a><a href="https://peaksaunas.com/pages/contact">z</a>'
    out = relativize_internal_links(html, tenant=TENANT)
    assert f'href="sms:{PHONE}?&amp;body=Hi"' in out and f'href="tel:{PHONE}"' in out
    assert 'href="/pages/contact"' in out


def test_the_review_page_and_export_pass_the_brand_lint(tmp_path):
    out = _render(tmp_path)
    html = (out / "index.html").read_text()
    export, _ = build_shopify_body(out)
    assert _lint(html) == [] and _lint(export) == []
    assert len(re.findall(r"<h1[\s>]", html)) == 1


def test_the_look_has_no_colour_literal():
    # cycle 85: the open look's template moved under looks/open/
    css = re.search(r"<style>(.*?)</style>", (CARTRIDGE_DIR / "looks" / "open" / "template.html").read_text(), re.S).group(1)
    assert not re.search(r"#[0-9a-fA-F]{3,8}\b|rgba?\(", css)


# ---------------------------------------------------------------------------
# Offline run
# ---------------------------------------------------------------------------

def test_fake_run_renders_the_landing_page():
    fixture = TENANT.fixtures_dir / "founder-warranty-demo.txt"
    exit_code, run_dir, pages = fake_run.run_once(str(fixture), tenant="peak-saunas", cartridges="landing")
    assert exit_code == 0
    html = (run_dir / "landing" / "index.html").read_text()
    assert 'class="pk-lp adv-landing"' in html and "<b>$8,250</b>" in html
    assert json.loads(pages[0].read_text())["cta_text"] == "Shop the Fuji"


# ---------------------------------------------------------------------------
# Cycle 82b: the gate fixes from the first five prod runs
# ---------------------------------------------------------------------------

def _words(n, end="."):
    return " ".join(["word"] * (n - 1) + ["last" + end])


@pytest.mark.parametrize("field,limit,key", [
    (("hero", "subhead"), 30, "landing:subhead_words"),
    (("hero", "eyebrow"), 8, "landing:eyebrow_words"),
])
def test_the_first_screen_word_limits(field, limit, key):
    ok, over = _page(), _page()
    ok[field[0]][field[1]] = _words(limit)
    over[field[0]][field[1]] = _words(limit + 1)
    assert key not in _keys(landing.find_landing_violations(ok, _facts_pack()))
    assert key in _keys(landing.find_landing_violations(over, _facts_pack()))


def test_the_checked_claim_word_limits():
    page = _page()
    page["checked_claims"][0]["fact"] = _words(36)
    page["checked_claims"][1]["fact"] = _words(37)
    page["checked_claims"][2]["ad_said"] = _words(19)
    page["checked_claims"][3]["ad_said"] = _words(18)
    keys = _keys(landing.find_landing_violations(page, _facts_pack()))
    assert "landing:fact_words:0" not in keys
    assert "landing:fact_words:1" in keys and "landing:ad_said_words:2" in keys
    assert "landing:ad_said_words:3" not in keys


def test_the_accent_phrase_matches_case_and_punctuation_insensitively(tmp_path):
    page = _page()
    page["hero"]["accent_phrase"] = "\u201cNothing  Hidden.\u201d"
    assert _gates(page) == []
    html = (_render(tmp_path, page=page) / "index.html").read_text()
    assert '<span class="ld-accent">nothing hidden</span>' in html


def test_an_accent_phrase_not_in_the_headline_renders_no_accent_and_passes(tmp_path):
    page = _page()
    page["hero"]["accent_phrase"] = "see the full price"
    assert _gates(page) == []
    ctx = landing.render_context(page, _facts_pack(), ad_brief=AD_BRIEF, tenant=TENANT)
    assert "not in the headline" in ctx["accent_note"]
    html = (_render(tmp_path, page=page) / "index.html").read_text()
    assert 'class="ld-accent"' not in html


def test_ad_said_may_quote_the_ads_outlet_claim():
    page = _page()
    page["checked_claims"][1]["ad_said"] = "It plugs right into a normal outlet, no electrician involved"
    assert not [p for p in landing.find_landing_violations(page, _facts_pack()) if "power" in p["key"]]


def test_the_hero_may_not_repeat_a_contradicted_outlet_claim_and_the_repair_says_where_it_goes():
    page = _page()
    page["hero"]["headline"] = "A small-footprint sauna that plugs into a standard outlet"
    hits = [p for p in landing.find_landing_violations(page, _facts_pack()) if "power" in p["key"]]
    assert [p["path"] for p in hits] == ["$.hero.headline"]
    assert "move it into checked_claims[].ad_said" in hits[0]["issue"]


def test_the_writer_prompt_carries_the_contradicted_claim_rule():
    assert landing.CONTRADICTED_AD_CLAIM_LINE in landing.writer_lines(_facts_pack(), TENANT)


def test_trim_to_limit_cuts_only_at_a_sentence_or_clause_boundary():
    text = ("A home sauna is easier to stick with once the weather turns -- a warm session in your own "
            "house instead of a cold drive out.")
    assert landing.trim_to_limit(text, 20) == "A home sauna is easier to stick with once the weather turns."
    two = "One two three four. Five six seven eight nine."
    assert landing.trim_to_limit(two, 6) == "One two three four."
    assert landing.trim_to_limit("one two three four five six", 3) is None      # no terminal punctuation
    assert landing.trim_to_limit("one two three four five six.", 3) is None     # no boundary that fits
    assert landing.trim_to_limit(two, 20) == two


def _write_loop(page, tmp_path):
    from harness.budget import Budget
    from harness.log import RunLog
    from harness.repair import write_and_gate_page
    from tests.conftest import FakeClient, json_response

    client = FakeClient([json_response(page)] * 3)
    log = RunLog("test-run", tmp_path / "run.log")
    try:
        return write_and_gate_page(
            cartridge_name="landing", cartridges_dir=REPO_ROOT / "cartridges", ad_brief=AD_BRIEF,
            facts_pack=_facts_pack(), client=client, model="claude-sonnet-5", budget=Budget(), log=log,
            financing_lender="Bread Pay", speaker_pov=AD_BRIEF["speaker_pov"], tenant=TENANT,
        ), client
    finally:
        log.close()


def test_the_last_attempt_trims_an_over_long_subhead(tmp_path):
    page = _page()
    page["hero"]["subhead"] = ("The price, the power it needs and what arrives at your door, stated up front. "
                               "So you can decide on your own time, without a sales call or a showroom visit, "
                               "and without a single form.")
    assert len(page["hero"]["subhead"].split()) > landing.SUBHEAD_MAX_WORDS
    (result, attempts, fixes), client = _write_loop(page, tmp_path)
    assert len(client.messages.calls) == 3 and fixes[-1] == 1 and fixes[0] == 0
    assert result["hero"]["subhead"] == "The price, the power it needs and what arrives at your door, stated up front."


def test_an_untrimmable_subhead_still_stops_the_run(tmp_path):
    page = _page()
    page["hero"]["subhead"] = " ".join(["plain"] * 31)
    with pytest.raises(claims.ClaimsGateFailure):
        _write_loop(page, tmp_path)


# ---------------------------------------------------------------------------
# Cycle 82c: the power rule states what the claim states; ad_said is a quote
# ---------------------------------------------------------------------------

from harness import pdp  # noqa: E402

MINI_CLAIMS = [
    {"id": "spec-mini-electrical", "category": "spec", "source": SRC,
     "text": "Mini electrical: 120V/15A, standard outlet -- no electrician needed. Wattage ~1500W."},
    {"id": "pdp-mini-electrical", "category": "spec", "source": SRC,
     "text": "Plugs into a standard 120V/15A household outlet (NEMA 5-15P) \u2014 no electrician; use a dedicated 15A circuit."},
]
FUJI_POWER = {"id": "gbrain-fuji-power", "category": "spec", "source": SRC,
              "text": "Fuji electrical: 120V/20A, dedicated outlet required \u2014 a standard 15A outlet is NOT sufficient. Wattage ~2050W."}
MINI_SENTENCES = (
    "The Mini plugs into a standard 120V/15A household outlet, but the maker specifies a dedicated 15A circuit for that outlet.",
    "The Mini plugs into a standard 120V/15A household outlet, and the maker specifies that outlet be on a dedicated 15A circuit for the sauna.",
    "It plugs into a standard 120V/15A household outlet (NEMA 5-15P) on a dedicated 15A circuit, per the product's own electrical specification.",
)


def _power_fp(name, claims_):
    return {"product": {"name": name}, "specs": [], "verified_claims": list(claims_)}


def _power_page(*texts):
    return {"faq": {"questions": [{"question": "Power?", "answer": t} for t in texts]}}


def test_the_minis_own_wording_passes_the_power_rule():
    mini = _facts_pack()
    mini["product"]["name"] = "Mini"
    mini["verified_claims"] += MINI_CLAIMS
    page = _page()
    page["checked_claims"][1]["fact"] = MINI_SENTENCES[0]
    page["checked_claims"][1]["claim_ids"] = ["pdp-mini-electrical"]
    page["faq"]["questions"][0]["answer"] = MINI_SENTENCES[1]
    page["faq"]["questions"][1]["answer"] = MINI_SENTENCES[2]
    assert [p for p in landing.find_landing_violations(page, mini) if ":power:" in p["key"]] == []
    assert pdp.find_power_violations(_power_page(*MINI_SENTENCES), _power_fp("Mini", MINI_CLAIMS)) == []


def test_no_electrician_alone_passes_when_the_claim_says_so_but_a_bare_normal_outlet_does_not():
    fp = _power_fp("Mini", MINI_CLAIMS)
    assert pdp.find_power_violations(_power_page("No electrician is needed to set it up."), fp) == []
    assert pdp.find_power_violations(_power_page("It plugs into any normal outlet."), fp)


def test_a_dedicated_circuit_product_still_fails_the_easy_install_line():
    fp = _power_fp("Fuji", [FUJI_POWER])
    assert pdp.find_power_violations(_power_page("It plugs into any normal outlet, no electrician needed."), fp)
    assert pdp.find_power_violations(_power_page("No electrician needed."), fp)       # the Fuji claim does not say so
    assert pdp.find_power_violations(_power_page(
        "It plugs into a standard outlet with no dedicated circuit needed."), fp)
    assert pdp.find_power_violations(_power_page(
        "The Fuji runs on a 120V / 20A dedicated outlet; a standard 15A outlet is not sufficient."), fp) == []


def test_states_dedicated_ignores_a_negated_dedicated_circuit():
    assert pdp.states_dedicated("use a dedicated 15A circuit")
    assert pdp.states_dedicated("a 20A dedicated outlet")
    assert not pdp.states_dedicated("no dedicated circuit needed")
    assert not pdp.states_dedicated("you don't need a dedicated circuit")


def test_the_last_attempt_may_trim_ad_said():
    page = _page()
    page["checked_claims"][0]["ad_said"] = ("I can start it from an app on my phone. "
                                            "It has Bluetooth for music and podcasts while I sit in there.")
    item = {"key": "landing:ad_said_words:0", "path": "$.checked_claims[0].ad_said"}
    assert landing.trim_word_limit_violation(page, item)[2] == "I can start it from an app on my phone."


def test_the_warranty_fix_never_rewrites_ad_said():
    from harness import repair as repair_mod

    page = _page()
    page["checked_claims"][1]["ad_said"] = "Every sauna comes with a lifetime warranty."
    problems = [p for p in _gates(page) if p.get("path") == "$.checked_claims[1].ad_said"]
    assert problems and any("warranty wording must be exactly" in p["issue"] for p in problems)
    valid = {c["id"] for c in _facts_pack()["verified_claims"]}
    repair_mod.apply_deterministic_fixes(page, problems, valid, cartridge_name="landing", facts_pack=_facts_pack(),
                                         financing_lender="Bread Pay", tenant=TENANT)
    assert page["checked_claims"][1]["ad_said"] == "Every sauna comes with a lifetime warranty."


@pytest.mark.parametrize("said,bad", [
    ("Fall Sale: $4,025+ off every model, extra $250 with code CRISP250", True),
    ("Save 20% this week", True),
    ("Use code SAUNAS at checkout", True),
    ("It runs on 120V", True),
    ("A fall sale with money off every model", False),
    ("It plugs right into a normal outlet, no electrician involved", False),
])
def test_ad_said_carries_no_figure_or_code(said, bad):
    page = _page()
    page["checked_claims"][0]["ad_said"] = said
    hits = [p for p in landing.find_landing_violations(page, _facts_pack()) if p["key"] == "landing:ad_said_figures:0"]
    assert bool(hits) is bad
    if bad:
        assert "paraphrase a price or promo claim without figures" in hits[0]["issue"]


def test_digits_stay_allowed_in_a_cited_fact():
    assert "landing:ad_said_figures:2" not in _keys(landing.find_landing_violations(_page(), _facts_pack()))
    assert any(ch.isdigit() for ch in _page()["checked_claims"][2]["fact"])


def test_the_writer_prompt_says_to_paraphrase_promos():
    assert landing.AD_SAID_FIGURES_LINE in landing.writer_lines(_facts_pack(), TENANT)
    assert "money off every model" in landing.AD_SAID_FIGURES_LINE
