"""Cycle 69: the listicle site (listicle.peaksaunasteam.com) -- the ads home,
the generation page with feedback -> regenerate, "Replace live variant" /
"Publish" with a confirm step, ad upload -> inbox item -> create-test job,
`harness abtest from-inbox`, the job queue + worker, and the audit log.

No network, no model calls: regenerate uses a fake revise that writes a new
version with the real versioning helpers, the harness runner is
tests/test_abtest_cycle67.py's FakeRunner, and Shopify is a real
ShopifyPublisher over tests/test_publishers.py's FakeTransport.
"""
import base64
import datetime
import json
import re
import threading
from pathlib import Path

import pytest

from harness import abevents, abtest, abtest_inbox, ads, budget, cli, jobs, meta_ingest, revise, runstate
from harness import serve, site, upload
from harness.publishers.shopify import ShopifyPublisher
from tests.support import REPO_ROOT, TENANT
from tests.test_abtest_cycle67 import FakeRunner, _insert
from tests.test_publishers import FakeTransport

REVIEWER = "caleb@peaksaunas.com"
OTHER = "michael@peaksaunas.com"
PASSWORD = "pw-cycle-69"

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64
JPG = b"\xff\xd8\xff\xe0" + b"\x00" * 64
WEBP = b"RIFF\x24\x00\x00\x00WEBPVP8 " + b"\x00" * 64
MP4 = b"\x00\x00\x00\x18ftypisom\x00\x00\x02\x00isomiso2" + b"\x00" * 64
MOV = b"\x00\x00\x00\x14ftypqt  \x00\x00\x02\x00qt  " + b"\x00" * 64


def _auth(email=REVIEWER):
    token = base64.b64encode(f"{email}:{PASSWORD}".encode()).decode()
    return {"Authorization": f"Basic {token}"}


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setenv("REVIEW_PASSWORD", PASSWORD)
    monkeypatch.delenv("REVIEW_TRUST_CF_ACCESS", raising=False)
    app = serve.build_app(TENANT)
    app.testing = True
    return app.test_client()


def _csrf(email=REVIEWER):
    return site.csrf_token(email)


def _fake_revise(run_dir, page, *, by=None, tenant=None, **_kw):
    """What revise.revise_page leaves on disk, minus the model call."""
    run_dir = Path(run_dir)
    cart = run_dir / page
    version = revise.next_version(cart)
    revise._version_existing_files(run_dir, page, version)
    (cart / "page.json").write_text(json.dumps({"headline": f"revised {version + 1}"}))
    (cart / "index.html").write_text(
        "<html><head><style>.pk-lp{color:red}</style></head><body><div class=\"pk-lp adv-wrap\">"
        f"<h1>REVISED BODY {version + 1}</h1>"
        '<a class="lst-btn" href="https://peaksaunas.com/products/peak-fuji">Shop the Fuji</a>'
        "</div></body></html>"
    )
    (run_dir / f"{page}-review.html").write_text(f"<html><body>REVISED REVIEW {version + 1}</body></html>")
    runstate.mark_revised(run_dir, page=page, version=version, by=by or "system", note="gate PASS")
    runstate.set_revise_status(run_dir, page=page, status="done", detail="gate PASS")
    return {"page": page, "version": version, "model_called": True, "applied_cuts": [],
            "gate_problems": [], "cost": 0.0}


def _built_test(monkeypatch, name="Hidden Costs V2", seed=7):
    monkeypatch.setattr(abtest, "default_runner", FakeRunner())
    rc, rec = abtest.create_test(TENANT, input_path="fixture.mov", name=name, seed=seed,
                                 runner=abtest.default_runner)
    assert rc == 0
    return rec


def _fake_shopify(monkeypatch, test_id=None, *, start_id=100):
    transport = FakeTransport()
    handles = [f"lp-{test_id}-a", f"lp-{test_id}-b", f"lp-{test_id}-c", f"lp-{test_id}"] if test_id else ["x"]
    for i, handle in enumerate(handles):
        transport.set_response("POST", "pages.json", 201, {"page": {"id": start_id + i, "handle": handle}})
    for i in range(4):
        transport.set_response("PUT", f"pages/{start_id + i}.json", 200,
                               {"page": {"id": start_id + i, "handle": handles[min(i, len(handles) - 1)]}})
    publisher = ShopifyPublisher(store="acme.myshopify.com", token="tok",
                                 transport=transport, upload_transport=transport)
    monkeypatch.setattr(cli, "_make_publisher", lambda tenant, *, export_dir: publisher)
    monkeypatch.setattr(ShopifyPublisher, "verify_cache", lambda self, *a, **k: (8, 8))
    return transport


def _live_test(monkeypatch):
    rec = _built_test(monkeypatch)
    transport = _fake_shopify(monkeypatch, rec["test_id"])
    assert cli.main(["abtest", "publish", rec["test_id"], "--by", REVIEWER, "--tenant", TENANT.name]) == 0
    return abtest.load_test(TENANT, rec["test_id"]), transport


