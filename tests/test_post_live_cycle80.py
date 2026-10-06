"""Cycle 80: "Post live (unlinked)" -- the one narrow exception to
publish_locked -- and the redesigned review site around it.

The lock stays on for everything automatic or indirect (A/B/C publish, auto
publish, redirects, a plain `harness publish --live`). harness/postlive.py's
path publishes one page live at an explicit lp-<slug> handle with no redirect
and no test, records who did it, and can update or hide it again.

No network: the REAL _make_publisher / LockedShopifyPublisher run over
tests/test_publishers.py's FakeTransport.
"""
import json
from pathlib import Path

import pytest

from harness import abtest, cli, jobs, meta_ingest, postlive, revise, runstate, site
from harness.publishers.shopify import ShopifyPublisher
from tests.support import TENANT
from tests.test_listicle_site_cycle69 import (  # noqa: F401 -- `client` is a fixture
    OTHER, PNG, REVIEWER, _auth, _built_test, _csrf, _fake_revise, _feedback, _older_run, _upload, client,
)
from tests.test_publishers import FakeTransport

PAGE = "listicle"
HANDLE = "lp-small-space-excuses-mini"
PAGE_ID = 500


def _lookup_suffix(handle):
    return f"pages.json?handle={handle}&fields=id%2Chandle%2Cpublished_at"


def _store(monkeypatch, *, handle=HANDLE, taken=False):
    """The real publisher factory (so the real lock decides), over a fake store."""
    transport = FakeTransport()
    transport.set_response("GET", _lookup_suffix(handle), 200,
                           {"pages": [{"id": 9, "handle": handle}] if taken else []})
    transport.set_response("POST", "pages.json", 201, {"page": {"id": PAGE_ID, "handle": handle}})
    transport.set_response("PUT", f"pages/{PAGE_ID}.json", 200, {"page": {"id": PAGE_ID, "handle": handle}})
    transport.set_response("POST", "redirects.json", 201, {"redirect": {"id": 1}})
    real = cli._make_publisher
    made = []

    def make(tenant, *, export_dir, **kw):
        p = real(tenant, export_dir=export_dir, **kw)
        if isinstance(p, ShopifyPublisher):
            p.store, p.token = "acme.myshopify.com", "tok"
            p._transport = p._upload_transport = transport
        made.append(p)
        return p

    monkeypatch.setattr(cli, "_make_publisher", make)
    monkeypatch.setattr(ShopifyPublisher, "verify_cache", lambda self, *a, **k: (8, 8))
    return transport, made


def _run(name="small-space.mov"):
    run_dir = _older_run(name)
    (run_dir / PAGE / "page.json").write_text(json.dumps({
        "headline": "She wanted a sauna. Her apartment had a small footprint.",
        "display_headline": "Small space, no excuses", "hero_style": "face"}))
    (run_dir / "facts_pack.json").write_text(json.dumps({"product": {"name": "Mini"}}))
    return run_dir


def _calls(transport, method, suffix=""):
    return [c for c in transport.calls if c["method"] == method and suffix in c["url"]]


def _post_live(client, run_dir, handle=HANDLE, *, csrf=None):
    return client.post(f"/gen/{run_dir.name}/{PAGE}/post-live",
                       data={"csrf": csrf if csrf is not None else _csrf(), "confirm": "yes", "handle": handle},
                       headers=_auth())


# ---------------------------------------------------------------------------
# 1. the lock still blocks every automatic or indirect path
# ---------------------------------------------------------------------------

def test_peak_is_still_locked():
    assert TENANT.get("publish_locked") is True


def test_plain_publish_live_is_still_refused(monkeypatch, capsys):
    transport, made = _store(monkeypatch)
    run_dir = _run()
    runstate.approve(run_dir, TENANT, by=REVIEWER, pages=[PAGE])
    runstate.set_packet_stamp(run_dir, stamp="ship", by=REVIEWER)
    rc = cli.main(["publish", str(run_dir), "--page", PAGE, "--live", "--handle", HANDLE, "--tenant", TENANT.name])
    assert rc == 1 and "publish_locked" in capsys.readouterr().err
    assert isinstance(made[-1], cli.LockedShopifyPublisher) and made[-1].unlinked_live is False
    assert not _calls(transport, "POST", "pages.json") and runstate.published_page_record(run_dir, PAGE) is None


