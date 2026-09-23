"""Cycle 68: harness/meta_ingest.py -- pull new ads from the tenant's Meta ad
account into tenants/<t>/meta_inbox/.

Every Graph API call and every media download here goes through a fake
transport; the JSON bodies are hand-written from Meta's documented response
shapes (tests/fixtures/meta/). No test in this file opens a socket -- the
suite's _no_network guard would fail it."""
import datetime
import json
import urllib.parse
from pathlib import Path

import pytest

from harness import cli
from harness import ingest
from harness import meta_ingest
from harness import tenant as tenant_mod
from harness.budget import Budget
from harness.log import RunLog
from harness.meta_ingest import (
    GraphClient,
    Inbox,
    MediaRejected,
    MetaAPIError,
    MetaAuthError,
    MetaTokenMissing,
)
from tests.conftest import FakeClient, json_response
from tests.support import TENANT

FIXTURES = Path(__file__).parent / "fixtures" / "meta"
TOKEN = "EAAtest-SECRET-token-0123456789"
ACCOUNT = "act_963439094733982"
V = "v24.0"
SINCE = datetime.datetime(2026, 9, 23, tzinfo=datetime.timezone.utc)


def fixture(name):
    return json.loads((FIXTURES / name).read_text())


class FakeGraph:
    """The Graph API transport: (method, url, *, headers, body) -> (status,
    bytes), the same shape as harness/publishers/shopify.py's transport.

    Routes are keyed by URL path, plus "?after=<cursor>" when the request
    carries a paging cursor. Each key holds a queue of (status, body)
    responses; the last one repeats."""

    def __init__(self, routes=None):
        self.calls = []
        self.routes = {}
        for key, responses in (routes or {}).items():
            for status, body in responses:
                self.add(key, status, body)

    def add(self, key, status, body):
        self.routes.setdefault(key, []).append((status, json.dumps(body).encode()))

    def __call__(self, method, url, *, headers, body=None):
        self.calls.append({"method": method, "url": url, "headers": dict(headers)})
        parts = urllib.parse.urlsplit(url)
        query = urllib.parse.parse_qs(parts.query)
        key = parts.path + (f"?after={query['after'][0]}" if "after" in query else "")
        queue = self.routes.get(key)
        if not queue:
            raise AssertionError(f"no fake Graph response for {method} {key}")
        return queue.pop(0) if len(queue) > 1 else queue[0]

    def query(self, index):
        return urllib.parse.parse_qs(urllib.parse.urlsplit(self.calls[index]["url"]).query)


class FakeDownloads:
    """The media download transport: url -> (status, headers, chunks)."""

    def __init__(self, files=None):
        self.calls = []
        self.files = dict(files or {})

    def __call__(self, url, *, headers):
        self.calls.append({"url": url, "headers": dict(headers)})
        key = url.split("?")[0]
        if key not in self.files:
            raise AssertionError(f"no fake download for {key}")
        status, response_headers, chunks = self.files[key]
        return status, dict(response_headers), iter(list(chunks))


VIDEO_URL = "https://video.xx.fbcdn.net/v/t42.1790-2/101.mp4"
IMAGE_URL = "https://scontent.xx.fbcdn.net/v/t45.1600-4/full-102.jpg"


