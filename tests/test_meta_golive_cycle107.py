"""Cycle 107: go live on Meta, the auto go-live lever, and the results leaderboard.

`harness abtest go-live` copies the ad's creative with every link set to the
test's split link (url_tags and every other field unchanged), points the ad at
the copy, reads it back and verifies. No test here talks to Meta: every Graph
call goes to MetaFake, a transport keyed by (method, path).
"""
import json
import urllib.parse

import pytest

from harness import abevents, abtest, abtest_inbox, cli, jobs, meta_golive, meta_ingest, site
from harness.meta_ingest import Inbox
from tests.support import REPO_ROOT, TENANT, tenant_copy
from tests.test_abtest_cycle67 import _insert
from tests.test_listicle_site_cycle69 import REVIEWER, _auth, _csrf, _live_test, client  # noqa: F401 -- fixture
from tests.test_meta_ingest import TOKEN, V, make_client, no_real_env  # noqa: F401 -- fixture

AD = "120210000000000301"
ACCOUNT = "act_963439094733982"
TEST_ID = "cold-mornings-ab12"
SPLIT = "https://peaksaunas.com/pages/lp-cold-mornings-ab12"
OLD = "https://peaksaunas.com/products/peak-fuji"
TAGS = "utm_source=facebook&utm_medium=paid&utm_campaign={{campaign.name}}"


class MetaFake:
    """(method, url, *, headers, body) -> (status, bytes). Routes keyed by
    (method, path); each holds a queue of responses, the last one repeats."""

    def __init__(self):
        self.calls, self.routes = [], {}

    def add(self, method, path, body, status=200):
        self.routes.setdefault((method, f"/{V}/{path}"), []).append((status, json.dumps(body).encode()))
        return self

    def __call__(self, method, url, *, headers, body=None):
        parts = urllib.parse.urlsplit(url)
        form = {k: v[0] for k, v in urllib.parse.parse_qs(body.decode()).items()} if body else {}
        self.calls.append({"method": method, "path": parts.path, "query": urllib.parse.parse_qs(parts.query),
                           "form": form, "headers": dict(headers)})
        queue = self.routes.get((method, parts.path))
        if not queue:
            raise AssertionError(f"no fake Meta response for {method} {parts.path}")
        return queue.pop(0) if len(queue) > 1 else queue[0]

    def methods(self):
        return [(c["method"], c["path"].split("/", 2)[-1]) for c in self.calls]


def video_creative(link=OLD, cid="c1"):
    return {"id": cid, "name": "Cold mornings v1", "url_tags": TAGS, "object_story_spec": {
        "page_id": "111", "instagram_user_id": "222", "video_data": {
            "video_id": "9", "image_hash": "abc", "message": "Mornings are cold.", "title": "Peak Fuji",
            "call_to_action": {"type": "SHOP_NOW", "value": {"link": link, "link_caption": "peaksaunas.com"}}}}}


def link_creative(link=OLD):
    return {"id": "c1", "name": "Carousel", "url_tags": TAGS, "object_story_spec": {"page_id": "111", "link_data": {
        "link": link, "message": "Hello", "name": "Peak", "image_hash": "h1",
        "call_to_action": {"type": "SHOP_NOW", "value": {"link": link}},
        "child_attachments": [{"link": link, "image_hash": "h2", "name": "Card 1",
                               "call_to_action": {"type": "SHOP_NOW", "value": {"link": link}}},
                              {"link": link, "image_hash": "h3", "name": "Card 2"}]}}}


def feed_creative(link=OLD):
    return {"id": "c1", "name": "Dynamic", "url_tags": TAGS, "object_url": link,
            "object_story_spec": {"page_id": "111"},
            "asset_feed_spec": {"images": [{"hash": "h1"}, {"hash": "h2"}], "bodies": [{"text": "B"}],
                                "titles": [{"text": "T"}], "call_to_action_types": ["SHOP_NOW"],
                                "ad_formats": ["SINGLE_IMAGE"],
                                "link_urls": [{"website_url": link, "display_url": "peaksaunas.com"},
                                              {"website_url": link + "?v=2"}]}}


def ad(creative, status="ACTIVE", account="963439094733982"):
    return {"id": AD, "name": "Cold mornings", "account_id": account, "effective_status": status,
            "creative": creative}


def golive_fake(creative=None, *, status="ACTIVE", verify_creative=None):
    creative = creative or video_creative()
    fake = MetaFake()
    fake.add("GET", AD, ad(creative, status))
    fake.add("GET", "111", {"id": "111", "instagram_business_account": {"id": "222"}})
    fake.add("GET", AD, {"id": AD, "effective_status": status,
                         "creative": verify_creative or {**video_creative(SPLIT, "c2")}})
    fake.add("POST", f"{ACCOUNT}/adcreatives", {"id": "c2"})
    fake.add("POST", AD, {"success": True})
    return fake


def live_record(**extra):
    rec = {"test_id": TEST_ID, "name": "Cold mornings", "status": "live", "reason": "",
           "created_at": "2026-10-12T10:00:00", "live_at": "2026-10-12T15:00:00Z", "finished_at": None,
           "source": {"kind": "meta", "ad_id": AD, "ad_name": "Cold mornings"},
           "variants": [{"key": "A", "arm": "landing:cinematic", "cartridge": "landing", "look": "cinematic",
                         "hero_style": "caps"},
                        {"key": "B", "arm": "landing:open", "cartridge": "landing", "look": "open",
                         "hero_style": "split"},
                        {"key": "C", "arm": "landing:proof", "cartridge": "landing", "look": "proof",
                         "hero_style": "caps"}],
           "split": {"url": SPLIT, "handle": f"lp-{TEST_ID}", "page_id": 5}, "winner": None}
    rec.update(extra)
    abtest.save_test(TENANT, rec)
    return rec