def test_publish_has_no_flag_that_lifts_the_lock():
    for flag in ("--unlinked-live", "--unlinked_live"):
        with pytest.raises(SystemExit):
            cli.build_parser().parse_args(["publish", "r", "--page", PAGE, "--live", flag])


def test_redirects_stay_locked_even_for_the_unlinked_publisher(monkeypatch):
    p = cli.LockedShopifyPublisher(store="acme.myshopify.com", token="tok", transport=FakeTransport(),
                                   unlinked_live=True)
    with pytest.raises(cli.PublishLocked):
        p.create_redirect("/a", "/pages/b")


def test_the_unlinked_publisher_needs_an_explicit_handle():
    transport = FakeTransport()
    transport.set_response("POST", "pages.json", 201, {"page": {"id": 1, "handle": "lp-a"}})
    p = cli.LockedShopifyPublisher(store="acme.myshopify.com", token="tok", transport=transport, unlinked_live=True)
    with pytest.raises(cli.PublishLocked):
        p.publish({"title": "t", "body_html": "b"}, unpublished=False)
    assert transport.calls == []
    p.publish({"title": "t", "body_html": "b", "handle": "lp-a"}, unpublished=False)
    assert json.loads(transport.calls[0]["body"])["page"]["published"] is True


def test_cmd_publish_refuses_an_unlinked_publish_with_a_redirect_or_without_a_handle(monkeypatch):
    transport, _made = _store(monkeypatch)
    run_dir = _run()
    runstate.approve(run_dir, TENANT, by=REVIEWER, pages=[PAGE])
    runstate.set_packet_stamp(run_dir, stamp="ship", by=REVIEWER)
    base = dict(run_dir=str(run_dir), page=PAGE, live=True, dry_run=False, update=False, tenant=TENANT.name,
                seo_hidden=False, unlinked_live=True, by=REVIEWER)
    import argparse
    assert cli.cmd_publish(argparse.Namespace(**base, handle=HANDLE, redirect_from="/x")) == 1
    assert cli.cmd_publish(argparse.Namespace(**base, handle=None, redirect_from=None)) == 1
    assert transport.calls == []


def test_abtest_publish_is_still_locked(monkeypatch, client):
    transport, _made = _store(monkeypatch)
    rec = _built_test(monkeypatch)
    assert cli.main(["abtest", "publish", rec["test_id"], "--by", REVIEWER, "--tenant", TENANT.name]) != 0
    assert not _calls(transport, "POST", "pages.json") and not _calls(transport, "POST", "redirects.json")
    assert abtest.load_test(TENANT, rec["test_id"])["status"] != "live"


def test_auto_publish_is_still_locked(monkeypatch, client):
    from harness import abtest_inbox
    from tests.test_abtest_cycle67 import FakeRunner

    transport, _made = _store(monkeypatch)
    monkeypatch.setitem(TENANT.config["abtest"], "auto_publish", True)
    monkeypatch.setattr(abtest_inbox, "_auto_publish", lambda tenant: True)
    monkeypatch.setattr(abtest, "default_runner", FakeRunner())
    _upload(client, name="Locked upload", data=PNG, filename="ad.png")
    done = jobs.run_one(TENANT)
    assert done["state"] == "done" and "did not publish" in (done["result"]["publish_error"] or "")
    assert not done["result"]["split_url"] and not _calls(transport, "POST", "pages.json")
    assert abtest.load_test(TENANT, done["result"]["test_id"])["status"] == "built"


# ---------------------------------------------------------------------------
# 2. handles
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("handle", ["", "LP-UPPER", "small-space", "lp--double", "lp-ends-", "lp-near-zero-emf-mini",
                                    "lp-" + "a" * 80, "lp-has space", "lp-a/b"])
def test_bad_handles_are_refused(handle):
    assert postlive.handle_problem(handle, TENANT)


def test_good_handle_passes():
    assert postlive.handle_problem("lp-cold-mornings-sauna", TENANT) is None


def test_suggested_handle_is_topic_plus_model_and_unique():
    run_dir = _run()
    assert postlive.suggest_handle(TENANT, run_dir, PAGE, taken=set()) == HANDLE
    assert postlive.suggest_handle(TENANT, run_dir, PAGE, taken={HANDLE}) == HANDLE + "-2"
    assert postlive.suggest_handle(TENANT, run_dir, PAGE, taken={HANDLE, HANDLE + "-2"}) == HANDLE + "-3"
    assert postlive.handle_problem(postlive.suggest_handle(TENANT, run_dir, PAGE, taken=set()), TENANT) is None


