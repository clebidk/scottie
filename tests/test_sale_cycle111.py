"""Cycle 111: the sale's deadline from the store's countdown, the expiry sweep,
the non-sale twin and the page fail-safe.

Real data: tests/fixtures/c111/site-promo-2026-10-16.json is the pk_promo_*
slice of the peaksaunas.com homepage fetched 2026-10-16 15:38 UTC (the page
path list pk_promo_exclude is left out), and PROMO is the real promo claim
("... prices go up on October 16, 2026."). The site says the sale ends
2026-10-16T23:30:00-05:00 (Central), i.e. 22:30 on Oct 16 in America/Denver,
the tenant's timezone. Clocks are explicit `now=` values; no network.
"""
import copy
import datetime
import json
import re
import shutil

import pytest

from harness import abtest, capture, cli, jobs, landing_sell, offer, offer_failsafe, perf, pipeline, runstate, sale_sync
from harness.publishers.shopify import ShopifyPublisher
from tests.support import REPO_ROOT, TENANT
from tests.test_offer_cycle109 import AIIR
from tests.test_presell_cycle110 import PROMO, _offer_facts, _page, _render

pytestmark = pytest.mark.frozen_tenant  # Cycle 113: the claims and tenant.yaml of 9bdb996 (tests/conftest.py)

FIXTURE = json.loads((REPO_ROOT / "tests" / "fixtures" / "c111" / "site-promo-2026-10-16.json").read_text())
END = "2026-10-16T23:30:00-05:00"
UTC = datetime.timezone.utc
FETCHED = datetime.datetime(2026, 10, 16, 15, 38, tzinfo=UTC)


def _home(values=None):
    """A homepage the way the theme prints it: the settings JSON with
    escaped slashes, among other theme keys."""
    data = dict(FIXTURE, **(values or {}))
    inner = json.dumps(data)[1:-1].replace("/", "\\/")
    return ('<html><head><script>window.theme={"input_style":"solid","pk_style":"peak",' + inner +
            ',"pk_promo_exclude":"\\/pages\\/adv"};</script></head><body>home</body></html>')


def _record(values=None, fetched=FETCHED):
    raw = sale_sync.parse_promo(_home(values))
    return sale_sync.normalize(raw, tz=offer.tenant_tz(TENANT), source_url="https://peaksaunas.com/", fetched_at=fetched)


@pytest.fixture
def claims_tmp(tmp_path, monkeypatch):
    """claims/ copied to tmp (the real verified.json, the real promo claim)."""
    root = tmp_path / "claims"
    shutil.copytree(TENANT.claims_dir, root)
    monkeypatch.setattr(type(TENANT), "claims_dir", property(lambda self: root))
    return root


def _promo(root):
    return next(c for c in json.loads((root / "verified.json").read_text()) if c["id"] == PROMO["id"])


# ---------------------------------------------------------------------------
# parse, timezone
# ---------------------------------------------------------------------------

def test_parse_reads_the_real_promo_settings_and_not_the_page_paths():
    raw = sale_sync.parse_promo(_home())
    assert raw["enabled"] is True and raw["end"] == END and raw["after_end"] == "hide"
    assert raw["cta_url"] == "/collections/saunas" and "exclude" not in raw and "style" not in raw
    assert raw["message"] == "Community Savings Sale: $300 off indoor with INDOOR300 · $500 off outdoor with OUTDOOR500"


def test_the_central_end_is_the_denver_last_day_oct_16():
    rec = _record()
    assert rec["end"] == END and rec["end_local"] == "2026-10-16T22:30:00-06:00"
    assert rec["last_sale_day"] == "2026-10-16" and rec["timezone"] == "America/Denver"
    assert (rec["amounts"], rec["codes"]) == (["$300", "$500"], ["INDOOR300", "OUTDOOR500"])
    assert rec["fetched_at"] == "2026-10-16T15:38:00+00:00"


def test_the_synced_claim_says_tonight_on_oct_16_and_friday_on_oct_15(monkeypatch):
    # before the sync the claim's own text ends the sale on Oct 15: on Oct 16 no page is offer-led
    assert offer.claim_expired(PROMO, "2026-10-16") and not offer.claim_expired(PROMO, "2026-10-15")
    claim = dict(PROMO, **sale_sync.deadline_update(PROMO, _record()))
    assert claim["text"] == PROMO["text"]            # "prices go up on October 16, 2026" stays true
    assert (claim["last_sale_day"], claim["deadline_end"]) == ("2026-10-16", END)
    assert not offer.claim_expired(claim, "2026-10-16") and offer.claim_expired(claim, "2026-10-17")
    # the instant: 22:29 Denver still on sale, 22:31 over
    denver = offer.tenant_tz(TENANT)
    assert not offer.claim_expired(claim, "2026-10-16", datetime.datetime(2026, 10, 16, 22, 29, tzinfo=denver))
    assert offer.claim_expired(claim, "2026-10-16", datetime.datetime(2026, 10, 16, 22, 31, tzinfo=denver))
    fp, brief = _offer_facts()
    assert offer.apply_to_run(brief, fp, [claim], today="2026-10-16")
    assert fp["offer"]["last_sale_day"] == "2026-10-16" and offer.expires_iso(fp["offer"], TENANT) == END
    monkeypatch.setattr(offer, "render_today", lambda tenant=None: datetime.date(2026, 10, 16))
    assert landing_sell.deadline(fp)["day"] == "tonight"
    assert landing_sell.token_values(fp)["deadline_day"]["text"] == "tonight"
    monkeypatch.setattr(offer, "render_today", lambda tenant=None: datetime.date(2026, 10, 15))
    assert landing_sell.deadline(fp)["day"] == "Friday"
    assert offer.offer_view(fp, today="2026-10-17") is None


# ---------------------------------------------------------------------------
# sync
# ---------------------------------------------------------------------------