def inbox_item(destination=OLD, status="ACTIVE", ad_id=AD):
    item = {"ad_id": ad_id, "ad_name": "Cold mornings", "created_time": "2026-10-12T09:15:02-0700",
            "effective_status": status, "destination_url": destination, "url_tags": TAGS, "source": "meta",
            "state": "tested", "reason": "", "test_id": TEST_ID, "pulled_at": "2026-10-12T08:00:00+00:00",
            "history": []}
    Inbox(TENANT.meta_inbox_dir).write(item)
    return item


@pytest.fixture
def meta(monkeypatch, no_real_env):  # noqa: F811
    """The CLI's Meta client is a GraphClient on whatever MetaFake the test sets."""
    monkeypatch.setenv("META_ACCESS_TOKEN", TOKEN)
    box = {}
    monkeypatch.setattr(meta_ingest, "make_client", lambda token, cfg: make_client(box["fake"]))
    return box


# ---------------------------------------------------------------------------
# 1. the creative copy
# ---------------------------------------------------------------------------

def test_copy_video_creative_replaces_the_cta_link_and_keeps_everything_else():
    creative = video_creative()
    payload, changes = meta_golive.copy_creative(creative, SPLIT, name="copy")
    assert changes == [{"field": "object_story_spec.video_data.call_to_action.value.link", "old": OLD, "new": SPLIT}]
    assert payload["url_tags"] == TAGS and payload["name"] == "copy" and "id" not in payload
    expected = video_creative(SPLIT)["object_story_spec"]
    assert payload["object_story_spec"] == expected  # page, video, copy, CTA type, caption unchanged
    assert creative == video_creative()  # the input is not changed


def test_copy_link_data_creative_replaces_link_cta_and_carousel_links():
    payload, changes = meta_golive.copy_creative(link_creative(), SPLIT, name="copy")
    assert [c["field"] for c in changes] == [
        "object_story_spec.link_data.link", "object_story_spec.link_data.call_to_action.value.link",
        "object_story_spec.link_data.child_attachments[0].link",
        "object_story_spec.link_data.child_attachments[0].call_to_action.value.link",
        "object_story_spec.link_data.child_attachments[1].link"]
    assert payload["object_story_spec"] == link_creative(SPLIT)["object_story_spec"]
    assert payload["url_tags"] == TAGS


def test_copy_asset_feed_creative_replaces_every_website_url_and_object_url():
    payload, changes = meta_golive.copy_creative(feed_creative(), SPLIT, name="copy")
    assert [c["field"] for c in changes] == ["asset_feed_spec.link_urls[0].website_url",
                                             "asset_feed_spec.link_urls[1].website_url", "object_url"]
    feed = payload["asset_feed_spec"]
    assert [u["website_url"] for u in feed["link_urls"]] == [SPLIT, SPLIT]
    assert feed["link_urls"][0]["display_url"] == "peaksaunas.com"  # what the ad shows, not where it goes
    assert {k: v for k, v in feed.items() if k != "link_urls"} == {
        k: v for k, v in feed_creative()["asset_feed_spec"].items() if k != "link_urls"}
    assert payload["object_url"] == SPLIT and payload["url_tags"] == TAGS
    assert payload["object_story_spec"] == {"page_id": "111"}


def test_copy_refuses_an_existing_post_and_a_creative_without_links():
    with pytest.raises(meta_golive.ExistingPost, match="Ads Manager"):
        meta_golive.copy_creative({"id": "c9", "object_story_id": "111_999", "url_tags": TAGS}, SPLIT, name="x")
    with pytest.raises(meta_golive.GoLiveRefused, match="no link field"):
        meta_golive.copy_creative({"id": "c9", "object_story_spec": {"page_id": "1", "photo_data": {}}}, SPLIT,
                                  name="x")


# ---------------------------------------------------------------------------
# 2. harness abtest go-live
# ---------------------------------------------------------------------------