def _older_run(source_file, *, page="listicle", real=True):
    """A run made before cycle 67 (no test): ad_brief.source_file names the ad."""
    run_id, run_dir = cli.make_run_dir(TENANT.out_dir, Path(source_file).name.split(".")[0])
    cart = run_dir / page
    cart.mkdir()
    (cart / "index.html").write_text(
        "<html><head><style>.pk-lp{color:red}</style></head><body><div class=\"pk-lp adv-wrap\">"
        "<h1>older page</h1></div></body></html>"
    )
    (cart / "page.json").write_text(json.dumps({"headline": "older"}))
    (run_dir / "ad_brief.json").write_text(json.dumps({"source_file": source_file, "hook": "h"}))
    runstate.init_state(run_dir, pages=[page])
    runstate.mark_needs_review(run_dir)
    runstate.init_packet(run_dir)
    if real:
        TENANT.runs_dir.mkdir(parents=True, exist_ok=True)
        (TENANT.runs_dir / f"{run_dir.name}.log").write_text("write input_tokens=100 output_tokens=10\n")
    return run_dir


def _meta_item(ad_id, name, *, state="new", media=b"", media_file=None):
    inbox = meta_ingest.Inbox(TENANT.meta_inbox_dir)
    directory = inbox.item_dir(ad_id)
    directory.mkdir(parents=True, exist_ok=True)
    media_file = media_file or f"meta-{ad_id}.jpg"
    (directory / media_file).write_bytes(media or JPG)
    item = {"ad_id": ad_id, "ad_name": name, "created_time": "2026-09-23T10:00:00+0000",
            "media_type": "image", "media_file": media_file, "source": "meta",
            "state": state, "reason": "", "history": []}
    inbox.write(item)
    return item


def _upload(client, *, name="Cold Plunge Myths", data=PNG, filename="ad.png", email=REVIEWER, extra=None,
            csrf=None):
    import io
    form = {"ad_name": name, "csrf": csrf if csrf is not None else _csrf(email),
            "media": (io.BytesIO(data), filename)}
    form.update(extra or {})
    return client.post("/upload", data=form, headers=_auth(email), content_type="multipart/form-data")


def _feedback(client, run_dir, page, text, *, email=REVIEWER, csrf=None):
    return client.post(f"/gen/{Path(run_dir).name}/{page}/feedback",
                       data={"feedback": text, "csrf": csrf if csrf is not None else _csrf(email)},
                       headers=_auth(email))


# ---------------------------------------------------------------------------
# 1. isolation + small units
# ---------------------------------------------------------------------------

def test_jobs_dir_is_redirected_by_the_isolation_fixture(isolated_tenant_paths):
    assert TENANT.jobs_dir == isolated_tenant_paths / "jobs"
    from tests import conftest
    assert "jobs" in conftest.TRACKED_TENANT_SUBDIRS


@pytest.mark.real_tenant_paths
def test_jobs_dir_lives_under_the_tenant_root():
    assert TENANT.jobs_dir == TENANT.root / "jobs"


def test_gitignore_keeps_jobs_out_of_the_repo():
    assert "tenants/*/jobs/" in (REPO_ROOT / ".gitignore").read_text().splitlines()


@pytest.mark.parametrize("head,ext", [(PNG, ".png"), (JPG, ".jpg"), (WEBP, ".webp"), (MP4, ".mp4"), (MOV, ".mov")])
def test_sniff_reads_the_type_from_magic_bytes(head, ext):
    assert upload.sniff(head)[1] == ext


def test_sniff_refuses_text_and_unknown_bytes():
    assert upload.sniff(b"<html><script>") is None
    assert upload.sniff(b"GIF89a....") is None
    assert upload.sniff(b"") is None


def test_inbox_accepts_upload_ids_but_nothing_path_like():
    inbox = meta_ingest.Inbox(TENANT.meta_inbox_dir)
    assert inbox.item_dir("up-ab12cd34").name == "up-ab12cd34"
    assert inbox.item_dir("120210000000001").name == "120210000000001"
    for bad in ("up-../x", "up-", "../etc", "up-AB/../..", "up-a b"):
        with pytest.raises(ValueError):
            inbox.item_dir(bad)


def test_ad_key_groups_names_and_source_files():
    assert ads.ad_key("Hidden Costs V2") == ads.ad_key("hidden-costs-v2") == "hidden costs v2"
    assert ads.name_from_source_file("hidden-costs-v2.transcript.txt") == "hidden-costs-v2"
    assert ads.name_from_source_file("dir/product-features-v2.wav") == "product-features-v2"


def test_auto_publish_setting_defaults_false_and_is_on_for_peak():
    from tests.test_abtest_cycle67 import ConfigTenant
    assert abtest.settings(ConfigTenant())["auto_publish"] is False
    assert abtest.settings(ConfigTenant({"auto_publish": True}))["auto_publish"] is True
    assert abtest.settings(TENANT)["auto_publish"] is True
    template = (REPO_ROOT / "tenants" / "_template" / "tenant.yaml").read_text()
    assert re.search(r"^\s+auto_publish: false\s*$", template, re.M)


# ---------------------------------------------------------------------------
# 2. ads home: grouping by ad name across sources, search, pagination, spend
# ---------------------------------------------------------------------------

