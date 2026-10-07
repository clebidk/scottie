"""Cycle 83: the linked-ad icon on the review site.

A page whose URL is the CURRENT destination of a Meta ad gets a link icon
next to its status pill (muted when no ACTIVE ad points at it). The data
comes from the inbox's ad.json files, which `harness meta refresh-links`
keeps current with GETs only. No test here opens a socket: Meta is
tests/test_meta_ingest.py's fake transport.
"""
import json

import pytest

from harness import abtest, cli, meta_ingest, postlive, runstate
from harness.meta_ingest import Inbox
from tests.support import TENANT
from tests.test_listicle_site_cycle69 import REVIEWER, _auth, client  # noqa: F401 -- `client` is a fixture
from tests.test_meta_ingest import TOKEN, V, FakeGraph, make_client, no_real_env  # noqa: F401 -- fixture
from tests.test_post_live_cycle80 import PAGE, _run

URL = "https://peaksaunas.com/pages/lp-cold-mornings-sauna"
AD_ACTIVE = "120210000000000201"
AD_PAUSED = "120210000000000202"
AD_OTHER = "120210000000000203"


def _ad(ad_id, name, destination, status="ACTIVE", **extra):
    item = {"ad_id": ad_id, "ad_name": name, "created_time": "2026-09-24T09:15:02-0700",
            "effective_status": status, "adset": {"id": "1", "name": "s"}, "campaign": {"id": "2", "name": "c"},
            "destination_url": destination, "url_tags": "utm_source=facebook", "media_type": "video",
            "media_file": f"meta-{ad_id}.mp4", "source": "meta", "state": "tested", "reason": "",
            "test_id": "cold-mornings-ab12", "pulled_at": "2026-10-01T08:00:00+00:00",
            "updated_at": "2026-10-01T09:00:00+00:00",
            "history": [{"state": "new", "at": "2026-10-01T08:00:00+00:00", "reason": ""}]}
    item.update(extra)
    Inbox(TENANT.meta_inbox_dir).write(item)
    return item


def _live_run(url=URL):
    run_dir = _run()
    runstate.approve(run_dir, TENANT, by=REVIEWER, pages=[PAGE])
    runstate.mark_published(run_dir, page=PAGE, by="operator", note=f"url={url} live=True", page_id=165230477613,
                            handle=url.rsplit("/", 1)[-1], url=url)
    return run_dir


# ---------------------------------------------------------------------------
# 1. URL normalisation
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("variant", [
    URL,
    URL + "?utm_source=facebook&utm_campaign=x",
    URL + "/",
    URL + "/?fbclid=abc#top",
    "https://www.peaksaunas.com/pages/lp-cold-mornings-sauna",
    "http://peaksaunas.com/pages/lp-cold-mornings-sauna",
    "HTTPS://WWW.PeakSaunas.com/pages/lp-cold-mornings-sauna",
    "https://peaksaunas.com/pages/lp%2Dcold%2Dmornings%2Dsauna",
])
def test_normalize_url_treats_these_as_the_same_page(variant):
    assert postlive.normalize_url(variant) == postlive.normalize_url(URL) == "peaksaunas.com/pages/lp-cold-mornings-sauna"


def test_normalize_url_keeps_other_pages_apart():
    assert postlive.normalize_url(URL) != postlive.normalize_url("https://peaksaunas.com/pages/lp-cold-mornings")
    assert postlive.normalize_url(URL) != postlive.normalize_url("https://shop.peaksaunas.com/pages/lp-cold-mornings-sauna")
    assert postlive.normalize_url("") == "" and postlive.normalize_url(None) == ""


# ---------------------------------------------------------------------------
# 2. linked_ads
# ---------------------------------------------------------------------------

def test_linked_ads_matches_the_live_url_active_first():
    _ad(AD_PAUSED, "Alpha paused", "http://www.peaksaunas.com/pages/lp-cold-mornings-sauna/", status="PAUSED",
        links_checked_at="2026-10-07T06:15:00+00:00")
    _ad(AD_ACTIVE, "Zulu active", URL + "?utm_source=facebook")
    _ad(AD_OTHER, "Other page", "https://peaksaunas.com/pages/lp-something-else")
    found = postlive.linked_ads(TENANT, URL)
    assert [a["ad_id"] for a in found] == [AD_ACTIVE, AD_PAUSED]
    assert found[0] == {"ad_id": AD_ACTIVE, "ad_name": "Zulu active", "effective_status": "ACTIVE",
                        "links_checked_at": "2026-10-01T08:00:00+00:00",  # never refreshed: pulled_at
                        "destination_url": URL + "?utm_source=facebook"}
    assert found[1]["links_checked_at"] == "2026-10-07T06:15:00+00:00"


def test_linked_ads_none():
    assert postlive.linked_ads(TENANT, URL) == []  # empty inbox
    _ad(AD_OTHER, "Other page", "https://peaksaunas.com/pages/lp-something-else")
    assert postlive.linked_ads(TENANT, URL) == []
    assert postlive.linked_ads(TENANT, "") == []


