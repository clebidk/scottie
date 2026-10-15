"""Cycle 110: the proof and offer facts behind the pre-sell page.

Every network read is faked with the real samples fetched on 2026-10-15
(tests/fixtures/c110/): the Judge.me store page head, two pages of the Fuji
product-review widget JSON, one page of the store-wide reviews and the Fuji
product page's financing line and disclosure."""
import html
import json
import re

import pytest

from harness import offer, proof, textutil
from harness import tenant as tenant_mod
from harness.ground import LocalFactsSource, load_claims_config
from harness.sources import judgeme
from tests.support import REPO_ROOT, TENANT

FX = REPO_ROOT / "tests" / "fixtures" / "c110"
FUJI = "peak-saunas-fuji-2-person-indoor-near-zero-emf-full-spectrum-infrared-sauna-with-medical-grade-red-light-therapy"
MINI = "peak-saunas-mini-1-person-indoor-full-spectrum-infrared-sauna-with-medical-grade-red-light-therapy"
CROWN = "peak-saunas-crown-2-person-full-spectrum-infrared-sauna"
PATAGONIA = "peak-saunas-patagonia-2-person-outdoor-full-spectrum-infrared-sauna-with-smart-wifi-app-control"
MATTERHORN = ("peak-saunas-matterhorn-3-person-full-spectrum-infrared-sauna-with-two-xl-medical-grade-red-light-"
              "therapy-smart-wifi-app-control")
FUJI_ID = 9964509331757
BRIEF = {"transcript_or_text": "", "hook": "", "promise": "", "angle": ""}
CONTRACT_QUOTE_KEYS = {"claim_id", "text", "author", "date", "stars", "product"}


def _fx(name):
    return (FX / name).read_text()


def fake_fetch(url):
    """The real 2026-10-15 responses, by URL."""
    if url == "https://judge.me/reviews/stores/peaksaunas.com":
        return _fx("judgeme-store-page-20261015.html")
    if "reviews_for_widget" in url and f"product_id={FUJI_ID}" in url:
        page = re.search(r"[?&]page=(\d+)", url).group(1)
        return _fx(f"judgeme-widget-fuji-p{page}-20261015.json")
    if "all_reviews_js_based" in url and "page=1&" in url:
        return _fx("judgeme-all-reviews-p1-20261015.json")
    raise OSError(f"no fake response for {url}")


def _widget_reviews():
    return (judgeme.parse_widget_reviews(_fx("judgeme-widget-fuji-p1-20261015.json"))
            + judgeme.parse_widget_reviews(_fx("judgeme-widget-fuji-p2-20261015.json")))


def _live(**kw):
    product = {"slug": FUJI, "name": "Fuji"}
    return proof.live_claims(TENANT, product, today_iso="2026-10-15",
                             live_products=[{"handle": FUJI, "id": FUJI_ID}], fetch=fake_fetch, **kw)


# ---------------------------------------------------------------------------
# 1. store-level rating
# ---------------------------------------------------------------------------

def test_store_rating_is_read_from_the_real_store_page():
    assert judgeme.parse_store_summary(_fx("judgeme-store-page-20261015.html")) == {"rating": 4.6, "count": 4260}
    claim = judgeme.fetch_store_claim("2026-10-15", fetch=fake_fetch)
    assert claim["id"] == "reviews-store-live"
    assert claim["text"] == "Rated 4.6 out of 5 from 4,260 reviews on Judge.me (fetched 2026-10-15)."
    assert claim["source"] == "https://judge.me/reviews/stores/peaksaunas.com"
    assert (claim["rating"], claim["count"], claim["date"]) == (4.6, 4260, "2026-10-15")


def test_store_rating_fails_soft():
    def boom(url):
        raise OSError("blocked")
    assert judgeme.fetch_store_claim("2026-10-15", fetch=boom) is None
    assert judgeme.fetch_store_claim("2026-10-15", fetch=lambda u: "<html>no rating</html>") is None


