"""Cycle 67: A/B/C tests -- arm selection, the test record, `harness abtest`,
the split and beacon scripts, the /e receiver, order attribution, and the
Beta-posterior stats.

No network: the harness runner is a fake that writes a minimal run dir, and
Shopify is a real ShopifyPublisher over tests/test_publishers.py's
FakeTransport. The two inline scripts are run under node (skipped when node is
not installed) with a stub document/location/navigator.
"""
import json
import math
import random
import shutil
import sqlite3
import subprocess
from pathlib import Path

import pytest

from harness import abevents, abstats, abtest, cli, page_body, runstate, serve
from harness.publishers.shopify import ShopifyPublisher
from tests.support import REPO_ROOT, TENANT
from tests.test_publishers import FakeTransport

REVIEWER = "caleb@peaksaunas.com"
NODE = shutil.which("node")
needs_node = pytest.mark.skipif(NODE is None, reason="node is not installed")


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

class ConfigTenant:
    """Only what abtest's library/settings code reads."""

    def __init__(self, abtest_cfg=None, name="acme"):
        self.name = name
        self._cfg = {"abtest": abtest_cfg} if abtest_cfg is not None else {}

    def get(self, dotted, default=None):
        node = self._cfg
        for part in dotted.split("."):
            if not isinstance(node, dict) or part not in node:
                return default
            node = node[part]
        return node


def _stats(**by_arm):
    return {arm: {"views": v, "clicks": c, "tests": 1} for arm, (c, v) in by_arm.items()}


def _write_fake_run(tenant, arm, seed):
    run_id, run_dir = cli.make_run_dir(tenant.out_dir, f"ad-{seed}")
    cart = run_dir / arm.cartridge
    cart.mkdir()
    (cart / "index.html").write_text(
        "<html><head><style>.pk-lp{color:red}</style></head><body><div class=\"pk-lp adv-wrap\">"
        f"<h1>{arm.id} page</h1>"
        '<a class="lst-btn" href="https://peaksaunas.com/products/peak-fuji">Shop the Fuji</a>'
        "</div></body></html>"
    )
    (cart / "page.json").write_text(json.dumps({"headline": f"{arm.id} headline"}))
    runstate.init_state(run_dir, pages=[arm.cartridge])
    runstate.mark_needs_review(run_dir)
    runstate.init_packet(run_dir)
    return run_dir


class FakeRunner:
    """rc by arm id (default 0); records every call."""

    def __init__(self, fail_arms=(), budget_after=None):
        self.calls = []
        self.fail_arms = set(fail_arms)
        self.budget_after = budget_after

    def __call__(self, tenant, input_path, arm, seed):
        self.calls.append((arm.id, seed))
        if self.budget_after is not None and len(self.calls) > self.budget_after:
            return 3, None
        if arm.id in self.fail_arms:
            return 2, None
        return 0, _write_fake_run(tenant, arm, seed)


def _create(monkeypatch, runner, *, seed=7, name="Hidden Costs V2", extra=()):
    monkeypatch.setattr(abtest, "default_runner", runner)
    argv = ["abtest", "create", "--tenant", TENANT.name, "--input", "fixture.mov",
            "--name", name, "--seed", str(seed), *extra]
    return cli.main(argv)


def _only_test(tenant=TENANT):
    tests = abtest.list_tests(tenant)
    assert len(tests) == 1
    return tests[0]


def _patch_publisher(monkeypatch, test_id):
    transport = FakeTransport()
    for i, handle in enumerate([f"lp-{test_id}-a", f"lp-{test_id}-b", f"lp-{test_id}-c", f"lp-{test_id}"]):
        transport.set_response("POST", "pages.json", 201, {"page": {"id": 100 + i, "handle": handle}})
    publisher = ShopifyPublisher(store="acme.myshopify.com", token="tok",
                                 transport=transport, upload_transport=transport)
    monkeypatch.setattr(cli, "_make_publisher", lambda tenant, *, export_dir: publisher)
    monkeypatch.setattr(ShopifyPublisher, "verify_cache", lambda self, *a, **k: (8, 8))
    return transport


def _insert(db, test_id, key, event, n, start=0):
    conn = abevents.connect(db)
    for i in range(start, start + n):
        abevents.record_event(conn, test_id=test_id, key=key, event=event, vid=f"v{key.lower()}{i:06d}", iph="x")
    conn.close()


# ---------------------------------------------------------------------------
# 1. library + arm selection
# ---------------------------------------------------------------------------

