"""Cycle 88: the PDP or the store home page as an A/B/C arm, or as the ad's
only destination behind the split link.

Plan validation; PDP / home resolution (the "product not named" refusal);
building, publishing and the split script with external arms (UTMs and the
"assign" ping, run under node); assign events at the /e receiver; results
for mixed and external-only tests; `from-inbox` and the upload form honour
the plan; the site's destination labels.

No network, no model calls: tests/test_abtest_cycle67.py's FakeRunner (plus
an ad brief and a facts pack per run), a fake brief runner, and a real
ShopifyPublisher over tests/test_publishers.py's FakeTransport.
"""
import json
from pathlib import Path

import pytest

from harness import abevents, abtest, abtest_inbox, cli, jobs, meta_ingest, serve
from harness.publishers.shopify import ShopifyPublisher
from tests.support import TENANT
from tests.test_abtest_cycle67 import NODE, FakeRunner, _insert, _run_js, _script_body
from tests.test_listicle_site_cycle69 import _auth, _meta_item, _upload, client  # noqa: F401 (fixture)
from tests.test_publishers import FakeTransport

REVIEWER = "caleb@peaksaunas.com"
needs_node = pytest.mark.skipif(NODE is None, reason="node is not installed")

PRODUCTS = json.loads((TENANT.claims_dir / "products.json").read_text())["products"]
EVEREST = next(p for p in PRODUCTS.values() if p["name"] == "Everest")
NAMED_BRIEF = {"transcript_or_text": "Why I bought the Everest after a year of gym saunas.", "hook": "h"}
UNNAMED_BRIEF = {"transcript_or_text": "Why I bought an infrared sauna after a year of gym saunas.", "hook": "h"}


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

class BriefRunner(FakeRunner):
    """FakeRunner whose runs also carry the ad brief and the facts pack the
    real ground stage writes (facts_pack.product.url is what cta_url uses)."""

    def __init__(self, brief=NAMED_BRIEF, **kw):
        super().__init__(**kw)
        self.brief = brief

    def __call__(self, tenant, input_path, arm, seed):
        rc, run_dir = super().__call__(tenant, input_path, arm, seed)
        if run_dir:
            (run_dir / "ad_brief.json").write_text(json.dumps(self.brief))
            (run_dir / "facts_pack.json").write_text(json.dumps({"product": {"url": EVEREST["url"]}}))
        return rc, run_dir


class FakeBriefOnly:
    """Stands in for abtest.default_brief_runner (`harness ingest`)."""

    def __init__(self, brief=NAMED_BRIEF, rc=0):
        self.brief, self.rc, self.calls = brief, rc, 0

    def __call__(self, tenant, input_path, seed):
        self.calls += 1
        if self.rc:
            return self.rc, None
        run_id, run_dir = cli.make_run_dir(tenant.out_dir, "brief")
        (run_dir / "ad_brief.json").write_text(json.dumps(self.brief))
        return 0, run_dir


def _make(plan, *, runner=None, brief_runner=None, name="Control Arms", seed=7):
    runner = runner or BriefRunner()
    rc, rec = abtest.create_test(TENANT, input_path="fixture.mov", name=name, seed=seed, runner=runner,
                                 plan=plan, brief_runner=brief_runner or FakeBriefOnly())
    return rc, rec, runner


def _shopify(monkeypatch, handles):
    transport = FakeTransport()
    for i, handle in enumerate(handles):
        transport.set_response("POST", "pages.json", 201, {"page": {"id": 500 + i, "handle": handle}})
    publisher = ShopifyPublisher(store="acme.myshopify.com", token="tok",
                                 transport=transport, upload_transport=transport)
    monkeypatch.setattr(cli, "_make_publisher", lambda tenant, *, export_dir: publisher)
    monkeypatch.setattr(ShopifyPublisher, "verify_cache", lambda self, *a, **k: (8, 8))
    return transport


def _publish(monkeypatch, rec):
    tid = rec["test_id"]
    handles = [f"lp-{tid}-{v['key'].lower()}" for v in rec["variants"] if not abtest.is_external(v)]
    transport = _shopify(monkeypatch, handles + [f"lp-{tid}"])
    assert cli.main(["abtest", "publish", tid, "--by", REVIEWER, "--tenant", TENANT.name]) == 0
    posts = [json.loads(c["body"])["page"] for c in transport.calls if c["url"].endswith("pages.json")]
    return abtest.load_test(TENANT, tid), posts


