"""Cycle 109: offer-led landing pages, the pipeline hold for unverified offers,
`harness abtest rebuild-arm` (owner, 2026-10-14: "if the ad is about a sale,
the landing page 100% needs to have that as primary focus").

What is asserted here, offline (no model call, no Meta or Shopify write: the
cycle 88 BriefRunner, the fake Shopify transport, cycle 107's MetaFake):
  - the ad brief's offer block from the AIIR sale sentence; claim matching
    against the real promo claim (and its deadline);
  - the offer-led first screen on every landing look and every composition:
    the sale name as the eyebrow, the code and the claim's deadline sentence
    exactly once each, the CTA note, the code in the sticky bar, the offer as
    the first checked claim; the listicle's lighter offer line;
  - the unverified case: no offer text on the page, no denial, offer_unverified;
  - the urgency gate: only the claim's own deadline sentence passes;
  - the hold: built and published, not live on Meta, the Ads page form adds
    the claim, the next run rebuilds the arms and the lever takes it live;
  - rebuild-arm end to end with fake Shopify: the arm's own page is updated,
    the split link is untouched, the old run is kept in history.
"""
import copy
import json
import re
import shutil

import pytest

from evals import fake_run
from harness import abtest, abtest_inbox, abtest_rebuild, cli, claims, landing, meta_golive, meta_ingest, offer
from harness import render as render_mod
from harness import repair, runstate
from tests.support import REPO_ROOT, TENANT, tenant_copy
from tests.test_abtest_cycle67 import _write_fake_run
from tests.test_auto_pipeline_cycle108 import EchoMeta, _item
from tests.test_control_arms_cycle88 import BriefRunner, _shopify
from tests.test_landing_cycle82 import _facts_pack, _gates
from tests.test_landing_proof_cycle85 import _proof_page
from tests.test_listicle import RICH_FACTS_PACK
from tests.test_listicle_site_cycle69 import REVIEWER, _auth, _csrf, client  # noqa: F401 -- fixture
from tests.test_meta_golive_cycle107 import ACCOUNT, AD, ad, video_creative
from tests.test_meta_ingest import make_client
from tests.test_render import AD_BRIEF

AIIR = "$300 off every indoor sauna. $500 off outdoor. Prices go up October 16, so Thursday is your last chance"
PROMO = next(c for c in json.loads((REPO_ROOT / "tenants" / "peak-saunas" / "claims" / "verified.json").read_text())
             if c["category"] == "promo")
DEADLINE = "Prices go up on October 16, 2026."
TODAY = "2026-10-14"
LOOKS = ("open", "proof", "cinematic", "cinematic-light", "buybox")
HEROES = ("dark_statement", "split", "letters_pill", "buybox", "caps_over_photo")


def _product(handle, name):
    return {"slug": f"peak-saunas-{handle}", "name": name, "title": f"Peak {name}", "short_name": f"Peak {name}",
            "url": f"https://peaksaunas.com/products/peak-saunas-{handle}"}


def _offer_facts(product=None):
    """The landing suite's Fuji facts pack after the claims step matched the
    AIIR offer to the real promo claim."""
    fp = _facts_pack()
    if product:
        fp["product"] = dict(fp["product"], **product)
    brief = dict(AD_BRIEF, offer=offer.extract_offer(AIIR))
    assert offer.apply_to_run(brief, fp, [PROMO], today=TODAY)["id"] == PROMO["id"]
    return fp, brief


def _unverified_facts():
    fp = _facts_pack()
    brief = dict(AD_BRIEF, offer=offer.extract_offer(AIIR))
    assert offer.apply_to_run(brief, fp, [], today=TODAY) is None
    return fp, brief


def _landing(look, hero=None, headline="Money off every indoor sauna during the sale"):
    page = copy.deepcopy(fake_run.LANDING_PAGE) if look != "proof" else _proof_page()
    page["look"] = look
    page["hero"]["headline"] = headline
    if hero:
        page["hero_style"] = hero
    return page