def test_go_live_copies_the_creative_points_the_ad_verifies_and_records(meta, capsys):
    live_record()
    inbox_item()
    meta["fake"] = fake = golive_fake()
    assert cli.main(["abtest", "go-live", TEST_ID, "--by", REVIEWER, "--tenant", TENANT.name]) == 0
    out = capsys.readouterr().out
    assert fake.methods() == [("GET", AD), ("GET", "111"), ("POST", f"{ACCOUNT}/adcreatives"), ("POST", AD), ("GET", AD)]
    created = fake.calls[2]["form"]
    spec = json.loads(created["object_story_spec"])
    assert spec["video_data"]["call_to_action"]["value"]["link"] == SPLIT
    assert created["url_tags"] == TAGS and created["name"] == f"Cold mornings v1 | split {TEST_ID}"
    assert json.loads(fake.calls[3]["form"]["creative"]) == {"creative_id": "c2"}
    assert fake.calls[3]["headers"]["Content-Type"] == "application/x-www-form-urlencoded"
    assert all(TOKEN not in c["path"] and TOKEN not in json.dumps(c["form"]) for c in fake.calls)

    rec = abtest.load_test(TENANT, TEST_ID)
    block = rec["meta_go_live"]
    assert {k: block[k] for k in ("by", "ad_id", "old_creative_id", "new_creative_id", "verified_destination",
                                  "verified", "old_destination")} == {
        "by": REVIEWER, "ad_id": AD, "old_creative_id": "c1", "new_creative_id": "c2",
        "verified_destination": SPLIT, "verified": True, "old_destination": OLD}
    assert block["at"].endswith("Z") and rec["linked_at"] == block["at"]
    item = Inbox(TENANT.meta_inbox_dir).read(AD)
    assert item["destination_url"] == SPLIT and item["links_checked_at"]
    audit = jobs.audit_rows(TENANT)
    assert audit[0]["action"] == "meta_go_live" and audit[0]["target"] == f"test:{TEST_ID}"
    assert audit[0]["detail"]["new_creative_id"] == "c2"
    assert "WARNING" in out and "learning phase" in out and TOKEN not in out
    assert f"Test {TEST_ID} is live on Meta ad {AD}" in out


def test_go_live_dry_run_prints_the_change_and_writes_nothing(meta, capsys):
    live_record()
    before = (TENANT.abtests_dir / f"{TEST_ID}.json").read_text()
    meta["fake"] = fake = golive_fake(feed_creative())
    assert cli.main(["abtest", "go-live", TEST_ID, "--by", REVIEWER, "--dry-run", "--tenant", TENANT.name]) == 0
    out = capsys.readouterr().out
    assert fake.methods() == [("GET", AD)]
    assert f"asset_feed_spec.link_urls[0].website_url: {OLD} -> {SPLIT}" in out
    assert f"object_url: {OLD} -> {SPLIT}" in out and f"url_tags kept: {TAGS}" in out
    assert "learning phase" in out and "no likes, comments or shares" in out
    assert "dry run: nothing was changed" in out
    assert (TENANT.abtests_dir / f"{TEST_ID}.json").read_text() == before
    assert jobs.audit_rows(TENANT) == []


@pytest.mark.parametrize("extra, words", [
    ({"status": "built", "split": None}, "no split link"),
    ({"status": "finished"}, "only a live test"),
    ({"source": {"kind": "upload"}}, "not built from a Meta ad"),
])
def test_go_live_refuses_before_any_meta_call(meta, capsys, extra, words):
    live_record(**extra)
    meta["fake"] = fake = MetaFake()
    assert cli.main(["abtest", "go-live", TEST_ID, "--by", REVIEWER, "--tenant", TENANT.name]) == 1
    assert words in capsys.readouterr().err
    assert fake.calls == []


def test_go_live_refuses_an_existing_post_creative(meta, capsys):
    live_record()
    meta["fake"] = fake = golive_fake({"id": "c9", "object_story_id": "111_999", "url_tags": TAGS})
    assert cli.main(["abtest", "go-live", TEST_ID, "--by", REVIEWER, "--tenant", TENANT.name]) == 1
    err = capsys.readouterr().err
    assert "existing Page post" in err and "by hand in Ads Manager" in err and SPLIT in err
    assert fake.methods() == [("GET", AD)]
    assert "meta_go_live" not in abtest.load_test(TENANT, TEST_ID)


def test_go_live_refuses_an_ad_that_is_not_active_unless_forced(meta, capsys):
    live_record()
    meta["fake"] = fake = golive_fake(status="PENDING_REVIEW")
    assert cli.main(["abtest", "go-live", TEST_ID, "--by", REVIEWER, "--tenant", TENANT.name]) == 1
    assert "PENDING_REVIEW, not ACTIVE" in capsys.readouterr().err
    assert fake.methods() == [("GET", AD)]
    meta["fake"] = golive_fake(status="PAUSED")
    assert cli.main(["abtest", "go-live", TEST_ID, "--by", REVIEWER, "--force", "--tenant", TENANT.name]) == 0
    assert abtest.load_test(TENANT, TEST_ID)["meta_go_live"]["forced"] is True


def test_go_live_refuses_an_ad_of_another_account_and_a_second_go_live(meta, capsys):
    live_record()
    fake = MetaFake().add("GET", AD, ad(video_creative(), account="111"))
    meta["fake"] = fake
    assert cli.main(["abtest", "go-live", TEST_ID, "--by", REVIEWER, "--tenant", TENANT.name]) == 1
    assert "is in ad account act_111" in capsys.readouterr().err
    live_record(meta_go_live={"ad_id": AD, "at": "2026-10-12T16:00:00Z", "old_creative_id": "c1"})
    meta["fake"] = fake = MetaFake()
    assert cli.main(["abtest", "go-live", TEST_ID, "--by", REVIEWER, "--tenant", TENANT.name]) == 1
    assert "already live on Meta" in capsys.readouterr().err and fake.calls == []


def test_go_live_needs_a_listed_reviewer(meta, capsys):
    live_record()
    meta["fake"] = fake = golive_fake()
    assert cli.main(["abtest", "go-live", TEST_ID, "--by", "stranger@example.com", "--tenant", TENANT.name]) == 1
    assert "not a listed reviewer" in capsys.readouterr().err and fake.calls == []