def _shape(specs):
    return [s["kind"] if s["kind"] == "generated" else s["target"] for s in specs]


# ---------------------------------------------------------------------------
# 1. plans
# ---------------------------------------------------------------------------

def test_presets_have_the_six_destination_choices():
    assert {k: v[0] for k, v in abtest.PLANS.items()} == {
        "generated": "3 generated", "gen2-pdp": "2 generated + PDP", "gen2-home": "2 generated + home",
        "gen1-pdp-home": "1 generated + PDP + home", "pdp-only": "PDP only", "home-only": "Home only",
    }
    assert abtest.parse_plan(None) == ("generated", [{"kind": "generated"}] * 3)
    name, specs = abtest.parse_plan("gen1-pdp-home")
    assert name == "gen1-pdp-home" and _shape(specs) == ["generated", "pdp", "home"]
    assert _shape(abtest.parse_plan("home-only")[1]) == ["home"]


def test_a_json_plan_is_validated_and_named():
    url = {"kind": "external", "target": "url", "url": "https://peaksaunas.com/collections/all"}
    name, specs = abtest.parse_plan(json.dumps([{"kind": "generated"}, {"kind": "generated"}, url]))
    assert name == "custom" and specs[2] == url
    assert abtest.parse_plan([{"kind": "external", "target": "pdp"}])[0] == "pdp-only"
    assert abtest.parse_plan([url])[0] == "custom"


@pytest.mark.parametrize("plan", [
    [],
    [{"kind": "generated"}],
    [{"kind": "generated"}, {"kind": "generated"}],
    [{"kind": "external", "target": "pdp"}, {"kind": "generated"}, {"kind": "generated"}],
    [{"kind": "generated"}, {"kind": "external", "target": "home"}, {"kind": "external", "target": "pdp"}],
    [{"kind": "generated"}, {"kind": "external", "target": "pdp"}, {"kind": "generated"}],
    [{"kind": "external", "target": "pdp"}, {"kind": "external", "target": "home"}],
    [{"kind": "external", "target": "cart"}],
    [{"kind": "external", "target": "url", "url": "javascript:alert(1)"}],
    [{"kind": "external", "target": "url"}],
    [{"kind": "page"}],
    ["generated"],
])
def test_plans_outside_the_four_shapes_are_refused(plan):
    with pytest.raises(abtest.AbtestError):
        abtest.parse_plan(plan)


def test_an_unknown_preset_is_refused():
    with pytest.raises(abtest.AbtestError, match="unknown destinations"):
        abtest.parse_plan("pdp-and-home")


# ---------------------------------------------------------------------------
# 2. resolving PDP and home
# ---------------------------------------------------------------------------

def test_home_is_the_storefront_root():
    assert abtest.home_url(TENANT) == "https://peaksaunas.com/"


def test_pdp_is_the_runs_product_page(tmp_path):
    (tmp_path / "ad_brief.json").write_text(json.dumps(NAMED_BRIEF))
    # no facts pack (a brief-only run): the tenant product map's pick
    assert abtest.pdp_url(TENANT, tmp_path) == EVEREST["url"]
    # with a facts pack: the url the generated page's cta_url uses
    (tmp_path / "facts_pack.json").write_text(json.dumps({"product": {"url": "https://peaksaunas.com/products/x"}}))
    assert abtest.pdp_url(TENANT, tmp_path) == "https://peaksaunas.com/products/x"


def test_pdp_is_refused_when_the_ad_names_no_product(tmp_path):
    (tmp_path / "ad_brief.json").write_text(json.dumps(UNNAMED_BRIEF))
    (tmp_path / "facts_pack.json").write_text(json.dumps({"product": {"url": EVEREST["url"]}}))
    with pytest.raises(abtest.AbtestError, match="product not named in ad"):
        abtest.pdp_url(TENANT, tmp_path)


def test_split_path_keeps_the_redirect_on_the_store():
    assert abtest.split_path(TENANT, "https://peaksaunas.com/products/peak-everest") == "/products/peak-everest"
    assert abtest.split_path(TENANT, "https://www.peaksaunas.com/") == "/"
    assert abtest.split_path(TENANT, "https://peaksaunas.com/collections/all?sort=x") == "/collections/all?sort=x"
    assert abtest.split_path(TENANT, "https://other.example/x") == "https://other.example/x"