def _render(tmp_path, page, fp, brief, cartridge="landing"):
    out = tmp_path / cartridge
    html = render_mod.render_page(
        cartridge_name=cartridge, page=page, ad_brief=brief, facts_pack=fp, cartridges_dir=REPO_ROOT / "cartridges",
        brand_dir=TENANT.brand_dir, templates_dir=REPO_ROOT / "harness" / "templates", out_dir=out,
        published=TODAY, updated=TODAY, tenant=TENANT, download_assets=False,
    ).read_text()
    return html, json.loads((out / "page.json").read_text())


def _first_screen(html):
    start = html.index("<header")
    return html[start:html.index("</header>", start)]


# ---------------------------------------------------------------------------
# 1. the brief's offer block
# ---------------------------------------------------------------------------

def test_the_aiir_sentence_gives_amounts_the_deadline_and_a_calm_sentence():
    got = offer.extract_offer(AIIR)
    assert got["amounts"] == ["$300", "$500"] and got["amount"] == "$300"
    assert got["deadline"] == "October 16" and got["codes"] == [] and got["name"] is None
    # the ad's own sentence that states the offer -- never the "last chance" one
    assert got["sentence"] == "$300 off every indoor sauna. $500 off outdoor."


def test_codes_percentages_and_sale_names_and_no_offer():
    got = offer.extract_offer("Use code FALL10 for 10% off.", "The Labor Day Sale ends September 5, 2026.")
    assert (got["amounts"], got["codes"], got["name"], got["deadline"]) == (
        ["10%"], ["FALL10"], "Labor Day Sale", "September 5, 2026")
    assert offer.extract_offer("Save $250 with promo code PEAK250 at checkout.")["codes"] == ["PEAK250"]
    assert offer.extract_offer("A sauna that plugs into a normal outlet. Read the code of conduct.") is None
    assert offer.extract_offer("Delivery by October 20 in most states.") is None   # a date, not a deadline


def test_the_brief_reads_the_offer_from_the_ad_copy_around_the_media():
    brief = {"transcript_or_text": "Mornings are cold in Ohio."}
    got = offer.offer_from_brief(brief, {"primary_text": AIIR, "headline": "Community Savings Sale"})
    assert got["amounts"] == ["$300", "$500"] and got["name"] == "Community Savings Sale"
    assert offer.offer_from_brief(brief, None) is None


# ---------------------------------------------------------------------------
# 2. the claims step
# ---------------------------------------------------------------------------

def test_the_aiir_offer_matches_the_real_promo_claim_until_its_deadline():
    aiir = offer.extract_offer(AIIR)
    verified = json.loads((TENANT.claims_dir / "verified.json").read_text())
    assert offer.match_offer(aiir, verified, TODAY)["id"] == "promo-community-savings-sale-2026-10"
    assert offer.match_offer(aiir, verified, "2026-10-15") is not None
    assert offer.match_offer(aiir, verified, "2026-10-16") is None   # "prices go up on October 16"


@pytest.mark.parametrize("change", [
    {"amounts": ["$400"]},                      # an amount the claim does not state
    {"codes": ["FALL300"]},                     # a code the claim does not state
    {"deadline": "October 20"},                 # another deadline day
    {"amounts": [], "codes": [], "name": "Black Friday Sale"},
])
def test_an_offer_the_claim_does_not_state_does_not_match(change):
    assert offer.match_offer(dict(offer.extract_offer(AIIR), **change), [PROMO], TODAY) is None


def test_only_a_promo_claim_matches():
    assert offer.match_offer(offer.extract_offer(AIIR), [dict(PROMO, category="price")], TODAY) is None


def test_the_claims_step_puts_the_claim_in_the_facts_pack_or_marks_the_offer_unverified():
    fp, brief = _offer_facts()
    assert brief["offer"]["claim_id"] == PROMO["id"]
    assert fp["offer"]["claim_id"] == PROMO["id"] and fp["offer"]["sentence"].startswith("$300 off")
    assert PROMO["id"] in {c["id"] for c in fp["verified_claims"]} and "offer_unverified" not in fp
    fp, brief = _unverified_facts()
    assert "claim_id" not in brief["offer"] and "offer" not in fp
    assert fp["offer_unverified"]["sentence"] == "$300 off every indoor sauna. $500 off outdoor."