def test_handles_in_use_reads_every_run(monkeypatch):
    run_dir = _run()
    data = runstate.load_state(run_dir)
    data["published_pages"] = {PAGE: {"page_id": 1, "handle": "lp-taken", "url": "u"}}
    runstate.save_state(run_dir, data)
    assert "lp-taken" in postlive.handles_in_use(TENANT)


def test_route_refuses_a_bad_or_used_handle_and_queues_nothing(monkeypatch, client):
    _store(monkeypatch)
    run_dir, other = _run(), _run("other-ad.mov")
    data = runstate.load_state(other)
    data["published_pages"] = {PAGE: {"page_id": 7, "handle": "lp-taken", "url": "https://peaksaunas.com/pages/lp-taken"}}
    runstate.save_state(other, data)
    bad = _post_live(client, run_dir, "No Prefix")
    assert bad.status_code == 400 and "single hyphens" in bad.get_data(as_text=True)
    used = _post_live(client, run_dir, "lp-taken")
    assert used.status_code == 400 and "already used" in used.get_data(as_text=True)
    assert jobs.list_jobs(TENANT) == []


def test_a_handle_shopify_already_has_fails_the_job_before_anything_is_created(monkeypatch, client):
    transport, _made = _store(monkeypatch, taken=True)
    run_dir = _run()
    assert _post_live(client, run_dir).status_code == 303
    job = jobs.run_one(TENANT)
    assert job["state"] == "failed" and "already used by Shopify page 9" in job["reason"]
    assert not _calls(transport, "POST", "pages.json")
    assert runstate.published_page_record(run_dir, PAGE) is None


# ---------------------------------------------------------------------------
# 3. post live -> update -> unpublish, through the site and the worker
# ---------------------------------------------------------------------------

def test_post_live_job_publishes_at_the_handle_with_no_redirect_and_records_who(monkeypatch, client):
    transport, made = _store(monkeypatch)
    run_dir = _run()
    page_url = f"/gen/{run_dir.name}/{PAGE}"
    body = client.get(page_url, headers=_auth()).get_data(as_text=True)
    assert "Post live" in body and f'value="{HANDLE}"' in body and 'id="post-live"' in body

    assert _post_live(client, run_dir).status_code == 303
    [job] = jobs.list_jobs(TENANT)
    assert job["type"] == "post_live" and job["payload"] == {"run_id": run_dir.name, "page": PAGE, "by": REVIEWER,
                                                             "handle": HANDLE}
    done = jobs.run_one(TENANT)
    assert done["state"] == "done", done
    url = f"https://peaksaunas.com/pages/{HANDLE}"
    assert done["result"] == {"url": url, "handle": HANDLE, "live": True, "unlinked": True}

    # the real lock, with only the narrow flag set
    assert isinstance(made[-1], cli.LockedShopifyPublisher) and made[-1].unlinked_live is True
    [create] = _calls(transport, "POST", "pages.json")
    sent = json.loads(create["body"])["page"]
    assert sent["published"] is True and sent["handle"] == HANDLE and "metafields" not in sent
    assert _calls(transport, "GET", _lookup_suffix(HANDLE))
    assert not _calls(transport, "POST", "redirects.json") and not _calls(transport, "PUT")

    state = runstate.load_state(run_dir)
    record = state["published_pages"][PAGE]
    assert record["unlinked"] is True and record["live"] is True and record["by"] == REVIEWER and record["at"]
    assert record["handle"] == HANDLE and record["url"] == url and record["page_id"] == PAGE_ID
    assert state["pages"][PAGE] == "published"
    assert any(h["state"] == "approved" and h["by"] == REVIEWER for h in state["history"])
    assert runstate.load_packet(run_dir)["stamp"] == "ship" and runstate.load_packet(run_dir)["by"] == REVIEWER
    assert state["history"][-1]["by"] == REVIEWER and f"url={url} live=True" in state["history"][-1]["note"]
    assert not runstate.abtest_record(run_dir, PAGE)
    actions = [a["action"] for a in jobs.audit_rows(TENANT)]
    assert actions[:2] == ["post_live_done", "post_live"]

    body = client.get(page_url, headers=_auth()).get_data(as_text=True)
    assert url in body and "Copy" in body and "Update live page" in body and "Unpublish" in body
    assert 'id="post-live"' not in body
    home = client.get("/?f=live", headers=_auth()).get_data(as_text=True)
    assert url in home and run_dir.name in home