# ---------------------------------------------------------------------------
# 3. building a test with external arms
# ---------------------------------------------------------------------------

def test_two_generated_plus_pdp_builds_two_pages_and_names_the_pdp():
    rc, rec, runner = _make("gen2-pdp")
    assert rc == 0 and rec["status"] == "built" and rec["plan"] == "gen2-pdp"
    assert len(runner.calls) == 2
    a, b, c = rec["variants"]
    assert a["run_dir"] and b["run_dir"] and a["arm"] != b["arm"]
    assert c["kind"] == "external" and c["target"] == "pdp" and c["url"] == EVEREST["url"]
    assert c["status"] == "ready" and c["product_from"] == Path(a["run_dir"]).name
    assert abtest.destinations_label(rec) == "A = generated, B = generated, C = PDP"


def test_one_generated_plus_pdp_plus_home():
    rc, rec, runner = _make("gen1-pdp-home")
    assert rc == 0 and len(runner.calls) == 1
    assert [abtest.variant_label(v) for v in rec["variants"]] == ["generated", "PDP", "Home"]
    assert rec["variants"][2]["url"] == "https://peaksaunas.com/"


def test_home_only_builds_nothing():
    brief = FakeBriefOnly()
    rc, rec, runner = _make("home-only", brief_runner=brief)
    assert rc == 0 and rec["status"] == "built"
    assert runner.calls == [] and brief.calls == 0
    assert [(v["key"], v["url"]) for v in rec["variants"]] == [("A", "https://peaksaunas.com/")]


def test_pdp_only_reads_the_ad_once_for_its_product():
    brief = FakeBriefOnly()
    rc, rec, runner = _make("pdp-only", brief_runner=brief)
    assert rc == 0 and runner.calls == [] and brief.calls == 1
    assert rec["variants"][0]["url"] == EVEREST["url"] and rec["brief"]["rc"] == 0


def test_a_pdp_the_ad_does_not_name_fails_after_one_build():
    rc, rec, runner = _make("gen2-pdp", runner=BriefRunner(brief=UNNAMED_BRIEF))
    assert rc == 1 and rec["status"] == "failed"
    assert "product not named in ad" in rec["reason"]
    assert len(runner.calls) == 1  # refused before the second build was paid for


def test_pdp_only_refusal_and_budget_stop_then_resume():
    rc, rec, _ = _make("pdp-only", brief_runner=FakeBriefOnly(brief=UNNAMED_BRIEF))
    assert rc == 1 and "product not named in ad" in rec["reason"]

    rc, rec, _ = _make("pdp-only", brief_runner=FakeBriefOnly(rc=3), name="Capped")
    assert rc == 3 and rec["status"] == "queued" and "budget cap" in rec["reason"]
    rc, rec = abtest.build_variants(TENANT, rec, BriefRunner(), FakeBriefOnly())
    assert rc == 0 and rec["status"] == "built" and rec["variants"][0]["url"] == EVEREST["url"]


def test_a_brief_runner_error_fails_the_test_instead_of_leaving_it_building():
    def broken(tenant, input_path, seed):
        raise abtest.AbtestError("whisper is not installed")

    rc, rec, _ = _make("pdp-only", brief_runner=broken)
    assert rc == 1 and rec["status"] == "failed" and "could not read the ad" in rec["reason"]


def test_default_brief_runner_writes_the_brief_and_records_the_spend(monkeypatch):
    from harness import anthropic_client, budget, ingest

    monkeypatch.setattr(anthropic_client, "make_client", lambda: object())
    seen = {}

    def fake_ingest(**kw):
        seen.update(kw)
        return dict(NAMED_BRIEF)

    monkeypatch.setattr(ingest, "run_ingest", fake_ingest)
    rc, run_dir = abtest.default_brief_runner(TENANT, "fixtures/ad.mov", 1)
    assert rc == 0 and json.loads((run_dir / "ad_brief.json").read_text()) == NAMED_BRIEF
    assert seen["input_arg"] == "fixtures/ad.mov" and seen["workdir"] == run_dir
    assert run_dir.name in budget.spend_ledger_path(TENANT).read_text()
    assert abtest.pdp_url(TENANT, run_dir) == EVEREST["url"]

    def over(**kw):
        raise budget.BudgetExceeded("over the per-run cap", "cost")

    monkeypatch.setattr(ingest, "run_ingest", over)
    assert abtest.default_brief_runner(TENANT, "fixtures/ad.mov", 2) == (3, None)