def test_default_library_is_the_seven_builds_with_the_real_style_look_pairing():
    arms = abtest.library(ConfigTenant())
    assert [a.id for a in arms] == [
        "listicle:reasons:cards", "listicle:mistakes:editorial", "listicle:questions:scorecard",
        "listicle:myths:pillars", "listicle:tested:lander", "comparison", "quiz",
    ]
    from harness import listicle
    for arm in arms[:5]:
        assert listicle.LOOK_BY_STYLE[arm.style] == arm.look


def test_library_comes_from_tenant_config():
    tenant = ConfigTenant({"library": ["quiz", "listicle:myths", "comparison"]})
    ids = [a.id for a in abtest.library(tenant)]
    # a style with no look gets the tenant's own style -> look pairing
    assert ids == ["quiz", "listicle:myths:pillars", "comparison"]
    assert set(abtest.choose_arms(tenant, 3, random.Random(1), stats={})) == set(ids)


def test_library_rejects_an_unknown_build():
    with pytest.raises(abtest.AbtestError):
        abtest.library(ConfigTenant({"library": ["listicle:rants", "quiz", "comparison"]}))
    with pytest.raises(abtest.AbtestError):
        abtest.library(ConfigTenant({"library": ["nosuchcartridge", "quiz", "comparison"]}))


def test_peak_tenant_library_matches_the_default():
    assert [a.id for a in abtest.library(TENANT)] == list(abtest.DEFAULT_LIBRARY)
    assert abtest.settings(TENANT)["beacon_url"] == "https://listicle.peaksaunasteam.com/e"


def test_choose_arms_is_three_distinct_and_deterministic_under_a_seed():
    tenant = ConfigTenant()
    stats = _stats(**{"quiz": (30, 400), "comparison": (10, 400)})
    first = abtest.choose_arms(tenant, 3, random.Random(42), stats=stats)
    again = abtest.choose_arms(tenant, 3, random.Random(42), stats=stats)
    assert first == again
    assert len(first) == 3 and len(set(first)) == 3


def test_cold_start_is_uniform_random():
    tenant = ConfigTenant({"explore": 0.0})
    counts = {a: 0 for a in abtest.DEFAULT_LIBRARY}
    n = 3500
    for seed in range(n):
        for arm in abtest.choose_arms(tenant, 3, random.Random(seed), stats={}):
            counts[arm] += 1
    expected = n * 3 / len(counts)  # 1500
    for arm, c in counts.items():
        assert abs(c - expected) < 0.1 * expected, (arm, c)


def test_thompson_favors_a_clearly_better_arm():
    tenant = ConfigTenant({"explore": 0.0})
    stats = {a: {"views": 1000, "clicks": 40, "tests": 2} for a in abtest.DEFAULT_LIBRARY}
    stats["quiz"] = {"views": 1000, "clicks": 150, "tests": 2}
    picks = [abtest.choose_arms(tenant, 3, random.Random(s), stats=stats) for s in range(300)]
    assert all("quiz" in p for p in picks)
    assert all(p[0] == "quiz" for p in picks)


def test_explore_rate_replaces_the_choice_with_uniform_random():
    tenant = ConfigTenant({"explore": 0.2})
    stats = {a: {"views": 5000, "clicks": 50, "tests": 3} for a in abtest.DEFAULT_LIBRARY}
    best = {"quiz", "comparison", "listicle:myths:pillars"}
    for arm in best:
        stats[arm] = {"views": 5000, "clicks": 900, "tests": 3}
    n = 3000
    off_best = sum(set(abtest.choose_arms(tenant, 3, random.Random(s), stats=stats)) != best for s in range(n))
    # explore fires 20% of the time; a uniform draw still lands on the best 3 in 1 of C(7,3)=35
    expected = 0.2 * (1 - 1 / 35)
    assert abs(off_best / n - expected) < 0.03
    cold = ConfigTenant({"explore": 0.0})
    assert all(set(abtest.choose_arms(cold, 3, random.Random(s), stats=stats)) == best for s in range(200))


def test_rank_arms_orders_every_library_arm_once():
    ranking = abtest.rank_arms(ConfigTenant(), random.Random(3), stats={})
    assert sorted(ranking) == sorted(abtest.DEFAULT_LIBRARY)


# ---------------------------------------------------------------------------
# 8. stats
# ---------------------------------------------------------------------------

def _exact_p_b_beats_a(a_clicks, a_views, b_clicks, b_views):
    """Evan Miller's closed form for P(p_B > p_A), Beta(1+s, 1+f) posteriors."""
    alpha_a, beta_a = a_clicks + 1, a_views - a_clicks + 1
    alpha_b, beta_b = b_clicks + 1, b_views - b_clicks + 1

    def lbeta(x, y):
        return math.lgamma(x) + math.lgamma(y) - math.lgamma(x + y)

    total = 0.0
    for i in range(alpha_b):
        total += math.exp(lbeta(alpha_a + i, beta_a + beta_b) - math.log(beta_b + i)
                          - lbeta(1 + i, beta_b) - lbeta(alpha_a, beta_a))
    return total