def test_sync_dry_run_writes_nothing_then_the_real_sync_updates_the_deadline_only(claims_tmp):
    before = (claims_tmp / "verified.json").read_text()
    lines = []
    result = sale_sync.sync(TENANT, dry_run=True, fetch=lambda url: _home(), now=FETCHED, log=lines.append)
    assert result["action"] == "would update" and result["claim_id"] == PROMO["id"]
    assert (claims_tmp / "verified.json").read_text() == before
    assert not sale_sync.site_path(TENANT).exists()
    assert any("2026-10-15 -> 2026-10-16" in line and "dry run" in line for line in lines)
    result = sale_sync.sync(TENANT, fetch=lambda url: _home(), now=FETCHED, log=lines.append)
    claim = _promo(claims_tmp)
    assert result["action"] == "updated" and claim["text"] == PROMO["text"] and claim["id"] == PROMO["id"]
    assert claim["last_sale_day"] == "2026-10-16" and claim["deadline_end"] == END
    assert claim["source"].endswith("; deadline from site promo bar, fetched 2026-10-16T15:38:00+00:00")
    saved = json.loads(sale_sync.site_path(TENANT).read_text())
    assert saved["claim_id"] == PROMO["id"] and saved["end"] == END
    # every other claim is byte-for-byte the same
    old = {c["id"]: c for c in json.loads(before)}
    assert all(c == old[c["id"]] for c in json.loads((claims_tmp / "verified.json").read_text())
               if c["id"] != PROMO["id"])
    assert sale_sync.sync(TENANT, fetch=lambda url: _home(), now=FETCHED, log=lines.append)["action"] == "unchanged"


def test_the_cli_dry_run_prints_the_record_and_writes_nothing(claims_tmp, monkeypatch, capsys):
    monkeypatch.setattr(sale_sync, "fetch_site", lambda url: _home())
    before = (claims_tmp / "verified.json").read_text()
    assert cli.main(["sale", "sync", "--dry-run", "--tenant", TENANT.name]) == 0
    out = capsys.readouterr().out
    assert '"end": "2026-10-16T23:30:00-05:00"' in out and "would" not in before
    assert (claims_tmp / "verified.json").read_text() == before and not sale_sync.site_path(TENANT).exists()


def test_an_extension_moves_the_date_in_the_claim_text(claims_tmp):
    later = "2026-10-20T23:30:00-05:00"
    sale_sync.sync(TENANT, fetch=lambda url: _home({"pk_promo_end": later}), now=FETCHED, log=lambda *_: None)
    claim = _promo(claims_tmp)
    assert claim["text"].endswith("prices go up on October 20, 2026.") and claim["last_sale_day"] == "2026-10-20"
    assert claim["text"].replace("October 20", "October 16") == PROMO["text"]


def test_a_different_amount_on_the_site_stops_the_sync(claims_tmp):
    before = (claims_tmp / "verified.json").read_text()
    lines = []
    message = "Community Savings Sale: $400 off indoor with INDOOR400 · $500 off outdoor with OUTDOOR500"
    result = sale_sync.sync(TENANT, fetch=lambda url: _home({"pk_promo_message": message}), now=FETCHED,
                            log=lines.append)
    assert result["action"] == "stopped" and (claims_tmp / "verified.json").read_text() == before
    assert any("WARNING" in line and "$400" in line for line in lines)
    assert "claim_id" not in json.loads(sale_sync.site_path(TENANT).read_text())   # never used for a run's expiry
    result = sale_sync.sync(TENANT, accept_site_offer=True, fetch=lambda url: _home({"pk_promo_message": message}),
                            now=FETCHED, log=lines.append)
    claim = _promo(claims_tmp)
    assert result["action"] == "updated" and "$400 off indoor saunas with code INDOOR400" in claim["text"]
    assert "$500 off outdoor saunas with code OUTDOOR500" in claim["text"]


def test_the_bar_switched_off_ends_the_claim_when_it_was_seen(claims_tmp):
    seen = datetime.datetime(2026, 10, 16, 18, 0, tzinfo=UTC)   # noon in Denver, before the countdown
    off = lambda url: _home({"pk_promo_enabled": False})  # noqa: E731
    # the unsynced claim already ended on Oct 15 by its own text: nothing to change
    assert sale_sync.sync(TENANT, fetch=off, now=seen, log=lambda *_: None)["action"] == "unchanged"
    sale_sync.sync(TENANT, fetch=lambda url: _home(), now=FETCHED, log=lambda *_: None)   # synced: Oct 16
    sale_sync.sync(TENANT, fetch=off, now=seen, log=lambda *_: None)
    claim = _promo(claims_tmp)
    assert claim["deadline_end"] == "2026-10-16T18:00:00+00:00" and claim["last_sale_day"] == "2026-10-15"
    assert claim["text"] == PROMO["text"] and "sale off on site promo bar" in claim["source"]


# ---------------------------------------------------------------------------
# a run's offer_expires
# ---------------------------------------------------------------------------

def _run(tmp_path, name="20261016-aiir-abcd"):
    run_dir = TENANT.out_dir / name
    (run_dir / "landing").mkdir(parents=True)
    runstate.init_state(run_dir, pages=["landing"])
    return run_dir


def test_a_run_takes_the_site_end_when_the_record_is_fresh_else_the_claim(tmp_path):
    fp, _brief = _offer_facts()
    run_dir = _run(tmp_path)
    rec = dict(_record(), claim_id=PROMO["id"])
    sale_sync._write_json(sale_sync.site_path(TENANT), rec)
    assert sale_sync.record_run_expiry(TENANT, run_dir, fp, now=FETCHED + datetime.timedelta(hours=2)) == END
    state = runstate.load_state(run_dir)
    assert (state["offer_expires"], state["offer_claim_id"]) == (END, PROMO["id"]) and fp["offer"]["expires"] == END
    # 25 hours later the record is stale: the claim's own day (prices go up Oct 16 -> ends Oct 16 00:00 Denver)
    fp, _brief = _offer_facts()
    stale = FETCHED + datetime.timedelta(hours=25)
    assert sale_sync.record_run_expiry(TENANT, run_dir, fp, now=stale) == "2026-10-16T00:00:00-06:00"


# ---------------------------------------------------------------------------
# the sweep
# ---------------------------------------------------------------------------