def test_facts_pack_gets_reviews_store_and_keeps_reviews_summary():
    reviews_live = {"id": "reviews-live", "text": "Rated 4.8 out of 5 across 49 reviews on Judge.me (fetched 2026-10-15).",
                    "category": "trust", "source": "https://judge.me/reviews/stores/peaksaunas.com"}
    pack = LocalFactsSource(TENANT.claims_dir).facts_for(FUJI, BRIEF, reviews_claim=reviews_live,
                                                         live_claims=_live(), today_iso="2026-10-15")
    assert pack["reviews_store"] == {"text": "Rated 4.6 out of 5 from 4,260 reviews on Judge.me (fetched 2026-10-15).",
                                     "rating": 4.6, "count": 4260, "claim_ids": ["reviews-store-live"]}
    assert pack["reviews_summary"]["claim_ids"] == ["reviews-live"]
    ids = [c["id"] for c in pack["verified_claims"]]
    assert "reviews-store-live" in ids and "reviews-live" in ids


# ---------------------------------------------------------------------------
# 2. verbatim review quotes
# ---------------------------------------------------------------------------

def test_widget_and_store_pages_parse_to_reviews():
    product = _widget_reviews()
    assert len(product) == 49  # "Based on 49 reviews"
    first = product[0]
    assert (first["author"], first["stars"], first["date"], first["verified"]) == ("susan l.", 5, "2026-10-04", True)
    assert first["body"].startswith("I honestly have spent years researching different saunas")
    store = judgeme.parse_all_reviews_html(_fx("judgeme-all-reviews-p1-20261015.json"))
    assert len(store) == 25 and all(r["stars"] in range(1, 6) and r["date"] for r in store)
    assert store[0]["product_title"] == "Peak Rainier 1-Person Full Spectrum Infrared Sauna"


def test_real_fuji_reviews_select_six_verbatim_quotes_that_pass_every_rule():
    product = _widget_reviews()
    store = judgeme.parse_all_reviews_html(_fx("judgeme-all-reviews-p1-20261015.json"))
    picked, skipped = proof.select_quotes(product, store, products=TENANT.catalog_products())
    assert len(picked) == 6
    raw_bodies = {r["body"] for r in product + store}
    for q in picked:
        assert q["body"] in raw_bodies  # verbatim, never cut or mended
        assert proof.quote_problems(q, proof._banned_terms()) == []
        assert 12 <= len(q["body"].split()) <= 70 and q["stars"] >= 4 and q["verified"]
        assert re.fullmatch(r"[A-Z][a-z'\-]+ [A-Z]\.", q["author_label"])
        assert q["product_label"] == "Peak Fuji"
    assert [q["author_label"] for q in picked[:2]] == ["Kristi W.", "David H."]
    # the reasons real reviews were left out
    for reason in ("health outcome", "under 4 stars", "no first name and last initial", "complaint or hedge",
                   "a figure", "marked as machine-made media"):
        assert skipped.get(reason), reason


@pytest.mark.parametrize("body,reason", [
    ("I have had less back pain since I started using it every single night after work", "health outcome"),
    ("Helps my arthritis so much and I love the cedar smell in the evenings with my wife", "health outcome"),
    ("I sleep so much better now and the cabin is beautiful and easy to put together", "health outcome"),
    ("We compared it with a Sunlighten unit and this one won on every point that mattered to us", "banned term 'sunlighten'"),
    ("Got it on sale with the code and it was a great deal for a sauna of this quality", "price, discount or financing"),
    ("Saved three hundred dollars on Black Friday with the discount and it is a lovely sauna", "price, discount or financing"),
    ("It took so long to arrive but now that it is here we love it very much indeed", "complaint or hedge"),
    ("Heats to 150 in about fifteen minutes and holds the temperature through the whole session", "a figure"),
    ("It is a beautiful sauna", "under 12 words"),
    ("The low EMF design was the reason we chose it over every other sauna we looked at", "banned term 'emf'"),
])
def test_quote_rules(body, reason):
    review = {"body": body, "stars": 5, "verified": True, "author": "Ann B.", "ai_media": False}
    assert reason in proof.quote_problems(review, proof._banned_terms())