def test_p_best_matches_the_exact_two_arm_formula():
    data = {"A": (20, 200), "B": (30, 200)}
    got = abstats.p_best(data, draws=40000, seed=1)
    exact = _exact_p_b_beats_a(20, 200, 30, 200)
    assert abs(got["B"] - exact) < 0.01
    assert abs(got["A"] + got["B"] - 1) < 1e-9


def test_p_best_is_symmetric_for_equal_arms_and_sure_for_a_clear_winner():
    even = abstats.p_best({"A": (10, 100), "B": (10, 100), "C": (10, 100)}, draws=30000, seed=2)
    for p in even.values():
        assert abs(p - 1 / 3) < 0.02
    clear = abstats.p_best({"A": (10, 1000), "B": (100, 1000), "C": (12, 1000)}, seed=3)
    assert clear["B"] > 0.999


def test_p_best_is_deterministic_and_clamps_clicks_above_views():
    data = {"A": (5, 50), "B": (7, 50), "C": (60, 50)}
    assert abstats.p_best(data, seed=9) == abstats.p_best(data, seed=9)
    assert abstats.ctr(60, 50) == 1.0
    assert abstats.ctr(0, 0) == 0.0


# ---------------------------------------------------------------------------
# 2. test record + suite isolation
# ---------------------------------------------------------------------------

def test_abtests_dir_is_redirected_by_the_isolation_fixture(isolated_tenant_paths):
    assert TENANT.abtests_dir == isolated_tenant_paths / "abtests"
    real = TENANT.root / "abtests"
    assert TENANT.abtests_dir != real
    from tests import conftest
    assert "abtests" in conftest.TRACKED_TENANT_SUBDIRS


@pytest.mark.real_tenant_paths
def test_abtests_dir_lives_under_the_tenant_root_not_out():
    assert TENANT.abtests_dir == TENANT.root / "abtests"
    assert TENANT.out_dir not in TENANT.abtests_dir.parents


def test_test_id_is_a_slug_of_the_ad_name_plus_a_short_id():
    test_id = abtest.new_test_id("Hidden Costs V2 -- Sept (Meta)")
    assert abtest.TEST_ID_RE.match(test_id)
    assert test_id.startswith("hidden-costs-v2-sept-meta-")
    assert len(test_id.rsplit("-", 1)[1]) == 4


def test_create_builds_three_variants_and_records_them(monkeypatch, capsys):
    runner = FakeRunner()
    assert _create(monkeypatch, runner) == 0
    rec = _only_test()
    assert rec["status"] == "built"
    assert [v["key"] for v in rec["variants"]] == ["A", "B", "C"]
    assert len({v["arm"] for v in rec["variants"]}) == 3
    for v in rec["variants"]:
        run_dir = Path(v["run_dir"])
        assert (run_dir / v["cartridge"] / "index.html").exists()
    assert rec["input"].endswith("fixture.mov")
    assert rec["source"] == {"kind": "upload"}
    path = TENANT.abtests_dir / f"{rec['test_id']}.json"
    assert path.exists() and TENANT.out_dir not in path.parents
    # same seed, same arms (the rng is seeded from --seed)
    assert [c[0] for c in runner.calls] == [v["arm"] for v in rec["variants"]]
    assert rec["test_id"] in capsys.readouterr().out


def test_create_reads_optional_source_json(monkeypatch, tmp_path):
    src = tmp_path / "meta.json"
    src.write_text(json.dumps({"ad_id": "120211", "ad_name": "Hidden costs", "campaign": "C1"}))
    assert _create(monkeypatch, FakeRunner(), extra=("--source-json", str(src))) == 0
    rec = _only_test()
    assert rec["source"]["kind"] == "meta"
    assert rec["source"]["ad_id"] == "120211"


def test_a_failing_arm_is_retried_three_seeds_then_substituted(monkeypatch):
    rng_first = abtest.choose_arms(TENANT, 3, random.Random(7), stats={})
    bad = rng_first[1]
    runner = FakeRunner(fail_arms={bad})
    assert _create(monkeypatch, runner, seed=7) == 0
    rec = _only_test()
    assert rec["status"] == "built"
    arms = [v["arm"] for v in rec["variants"]]
    assert bad not in arms and len(set(arms)) == 3
    tried_bad = [s for a, s in runner.calls if a == bad]
    assert len(tried_bad) == 3 and len(set(tried_bad)) == 3
    assert rec["failed_arms"][0]["arm"] == bad
    assert rec["variants"][1]["key"] == "B"