def test_ads_group_by_name_across_tests_inbox_uploads_and_older_runs(monkeypatch, client):
    rec = _built_test(monkeypatch, name="Hidden Costs V2")
    older = _older_run("hidden-costs-v2.transcript.txt")
    _older_run("price-comparison-v2.mov")
    _meta_item("120210000000001", "hidden costs v2")
    _meta_item("120210000000002", "Sauna Myths")

    groups = {a["key"]: a for a in ads.collect_ads(TENANT)}
    assert set(groups) == {"hidden costs v2", "price comparison v2", "sauna myths"}
    hc = groups["hidden costs v2"]
    assert [t["test_id"] for t in hc["tests"]] == [rec["test_id"]]
    assert [i["ad_id"] for i in hc["inbox"]] == ["120210000000001"]
    assert [r["run_id"] for r in hc["runs"]] == [older.name]
    # a test variant's own run dir never shows up again as an "older run"
    variant_runs = {Path(v["run_dir"]).name for v in rec["variants"]}
    assert not variant_runs & {r["run_id"] for a in groups.values() for r in a["runs"]}
    assert len(hc["variants"]) == 3 and {v["arm"] for v in hc["variants"]} == {v["arm"] for v in rec["variants"]}

    body = client.get("/", headers=_auth()).get_data(as_text=True)
    for name in ("Hidden Costs V2", "price-comparison-v2", "Sauna Myths"):
        assert name in body
    for v in rec["variants"]:
        assert f"/gen/{Path(v['run_dir']).name}/{v['cartridge']}" in body


def test_ads_home_search_and_pagination(monkeypatch, client):
    for i in range(5):
        _meta_item(f"12021000000010{i}", f"Ad number {i}")
    monkeypatch.setattr(site, "ADS_PER_PAGE", 2)
    body = client.get("/?q=number%203", headers=_auth()).get_data(as_text=True)
    assert "Ad number 3" in body and "Ad number 1" not in body
    page1 = client.get("/", headers=_auth()).get_data(as_text=True)
    page3 = client.get("/?page=3", headers=_auth()).get_data(as_text=True)
    assert "page 1 of 3" in page1 and "page 3 of 3" in page3
    shown = lambda b: set(re.findall(r"Ad number \d", b))  # noqa: E731
    assert len(shown(page1)) == 2 and len(shown(page3)) == 1 and not shown(page1) & shown(page3)


def test_ads_home_shows_live_stats_split_link_and_spend(monkeypatch, client):
    rec, _transport = _live_test(monkeypatch)
    db = abevents.db_path(TENANT)
    _insert(db, rec["test_id"], "A", "view", 40)
    _insert(db, rec["test_id"], "A", "cta", 10)
    _insert(db, rec["test_id"], "B", "view", 40)
    TENANT.runs_dir.mkdir(parents=True, exist_ok=True)
    today = datetime.date.today().isoformat()
    (TENANT.runs_dir / "spend-ledger.jsonl").write_text(
        json.dumps({"date": today, "run_id": "r1", "cost_estimate": 1.25}) + "\n")
    body = client.get("/", headers=_auth()).get_data(as_text=True)
    assert rec["split"]["url"] in body and "Copy" in body
    assert "25.0%" in body  # A: 10 of 40
    assert "P(best)" in body
    assert "$1.25" in body and "$10.00" in body


# ---------------------------------------------------------------------------
# 3. generation page: feedback -> regenerate job -> new version
# ---------------------------------------------------------------------------

def _single_run(monkeypatch):
    rec = _built_test(monkeypatch)
    v = rec["variants"][0]
    return Path(v["run_dir"]), v["cartridge"]


def test_feedback_is_required_and_capped(monkeypatch, client):
    run_dir, page = _single_run(monkeypatch)
    assert _feedback(client, run_dir, page, "   ").status_code == 400
    assert _feedback(client, run_dir, page, "x" * 2001).status_code == 400
    assert not runstate.load_state(run_dir).get("feedback")
    assert jobs.list_jobs(TENANT) == []


def test_feedback_records_author_and_queues_one_regenerate_job(monkeypatch, client):
    run_dir, page = _single_run(monkeypatch)
    resp = _feedback(client, run_dir, page, "Make the headline shorter.\ncut: An exact sentence.")
    assert resp.status_code == 303
    fb = runstate.load_state(run_dir)["feedback"][-1]
    assert fb["by"] == REVIEWER and fb["page"] == page and fb["at"]
    assert fb["notes"] == "Make the headline shorter." and fb["cuts"] == ["An exact sentence."]
    [job] = jobs.list_jobs(TENANT)
    assert job["type"] == "regenerate" and job["state"] == "queued" and job["by"] == REVIEWER
    assert job["payload"] == {"run_id": run_dir.name, "page": page, "by": REVIEWER}
    # a second feedback while that job waits is refused (revise reads only the last one)
    assert _feedback(client, run_dir, page, "Another change").status_code == 409
    assert len(jobs.list_jobs(TENANT)) == 1