def test_the_ad_claims_gate_matches_the_offer_to_its_promo_claim():
    fp, _brief = _offer_facts()
    brief = {"claims_made": ["$300 off every indoor sauna.", "$500 off outdoor saunas."]}
    product = {"slug": "fuji", "price": "8250.00"}
    matched, not_repeated, _alt = claims.gate_ad_brief_claims(brief, [PROMO], product=product, policy="warn",
                                                               offer_claim=fp["offer"])
    assert [m["matched_claim_id"] for m in matched] == [PROMO["id"]] * 2 and not_repeated == []
    # without a verified offer, the same claims are prices that match no product (not repeated)
    matched, not_repeated, _alt = claims.gate_ad_brief_claims(brief, [PROMO], product=product, policy="warn")
    assert matched == [] and len(not_repeated) == 2


# ---------------------------------------------------------------------------
# 3. the offer-led first screen on every landing look and composition
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("look, hero", [(look, None) for look in LOOKS] + [("open", h) for h in HEROES])
def test_every_landing_look_leads_with_the_offer(tmp_path, look, hero):
    fp, brief = _offer_facts()
    html, page = _render(tmp_path, _landing(look, hero), fp, brief)
    first = _first_screen(html)
    assert first.count("INDOOR300") == 1 and first.count(DEADLINE) == 1
    assert "$300 off with code INDOOR300" in first and "OUTDOOR500" not in first
    assert "Community Savings Sale" in first                     # the eyebrow names the sale
    assert "Offer applied at checkout with the code." in first   # under the first CTA
    assert first.index("$8,250") < first.index("INDOOR300")      # next to the price
    bar = html[html.index('class="ld-bar"'):]
    assert "$8,250 &middot; Code INDOOR300" in bar[:400]
    # the offer is the first checked claim: the ad's sentence, the verified claim's text
    said = html.index("$300 off every indoor sauna. $500 off outdoor.")
    assert said < html.index(PROMO["text"]) and said < html.index("Sauna shopping used to be confusing.")
    assert all(not i.get("offer") for i in page["checked_claims"])
    assert "offer_unverified" not in page and "last chance" not in html.lower()


def test_an_outdoor_product_shows_its_own_amount_and_code(tmp_path):
    fp, brief = _offer_facts(_product("kilimanjaro-5-person-outdoor", "Kilimanjaro"))
    view = offer.offer_view(fp)
    assert (view["line"], view["bar"], view["code"]) == (
        "$500 off with code OUTDOOR500", "Code OUTDOOR500", "OUTDOOR500")
    fp, _ = _offer_facts(_product("matterhorn-3-person", "Matterhorn"))
    view = offer.offer_view(fp)   # neither indoor nor outdoor in its name: the claim's whole offer
    assert view["code"] is None and view["bar"] == "Codes INDOOR300 / OUTDOOR500" and "OUTDOOR500" in view["line"]


def test_the_offer_claim_joins_the_landing_claim_ids(tmp_path):
    fp, brief = _offer_facts()
    ctx = landing.render_context(_landing("open"), fp, ad_brief=brief, tenant=TENANT, look="open")
    assert PROMO["id"] in ctx["claim_ids"] and ctx["eyebrow"] == "Community Savings Sale"


def test_the_listicle_shows_the_offer_line_under_the_price_and_in_the_closing(tmp_path):
    from tests.test_first_screen_cycle79 import _page as listicle_page

    fp = copy.deepcopy(RICH_FACTS_PACK)
    fp["verified_claims"] += [   # the buy box (value stack) needs a feature line and the price claim
        {"id": "pdp-fuji-cabin", "text": "Canadian red cedar cabin, inside and out.", "category": "spec",
         "source": "https://peaksaunas.com/products/fuji"},
        {"id": "price-fuji", "text": "The Peak Fuji is priced at $8,250.", "category": "price",
         "source": "https://peaksaunas.com/products/fuji"},
    ]
    brief = dict(AD_BRIEF, offer=offer.extract_offer(AIIR))
    offer.apply_to_run(brief, fp, [PROMO], today=TODAY)
    html, _page = _render(tmp_path, listicle_page("story"), fp, brief, cartridge="listicle")
    assert html.count(f"<b>{offer.offer_view(fp)['line']}</b>") == 2 and "Community Savings Sale: " in html
    assert html.count(DEADLINE) == 1


