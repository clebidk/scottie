"""Cycle 108: the automatic pipeline for new Meta ads (owner decision 2026-10-13).

The plan of each test from-inbox builds is chosen per ad (rule 1: the ad's own
specific store page is arm A; rule 2: the ad names a model, so C is its PDP;
rule 3: C is the home page); only ACTIVE / PENDING_REVIEW / IN_PROCESS ads
created on or after abtest.auto_since are built, at most auto_max_per_run per
run; an existing-post ad is built but marked "manual link"; each run writes
runs/auto-pipeline.json for the Ads page; crons/install-cron.sh puts the hourly
job in the crontab.

No network, no model calls: the cycle 88 BriefRunner and fake Shopify
transport, and a fake Meta transport (cycle 107's MetaFake).
"""
import json
import os
import subprocess

import pytest

from harness import abtest, abtest_inbox, meta_golive, meta_ingest
from tests.support import REPO_ROOT, TENANT, tenant_copy
from tests.test_control_arms_cycle88 import EVEREST, BriefRunner, _shopify
from tests.test_listicle_site_cycle69 import REVIEWER, _auth, _meta_item, client  # noqa: F401 -- fixture
from tests.test_meta_golive_cycle107 import ACCOUNT, AD, MetaFake, ad, video_creative
from tests.test_meta_ingest import make_client

PAGE = "https://peaksaunas.com/pages/i-regret-my-home-sauna"
HOME = "https://peaksaunas.com/"
PDP = EVEREST["url"]
GEN = {"kind": "generated"}


def _item(ad_id="120210000000108", name="Cold mornings", *, destination=HOME, status="ACTIVE",
          created="2026-10-13T10:00:00+0000", creative=None, **extra):
    _meta_item(ad_id, name)
    inbox = meta_ingest.Inbox(TENANT.meta_inbox_dir)
    item = inbox.read(ad_id)
    item.update(destination_url=destination, effective_status=status, created_time=created,
                meta={"creative": creative or video_creative(destination)}, **extra)
    inbox.write(item)
    return item


def _letters(rec):
    return {v["key"]: (v.get("target") or "generated", v.get("url")) for v in rec["variants"]}


def _off(monkeypatch, **extra):
    """Both levers off (the builds only), whatever the real tenant.yaml says."""
    tenant_copy(monkeypatch, {"abtest.auto_publish": False, "abtest.auto_go_live": False, **extra})


# ---------------------------------------------------------------------------
# 1. the three plan rules
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("destination, copy, name, letters", [
    (PAGE, "", "auto-page", {"A": ("url", PAGE), "B": ("generated", None), "C": ("generated", None)}),
    ("https://www.peaksaunas.com/pages/infrared-sauna-hsa-fsa?utm_source=x", "Everest", "auto-page",
     {"A": ("url", "https://www.peaksaunas.com/pages/infrared-sauna-hsa-fsa?utm_source=x"),
      "B": ("generated", None), "C": ("generated", None)}),
    (HOME, "Why I bought the Everest", "auto-pdp",
     {"A": ("generated", None), "B": ("generated", None), "C": ("pdp", PDP)}),
    (PDP, "Why I bought the Everest", "auto-pdp",
     {"A": ("generated", None), "B": ("generated", None), "C": ("pdp", PDP)}),
    (HOME, "Why I bought an infrared sauna", "auto-home",
     {"A": ("generated", None), "B": ("generated", None), "C": ("home", None)}),
    (PDP, "Why I bought an infrared sauna", "auto-home",
     {"A": ("generated", None), "B": ("generated", None), "C": ("home", None)}),
    ("https://peaksaunas.com/collections/saunas/products/x", "", "auto-home",
     {"A": ("generated", None), "B": ("generated", None), "C": ("home", None)}),
    ("https://peaksaunas.com/pages/lp-older-test-ab12", "", "auto-home",  # another test's split link
     {"A": ("generated", None), "B": ("generated", None), "C": ("home", None)}),
    ("https://example.com/pages/landing", "", "auto-home",  # not on the store
     {"A": ("generated", None), "B": ("generated", None), "C": ("home", None)}),
])
def test_the_plan_rules_and_their_letters(destination, copy, name, letters):
    item = {"ad_name": "Cold mornings", "primary_text": copy, "destination_url": destination}
    got_name, specs, rule = abtest_inbox.plan_for_item(item, TENANT)
    assert got_name == name and rule.startswith(f"rule {['auto-page', 'auto-pdp', 'auto-home'].index(name) + 1}:")
    shown = {k: (s.get("target") or "generated", s.get("url")) for k, s in zip(abtest.KEYS, specs, strict=False)}
    assert shown == letters
    assert abtest.validate_plan(specs)  # every automatic plan is an allowed shape