def test_regenerate_result_shows_as_a_new_version_next_to_the_old(monkeypatch, client):
    run_dir, page = _single_run(monkeypatch)
    monkeypatch.setattr(revise, "revise_page", _fake_revise)
    _feedback(client, run_dir, page, "Shorter headline")
    body = client.get(f"/gen/{run_dir.name}/{page}", headers=_auth()).get_data(as_text=True)
    assert "queued" in body

    job = jobs.run_one(TENANT)
    assert job["state"] == "done", job
    assert job["result"]["new_version"] == 2

    body = client.get(f"/gen/{run_dir.name}/{page}", headers=_auth()).get_data(as_text=True)
    assert "done" in body
    assert "Version 2 (current)" in body and "Version 1" in body
    assert "new version 2 (old one kept as version 1)" in body
    assert f"/run/{run_dir.name}/review/{page}/v/1" in body
    assert f"/run/{run_dir.name}/review/{page}\"" in body
    old = client.get(f"/run/{run_dir.name}/review/{page}/v/1", headers=_auth())
    assert old.status_code == 200 and b"REVISED" not in old.data
    assert old.headers.get("X-Frame-Options") == "SAMEORIGIN"
    new = client.get(f"/run/{run_dir.name}/review/{page}", headers=_auth())
    assert b"REVISED REVIEW 2" in new.data


def test_a_failed_regenerate_shows_the_reason(monkeypatch, client):
    run_dir, page = _single_run(monkeypatch)

    def failing(*a, **k):
        raise revise.ReviseError("did not pass the gate: word count")

    monkeypatch.setattr(revise, "revise_page", failing)
    _feedback(client, run_dir, page, "Shorter")
    job = jobs.run_one(TENANT)
    assert job["state"] == "failed" and "word count" in job["reason"]
    body = client.get(f"/gen/{run_dir.name}/{page}", headers=_auth()).get_data(as_text=True)
    assert "failed" in body and "word count" in body


# ---------------------------------------------------------------------------
# 4. live test variants: never replaced without the explicit, confirmed action
# ---------------------------------------------------------------------------

def test_live_variant_is_never_replaced_without_the_confirmed_action(monkeypatch, client):
    rec, transport = _live_test(monkeypatch)
    monkeypatch.setattr(revise, "revise_page", _fake_revise)
    v = rec["variants"][0]
    run_dir, page, key = Path(v["run_dir"]), v["cartridge"], v["key"]
    db = abevents.db_path(TENANT)
    _insert(db, rec["test_id"], key, "view", 30)
    _insert(db, rec["test_id"], key, "cta", 6)

    calls_before = len(transport.calls)
    published_before = runstate.published_page_record(run_dir, page)
    _feedback(client, run_dir, page, "Warmer intro")
    assert jobs.run_one(TENANT)["state"] == "done"
    # regenerating touched nothing on Shopify and nothing about the live variant
    assert len(transport.calls) == calls_before
    assert runstate.published_page_record(run_dir, page) == published_before
    assert abevents.counts(db, rec["test_id"])[key] == {"views": 30, "clicks": 6}

    body = client.get(f"/gen/{run_dir.name}/{page}", headers=_auth()).get_data(as_text=True)
    assert "Replace live variant" in body and "resets" in body
    assert f"/gen/{run_dir.name}/{page}/replace" in body

    # no confirm -> refused, nothing queued
    resp = client.post(f"/gen/{run_dir.name}/{page}/replace", data={"csrf": _csrf()}, headers=_auth())
    assert resp.status_code == 400
    assert [j["type"] for j in jobs.list_jobs(TENANT)] == ["regenerate"]
    # the confirm page names what happens to the stats
    confirm = client.get(f"/gen/{run_dir.name}/{page}/replace", headers=_auth()).get_data(as_text=True)
    assert "reset" in confirm and rec["test_id"] in confirm and 'name="confirm" value="yes"' in confirm

    resp = client.post(f"/gen/{run_dir.name}/{page}/replace", data={"csrf": _csrf(), "confirm": "yes"},
                       headers=_auth())
    assert resp.status_code == 303
    job = jobs.run_one(TENANT)
    assert job["type"] == "replace_variant" and job["state"] == "done", job

    puts = [c for c in transport.calls[calls_before:] if c["method"] == "PUT"]
    assert len(puts) == 1 and puts[0]["url"].endswith(f"pages/{v['shopify']['page_id']}.json")
    sent = json.loads(puts[0]["body"])["page"]
    assert sent["published"] is True and "REVISED BODY 2" in sent["body_html"] and "sendBeacon" in sent["body_html"]

    # the variant's stats start again; the old events are kept under an archive key
    assert abevents.counts(db, rec["test_id"]).get(key, {"views": 0, "clicks": 0}) == {"views": 0, "clicks": 0}
    assert abevents.counts(db, rec["test_id"])[f"{key}@1"] == {"views": 30, "clicks": 6}
    rec = abtest.load_test(TENANT, rec["test_id"])
    [replacement] = rec["variants"][0]["replacements"]
    assert replacement["by"] == REVIEWER and replacement["archived_key"] == f"{key}@1" and replacement["at"]
    assert abtest.pooled_arm_stats(TENANT)[v["arm"]]["views"] == 30
    # the returning visitor who saw the old page counts again on the new one
    conn = abevents.connect(db)
    assert abevents.record_event(conn, test_id=rec["test_id"], key=key, event="view",
                                 vid="va000000", iph="x") is True
    conn.close()
    assert [a["action"] for a in jobs.audit_rows(TENANT)][:2] == ["replace_done", "replace"]