def test_a_read_back_that_does_not_match_is_recorded_unverified(meta, capsys):
    live_record()
    meta["fake"] = golive_fake(verify_creative=video_creative(OLD, "c1"))  # Meta still shows the old creative
    assert cli.main(["abtest", "go-live", TEST_ID, "--by", REVIEWER, "--tenant", TENANT.name]) == 1
    err = capsys.readouterr().err
    assert "was changed, but reading it back shows creative c1" in err and "revert-go-live" in err
    rec = abtest.load_test(TENANT, TEST_ID)
    assert rec["meta_go_live"]["verified"] is False and rec["meta_go_live"]["new_creative_id"] == "c2"
    assert not rec.get("linked_at")


def test_revert_sets_the_old_creative_back_and_verifies(meta, capsys):
    live_record()
    inbox_item()
    meta["fake"] = golive_fake()
    assert cli.main(["abtest", "go-live", TEST_ID, "--by", REVIEWER, "--tenant", TENANT.name]) == 0
    fake = MetaFake().add("POST", AD, {"success": True})
    fake.add("GET", AD, {"id": AD, "effective_status": "ACTIVE", "creative": video_creative(OLD, "c1")})
    meta["fake"] = fake
    assert cli.main(["abtest", "revert-go-live", TEST_ID, "--by", REVIEWER, "--tenant", TENANT.name]) == 0
    assert fake.methods() == [("POST", AD), ("GET", AD)]
    assert json.loads(fake.calls[0]["form"]["creative"]) == {"creative_id": "c1"}
    rec = abtest.load_test(TENANT, TEST_ID)
    block = rec["meta_go_live"]
    assert block["reverted_by"] == REVIEWER and block["revert_verified"] is True and block["reverted_at"]
    assert block["revert_verified_destination"] == OLD and rec["linked_at"] is None
    assert Inbox(TENANT.meta_inbox_dir).read(AD)["destination_url"] == OLD
    assert [r["action"] for r in jobs.audit_rows(TENANT)][:2] == ["meta_revert_go_live", "meta_go_live"]
    capsys.readouterr()
    assert cli.main(["abtest", "revert-go-live", TEST_ID, "--by", REVIEWER, "--tenant", TENANT.name]) == 1
    assert "not live on Meta" in capsys.readouterr().err


def test_a_post_is_retried_on_a_rate_limit_but_never_after_a_server_error():
    fake = MetaFake().add("POST", AD, {"error": {"code": 1, "message": "boom", "is_transient": True}}, status=500)
    with pytest.raises(meta_ingest.MetaAPIError, match="POST"):
        make_client(fake).post(AD, {"creative": {"creative_id": "c2"}})
    assert len(fake.calls) == 1  # it may have gone through: not sent twice
    fake = MetaFake().add("POST", AD, {"error": {"code": 17, "message": "slow down"}}, status=400)
    fake.add("POST", AD, {"success": True})
    assert make_client(fake).post(AD, {"creative": {"creative_id": "c2"}}) == {"success": True}
    assert len(fake.calls) == 2


# ---------------------------------------------------------------------------
# 3. the lever (abtest.auto_go_live)
# ---------------------------------------------------------------------------

def _publish_and_build(monkeypatch):
    """build_test_for_item for an inbox item whose test is built: publish is
    replaced by one that marks the record live with the split link."""
    # Cycle 108: built by the pipeline after the lever went on (abtest.auto_since).
    rec = live_record(status="built", split=None, created_at="2026-10-13T10:00:00")

    def publish(tenant, test_id, *, by):
        r = abtest.load_test(tenant, test_id)
        r.update(status="live", split={"url": SPLIT, "handle": f"lp-{test_id}", "page_id": 5})
        abtest.save_test(tenant, r)
        return SPLIT, None

    monkeypatch.setattr(abtest_inbox, "publish_test", publish)
    inbox_item()
    item = Inbox(TENANT.meta_inbox_dir).read(AD)
    item.update(state="building", test_id=rec["test_id"])
    Inbox(TENANT.meta_inbox_dir).write(item)
    return abtest_inbox.build_test_for_item(TENANT, AD, by=REVIEWER)


def test_the_lever_is_off_for_peak_and_from_inbox_never_calls_meta(monkeypatch):
    # The DEFAULT is off for a fresh tenant; PEAK's value is the owner's to flip
    # (on 2026-10-13), so for PEAK only check that it is the boolean from its config.
    from tests.test_abtest_cycle67 import ConfigTenant
    assert abtest.settings(ConfigTenant())["auto_go_live"] is False
    assert isinstance(TENANT.config["abtest"]["auto_go_live"], bool)
    assert abtest.settings(TENANT)["auto_go_live"] is TENANT.config["abtest"]["auto_go_live"]
    # with the lever off, from-inbox publishes and never calls Meta
    tenant_copy(monkeypatch, {"abtest.auto_publish": True, "abtest.auto_go_live": False})
    monkeypatch.setattr(meta_golive, "client_for", lambda tenant: pytest.fail("Meta called with the lever off"))
    outcome = _publish_and_build(monkeypatch)
    assert outcome["outcome"] == "live" and outcome["meta_go_live"] is None
    assert "meta_go_live" not in abtest.load_test(TENANT, TEST_ID)