def test_cli_create_takes_destinations(monkeypatch, capsys):
    monkeypatch.setattr(abtest, "default_runner", BriefRunner())
    argv = ["abtest", "create", "--tenant", TENANT.name, "--input", "fixture.mov", "--name", "Cli Arms",
            "--seed", "3", "--destinations", "gen2-home"]
    assert cli.main(argv) == 0
    out = capsys.readouterr().out
    assert "external:home" in out and "https://peaksaunas.com/" in out
    [rec] = abtest.list_tests(TENANT)
    assert rec["plan"] == "gen2-home"
    assert cli.main(argv[:-1] + ["nope"]) == 1


# ---------------------------------------------------------------------------
# 4. publish + the split script
# ---------------------------------------------------------------------------

def test_publish_ships_only_generated_pages_and_routes_the_split_to_the_pdp(monkeypatch, capsys):
    _, rec, _ = _make("gen2-pdp")
    tid = rec["test_id"]
    rec, posts = _publish(monkeypatch, rec)
    assert [p["handle"] for p in posts] == [f"lp-{tid}-a", f"lp-{tid}-b", f"lp-{tid}"]
    split = posts[-1]["body_html"]
    pdp_path = abtest.split_path(TENANT, EVEREST["url"])
    assert f'"C":"{pdp_path}"' in split and f'href="{pdp_path}"' in split
    assert "e:'assign'" in split and TENANT.get("abtest.beacon_url") in split
    assert rec["status"] == "live" and "shopify" not in rec["variants"][2]
    assert "SPLIT LINK" in capsys.readouterr().out


def test_external_only_publishes_just_the_split_page(monkeypatch):
    _, rec, _ = _make("home-only")
    rec, posts = _publish(monkeypatch, rec)
    assert [p["handle"] for p in posts] == [f"lp-{rec['test_id']}"]
    assert '{"A":"/"}' in posts[0]["body_html"]
    assert rec["status"] == "live" and rec["split"]["url"].endswith(f"/pages/lp-{rec['test_id']}")


def _split_env(search="", cookie="", rand=0.9):
    return f"""
var OUT = {{cookies: [], replaced: null, beacons: []}};
var _c = {json.dumps(cookie)};
var document = {{}};
Object.defineProperty(document, 'cookie', {{get: function(){{return _c;}}, set: function(v){{OUT.cookies.push(v);}}}});
var location = {{search: {json.dumps(search)}, hash: '', replace: function(u){{OUT.replaced = u;}}}};
Math.random = function(){{return {rand};}};
function G(n, v){{ Object.defineProperty(globalThis, n, {{value: v, configurable: true, writable: true}}); }}
G('navigator', {{sendBeacon: function(u, d){{OUT.beacons.push({{u: u, d: JSON.parse(d)}}); return true;}}}});
G('crypto', {{getRandomValues: function(a){{for (var i=0;i<a.length;i++) a[i]=3735928559+i; return a;}}}});
"""


ARMS = {"A": "/pages/lp-t1-a", "B": "/products/peak-everest", "C": "/"}


@needs_node
def test_split_assigns_an_external_arm_pings_and_redirects_with_utms():
    script = _script_body(abtest.split_body("t1", ARMS, beacon_url="https://listicle.example/e"))
    out = _run_js(_split_env("?fbclid=AbC&utm_content=old", rand=0.9), script)
    assert out["replaced"] == ("/?fbclid=AbC&utm_source=meta&utm_medium=paid&utm_campaign=t1&utm_content=t1_C"
                               "&pk_t=t1&pk_v=C")
    [beacon] = out["beacons"]
    assert beacon["u"] == "https://listicle.example/e"
    assert beacon["d"]["t"] == "t1" and beacon["d"]["v"] == "C" and beacon["d"]["e"] == "assign"
    assert abevents.VID_RE.match(beacon["d"]["vid"])
    assert any(c.startswith("pk_vid=" + beacon["d"]["vid"]) for c in out["cookies"])
    assert any(c.startswith("pk_ab_t1=C;") for c in out["cookies"])