def _live(tmp_path, *, expires=END, kind="abtest", twin=False):
    run_dir = _run(tmp_path, f"20261014-aiir-{kind}")
    (run_dir / "landing" / "index.html").write_text("<html><body>sale page</body></html>")
    (run_dir / "landing" / "page.json").write_text(json.dumps({"look": "peak", "hero_style": "peak_first"}))
    fp, brief = _offer_facts()
    (run_dir / "facts_pack.json").write_text(json.dumps(fp))
    (run_dir / "ad_brief.json").write_text(json.dumps(dict(brief, source_file="meta-1.jpg")))
    data = runstate.load_state(run_dir)
    data.update(offer_expires=expires, offer_claim_id=PROMO["id"])
    runstate.save_state(run_dir, data)
    runstate.mark_published(run_dir, page="landing", by="caleb@peaksaunas.com", page_id=500,
                            handle=f"lp-aiir-{kind}", url=f"https://peaksaunas.com/pages/lp-aiir-{kind}")
    if twin:
        (run_dir / "landing-nosale").mkdir()
        (run_dir / "landing-nosale" / "index.html").write_text("<html><body>no-sale page</body></html>")
    if kind == "abtest":
        runstate.record_abtest(run_dir, page="landing", test_id="aiir-t1", key="A")
        abtest.save_test(TENANT, {"test_id": "aiir-t1", "status": "live", "seed": 1, "input": "x.jpg", "variants": [
            {"key": "A", "arm": "landing:peak:peak_first", "cartridge": "landing", "run_dir": str(run_dir),
             "status": "built", "shopify": {"page_id": 500, "handle": "lp-aiir-abtest",
                                            "url": "https://peaksaunas.com/pages/lp-aiir-abtest"}}]})
    else:
        runstate.record_unlinked(run_dir, page="landing", by="caleb@peaksaunas.com", action="post")
    return run_dir


def test_decide_wait_extend_end_unknown():
    item = {"offer_expires": END}
    before, after = datetime.datetime(2026, 10, 17, 4, 0, tzinfo=UTC), datetime.datetime(2026, 10, 17, 4, 40, tzinfo=UTC)
    assert sale_sync.decide(item, _record(), before)["action"] == "wait"
    later = _record({"pk_promo_end": "2026-10-20T23:30:00-05:00"})
    assert sale_sync.decide(item, later, after) == {"action": "extend", "end": "2026-10-20T23:30:00-05:00",
                                                   "end_local": "2026-10-20T22:30:00-06:00"}
    assert sale_sync.decide(item, _record({"pk_promo_enabled": False}), after)["action"] == "end"
    assert sale_sync.decide(item, _record(), after)["action"] == "end"            # end passed, after_end hide
    assert sale_sync.decide(item, _record({"pk_promo_after_end": "show"}), after)["action"] == "unknown"
    assert sale_sync.decide(item, None, after)["action"] == "unknown"


def test_the_sweep_queues_one_job_per_page_and_never_twice(tmp_path, claims_tmp):
    arm = _live(tmp_path, kind="abtest")
    post = _live(tmp_path, kind="postlive")
    items = sale_sync.live_offer_pages(TENANT)
    assert sorted((i["kind"], i["run_id"]) for i in items) == [("abtest", arm.name), ("postlive", post.name)]
    after = datetime.datetime(2026, 10, 17, 4, 40, tzinfo=UTC)
    lines = []
    out = sale_sync.sweep(TENANT, now=after, fetch=lambda url: _home(), dry_run=True, log=lines.append)
    assert out["due"] == 2 and not jobs.list_jobs(TENANT) and any("would queue sale_end" in x for x in lines)
    out = sale_sync.sweep(TENANT, now=after, fetch=lambda url: _home(), log=lines.append)
    queued = jobs.list_jobs(TENANT)
    assert sorted((j["type"], j["target"]) for j in queued) == [("sale_end", f"run:{arm.name}/landing"),
                                                                 ("sale_end", f"run:{post.name}/landing")]
    assert runstate.load_state(arm)["sale_expiry"]["status"] == "ending"
    sale_sync.sweep(TENANT, now=after, fetch=lambda url: _home(), log=lines.append)
    assert len(jobs.list_jobs(TENANT)) == 2
    # before the end nothing is fetched
    assert sale_sync.sweep(TENANT, now=FETCHED, fetch=lambda url: 1 / 0, log=lines.append)["due"] == 0


def test_the_sweep_extends_when_the_countdown_moved(tmp_path, claims_tmp):
    arm = _live(tmp_path, kind="abtest")
    after = datetime.datetime(2026, 10, 17, 4, 40, tzinfo=UTC)
    later = "2026-10-20T23:30:00-05:00"
    lines = []
    sale_sync.sweep(TENANT, now=after, fetch=lambda url: _home({"pk_promo_end": later}), log=lines.append)
    [job] = jobs.list_jobs(TENANT)
    assert job["type"] == "sale_extend" and job["payload"]["end"] == later
    assert any(f"run {arm.name} page landing" in x and "sale extended to 2026-10-20T22:30:00-06:00" in x
               for x in lines)
    assert _promo(claims_tmp)["last_sale_day"] == "2026-10-20"


def test_the_extend_job_re_renders_in_place_with_the_new_deadline(tmp_path, claims_tmp, monkeypatch):
    later = "2026-10-20T23:30:00-05:00"
    sale_sync.sync(TENANT, fetch=lambda url: _home({"pk_promo_end": later}), now=FETCHED, log=lambda *_: None)
    run_dir = _live(tmp_path, kind="abtest")
    fp, brief = _offer_facts()
    page = _page(cta_text="Shop the sale")
    page["hero"]["headline"] = "{offer_amount} off the Fuji, today's price ends {deadline_day}"
    page["look"] = "peak"
    from harness.render import render_page
    render_page(cartridge_name="landing", page=page, ad_brief=brief, facts_pack=fp, cartridges_dir=REPO_ROOT /
                "cartridges", brand_dir=TENANT.brand_dir, templates_dir=REPO_ROOT / "harness" / "templates",
                out_dir=run_dir / "landing", published="2026-10-14", updated="2026-10-14", tenant=TENANT,
                download_assets=False)
    assert "Prices go up on October 16, 2026." in (run_dir / "landing" / "index.html").read_text()
    published = []
    monkeypatch.setattr(sale_sync, "_publish", lambda tenant, rd, payload, note: published.append(note) or {})
    payload = {"run_id": run_dir.name, "page": "landing", "kind": "abtest", "test_id": "aiir-t1", "key": "A",
               "by": "caleb@peaksaunas.com", "end": later, "end_local": "2026-10-20T22:30:00-06:00"}
    sale_sync.handle_sale_extend(TENANT, payload)
    html = (run_dir / "landing" / "index.html").read_text()
    assert "Prices go up on October 20, 2026." in html and "October 16" not in html
    assert f'data-offer-expires="{later}"' in html
    state = runstate.load_state(run_dir)
    assert state["offer_expires"] == later and state["sale_expiry"]["status"] == "extended"
    assert json.loads((run_dir / "facts_pack.json").read_text())["offer"]["last_sale_day"] == "2026-10-20"
    assert published == ["sale extended to 2026-10-20T22:30:00-06:00"]