def test_a_built_test_records_the_rule_and_the_page_is_arm_a(monkeypatch):
    _off(monkeypatch)
    _item(destination=PAGE)
    runner = BriefRunner()
    summary = abtest_inbox.from_inbox(TENANT, by=REVIEWER, runner=runner, log=lambda *_: None)
    assert summary["built"] == ["120210000000108"]
    rec = abtest.list_tests(TENANT)[0]
    assert rec["plan"] == "auto-page" and rec["plan_rule"].startswith("rule 1:") and PAGE in rec["plan_rule"]
    assert _letters(rec) == {"A": ("url", PAGE), "B": ("generated", None), "C": ("generated", None)}
    assert rec["variants"][0]["status"] == "ready" and len(runner.calls) == 2
    assert abtest.destinations_label(rec) == "A = URL, B = generated, C = generated"


def test_rule_2_names_the_pdp_before_any_build_and_an_item_or_flag_plan_still_wins(monkeypatch):
    _off(monkeypatch)
    _item("120210000000201", "Everest morning", destination=HOME)
    _item("120210000000202", "Own plan", destinations="gen2-home")
    runner = BriefRunner()
    abtest_inbox.from_inbox(TENANT, by=REVIEWER, runner=runner, log=lambda *_: None)
    recs = {r["name"]: r for r in abtest.list_tests(TENANT)}
    assert recs["Everest morning"]["plan"] == "auto-pdp"
    assert recs["Everest morning"]["variants"][2]["url"] == PDP
    assert recs["Own plan"]["plan"] == "gen2-home" and "plan_rule" not in recs["Own plan"]
    _item("120210000000203", "Flagged", destination=PAGE)
    abtest_inbox.from_inbox(TENANT, by=REVIEWER, runner=runner, log=lambda *_: None, plan="generated")
    assert {r["name"]: r for r in abtest.list_tests(TENANT)}["Flagged"]["plan"] == "generated"


# ---------------------------------------------------------------------------
# 2. parse_plan honours the order; the presets keep their letters
# ---------------------------------------------------------------------------

def test_an_external_arm_listed_first_is_arm_a_and_presets_keep_their_letters():
    url = {"kind": "external", "target": "url", "url": PAGE}
    name, specs = abtest.parse_plan(json.dumps([url, GEN, GEN]))
    assert name == "custom" and specs[0] == url
    variants = abtest._new_variants(specs, ["landing:open", "landing:proof", "landing:cinematic"])
    assert [(v["key"], v["arm"]) for v in variants] == [("A", "external:url"), ("B", "landing:open"),
                                                         ("C", "landing:proof")]
    assert variants[0]["url"] == PAGE and variants[0]["status"] == "ready"
    expected = {"generated": "gen gen gen", "gen2-pdp": "gen gen pdp", "gen2-home": "gen gen home",
                "gen1-pdp-home": "gen pdp home", "pdp-only": "pdp", "home-only": "home"}
    for preset, shape in expected.items():
        got = abtest._new_variants(abtest.parse_plan(preset)[1], ["x:1", "x:2", "x:3"])
        assert [v["key"] for v in got] == list(abtest.KEYS[:len(got)])
        assert " ".join("gen" if not abtest.is_external(v) else v["target"] for v in got) == shape, preset