@needs_node
def test_split_keeps_the_ads_utms_and_a_returning_visitors_ids():
    script = _script_body(abtest.split_body("t1", ARMS, beacon_url="https://listicle.example/e"))
    out = _run_js(_split_env("?utm_source=facebook&utm_medium=cpc&utm_campaign=c9",
                             cookie="pk_ab_t1=B; pk_vid=abc123def456", rand=0.0), script)
    assert out["replaced"] == ("/products/peak-everest?utm_source=facebook&utm_medium=cpc&utm_campaign=c9"
                               "&utm_content=t1_B&pk_t=t1&pk_v=B")
    assert out["beacons"][0]["d"] == {"t": "t1", "v": "B", "e": "assign", "vid": "abc123def456"}
    assert out["cookies"] == []


@needs_node
def test_split_joins_a_url_that_already_has_a_query_and_skips_the_ping_without_a_beacon_url():
    script = _script_body(abtest.split_body("t1", {"A": "https://other.example/x?ref=1"}))
    out = _run_js(_split_env("", rand=0.0), script)
    assert out["replaced"] == ("https://other.example/x?ref=1&utm_source=meta&utm_medium=paid&utm_campaign=t1"
                               "&utm_content=t1_A&pk_t=t1&pk_v=A")
    assert out["beacons"] == []


# ---------------------------------------------------------------------------
# 5. assign events at the receiver
# ---------------------------------------------------------------------------

UA = "Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X) AppleWebKit/605.1.15 Mobile/15E148"


@pytest.fixture
def beacon_app(monkeypatch):
    monkeypatch.setenv("REVIEW_PASSWORD", "pw-for-tests")
    monkeypatch.delenv("REVIEW_TRUST_CF_ACCESS", raising=False)
    abtest.save_test(TENANT, {"test_id": "ctl-ab12", "status": "live", "variants": [
        {"key": "A", "arm": "quiz"}, {"key": "B", "arm": "comparison"},
        {"key": "C", "arm": "external:pdp", "kind": "external", "target": "pdp", "url": EVEREST["url"]}]})
    abtest.save_test(TENANT, {"test_id": "ctl-done", "status": "built", "variants": [
        {"key": "A", "arm": "external:home", "kind": "external", "target": "home"}]})

    def make():
        app = serve.build_app(TENANT)
        app.config["TESTING"] = True
        return app.test_client()
    return make


def _post(client, payload, ip="203.0.113.9"):
    return client.post("/e", data=json.dumps(payload), environ_base={"REMOTE_ADDR": ip},
                       headers={"User-Agent": UA, "Content-Type": "text/plain;charset=UTF-8"})


def test_assign_events_are_recorded_once_per_visitor_and_arm(beacon_app):
    client = beacon_app()
    ev = {"t": "ctl-ab12", "v": "C", "e": "assign", "vid": "abc123def456"}
    assert _post(client, ev).status_code == 204
    assert _post(client, ev).status_code == 204  # duplicate: a no-op
    assert _post(client, {**ev, "vid": "zzz999yyy888"}).status_code == 204
    assert _post(client, {**ev, "v": "A"}).status_code == 204
    assert _post(client, {**ev, "t": "ctl-done"}).status_code == 400  # not live
    assert _post(client, {**ev, "v": "D"}).status_code == 400
    counts = abevents.counts(abevents.db_path(TENANT), "ctl-ab12")
    assert counts["C"] == {"views": 0, "clicks": 0, "assigns": 2}
    assert counts["A"] == {"views": 0, "clicks": 0, "assigns": 1}


def test_assign_events_share_the_per_ip_rate_limit(monkeypatch, beacon_app):
    monkeypatch.setattr(abevents, "RATE_LIMIT_PER_MIN", 3)
    client = beacon_app()
    codes = [_post(client, {"t": "ctl-ab12", "v": "C", "e": "assign" if i % 2 else "view", "vid": f"vid{i:08d}"})
             .status_code for i in range(5)]
    assert codes == [204, 204, 204, 429, 429]


# ---------------------------------------------------------------------------
# 6. results
# ---------------------------------------------------------------------------

def _assign(tid, key, n, start=0):
    _insert(abevents.db_path(TENANT), tid, key, "assign", n, start)