def test_the_end_job_prefers_the_twin(tmp_path, monkeypatch):
    run_dir = _live(tmp_path, kind="abtest", twin=True)
    calls = []
    monkeypatch.setattr(sale_sync, "_publish", lambda tenant, rd, payload, note: calls.append((rd, note)) or
                        {"url": "https://peaksaunas.com/pages/lp-aiir-abtest"})
    payload = {"run_id": run_dir.name, "page": "landing", "kind": "abtest", "test_id": "aiir-t1", "key": "A",
               "by": "caleb@peaksaunas.com"}
    result = sale_sync.handle_sale_end(TENANT, payload)
    assert result["via"] == "twin" and calls == [(run_dir, "sale ended: switched to the non-sale twin")]
    assert (run_dir / "landing" / "index.html").read_text() == "<html><body>no-sale page</body></html>"
    assert (run_dir / "landing-sale" / "index.html").read_text() == "<html><body>sale page</body></html>"
    assert runstate.load_state(run_dir)["sale_expiry"]["status"] == "switched"
    assert not jobs.list_jobs(TENANT)


class _Publisher(ShopifyPublisher):
    def __init__(self, redirects=None):   # no store, no token: records the calls; redirects by path
        self.calls, self.redirects = [], dict(redirects or {})

    def update_page(self, page_id, page, *, unpublished=True):
        self.calls.append(("update_page", page_id, unpublished))

    def _find_redirect_by_path(self, path):
        self.calls.append(("find_redirect", path))
        return self.redirects.get(path)

    def create_redirect(self, path, target):
        self.calls.append(("create_redirect", path, target))
        self.redirects[path] = {"id": 77, "path": path, "target": target}
        return self.redirects[path]

    def delete_redirect(self, redirect_id):
        self.calls.append(("delete_redirect", redirect_id))


def test_with_no_twin_the_page_is_hidden_redirected_and_rebuilt(tmp_path, monkeypatch):
    from tests.support import tenant_copy

    tenant_copy(monkeypatch, {"sale_sweep_rebuild": True})   # the template default
    run_dir = _live(tmp_path, kind="postlive")
    pub = _Publisher()
    monkeypatch.setattr(cli, "_make_publisher", lambda tenant, *, export_dir: pub)
    monkeypatch.setattr(type(TENANT), "load_env", lambda self: None)
    payload = {"run_id": run_dir.name, "page": "landing", "kind": "postlive", "test_id": None, "key": None,
               "by": "caleb@peaksaunas.com"}
    result = sale_sync.handle_sale_end(TENANT, payload)
    product = "/products/" + json.loads((run_dir / "facts_pack.json").read_text())["product"]["url"].split("/products/")[1]
    assert pub.calls == [("update_page", 500, True), ("find_redirect", "/pages/lp-aiir-postlive"),
                         ("create_redirect", "/pages/lp-aiir-postlive", product)]
    assert result["via"] == "redirect"
    assert runstate.load_state(run_dir)["sale_expiry"]["redirect"] == {"from": "/pages/lp-aiir-postlive",
                                                                       "to": product, "id": 77}
    [job] = jobs.list_jobs(TENANT)
    assert job["type"] == "sale_rebuild" and job["target"] == f"run:{run_dir.name}/landing"
    assert sale_sync.card_line(runstate.load_state(run_dir)) == "rebuilding"


def test_card_lines():
    assert sale_sync.card_line({"sale_expiry": {"status": "switched"}}) == "sale ended: switched"
    assert sale_sync.card_line({"sale_expiry": {"status": "extended"}}) == "sale extended"
    assert sale_sync.card_line({}) == ""


def test_the_worker_clock_sweeps_when_due_only(tmp_path):
    seen = []
    clock = sale_sync.SweepClock(TENANT, sweeper=lambda tenant, now, log: seen.append(now), log=lambda *_: None)
    clock(FETCHED)
    clock(FETCHED + datetime.timedelta(minutes=30))
    clock(FETCHED + datetime.timedelta(minutes=61))
    assert seen == [FETCHED, FETCHED + datetime.timedelta(minutes=61)]
    # a live offer ending in 20 minutes: the next sweep is 10 minutes after it
    _live(tmp_path, expires=(FETCHED + datetime.timedelta(minutes=20)).isoformat())
    assert sale_sync.next_sweep_at(TENANT, FETCHED) == FETCHED + datetime.timedelta(minutes=30)


# ---------------------------------------------------------------------------
# the page fail-safe
# ---------------------------------------------------------------------------

def _offer_page_html(tmp_path, cta="Shop the sale"):
    fp, brief = _offer_facts()
    fp["offer"]["expires"] = END
    page = _page(cta_text=cta)
    page["hero"]["headline"] = "{offer_amount} off the Fuji, today's price ends {deadline_day}"
    return _render(tmp_path, page, fp, brief)[0]


def test_the_offer_page_carries_the_instant_and_the_script(tmp_path):
    html = _offer_page_html(tmp_path)
    assert html.count(offer_failsafe.SCRIPT) == 1 and len(offer_failsafe.SCRIPT.splitlines()) <= 25
    assert "fetch(" not in offer_failsafe.SCRIPT and "src=" not in offer_failsafe.SCRIPT
    marked = re.findall(r"<(\w+)\b[^>]*data-offer-expires=\"" + re.escape(END) + r"\"[^>]*>", html)
    assert marked, "no element carries the instant"
    tags = re.findall(r"<[^>]*data-offer-expires[^>]*>", html)
    assert any("data-offer-cta=\"Shop the Fuji\"" in t for t in tags)              # the sale CTA
    assert any("data-offer-list=\"$8,250\"" in t and "data-offer-net=\"$7,950\"" in t for t in tags)  # price line
    hidden = [t for t in tags if "data-offer-cta" not in t and "data-offer-list" not in t]
    assert hidden                                                                   # offer lines, bar, note
    assert not any(t.startswith("<h1") for t in tags)


def test_an_offer_free_page_has_no_fail_safe(tmp_path):
    html, _ = _render(tmp_path, _page())
    assert "data-offer-expires" not in html and offer_failsafe.SCRIPT not in html