def test_linked_ads_matches_the_split_link_of_a_test_page():
    split = "https://peaksaunas.com/pages/lp-cold-mornings-ab12"
    abtest.save_test(TENANT, {"test_id": "cold-mornings-ab12", "status": "live", "variants": [],
                              "split": {"url": split, "handle": "lp-cold-mornings-ab12"}})
    _ad(AD_ACTIVE, "Split ad", split + "/?utm_source=facebook")
    variant_url = "https://peaksaunas.com/pages/lp-cold-mornings-ab12-a"
    assert postlive.linked_ads(TENANT, variant_url) == []
    by_state = postlive.linked_ads(TENANT, variant_url, test={"test_id": "cold-mornings-ab12", "key": "A"})
    by_record = postlive.linked_ads(TENANT, variant_url, test=abtest.load_test(TENANT, "cold-mornings-ab12"))
    assert [a["ad_id"] for a in by_state] == [a["ad_id"] for a in by_record] == [AD_ACTIVE]


# ---------------------------------------------------------------------------
# 3. harness meta refresh-links
# ---------------------------------------------------------------------------

def _creative(link):
    return {"object_story_spec": {"video_data": {"video_id": "9", "call_to_action": {
        "type": "LEARN_MORE", "value": {"link": link}}}}, "url_tags": "utm_source=facebook"}


NEW_URL = "https://peaksaunas.com/pages/lp-new-home?utm_source=facebook"


def _links_graph():
    return FakeGraph({f"/{V}/": [(200, {
        AD_ACTIVE: {"id": AD_ACTIVE, "effective_status": "PAUSED", "creative": _creative(NEW_URL)},
        AD_OTHER: {"id": AD_OTHER, "effective_status": "ACTIVE",
                   "creative": _creative("https://peaksaunas.com/pages/lp-something-else")},
    })]})


def _inbox_fixture():
    _ad(AD_ACTIVE, "Kälte am Morgen", URL)  # non-ASCII name: the file keeps ensure_ascii=False
    _ad(AD_OTHER, "Other page", "https://peaksaunas.com/pages/lp-something-else")
    upload = Inbox(TENANT.meta_inbox_dir).item_dir("up-abcd1234")
    upload.mkdir(parents=True)
    (upload / "ad.json").write_text(json.dumps({"ad_id": "up-abcd1234", "source": "upload"}, indent=2) + "\n")
    root = TENANT.meta_inbox_dir
    return {ad_id: (root / ad_id / "ad.json").read_text() for ad_id in (AD_ACTIVE, AD_OTHER, "up-abcd1234")}


def _strip(text):
    keys = tuple(f'  "{k}":' for k in meta_ingest.LINK_KEYS)
    return [line for line in text.splitlines() if not line.startswith(keys)]


def test_refresh_links_updates_only_the_three_fields(no_real_env, monkeypatch, capsys):
    monkeypatch.setenv("META_ACCESS_TOKEN", TOKEN)
    before = _inbox_fixture()
    graph = _links_graph()
    monkeypatch.setattr(meta_ingest, "make_client", lambda token, cfg: make_client(graph))
    assert cli.main(["meta", "refresh-links", "--tenant", TENANT.name]) == 0
    out = capsys.readouterr().out
    assert "refresh-links: 2 ad(s) checked, 1 changed, 0 not checked" in out and TOKEN not in out

    # one batched GET, ads only (the upload item is not a Meta ad), nothing written to Meta
    assert [c["method"] for c in graph.calls] == ["GET"]
    assert graph.query(0)["ids"] == [f"{AD_ACTIVE},{AD_OTHER}"]
    assert graph.query(0)["fields"] == [meta_ingest.LINK_FIELDS]

    root = TENANT.meta_inbox_dir
    after = {ad_id: (root / ad_id / "ad.json").read_text() for ad_id in before}
    changed = json.loads(after[AD_ACTIVE])
    assert changed["destination_url"] == NEW_URL and changed["effective_status"] == "PAUSED"
    assert changed["links_checked_at"].endswith("+00:00")
    unchanged = json.loads(after[AD_OTHER])
    assert unchanged["effective_status"] == "ACTIVE" and unchanged["links_checked_at"] == changed["links_checked_at"]
    for ad_id in (AD_ACTIVE, AD_OTHER):
        old = json.loads(before[ad_id])
        assert _strip(after[ad_id]) == _strip(before[ad_id])  # every other line byte-identical, same order
        keys = list(old)
        keys.insert(keys.index("destination_url") + 1, "links_checked_at")
        assert list(json.loads(after[ad_id])) == keys
    assert "Kälte am Morgen" in after[AD_ACTIVE]
    assert after["up-abcd1234"] == before["up-abcd1234"]
    assert postlive.linked_ads(TENANT, URL) == []
    assert [a["ad_id"] for a in postlive.linked_ads(TENANT, "https://www.peaksaunas.com/pages/lp-new-home")] == [AD_ACTIVE]