# ---------------------------------------------------------------------------
# 4. the unverified offer
# ---------------------------------------------------------------------------

def test_an_unverified_offer_page_says_nothing_about_it_and_records_the_flag(tmp_path):
    fp, brief = _unverified_facts()
    html, page = _render(tmp_path, _landing("open"), fp, brief)
    assert page["offer_unverified"] is True
    for text in ("$300", "INDOOR300", "Prices go up", "no discount", "Offer applied", "Community Savings Sale"):
        assert text not in html, text
    lines = landing.writer_lines(fp, look="open")
    assert offer.UNVERIFIED_LINE in lines and not any("OFFER-LED" in line for line in lines)


@pytest.mark.parametrize("path, text", [
    (("checked_claims", 0, "fact"), "The Fuji is $8,250 at full price, no discount applied."),
    (("faq", "questions", 0, "answer"), "There is no sale right now; the price is the price."),
    (("closing", "headline"), "Not on sale, still worth it"),
])
def test_no_page_denies_an_offer_the_ad_states(path, text):
    def put(page):
        node = page
        for key in path[:-1]:
            node = node[key]
        node[path[-1]] = text
        return page

    fp, _ = _unverified_facts()
    keys = {p["key"].split(":")[1] for p in landing.find_landing_violations(put(_landing("open")), fp)}
    assert "offer_denial" in keys
    # the same words on a page whose ad states no offer are not this gate's business
    plain = landing.find_landing_violations(put(_landing("open")), _facts_pack())
    assert not any(":offer_denial:" in p["key"] for p in plain)


def test_an_unverified_offer_stays_out_of_the_checked_claims():
    fp, _ = _unverified_facts()
    page = _landing("open")
    page["checked_claims"][0]["ad_said"] = "A fall sale with money off every model."
    keys = {p["key"] for p in landing.find_landing_violations(page, fp)}
    assert "landing:offer_in_checked:0" in keys


def test_the_listicle_gate_refuses_a_denial_too(monkeypatch):
    fp, _ = _unverified_facts()
    page = {"headline": "x", "items": [{"heading": "Price", "body": "Full price, no discount."}]}
    problems = repair.check_page_gates(page, fp, "listicle", financing_lender=None, speaker_pov="brand",
                                       word_range=None, allowed_cta_texts=None, tenant=TENANT)
    assert any(p.get("key", "").startswith("listicle:offer_denial:") for p in problems)


# ---------------------------------------------------------------------------
# 5. the urgency gate and the offer-led writer rules
# ---------------------------------------------------------------------------

def test_the_canned_offer_led_page_passes_every_gate():
    fp, _ = _offer_facts()
    page = _landing("open")
    page["faq"]["questions"][0]["answer"] = DEADLINE
    page["faq"]["questions"][0]["claim_ids"] = [PROMO["id"]]
    assert _gates(page, fp) == []


@pytest.mark.parametrize("answer, ok", [
    (DEADLINE, True),                                            # the verified claim's own sentence
    ("Prices go up on October 16, 2026", True),                  # the same sentence, no full stop
    ("Prices go up October 16, so Thursday is your last chance.", False),
    ("The sale ends soon, so order this week.", False),
    ("Prices go up on October 20, 2026.", False),                # not the claim's date
])
def test_the_urgency_gate_allows_only_the_claims_deadline_sentence(answer, ok):
    fp, _ = _offer_facts()
    page = _landing("open")
    page["faq"]["questions"][0].update(answer=answer, claim_ids=[PROMO["id"]])
    urgent = [p for p in landing.find_landing_violations(page, fp) if ":urgency:" in p["key"]]
    assert (urgent == []) is ok, urgent


def test_with_no_offer_a_deadline_sentence_is_urgency():
    page = _landing("open")
    page["faq"]["questions"][0]["answer"] = DEADLINE
    keys = {p["key"] for p in landing.find_landing_violations(page, _facts_pack())}
    assert "landing:urgency:deadline" in keys