def test_update_and_unpublish_and_post_again(monkeypatch, client):
    transport, _made = _store(monkeypatch)
    run_dir = _run()
    _post_live(client, run_dir)
    jobs.run_one(TENANT)
    monkeypatch.setattr(revise, "revise_page", _fake_revise)
    _feedback(client, run_dir, PAGE, "Tighter copy", email=OTHER)
    jobs.run_one(TENANT)

    # update: confirm page first, then a PUT over the same page, still live
    confirm = client.get(f"/gen/{run_dir.name}/{PAGE}/update-live", headers=_auth(OTHER)).get_data(as_text=True)
    assert 'name="confirm" value="yes"' in confirm and HANDLE in confirm
    assert client.post(f"/gen/{run_dir.name}/{PAGE}/update-live", data={"csrf": _csrf(OTHER)},
                       headers=_auth(OTHER)).status_code == 400
    assert client.post(f"/gen/{run_dir.name}/{PAGE}/update-live", data={"csrf": _csrf(OTHER), "confirm": "yes"},
                       headers=_auth(OTHER)).status_code == 303
    done = jobs.run_one(TENANT)
    assert done["type"] == "update_live" and done["state"] == "done", done
    [put] = _calls(transport, "PUT", f"pages/{PAGE_ID}.json")
    sent = json.loads(put["body"])["page"]
    assert sent["published"] is True and "REVISED BODY 2" in sent["body_html"]
    assert len(_calls(transport, "POST", "pages.json")) == 1  # no second page
    record = runstate.published_page_record(run_dir, PAGE)
    assert record["by"] == REVIEWER and record["updated_by"] == OTHER and record["live"] is True

    # unpublish: only `published: false` is sent
    assert client.post(f"/gen/{run_dir.name}/{PAGE}/unpublish", data={"csrf": _csrf(), "confirm": "yes"},
                       headers=_auth()).status_code == 303
    done = jobs.run_one(TENANT)
    assert done["type"] == "unpublish_live" and done["state"] == "done", done
    put = _calls(transport, "PUT", f"pages/{PAGE_ID}.json")[-1]
    assert json.loads(put["body"]) == {"page": {"id": PAGE_ID, "published": False}}
    record = runstate.published_page_record(run_dir, PAGE)
    assert record["live"] is False and record["unpublished_by"] == REVIEWER and record["by"] == REVIEWER
    body = client.get(f"/gen/{run_dir.name}/{PAGE}", headers=_auth()).get_data(as_text=True)
    assert "Hidden" in body and 'id="post-live"' in body and f"/gen/{run_dir.name}/{PAGE}/unpublish" not in body
    home = client.get("/?f=live", headers=_auth()).get_data(as_text=True)
    assert run_dir.name not in home

    # post again: the hidden page goes live in place, at its own handle
    assert _post_live(client, run_dir, "lp-ignored-for-a-hidden-page").status_code == 303
    done = jobs.run_one(TENANT)
    assert done["state"] == "done", done
    assert len(_calls(transport, "POST", "pages.json")) == 1
    assert json.loads(_calls(transport, "PUT", f"pages/{PAGE_ID}.json")[-1]["body"])["page"]["published"] is True
    record = runstate.published_page_record(run_dir, PAGE)
    assert record["live"] is True and record["handle"] == HANDLE and "unpublished_by" not in record


def test_update_and_unpublish_are_refused_for_a_page_that_is_not_unlinked_live(monkeypatch, client):
    _store(monkeypatch)
    run_dir = _run()
    for action in ("update-live", "unpublish"):
        resp = client.post(f"/gen/{run_dir.name}/{PAGE}/{action}", data={"csrf": _csrf(), "confirm": "yes"},
                           headers=_auth())
        assert resp.status_code == 409
    assert jobs.list_jobs(TENANT) == []


def test_a_test_variant_is_never_posted_on_its_own(monkeypatch, client):
    _store(monkeypatch)
    rec = _built_test(monkeypatch)
    v = rec["variants"][0]
    run_dir = Path(v["run_dir"])
    runstate.record_abtest(run_dir, page=v["cartridge"], test_id=rec["test_id"], key=v["key"])
    resp = client.post(f"/gen/{run_dir.name}/{v['cartridge']}/post-live",
                       data={"csrf": _csrf(), "confirm": "yes", "handle": "lp-variant-a"}, headers=_auth())
    assert resp.status_code == 409 and jobs.list_jobs(TENANT) == []
    with pytest.raises(postlive.PostLiveRefused, match="A/B/C test"):
        postlive.post_live(TENANT, run_dir, v["cartridge"], by=REVIEWER, handle="lp-variant-a")