def test_quote_rules_refuse_low_stars_unverified_anonymous_and_machine_made():
    body = "Our family uses it most evenings and the whole build feels solid and well made throughout"
    ok = {"body": body, "stars": 5, "verified": True, "author": "ann b.", "ai_media": False}
    assert proof.quote_problems(ok, []) == []
    assert "under 4 stars" in proof.quote_problems({**ok, "stars": 3}, [])
    assert "not a verified buyer" in proof.quote_problems({**ok, "verified": False}, [])
    assert "no first name and last initial" in proof.quote_problems({**ok, "author": "Anonymous"}, [])
    assert "no first name and last initial" in proof.quote_problems({**ok, "author": "Steve"}, [])
    assert "marked as machine-made media" in proof.quote_problems({**ok, "ai_media": True}, [])
    assert proof.author_label("susan l.") == "Susan L." and proof.author_label("Rich S.C.") == "Rich S."


def test_quotes_become_verified_claims_and_the_contract_field():
    claims = _live()
    quotes = [c for c in claims if c["id"].startswith("review-quote-")]
    assert [c["id"] for c in quotes] == [f"review-quote-{n}" for n in range(1, 7)]
    assert quotes[0]["category"] == "review" and quotes[0]["approved_by"] == "live-fetch"
    assert "by Kristi W., 2026-07-25, 5 stars, Peak Fuji (https://judge.me/reviews/stores/peaksaunas.com)" in \
        quotes[0]["source"]
    pack = LocalFactsSource(TENANT.claims_dir).facts_for(FUJI, BRIEF, live_claims=claims, today_iso="2026-10-15")
    assert len(pack["review_quotes"]) == 6
    q = pack["review_quotes"][0]
    assert set(q) == CONTRACT_QUOTE_KEYS
    assert (q["claim_id"], q["author"], q["date"], q["stars"], q["product"]) == \
        ("review-quote-1", "Kristi W.", "2026-07-25", 5, "Peak Fuji")
    by_id = {c["id"]: c for c in pack["verified_claims"]}
    for q in pack["review_quotes"]:
        assert by_id[q["claim_id"]]["text"] == q["text"]  # the gates accept the quote word for word


def test_no_reviews_readable_means_no_quotes_and_no_store_field():
    def boom(url):
        raise OSError("blocked")
    claims = proof.live_claims(TENANT, {"slug": FUJI, "name": "Fuji"}, today_iso="2026-10-15",
                               live_products=[{"handle": FUJI, "id": FUJI_ID}], fetch=boom)
    assert [c for c in claims if c["id"].startswith(("review", "reviews"))] == []
    pack = LocalFactsSource(TENANT.claims_dir).facts_for(FUJI, BRIEF, live_claims=claims)
    assert "review_quotes" not in pack and "reviews_store" not in pack
    # reviews switched off: nothing is fetched at all
    calls = []
    proof.live_claims(TENANT, {"slug": FUJI, "name": "Fuji"}, today_iso="2026-10-15", reviews=False,
                      fetch=lambda u: calls.append(u))
    assert calls == []


def test_no_shop_domain_means_no_review_fetch(monkeypatch):
    monkeypatch.delenv("SHOPIFY_STORE", raising=False)
    monkeypatch.setitem(TENANT.config["reviews"], "shop_domain", "")
    calls = []
    assert judgeme.fetch_reviews(FUJI_ID, fetch=lambda u: calls.append(u) or "{}", tenant=TENANT) == ([], [])
    assert calls == []
    monkeypatch.setitem(TENANT.config["reviews"], "shop_domain", "acme.myshopify.com")
    monkeypatch.setitem(TENANT.config["reviews"], "widget_url_template", "")
    monkeypatch.setitem(TENANT.config["reviews"], "all_reviews_url_template", "")
    assert judgeme.fetch_reviews(FUJI_ID, fetch=lambda u: calls.append(u) or "{}", tenant=TENANT) == ([], [])
    assert calls == []


