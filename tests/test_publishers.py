"""Cycle 20: the publisher adapters (harness/publishers/). The Shopify
adapter is exercised entirely through a fake transport -- no real HTTP call
is made anywhere in this file, matching the rest of this test suite."""
import json

import pytest

from harness.publishers.export import ExportPublisher
from harness.publishers.shopify import (
    ShopifyCredentialsMissing,
    ShopifyPublisher,
    normalize_shopify_store,
    rewrite_asset_srcs,
)


class FakeTransport:
    """Records every call and returns canned (status, json_bytes) pairs in
    order, keyed loosely by (method, path suffix) -- good enough for these
    tests' single-call-per-assertion style."""

    def __init__(self):
        self.calls = []
        self.responses = {}  # (method, path_suffix) -> (status, dict)

    def set_response(self, method, path_suffix, status, body):
        self.responses[(method, path_suffix)] = (status, json.dumps(body).encode("utf-8"))

    def __call__(self, method, url, *, headers, body=None):
        self.calls.append({"method": method, "url": url, "headers": headers, "body": body})
        for (m, suffix), resp in self.responses.items():
            if m == method and url.endswith(suffix):
                return resp
        raise AssertionError(f"no fake response registered for {method} {url}")


# ---------------------------------------------------------------------------
# Credentials
# ---------------------------------------------------------------------------

def test_dry_run_reports_missing_credentials_without_a_network_call(monkeypatch):
    monkeypatch.delenv("SHOPIFY_STORE", raising=False)
    monkeypatch.delenv("SHOPIFY_TOKEN", raising=False)
    transport = FakeTransport()
    publisher = ShopifyPublisher(transport=transport)
    report = publisher.dry_run({"body_html": "<p>hi</p>", "assets": []})
    assert report["ok"] is False
    assert "SHOPIFY_STORE" in report["reason"] and "SHOPIFY_TOKEN" in report["reason"]
    assert transport.calls == []


def test_publish_raises_credentials_missing_with_no_store(monkeypatch):
    monkeypatch.delenv("SHOPIFY_STORE", raising=False)
    monkeypatch.delenv("SHOPIFY_TOKEN", raising=False)
    transport = FakeTransport()
    publisher = ShopifyPublisher(transport=transport)
    with pytest.raises(ShopifyCredentialsMissing):
        publisher.publish({"title": "t", "body_html": "<p>hi</p>"})
    assert transport.calls == []


def test_upload_assets_raises_credentials_missing_before_any_call():
    transport = FakeTransport()
    publisher = ShopifyPublisher(store=None, token=None, transport=transport)
    with pytest.raises(ShopifyCredentialsMissing):
        publisher.upload_assets([{"local_path": "assets/a.jpg", "cdn_filename": "a.jpg", "alt": "", "bytes": b"x"}])


# ---------------------------------------------------------------------------
# dry_run with credentials present
# ---------------------------------------------------------------------------

def test_dry_run_ok_when_shop_endpoint_returns_200():
    transport = FakeTransport()
    transport.set_response("GET", "shop.json", 200, {"shop": {"name": "Acme"}})
    publisher = ShopifyPublisher(store="acme.myshopify.com", token="tok_123", transport=transport)
    report = publisher.dry_run({"body_html": "<p>hi</p>", "assets": [{"a": 1}]})
    assert report["ok"] is True
    assert report["store"] == "acme.myshopify.com"
    assert report["asset_count"] == 1
    assert len(transport.calls) == 1
    assert transport.calls[0]["method"] == "GET"
    assert transport.calls[0]["headers"]["X-Shopify-Access-Token"] == "tok_123"


def test_dry_run_not_ok_when_shop_endpoint_errors():
    transport = FakeTransport()
    transport.set_response("GET", "shop.json", 401, {"errors": "Unauthorized"})
    publisher = ShopifyPublisher(store="acme.myshopify.com", token="bad-token", transport=transport)
    report = publisher.dry_run({"body_html": "", "assets": []})
    assert report["ok"] is False


# ---------------------------------------------------------------------------
# publish -- payload shape, unpublished default
# ---------------------------------------------------------------------------

def test_publish_creates_page_unpublished_by_default():
    transport = FakeTransport()
    transport.set_response(
        "POST", "pages.json", 201,
        {"page": {"id": 999, "handle": "acme-article"}},
    )
    publisher = ShopifyPublisher(store="acme.myshopify.com", token="tok_123", transport=transport)
    result = publisher.publish({"title": "My Page", "body_html": "<p>hi</p>"}, unpublished=True)

    assert result["id"] == 999
    assert result["url"] == "https://acme.myshopify.com/pages/acme-article"
    call = transport.calls[0]
    sent = json.loads(call["body"])
    assert sent["page"]["title"] == "My Page"
    assert sent["page"]["body_html"] == "<p>hi</p>"
    assert sent["page"]["published"] is False


def test_publish_live_sets_published_true():
    transport = FakeTransport()
    transport.set_response("POST", "pages.json", 201, {"page": {"id": 1, "handle": "h"}})
    publisher = ShopifyPublisher(store="acme.myshopify.com", token="tok", transport=transport)
    publisher.publish({"title": "t", "body_html": "b"}, unpublished=False)
    sent = json.loads(transport.calls[0]["body"])
    assert sent["page"]["published"] is True


def test_publish_raises_on_non_2xx_response():
    transport = FakeTransport()
    transport.set_response("POST", "pages.json", 422, {"errors": {"title": ["can't be blank"]}})
    publisher = ShopifyPublisher(store="acme.myshopify.com", token="tok", transport=transport)
    with pytest.raises(RuntimeError):
        publisher.publish({"title": "", "body_html": "b"})