def test_budget_cap_leaves_the_test_queued_and_resume_finishes_it(monkeypatch, capsys):
    rc = _create(monkeypatch, FakeRunner(budget_after=1))
    assert rc == 3
    rec = _only_test()
    assert rec["status"] == "queued"
    assert "budget" in rec["reason"].lower()
    assert "harness abtest resume" in capsys.readouterr().err
    built = [v for v in rec["variants"] if v.get("run_dir")]
    assert len(built) == 1

    runner = FakeRunner()
    monkeypatch.setattr(abtest, "default_runner", runner)
    assert cli.main(["abtest", "resume", rec["test_id"], "--tenant", TENANT.name]) == 0
    rec = _only_test()
    assert rec["status"] == "built"
    assert all(v.get("run_dir") for v in rec["variants"])
    assert len(runner.calls) == 2  # only the two missing variants were built


def test_no_arm_left_marks_the_test_failed(monkeypatch):
    runner = FakeRunner(fail_arms=set(abtest.DEFAULT_LIBRARY))
    assert _create(monkeypatch, runner) == 1
    assert _only_test()["status"] == "failed"


# ---------------------------------------------------------------------------
# 3. publish (fake Shopify), status, results, finish, library
# ---------------------------------------------------------------------------

def _built_test(monkeypatch):
    assert _create(monkeypatch, FakeRunner()) == 0
    return _only_test()


def test_publish_ships_three_hidden_variants_and_a_split_page(monkeypatch, capsys):
    rec = _built_test(monkeypatch)
    tid = rec["test_id"]
    transport = _patch_publisher(monkeypatch, tid)
    assert cli.main(["abtest", "publish", tid, "--by", REVIEWER, "--tenant", TENANT.name]) == 0
    out = capsys.readouterr().out

    posts = [json.loads(c["body"])["page"] for c in transport.calls if c["url"].endswith("pages.json")]
    assert [p["handle"] for p in posts] == [f"lp-{tid}-a", f"lp-{tid}-b", f"lp-{tid}-c", f"lp-{tid}"]
    for p in posts:
        assert p["published"] is True
        assert {"namespace": "seo", "key": "hidden", "value": 1, "type": "number_integer"} in p["metafields"]
    # variants carry the beacon; the split page carries only the split script
    for p, key in zip(posts[:3], "ABC"):
        assert "sendBeacon" in p["body_html"] and f'"{key}"' in p["body_html"]
    split = posts[3]["body_html"]
    assert "sendBeacon" not in split
    assert split.lstrip().startswith("<script>")
    assert f"/pages/lp-{tid}-a" in split and "<noscript>" in split

    rec = abtest.load_test(TENANT, tid)
    assert rec["status"] == "live" and rec["live_at"]
    assert rec["split"]["url"] == f"https://peaksaunas.com/pages/lp-{tid}"
    assert [v["shopify"]["handle"] for v in rec["variants"]] == [f"lp-{tid}-a", f"lp-{tid}-b", f"lp-{tid}-c"]
    for v in rec["variants"]:
        run_dir = Path(v["run_dir"])
        assert runstate.load_state(run_dir)["pages"][v["cartridge"]] == "published"
        assert runstate.load_packet(run_dir)["stamp"] == "ship"
    assert f"https://peaksaunas.com/pages/lp-{tid}" in out


def test_publish_refuses_an_unknown_reviewer_and_a_missing_beacon_url(monkeypatch, capsys):
    rec = _built_test(monkeypatch)
    _patch_publisher(monkeypatch, rec["test_id"])
    assert cli.main(["abtest", "publish", rec["test_id"], "--by", "nobody@x.com", "--tenant", TENANT.name]) == 1
    real_settings = abtest.settings
    monkeypatch.setattr(abtest, "settings", lambda t: {**real_settings(t), "beacon_url": ""})
    assert cli.main(["abtest", "publish", rec["test_id"], "--by", REVIEWER, "--tenant", TENANT.name]) == 1
    assert "beacon_url" in capsys.readouterr().err
    assert abtest.load_test(TENANT, rec["test_id"])["status"] == "built"


def test_a_variant_export_carries_the_beacon_only_when_it_is_in_a_test(monkeypatch):
    rec = _built_test(monkeypatch)
    v = rec["variants"][0]
    cart = Path(v["run_dir"]) / v["cartridge"]
    html, _ = page_body.build_shopify_body(cart)
    assert "sendBeacon" not in html
    runstate.record_abtest(Path(v["run_dir"]), page=v["cartridge"], test_id=rec["test_id"], key="A")
    html, _ = page_body.build_shopify_body(cart)
    assert html.count("sendBeacon") == 1
    assert rec["test_id"] in html and "https://listicle.peaksaunasteam.com/e" in html
    # the beacon is the last thing in the body, after the page's own CSS/markup
    assert html.rstrip().endswith("</script>")