def test_a_live_variant_page_offers_no_plain_publish(monkeypatch, client):
    rec, _transport = _live_test(monkeypatch)
    monkeypatch.setattr(revise, "revise_page", _fake_revise)
    v = rec["variants"][1]
    run_dir, page = Path(v["run_dir"]), v["cartridge"]
    _feedback(client, run_dir, page, "x")
    jobs.run_one(TENANT)
    resp = client.post(f"/gen/{run_dir.name}/{page}/publish", data={"csrf": _csrf(), "confirm": "yes"},
                       headers=_auth())
    assert resp.status_code == 409
    assert [j["type"] for j in jobs.list_jobs(TENANT)] == ["regenerate"]


def test_publish_updates_the_existing_live_page_after_confirm(monkeypatch, client):
    run_dir = _older_run("sauna-for-two.mov")
    page = "listicle"
    transport = _fake_shopify(monkeypatch)
    runstate.approve(run_dir, TENANT, by=REVIEWER, pages=[page])
    runstate.set_packet_stamp(run_dir, stamp="ship", by=REVIEWER)
    assert cli.cmd_publish(cli.build_parser().parse_args(
        ["publish", str(run_dir), "--page", page, "--live", "--tenant", TENANT.name])) == 0
    page_id = runstate.published_page_record(run_dir, page)["page_id"]

    monkeypatch.setattr(revise, "revise_page", _fake_revise)
    body = client.get(f"/gen/{run_dir.name}/{page}", headers=_auth()).get_data(as_text=True)
    assert "Publish new version" not in body  # nothing new to publish yet
    _feedback(client, run_dir, page, "Tighter copy")
    jobs.run_one(TENANT)
    body = client.get(f"/gen/{run_dir.name}/{page}", headers=_auth()).get_data(as_text=True)
    assert "Publish new version" in body and "Replace live variant" not in body

    calls_before = len(transport.calls)
    assert client.post(f"/gen/{run_dir.name}/{page}/publish", data={"csrf": _csrf()},
                       headers=_auth()).status_code == 400
    assert client.post(f"/gen/{run_dir.name}/{page}/publish", data={"csrf": _csrf(), "confirm": "yes"},
                       headers=_auth()).status_code == 303
    job = jobs.run_one(TENANT)
    assert job["type"] == "publish_page" and job["state"] == "done", job
    puts = [c for c in transport.calls[calls_before:] if c["method"] == "PUT"]
    assert len(puts) == 1 and puts[0]["url"].endswith(f"pages/{page_id}.json")
    assert json.loads(puts[0]["body"])["page"]["published"] is True
    assert runstate.load_state(run_dir)["pages"][page] == "published"
    assert runstate.load_packet(run_dir)["stamp"] == "ship"
    assert "publish" in [a["action"] for a in jobs.audit_rows(TENANT)]


# ---------------------------------------------------------------------------
# 5. upload: validation, inbox item, create-test job, auto publish
# ---------------------------------------------------------------------------

def test_upload_requires_a_name_and_real_media(client):
    assert _upload(client, name="  ").status_code == 400
    assert _upload(client, data=b"<?php echo 1; ?>" + b"\x00" * 40, filename="ad.png").status_code == 400
    # extension says video, bytes say image
    assert _upload(client, data=PNG, filename="ad.mp4").status_code == 400
    assert _upload(client, data=PNG, filename="ad.gif").status_code == 400
    assert not TENANT.meta_inbox_dir.exists() or not list(TENANT.meta_inbox_dir.iterdir())
    assert jobs.list_jobs(TENANT) == []


def test_upload_over_the_size_cap_is_refused(monkeypatch, client):
    monkeypatch.setattr(upload, "MAX_UPLOAD_BYTES", 1000)
    resp = _upload(client, data=PNG + b"\x00" * 2000)
    assert resp.status_code == 413
    assert not TENANT.meta_inbox_dir.exists() or not any(TENANT.meta_inbox_dir.rglob("*.png"))
    assert jobs.list_jobs(TENANT) == []