def test_mark_on_the_shared_offer_markup():
    view = {"line": "$300 off with code INDOOR300", "deadline": "Prices go up on October 16, 2026.",
            "cta_note": "Offer applied at checkout with the code.", "bar": "Code INDOOR300", "codes": ["INDOOR300"],
            "name": "Community Savings Sale", "check": {"fact": PROMO["text"]}}
    page = ('<html><head></head><body><p class="eyebrow">Community Savings Sale</p><h1>Community Savings Sale on '
            'the Fuji</h1><p class="ld-price"><b>$8,250</b><span class="ld-offer">$300 off with code INDOOR300</span>'
            '<span class="ld-deadline">Prices go up on October 16, 2026.</span><span>$458/mo*</span></p>'
            '<a class="ld-btn" href="/products/fuji">Get $300 off the Fuji</a>'
            '<p class="ld-offer-note">Offer applied at checkout with the code.</p>'
            '<div class="ld-bar"><p>Fuji<span>$8,250 &middot; Code INDOOR300</span></p>'
            '<a class="ld-btn" href="/products/fuji">Shop now</a></div>'
            f'<ul><li><p>The ad said: $300 off</p><p>{PROMO["text"]}</p></li><li><p>Other claim</p></li></ul>'
            '</body></html>')
    out = offer_failsafe.mark(page, view, END, default_cta="Shop the Fuji",
                              sale_ctas=["Get $300 off the Fuji", "Shop the sale"])
    tagged = re.findall(r"<(\w+)([^>]*data-offer-expires[^>]*)>", out)
    classes = [(tag, re.search(r'class="([^"]*)"', attrs).group(1) if "class=" in attrs else "") for tag, attrs in tagged]
    assert ("p", "eyebrow") in classes and ("span", "ld-offer") in classes and ("span", "ld-deadline") in classes
    assert ("p", "ld-offer-note") in classes and ("a", "ld-btn") in classes and ("li", "") in classes
    assert ("span", "") in classes            # the sticky bar's code
    assert not any(tag == "h1" for tag, _ in tagged) and out.count("data-offer-cta") == 1
    assert out.count("<li data-offer-expires") == 1   # only the offer's checked claim
    assert offer_failsafe.mark(out, view, END, default_cta="Shop the Fuji") == out   # idempotent


def test_the_listicle_offer_lines_carry_the_instant(tmp_path):
    from tests.test_first_screen_cycle79 import _page as listicle_page
    from tests.test_listicle import RICH_FACTS_PACK
    from tests.test_offer_cycle109 import TODAY
    from tests.test_offer_cycle109 import _render as render109
    from tests.test_render import AD_BRIEF

    fp = copy.deepcopy(RICH_FACTS_PACK)
    fp["verified_claims"] += [
        {"id": "pdp-fuji-cabin", "text": "Canadian red cedar cabin, inside and out.", "category": "spec",
         "source": "https://peaksaunas.com/products/fuji"},
        {"id": "price-fuji", "text": "The Peak Fuji is priced at $8,250.", "category": "price",
         "source": "https://peaksaunas.com/products/fuji"},
    ]
    brief = dict(AD_BRIEF, offer=offer.extract_offer(AIIR))
    offer.apply_to_run(brief, fp, [PROMO], today=TODAY)
    html, _ = render109(tmp_path, listicle_page("story"), fp, brief, cartridge="listicle")
    view = offer.offer_view(fp)
    tree = offer_failsafe._Tree(html)
    tree.feed(html)
    for needle in (view["line"], view["deadline"].rstrip("."), "Community Savings Sale: "):
        holders = [n for n in tree.nodes if n["body"] and needle in offer_failsafe._text(n) + " "
                   and not any(needle in offer_failsafe._text(c) + " " for c in n["children"])]
        assert holders, needle
        for node in holders:   # the innermost holder, or an element around it, carries the instant
            up = node
            while up is not None and "data-offer-expires" not in html[up["start"]:up["tag_end"]]:
                up = up["parent"]
            assert up is not None, (needle, html[node["start"]:node["tag_end"]])
    assert html.count(offer_failsafe.SCRIPT) == 1


@pytest.mark.live          # the load-budget measure talks to its own 127.0.0.1 server: run with `-m live`
@pytest.mark.real_browser
def test_lcp_and_cls_with_the_fail_safe_before_and_after_the_instant(tmp_path):
    browser = capture.find_browser()
    if not browser:
        pytest.skip("needs a real browser")
    for expires in (END, "2020-01-01T00:00:00+00:00"):
        fp, brief = _offer_facts()
        fp["offer"]["expires"] = expires
        _render(tmp_path / expires[:4], _page(cta_text="Shop the sale"), fp, brief)
        page_dir = tmp_path / expires[:4] / "run" / "landing"
        measured = perf.measure(browser, page_dir / "index.html")
        assert measured is not None
        static = perf.static_budget(page_dir)
        assert measured["cls"] <= perf.CLS_MAX and measured["lcp_ms"] <= perf.LCP_MAX_MS, measured
        assert static["html_bytes"] <= perf.HTML_MAX_BYTES and static["script_src"] == 0


# ---------------------------------------------------------------------------
# the non-sale twin
# ---------------------------------------------------------------------------

def test_without_offer_removes_the_offer_and_marks_it_unverified():
    fp, brief = _offer_facts()
    twin_fp, twin_brief = offer.without_offer(fp, brief)
    assert "offer" not in twin_fp and offer.offer_view(twin_fp) is None
    assert PROMO["id"] not in {c["id"] for c in twin_fp["verified_claims"]}
    assert twin_fp["offer_unverified"]["codes"] == fp["offer"]["codes"]
    assert "claim_id" not in twin_brief["offer"] and brief["offer"]["claim_id"] == PROMO["id"]
    assert offer.writer_lines(twin_fp) == [offer.UNVERIFIED_LINE]


def test_the_twin_stage_is_off_without_the_flag(monkeypatch):
    class S:
        tenant = TENANT
        facts_pack = _offer_facts()[0]
        today_iso = "2026-10-15"
    monkeypatch.setenv("HARNESS_SALE_TWIN", "off")
    called = []
    monkeypatch.setattr(pipeline, "_write_sale_twin", lambda state, c: called.append(c))
    pipeline.sale_twin(S)
    assert called == []
    monkeypatch.delenv("HARNESS_SALE_TWIN")
    S.pages = {"landing": {}}
    pipeline.sale_twin(S)
    assert called == ["landing"] and TENANT.get("sale_twin") is True
    assert pipeline.DEFAULT_STAGES.index("sale_twin") == pipeline.DEFAULT_STAGES.index("design_gate") + 1