def test_the_lever_on_goes_live_for_an_active_ad(monkeypatch):
    tenant_copy(monkeypatch, {"abtest.auto_publish": True, "abtest.auto_go_live": True})
    fake = golive_fake()
    monkeypatch.setattr(meta_golive, "client_for", lambda tenant: make_client(fake))
    outcome = _publish_and_build(monkeypatch)
    assert outcome["meta_go_live"]["state"] == "done"
    assert "Meta go-live done" in outcome["reason"]
    rec = abtest.load_test(TENANT, TEST_ID)
    assert rec["meta_go_live"]["verified"] is True and rec["meta_go_live"]["by"] == REVIEWER
    assert rec["auto_go_live"]["state"] == "done" and rec["meta_go_live"]["forced"] is False


def test_the_lever_waits_for_approval_then_goes_live_on_a_later_run(monkeypatch):
    tenant_copy(monkeypatch, {"abtest.auto_publish": True, "abtest.auto_go_live": True})
    box = {"fake": golive_fake(status="PENDING_REVIEW")}
    monkeypatch.setattr(meta_golive, "client_for", lambda tenant: make_client(box["fake"]))
    outcome = _publish_and_build(monkeypatch)
    assert outcome["meta_go_live"]["state"] == "pending" and "PENDING_REVIEW" in outcome["meta_go_live"]["reason"]
    assert [c["method"] for c in box["fake"].calls] == ["GET"]  # never forced
    box["fake"] = golive_fake(status="ACTIVE")  # Meta approved the ad
    summary = abtest_inbox.from_inbox(TENANT, by=REVIEWER, log=lambda *_: None)
    assert summary["went_live"] == [TEST_ID]
    assert abtest.load_test(TENANT, TEST_ID)["auto_go_live"]["state"] == "done"


def test_the_lever_never_touches_an_existing_post_or_a_test_it_did_not_publish(monkeypatch):
    tenant_copy(monkeypatch, {"abtest.auto_publish": True, "abtest.auto_go_live": True})
    fake = golive_fake({"id": "c9", "object_story_id": "111_999"})
    monkeypatch.setattr(meta_golive, "client_for", lambda tenant: make_client(fake))
    outcome = _publish_and_build(monkeypatch)
    assert outcome["meta_go_live"]["state"] == "refused" and "existing Page post" in outcome["meta_go_live"]["reason"]
    assert all(c["method"] == "GET" for c in fake.calls)
    # a live test the lever never touched (e.g. one of today's 20) is left alone
    other = live_record(test_id="older-test-ab34")
    assert meta_golive.retry_pending(TENANT, by=REVIEWER, client=make_client(MetaFake())) == []
    assert "auto_go_live" not in abtest.load_test(TENANT, other["test_id"])


# ---------------------------------------------------------------------------
# 4. the Ads page, the confirm, the jobs
# ---------------------------------------------------------------------------

def _site_test(monkeypatch, status="ACTIVE"):
    rec, _transport = _live_test(monkeypatch)
    rec["source"] = {"kind": "meta", "ad_id": AD, "ad_name": "Cold mornings"}
    abtest.save_test(TENANT, rec)
    inbox_item(status=status)
    item = Inbox(TENANT.meta_inbox_dir).read(AD)
    item["test_id"] = rec["test_id"]
    Inbox(TENANT.meta_inbox_dir).write(item)
    return rec


def test_card_has_go_live_with_a_confirm_that_states_the_consequences(monkeypatch, client):
    rec = _site_test(monkeypatch)
    body = client.get("/", headers=_auth()).get_data(as_text=True)
    assert f'href="/test/{rec["test_id"]}/go-live"' in body and "Go live on Meta" in body
    dialog = body[body.index('<dialog id="golive-0"'):]
    dialog = dialog[:dialog.index("</dialog>")]
    assert "Cold mornings" in dialog and AD in dialog
    assert f"Current link: <code>{OLD}</code>" in dialog
    assert f"Split link (new link): <code>{rec['split']['url']}</code>" in dialog
    assert "learning phase" in dialog and "no likes, comments or shares" in dialog
    assert 'name="csrf"' in dialog and "Yes, go live on Meta" in dialog
    page = client.get(f"/test/{rec['test_id']}/go-live", headers=_auth()).get_data(as_text=True)
    assert "learning phase" in page and "Yes, go live on Meta" in page  # the same confirm without JS


def test_card_for_an_ad_that_is_not_active_has_no_button(monkeypatch, client):
    _site_test(monkeypatch, status="PAUSED")
    body = client.get("/", headers=_auth()).get_data(as_text=True)
    assert "is PAUSED: Go live on Meta needs an ACTIVE" in body and 'id="golive-0"' not in body


def test_go_live_post_queues_a_job_and_audits_it(monkeypatch, client):
    rec = _site_test(monkeypatch)
    url = f"/test/{rec['test_id']}/go-live"
    assert client.post(url, data={"confirm": "yes"}, headers=_auth()).status_code == 403  # no CSRF token
    resp = client.post(url, data={"confirm": "yes", "csrf": _csrf()}, headers=_auth())
    assert resp.status_code == 303 and "/job/" in resp.headers["Location"]
    job = jobs.list_jobs(TENANT)[0]
    assert job["type"] == "meta_go_live" and job["target"] == f"test:{rec['test_id']}"
    assert job["payload"] == {"test_id": rec["test_id"], "by": REVIEWER}
    audit = jobs.audit_rows(TENANT)[0]
    assert audit["action"] == "meta_go_live_requested" and audit["detail"]["job_id"] == job["id"]
    again = client.post(url, data={"confirm": "yes", "csrf": _csrf()}, headers=_auth())
    assert again.status_code == 409  # already queued
    assert client.post("/test/no-such-test/go-live", data={"confirm": "yes", "csrf": _csrf()},
                       headers=_auth()).status_code == 404