def test_the_split_page_and_the_utm_keys_follow_the_letters(monkeypatch):
    _off(monkeypatch)
    _item(destination=PAGE)
    abtest_inbox.from_inbox(TENANT, by=REVIEWER, runner=BriefRunner(), log=lambda *_: None)
    rec = abtest.list_tests(TENANT)[0]
    tid = rec["test_id"]
    transport = _shopify(monkeypatch, [f"lp-{tid}-b", f"lp-{tid}-c", f"lp-{tid}"])
    url, error = abtest_inbox.publish_test(TENANT, tid, by=REVIEWER)
    assert error is None and url
    posts = [json.loads(c["body"])["page"] for c in transport.calls if c["url"].endswith("pages.json")]
    assert [p["handle"] for p in posts] == [f"lp-{tid}-b", f"lp-{tid}-c", f"lp-{tid}"]  # no page for arm A
    split = posts[-1]["body_html"]
    assert ('"A":"/pages/i-regret-my-home-sauna","B":"/pages/lp-' + tid + '-b","C":"/pages/lp-' + tid + '-c"'
            in split)
    assert "'utm_content='+T+'_'+v" in split  # utm_content=<test>_<the arm's own letter>
    assert f'"{tid}"' in posts[0]["body_html"] and "V=\"B\"" in posts[0]["body_html"]  # B's beacon says B


# ---------------------------------------------------------------------------
# 3. eligibility: status and auto_since
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("status, built", [
    ("ACTIVE", True), ("PENDING_REVIEW", True), ("IN_PROCESS", True),
    ("PAUSED", False), ("CAMPAIGN_PAUSED", False), ("ADSET_PAUSED", False), ("WITH_ISSUES", False), ("", False),
])
def test_only_active_or_in_review_ads_are_built(monkeypatch, status, built):
    _off(monkeypatch)
    _item(status=status)
    summary = abtest_inbox.from_inbox(TENANT, by=REVIEWER, runner=BriefRunner(), log=lambda *_: None)
    item = meta_ingest.Inbox(TENANT.meta_inbox_dir).read("120210000000108")
    assert (summary["built"] == ["120210000000108"]) is built
    if not built:
        assert item["state"] == "new" and summary["skipped"]["120210000000108"].startswith(
            f"ad status {status or 'unknown'}")
        assert item["history"] == []  # nothing written to the item


def test_ads_created_before_auto_since_stay_new_and_a_paused_ad_is_built_once_active(monkeypatch):
    _off(monkeypatch)
    assert TENANT.config["abtest"]["auto_since"] == "2026-10-13"
    _item("120210000000301", "Old one", created="2026-10-12T23:59:59+0000")
    _item("120210000000302", "Paused", status="CAMPAIGN_PAUSED")
    _item("120210000000303", "Edge", created="2026-10-12T17:00:00-0700")  # 00:00 UTC on the 13th
    logs = []
    summary = abtest_inbox.from_inbox(TENANT, by=REVIEWER, runner=BriefRunner(), log=logs.append)
    assert summary["built"] == ["120210000000303"]
    assert summary["skipped"]["120210000000301"] == "created 2026-10-12, before abtest.auto_since 2026-10-13"
    assert "not auto-built (listed on the Ads page): 1 before auto_since, 1 paused" in logs
    inbox = meta_ingest.Inbox(TENANT.meta_inbox_dir)
    item = inbox.read("120210000000302")
    item["effective_status"] = "ACTIVE"  # what refresh-links stores once the owner turns it on
    inbox.write(item)
    summary = abtest_inbox.from_inbox(TENANT, by=REVIEWER, runner=BriefRunner(), log=lambda *_: None)
    assert summary["built"] == ["120210000000302"] and list(summary["skipped"]) == ["120210000000301"]