def test_the_aiir_ad_offer_still_matches_the_synced_claim():
    claim = dict(PROMO, **sale_sync.deadline_update(PROMO, _record()))
    assert offer.match_offer(offer.extract_offer(AIIR), [claim], "2026-10-16") is claim
    assert copy.deepcopy(claim)["deadline_end"] == END


def test_a_fake_run_of_the_sale_ad_writes_the_non_sale_twin(tmp_path, monkeypatch):
    """ingest -> ground -> write -> render -> sale_twin with FakeClient: the
    sale page is offer-led, landing-nosale/ is the same look with no offer,
    and the twin's cost is its own ledger line."""
    from evals import fake_run
    from harness import budget as budget_mod
    from tests.conftest import json_response

    root = tmp_path / "claims"
    shutil.copytree(TENANT.claims_dir, root)
    verified = json.loads((root / "verified.json").read_text())
    for c in verified:
        if c["category"] == "promo":
            c["text"] = c["text"].replace("2026", "2099")
    (root / "verified.json").write_text(json.dumps(verified, indent=2) + "\n")
    monkeypatch.setattr(type(TENANT), "claims_dir", property(lambda self: root))
    monkeypatch.delenv("HARNESS_SALE_TWIN")
    fixture = tmp_path / "aiir-sale.txt"
    fixture.write_text(AIIR + "\n")
    fixed = dict(copy.deepcopy(fake_run.LANDING_PAGE), cta_text="Shop the sale", hero=dict(
        fake_run.LANDING_PAGE["hero"], headline="Money off every indoor sauna during the sale", accent_phrase="Money off"))
    twin = dict(copy.deepcopy(fake_run.LANDING_PAGE), cta_text="Shop the Fuji")
    rc, run_dir, _pages = fake_run.run_once(str(fixture), tenant="peak-saunas", cartridges="landing", look="open",
                                            extra_responses=[json_response(fixed), json_response(twin)])
    assert rc == 0
    state = runstate.load_state(run_dir)
    log = next(p for p in TENANT.runs_dir.glob("*.log") if not p.name.endswith("-nosale.log")).read_text()
    assert "sale_twin: landing-nosale:" in log, log[-2000:]
    assert state["sale_twin"]["landing"]["status"] == "pass", state["sale_twin"]
    assert state["offer_claim_id"] == PROMO["id"] and state["offer_expires"] == "2099-10-16T00:00:00-06:00"
    sale = (run_dir / "landing" / "index.html").read_text()
    nosale = (run_dir / "landing-nosale" / "index.html").read_text()
    assert "INDOOR300" in sale and "data-offer-expires" in sale
    assert "INDOOR300" not in nosale and "Prices go up" not in nosale and "data-offer-expires" not in nosale
    assert json.loads((run_dir / "landing-nosale" / "page.json").read_text())["look"] == "open"
    # Q3: the sale page's fail-safe carries the twin's h1 and CTA
    twin_h1, twin_cta = offer_failsafe.nosale_texts(run_dir / "landing-nosale")
    assert twin_h1 and twin_cta == "Shop the Fuji"
    h1 = re.search(r"<h1\b[^>]*>", sale).group(0)
    assert "data-offer-expires" in h1 and f'data-nosale-headline="{offer_failsafe.html_mod.escape(twin_h1)}"' in h1
    ctas = re.findall(r"<a\b[^>]*data-offer-cta=[^>]*>", sale)
    assert ctas and all('data-nosale-cta="Shop the Fuji"' in a for a in ctas)
    ledger = [json.loads(x) for x in budget_mod.spend_ledger_path(TENANT).read_text().splitlines() if x.strip()]
    assert any(e.get("run_id") == f"{run_dir.name}-landing-nosale" and "cost_estimate" in e for e in ledger)


# ---------------------------------------------------------------------------
# owner decisions 2026-10-16
# ---------------------------------------------------------------------------

LWTR = REPO_ROOT / "tests" / "fixtures" / "c111" / "lwtr-a"   # live arm A of aiir-sale-300-indoor-...-lwtr (Oct 14)


def _real_arm():
    """The real run of a live AIIR arm (index.html, page.json, facts_pack.json,
    ad_brief.json as on prod), published and in a live test; no
    offer_expires in state.json (built before cycle 111)."""
    run_dir = TENANT.out_dir / "20261014-161608-meta-120259310328070746-ks3s"
    (run_dir / "landing").mkdir(parents=True)
    for name in ("index.html", "page.json"):
        shutil.copy(LWTR / name, run_dir / "landing" / name)
    for name in ("facts_pack.json", "ad_brief.json"):
        shutil.copy(LWTR / name, run_dir / name)
    runstate.init_state(run_dir, pages=["landing"])
    runstate.mark_published(run_dir, page="landing", by="caleb@peaksaunas.com", page_id=165391204653,
                            handle="lp-aiir-sale-300-indoor-static-oct-w2-homep-lwtr-a",
                            url="https://peaksaunas.com/pages/lp-aiir-sale-300-indoor-static-oct-w2-homep-lwtr-a")
    runstate.record_abtest(run_dir, page="landing", test_id="aiir-lwtr", key="A")
    abtest.save_test(TENANT, {"test_id": "aiir-lwtr", "status": "live", "seed": 1, "input": "x.jpg", "variants": [
        {"key": "A", "arm": "landing:cinematic:caps_over_photo", "cartridge": "landing", "run_dir": str(run_dir),
         "status": "built", "shopify": {"page_id": 165391204653, "handle": "lp-aiir-sale-300-indoor-static-oct-w2-"
                                        "homep-lwtr-a", "url": "https://peaksaunas.com/pages/lp-aiir-sale-300-indoor-"
                                        "static-oct-w2-homep-lwtr-a"}}]})
    return run_dir