def test_only_a_listed_reviewer_can_post(monkeypatch):
    transport, _made = _store(monkeypatch)
    run_dir = _run()
    with pytest.raises(postlive.PostLiveRefused, match="not a listed reviewer"):
        postlive.post_live(TENANT, run_dir, PAGE, by="nobody@example.com", handle=HANDLE)
    jobs.enqueue(TENANT, "post_live", {"run_id": run_dir.name, "page": PAGE, "by": "nobody@example.com",
                                       "handle": HANDLE}, by="nobody@example.com", target="t")
    job = jobs.run_one(TENANT)
    assert job["state"] == "failed" and "not a listed reviewer" in job["reason"]
    assert transport.calls == [] and runstate.load_state(run_dir)["pages"][PAGE] == "needs_review"


def test_new_routes_need_login_and_the_form_token(monkeypatch, client):
    _store(monkeypatch)
    run_dir = _run()
    for action in ("post-live", "update-live", "unpublish"):
        url = f"/gen/{run_dir.name}/{PAGE}/{action}"
        assert client.get(url).status_code == 401
        assert client.post(url, data={"confirm": "yes"}).status_code == 401
        assert client.post(url, data={"confirm": "yes", "handle": HANDLE}, headers=_auth()).status_code == 403
    assert client.get(f"/run/{run_dir.name}/ad-frame").status_code == 401
    assert jobs.list_jobs(TENANT) == []


def test_cli_post_live(monkeypatch, capsys):
    transport, _made = _store(monkeypatch)
    run_dir = _run()
    rc = cli.main(["post-live", str(run_dir), "--page", PAGE, "--handle", HANDLE, "--by", REVIEWER,
                   "--tenant", TENANT.name])
    assert rc == 0, capsys.readouterr().err
    assert f"live at https://peaksaunas.com/pages/{HANDLE}" in capsys.readouterr().out
    assert runstate.published_page_record(run_dir, PAGE)["unlinked"] is True
    rc = cli.main(["post-live", str(run_dir), "--page", PAGE, "--unpublish", "--by", REVIEWER, "--tenant", TENANT.name])
    assert rc == 0 and runstate.published_page_record(run_dir, PAGE)["live"] is False


# ---------------------------------------------------------------------------
# 4. the redesigned site reads what is already live
# ---------------------------------------------------------------------------

def test_a_page_published_by_hand_shows_live_with_its_url(monkeypatch, client):
    """The five runs of 2026-10-06 were published with the CLI (no `unlinked`
    flag): their published_pages record and history make them Live."""
    run_dir = _run()
    url = "https://peaksaunas.com/pages/lp-cold-mornings-sauna"
    runstate.approve(run_dir, TENANT, by=REVIEWER, pages=[PAGE])
    runstate.mark_published(run_dir, page=PAGE, by="operator", note=f"url={url} live=True", page_id=165230477613,
                            handle="lp-cold-mornings-sauna", url=url)
    home = client.get("/", headers=_auth()).get_data(as_text=True)
    assert url in home and ">Live<" in home
    assert "Live" in client.get("/?f=live", headers=_auth()).get_data(as_text=True)
    body = client.get(f"/gen/{run_dir.name}/{PAGE}", headers=_auth()).get_data(as_text=True)
    assert url in body and "Update live page" in body and "Unpublish" in body


def test_generation_page_shows_the_details_sidebar(monkeypatch, client):
    run_dir = _run()
    data = runstate.load_state(run_dir)
    data["listicle"] = {"style": "mistakes", "look": "open", "headline_template_id": "o4", "hero_style": "face"}
    data["jev"] = {"shipped": 1, "reason": "draft 1 passed", "drafts": [
        {"draft": 1, "gate": "PASS", "composite": 0.72, "scores": {"hook": 0.8}, "headline_template_id": "o4"},
        {"draft": 2, "gate": "FAIL", "composite": None, "scores": {}}]}
    runstate.save_state(run_dir, data)
    body = client.get(f"/gen/{run_dir.name}/{PAGE}", headers=_auth()).get_data(as_text=True)
    for text in ("Details", "Face", "mistakes", "o4", "0.72", "PASS", run_dir.name, "Drafts (best of 2)",
                 'data-view="desktop"', 'data-view="mobile"'):
        assert text in body, text
    home = client.get("/", headers=_auth()).get_data(as_text=True)
    assert "Jev <b>0.72</b>" in home and "Face" in home