def test_an_upload_and_a_manual_create_ignore_the_rules(monkeypatch):
    _off(monkeypatch)
    _item("120210000000401", "Paused", status="PAUSED", created="2026-09-30T10:00:00+0000")
    upload = {"source": "upload", "state": "new", "effective_status": "", "created_time": ""}
    assert abtest_inbox.not_auto_built(TENANT, upload) is None
    rc, rec = abtest.create_test(TENANT, input_path="fixture.mov", name="Manual", seed=3, runner=BriefRunner())
    assert rc == 0 and rec["plan"] == "generated"  # `harness abtest create` is not the inbox
    item_dir = meta_ingest.Inbox(TENANT.meta_inbox_dir).item_dir("120210000000401")
    media = item_dir / meta_ingest.Inbox(TENANT.meta_inbox_dir).read("120210000000401")["media_file"]
    rc, rec = abtest.create_test(TENANT, input_path=media, name="Paused by hand", seed=4, runner=BriefRunner())
    assert rc == 0


# ---------------------------------------------------------------------------
# 4. the per-run limit
# ---------------------------------------------------------------------------

def test_at_most_auto_max_per_run_items_are_built_per_run(monkeypatch, capsys):
    _off(monkeypatch)
    assert abtest.settings(TENANT)["auto_max_per_run"] == 5
    for i in range(7):
        _item(f"12021000000050{i}", f"Ad {i}")
    summary = abtest_inbox.from_inbox(TENANT, by=REVIEWER, runner=BriefRunner(), log=print)
    assert len(summary["built"]) == 5 and summary["waiting"] == ["120210000000505", "120210000000506"]
    assert "at most 5 item(s) per run (abtest.auto_max_per_run 5): 2 wait for the next run" in capsys.readouterr().out
    inbox = meta_ingest.Inbox(TENANT.meta_inbox_dir)
    assert [inbox.read(a)["state"] for a in summary["waiting"]] == ["new", "new"]
    assert abtest_inbox.from_inbox(TENANT, by=REVIEWER, runner=BriefRunner(), log=lambda *_: None,
                                   limit=1)["built"] == ["120210000000505"]
    monkeypatch.setitem(TENANT.config["abtest"], "auto_max_per_run", 0)  # 0 stops auto builds
    assert abtest_inbox.from_inbox(TENANT, by=REVIEWER, runner=BriefRunner(), log=lambda *_: None)["built"] == []


# ---------------------------------------------------------------------------
# 5. manual link, and the lever never touches older tests
# ---------------------------------------------------------------------------

def test_an_existing_post_ad_is_built_but_marked_manual_link_and_never_goes_live(monkeypatch):
    tenant_copy(monkeypatch, {"abtest.auto_publish": True, "abtest.auto_go_live": True})
    _item(creative={"id": "c9", "effective_object_story_id": "111_999"})
    monkeypatch.setattr(abtest_inbox, "publish_test", lambda tenant, test_id, *, by: _fake_publish(test_id))
    monkeypatch.setattr(meta_golive, "client_for", lambda tenant: pytest.fail("Meta called for a manual link ad"))
    summary = abtest_inbox.from_inbox(TENANT, by=REVIEWER, runner=BriefRunner(), log=lambda *_: None)
    rec = abtest.list_tests(TENANT)[0]
    assert summary["published"] == ["120210000000108"] and summary["went_live"] == []
    assert rec["manual_link"] == abtest_inbox.MANUAL_LINK and "auto_go_live" not in rec
    item = meta_ingest.Inbox(TENANT.meta_inbox_dir).read("120210000000108")
    assert "Meta go-live skipped: manual link" in item["reason"]