def test_status_results_and_finish(monkeypatch, capsys):
    rec = _built_test(monkeypatch)
    tid = rec["test_id"]
    _patch_publisher(monkeypatch, tid)
    assert cli.main(["abtest", "publish", tid, "--by", REVIEWER, "--tenant", TENANT.name]) == 0
    capsys.readouterr()

    assert cli.main(["abtest", "status", "--tenant", TENANT.name]) == 0
    assert tid in capsys.readouterr().out

    db = abevents.db_path(TENANT)
    for key, clicks in (("A", 20), ("B", 60), ("C", 25)):
        _insert(db, tid, key, "view", 100)
        _insert(db, tid, key, "cta", clicks)
    assert cli.main(["abtest", "results", tid, "--no-orders", "--tenant", TENANT.name]) == 0
    out = capsys.readouterr().out
    assert "P(best)" in out and "60.0%" in out

    # P(best) is high but 100 views < min_views 300 -> refused
    assert cli.main(["abtest", "finish", tid, "--tenant", TENANT.name]) == 1
    assert "min_views" in capsys.readouterr().err
    for key, clicks in (("A", 40), ("B", 120), ("C", 50)):
        _insert(db, tid, key, "view", 200, start=100)
        _insert(db, tid, key, "cta", clicks, start=100)
    assert cli.main(["abtest", "finish", tid, "--tenant", TENANT.name]) == 0
    rec = abtest.load_test(TENANT, tid)
    assert rec["status"] == "finished"
    assert rec["winner"]["key"] == "B"
    assert rec["winner"]["arm"] == rec["variants"][1]["arm"]
    assert rec["winner"]["p_best"] >= 0.95


def test_finish_refuses_a_close_race_unless_forced(monkeypatch, capsys):
    rec = _built_test(monkeypatch)
    tid = rec["test_id"]
    rec["status"] = "live"
    abtest.save_test(TENANT, rec)
    db = abevents.db_path(TENANT)
    for key, clicks in (("A", 30), ("B", 31), ("C", 29)):
        _insert(db, tid, key, "view", 400)
        _insert(db, tid, key, "cta", clicks)
    assert cli.main(["abtest", "finish", tid, "--tenant", TENANT.name]) == 1
    assert "0.95" in capsys.readouterr().err
    assert cli.main(["abtest", "finish", tid, "--force", "--tenant", TENANT.name]) == 0
    rec = abtest.load_test(TENANT, tid)
    assert rec["status"] == "finished" and rec["winner"]["forced"] is True


def test_pooled_arm_stats_and_library_command(monkeypatch, capsys):
    rec = _built_test(monkeypatch)
    rec["status"] = "live"
    abtest.save_test(TENANT, rec)
    db = abevents.db_path(TENANT)
    _insert(db, rec["test_id"], "A", "view", 50)
    _insert(db, rec["test_id"], "A", "cta", 5)
    stats = abtest.pooled_arm_stats(TENANT)
    arm_a = rec["variants"][0]["arm"]
    assert stats[arm_a] == {"views": 50, "clicks": 5, "tests": 1}
    # a test that never went live does not count
    rec["status"] = "built"
    abtest.save_test(TENANT, rec)
    assert abtest.pooled_arm_stats(TENANT).get(arm_a, {"views": 0})["views"] == 0

    assert cli.main(["abtest", "library", "--tenant", TENANT.name]) == 0
    out = capsys.readouterr().out
    for arm in abtest.DEFAULT_LIBRARY:
        assert arm in out


# ---------------------------------------------------------------------------
# 4. split script
# ---------------------------------------------------------------------------

SPLIT_VARIANTS = {"A": "/pages/lp-t1-a", "B": "/pages/lp-t1-b", "C": "/pages/lp-t1-c"}


def _script_body(html):
    start = html.index("<script>") + len("<script>")
    return html[start:html.index("</script>", start)]


def _run_js(prelude, script):
    code = prelude + "\n" + script + "\nconsole.log(JSON.stringify(OUT));"
    proc = subprocess.run([NODE, "-e", code], capture_output=True, text=True, timeout=20)
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout.strip().splitlines()[-1])


def _split_prelude(search, cookie="", rand=0.5):
    return f"""
var OUT = {{cookies: [], replaced: null}};
var _c = {json.dumps(cookie)};
var document = {{}};
Object.defineProperty(document, 'cookie', {{get: function(){{return _c;}}, set: function(v){{OUT.cookies.push(v);}}}});
var location = {{search: {json.dumps(search)}, hash: '', replace: function(u){{OUT.replaced = u;}}}};
Math.random = function(){{return {rand};}};
"""