def test_q1_the_real_aiir_arm_is_a_no_op_before_the_countdown_ends(tmp_path, claims_tmp, monkeypatch):
    monkeypatch.setattr(offer, "render_today", lambda tenant=None: datetime.date(2026, 10, 16))
    run_dir = _real_arm()
    before = (run_dir / "landing" / "index.html").read_bytes()
    assert b"Prices go up on October 16, 2026." in before and b"data-offer-expires" not in before
    [item] = sale_sync.live_offer_pages(TENANT)
    assert item["offer_expires"] == "2026-10-16T00:00:00-06:00" and item["expires_from"] == "facts_pack"
    noon = datetime.datetime(2026, 10, 16, 18, 0, tzinfo=UTC)          # 12:00 MDT, before 22:30
    lines = []
    out = sale_sync.sweep(TENANT, now=noon, fetch=lambda url: _home(), log=lines.append)
    assert [d["action"] for _i, d, _j in out["decisions"]] == ["unchanged"]
    assert any("extend: unchanged" in x for x in lines) and not jobs.list_jobs(TENANT)
    assert (run_dir / "landing" / "index.html").read_bytes() == before            # no render, no publish
    state = runstate.load_state(run_dir)
    assert state["offer_expires"] == END and state["sale_expiry"]["status"] == "unchanged"
    assert json.loads((run_dir / "facts_pack.json").read_text())["offer"]["last_sale_day"] == "2026-10-16"
    # the next sweep before 22:30 MDT has nothing due; at 22:40 MDT the sale has ended
    assert sale_sync.sweep(TENANT, now=noon + datetime.timedelta(hours=1), fetch=lambda url: 1 / 0,
                           log=lines.append)["due"] == 0
    late = datetime.datetime(2026, 10, 17, 4, 40, tzinfo=UTC)
    sale_sync.sweep(TENANT, now=late, fetch=lambda url: _home(), log=lines.append)
    assert [j["type"] for j in jobs.list_jobs(TENANT)] == ["sale_end"]


def test_q1_a_moved_countdown_that_changes_the_sentence_still_re_renders(tmp_path, claims_tmp, monkeypatch):
    monkeypatch.setattr(offer, "render_today", lambda tenant=None: datetime.date(2026, 10, 16))
    run_dir = _real_arm()
    later = "2026-10-20T23:30:00-05:00"
    noon = datetime.datetime(2026, 10, 16, 18, 0, tzinfo=UTC)
    sale_sync.sweep(TENANT, now=noon, fetch=lambda url: _home({"pk_promo_end": later}), log=lambda *_: None)
    [job] = jobs.list_jobs(TENANT)
    assert job["type"] == "sale_extend" and job["target"] == f"run:{run_dir.name}/landing"
    fp = sale_sync.offer_with_claim(json.loads((run_dir / "facts_pack.json").read_text()), _promo(claims_tmp), later)
    assert sale_sync.extend_changes(run_dir, "landing", fp, later) == ["text 'Prices go up on October 20, 2026.'"]


def _ended_arm(tmp_path, monkeypatch, store_redirect):
    """An arm after sale_end made redirect 77; the rebuild is faked (its run
    is live again); the store holds `store_redirect` for the path."""
    run_dir = _live(tmp_path, kind="abtest")
    sale_sync.set_sale_expiry(run_dir, status="rebuilding", redirect={"from": "/pages/lp-aiir-abtest",
                                                                      "to": "/products/fuji", "id": 77})
    new_run = _run(tmp_path, "20261017-aiir-rebuilt")
    from harness import abtest_rebuild

    monkeypatch.setattr(abtest_rebuild, "rebuild_arm",
                        lambda tenant, test_id, key, *, by, runner=None, log=print: {"run_dir": str(new_run)})
    pub = _Publisher({"/pages/lp-aiir-abtest": store_redirect} if store_redirect else {})
    monkeypatch.setattr(cli, "_make_publisher", lambda tenant, *, export_dir: pub)
    monkeypatch.setattr(type(TENANT), "load_env", lambda self: None)
    payload = {"run_id": run_dir.name, "page": "landing", "kind": "abtest", "test_id": "aiir-t1", "key": "A",
               "by": "caleb@peaksaunas.com"}
    return run_dir, pub, payload


def test_q2_the_rebuild_deletes_the_redirect_the_sweep_made(tmp_path, monkeypatch):
    run_dir, pub, payload = _ended_arm(tmp_path, monkeypatch, {"id": 77, "path": "/pages/lp-aiir-abtest",
                                                                "target": "/products/fuji"})
    sale_sync.handle_sale_rebuild(TENANT, payload)
    assert ("delete_redirect", 77) in pub.calls
    rec = runstate.load_state(run_dir)["sale_expiry"]
    assert rec["status"] == "switched" and rec["redirect_deleted"] is True


@pytest.mark.parametrize("store", [{"id": 78, "path": "/pages/lp-aiir-abtest", "target": "/products/fuji"},
                                   {"id": 77, "path": "/pages/lp-aiir-abtest", "target": "/collections/saunas"},
                                   None])
def test_q2_never_another_redirect(tmp_path, monkeypatch, store):
    run_dir, pub, payload = _ended_arm(tmp_path, monkeypatch, store)
    sale_sync.handle_sale_rebuild(TENANT, payload)
    assert not any(c[0] == "delete_redirect" for c in pub.calls)
    assert runstate.load_state(run_dir)["sale_expiry"]["redirect_deleted"] is False


def test_q2_an_existing_redirect_is_not_taken_over(tmp_path, monkeypatch):
    run_dir = _live(tmp_path, kind="postlive")
    pub = _Publisher({"/pages/lp-aiir-postlive": {"id": 5, "path": "/pages/lp-aiir-postlive", "target": "/"}})
    monkeypatch.setattr(cli, "_make_publisher", lambda tenant, *, export_dir: pub)
    monkeypatch.setattr(type(TENANT), "load_env", lambda self: None)
    sale_sync.handle_sale_end(TENANT, {"run_id": run_dir.name, "page": "landing", "kind": "postlive",
                                       "test_id": None, "key": None, "by": "caleb@peaksaunas.com"})
    assert not any(c[0] == "create_redirect" for c in pub.calls)
    assert runstate.load_state(run_dir)["sale_expiry"]["redirect"]["id"] is None


def test_q2_delete_redirect_sends_one_delete():
    from tests.test_publishers import FakeTransport

    transport = FakeTransport()
    transport.set_response("DELETE", "redirects/77.json", 200, {})
    ShopifyPublisher(store="acme.myshopify.com", token="tok", transport=transport).delete_redirect(77)
    assert [(c["method"], c["url"].rsplit("/", 1)[-1]) for c in transport.calls] == [("DELETE", "77.json")]