def _fake_publish(test_id):
    rec = abtest.load_test(TENANT, test_id)
    url = f"https://peaksaunas.com/pages/lp-{test_id}"
    rec.update(status="live", split={"url": url, "handle": f"lp-{test_id}", "page_id": 5})
    abtest.save_test(TENANT, rec)
    return url, None


def test_the_lever_leaves_a_test_created_before_auto_since_alone(monkeypatch):
    tenant_copy(monkeypatch, {"abtest.auto_publish": True, "abtest.auto_go_live": True})
    item = _item()
    rec = {"test_id": "older-test-ab34", "name": "Older", "status": "built", "reason": "",
           "created_at": "2026-10-12T22:00:00", "source": {"kind": "meta", "ad_id": item["ad_id"]},
           "variants": [], "split": None, "winner": None}
    abtest.save_test(TENANT, rec)
    inbox = meta_ingest.Inbox(TENANT.meta_inbox_dir)
    inbox.set_state(item["ad_id"], "queued", "picked")
    inbox.set_state(item["ad_id"], "building", "building")
    inbox.note(item["ad_id"], "building", test_id=rec["test_id"])
    monkeypatch.setattr(abtest_inbox, "publish_test", lambda tenant, test_id, *, by: _fake_publish(test_id))
    monkeypatch.setattr(meta_golive, "client_for", lambda tenant: pytest.fail("Meta called for an older test"))
    outcome = abtest_inbox.build_test_for_item(TENANT, item["ad_id"], by=REVIEWER)
    assert outcome["meta_go_live"] is None
    assert "Meta go-live skipped: test created before abtest.auto_since 2026-10-13" in outcome["reason"]


# ---------------------------------------------------------------------------
# 6. the status file and the Ads page
# ---------------------------------------------------------------------------

def test_each_run_writes_the_status_file_and_the_ads_page_shows_it(monkeypatch, client):  # noqa: F811
    _off(monkeypatch)
    _item("120210000000601", "Builds")
    _item("120210000000602", "Paused one", status="PAUSED")
    abtest_inbox.write_status(TENANT, "pull", {"at": "2026-10-13T14:07:00", "ingested": 4})
    abtest_inbox.from_inbox(TENANT, by=REVIEWER, runner=BriefRunner(), log=lambda *_: None)
    status = json.loads((TENANT.runs_dir / "auto-pipeline.json").read_text())
    run = status["inbox"]
    assert status["pull"]["ingested"] == 4
    assert (run["built"], run["published"], run["went_live"], run["skipped"]) == (1, 0, 0, 1)
    assert run["skipped_by"] == {"paused": 1} and run["inbox_plan"] == "auto"
    assert run["items"][0]["plan"] == "auto-home" and run["items"][0]["plan_rule"].startswith("rule 3:")
    line = abtest_inbox.status_line(TENANT, today=run["at"][:10])
    assert line == (f"Auto pipeline: last run {run['at'][11:16]}, pulled 4, 1 built, 0 live, 1 skipped (paused), "
                    "$0.00 spend (auto publish off, auto go-live off)")
    body = client.get("/", headers=_auth()).get_data(as_text=True)
    assert "Auto pipeline: last run" in body and "1 skipped (paused)" in body
    assert "not auto-built: ad status PAUSED; it is built once it is ACTIVE, PENDING_REVIEW or IN_PROCESS" in body
    assert "Plan auto-home: rule 3: the ad names no model" in body


def test_the_meta_pull_command_records_its_count(monkeypatch, capsys):
    from harness import cli

    monkeypatch.setattr(cli, "_meta_setup", lambda tenant: ({"ad_account_id": ACCOUNT, "ingest_since": None}, None))
    monkeypatch.setattr(meta_ingest, "pull", lambda *a, **k: {"listed": 2, "ingested": ["1", "2"], "existing": [],
                                                              "failed": [], "skipped": [], "would_ingest": [],
                                                              "refused": []})
    assert cli.main(["meta", "pull", "--tenant", TENANT.name]) == 0
    assert abtest_inbox.read_status(TENANT)["pull"]["ingested"] == 2