def test_upload_writes_an_inbox_item_and_queues_a_create_test_job(client):
    resp = _upload(client, name="Cold Plunge Myths", data=MP4, filename="../../etc/My Ad (final).mp4",
                   extra={"primary_text": "Why cold first?", "headline": "Try heat", "cta": "SHOP_NOW"})
    assert resp.status_code == 303
    [item] = meta_ingest.Inbox(TENANT.meta_inbox_dir).items()
    assert re.fullmatch(r"up-[a-z0-9]{8}", item["ad_id"])
    assert item["source"] == "upload" and item["ad_name"] == "Cold Plunge Myths"
    assert item["media_type"] == "video" and item["media_file"] == f"upload-{item['ad_id']}.mp4"
    assert item["primary_text"] == "Why cold first?" and item["headline"] == "Try heat" and item["cta"] == "SHOP_NOW"
    assert item["uploaded_by"] == REVIEWER and item["state"] == "queued"
    served = client.get(f"/ad-media/{item['ad_id']}", headers=_auth())
    assert served.status_code == 404  # only image ads are served back
    assert served.headers.get("X-Content-Type-Options") == "nosniff"
    media = TENANT.meta_inbox_dir / item["ad_id"] / item["media_file"]
    assert media.read_bytes() == MP4
    # the uploaded file name is never used as a path
    assert not list(TENANT.meta_inbox_dir.rglob("*final*")) and not (TENANT.root / "etc").exists()
    [job] = jobs.list_jobs(TENANT)
    assert job["type"] == "create_test" and job["payload"]["ad_id"] == item["ad_id"] and job["by"] == REVIEWER
    assert resp.headers["Location"].endswith(f"/job/{job['id']}")
    [row] = [a for a in jobs.audit_rows(TENANT) if a["action"] == "upload"]
    assert row["by"] == REVIEWER and row["target"] == f"ad:{item['ad_id']}"


def test_an_uploaded_image_is_served_as_the_type_its_bytes_say(client):
    _upload(client, name="Still ad", data=PNG, filename="still.PNG")
    [item] = meta_ingest.Inbox(TENANT.meta_inbox_dir).items()
    resp = client.get(f"/ad-media/{item['ad_id']}", headers=_auth())
    assert resp.status_code == 200 and resp.mimetype == "image/png" and resp.data == PNG
    assert client.get("/ad-media/..%2F..%2Fetc", headers=_auth()).status_code == 404
    home = client.get("/", headers=_auth()).get_data(as_text=True)
    assert f"/ad-media/{item['ad_id']}" in home


def test_create_test_job_auto_publishes_and_shows_the_split_link(monkeypatch, client):
    runner = FakeRunner()
    monkeypatch.setattr(abtest, "default_runner", runner)
    _upload(client, name="Cold Plunge Myths")
    [job] = jobs.list_jobs(TENANT)
    item = meta_ingest.Inbox(TENANT.meta_inbox_dir).items()[0]

    # publish happens inside the job; the fake store answers for whatever id the test gets
    real_create = abtest.create_test

    def create_and_arm_shopify(tenant, **kw):
        on_record = kw.pop("on_record", None)

        def hook(rec):
            _fake_shopify(monkeypatch, rec["test_id"])
            if on_record:
                on_record(rec)
        return real_create(tenant, on_record=hook, **kw)

    monkeypatch.setattr(abtest, "create_test", create_and_arm_shopify)
    done = jobs.run_one(TENANT)
    assert done["state"] == "done", done
    rec = abtest.load_test(TENANT, done["result"]["test_id"])
    assert rec["status"] == "live" and rec["name"] == "Cold Plunge Myths"
    assert rec["source"]["kind"] == "upload" and rec["source"]["ad_id"] == item["ad_id"]
    assert rec["input"].endswith(item["media_file"])
    assert done["result"]["split_url"] == rec["split"]["url"]
    item = meta_ingest.Inbox(TENANT.meta_inbox_dir).read(item["ad_id"])
    assert item["state"] == "tested" and item["test_id"] == rec["test_id"]
    body = client.get(f"/job/{job['id']}", headers=_auth()).get_data(as_text=True)
    assert rec["split"]["url"] in body and "Ads Manager" in body


def test_create_test_job_without_auto_publish_only_builds(monkeypatch, client):
    monkeypatch.setitem(TENANT.config["abtest"], "auto_publish", False)
    monkeypatch.setattr(abtest, "default_runner", FakeRunner())
    transport = _fake_shopify(monkeypatch)
    _upload(client, name="Cold Plunge Myths")
    done = jobs.run_one(TENANT)
    assert done["state"] == "done"
    rec = abtest.load_test(TENANT, done["result"]["test_id"])
    assert rec["status"] == "built" and not done["result"].get("split_url")
    assert transport.calls == []


# ---------------------------------------------------------------------------
# 6. harness abtest from-inbox: budget stop, resume next day
# ---------------------------------------------------------------------------

def _ledger_cost(amount):
    TENANT.runs_dir.mkdir(parents=True, exist_ok=True)
    with open(budget.spend_ledger_path(TENANT), "a") as fh:
        fh.write(json.dumps({"date": datetime.date.today().isoformat(), "run_id": f"r{amount}",
                             "cost_estimate": amount}) + "\n")


def test_budget_ok_reads_the_daily_cap():
    assert abtest_inbox.budget_ok(TENANT) is True
    _ledger_cost(9.9)
    assert abtest_inbox.budget_ok(TENANT) is False