# ---------------------------------------------------------------------------
# upload_assets + src rewriting
# ---------------------------------------------------------------------------

def test_upload_assets_returns_url_by_local_path():
    transport = FakeTransport()
    transport.set_response("POST", "files.json", 201, {"file": {"url": "https://cdn.shopify.com/x/a.jpg"}})
    publisher = ShopifyPublisher(store="acme.myshopify.com", token="tok", transport=transport)
    mapping = publisher.upload_assets([
        {"local_path": "assets/a.jpg", "cdn_filename": "pk-article-01-a.jpg", "alt": "hero", "bytes": b"\xff\xd8"}
    ])
    assert mapping == {"assets/a.jpg": "https://cdn.shopify.com/x/a.jpg"}


def test_rewrite_asset_srcs_replaces_known_paths_and_leaves_unknown_alone():
    html = '<img src="assets/a.jpg"><img src="assets/unmapped.jpg">'
    out = rewrite_asset_srcs(html, {"assets/a.jpg": "https://cdn.shopify.com/a.jpg"})
    assert 'src="https://cdn.shopify.com/a.jpg"' in out
    assert 'src="assets/unmapped.jpg"' in out  # unmapped path left as-is


def test_rewrite_asset_srcs_rewrites_srcset_entries_and_keeps_descriptors():
    html = (
        '<picture>'
        '<source type="image/webp" srcset="assets/a-480.webp 480w, assets/a-800.webp 800w">'
        '<img src="assets/a-800.jpg" srcset="assets/a-480.jpg 480w, assets/a-800.jpg 800w">'
        '</picture>'
    )
    mapping = {
        "assets/a-480.webp": "https://cdn.shopify.com/a-480.webp",
        "assets/a-800.webp": "https://cdn.shopify.com/a-800.webp",
        "assets/a-480.jpg": "https://cdn.shopify.com/a-480.jpg",
        "assets/a-800.jpg": "https://cdn.shopify.com/a-800.jpg",
    }
    out = rewrite_asset_srcs(html, mapping)
    assert 'src="https://cdn.shopify.com/a-800.jpg"' in out
    assert 'srcset="https://cdn.shopify.com/a-480.webp 480w, https://cdn.shopify.com/a-800.webp 800w"' in out
    assert 'srcset="https://cdn.shopify.com/a-480.jpg 480w, https://cdn.shopify.com/a-800.jpg 800w"' in out
    assert "assets/" not in out


# ---------------------------------------------------------------------------
# verify_cache -- the storefront cache-epoch trap, 8 pulls
# ---------------------------------------------------------------------------

def test_verify_cache_counts_hits_across_pulls():
    bodies = [b"old body", b"old body", b"NEW-MARKER body", b"NEW-MARKER body"]
    def fetch(url):
        return bodies.pop(0)

    publisher = ShopifyPublisher(store="acme.myshopify.com", token="tok", transport=FakeTransport())
    hits, pulls = publisher.verify_cache(
        "https://acme.com/pages/x", marker="NEW-MARKER", pulls=4, delay_s=0, sleep=lambda s: None, fetch=fetch
    )
    assert (hits, pulls) == (2, 4)


# ---------------------------------------------------------------------------
# ExportPublisher
# ---------------------------------------------------------------------------

def test_export_publisher_writes_a_self_contained_folder(tmp_path):
    out_dir = tmp_path / "export"
    publisher = ExportPublisher(out_dir=out_dir)
    manifest = publisher.upload_assets([
        {"local_path": "assets/a.jpg", "cdn_filename": "pk-article-01-a.jpg", "alt": "hero", "bytes": b"\xff\xd8"}
    ])
    assert manifest == {"assets/a.jpg": "assets/pk-article-01-a.jpg"}
    assert (out_dir / "assets" / "pk-article-01-a.jpg").read_bytes() == b"\xff\xd8"

    result = publisher.publish({"title": "My Page", "body_html": "<style>x</style><p>hi</p>"})
    assert result["id"] is None
    assert (out_dir / "shopify-body.html").exists()
    assert (out_dir / "index.html").exists()
    readme = (out_dir / "README.md").read_text()
    assert "manual upload" in readme.lower()
    assert "ship" in readme.lower()


def test_export_publisher_dry_run_always_ok(tmp_path):
    publisher = ExportPublisher(out_dir=tmp_path / "export")
    report = publisher.dry_run({"body_html": "x", "assets": []})
    assert report["ok"] is True


# ---------------------------------------------------------------------------
# Cycle 34: SHOPIFY_STORE host allowlist + response size cap helpers
# ---------------------------------------------------------------------------

def test_normalize_shopify_store_accepts_bare_and_https_host():
    assert normalize_shopify_store("acme.myshopify.com") == "acme.myshopify.com"
    assert normalize_shopify_store("https://Acme.myshopify.com/admin") == "acme.myshopify.com"


@pytest.mark.parametrize(
    "bad",
    [
        "evil.example",
        "http://acme.myshopify.com",
        "acme.myshopify.com:8443",
        "user@acme.myshopify.com",
        "169.254.169.254",
        "",
        "https://evil.example",
    ],
)
def test_normalize_shopify_store_rejects_non_myshopify_hosts(bad):
    with pytest.raises(ValueError):
        normalize_shopify_store(bad)


def test_publisher_init_rejects_non_myshopify_store():
    with pytest.raises(ValueError):
        ShopifyPublisher(store="evil.example", token="tok", transport=FakeTransport())


def test_publisher_init_normalizes_store_url():
    publisher = ShopifyPublisher(
        store="https://Acme.myshopify.com/admin", token="tok", transport=FakeTransport()
    )
    assert publisher.store == "acme.myshopify.com"