def test_split_body_survives_the_shopify_export_unchanged():
    html = abtest.split_body("t1", SPLIT_VARIANTS, title="Hidden costs")
    passed = page_body.strip_document_chrome("<html><head></head><body>" + html + "</body></html>")
    passed, css = page_body.extract_page_css(passed)
    passed, motion = page_body.extract_motion_script(passed)
    passed = page_body.relativize_internal_links(passed, TENANT)
    passed = page_body.css_scope.rem_to_px_in_style_attrs(passed)
    assert passed == html and css == "" and motion is None
    assert html.index("<script>") == 0
    assert len(_script_body(html)) < 900


@needs_node
def test_split_script_picks_sets_cookie_and_forwards_the_query():
    script = _script_body(abtest.split_body("t1", SPLIT_VARIANTS))
    out = _run_js(_split_prelude("?utm_source=fb&utm_campaign=x%20y&fbclid=AbC&pk_v=Z", rand=0.5), script)
    assert out["replaced"] == "/pages/lp-t1-b?utm_source=fb&utm_campaign=x%20y&fbclid=AbC&pk_t=t1&pk_v=B"
    cookie = out["cookies"][0]
    assert cookie.startswith("pk_ab_t1=B;") and "path=/" in cookie
    assert "max-age=2592000" in cookie and "SameSite=Lax" in cookie


@needs_node
def test_split_script_honors_an_existing_cookie_and_handles_no_query():
    script = _script_body(abtest.split_body("t1", SPLIT_VARIANTS))
    out = _run_js(_split_prelude("", cookie="x=1; pk_ab_t1=C", rand=0.0), script)
    assert out["replaced"] == "/pages/lp-t1-c?pk_t=t1&pk_v=C"
    assert out["cookies"] == []
    # a stale cookie naming no current variant is re-picked
    out = _run_js(_split_prelude("?a=1", cookie="pk_ab_t1=Q", rand=0.0), script)
    assert out["replaced"] == "/pages/lp-t1-a?a=1&pk_t=t1&pk_v=A"


# ---------------------------------------------------------------------------
# 5. beacon script + CTA detection
# ---------------------------------------------------------------------------

def _beacon_prelude(cookie=""):
    return f"""
var OUT = {{beacons: [], cookies: [], fetches: []}};
var _c = {json.dumps(cookie)};
var _h = [];
var document = {{addEventListener: function(t, f){{_h.push(f);}}}};
Object.defineProperty(document, 'cookie', {{get: function(){{return _c;}}, set: function(v){{OUT.cookies.push(v);}}}});
function G(n, v){{ Object.defineProperty(globalThis, n, {{value: v, configurable: true, writable: true}}); }}
G('navigator', {{sendBeacon: function(u, d){{OUT.beacons.push({{u: u, d: JSON.parse(d)}}); return true;}}}});
G('crypto', {{getRandomValues: function(a){{for (var i=0;i<a.length;i++) a[i]=3735928559+i; return a;}}}});
G('fetch', function(u, o){{OUT.fetches.push({{u: u, b: JSON.parse(o.body), k: o.keepalive}});}});
function click(sel, path){{ var a = sel ? {{pathname: path}} : null;
  _h.forEach(function(f){{ f({{target: {{closest: function(s){{ OUT.sel = s; return a; }}}}}}); }}); }}
"""


@needs_node
def test_beacon_sends_view_then_cta_for_a_product_link_only():
    script = _script_body(abtest.beacon_script("t1", "B", "https://listicle.example/e"))
    out = _run_js(_beacon_prelude() + "\n", script + "\nclick(true, '/products/peak-fuji');"
                  "\nclick(true, '/pages/about');\nclick(false, '');")
    views = [b for b in out["beacons"] if b["d"]["e"] == "view"]
    ctas = [b for b in out["beacons"] if b["d"]["e"] == "cta"]
    assert len(views) == 1 and len(ctas) == 1
    assert views[0]["u"] == "https://listicle.example/e"
    assert views[0]["d"]["t"] == "t1" and views[0]["d"]["v"] == "B"
    assert views[0]["d"]["vid"] == ctas[0]["d"]["vid"]
    assert out["cookies"][0].startswith("pk_vid=") and "SameSite=Lax" in out["cookies"][0]
    # the CTA click tags the cart so the order carries the variant
    assert out["fetches"] == [{"u": "/cart/update.js", "b": {"attributes": {"pk_ab": "t1:B"}}, "k": True}]
    for cls in abtest.CTA_CLASSES:
        assert "." + cls in out["sel"]