def test_the_worker_runs_go_live_and_the_card_shows_live_revert_and_the_link_icon(monkeypatch, client):
    rec = _site_test(monkeypatch)
    body = client.get("/", headers=_auth()).get_data(as_text=True)
    assert 'class="adlink' not in body  # the ad still points at the PDP
    client.post(f"/test/{rec['test_id']}/go-live", data={"confirm": "yes", "csrf": _csrf()}, headers=_auth())
    split = rec["split"]["url"]
    fake = golive_fake(verify_creative=video_creative(split, "c2"))
    monkeypatch.setattr(meta_golive, "client_for", lambda tenant: make_client(fake))
    job = jobs.run_one(TENANT, log=lambda *_: None)
    assert job["state"] == "done", job["reason"]
    assert job["result"]["new_creative_id"] == "c2" and job["result"]["verified_destination"] == split
    page = client.get(f"/job/{job['id']}", headers=_auth()).get_data(as_text=True)
    assert "Go live on Meta" in page and "c1 -&gt; c2" in page
    assert f"<dt>Link before</dt><dd>{OLD}</dd>" in page and f"<dt>Link after (read back)</dt><dd>{split}</dd>" in page

    body = client.get("/", headers=_auth()).get_data(as_text=True)
    assert "Live on Meta" in body and f'href="/test/{rec["test_id"]}/revert-go-live"' in body
    dialog = body[body.index('<dialog id="golive-0-revert"'):]
    assert f"Link after revert: <code>{OLD}</code>" in dialog[:dialog.index("</dialog>")]
    assert 'class="adlink"' in body  # the cycle 83 icon: the ACTIVE ad's verified link is the split link
    assert f'href="/test/{rec["test_id"]}/go-live"' not in body


def test_a_refused_go_live_fails_the_job_with_the_reason(monkeypatch, client):
    rec = _site_test(monkeypatch)
    client.post(f"/test/{rec['test_id']}/go-live", data={"confirm": "yes", "csrf": _csrf()}, headers=_auth())
    fake = golive_fake({"id": "c9", "object_story_id": "111_999"})
    monkeypatch.setattr(meta_golive, "client_for", lambda tenant: make_client(fake))
    job = jobs.run_one(TENANT, log=lambda *_: None)
    assert job["state"] == "failed" and "by hand in Ads Manager" in job["reason"]
    body = client.get("/", headers=_auth()).get_data(as_text=True)
    assert "by hand in Ads Manager" in body and "Go live on Meta" in body  # can be tried again


# ---------------------------------------------------------------------------
# 5. results: Meta numbers, cost per order, the leaderboard
# ---------------------------------------------------------------------------

def test_insights_are_read_for_the_live_period_and_cached_for_an_hour():
    import datetime

    rec = live_record(meta_go_live={"ad_id": AD, "at": "2026-10-12T16:00:00Z"})
    fake = MetaFake().add("GET", f"{AD}/insights", {"data": [
        {"spend": "40.50", "impressions": "9000", "inline_link_clicks": "180"},
        {"spend": "9.50", "impressions": "1000", "inline_link_clicks": "20"}]})
    now = datetime.datetime(2026, 10, 14, 9, 0, tzinfo=datetime.timezone.utc)
    rec, how = meta_golive.refresh_insights(TENANT, rec, client=make_client(fake), now=now)
    assert how == "fetched"
    assert fake.calls[0]["query"]["time_range"] == ['{"since": "2026-10-12", "until": "2026-10-14"}']
    assert fake.calls[0]["query"]["fields"] == ["spend,impressions,inline_link_clicks"]
    ins = abtest.load_test(TENANT, TEST_ID)["meta_insights"]
    assert (ins["spend"], ins["impressions"], ins["link_clicks"]) == ("50.00", 10000, 200)
    _rec, how = meta_golive.refresh_insights(TENANT, rec, client=make_client(fake),
                                             now=now + datetime.timedelta(minutes=30))
    assert how == "cached" and len(fake.calls) == 1
    meta_golive.refresh_insights(TENANT, rec, client=make_client(fake), now=now + datetime.timedelta(minutes=61))
    assert len(fake.calls) == 2
    assert "spend $50.00, 10000 impressions, 200 link clicks (CTR 2.00%)" in meta_golive.format_insights(
        abtest.load_test(TENANT, TEST_ID))


def test_spend_is_shared_by_assignments_and_cost_per_order_follows():
    rows = [{"key": "A", "assigns": 300, "orders": 3}, {"key": "B", "assigns": 100, "orders": 0},
            {"key": "C", "assigns": 100, "orders": None}]
    meta_golive.with_costs(rows, {"spend": "50.00"})
    assert [(r["spend"], r["cost_per_order"]) for r in rows] == [("30.00", "10.00"), ("10.00", None), ("10.00", None)]
    even = meta_golive.with_costs([{"key": "A", "assigns": 0, "orders": 1}, {"key": "B", "assigns": 0}],
                                  {"spend": "10"})
    assert [r["spend"] for r in even] == ["5.00", "5.00"] and even[0]["cost_per_order"] == "5.00"
    assert meta_golive.with_costs([{"key": "A"}], None) == [{"key": "A"}]


