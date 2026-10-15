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
from harness.vocab import ALLOWED_WARRANTY_SENTENCE
from harness.vocab import ALLOWED_WARRANTY_SENTENCE
from tests.support import REPO_ROOT, TENANT
from tests.test_brand_lint_cycle78 import _PAGE_SCOPED, _css, _lint, _rules
from tests.test_product_page_looks import _rich_facts_pack
from tests.test_render import AD_BRIEF

CARTRIDGE_DIR = REPO_ROOT / "cartridges" / "landing"
PHONE = "+14706653747"
# The trust line shows the first clause of the tenant's fixed warranty sentence.
WARRANTY_TERM = ALLOWED_WARRANTY_SENTENCE.split(";")[0]
SRC = "https://peaksaunas.com/products/fuji"
# Cycle 110: the founder fixture's own brief -- the canned page restates its
# hook (landing:hook_match); the suite's AD_BRIEF ("hook") matches nothing.
LANDING_BRIEF = fake_run._brief_for(str(TENANT.fixtures_dir / "founder-warranty-demo.txt"))


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
    ctas = landing.allowed_cta_texts(schema, facts_pack, LANDING_BRIEF, TENANT)
    return repair.check_page_gates(
        page, facts_pack, "landing", financing_lender="Bread Pay", speaker_pov="first_person",
        word_range=write.parse_word_range(md), allowed_cta_texts=ctas, ad_brief=LANDING_BRIEF, tenant=TENANT,
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


def test_a_reason_with_no_claim_ids_fails():
    page = _page()
    page["reasons"][0]["claim_ids"] = []
    assert "landing:reason_ids:0" in _keys(_gates(page))


def test_six_reasons_fail_and_a_page_with_checked_claims_fails():
    page = _page()
    page["reasons"] = (page["reasons"] * 2)[:6]
    assert "landing:reasons_count" in _keys(landing.find_landing_violations(page, _facts_pack()))
    legacy = _page(checked_claims=[{"ad_said": "x", "fact": "y", "claim_ids": ["spec-fuji-capacity"]}])
    assert "landing:checked_claims_retired" in _keys(landing.find_landing_violations(legacy, _facts_pack()))


@pytest.mark.parametrize("where", [
    lambda p: p["closing"].__setitem__("cta_url", "/collections/all"),
    lambda p: p["hero"].__setitem__("url", "/pages/contact"),
    lambda p: p["reasons"][0].__setitem__("button", "Book a call"),
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
    page["reasons"][1]["body"] = "Text us at +1 (470) 665-3747 with any question."
    assert any(k.startswith("landing:phone:") for k in _keys(_gates(page)))


def test_the_room_photo_must_be_a_real_room_photo():
    fp = _facts_pack()
    lifestyle = "asset-drive-lifestyle-1"
    render_id = fp["assets"][0]["id"]
    assert landing.find_room_photo_violations(_page(room_photo={"asset_id": lifestyle}), fp) == []
    assert landing.find_room_photo_violations(_page(room_photo={"asset_id": render_id}), fp)
    assert landing.find_room_photo_violations(_page(), fp) == []      # omitted: fine


def test_a_quote_must_be_verbatim_in_one_of_the_review_quotes():
    fp = _facts_pack()
    fp["review_quotes"] = [{"text": "Best sauna I ever owned. It heats fast.", "author": "Dana R."}]
    good = {"quote": {"text": "Best sauna I ever owned", "author": "Dana R.", "claim_ids": ["reviews-live"]}}
    bad = {"quote": {"text": "Best sauna in the world", "author": "A buyer", "claim_ids": ["reviews-live"]}}
    assert landing.find_quote_violations(_page(proof=good), fp) == []
    assert _keys(landing.find_quote_violations(_page(proof=bad), fp)) == {"landing:quote_verbatim"}


def test_fewer_than_three_benefit_claims_fail():
    page = _page()
    for item in page["reasons"]:
        item["claim_ids"] = ["shipping-policy"]
    problems = claims.find_benefit_claim_shortfall(page, _facts_pack(), "landing")
    assert problems and problems[0]["path"] == "$.landing-benefit-claims"


def test_power_wording_must_match_the_electrical_claim():
    page = _page()
    page["reasons"][3]["body"] = "It plugs into any standard outlet, no electrician needed."
    assert any(k.startswith("landing:power:") for k in _keys(landing.find_landing_violations(page, _facts_pack())))


def test_a_renderer_owned_key_fails():
    page = _page(price_line={"text": "$1", "claim_ids": ["price-fuji"]})
    assert "landing:renderer_owned:$.price_line" in _keys(landing.find_landing_violations(page, _facts_pack()))


def test_the_writer_lines_ask_for_reasons_the_hook_and_the_buyers_faq():
    text = " ".join(landing.writer_lines(_facts_pack(), TENANT))
    for words in ("reasons has 3-5 items", "outcome", "likelihood", "time", "effort", "restates ad_brief.hook",
                  "Grade 5-7 reading level", "Never quote, paraphrase, grade or correct the ad",
                  "At most one logistics question", "Never a question about returns",
                  "room_photo, checked_claims and ad_said are retired"):
        assert words in text, words
    assert "checked_claims has" not in text and "ad_said is what" not in text


# ---------------------------------------------------------------------------
# Renderer
# ---------------------------------------------------------------------------

def test_price_financing_trust_and_specs_come_from_facts_pack(tmp_path):
    html = (_render(tmp_path) / "index.html").read_text()
    assert "<b>$8,250</b>" in html
    assert "Financing is available through Bread Pay at checkout." in html
    assert "<li>Free shipping</li>" in html
    assert "4.79 ★ · 47 reviews" in html
    assert f"{WARRANTY_TERM}</li>" in html
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
    # cycle 97: the sticky bar's button reads "Shop now" beside the model name;
    # cycle 110: hero, after the reasons, closing -- every one tagged for the beacon
    assert html.count(">Shop the Fuji<") == 3 and html.count(">Shop now<") == 1
    assert html.count('class="ld-btn"') == 4
    assert re.findall(r'class="ld-btn" data-el="(\w+)"', html) == ["hero", "mid", "closing", "sticky"]
    assert '<p class="ld-bar-name">Peak Fuji<span>$8,250</span></p>' in html


def test_the_secondary_link_is_an_sms_link_from_tenant_config(tmp_path):
    html = (_render(tmp_path) / "index.html").read_text()
    # cycle 110: in the closing band (data-el="sms"), never beside the hero's pill
    hrefs = re.findall(r'<a class="ld-text" data-el="sms" href="([^"]*)"[^>]*>([^<]*)</a>', html)
    assert html.index('class="ld-close"') < html.index('class="ld-text"')
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


def test_the_reason_word_limits():
    page = _page()
    page["reasons"][0]["body"] = _words(45)
    page["reasons"][1]["body"] = _words(46)
    page["reasons"][2]["headline"] = _words(11, "")
    page["reasons"][3]["headline"] = _words(10, "")
    keys = _keys(landing.find_landing_violations(page, _facts_pack()))
    assert "landing:reason_body_words:0" not in keys
    assert "landing:reason_body_words:1" in keys and "landing:reason_headline_words:2" in keys
    assert "landing:reason_headline_words:3" not in keys


def test_the_accent_phrase_matches_case_and_punctuation_insensitively(tmp_path):
    page = _page()
    page["hero"]["accent_phrase"] = "\u201cThis  Confusing.\u201d"
    assert _gates(page) == []
    html = (_render(tmp_path, page=page) / "index.html").read_text()
    assert '<span class="ld-accent">this confusing</span>' in html


def test_an_accent_phrase_not_in_the_headline_renders_no_accent_and_passes(tmp_path):
    page = _page()
    page["hero"]["accent_phrase"] = "see the full price"
    assert _gates(page) == []
    ctx = landing.render_context(page, _facts_pack(), ad_brief=AD_BRIEF, tenant=TENANT)
    assert "not in the headline" in ctx["accent_note"]
    html = (_render(tmp_path, page=page) / "index.html").read_text()
    assert 'class="ld-accent"' not in html


def test_the_hero_may_not_repeat_a_contradicted_outlet_claim_and_the_repair_says_where_it_goes():
    page = _page()
    page["hero"]["headline"] = "A small-footprint sauna that plugs into a standard outlet"
    hits = [p for p in landing.find_landing_violations(page, _facts_pack()) if "power" in p["key"]]
    assert [p["path"] for p in hits] == ["$.hero.headline"]
    assert "never repeats an ad claim the verified facts contradict" in hits[0]["issue"]


def test_the_writer_prompt_never_lets_the_page_argue_with_the_ad():
    text = " ".join(landing.writer_lines(_facts_pack(), TENANT))
    assert "An ad claim the facts do not support is simply left out" in text


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

    client = FakeClient([json_response(page)] * (repair.max_repair_attempts("landing") + 1))
    log = RunLog("test-run", tmp_path / "run.log")
    try:
        return write_and_gate_page(
            cartridge_name="landing", cartridges_dir=REPO_ROOT / "cartridges", ad_brief=LANDING_BRIEF,
            facts_pack=_facts_pack(), client=client, model="claude-sonnet-5", budget=Budget(), log=log,
            financing_lender="Bread Pay", speaker_pov=LANDING_BRIEF["speaker_pov"], tenant=TENANT,
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
    assert len(client.messages.calls) == 4 and fixes[-1] == 1 and fixes[:-1] == [0, 0, 0]
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
    page["reasons"][3]["body"] = MINI_SENTENCES[0]
    page["reasons"][3]["claim_ids"] = ["pdp-mini-electrical"]
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


def test_digits_stay_allowed_in_a_cited_reason():
    assert _gates(_page()) == []
    assert any(ch.isdigit() for ch in _page()["reasons"][3]["body"])


# ---------------------------------------------------------------------------
# Cycle 82d: the fixed-wording gates (cycle 110: ad_said is retired)
# ---------------------------------------------------------------------------

_LIFETIME = "Backed by a lifetime warranty"
_FINANCING = "Easy financing available at checkout"


def _wording(problems):
    return [p for p in problems if "wording must be exactly" in p["issue"] or "financing_line must be" in p["issue"]]


@pytest.mark.parametrize("said,word", [(_LIFETIME, "warranty"), (_FINANCING, "financing")])
@pytest.mark.parametrize("path", ["reason", "faq"])
def test_the_same_words_in_a_reason_or_faq_still_fail(said, word, path):
    page = _page()
    if path == "reason":
        page["reasons"][1]["body"] = said + "."
        where = "$.reasons[1].body"
    else:
        page["faq"]["questions"][1]["answer"] = said + "."
        where = "$.faq.questions[1].answer"
    hits = [p for p in _wording(_gates(page)) if p["path"] == where]
    assert hits and f"{word} wording must be exactly" in hits[0]["issue"]


def test_a_wording_failure_carries_a_key():
    page = _page()
    page["reasons"][1]["body"] = _LIFETIME + "."
    page["faq"]["questions"][1]["answer"] = _FINANCING + "."
    keys = {p["path"]: p.get("key") for p in _wording(_gates(page))}
    assert keys["$.reasons[1].body"] == "warranty:$.reasons[1].body"
    assert keys["$.faq.questions[1].answer"] == "financing:$.faq.questions[1].answer"
    line = claims.find_financing_violations({"offer": {"financing_line": "Pay later."}}, financing_lender="Bread Pay")
    assert line[0]["key"] == "financing_line:$.offer.financing_line"


# ---------------------------------------------------------------------------
# Cycle 82e: other words for "dedicated circuit"; the repair quotes the
# claim; landing gets 4 attempts; proof.quote is a review, not the rating
# ---------------------------------------------------------------------------

PROD_POWER_ANSWER = ("No new wiring is required beyond a dedicated outlet. The Peak Mini plugs into a standard "
                     "120V/15A household outlet on its own 15A circuit.")


def test_the_prod_mini_answer_passes_the_power_rule():
    mini = _facts_pack()
    mini["product"]["name"] = "Mini"
    mini["verified_claims"] += MINI_CLAIMS
    page = _page()
    page["faq"]["questions"][1]["answer"] = PROD_POWER_ANSWER
    assert pdp.find_power_violations(_power_page(PROD_POWER_ANSWER), _power_fp("Mini", MINI_CLAIMS)) == []
    assert [p for p in landing.find_landing_violations(page, mini) if ":power:" in p["key"]] == []


@pytest.mark.parametrize("words", ["on its own circuit", "on a separate 20A line", "on a dedicated 15A",
                                   "on its own 20-amp breaker", "on a single-purpose circuit",
                                   "into its own outlet", "on a dedicated breaker"])
def test_other_words_for_a_dedicated_circuit_pass(words):
    sentence = f"It plugs into a standard 120V/15A household outlet {words}."
    assert pdp.states_dedicated(sentence)
    assert pdp.find_power_violations(_power_page(sentence), _power_fp("Mini", MINI_CLAIMS)) == []


@pytest.mark.parametrize("sentence", [
    "It plugs into a standard household outlet, not on its own circuit.",
    "It plugs into a standard household outlet without a separate circuit.",
    "It plugs into a standard household outlet with no dedicated line.",
])
def test_a_negated_dedicated_statement_still_fails(sentence):
    assert not pdp.states_dedicated(sentence)
    assert pdp.find_power_violations(_power_page(sentence), _power_fp("Mini", MINI_CLAIMS))


def test_the_power_repair_quotes_the_claim_that_states_the_dedicated_circuit():
    mini = _facts_pack()
    mini["product"]["name"] = "Mini"
    mini["verified_claims"] += MINI_CLAIMS
    page = _page()
    page["faq"]["questions"][1]["answer"] = "It plugs into any standard household outlet."
    hits = [p for p in landing.find_landing_violations(page, mini) if ":power:" in p["key"]]
    assert hits and hits[0]["issue"].endswith(
        "Say 'dedicated circuit' in the same sentence, for example: '" + MINI_CLAIMS[1]["text"] + "'")


def test_landing_gets_four_attempts_and_other_cartridges_keep_three(tmp_path):
    assert repair.max_repair_attempts("landing") == 3
    assert repair.max_repair_attempts("listicle") == repair.max_repair_attempts("article") == 2
    page = _page()
    page["hero"]["subhead"] = " ".join(["plain"] * 31)
    with pytest.raises(claims.ClaimsGateFailure) as exc_info:
        _write_loop(page, tmp_path)
    assert len(exc_info.value.attempts) == 4
    assert "repair attempt 3 of 3" in repair.build_revision_note(3, [], 3)


PROD_RATING = "Rated 4.8 out of 5 across 49 reviews on Judge.me (fetched 2026-10-07)."


@pytest.mark.parametrize("text", [PROD_RATING, "Rated 4.79 out of 5 across 47 reviews", "4.8 stars from 49 buyers"])
def test_the_rating_sentence_is_never_a_quote(text):
    fp = _facts_pack()
    fp["review_quotes"] = [{"text": PROD_RATING, "author": "Judge.me reviews"}]   # even when listed there
    proof = {"quote": {"text": text, "author": "Judge.me reviews", "claim_ids": ["reviews-live"]}}
    assert _keys(landing.find_quote_violations(_page(proof=proof), fp)) == {"landing:quote_not_review"}
    assert "landing:quote_not_review" in _keys(_gates(_page(proof=proof), fp))


def test_the_open_look_drops_the_rating_sentence_quote_and_keeps_the_rating_row(tmp_path):
    rating = _facts_pack()["reviews_summary"]["text"]
    proof = {"quote": {"text": rating, "author": "Judge.me reviews", "claim_ids": ["reviews-live"]}}
    html = (_render(tmp_path, page=_page(proof=proof)) / "index.html").read_text()
    assert 'class="ld-quote"' not in html and "Judge.me reviews" not in html
    assert 'class="ld-rating"' in html


# ---------------------------------------------------------------------------
# Cycle 82f: an unverifiable quote is dropped, not repaired; a repair that
# drops most of the page is discarded (prod run 20261007-201051-...-tks7)
# ---------------------------------------------------------------------------

AD_LINE_QUOTE = {"quote": {"text": "I use it every night after work and sleep like a rock.",
                           "author": "From the ad", "claim_ids": ["reviews-live"]}}
DANA = "Best sauna I ever owned. It heats fast."


def _loop(responses, tmp_path, facts_pack=None):
    from harness.budget import Budget
    from harness.log import RunLog
    from tests.conftest import FakeClient, json_response

    client = FakeClient([json_response(r) for r in responses])
    log = RunLog("test-run", tmp_path / "run.log")
    try:
        result = repair.write_and_gate_page(
            cartridge_name="landing", cartridges_dir=REPO_ROOT / "cartridges", ad_brief=LANDING_BRIEF,
            facts_pack=facts_pack or _facts_pack(), client=client, model="claude-sonnet-5", budget=Budget(),
            log=log, financing_lender="Bread Pay", speaker_pov=LANDING_BRIEF["speaker_pov"], tenant=TENANT,
        )
    finally:
        log.close()
    return result, client, (tmp_path / "run.log").read_text()


def test_a_quote_with_no_review_quotes_is_dropped_and_the_page_passes_on_attempt_one(tmp_path):
    fp = _facts_pack()
    assert not fp.get("review_quotes")
    (page, attempts, fixes), client, log_text = _loop([_page(proof=copy.deepcopy(AD_LINE_QUOTE))], tmp_path, fp)
    assert len(client.messages.calls) == 1 and attempts == [[]] and fixes == [1]
    assert "quote" not in page["proof"]
    assert "fix: dropped unverifiable proof.quote" in log_text


def test_the_deterministic_pass_drops_an_unverifiable_quote():
    page = _page(proof=copy.deepcopy(AD_LINE_QUOTE))
    problems = _gates(page)
    assert "landing:quote_verbatim" in _keys(problems)
    fixed = repair.apply_deterministic_fixes(page, problems, set(), cartridge_name="landing", facts_pack=_facts_pack())
    assert fixed == 1 and "quote" not in page["proof"]
    assert "landing:quote_verbatim" not in _keys(_gates(page))


def test_a_verbatim_quote_survives_the_write_loop(tmp_path):
    fp = _facts_pack()
    fp["review_quotes"] = [{"id": "rq-dana", "text": DANA, "author": "Dana R."}]
    proof = {"quote": {"text": "Best sauna I ever owned", "author": "Dana R.", "claim_ids": ["reviews-live"]}}
    (page, attempts, fixes), client, log_text = _loop([_page(proof=proof)], tmp_path, fp)
    assert page["proof"] == proof and fixes == [0]
    assert "dropped unverifiable" not in log_text


def test_the_prompt_says_do_not_write_a_quote_when_there_are_none():
    lines = landing.writer_lines(_facts_pack(), TENANT, look="proof")
    assert ("There are no customer review quotes in the facts pack: do not write proof.quote; "
            "the rating row is shown by the renderer.") in lines
    assert not any("word for word from" in line for line in lines)
    fp = _facts_pack()
    fp["review_quotes"] = [{"id": "rq-dana", "text": DANA, "author": "Dana R."}]
    text = " ".join(landing.writer_lines(fp, TENANT, look="proof"))
    assert landing.NO_REVIEW_QUOTES_LINE not in text
    assert '"id": "rq-dana"' in text and DANA in text and "proof.quote sits directly under the subhead" in text


def _degenerate_page():
    page = _page()
    return {"cta_text": page["cta_text"], "cta_url": page["cta_url"], "hero": page["hero"],
            "reasons": [], "faq": {"questions": []}, "closing": {}}


def test_a_degenerate_repair_is_discarded_and_sent_again(tmp_path):
    bad = _page()
    bad["hero"]["subhead"] = " ".join(["plain"] * 31)       # one gate failure, not trimmable
    good = _page()
    (page, attempts, fixes), client, log_text = _loop([bad, _degenerate_page(), good], tmp_path)
    assert page == good and len(client.messages.calls) == 3
    assert len(attempts) == 3 and attempts[1] == attempts[0] and attempts[2] == []
    assert "attempt 2: degenerate repair discarded" in log_text and "kept attempt 1's page" in log_text
    third = json.dumps(client.messages.calls[2], default=str)
    assert repair.DEGENERATE_REPAIR_NOTE in third
    assert repair.DEGENERATE_REPAIR_NOTE not in json.dumps(client.messages.calls[1], default=str)


def test_a_degenerate_last_attempt_stops_with_the_previous_pages_failures(tmp_path):
    bad = _page()
    bad["hero"]["subhead"] = " ".join(["plain"] * 31)
    n = repair.max_repair_attempts("landing")
    with pytest.raises(claims.ClaimsGateFailure) as exc_info:
        _loop([bad] * n + [_degenerate_page()], tmp_path)
    assert len(exc_info.value.attempts) == n + 1
    assert exc_info.value.attempts[-1] == exc_info.value.attempts[-2]


def test_the_degenerate_guard_is_general():
    full = _page()
    assert not repair.degenerate_repair(full, copy.deepcopy(full))
    assert repair.degenerate_repair(full, _degenerate_page())
    for drop in ("reasons", "faq", "closing"):
        lost = copy.deepcopy(full)
        lost[drop] = [] if drop == "reasons" else {}
        lost["hero"]["subhead"] += " word" * 200                                  # keep the word count up
        assert repair.count_words(lost) >= 0.6 * repair.count_words(full), drop   # the section rule, not words
        assert repair.degenerate_repair(full, lost), drop
    # a cartridge with none of the landing sections: the word count alone
    article = {"headline": "A headline", "body_sections": [{"paragraphs": [{"text": "word " * 200}]}]}
    short = {"headline": "A headline", "body_sections": [{"paragraphs": [{"text": "word " * 100}]}]}
    assert repair.degenerate_repair(article, short)
    assert not repair.degenerate_repair(article, copy.deepcopy(article))