def test_results_for_a_mixed_test_show_assignments_and_no_invented_numbers(monkeypatch, capsys):
    _, rec, _ = _make("gen2-pdp")
    rec, _ = _publish(monkeypatch, rec)
    tid = rec["test_id"]
    db = abevents.db_path(TENANT)
    for key, views, clicks in (("A", 100, 10), ("B", 100, 30)):
        _assign(tid, key, views + 5)
        _insert(db, tid, key, "view", views)
        _insert(db, tid, key, "cta", clicks)
    _assign(tid, "C", 107)
    rows = {r["key"]: r for r in abtest.results(TENANT, rec)}
    assert rows["C"]["assigns"] == 107 and rows["C"]["kind"] == "external"
    assert rows["C"]["views"] is rows["C"]["clicks"] is rows["C"]["ctr"] is rows["C"]["p_best"] is None
    assert rows["C"]["utm_content"] == f"{tid}_C"
    assert rows["A"]["assigns"] == 105 and rows["B"]["views"] == 100
    assert rows["A"]["p_best"] + rows["B"]["p_best"] == pytest.approx(1.0)

    assert cli.main(["abtest", "results", tid, "--no-orders", "--tenant", TENANT.name]) == 0
    out = capsys.readouterr().out
    [c_line] = [ln for ln in out.splitlines() if ln.startswith("C  external:pdp")]
    assert c_line.split()[2] == "107" and c_line.split()[3:] == ["-"] * 6
    assert f"measure in GA4/Shopify by utm_content={tid}_C" in out
    assert "assigned" in out

    # an order cannot be credited to an arm the beacon never saw
    assert abtest.results(TENANT, rec, orders={"A": {"orders": 1, "revenue": "10.00"}})[2].get("orders") is None
    assert cli.main(["abtest", "status", tid, "--tenant", TENANT.name]) == 0
    assert "destinations: A = generated, B = generated, C = PDP" in capsys.readouterr().out


def test_results_for_an_external_only_test_and_finishing_it(monkeypatch, capsys):
    _, rec, _ = _make("pdp-only")
    rec, _ = _publish(monkeypatch, rec)
    tid = rec["test_id"]
    _assign(tid, "A", 42)
    [row] = abtest.results(TENANT, rec)
    assert row["assigns"] == 42 and row["views"] is None and row["p_best"] is None
    text = abtest.format_results(rec, [row])
    assert "no generated page" in text and "%" not in text

    assert cli.main(["abtest", "finish", tid, "--tenant", TENANT.name]) == 1
    assert "no generated page" in capsys.readouterr().err
    assert cli.main(["abtest", "finish", tid, "--force", "--tenant", TENANT.name]) == 0
    assert "no winner" in capsys.readouterr().out
    rec = abtest.load_test(TENANT, tid)
    assert rec["status"] == "finished" and rec["winner"] is None


def test_one_generated_page_gets_no_p_best(monkeypatch):
    _, rec, _ = _make("gen1-pdp-home")
    rec, _ = _publish(monkeypatch, rec)
    _insert(abevents.db_path(TENANT), rec["test_id"], "A", "view", 50)
    rows = abtest.results(TENANT, rec)
    assert [r["p_best"] for r in rows] == [None, None, None]
    assert "P(best) needs two generated pages" in abtest.format_results(rec, rows)


def test_external_arms_stay_out_of_the_pooled_build_stats(monkeypatch):
    _, rec, _ = _make("gen2-home")
    rec, _ = _publish(monkeypatch, rec)
    _assign(rec["test_id"], "C", 10)
    stats = abtest.pooled_arm_stats(TENANT)
    assert not any(k.startswith("external:") for k in stats)
    assert set(stats) == {rec["variants"][0]["arm"], rec["variants"][1]["arm"]}


# ---------------------------------------------------------------------------
# 7. from-inbox
# ---------------------------------------------------------------------------

def test_from_inbox_uses_the_items_plan_then_the_flag(monkeypatch):
    monkeypatch.setitem(TENANT.config["abtest"], "auto_publish", False)
    _meta_item("120210000000881", "Own plan")
    inbox = meta_ingest.Inbox(TENANT.meta_inbox_dir)
    inbox.note("120210000000881", "destinations: Home only", destinations="home-only")
    _meta_item("120210000000882", "Flag plan")
    runner = BriefRunner()
    abtest_inbox.from_inbox(TENANT, by=REVIEWER, runner=runner, log=lambda *_: None, plan="gen2-home")
    plans = {r["name"]: r for r in abtest.list_tests(TENANT)}
    assert plans["Own plan"]["plan"] == "home-only" and len(plans["Own plan"]["variants"]) == 1
    assert plans["Flag plan"]["plan"] == "gen2-home"
    assert len(runner.calls) == 2  # only the flag plan's two generated pages
    assert {i["state"] for i in inbox.items()} == {"tested"}