def _events(test_id, key, views, clicks):
    db = abevents.db_path(TENANT)
    _insert(db, test_id, key, "view", views)
    if clicks:
        _insert(db, test_id, key, "cta", clicks)


def _two_tests():
    live_record(orders_cache={"fetched_at": "2026-10-13T08:00:00Z", "by_key": {
        "A": {"orders": 2, "revenue": "9000.00"}, "B": {"orders": 0, "revenue": "0.00"},
        "C": {"orders": 1, "revenue": "4500.00"}}})
    _events(TEST_ID, "A", 40, 10)   # cinematic:caps
    _events(TEST_ID, "B", 40, 4)    # open:split
    _events(TEST_ID, "C", 20, 2)    # proof:caps
    live_record(test_id="second-test-cd56", status="finished")  # no orders cached
    _events("second-test-cd56", "A", 10, 5)
    _events("second-test-cd56", "B", 5, 1)
    _events("second-test-cd56", "C", 30, 3)
    live_record(test_id="built-test-ef78", status="built")  # not pooled
    _events("built-test-ef78", "A", 99, 99)


def test_leaderboard_by_build_ranks_by_cta_rate_with_honest_order_rates(monkeypatch):
    tenant_copy(monkeypatch, {"abtest.min_views": 30})
    _two_tests()
    rows, min_views = abtest.leaderboard(TENANT, by="build")
    assert min_views == 30
    by_group = {r["group"]: r for r in rows}
    cin = by_group["landing:cinematic:caps"]
    assert (cin["tests"], cin["views"], cin["clicks"]) == (2, 50, 15) and cin["cta_rate"] == pytest.approx(0.3)
    assert cin["orders"] == 2 and cin["order_views"] == 40  # the second test has no order data
    assert cin["orders_per_1000"] == pytest.approx(50.0)
    assert [r["group"] for r in rows] == ["landing:cinematic:caps", "landing:open:split", "landing:proof:caps"]
    assert [r["rank"] for r in rows] == [1, 2, 3]
    assert by_group["landing:proof:caps"]["cta_rate"] == pytest.approx(0.1)
    # by orders per 1000 views cinematic (2 in 40) and proof (1 in 20) tie at 50.0; CTA rate breaks it
    rows, _ = abtest.leaderboard(TENANT, by="build", sort="orders")
    assert [r["group"] for r in rows][:2] == ["landing:cinematic:caps", "landing:proof:caps"]


def test_leaderboard_lists_builds_under_min_views_unranked_and_groups_by_look(monkeypatch, capsys):
    tenant_copy(monkeypatch, {"abtest.min_views": 46})
    _two_tests()
    rows, _ = abtest.leaderboard(TENANT, by="build")
    assert [(r["group"], r["rank"]) for r in rows] == [
        ("landing:cinematic:caps", 1), ("landing:proof:caps", 2), ("landing:open:split", None)]
    looks = {r["group"]: r for r in abtest.leaderboard(TENANT, by="look")[0]}
    assert set(looks) == {"landing:cinematic", "landing:open", "landing:proof"}
    assert cli.main(["abtest", "leaderboard", "--tenant", TENANT.name]) == 0
    out = capsys.readouterr().out
    assert "Leaderboard by build (look x first screen), ranked by CTA rate" in out
    assert "landing:open:split" in out and "(under 46 views: not ranked)" in out
    assert "min_views = 46" in out
    assert cli.main(["abtest", "leaderboard", "--by", "look", "--sort", "orders", "--tenant", TENANT.name]) == 0
    assert "ranked by orders per 1000 views" in capsys.readouterr().out


def test_results_page_shows_the_leaderboard_and_per_arm_meta_numbers(monkeypatch, client):
    tenant_copy(monkeypatch, {"abtest.min_views": 46})
    _two_tests()
    rec = abtest.load_test(TENANT, TEST_ID)
    rec["meta_insights"] = {"ad_id": AD, "since": "2026-10-12", "until": "2026-10-13", "spend": "60.00",
                            "impressions": 12000, "link_clicks": 240, "fetched_at": "2026-10-13T08:00:00Z"}
    abtest.save_test(TENANT, rec)
    _insert(abevents.db_path(TENANT), TEST_ID, "A", "assign", 20)
    _insert(abevents.db_path(TENANT), TEST_ID, "B", "assign", 10)
    body = client.get("/results", headers=_auth()).get_data(as_text=True)
    assert "By build (look x first screen)" in body and "By look" in body
    assert "under 46 views: not ranked" in body
    assert f'id="t-{TEST_ID}"' in body and "spend <b>$60.00</b>, 12000 impressions, 240 link clicks" in body
    assert "$40.00" in body and "$20.00" in body  # A: 20 of 30 assigns; cost per order 40/2
    assert "No Meta numbers cached yet" in body  # the second test
    assert 'href="/results"' in body  # in the sidebar