# ---------------------------------------------------------------------------
# 3. financing per model
# ---------------------------------------------------------------------------

def _pdp_financing():
    raw = _fx("fuji-pdp-financing-20261015.html")
    line = re.search(r"<li>(.*?)</li>", raw).group(1)
    fine = re.search(r'<div class="pk-fine"><b>\*</b> (.*?)</div>', raw, re.S).group(1)
    return line, html.unescape(fine)


def test_fuji_financing_is_the_live_product_page_line_and_disclosure_word_for_word():
    line, fine = _pdp_financing()
    pack = LocalFactsSource(TENANT.claims_dir).facts_for(FUJI, BRIEF, today_iso="2026-10-15")
    monthly = pack["product"]["financing"]["monthly"]
    assert monthly["text"] == line == "$458.33/mo for 18 months at 0% APR*"
    assert monthly["disclosure"] == fine
    assert (monthly["amount"], monthly["months"], monthly["apr"]) == ("$458.33", 18, "0%")
    assert monthly["claim_ids"] == ["financing-monthly-fuji"]
    claim = next(c for c in pack["verified_claims"] if c["id"] == "financing-monthly-fuji")
    assert claim["text"] == ("Est. $458.33/mo for 18 months at 0% APR with $0 down through Bread Pay; your actual "
                             "rate, term, and payment are set by Bread Pay when you apply.")
    assert claim["source"].startswith("https://peaksaunas.com/products/" + FUJI)
    assert pack["product"]["financing"]["lender"] == "Bread Pay"
    assert pack["product"]["compare_at_price"] is None


def test_financing_is_price_over_18_and_respects_the_minimum_purchase():
    config = load_claims_config(TENANT.claims_dir)
    mini = proof.financing_claim({"name": "Mini", "url": "u"}, "5450.00", config, "2026-10-15")
    assert mini["id"] == "financing-monthly-mini" and mini["monthly"]["amount"] == "$302.78"
    assert proof.financing_claim({"name": "Crown", "url": "u"}, "4950.00", config, "2026-10-15") is None
    pack = LocalFactsSource(TENANT.claims_dir).facts_for(CROWN, BRIEF)
    assert pack["product"]["financing"]["monthly"] is None
    assert proof.financing_claim({"name": "Fuji"}, "8250", {**config, "financing_lender": None}, "x") is None
    assert proof.financing_claim({"name": "Fuji"}, "8250", {k: v for k, v in config.items()
                                                            if k != "financing_monthly"}, "x") is None


# ---------------------------------------------------------------------------
# 5. existing claims wired in, the included stack
# ---------------------------------------------------------------------------