def test_an_offer_led_headline_is_about_the_offer_and_the_writer_is_told():
    fp, _ = _offer_facts()
    keys = {p["key"] for p in landing.find_landing_violations(_landing("open", headline="Know the sauna before "
                                                                                       "it arrives"), fp)}
    assert "landing:offer_headline" in keys
    lines = landing.writer_lines(fp, look="open")
    assert any("OFFER-LED" in line and PROMO["id"] in line for line in lines)
    assert any(DEADLINE in line for line in lines)


def test_a_fake_run_of_the_sale_ad_matches_the_claim_and_renders_an_offer_led_page(tmp_path, monkeypatch):
    """ingest -> ground (the claims step) -> write (the offer headline gate and
    one repair) -> render, with FakeClient and a promo claim dated 2099."""
    from tests.conftest import json_response

    root = tmp_path / "claims"
    shutil.copytree(TENANT.claims_dir, root)
    verified = json.loads((root / "verified.json").read_text())
    for c in verified:
        if c["category"] == "promo":
            c["text"] = c["text"].replace("2026", "2099")
    (root / "verified.json").write_text(json.dumps(verified, indent=2) + "\n")
    monkeypatch.setattr(type(TENANT), "claims_dir", property(lambda self: root))
    fixture = tmp_path / "aiir-sale.txt"
    fixture.write_text(AIIR + "\n")
    fixed = dict(copy.deepcopy(fake_run.LANDING_PAGE), hero=dict(fake_run.LANDING_PAGE["hero"],
                 headline="Money off every indoor sauna during the sale", accent_phrase="Money off"))
    rc, run_dir, _pages = fake_run.run_once(str(fixture), tenant="peak-saunas", cartridges="landing", look="open",
                                            extra_responses=[json_response(fixed)])
    assert rc == 0
    brief = json.loads((run_dir / "ad_brief.json").read_text())
    facts = json.loads((run_dir / "facts_pack.json").read_text())
    assert brief["offer"]["claim_id"] == PROMO["id"] and facts["offer"]["claim_id"] == PROMO["id"]
    html = (run_dir / "landing" / "index.html").read_text()
    first = _first_screen(html)
    assert first.count("INDOOR300") == 1 and first.count("Prices go up on October 16, 2099.") == 1
    assert "every indoor sauna during the sale" in first and "Community Savings Sale" in first
    log = next((TENANT.runs_dir).glob("*.log")).read_text()
    assert "landing:offer_headline" in log


# ---------------------------------------------------------------------------
# 6. the pipeline hold and the claim form
# ---------------------------------------------------------------------------

OFFER_BRIEF = {"transcript_or_text": AIIR, "offer": offer.extract_offer(AIIR)}


@pytest.fixture
def claims_copy(tmp_path, monkeypatch):
    """claims/ copied to tmp with no promo claim: the AIIR offer is unverified."""
    root = tmp_path / "claims"
    shutil.copytree(TENANT.claims_dir, root)
    verified = [c for c in json.loads((root / "verified.json").read_text()) if c["category"] != "promo"]
    (root / "verified.json").write_text(json.dumps(verified, indent=2) + "\n")
    monkeypatch.setattr(type(TENANT), "claims_dir", property(lambda self: root))
    return root


def _meta(monkeypatch):
    fake = EchoMeta()
    fake.add("GET", AD, ad(video_creative("https://peaksaunas.com/")))
    fake.add("GET", "111", {"id": "111", "instagram_business_account": {"id": "222"}})
    fake.add("POST", f"{ACCOUNT}/adcreatives", {"id": "c2"})
    fake.add("POST", AD, {"success": True})
    monkeypatch.setattr(meta_golive, "client_for", lambda tenant: make_client(fake))
    return fake


def _shopify_publish(monkeypatch, store):
    real = abtest_inbox.publish_test

    def publish(tenant, test_id, *, by):
        transport = _shopify(monkeypatch, [f"lp-{test_id}-a", f"lp-{test_id}-b", f"lp-{test_id}"])
        transport.set_response("PUT", "pages/500.json", 200, {"page": {"id": 500, "handle": f"lp-{test_id}-a"}})
        transport.set_response("PUT", "pages/501.json", 200, {"page": {"id": 501, "handle": f"lp-{test_id}-b"}})
        store["t"] = transport
        return real(tenant, test_id, by=by)

    monkeypatch.setattr(abtest_inbox, "publish_test", publish)