def video_download(size=1024, content_type="video/mp4", length_header=True):
    headers = {"Content-Type": content_type}
    if length_header:
        headers["Content-Length"] = str(size)
    return (200, headers, [b"v" * (size // 2), b"v" * (size - size // 2)])


def full_routes():
    """Both ads pages, the video lookups, and the adimages lookup."""
    video = fixture("video.json")
    routes = {
        f"/{V}/{ACCOUNT}/ads": [(200, fixture("ads_page1.json"))],
        f"/{V}/{ACCOUNT}/ads?after=QVFIUAFTER1": [(200, fixture("ads_page2.json"))],
        f"/{V}/{ACCOUNT}/adimages": [(200, fixture("adimages.json"))],
    }
    for vid in ("900000000000001", "900000000000004", "900000000000005"):
        routes[f"/{V}/{vid}"] = [(200, dict(video, id=vid))]
    return routes


def make_client(graph, downloads=None, sleeps=None):
    return GraphClient(
        TOKEN, version=V, transport=graph,
        download_transport=downloads or FakeDownloads(),
        sleep=(sleeps.append if sleeps is not None else (lambda s: None)),
    )


def default_downloads():
    return FakeDownloads({
        VIDEO_URL: video_download(),
        IMAGE_URL: (200, {"Content-Type": "image/jpeg", "Content-Length": "300"}, [b"j" * 300]),
    })


# ---------------------------------------------------------------------------
# Listing: paging, created_time cutoff, status filter
# ---------------------------------------------------------------------------

def test_list_new_ads_follows_paging_next_and_filters_created_time_and_status():
    graph = FakeGraph(full_routes())
    ads = meta_ingest.list_new_ads(make_client(graph), ACCOUNT, SINCE)
    ids = [a["id"] for a in ads]
    # 103 was created before the cutoff (only edited after it); 106 is DELETED.
    assert ids == [
        "120210000000000101", "120210000000000102", "120210000000000104",
        "120210000000000105", "120210000000000107",
    ]
    ads_calls = [c for c in graph.calls if "/ads" in c["url"]]
    assert len(ads_calls) == 2  # page 1, then paging.next


def test_list_request_asks_meta_for_the_statuses_and_a_server_side_superset():
    graph = FakeGraph(full_routes())
    meta_ingest.list_new_ads(make_client(graph), ACCOUNT, SINCE)
    query = graph.query(0)
    statuses = json.loads(query["effective_status"][0])
    for status in ("ACTIVE", "PAUSED", "IN_PROCESS"):
        assert status in statuses
    assert "DELETED" not in statuses and "ARCHIVED" not in statuses
    # updated_since >= created cutoff is a superset: an ad created after the
    # cutoff was also updated after it.
    assert int(query["updated_since"][0]) == int(SINCE.timestamp())
    fields = query["fields"][0]
    for field in ("created_time", "effective_status", "adset{id,name}", "campaign{id,name}",
                  "object_story_spec", "asset_feed_spec", "video_id", "image_hash", "url_tags"):
        assert field in fields


def test_a_status_outside_the_list_is_skipped_even_if_meta_returns_it():
    page = {"data": [
        {"id": "1", "created_time": "2026-09-24T00:00:00+0000", "effective_status": "ARCHIVED", "creative": {}},
        {"id": "2", "created_time": "2026-09-24T00:00:00+0000", "effective_status": "DISAPPROVED", "creative": {}},
        {"id": "3", "created_time": "2026-09-24T00:00:00+0000", "effective_status": "ACTIVE", "creative": {}},
    ], "paging": {}}
    graph = FakeGraph({f"/{V}/{ACCOUNT}/ads": [(200, page)]})
    ads = meta_ingest.list_new_ads(make_client(graph), ACCOUNT, SINCE)
    assert [a["id"] for a in ads] == ["3"]


def test_paging_next_on_another_host_is_refused():
    page = fixture("ads_page1.json")
    page["paging"]["next"] = "https://evil.example/v24.0/act_963439094733982/ads?after=X"
    graph = FakeGraph({f"/{V}/{ACCOUNT}/ads": [(200, page)]})
    with pytest.raises(MetaAPIError, match="paging"):
        meta_ingest.list_new_ads(make_client(graph), ACCOUNT, SINCE)
    assert all("evil.example" not in c["url"] for c in graph.calls)


def test_parse_meta_time_reads_metas_offset_format():
    parsed = meta_ingest.parse_meta_time("2026-09-24T09:15:02-0700")
    assert parsed == datetime.datetime(2026, 9, 24, 16, 15, 2, tzinfo=datetime.timezone.utc)
    assert meta_ingest.parse_meta_time("garbage") is None


# ---------------------------------------------------------------------------
# Normalizing and media resolution
# ---------------------------------------------------------------------------

def _ad(page, ad_id):
    return next(a for a in fixture(page)["data"] if a["id"] == ad_id)


def test_normalize_video_ad():
    norm = meta_ingest.normalize_ad(_ad("ads_page1.json", "120210000000000101"))
    assert norm["ad_id"] == "120210000000000101"
    assert norm["ad_name"] == "Hidden costs v3 - UGC"
    assert norm["primary_text"].startswith("I didn't want to talk to anyone")
    assert norm["headline"] == "No sales call. Just the price."
    assert norm["description"] == "Free shipping on every sauna"
    assert norm["cta"] == "SHOP_NOW"
    assert norm["destination_url"] == "https://peaksaunas.com/products/fuji"
    assert norm["url_tags"] == "utm_source=meta&utm_medium=paid"
    assert norm["adset"] == {"id": "120210000000000201", "name": "Broad US 25-65"}
    assert norm["campaign"]["name"] == "Prospecting - Video"
    chosen = meta_ingest.choose_media(norm["media_candidates"])
    assert chosen["kind"] == "video" and chosen["video_id"] == "900000000000001"


def test_normalize_image_ad_uses_link_data():
    norm = meta_ingest.normalize_ad(_ad("ads_page1.json", "120210000000000102"))
    assert norm["primary_text"] == "The price is on the page. No quote form, no call."
    assert norm["headline"] == "See every price up front"
    assert norm["destination_url"] == "https://peaksaunas.com/pages/pricing"
    assert norm["cta"] == "LEARN_MORE"
    chosen = meta_ingest.choose_media(norm["media_candidates"])
    assert chosen["kind"] == "image" and chosen["image_hash"] == "a1b2c3d4e5f6a7b8c9d0e1f2a3b4c5d6"


def test_carousel_takes_the_first_video_and_records_every_card():
    norm = meta_ingest.normalize_ad(_ad("ads_page2.json", "120210000000000104"))
    kinds = [(c["kind"], c.get("video_id") or c.get("image_hash")) for c in norm["media_candidates"]]
    assert ("image", "c0c0c0c0c0c0c0c0c0c0c0c0c0c0c001") in kinds
    assert ("video", "900000000000004") in kinds
    assert ("image", "c0c0c0c0c0c0c0c0c0c0c0c0c0c0c003") in kinds
    chosen = meta_ingest.choose_media(norm["media_candidates"])
    assert chosen["video_id"] == "900000000000004"
    assert norm["primary_text"] == "Four saunas. Four prices. All on the page."


def test_asset_feed_spec_takes_the_first_video_and_the_first_text_variants():
    norm = meta_ingest.normalize_ad(_ad("ads_page2.json", "120210000000000105"))
    assert norm["primary_text"] == "A sauna at home costs less than a year of memberships."
    assert norm["headline"] == "Your own sauna, priced up front"
    assert norm["description"] == "Ships free"
    assert norm["destination_url"] == "https://peaksaunas.com/"
    assert norm["cta"] == "SHOP_NOW"
    chosen = meta_ingest.choose_media(norm["media_candidates"])
    assert chosen["kind"] == "video" and chosen["video_id"] == "900000000000005"
    assert any(c["kind"] == "image" for c in norm["media_candidates"])


def test_asset_feed_spec_with_images_only_takes_the_first_image():
    raw = {"id": "9", "creative": {"asset_feed_spec": {"images": [{"hash": "h1"}, {"hash": "h2"}]}}}
    chosen = meta_ingest.choose_media(meta_ingest.normalize_ad(raw)["media_candidates"])
    assert chosen == {"kind": "image", "image_hash": "h1", "source": "asset_feed_spec.images[0]"}


def test_catalog_ad_has_no_media():
    norm = meta_ingest.normalize_ad(_ad("ads_page2.json", "120210000000000107"))
    assert meta_ingest.choose_media(norm["media_candidates"]) is None


# ---------------------------------------------------------------------------
# pull(): the inbox, idempotence, states
# ---------------------------------------------------------------------------

def run_pull(tmp_path, graph=None, downloads=None, **kwargs):
    graph = graph or FakeGraph(full_routes())
    downloads = downloads or default_downloads()
    inbox = Inbox(tmp_path / "meta_inbox")
    messages = []
    summary = meta_ingest.pull(
        make_client(graph, downloads), inbox, account_id=ACCOUNT, since=SINCE,
        log=messages.append, **kwargs,
    )
    return summary, inbox, graph, downloads, messages


def test_pull_writes_each_new_ad_into_the_inbox(tmp_path):
    summary, inbox, graph, downloads, _ = run_pull(tmp_path)
    video = inbox.read("120210000000000101")
    assert video["state"] == "new"
    assert video["media_type"] == "video"
    assert video["media_file"] == "meta-120210000000000101.mp4"
    assert (inbox.item_dir("120210000000000101") / "meta-120210000000000101.mp4").stat().st_size == 1024
    assert video["video"]["length"] == 31.4
    for key in ("ad_id", "ad_name", "created_time", "primary_text", "headline", "description",
                "cta", "destination_url", "media_type", "media_file", "reason", "history"):
        assert key in video

    image = inbox.read("120210000000000102")
    assert image["media_type"] == "image"
    assert image["media_file"] == "meta-120210000000000102.jpg"
    assert image["image"]["width"] == 1080
    adimages = [c for c in graph.calls if c["url"].split("?")[0].endswith("/adimages")]
    assert json.loads(graph.query(graph.calls.index(adimages[0]))["hashes"][0]) == [
        "a1b2c3d4e5f6a7b8c9d0e1f2a3b4c5d6"
    ]

    catalog = inbox.read("120210000000000107")
    assert catalog["state"] == "skipped"
    assert "no video or image" in catalog["reason"]
    assert inbox.read("120210000000000103") is None  # before the cutoff
    assert inbox.read("120210000000000106") is None  # DELETED
    assert sorted(summary["ingested"]) == [
        "120210000000000101", "120210000000000102", "120210000000000104", "120210000000000105",
    ]
    assert summary["skipped"] == ["120210000000000107"]


def test_the_media_file_is_what_harness_run_accepts(tmp_path):
    _, inbox, _, _, _ = run_pull(tmp_path)
    for ad_id in ("120210000000000101", "120210000000000102"):
        item = inbox.read(ad_id)
        path = inbox.item_dir(ad_id) / item["media_file"]
        assert ingest.detect_type(path) in ("video", "still")


def test_a_second_pull_is_idempotent(tmp_path):
    run_pull(tmp_path)
    summary, inbox, graph, downloads, _ = run_pull(tmp_path)
    assert summary["ingested"] == []
    assert sorted(summary["existing"]) == [
        "120210000000000101", "120210000000000102", "120210000000000104",
        "120210000000000105", "120210000000000107",
    ]
    assert downloads.calls == []
    # only the two listing pages; no per-ad lookups
    assert all("/ads" in c["url"] for c in graph.calls)


def test_refresh_downloads_again_and_keeps_a_later_state(tmp_path):
    _, inbox, _, _, _ = run_pull(tmp_path)
    inbox.set_state("120210000000000101", "queued", "picked for an A/B/C test")
    summary, inbox, _, downloads, _ = run_pull(tmp_path, refresh=True)
    assert "120210000000000101" in summary["ingested"]
    assert any(c["url"].startswith(VIDEO_URL) for c in downloads.calls)
    item = inbox.read("120210000000000101")
    assert item["state"] == "queued"
    assert any(h.get("reason") == "refreshed from Meta" for h in item["history"])


def test_dry_run_writes_nothing_and_downloads_nothing(tmp_path):
    summary, inbox, graph, downloads, messages = run_pull(tmp_path, dry_run=True)
    assert len(summary["would_ingest"]) == 5
    assert not (tmp_path / "meta_inbox").exists() or not any((tmp_path / "meta_inbox").iterdir())
    assert downloads.calls == []
    assert all("/ads" in c["url"] for c in graph.calls)
    assert any("would ingest" in m for m in messages)


def test_limit_caps_the_number_of_new_items(tmp_path):
    summary, inbox, _, _, _ = run_pull(tmp_path, limit=2)
    assert summary["ingested"] == ["120210000000000101", "120210000000000102"]
    assert inbox.read("120210000000000104") is None


def test_a_failed_pull_is_retried_on_the_next_pull(tmp_path):
    downloads = FakeDownloads({
        VIDEO_URL: (503, {"Content-Type": "text/html"}, [b"busy"]),
        IMAGE_URL: (200, {"Content-Type": "image/jpeg"}, [b"j" * 10]),
    })
    summary, inbox, _, _, _ = run_pull(tmp_path, downloads=downloads)
    assert "120210000000000101" in summary["failed"]
    assert inbox.read("120210000000000101")["state"] == "failed"
    summary, inbox, _, _, _ = run_pull(tmp_path)
    assert "120210000000000101" in summary["ingested"]
    item = inbox.read("120210000000000101")
    assert item["state"] == "new"
    assert [h["state"] for h in item["history"]] == ["failed", "new"]


def test_a_video_without_a_source_fails_with_a_clear_reason(tmp_path):
    routes = full_routes()
    routes[f"/{V}/900000000000001"] = [(200, {"id": "900000000000001", "length": 30.0})]
    summary, inbox, _, _, _ = run_pull(tmp_path, graph=FakeGraph(routes))
    item = inbox.read("120210000000000101")
    assert item["state"] == "failed"
    assert "source" in item["reason"]


# ---------------------------------------------------------------------------
# Downloads: size cap and content type
# ---------------------------------------------------------------------------

@pytest.fixture
def download_dir(tmp_path):
    """An empty directory of its own (tmp_path also holds the suite's
    tenant-isolation directory)."""
    path = tmp_path / "downloads"
    path.mkdir()
    return path


def test_download_over_the_cap_by_content_length_is_refused_before_reading(download_dir):
    downloads = FakeDownloads({VIDEO_URL: video_download(size=2048)})
    client = make_client(FakeGraph(), downloads)
    dest = download_dir / "x.mp4"
    with pytest.raises(MediaRejected, match="cap"):
        client.download(VIDEO_URL, dest, kind="video", max_bytes=1000)
    assert not dest.exists()
    assert not list(download_dir.iterdir())


def test_download_over_the_cap_while_streaming_removes_the_partial_file(download_dir):
    downloads = FakeDownloads({VIDEO_URL: video_download(size=2048, length_header=False)})
    client = make_client(FakeGraph(), downloads)
    with pytest.raises(MediaRejected, match="cap"):
        client.download(VIDEO_URL, download_dir / "x.mp4", kind="video", max_bytes=1500)
    assert not list(download_dir.iterdir())


def test_download_with_the_wrong_content_type_is_refused(download_dir):
    downloads = FakeDownloads({VIDEO_URL: video_download(content_type="text/html")})
    client = make_client(FakeGraph(), downloads)
    with pytest.raises(MediaRejected, match="content type"):
        client.download(VIDEO_URL, download_dir / "x.mp4", kind="video", max_bytes=10_000)
    assert not list(download_dir.iterdir())


def test_download_never_sends_the_token_to_the_cdn(tmp_path):
    downloads = FakeDownloads({VIDEO_URL: video_download()})
    client = make_client(FakeGraph(), downloads)
    client.download(VIDEO_URL + "?oh=sig", tmp_path / "x.mp4", kind="video", max_bytes=10_000)
    assert "Authorization" not in downloads.calls[0]["headers"]
    assert TOKEN not in json.dumps(downloads.calls)


def test_pull_records_a_size_cap_failure(tmp_path):
    summary, inbox, _, _, _ = run_pull(tmp_path, max_bytes=500)
    item = inbox.read("120210000000000101")
    assert item["state"] == "failed"
    assert "cap" in item["reason"]
    assert not (inbox.item_dir("120210000000000101") / "meta-120210000000000101.mp4").exists()


# ---------------------------------------------------------------------------
# Inbox states
# ---------------------------------------------------------------------------

def test_state_transitions_follow_the_lifecycle(tmp_path):
    inbox = Inbox(tmp_path / "meta_inbox")
    inbox.write({"ad_id": "123", "state": "new", "reason": "", "history": [{"state": "new", "at": "t", "reason": ""}]})
    for state in ("queued", "building", "tested"):
        inbox.set_state("123", state, f"to {state}")
    item = inbox.read("123")
    assert item["state"] == "tested"
    assert [h["state"] for h in item["history"]] == ["new", "queued", "building", "tested"]
    assert item["reason"] == "to tested"
    with pytest.raises(ValueError, match="tested -> queued"):
        inbox.set_state("123", "queued")


def test_an_invalid_transition_is_refused(tmp_path):
    inbox = Inbox(tmp_path / "meta_inbox")
    inbox.write({"ad_id": "123", "state": "new", "reason": "", "history": []})
    with pytest.raises(ValueError, match="new -> tested"):
        inbox.set_state("123", "tested")
    with pytest.raises(ValueError, match="unknown state"):
        inbox.set_state("123", "published")


def test_inbox_refuses_an_ad_id_that_is_not_a_plain_id(tmp_path):
    inbox = Inbox(tmp_path / "meta_inbox")
    with pytest.raises(ValueError):
        inbox.item_dir("../escape")


# ---------------------------------------------------------------------------
# Token handling
# ---------------------------------------------------------------------------

def test_the_token_goes_in_the_authorization_header_never_the_url(tmp_path):
    _, _, graph, downloads, messages = run_pull(tmp_path)
    assert graph.calls
    for call in graph.calls:
        assert call["headers"]["Authorization"] == f"Bearer {TOKEN}"
        assert TOKEN not in call["url"]
        assert "access_token" not in call["url"]  # stripped from paging.next too
    assert all(TOKEN not in m for m in messages)
    for path in (tmp_path / "meta_inbox").rglob("*.json"):
        assert TOKEN not in path.read_text()


def test_an_error_body_that_echoes_the_token_is_redacted():
    body = {"error": {"message": f"Invalid parameter near {TOKEN}", "type": "OAuthException", "code": 100}}
    graph = FakeGraph({f"/{V}/me": [(400, body)]})
    with pytest.raises(MetaAPIError) as exc_info:
        make_client(graph).get("me", {"fields": "id,name"})
    assert TOKEN not in str(exc_info.value)
    assert TOKEN not in repr(exc_info.value.args)
    assert "[redacted]" in str(exc_info.value)


def test_an_expired_token_is_a_clear_auth_error_without_the_token():
    graph = FakeGraph({f"/{V}/me": [(400, fixture("error_token_expired.json"))]})
    sleeps = []
    with pytest.raises(MetaAuthError) as exc_info:
        make_client(graph, sleeps=sleeps).get("me", {"fields": "id,name"})
    message = str(exc_info.value)
    assert "invalid or expired" in message
    assert "META-INGEST.md" in message
    assert TOKEN not in message
    assert sleeps == []  # never retried


def test_token_from_env_missing_names_the_variable_and_makes_no_call(monkeypatch):
    monkeypatch.delenv("META_ACCESS_TOKEN", raising=False)
    cfg = meta_ingest.meta_config(TENANT)
    with pytest.raises(MetaTokenMissing) as exc_info:
        meta_ingest.token_from_env(cfg, TENANT)
    message = str(exc_info.value)
    assert "META_ACCESS_TOKEN" in message
    assert "No Meta API call was made" in message


# ---------------------------------------------------------------------------
# Retry / backoff
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("code", [17, 4, 32, 613])
def test_rate_limit_codes_are_retried_with_backoff(code):
    error = fixture("error_rate_limit.json")
    error["error"]["code"] = code
    graph = FakeGraph()
    graph.add(f"/{V}/me", 400, error)
    graph.add(f"/{V}/me", 400, error)
    graph.add(f"/{V}/me", 200, fixture("me.json"))
    sleeps = []
    data = make_client(graph, sleeps=sleeps).get("me", {"fields": "id,name"})
    assert data["name"] == "Harness Ingest (system user)"
    assert len(graph.calls) == 3
    assert len(sleeps) == 2 and sleeps[1] > sleeps[0] > 0


@pytest.mark.parametrize("status", [500, 502, 503, 429])
def test_http_5xx_and_429_are_retried(status):
    graph = FakeGraph()
    graph.add(f"/{V}/me", status, fixture("error_server.json"))
    graph.add(f"/{V}/me", 200, fixture("me.json"))
    sleeps = []
    make_client(graph, sleeps=sleeps).get("me", {"fields": "id,name"})
    assert len(graph.calls) == 2 and len(sleeps) == 1


def test_retries_stop_after_the_limit():
    graph = FakeGraph({f"/{V}/me": [(400, fixture("error_rate_limit.json"))]})
    sleeps = []
    with pytest.raises(MetaAPIError, match="17"):
        make_client(graph, sleeps=sleeps).get("me", {"fields": "id,name"})
    assert len(graph.calls) == meta_ingest.MAX_RETRIES + 1
    assert len(sleeps) == meta_ingest.MAX_RETRIES


def test_a_plain_bad_request_is_not_retried():
    body = {"error": {"message": "(#100) Tried accessing nonexisting field", "type": "OAuthException", "code": 100}}
    graph = FakeGraph({f"/{V}/me": [(400, body)]})
    sleeps = []
    with pytest.raises(MetaAPIError, match="100"):
        make_client(graph, sleeps=sleeps).get("me", {"fields": "id,name"})
    assert len(graph.calls) == 1 and sleeps == []


# ---------------------------------------------------------------------------
# Tenant config
# ---------------------------------------------------------------------------

def test_peak_saunas_meta_config():
    cfg = meta_ingest.meta_config(TENANT)
    assert cfg["ad_account_id"] == ACCOUNT
    assert cfg["graph_api_version"] == V
    assert cfg["ingest_since"] == SINCE
    assert cfg["token_env"] == "META_ACCESS_TOKEN"


def test_meta_is_a_known_tenant_key():
    assert "meta" in tenant_mod.known_tenant_keys()
    errors, warnings = TENANT.validate()
    assert not any("meta" in w for w in warnings)


def test_meta_config_without_an_account_is_a_clear_error(tmp_path):
    root = tmp_path / "acme"
    root.mkdir()
    (root / "tenant.yaml").write_text("name: Acme\nmeta:\n  ad_account_id: ''\n")
    with pytest.raises(meta_ingest.MetaConfigError, match="ad_account_id"):
        meta_ingest.meta_config(tenant_mod.Tenant("acme", root))


def test_meta_config_without_a_cutoff_is_a_clear_error(tmp_path):
    root = tmp_path / "acme"
    root.mkdir()
    (root / "tenant.yaml").write_text("name: Acme\nmeta:\n  ad_account_id: act_1\n")
    with pytest.raises(meta_ingest.MetaConfigError, match="ingest_since"):
        meta_ingest.meta_config(tenant_mod.Tenant("acme", root))


# ---------------------------------------------------------------------------
# Suite isolation: meta_inbox is redirected like out_dir
# ---------------------------------------------------------------------------

def test_meta_inbox_dir_is_isolated_in_tests(isolated_tenant_paths):
    assert TENANT.meta_inbox_dir == isolated_tenant_paths / "meta_inbox"
    assert tenant_mod.load_tenant("peak-saunas").meta_inbox_dir == isolated_tenant_paths / "meta_inbox"


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

@pytest.fixture
def no_real_env(monkeypatch):
    """The real tenants/peak-saunas/.env may hold a token one day; a CLI test
    must never load it."""
    monkeypatch.setattr(tenant_mod.Tenant, "load_env", lambda self: False)
    monkeypatch.delenv("META_ACCESS_TOKEN", raising=False)


def test_cli_meta_check_without_a_token(no_real_env, capsys):
    code = cli.main(["meta", "check", "--tenant", "peak-saunas"])
    err = capsys.readouterr().err
    assert code == 1
    assert "META_ACCESS_TOKEN" in err
    assert "No Meta API call was made" in err


def test_cli_meta_check_with_a_token(no_real_env, monkeypatch, capsys):
    monkeypatch.setenv("META_ACCESS_TOKEN", TOKEN)
    graph = FakeGraph({
        f"/{V}/me": [(200, fixture("me.json"))],
        f"/{V}/{ACCOUNT}": [(200, fixture("account.json"))],
    })
    monkeypatch.setattr(meta_ingest, "make_client", lambda token, cfg: make_client(graph))
    code = cli.main(["meta", "check", "--tenant", "peak-saunas"])
    out = capsys.readouterr().out
    assert code == 0
    assert "PEAK Saunas" in out and "ACTIVE" in out
    assert TOKEN not in out
    assert graph.query(1)["fields"] == ["name,account_status"]


def test_cli_meta_check_with_an_expired_token(no_real_env, monkeypatch, capsys):
    monkeypatch.setenv("META_ACCESS_TOKEN", TOKEN)
    graph = FakeGraph({f"/{V}/me": [(400, fixture("error_token_expired.json"))]})
    monkeypatch.setattr(meta_ingest, "make_client", lambda token, cfg: make_client(graph))
    code = cli.main(["meta", "check", "--tenant", "peak-saunas"])
    captured = capsys.readouterr()
    assert code == 1
    assert "invalid or expired" in captured.err
    assert TOKEN not in captured.err + captured.out


def test_cli_meta_pull_then_inbox(no_real_env, monkeypatch, capsys):
    monkeypatch.setenv("META_ACCESS_TOKEN", TOKEN)
    graph = FakeGraph(full_routes())
    monkeypatch.setattr(meta_ingest, "make_client", lambda token, cfg: make_client(graph, default_downloads()))
    assert cli.main(["meta", "pull", "--tenant", "peak-saunas", "--limit", "1"]) == 0
    out = capsys.readouterr().out
    assert "120210000000000101" in out
    assert TOKEN not in out
    assert (TENANT.meta_inbox_dir / "120210000000000101" / "ad.json").exists()

    assert cli.main(["meta", "inbox", "--tenant", "peak-saunas"]) == 0
    out = capsys.readouterr().out
    assert "120210000000000101" in out and "new" in out and "video" in out


def test_cli_meta_pull_dry_run_and_since(no_real_env, monkeypatch, capsys):
    monkeypatch.setenv("META_ACCESS_TOKEN", TOKEN)
    graph = FakeGraph(full_routes())
    monkeypatch.setattr(meta_ingest, "make_client", lambda token, cfg: make_client(graph))
    code = cli.main(["meta", "pull", "--tenant", "peak-saunas", "--dry-run", "--since", "2026-09-26"])
    out = capsys.readouterr().out
    assert code == 0
    assert "would ingest" in out
    assert "120210000000000104" in out and "120210000000000101" not in out
    assert not TENANT.meta_inbox_dir.exists() or not any(TENANT.meta_inbox_dir.iterdir())
    assert int(graph.query(0)["updated_since"][0]) == int(
        datetime.datetime(2026, 9, 26, tzinfo=datetime.timezone.utc).timestamp()
    )


def test_cli_meta_pull_rejects_a_bad_since(no_real_env, monkeypatch, capsys):
    monkeypatch.setenv("META_ACCESS_TOKEN", TOKEN)
    assert cli.main(["meta", "pull", "--tenant", "peak-saunas", "--since", "last tuesday"]) == 1
    assert "--since" in capsys.readouterr().err


# ---------------------------------------------------------------------------
# Ad copy passthrough: an inbox item's ad.json reaches the ad brief
# ---------------------------------------------------------------------------

BRIEF = {
    "hook": "h", "promise": "p", "angle": "a", "claims_made": [], "speaker_experience": [],
    "features_shown": [], "objections_raised": [], "cta": "Shop now", "tone": "calm",
    "speaker_pov": "brand", "source_file": "meta-1.jpg", "input_type": "still",
    "transcript_or_text": "PRICE ON THE PAGE", "audience": "",
}


def _inbox_still(tmp_path):
    item = tmp_path / "meta_inbox" / "1"
    item.mkdir(parents=True)
    (item / "meta-1.jpg").write_bytes(b"\xff\xd8\xff fake jpeg")
    (item / "ad.json").write_text(json.dumps({
        "ad_id": "1", "media_file": "meta-1.jpg",
        "primary_text": "The price is on the page. No quote form, no call.",
        "headline": "See every price up front", "description": "Home infrared saunas",
        "cta": "LEARN_MORE", "destination_url": "https://peaksaunas.com/pages/pricing",
    }))
    return item / "meta-1.jpg"


def test_load_ad_copy_reads_the_inbox_sidecar(tmp_path):
    media = _inbox_still(tmp_path)
    copy = ingest.load_ad_copy(media)
    assert copy == {
        "primary_text": "The price is on the page. No quote form, no call.",
        "headline": "See every price up front",
        "description": "Home infrared saunas",
        "cta": "Learn more",
    }


def test_load_ad_copy_ignores_an_ad_json_for_another_file(tmp_path):
    media = _inbox_still(tmp_path)
    other = media.parent / "other.jpg"
    other.write_bytes(b"x")
    assert ingest.load_ad_copy(other) is None
    assert ingest.load_ad_copy(tmp_path / "loose.mov") is None


def test_a_still_ad_with_copy_sends_the_copy_to_the_brief(tmp_path):
    media = _inbox_still(tmp_path)
    client = FakeClient(["PRICE ON THE PAGE", json_response(BRIEF)])
    log = RunLog("t", tmp_path / "t.log")
    brief = ingest.run_ingest(
        input_arg=str(media), workdir=tmp_path / "work", client=client, model="claude-haiku-4-5",
        budget=Budget(), log=log, ffmpeg_bin="ffmpeg", whisper_bin="w", whisper_model="m",
    )
    log.close()
    call = client.messages.calls[1]
    payload = json.loads(call["messages"][0]["content"])
    assert payload["ad_copy"]["primary_text"] == "The price is on the page. No quote form, no call."
    assert payload["ad_copy"]["headline"] == "See every price up front"
    assert "destination_url" not in payload["ad_copy"]
    # the transcript stays the on-image text only: ad copy is not the speaker
    assert payload["transcript_or_text"] == "PRICE ON THE PAGE"
    assert "ad_copy" in call["system"] and "speaker_experience" in call["system"]
    assert brief["transcript_or_text"] == "PRICE ON THE PAGE"


def test_without_a_sidecar_the_brief_prompt_is_unchanged(tmp_path):
    media = tmp_path / "ad.txt"
    media.write_text("I just wanted the price.")
    client = FakeClient([json_response(dict(BRIEF, input_type="text"))])
    log = RunLog("t", tmp_path / "t.log")
    ingest.run_ingest(
        input_arg=str(media), workdir=tmp_path / "work", client=client, model="claude-haiku-4-5",
        budget=Budget(), log=log, ffmpeg_bin="ffmpeg", whisper_bin="w", whisper_model="m",
    )
    log.close()
    call = client.messages.calls[0]
    assert "ad_copy" not in json.loads(call["messages"][0]["content"])
    assert call["system"] == ingest.AD_BRIEF_SYSTEM