def test_included_stack_hsa_support_founder_and_us_owned_for_fuji():
    pdp = [{"id": "pdp-fuji-app-control", "category": "spec", "source": "https://peaksaunas.com/products/x",
            "text": "Preheat from anywhere; adjust temperature, lighting, and session length in the Peak Saunas app "
                    "(iOS & Android)."}]
    pack = LocalFactsSource(TENANT.claims_dir).facts_for(FUJI, BRIEF, pdp_claims=pdp)
    assert pack["included"] == [
        {"text": "Medical-grade red light therapy, standard", "claim_ids": ["gbrain-fuji-rlt"]},
        {"text": "Full-spectrum infrared heat: near, mid and far", "claim_ids": ["spec-fuji-infrared-wavelength-range"]},
        {"text": "Peak Wellness Club, a $588-a-year membership, included free", "claim_ids": ["pwc-value-588"]},
        {"text": "Free shipping", "claim_ids": ["gbrain-allowlist-free-shipping"]},
        {"text": "7-year warranty on the heaters and the cabinet",
         "claim_ids": ["warranty-terms", "gbrain-allowlist-lifetime-warranty"]},
        {"text": "Lifetime U.S.-based support", "claim_ids": ["gbrain-warranty-lifetime-definition"]},
        {"text": "App control: preheat from your phone", "claim_ids": ["pdp-fuji-app-control"]},
        {"text": "Two Bluetooth speakers", "claim_ids": ["spec-fuji-audio"]},
    ]
    assert pack["hsa_fsa"] == {"text": "Peak saunas may be eligible for HSA/FSA purchase through Redo.",
                               "claim_ids": ["hsa-fsa-redo"]}
    assert pack["support"] == {"text": "Lifetime U.S.-based support",
                               "claim_ids": ["gbrain-warranty-lifetime-definition"]}
    assert pack["founder"]["claim_ids"] == ["founder-ceo"]
    assert pack["us_owned"] == {"text": "US-owned company.", "claim_ids": ["gbrain-allowlist-us-owned"]}
    ids = {c["id"] for c in pack["verified_claims"]}
    for item in pack["included"]:
        assert set(item["claim_ids"]) <= ids
    assert {"hsa-fsa-redo", "pwc-value-588", "gbrain-warranty-lifetime-definition", "founder-ceo",
            "gbrain-allowlist-us-owned"} <= ids
    assert "lifetime warranty" not in json.dumps(pack["included"]).lower()


def test_no_pdp_app_claim_means_no_app_line_and_a_missing_claim_leaves_the_field_out(monkeypatch):
    pack = LocalFactsSource(TENANT.claims_dir).facts_for(FUJI, BRIEF)
    assert not any("App control" in i["text"] for i in pack["included"])
    active = tenant_mod.active()
    monkeypatch.setitem(active.config["proof"], "hsa_fsa", {"claims": ["no-such-claim"]})
    monkeypatch.setitem(active.config["proof"], "support", {"claims": ["no-such-claim"]})
    pack = LocalFactsSource(TENANT.claims_dir).facts_for(FUJI, BRIEF)
    assert pack["hsa_fsa"] is None and "support" not in pack


def test_owner_decisions_of_2026_10_15_are_claims():
    verified = {c["id"]: c for c in json.loads((TENANT.claims_dir / "verified.json").read_text())}
    assert verified["hsa-fsa-redo"]["text"] == verified["hsa-fsa-truemed"]["text"]
    assert verified["hsa-fsa-truemed"]["superseded_by"] == "hsa-fsa-redo"
    assert not any("truemed" in a.lower() for a in verified["hsa-fsa-truemed"].get("aliases") or ())
    assert verified["pwc-value-588"]["text"] == ("Peak Wellness Club, a $588-per-year membership, is included free "
                                                "with every Peak sauna.")
    assert "owner_confirm" not in json.dumps(load_claims_config(TENANT.claims_dir))


def test_facts_pack_without_live_claims_stays_within_its_size_budget():
    pack = LocalFactsSource(TENANT.claims_dir).facts_for(FUJI, BRIEF)
    assert len(json.dumps(pack)) / 4 < 5000
    pack = LocalFactsSource(TENANT.claims_dir).facts_for(FUJI, BRIEF, live_claims=_live())
    assert len(json.dumps(pack)) / 4 < 6500


# ---------------------------------------------------------------------------
# 6. offer figures
# ---------------------------------------------------------------------------

PROMO = json.loads((TENANT.claims_dir / "verified.json").read_text())


@pytest.mark.parametrize("slug,net,listed,code", [
    (FUJI, "$7,950", "$8,250", "INDOOR300"),
    (PATAGONIA, "$10,450", "$10,950", "OUTDOOR500"),
    (MATTERHORN, None, "$10,750", None),  # no indoor/outdoor word: the whole clause, no single net price
])
def test_offer_view_and_facts_pack_offer_carry_net_list_code_and_short_deadline(slug, net, listed, code):
    pack = LocalFactsSource(TENANT.claims_dir).facts_for(slug, BRIEF)
    brief = {"offer": {"amounts": ["$300", "$500"], "codes": [], "deadline": "October 16", "name": None,
                       "sentence": "$300 off every indoor sauna. $500 off outdoor."}}
    assert offer.apply_to_run(brief, pack, PROMO, today="2026-10-15")
    view = offer.offer_view(pack)
    assert (view["net_price"], view["list_price"], view["code"]) == (net, listed, code)
    assert view["deadline_short"] == "today's price ends Thu, Oct 15"  # prices go up on Fri, Oct 16
    for key in ("net_price", "list_price", "code", "deadline_short"):
        assert pack["offer"][key] == view[key]