def test_an_unverified_offer_is_built_and_published_but_held_off_meta(monkeypatch, claims_copy, client):  # noqa: F811
    tenant_copy(monkeypatch, {"abtest.auto_publish": True, "abtest.auto_go_live": True})
    _item(AD, "AIIR sale", primary_text=AIIR)
    fake, store = _meta(monkeypatch), {}
    _shopify_publish(monkeypatch, store)
    summary = abtest_inbox.from_inbox(TENANT, by=REVIEWER, runner=BriefRunner(brief=OFFER_BRIEF), log=lambda *_: None)
    rec = abtest.list_tests(TENANT)[0]
    assert summary["published"] == [AD] and summary["went_live"] == [] and rec["status"] == "live"
    assert rec["offer_hold"]["state"] == "held" and rec["offer_hold"]["reason"] == "held: offer not verified"
    assert rec["offer_hold"]["sentence"] == "$300 off every indoor sauna. $500 off outdoor."
    assert rec["auto_go_live"]["state"] == "held" and "meta_go_live" not in rec
    assert not any(c["method"] == "POST" for c in fake.calls)            # Meta was never written
    item = meta_ingest.Inbox(TENANT.meta_inbox_dir).read(AD)
    assert "held: offer not verified" in item["reason"]
    assert abtest_inbox.read_status(TENANT)["inbox"]["held"] == 1
    assert "1 held (offer not verified)" in abtest_inbox.status_line(TENANT)
    # the Ads page: the ad's sentence and the one-click form
    body = client.get("/", headers=_auth()).get_data(as_text=True)
    assert "Held: offer not verified." in body and "$300 off every indoor sauna. $500 off outdoor." in body
    assert f'action="/test/{rec["test_id"]}/offer-claim"' in body and "Prices go up on October 16." in body


def test_the_claim_form_writes_a_promo_claim_and_the_next_run_rebuilds_and_goes_live(
        monkeypatch, claims_copy, client):  # noqa: F811
    tenant_copy(monkeypatch, {"abtest.auto_publish": True, "abtest.auto_go_live": True})
    _item(AD, "AIIR sale", primary_text=AIIR)
    fake, store = _meta(monkeypatch), {}
    _shopify_publish(monkeypatch, store)
    runner = BriefRunner(brief=OFFER_BRIEF)
    abtest_inbox.from_inbox(TENANT, by=REVIEWER, runner=runner, log=lambda *_: None)
    tid = abtest.list_tests(TENANT)[0]["test_id"]
    url = f"/test/{tid}/offer-claim"
    # a claim that does not state the ad's offer is refused, nothing written
    bad = client.post(url, data={"csrf": _csrf(), "text": "Fall Sale: $100 off every sauna."}, headers=_auth())
    assert bad.status_code == 400 and "does not state the ad" in bad.get_data(as_text=True)
    assert not any(c["category"] == "promo" for c in json.loads((claims_copy / "verified.json").read_text()))
    text = ("Community Savings Sale: $300 off indoor saunas with code INDOOR300 and $500 off outdoor saunas with code "
            "OUTDOOR500, applied at checkout; prices go up on October 16, 2099.")
    ok = client.post(url, data={"csrf": _csrf(), "text": text}, headers=_auth())
    assert ok.status_code == 303
    entry = next(c for c in json.loads((claims_copy / "verified.json").read_text()) if c["category"] == "promo")
    assert entry["text"] == text and entry["approved_by"] == REVIEWER and entry["id"].startswith(
        "promo-community-savings-sale-")
    assert re.fullmatch(rf"owner {re.escape(REVIEWER)} \d{{4}}-\d\d-\d\d", entry["source"])
    # the lever's hourly run: rebuild both generated arms on their own pages, then go live
    calls_before = len(runner.calls)
    summary = abtest_inbox.from_inbox(TENANT, by=REVIEWER, runner=runner, log=lambda *_: None)
    rec = abtest.load_test(TENANT, tid)
    assert summary["released"] == [tid] and summary["went_live"] == [tid]
    assert len(runner.calls) == calls_before + 2
    assert rec["offer_hold"]["state"] == "released" and rec["offer_hold"]["claim_id"] == entry["id"]
    assert rec["auto_go_live"]["state"] == "done" and rec["meta_go_live"]["verified"] is True
    puts = [c["url"] for c in store["t"].calls if c["method"] == "PUT"]
    assert any(u.endswith("pages/500.json") for u in puts) and any(u.endswith("pages/501.json") for u in puts)
    assert all(len(v.get("history") or []) == 1 for v in rec["variants"] if not abtest.is_external(v))