def test_from_inbox_stops_at_the_budget_cap_and_resumes(monkeypatch, capsys):
    # the command loads its own Tenant from tenant.yaml (auto_publish: true)
    monkeypatch.setattr(abtest_inbox, "_auto_publish", lambda tenant: False)
    for i in range(3):
        _meta_item(f"12021000000020{i}", f"Inbox ad {i}")

    class SpendingRunner(FakeRunner):
        def __call__(self, tenant, input_path, arm, seed):
            result = super().__call__(tenant, input_path, arm, seed)
            if len(self.calls) == 3:  # the first test's third build spends the day's cap
                _ledger_cost(9.9)
            return result

    monkeypatch.setattr(abtest, "default_runner", SpendingRunner())
    assert cli.main(["abtest", "from-inbox", "--tenant", TENANT.name]) == 0
    inbox = meta_ingest.Inbox(TENANT.meta_inbox_dir)
    states = {i["ad_id"]: (i["state"], i["reason"]) for i in inbox.items()}
    assert states["120210000000200"][0] == "tested"
    assert states["120210000000201"] == ("queued", "budget cap")
    assert states["120210000000202"] == ("queued", "budget cap")
    assert len(abtest.list_tests(TENANT)) == 1

    # next day: the ledger has nothing for today, the queued items build
    budget.spend_ledger_path(TENANT).unlink()
    monkeypatch.setattr(abtest, "default_runner", FakeRunner())
    assert cli.main(["abtest", "from-inbox", "--tenant", TENANT.name]) == 0
    assert {i["state"] for i in inbox.items()} == {"tested"}
    assert len(abtest.list_tests(TENANT)) == 3


def test_from_inbox_resumes_a_test_the_cap_stopped_mid_build(monkeypatch):
    monkeypatch.setitem(TENANT.config["abtest"], "auto_publish", False)
    _meta_item("120210000000300", "Half built")
    monkeypatch.setattr(abtest, "default_runner", FakeRunner(budget_after=1))
    summary = abtest_inbox.from_inbox(TENANT, by=REVIEWER, log=lambda *_: None)
    item = meta_ingest.Inbox(TENANT.meta_inbox_dir).read("120210000000300")
    assert summary["stopped"] == "budget cap"
    assert item["state"] == "building" and item["test_id"]
    assert abtest.load_test(TENANT, item["test_id"])["status"] == "queued"

    monkeypatch.setattr(abtest, "default_runner", FakeRunner())
    abtest_inbox.from_inbox(TENANT, by=REVIEWER, log=lambda *_: None)
    item = meta_ingest.Inbox(TENANT.meta_inbox_dir).read("120210000000300")
    assert item["state"] == "tested"
    assert abtest.load_test(TENANT, item["test_id"])["status"] == "built"
    assert len(abtest.list_tests(TENANT)) == 1


def test_meta_pull_cron_runs_from_inbox_after_the_pull():
    script = (REPO_ROOT / "crons" / "meta-pull.sh").read_text()
    pull = script.index('/harness" meta pull --tenant')
    assert pull < script.index('/harness" abtest from-inbox --tenant')


# ---------------------------------------------------------------------------
# 7. job queue + worker
# ---------------------------------------------------------------------------

def test_a_job_left_running_by_a_crash_is_failed_with_a_reason():
    job_id = jobs.enqueue(TENANT, "regenerate", {"run_id": "r", "page": "p", "by": REVIEWER},
                          by=REVIEWER, target="run:r/p")
    claimed = jobs.claim_next(TENANT)
    assert claimed["id"] == job_id and jobs.get_job(TENANT, job_id)["state"] == "running"
    # the worker process dies here; the next worker start recovers it
    assert jobs.recover_stale(TENANT) == [job_id]
    job = jobs.get_job(TENANT, job_id)
    assert job["state"] == "failed" and "restart" in job["reason"] and job["finished_at"]
    assert jobs.claim_next(TENANT) is None


def test_worker_runs_jobs_in_order_one_at_a_time_and_stops_on_request():
    order, stop = [], threading.Event()
    active = []

    def handler(tenant, payload):
        active.append(1)
        assert len(active) == 1
        order.append(payload["n"])
        if payload["n"] == 2:
            stop.set()  # SIGTERM arrives during job 2: it still finishes
        active.pop()
        return {"n": payload["n"]}

    for n in (1, 2, 3):
        jobs.enqueue(TENANT, "regenerate", {"n": n}, by=REVIEWER, target=f"t{n}")
    jobs.work(TENANT, interval=0.01, stop=stop, handlers={"regenerate": handler}, log=lambda *_: None)
    assert order == [1, 2]
    states = [j["state"] for j in sorted(jobs.list_jobs(TENANT), key=lambda j: j["id"])]
    assert states == ["done", "done", "queued"]


def test_an_unexpected_handler_error_fails_the_job_not_the_worker():
    jobs.enqueue(TENANT, "regenerate", {}, by=REVIEWER, target="t")

    def boom(tenant, payload):
        raise RuntimeError("disk full")

    job = jobs.run_one(TENANT, handlers={"regenerate": boom})
    assert job["state"] == "failed" and "disk full" in job["reason"]


def test_only_one_worker_per_tenant():
    with jobs.worker_lock(TENANT):
        with pytest.raises(jobs.JobError):
            with jobs.worker_lock(TENANT):
                pass


def test_worker_unit_file_is_a_user_service_for_harness_worker():
    unit = (REPO_ROOT / "crons" / "worker.service").read_text()
    assert "harness worker --tenant @@TENANT@@" in unit
    assert "KillSignal=SIGTERM" in unit and "Restart=on-failure" in unit