def test_deadline_short_follows_the_claim_expiry_rule():
    assert offer.deadline_short("Fall Sale: 10% off, ends November 3, 2026.") == "today's price ends Tue, Nov 3"
    assert offer.deadline_short("Prices go up on October 16, 2026.") == "today's price ends Thu, Oct 15"
    assert offer.deadline_short("No date here.") is None
    assert offer._price_figures({"product": {"price": "8250.00"}}, "10%") == ("$8,250", "$7,425")


# ---------------------------------------------------------------------------
# 8. reading level
# ---------------------------------------------------------------------------

def test_grade_level_matches_flesch_kincaid():
    assert textutil.grade_level("The cat sat on the mat.") == -1.45
    assert textutil.grade_level("") == 0.0
    plain = "Heat up in fifteen minutes. Sit for twenty. Feel warm all evening."
    dense = ("Comprehensive infrared considerations notwithstanding, the manufacturer recommends professional "
             "installation by a qualified electrician.")
    assert textutil.grade_level(plain) < 5 < 12 < textutil.grade_level(dense)
    assert [textutil.syllables(w) for w in ("sauna", "the", "heated", "cabinet", "evening")] == [2, 1, 2, 3, 3]


# ---------------------------------------------------------------------------
# the whole claims step, through `harness run` (FakeClient, no network)
# ---------------------------------------------------------------------------

def test_a_dry_run_grounds_the_new_fields_and_logs_the_quotes(monkeypatch):
    from harness import cli, pipeline
    from tests.conftest import FakeClient, json_response
    from tests.support import newest_run_dir
    from tests.test_cli_run import (AD_BRIEF_RESPONSE, ARTICLE_PAGE, LONGFORM_PAGE, PRODUCT_PAGE_PAGE,
                                    _base_args, _patch_network)

    _patch_network(monkeypatch)
    real_refresh = pipeline.refresh_price_data

    def refresh_with_live_ids(**kw):
        products, by_slug, _live = real_refresh(**kw)
        return products, by_slug, [{"handle": FUJI, "id": FUJI_ID}]

    monkeypatch.setattr(pipeline, "refresh_price_data", refresh_with_live_ids)
    monkeypatch.setattr(judgeme, "http_fetch", fake_fetch)
    client = FakeClient([json_response(AD_BRIEF_RESPONSE), json_response({}), json_response(ARTICLE_PAGE),
                         json_response(PRODUCT_PAGE_PAGE), json_response(LONGFORM_PAGE)])
    monkeypatch.setattr(cli, "make_client", lambda: client)
    assert cli.cmd_run(_base_args()) == 0
    run_dir = newest_run_dir(TENANT.out_dir, "*-hidden-costs-v2-transcript-*")
    pack = json.loads((run_dir / "facts_pack.json").read_text())
    assert pack["reviews_store"]["count"] == 4260
    assert [q["author"] for q in pack["review_quotes"]][:2] == ["Kristi W.", "David H."]
    assert pack["product"]["financing"]["monthly"]["amount"] == "$458.33"
    assert pack["hsa_fsa"]["claim_ids"] == ["hsa-fsa-redo"]
    assert "units" not in pack  # no units cache in an isolated test tenant
    log = "".join(p.read_text() for p in TENANT.runs_dir.glob("*.log"))
    assert "6 review quote(s) from 49 product and 25 store review(s); not quoted:" in log