def test_from_inbox_cli_flag_and_a_refused_pdp(monkeypatch, capsys):
    monkeypatch.setattr(abtest_inbox, "_auto_publish", lambda tenant: False)
    _meta_item("120210000000883", "No product named")
    monkeypatch.setattr(abtest, "default_runner", BriefRunner(brief=UNNAMED_BRIEF))
    assert cli.main(["abtest", "from-inbox", "--tenant", TENANT.name, "--destinations", "gen2-pdp"]) == 0
    item = meta_ingest.Inbox(TENANT.meta_inbox_dir).read("120210000000883")
    assert item["state"] == "failed" and "product not named in ad" in item["reason"]
    assert "1 failed" in capsys.readouterr().out
    assert cli.main(["abtest", "from-inbox", "--tenant", TENANT.name, "--destinations", "bogus"]) == 1


# ---------------------------------------------------------------------------
# 8. the site: upload form and labels
# ---------------------------------------------------------------------------

def test_upload_form_offers_the_destinations_for_a_new_test_only(client):
    body = client.get("/upload", headers=_auth()).get_data(as_text=True)
    assert '<select id="destinations" name="destinations">' in body
    for key, (label, _shape) in abtest.PLANS.items():
        assert f'<option value="{key}"' in body and label in body
    assert '<option value="generated" selected>' in body
    assert "for this new test only" in body and "a live test cannot be changed" in body
    assert body.count("never edits the ad in Meta") == 1


def test_upload_saves_the_plan_and_the_job_builds_it(monkeypatch, client):
    monkeypatch.setitem(TENANT.config["abtest"], "auto_publish", False)
    monkeypatch.setattr(abtest, "default_runner", BriefRunner())
    resp = _upload(client, name="Upload Arms", extra={"destinations": "gen1-pdp-home"})
    assert resp.status_code == 303
    [item] = meta_ingest.Inbox(TENANT.meta_inbox_dir).items()
    assert item["destinations"] == "gen1-pdp-home" and item["state"] == "queued"
    done = jobs.run_one(TENANT)
    assert done["state"] == "done", done
    rec = abtest.load_test(TENANT, done["result"]["test_id"])
    assert rec["plan"] == "gen1-pdp-home"
    assert [v.get("url") for v in rec["variants"][1:]] == [EVEREST["url"], "https://peaksaunas.com/"]
    [audit] = [a for a in jobs.audit_rows(TENANT) if a["action"] == "upload"]
    assert audit["detail"]["destinations"] == "gen1-pdp-home"


def test_upload_refuses_an_unknown_plan(client):
    resp = _upload(client, extra={"destinations": "pdp-and-home"})
    assert resp.status_code == 400 and "Choose one of the listed destinations" in resp.get_data(as_text=True)
    assert meta_ingest.Inbox(TENANT.meta_inbox_dir).items() == []


def test_test_card_and_generation_page_label_the_arms(monkeypatch, client):
    _, rec, _ = _make("gen2-pdp", name="Labelled Arms")
    rec, _ = _publish(monkeypatch, rec)
    tid = rec["test_id"]
    _assign(tid, "C", 9)
    home = client.get("/", headers=_auth()).get_data(as_text=True)
    assert "Destinations: <b>A = generated, B = generated, C = PDP</b>" in home
    assert "fixed when the test was created" in home
    assert '<div class="gen-title">C = PDP</div>' in home
    assert f'href="{EVEREST["url"]}"' in home and "<span>Assigned</span><b>9</b>" in home
    assert f"utm_content=<code>{tid}_C</code>" in home

    a = rec["variants"][0]
    page = client.get(f"/gen/{Path(a['run_dir']).name}/{a['cartridge']}", headers=_auth()).get_data(as_text=True)
    assert "Test destinations: A = this page, B = generated, C = PDP" in page


def test_a_three_generated_test_shows_no_destinations_line(monkeypatch, client):
    _, rec, _ = _make("generated", name="Plain Arms")
    _publish(monkeypatch, rec)
    home = client.get("/", headers=_auth()).get_data(as_text=True)
    assert "Plain Arms" in home and "Destinations:" not in home