def test_a_test_with_a_verified_offer_is_not_held(monkeypatch):
    tenant_copy(monkeypatch, {"abtest.auto_publish": False, "abtest.auto_go_live": False})
    _item(AD, "AIIR sale", primary_text=AIIR)
    brief = dict(OFFER_BRIEF, offer=dict(OFFER_BRIEF["offer"], claim_id=PROMO["id"]))
    abtest_inbox.from_inbox(TENANT, by=REVIEWER, runner=BriefRunner(brief=brief), log=lambda *_: None)
    rec = abtest.list_tests(TENANT)[0]
    assert "offer_hold" not in rec and abtest_inbox._lever_skip(TENANT, rec) is None


# ---------------------------------------------------------------------------
# 7. rebuild-arm end to end with fake Shopify
# ---------------------------------------------------------------------------

class ScoredRunner:
    """A fake `harness run` that records the arm, the seed and the design gate
    mode it ran under, and leaves a design score like the warn-mode gate."""

    def __init__(self, rc=0):
        self.calls, self.rc = [], rc

    def __call__(self, tenant, input_path, arm, seed):
        import os

        self.calls.append({"arm": arm, "seed": seed, "gate": os.environ.get("HARNESS_DESIGN_GATE")})
        if self.rc:
            return self.rc, None
        run_dir = _write_fake_run(tenant, arm, seed)
        page = {"headline": "x", "look": arm.look, "hero_style": arm.hero_style}
        (run_dir / arm.cartridge / "page.json").write_text(json.dumps(page))
        (run_dir / arm.cartridge / "design").mkdir()
        (run_dir / arm.cartridge / "design" / "scores.json").write_text(
            json.dumps({"score": 71, "status": "warn", "cost_usd": 0.02}))
        return 0, run_dir


def _live_test(monkeypatch):
    """A live 2-generated + home test whose arms were published as pages 500/501."""
    tenant_copy(monkeypatch, {"abtest.library": ["landing:open:split", "landing:proof:dark_statement",
                                                 "landing:cinematic:split"]})
    rc, rec = abtest.create_test(TENANT, input_path="fixture.mov", name="AIIR sale", seed=7, runner=BriefRunner(),
                                 plan="gen2-home")
    tid = rec["test_id"]
    for v, page_id in zip(rec["variants"][:2], (500, 501)):
        v.update(look=v["arm"].split(":")[1], hero_style=v["arm"].split(":")[2])
        runstate.mark_published(v["run_dir"], page=v["cartridge"], by=REVIEWER, page_id=page_id,
                                handle=f"lp-{tid}-{v['key'].lower()}", url=f"https://peaksaunas.com/pages/lp-{tid}-"
                                f"{v['key'].lower()}")
        runstate.record_abtest(v["run_dir"], page=v["cartridge"], test_id=tid, key=v["key"])
    rec.update(status="live", split={"handle": f"lp-{tid}", "url": f"https://peaksaunas.com/pages/lp-{tid}",
                                     "page_id": 502})
    abtest.save_test(TENANT, rec)
    transport = _shopify(monkeypatch, [])
    transport.set_response("PUT", "pages/500.json", 200, {"page": {"id": 500, "handle": f"lp-{tid}-a"}})
    transport.set_response("PUT", "pages/501.json", 200, {"page": {"id": 501, "handle": f"lp-{tid}-b"}})
    return abtest.load_test(TENANT, tid), transport