def test_refresh_links_dry_run_writes_nothing(no_real_env, monkeypatch, capsys):
    monkeypatch.setenv("META_ACCESS_TOKEN", TOKEN)
    before = _inbox_fixture()
    graph = _links_graph()
    monkeypatch.setattr(meta_ingest, "make_client", lambda token, cfg: make_client(graph))
    assert cli.main(["meta", "refresh-links", "--tenant", TENANT.name, "--dry-run"]) == 0
    out = capsys.readouterr().out
    assert f"would change {AD_ACTIVE}: destination_url {URL} -> {NEW_URL}; effective_status ACTIVE -> PAUSED" in out
    assert "1 would change" in out and "Nothing written." in out
    root = TENANT.meta_inbox_dir
    assert {ad_id: (root / ad_id / "ad.json").read_text() for ad_id in before} == before
    assert sorted(p.name for p in (root / AD_ACTIVE).iterdir()) == ["ad.json"]


def test_refresh_links_falls_back_to_one_get_per_ad_when_meta_refuses_the_batch():
    """Meta refuses a whole ?ids= call when one id cannot be read."""
    _ad(AD_ACTIVE, "Readable", URL)
    _ad(AD_OTHER, "Gone", "https://peaksaunas.com/pages/lp-something-else")
    gone = {"error": {"code": 100, "message": "Some of the aliases you requested do not exist"}}
    graph = FakeGraph({
        f"/{V}/": [(400, gone)],
        f"/{V}/{AD_ACTIVE}": [(200, {"id": AD_ACTIVE, "effective_status": "ACTIVE", "creative": _creative(URL)})],
        f"/{V}/{AD_OTHER}": [(400, gone)],
    })
    lines = []
    summary = meta_ingest.refresh_links(make_client(graph), Inbox(TENANT.meta_inbox_dir), log=lines.append)
    assert summary == {"checked": [AD_ACTIVE], "changed": [], "failed": [AD_OTHER]}
    assert any(line.startswith(f"not checked {AD_OTHER}:") for line in lines)
    assert "links_checked_at" in Inbox(TENANT.meta_inbox_dir).read(AD_ACTIVE)
    assert "links_checked_at" not in Inbox(TENANT.meta_inbox_dir).read(AD_OTHER)


def test_meta_pull_cron_refreshes_links_right_after_the_pull():
    from tests.test_listicle_site_cycle69 import REPO_ROOT

    script = (REPO_ROOT / "crons" / "meta-pull.sh").read_text()
    pull = script.index('/harness" meta pull --tenant')
    links = script.index('/harness" meta refresh-links --tenant "$TENANT"')
    assert pull < links < script.index('/harness" abtest from-inbox --tenant')


# ---------------------------------------------------------------------------
# 4. the site
# ---------------------------------------------------------------------------

def test_site_shows_the_icon_for_a_page_an_active_ad_points_at(client):
    run_dir = _live_run()
    _ad(AD_ACTIVE, "Cold Mornings", "https://www.peaksaunas.com/pages/lp-cold-mornings-sauna/?utm_source=facebook")
    title = "Destination of Meta ad Cold Mornings (ACTIVE)"
    home = client.get("/", headers=_auth()).get_data(as_text=True)
    assert f'<span class="adlink" role="img" title="{title}" aria-label="{title}">' in home
    assert ">Live<" in home  # the status word is unchanged
    body = client.get(f"/gen/{run_dir.name}/{PAGE}", headers=_auth()).get_data(as_text=True)
    assert f'class="adlink" role="img" title="{title}"' in body
    assert "<dt>Ads pointing here</dt>" in body and "Cold Mornings" in body and "ACTIVE" in body


def test_site_shows_a_muted_icon_when_only_paused_ads_point_at_the_page(client):
    run_dir = _live_run()
    _ad(AD_PAUSED, "Old Mornings", URL, status="PAUSED", links_checked_at="2026-10-07T06:15:00+00:00")
    _ad(AD_OTHER, "Second", URL, status="CAMPAIGN_PAUSED")
    body = client.get(f"/gen/{run_dir.name}/{PAGE}", headers=_auth()).get_data(as_text=True)
    title = ("Destination of Meta ad Old Mornings (PAUSED)\n"
             "Destination of Meta ad Second (CAMPAIGN_PAUSED)")
    assert f'<span class="adlink off" role="img" title="{title}"' in body
    assert "checked 2026-10-07 06:15" in body
    assert 'class="adlink" ' not in body


def test_site_shows_no_icon_when_no_ad_points_at_the_page(client):
    run_dir = _live_run()
    _ad(AD_OTHER, "Other page", "https://peaksaunas.com/pages/lp-something-else")
    home = client.get("/", headers=_auth()).get_data(as_text=True)
    assert 'class="adlink' not in home and ">Live<" in home
    body = client.get(f"/gen/{run_dir.name}/{PAGE}", headers=_auth()).get_data(as_text=True)
    assert 'class="adlink' not in body
    assert '<dt>Ads pointing here</dt><dd><span class="muted">none</span></dd>' in body


def test_site_has_no_ads_row_for_a_page_that_is_not_live(client):
    run_dir = _run()
    body = client.get(f"/gen/{run_dir.name}/{PAGE}", headers=_auth()).get_data(as_text=True)
    assert "Ads pointing here" not in body and 'class="adlink' not in body