@pytest.mark.parametrize("rebuild", [True, False])
def test_sale_sweep_rebuild_flag(tmp_path, monkeypatch, rebuild):
    from tests.support import tenant_copy

    tenant_copy(monkeypatch, {"sale_sweep_rebuild": rebuild})
    run_dir = _live(tmp_path, kind="postlive")
    pub = _Publisher()
    monkeypatch.setattr(cli, "_make_publisher", lambda tenant, *, export_dir: pub)
    monkeypatch.setattr(type(TENANT), "load_env", lambda self: None)
    payload = {"run_id": run_dir.name, "page": "landing", "kind": "postlive", "test_id": None, "key": None,
               "by": "caleb@peaksaunas.com"}
    result = sale_sync.handle_sale_end(TENANT, payload)
    assert [c[0] for c in pub.calls] == ["update_page", "find_redirect", "create_redirect"]   # hidden + redirect
    state = runstate.load_state(run_dir)
    if rebuild:
        assert [j["type"] for j in jobs.list_jobs(TENANT)] == ["sale_rebuild"] and result["rebuild_job"]
        assert sale_sync.card_line(state) == "rebuilding"
    else:
        assert jobs.list_jobs(TENANT) == [] and result["rebuild_job"] is None
        assert sale_sync.card_line(state) == "sale ended: hidden, rebuild deferred"
        # the next sweep does not end it again
        after = datetime.datetime(2026, 10, 17, 4, 40, tzinfo=UTC)
        out = sale_sync.sweep(TENANT, now=after, fetch=lambda url: _home(), dry_run=True, log=lambda *_: None)
        assert [d["action"] for _i, d, _j in out["decisions"]] == ["wait"]


def test_sale_sweep_rebuild_false_still_uses_the_twin(tmp_path, monkeypatch):
    from tests.support import tenant_copy

    tenant_copy(monkeypatch, {"sale_sweep_rebuild": False})
    run_dir = _live(tmp_path, kind="abtest", twin=True)
    monkeypatch.setattr(sale_sync, "_publish", lambda tenant, rd, payload, note: {})
    result = sale_sync.handle_sale_end(TENANT, {"run_id": run_dir.name, "page": "landing", "kind": "abtest",
                                                "test_id": "aiir-t1", "key": "A", "by": "caleb@peaksaunas.com"})
    assert result["via"] == "twin" and sale_sync.card_line(runstate.load_state(run_dir)) == "sale ended: switched"


def test_the_flag_defaults():
    import yaml

    template = yaml.safe_load((REPO_ROOT / "tenants" / "_template" / "tenant.yaml").read_text())
    assert template["sale_sweep_rebuild"] is True and TENANT.get("sale_sweep_rebuild") is False



def test_q3_no_twin_no_swap_texts(tmp_path):
    html = _offer_page_html(tmp_path)                                  # an offer page, no twin folder
    assert "data-offer-expires" in html and "data-nosale-" not in html
    old = (LWTR / "index.html").read_text()                           # a real arm from before cycle 111
    assert "data-nosale-" not in old and offer_failsafe.add_nosale(old, "x", "y") == old


def test_q3_a_re_render_next_to_a_twin_carries_its_texts(tmp_path):
    twin = tmp_path / "run" / "landing-nosale"
    twin.mkdir(parents=True)
    (twin / "index.html").write_text('<html><body><h1 class="x">A home sauna <em>for every</em> evening</h1>'
                                     "</body></html>")
    (twin / "page.json").write_text(json.dumps({"cta_text": "Shop the Fuji"}))
    html = _offer_page_html(tmp_path)
    h1 = re.search(r"<h1\b[^>]*>", html).group(0)
    assert 'data-nosale-headline="A home sauna for every evening"' in h1 and f'data-offer-expires="{END}"' in h1
    assert html.count('data-nosale-cta="Shop the Fuji"') == html.count("data-offer-cta=") > 0
    assert offer_failsafe.add_nosale(html, "A home sauna for every evening", "Shop the Fuji") == html


@pytest.mark.real_browser
def test_q3_the_script_swaps_the_headline_and_cta_after_the_instant(tmp_path):
    import subprocess

    browser = capture.find_browser()
    if not browser:
        pytest.skip("needs a real browser")
    page = ('<html><body><h1 data-offer-expires="2020-01-01T00:00:00Z" data-nosale-headline="No sale here">'
            '$300 off the Fuji, today&#39;s price ends tonight</h1><a data-offer-expires="2020-01-01T00:00:00Z" '
            'data-offer-cta="Shop the Fuji" data-nosale-cta="See the Fuji">Shop the sale</a>'
            '<a data-offer-expires="2999-01-01T00:00:00Z" data-offer-cta="Shop the Fuji">Get $300 off the Fuji</a>'
            + offer_failsafe.SCRIPT + "</body></html>")
    path = tmp_path / "p.html"
    path.write_text(page)
    dom = subprocess.run([browser, "--headless=new", "--no-sandbox", "--disable-gpu", f"--user-data-dir={tmp_path / 'u'}",
                          "--dump-dom", path.as_uri()], capture_output=True, text=True, timeout=60).stdout
    assert ">No sale here</h1>" in dom and ">See the Fuji</a>" in dom and ">Get $300 off the Fuji</a>" in dom



class _Log:
    def __init__(self):
        self.lines = []

    def event(self, stage, message):
        self.lines.append(f"{stage}: {message}")


def test_q4_a_site_deadline_wins_over_the_ad_date_on_the_64wr_brief(claims_tmp):
    from tests.test_hotfix_cycle110 import _64wr

    brief, fp = _64wr("ad_brief"), _64wr("facts_pack")
    assert brief["offer"]["deadline"] == "October 16"
    sale_sync.sync(TENANT, fetch=lambda url: _home({"pk_promo_end": "2026-10-20T23:30:00-05:00"}), now=FETCHED,
                   log=lambda *_: None)
    moved = _promo(claims_tmp)
    assert moved["text"].endswith("prices go up on October 20, 2026.")
    # the same text without the site provenance: the ad's Oct 16 does not match Oct 20
    hand = {k: v for k, v in moved.items() if k not in ("deadline_end", "last_sale_day")}
    assert offer.match_offer(brief["offer"], [hand], "2026-10-17") is None
    log = _Log()
    assert offer.apply_to_run(copy.deepcopy(brief), fp, [moved], today="2026-10-17", log=log)["id"] == PROMO["id"]
    assert "ground: ad date Oct 16 ignored, site deadline Oct 20" in log.lines
    assert fp["offer"]["last_sale_day"] == "2026-10-20"
    # the same day: nothing is ignored, nothing is logged
    log = _Log()
    claim = dict(PROMO, **sale_sync.deadline_update(PROMO, _record()))   # synced: last day Oct 16, provenance
    assert offer._site_synced(claim)
    assert offer.apply_to_run(copy.deepcopy(brief), _64wr("facts_pack"), [claim], today="2026-10-16", log=log)
    assert not any("ignored" in line for line in log.lines)