def test_rebuild_arm_updates_the_arms_own_page_and_keeps_the_split_link(monkeypatch):
    rec, transport = _live_test(monkeypatch)
    tid, old = rec["test_id"], rec["variants"][0]
    runner = ScoredRunner()
    monkeypatch.setattr(abtest, "default_runner", runner)
    assert cli.main(["abtest", "rebuild-arm", tid, "a", "--by", REVIEWER, "--tenant", TENANT.name]) == 0
    call = runner.calls[0]
    assert (call["arm"].cartridge, call["arm"].look, call["arm"].hero_style) == ("landing", "open", "split")
    assert call["seed"] == rec["seed"] + 1000 + len(old["attempts"]) + 1 and call["gate"] == "warn"
    # one PUT to arm A's own page; nothing created, the split page untouched
    writes = [c for c in transport.calls if c["method"] in ("POST", "PUT") and "pages" in c["url"]]
    assert [(c["method"], c["url"].rsplit("/", 1)[-1]) for c in writes] == [("PUT", "500.json")]
    body = json.loads(writes[0]["body"])["page"]["body_html"]
    assert f'"{tid}"' in body and 'V="A"' in body   # the arm's tracking beacon
    new = abtest.load_test(TENANT, tid)
    a = new["variants"][0]
    assert a["run_dir"] != old["run_dir"] and a["history"][0]["run_dir"] == old["run_dir"]
    assert (a["design_score"], a["design_status"]) == (71, "warn") and a["rebuilt_by"] == REVIEWER
    assert a["shopify"]["page_id"] == 500 and new["split"] == rec["split"] and new["variants"][1] == rec["variants"][1]
    assert runstate.published_page_record(a["run_dir"], "landing")["page_id"] == 500
    import os

    assert os.environ.get("HARNESS_DESIGN_GATE") == "off"   # the warn override ends with the run


def test_rebuild_arm_all_rebuilds_every_generated_arm(monkeypatch):
    rec, transport = _live_test(monkeypatch)
    monkeypatch.setattr(abtest, "default_runner", ScoredRunner())
    assert cli.main(["abtest", "rebuild-arm", rec["test_id"], "--all", "--by", REVIEWER, "--tenant", TENANT.name]) == 0
    puts = sorted(c["url"].rsplit("/", 1)[-1] for c in transport.calls if c["method"] == "PUT")
    assert puts == ["500.json", "501.json"]
    assert [len(v.get("history") or []) for v in abtest.load_test(TENANT, rec["test_id"])["variants"]] == [1, 1, 0]


@pytest.mark.parametrize("change, argv, message", [
    ({"status": "built"}, ["A"], "live test only"),
    ({}, ["C"], "external page"),
    ({}, ["A", "--all"], "name one arm"),
    ({}, [], "name one arm"),
])
def test_rebuild_arm_refusals_change_nothing(monkeypatch, capsys, change, argv, message):
    rec, transport = _live_test(monkeypatch)
    rec.update(change)
    abtest.save_test(TENANT, rec)
    runner = ScoredRunner()
    monkeypatch.setattr(abtest, "default_runner", runner)
    rc = cli.main(["abtest", "rebuild-arm", rec["test_id"], *argv, "--by", REVIEWER, "--tenant", TENANT.name])
    assert rc != 0 and message in capsys.readouterr().err
    assert runner.calls == [] and transport.calls == []


def test_a_failed_rebuild_leaves_the_live_page_alone(monkeypatch):
    rec, transport = _live_test(monkeypatch)
    with pytest.raises(abtest.AbtestError, match="the live page is unchanged"):
        abtest_rebuild.rebuild_arm(TENANT, rec["test_id"], "B", by=REVIEWER, runner=ScoredRunner(rc=2))
    after = abtest.load_test(TENANT, rec["test_id"])
    assert after["variants"][1]["run_dir"] == rec["variants"][1]["run_dir"] and transport.calls == []
    assert after["variants"][1]["attempts"][-1]["rebuild"] is True


def test_a_reviewer_must_be_listed(monkeypatch):
    rec, _transport = _live_test(monkeypatch)
    with pytest.raises(abtest.AbtestError, match="not a listed reviewer"):
        abtest_rebuild.rebuild_arm(TENANT, rec["test_id"], "A", by="someone@example.com", runner=ScoredRunner())