@needs_node
def test_beacon_reuses_an_existing_visitor_id():
    script = _script_body(abtest.beacon_script("t1", "A", "https://x/e"))
    out = _run_js(_beacon_prelude(cookie="pk_vid=abc123def456"), script)
    assert out["beacons"][0]["d"]["vid"] == "abc123def456"
    assert out["cookies"] == []


ARM_TEMPLATES = sorted(
    list((REPO_ROOT / "cartridges" / "listicle" / "looks").glob("*/template.html"))
    + [REPO_ROOT / "cartridges" / "comparison" / "template.html",
       REPO_ROOT / "cartridges" / "quiz" / "template.html"]
)


@pytest.mark.parametrize("template", ARM_TEMPLATES, ids=lambda p: f"{p.parent.parent.name}/{p.parent.name}")
def test_every_product_link_in_every_arm_template_is_a_detected_cta(template):
    import re
    anchors = re.findall(r"<a\b[^>]*>", template.read_text())
    product_links = [a for a in anchors
                     if re.search(r'href="\{\{\s*(page\.cta_url|model\.url|card\.url|cartridge_data\.all_models_url)', a)]
    assert product_links, template
    for a in product_links:
        classes = re.search(r'class="([^"]*)"', a).group(1).split()
        assert set(classes) & set(abtest.CTA_CLASSES), a


# ---------------------------------------------------------------------------
# 6. /e receiver
# ---------------------------------------------------------------------------

@pytest.fixture
def beacon_client(monkeypatch):
    monkeypatch.setenv("REVIEW_PASSWORD", "pw-for-tests")
    monkeypatch.delenv("REVIEW_TRUST_CF_ACCESS", raising=False)
    rec = {"test_id": "hc-ab12", "status": "live",
           "variants": [{"key": k, "arm": "quiz"} for k in "ABC"]}
    abtest.save_test(TENANT, rec)
    app = serve.build_app(TENANT)
    app.config["TESTING"] = True
    return app.test_client()


UA = "Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X) AppleWebKit/605.1.15 Mobile/15E148"


def _post(client, payload, ua=UA, ip="203.0.113.9", raw=None):
    return client.post("/e", data=raw if raw is not None else json.dumps(payload),
                       headers={"User-Agent": ua, "Content-Type": "text/plain;charset=UTF-8"},
                       environ_base={"REMOTE_ADDR": ip})


def _rows():
    conn = abevents.connect(abevents.db_path(TENANT))
    try:
        return conn.execute("SELECT test_id, variant, event, vid, iph FROM events").fetchall()
    finally:
        conn.close()


def test_receiver_accepts_a_valid_event_without_login(beacon_client):
    resp = _post(beacon_client, {"t": "hc-ab12", "v": "B", "e": "view", "vid": "abc123def456"})
    assert resp.status_code == 204
    rows = _rows()
    assert rows[0][:4] == ("hc-ab12", "B", "view", "abc123def456")
    assert "203.0.113.9" not in json.dumps(rows) and len(rows[0][4]) <= 16
    # the rest of the app is still behind the login
    assert beacon_client.get("/").status_code == 401
    conn = sqlite3.connect(abevents.db_path(TENANT))
    assert conn.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
    conn.close()


def test_receiver_validates_test_key_event_and_vid(beacon_client):
    ok = {"t": "hc-ab12", "v": "A", "e": "view", "vid": "abc123def456"}
    for bad in ({**ok, "t": "nope-0000"}, {**ok, "v": "D"}, {**ok, "e": "purchase"},
                {**ok, "vid": "x"}, {**ok, "t": "../../etc"}):
        assert _post(beacon_client, bad).status_code == 400, bad
    assert _post(beacon_client, None, raw="not json").status_code == 400
    assert _rows() == []


def test_receiver_refuses_a_body_over_1kb(beacon_client):
    big = json.dumps({"t": "hc-ab12", "v": "A", "e": "view", "vid": "abc123def456", "pad": "x" * 1100})
    assert _post(beacon_client, None, raw=big).status_code == 413
    assert _rows() == []


def test_receiver_drops_bots_and_duplicates(beacon_client):
    ev = {"t": "hc-ab12", "v": "A", "e": "view", "vid": "abc123def456"}
    for ua in ("facebookexternalhit/1.1", "Googlebot/2.1", "HeadlessChrome/120", ""):
        assert _post(beacon_client, ev, ua=ua).status_code == 204
    assert _rows() == []
    assert _post(beacon_client, ev).status_code == 204
    assert _post(beacon_client, ev).status_code == 204
    assert _post(beacon_client, {**ev, "e": "cta"}).status_code == 204
    assert len(_rows()) == 2