def test_refresh_metrics_caches_orders_and_meta_numbers(meta, monkeypatch, capsys):
    live_record(meta_go_live={"ad_id": AD, "at": "2026-10-12T16:00:00Z"})
    orders = {"A": {"orders": 1, "revenue": "4500.00"}, "B": {"orders": 0, "revenue": "0.00"},
              "C": {"orders": 0, "revenue": "0.00"}}
    monkeypatch.setattr(cli, "_abtest_orders", lambda tenant, rec: (orders, None))
    meta["fake"] = MetaFake().add("GET", f"{AD}/insights", {"data": [{"spend": "12.30", "impressions": "500",
                                                                      "inline_link_clicks": "9"}]})
    assert cli.main(["abtest", "refresh-metrics", "--tenant", TENANT.name]) == 0
    assert f"{TEST_ID}" in capsys.readouterr().out
    rec = abtest.load_test(TENANT, TEST_ID)
    assert rec["orders_cache"]["by_key"] == orders and rec["meta_insights"]["spend"] == "12.30"
    assert [c["method"] for c in meta["fake"].calls] == ["GET"]
    script = (REPO_ROOT / "crons" / "meta-pull.sh").read_text()
    assert script.index("abtest from-inbox") < script.index("abtest refresh-metrics")


def test_site_endpoints_are_registered_and_csrf_checked():
    for endpoint in ("test_go_live", "test_revert_go_live", "results_home"):
        assert endpoint in site.ENDPOINTS
    assert {"test_go_live", "test_revert_go_live"} <= site.CSRF_ENDPOINTS
    assert {"meta_go_live", "meta_revert_go_live"} <= set(jobs.JOB_TYPES) <= set(jobs.HANDLERS)


def test_copy_drops_the_read_only_image_url_when_an_image_hash_is_present():
    """Cycle 107b: Meta returns both image_url and image_hash on a creative it
    serves, and refuses that pair on create (code 100 / 1443051, seen on the
    first real go-live 2026-10-12). The copy keeps the hash and drops the url."""
    creative = {"id": "c1", "url_tags": "utm_source=facebook", "object_story_spec": {
        "page_id": "111", "video_data": {
            "video_id": "9", "image_hash": "abc", "image_url": "https://www.facebook.com/ads/image/?d=X",
            "call_to_action": {"type": "LEARN_MORE", "value": {"link": OLD}}}}}
    payload, _changes = meta_golive.copy_creative(creative, SPLIT, name="copy")
    vd = payload["object_story_spec"]["video_data"]
    assert "image_url" not in vd and vd["image_hash"] == "abc"
    link = {"id": "c2", "object_story_spec": {"page_id": "111", "link_data": {
        "link": OLD, "image_hash": "h1", "image_url": "https://www.facebook.com/ads/image/?d=Y",
        "child_attachments": [{"link": OLD, "image_hash": "h2", "image_url": "https://www.facebook.com/ads/image/?d=Z"}]}}}
    payload, _changes = meta_golive.copy_creative(link, SPLIT, name="copy")
    ld = payload["object_story_spec"]["link_data"]
    assert "image_url" not in ld and "image_url" not in ld["child_attachments"][0]
    only_url = {"id": "c3", "object_story_spec": {"page_id": "111", "link_data": {"link": OLD, "image_url": "https://x/y.jpg"}}}
    payload, _changes = meta_golive.copy_creative(only_url, SPLIT, name="copy")
    assert payload["object_story_spec"]["link_data"]["image_url"] == "https://x/y.jpg"


def test_the_copy_names_the_pages_connected_instagram_account_when_the_creative_has_a_stale_one(meta):
    """Cycle 107c: the first real go-live carried an Instagram id Meta no
    longer resolves; the copy uses the Page's connected account instead."""
    fake = MetaFake()
    fake.add("GET", AD, ad(video_creative(), "ACTIVE"))
    fake.add("GET", "111", {"id": "111", "instagram_business_account": {"id": "333"}})
    summary = meta_golive.plan(TENANT, live_record(), client=make_client(fake))
    assert summary["payload"]["object_story_spec"]["instagram_user_id"] == "333"
    assert {"field": "object_story_spec.instagram_user_id", "old": "222", "new": "333"} in summary["changes"]
    # the same account: nothing changes and nothing extra is listed
    fake = MetaFake()
    fake.add("GET", AD, ad(video_creative(), "ACTIVE"))
    fake.add("GET", "111", {"id": "111", "instagram_business_account": {"id": "222"}})
    summary = meta_golive.plan(TENANT, live_record(), client=make_client(fake))
    assert summary["payload"]["object_story_spec"]["instagram_user_id"] == "222"
    assert not [c for c in summary["changes"] if c["field"].endswith("instagram_user_id")]


def test_the_read_back_is_repeated_when_meta_still_shows_the_old_creative(monkeypatch):
    """Cycle 107d: the first read after the update can lag; a later read
    that shows the new creative verifies the change."""
    fake = MetaFake()
    fake.add("GET", AD, {"id": AD, "effective_status": "ACTIVE", "creative": video_creative(OLD, "c1")})
    fake.add("GET", AD, {"id": AD, "effective_status": "IN_PROCESS", "creative": video_creative(SPLIT, "c2")})
    naps = []
    back, verified, seen = meta_golive._read_back(make_client(fake), AD, "c2", SPLIT, sleep=naps.append)
    assert verified and seen == SPLIT and naps == [meta_golive.READ_BACK_WAIT]
    fake = MetaFake()
    for _ in range(meta_golive.READ_BACK_TRIES):
        fake.add("GET", AD, {"id": AD, "effective_status": "ACTIVE", "creative": video_creative(OLD, "c1")})
    back, verified, seen = meta_golive._read_back(make_client(fake), AD, "c2", SPLIT, sleep=naps.append)
    assert not verified and seen == OLD and len(naps) == meta_golive.READ_BACK_TRIES