def test_styled_error_pages(monkeypatch, client):
    resp = client.get("/gen/no-such-run/listicle", headers=_auth())
    assert resp.status_code == 404 and "Not found" in resp.get_data(as_text=True)
    assert 'class="topbar"' in resp.get_data(as_text=True)


def test_ad_frame_thumbnail(monkeypatch, client):
    run_dir = _run()
    (run_dir / "ad-frame").mkdir()
    from PIL import Image
    Image.new("RGB", (640, 360), (20, 20, 20)).save(run_dir / "ad-frame" / "frame.jpg")
    resp = client.get(f"/run/{run_dir.name}/ad-frame", headers=_auth())
    assert resp.status_code == 200 and resp.mimetype == "image/jpeg"
    assert f"/run/{run_dir.name}/ad-frame" in client.get("/", headers=_auth()).get_data(as_text=True)


def test_inbox_ad_name_names_the_generation(monkeypatch, client):
    inbox = meta_ingest.Inbox(TENANT.meta_inbox_dir)
    directory = inbox.item_dir("120210000000901")
    directory.mkdir(parents=True)
    inbox.write({"ad_id": "120210000000901", "ad_name": "PA | Morgan | product-features-v2 | DYN",
                 "media_type": "video", "media_file": "meta-120210000000901.mp4", "source": "meta", "state": "tested",
                 "reason": "", "history": []})
    run_dir = _run("meta-120210000000901.mp4")
    body = client.get(f"/gen/{run_dir.name}/{PAGE}", headers=_auth()).get_data(as_text=True)
    assert "PA | Morgan | product-features-v2 | DYN" in body


# ---------------------------------------------------------------------------
# 5. the review site's own favicon -- never on the generated pages
# ---------------------------------------------------------------------------

_ICON_MARKERS = ('rel="icon"', "apple-touch-icon", "site.webmanifest", "/favicon.ico")


def test_site_pages_link_the_favicon_and_serve_it(client):
    body = client.get("/", headers=_auth()).get_data(as_text=True)
    for marker in _ICON_MARKERS:
        assert marker in body, marker
    assert '<meta name="theme-color" content="#161817">' in body
    ico = client.get("/favicon.ico", headers=_auth())
    assert ico.status_code == 200 and ico.mimetype == "image/x-icon"
    assert ico.data == (Path(site.site_ui.STATIC_DIR) / "favicon.ico").read_bytes()
    for name in ("favicon-32.png", "apple-touch-icon.png", "icon-192.png", "icon-512.png"):
        resp = client.get(f"/site-static/{name}", headers=_auth())
        assert resp.status_code == 200 and resp.mimetype == "image/png" and resp.data[:4] == b"\x89PNG", name
    assert client.get("/site-static/..%2Fsite.py", headers=_auth()).status_code == 404
    assert client.get("/site-static/other.png", headers=_auth()).status_code == 404
    manifest = client.get("/site.webmanifest", headers=_auth())
    data = json.loads(manifest.data)
    assert manifest.mimetype == "application/manifest+json"
    assert data["name"] == f"{TENANT.display_name} Listicles" and data["theme_color"] == "#161817"
    assert {i["sizes"] for i in data["icons"]} == {"192x192", "512x512"}
    assert client.get("/favicon.ico").status_code == 401  # behind the same login as every page


def test_generated_pages_and_the_shopify_export_never_get_the_site_icon(monkeypatch, client):
    from harness import page_body
    from tests.support import REPO_ROOT

    for root in ("cartridges", "harness/templates", "harness/blocks"):
        for path in (REPO_ROOT / root).rglob("*.html"):
            text = path.read_text(errors="ignore")
            assert not any(m in text for m in _ICON_MARKERS), path
    run_dir = _run()
    body_html, _assets = page_body.build_shopify_body(run_dir / PAGE)
    assert not any(m in body_html for m in _ICON_MARKERS)
    review = client.get(f"/run/{run_dir.name}/review/{PAGE}", headers=_auth()).get_data(as_text=True)
    assert not any(m in review for m in _ICON_MARKERS)