def test_receiver_rate_limits_per_ip(monkeypatch, beacon_client):
    monkeypatch.setattr(abevents, "RATE_LIMIT_PER_MIN", 5)
    app = serve.build_app(TENANT)
    client = app.test_client()
    codes = [_post(client, {"t": "hc-ab12", "v": "A", "e": "view", "vid": f"vid{i:08d}"}).status_code
             for i in range(7)]
    assert codes == [204] * 5 + [429] * 2
    other = _post(client, {"t": "hc-ab12", "v": "A", "e": "view", "vid": "otherip00001"}, ip="198.51.100.4")
    assert other.status_code == 204


# ---------------------------------------------------------------------------
# 7. orders
# ---------------------------------------------------------------------------

def test_order_attribution_parses_landing_site_and_cart_attribute():
    orders = [
        {"id": 1, "total_price": "2999.00", "landing_site": "/pages/lp-t1-a?utm_source=fb&pk_t=t1&pk_v=A"},
        {"id": 2, "total_price": "4999.50", "landing_site": "https://peaksaunas.com/pages/lp-t1-b?pk_v=B&pk_t=t1&fbclid=z"},
        {"id": 3, "total_price": "100.00", "landing_site": "/pages/lp-t1?pk_t=t1x&pk_v=A"},  # other test
        {"id": 4, "total_price": "100.00", "landing_site": "/?utm_source=pk_t=t1&pk_v=A"},  # not a real param
        {"id": 5, "total_price": "1500.00", "landing_site": "/pages/lp-t1",
         "note_attributes": [{"name": "pk_ab", "value": "t1:C"}]},
        {"id": 6, "total_price": "900.00", "landing_site": "/pages/lp-t1-a?pk_t=t1&pk_v=A",
         "cancelled_at": "2026-09-24T10:00:00Z"},
        {"id": 7, "total_price": "10.00", "landing_site": None},
    ]
    got = abtest.attribute_orders(orders, "t1", ["A", "B", "C"])
    assert got["A"] == {"orders": 1, "revenue": "2999.00"}
    assert got["B"] == {"orders": 1, "revenue": "4999.50"}
    assert got["C"] == {"orders": 1, "revenue": "1500.00"}


def test_list_orders_pages_by_since_id_over_the_admin_api():
    transport = FakeTransport()
    first = [{"id": i, "landing_site": "/"} for i in range(1, 251)]
    transport.set_response("GET", "since_id=0", 200, {"orders": first})
    transport.set_response("GET", "since_id=250", 200, {"orders": [{"id": 251, "landing_site": "/"}]})
    pub = ShopifyPublisher(store="acme.myshopify.com", token="tok", transport=transport)
    orders = pub.list_orders(created_at_min="2026-09-23T00:00:00Z")
    assert [o["id"] for o in orders][-1] == 251 and len(orders) == 251
    url = transport.calls[0]["url"]
    assert "orders.json?" in url and "status=any" in url and "created_at_min=2026-09-23T00%3A00%3A00Z" in url
    assert "landing_site" in url and "note_attributes" in url
    assert all(c["method"] == "GET" for c in transport.calls)


def test_orders_command_uses_the_publisher(monkeypatch, capsys):
    rec = _built_test(monkeypatch)
    rec["status"] = "live"
    rec["live_at"] = "2026-09-23T12:00:00"
    abtest.save_test(TENANT, rec)
    tid = rec["test_id"]
    transport = FakeTransport()
    transport.set_response("GET", "since_id=0", 200, {"orders": [
        {"id": 9, "total_price": "2500.00", "landing_site": f"/pages/lp-{tid}-c?pk_t={tid}&pk_v=C"}]})
    pub = ShopifyPublisher(store="acme.myshopify.com", token="tok", transport=transport)
    monkeypatch.setattr(cli, "_make_publisher", lambda tenant, *, export_dir: pub)
    assert cli.main(["abtest", "orders", tid, "--tenant", TENANT.name]) == 0
    out = capsys.readouterr().out
    assert "C" in out and "2500.00" in out


def test_pick_winner_breaks_a_near_tie_on_orders_only():
    rows = [
        {"key": "A", "p_best": 0.48, "orders": 1, "revenue": "3000.00"},
        {"key": "B", "p_best": 0.50, "orders": 0, "revenue": "0.00"},
        {"key": "C", "p_best": 0.02, "orders": 9, "revenue": "9000.00"},
    ]
    assert abtest.pick_winner(rows)["key"] == "A"  # B leads by < 0.05; C is not close
    no_orders = [{k: v for k, v in r.items() if k in ("key", "p_best")} for r in rows]
    assert abtest.pick_winner(no_orders)["key"] == "B"
    clear = [dict(rows[0], p_best=0.10), dict(rows[1], p_best=0.88), rows[2]]
    assert abtest.pick_winner(clear)["key"] == "B"