# ---------------------------------------------------------------------------
# 7. end to end: a new ACTIVE ad with its own page -> built, published, live
# ---------------------------------------------------------------------------

class EchoMeta(MetaFake):
    """MetaFake whose read-back shows the creative go-live just posted."""

    def __call__(self, method, url, *, headers, body=None):
        result = super().__call__(method, url, headers=headers, body=body)
        call = self.calls[-1]
        if method == "POST" and call["path"].endswith("/adcreatives"):
            spec = json.loads(call["form"]["object_story_spec"])
            self.routes[("GET", call["path"].replace(f"{ACCOUNT}/adcreatives", AD))] = [
                (200, json.dumps({"id": AD, "effective_status": "ACTIVE", "creative": {
                    "id": "c2", "url_tags": "", "object_story_spec": spec}}).encode())]
        return result


def test_end_to_end_a_new_active_ad_with_its_own_page_goes_live(monkeypatch):
    tenant_copy(monkeypatch, {"abtest.auto_publish": True, "abtest.auto_go_live": True})
    _item(AD, "Cold mornings", destination=PAGE)
    fake = EchoMeta()
    fake.add("GET", AD, ad(video_creative(PAGE)))
    fake.add("GET", "111", {"id": "111", "instagram_business_account": {"id": "222"}})
    fake.add("POST", f"{ACCOUNT}/adcreatives", {"id": "c2"})
    fake.add("POST", AD, {"success": True})
    monkeypatch.setattr(meta_golive, "client_for", lambda tenant: make_client(fake))
    shopify = {}

    def publish(tenant, test_id, *, by):
        shopify["t"] = _shopify(monkeypatch, [f"lp-{test_id}-b", f"lp-{test_id}-c", f"lp-{test_id}"])
        return real_publish(tenant, test_id, by=by)

    real_publish = abtest_inbox.publish_test
    monkeypatch.setattr(abtest_inbox, "publish_test", publish)
    logs = []
    summary = abtest_inbox.from_inbox(TENANT, by=REVIEWER, runner=BriefRunner(), log=logs.append)

    rec = abtest.list_tests(TENANT)[0]
    tid = rec["test_id"]
    assert summary["built"] == [AD] and summary["published"] == [AD] and summary["went_live"] == [tid]
    assert _letters(rec)["A"] == ("url", PAGE) and rec["plan"] == "auto-page"
    split_url = rec["split"]["url"]
    assert rec["meta_go_live"]["verified"] is True and rec["meta_go_live"]["split_url"] == split_url
    assert rec["auto_go_live"]["state"] == "done"
    posted = json.loads(next(c for c in fake.calls if c["path"].endswith("/adcreatives"))["form"]["object_story_spec"])
    assert posted["video_data"]["call_to_action"]["value"]["link"] == split_url
    split_body = json.loads(shopify["t"].calls[-1]["body"])["page"]["body_html"]
    assert '"A":"/pages/i-regret-my-home-sauna"' in split_body
    item = meta_ingest.Inbox(TENANT.meta_inbox_dir).read(AD)
    assert item["state"] == "tested" and item["destination_url"] == split_url
    assert any(f"{AD} (Cold mornings): plan auto-page -- rule 1:" in line for line in logs)
    run = abtest_inbox.read_status(TENANT)["inbox"]
    assert (run["built"], run["published"], run["went_live"], run["pending"]) == (1, 1, 1, 0)
    assert run["auto_publish"] is True and run["auto_go_live"] is True


# ---------------------------------------------------------------------------
# 8. the tenant settings and the crontab
# ---------------------------------------------------------------------------