# ---------------------------------------------------------------------------
# 8. auth, CSRF, rate limits, escaping, audit
# ---------------------------------------------------------------------------

def _site_urls(run_id="r", page="p"):
    return [
        ("GET", "/"), ("GET", f"/gen/{run_id}/{page}"), ("POST", f"/gen/{run_id}/{page}/feedback"),
        ("GET", f"/gen/{run_id}/{page}/publish"), ("POST", f"/gen/{run_id}/{page}/publish"),
        ("GET", f"/gen/{run_id}/{page}/replace"), ("POST", f"/gen/{run_id}/{page}/replace"),
        ("GET", f"/run/{run_id}/review/{page}/v/1"), ("GET", f"/run/{run_id}/thumb/{page}"),
        ("GET", "/ad-media/up-abcd1234"), ("GET", "/upload"), ("POST", "/upload"),
        ("GET", "/jobs"), ("GET", "/job/1"), ("GET", "/audit"), ("GET", "/runs"),
    ]


def test_every_new_route_requires_login(monkeypatch, client):
    run_dir, page = _single_run(monkeypatch)
    rules = {r.endpoint for r in client.application.url_map.iter_rules()}
    assert set(site.ENDPOINTS) <= rules
    for method, url in _site_urls(run_dir.name, page):
        resp = client.open(url, method=method)
        assert resp.status_code == 401, (method, url)
        resp = client.open(url, method=method, headers=_auth("nobody@example.com"))
        assert resp.status_code == 401, (method, url)


def test_posts_without_the_csrf_token_are_refused(monkeypatch, client):
    run_dir, page = _single_run(monkeypatch)
    assert _feedback(client, run_dir, page, "x", csrf="").status_code == 403
    assert _feedback(client, run_dir, page, "x", csrf=_csrf(OTHER)).status_code == 403
    assert _upload(client, csrf="nope").status_code == 403
    for action in ("publish", "replace"):
        resp = client.post(f"/gen/{run_dir.name}/{page}/{action}", data={"confirm": "yes"}, headers=_auth())
        assert resp.status_code == 403
    assert not runstate.load_state(run_dir).get("feedback")
    assert jobs.list_jobs(TENANT) == []
    # every form the site renders carries the token
    body = client.get(f"/gen/{run_dir.name}/{page}", headers=_auth()).get_data(as_text=True)
    assert f'name="csrf" value="{_csrf()}"' in body
    body = client.get("/upload", headers=_auth()).get_data(as_text=True)
    assert f'name="csrf" value="{_csrf()}"' in body


def test_feedback_and_upload_are_rate_limited_per_user(monkeypatch, client):
    run_dir, page = _single_run(monkeypatch)
    monkeypatch.setattr(jobs, "active_job", lambda *a, **k: None)  # let every feedback queue
    codes = [_feedback(client, run_dir, page, f"change {i}").status_code for i in range(site.FEEDBACK_PER_MIN + 1)]
    assert codes[-1] == 429 and set(codes[:-1]) == {303}
    # another reviewer is not affected
    assert _feedback(client, run_dir, page, "mine", email=OTHER).status_code == 303
    codes = [_upload(client, name=f"u{i}").status_code for i in range(site.UPLOADS_PER_WINDOW + 1)]
    assert codes[-1] == 429 and set(codes[:-1]) == {303}


def test_user_text_is_escaped_everywhere(monkeypatch, client):
    run_dir, page = _single_run(monkeypatch)
    evil = '<script>alert("x")</script>'
    _upload(client, name=evil)
    _feedback(client, run_dir, page, f"notes {evil}")
    for url in ("/", f"/gen/{run_dir.name}/{page}", "/jobs", "/audit"):
        body = client.get(url, headers=_auth()).get_data(as_text=True)
        assert evil not in body, url
    assert "&lt;script&gt;" in client.get("/", headers=_auth()).get_data(as_text=True)
    assert "&lt;script&gt;" in client.get(f"/gen/{run_dir.name}/{page}", headers=_auth()).get_data(as_text=True)


def test_audit_log_records_who_and_when(monkeypatch, client):
    run_dir, page = _single_run(monkeypatch)
    _feedback(client, run_dir, page, "Shorter", email=OTHER)
    _upload(client, name="Audit me")
    rows = jobs.audit_rows(TENANT)
    by_action = {r["action"]: r for r in rows}
    assert by_action["feedback"]["by"] == OTHER and by_action["upload"]["by"] == REVIEWER
    assert by_action["feedback"]["target"] == f"run:{run_dir.name}/{page}"
    assert by_action["feedback"]["detail"]["text"] == "Shorter"
    for r in rows:
        datetime.datetime.fromisoformat(r["at"])
    body = client.get("/audit", headers=_auth()).get_data(as_text=True)
    assert OTHER in body and "Audit me" in body


def test_pages_fit_a_phone(monkeypatch, client):
    _single_run(monkeypatch)
    body = client.get("/", headers=_auth()).get_data(as_text=True)
    assert 'name="viewport" content="width=device-width, initial-scale=1"' in body
    for token in ("#181918", "#EFE3D2", "#F27046", "#C0C8C3"):
        assert token in body