def test_peak_has_the_automatic_plan_and_the_levers_are_left_to_the_owner():
    from tests.test_abtest_cycle67 import ConfigTenant

    text = (TENANT.root / "tenant.yaml").read_text()
    cfg = TENANT.config["abtest"]
    assert (cfg["inbox_plan"], cfg["auto_since"], cfg["auto_max_per_run"]) == ("auto", "2026-10-13", 5)
    # the levers are the owner's (on 2026-10-13): only booleans read from the config
    for lever in ("auto_publish", "auto_go_live"):
        assert isinstance(cfg[lever], bool) and abtest.settings(TENANT)[lever] is cfg[lever]
    fresh = abtest.settings(ConfigTenant())
    assert (fresh["auto_publish"], fresh["auto_go_live"], fresh["inbox_plan"], fresh["auto_since"],
            fresh["auto_max_per_run"]) == (False, False, "auto", None, 5)
    assert "lets turn it on. only exception is" in text


def _crontab(tmp_path, existing):
    """A fake `crontab` first on PATH: `-l` prints the stored file (and fails
    with "no crontab" when there is none, like the real one); `-` stores stdin."""
    store = tmp_path / "crontab.txt"
    if existing is not None:
        store.write_text(existing)
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    fake = bin_dir / "crontab"
    fake.write_text("#!/bin/bash\n"
                    f'if [ "$1" = "-l" ]; then [ -f "{store}" ] || {{ echo "no crontab for deploy" >&2; exit 1; }}; '
                    f'cat "{store}"; else cat > "{store}"; fi\n')
    fake.chmod(0o755)
    env = {**os.environ, "PATH": f"{bin_dir}:{os.environ['PATH']}", "HARNESS_REPO": str(REPO_ROOT)}

    def run(*args):
        return subprocess.run(["bash", str(REPO_ROOT / "crons" / "install-cron.sh"), *args, "peak-saunas"],
                              env=env, check=True, capture_output=True, text=True)
    return store, run


CRON_LINE = f"7 * * * * {REPO_ROOT}/crons/meta-pull.sh peak-saunas"
BACKUP = "0 3 * * * /home/deploy/advertorial/crons/backup-tenant-data.sh peak-saunas"


@pytest.mark.skipif(os.geteuid() == 0, reason="the script refuses root")
@pytest.mark.parametrize("existing, expected", [
    (None, [CRON_LINE]),  # no crontab yet
    ("", [CRON_LINE]),  # an empty crontab
    (BACKUP + "\n", [BACKUP, CRON_LINE]),  # one unrelated line (prod, 2026-10-13)
    ("MAILTO=ops@example.com\n# 7 * * * * /x/crons/meta-pull.sh peak-saunas\n" + BACKUP,
     ["MAILTO=ops@example.com", "# 7 * * * * /x/crons/meta-pull.sh peak-saunas", BACKUP, CRON_LINE]),
])
def test_install_cron_adds_the_line_once_and_a_second_run_changes_nothing(tmp_path, existing, expected):
    store, run = _crontab(tmp_path, existing)
    assert "installed: " + CRON_LINE in run().stdout
    assert store.read_text().splitlines() == expected
    second = run()
    assert "already installed, crontab not changed" in second.stdout
    assert store.read_text().splitlines() == expected
    assert store.read_text().count("crons/meta-pull.sh peak-saunas\n") == 1 + (existing or "").count("# 7")


@pytest.mark.skipif(os.geteuid() == 0, reason="the script refuses root")
def test_install_cron_leaves_an_existing_meta_pull_line_alone_and_remove_takes_it_out(tmp_path):
    old = "15 * * * * /home/deploy/advertorial/crons/meta-pull.sh peak-saunas >> /tmp/x.log 2>&1\n" + BACKUP + "\n"
    store, run = _crontab(tmp_path, old)
    assert "already installed" in run().stdout and store.read_text() == old
    run("--remove")
    assert store.read_text().splitlines() == [BACKUP]
    run()
    assert store.read_text().splitlines() == [BACKUP, CRON_LINE]
