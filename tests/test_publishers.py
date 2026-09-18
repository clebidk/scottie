"""Cycle 20: the publisher adapters (harness/publishers/). The Shopify
adapter is exercised entirely through a fake transport -- no real HTTP call
is made anywhere in this file, matching the rest of this test suite."""
import json
import re

import pytest

from harness.publishers.export import ExportPublisher
from harness.publishers.shopify import (
    ShopifyCredentialsMissing,
    ShopifyPublisher,
    normalize_shopify_store,
    rewrite_asset_srcs,
)


class FakeTransport:
    """Records every call and returns canned (status, json_bytes) responses,
    keyed loosely by (method, path suffix) -- one queue per key. A caller
    that hits the same URL more than once (GraphQL calls are all POST
    .../graphql.json) gets its responses back in registration order; a key
    with only one registered response keeps returning that same response
    for every further call (the single-call-per-assertion style everywhere
    else in this file, and the "never becomes ready" polling tests below)."""

    def __init__(self):
        self.calls = []
        self.responses = {}  # (method, path_suffix) -> [(status, json_bytes), ...]

    def set_response(self, method, path_suffix, status, body):
        self.responses.setdefault((method, path_suffix), []).append(
            (status, json.dumps(body).encode("utf-8"))
        )

    def __call__(self, method, url, *, headers, body=None):
        self.calls.append({"method": method, "url": url, "headers": headers, "body": body})
        for (m, suffix), queue in self.responses.items():
            if m == method and url.endswith(suffix):
                return queue.pop(0) if len(queue) > 1 else queue[0]
        raise AssertionError(f"no fake response registered for {method} {url}")


def _multipart_field_names(body, boundary):
    """[field names in order] for a multipart/form-data body built by
    ShopifyPublisher._build_multipart_body -- used to assert `file` is last."""
    names = []
    for part in body.split(f"--{boundary}".encode()):
        header_end = part.find(b"\r\n\r\n")
        if header_end <= 0:
            continue
        m = re.search(r'name="([^"]+)"', part[:header_end].decode(errors="ignore"))
        if m:
            names.append(m.group(1))
    return names


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
    assert transport.calls == []


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
# create_redirect
# ---------------------------------------------------------------------------

def test_create_redirect_posts_path_and_target():
    transport = FakeTransport()
    transport.set_response(
        "POST", "redirects.json", 201,
        {"redirect": {"id": 55, "path": "/listicle-test-1", "target": "/pages/listicle-test-1"}},
    )
    publisher = ShopifyPublisher(store="acme.myshopify.com", token="tok", transport=transport)
    result = publisher.create_redirect("/listicle-test-1", "/pages/listicle-test-1")
    assert result == {"id": 55, "path": "/listicle-test-1", "target": "/pages/listicle-test-1"}
    sent = json.loads(transport.calls[0]["body"])
    assert sent == {"redirect": {"path": "/listicle-test-1", "target": "/pages/listicle-test-1"}}


def test_create_redirect_rejects_paths_without_leading_slash():
    publisher = ShopifyPublisher(store="acme.myshopify.com", token="tok", transport=FakeTransport())
    with pytest.raises(ValueError):
        publisher.create_redirect("listicle-test-1", "/pages/listicle-test-1")
    with pytest.raises(ValueError):
        publisher.create_redirect("/listicle-test-1", "pages/listicle-test-1")


def test_create_redirect_updates_existing_redirect_on_422_already_taken():
    transport = FakeTransport()
    transport.set_response(
        "POST", "redirects.json", 422,
        {"errors": {"path": ["has already been taken"]}},
    )
    transport.set_response(
        "GET", "redirects.json?path=%2Flisticle-test-1", 200,
        {"redirects": [{"id": 77, "path": "/listicle-test-1", "target": "/pages/old-handle"}]},
    )
    transport.set_response(
        "PUT", "redirects/77.json", 200,
        {"redirect": {"id": 77, "path": "/listicle-test-1", "target": "/pages/listicle-test-1"}},
    )
    publisher = ShopifyPublisher(store="acme.myshopify.com", token="tok", transport=transport)
    result = publisher.create_redirect("/listicle-test-1", "/pages/listicle-test-1")
    assert result["target"] == "/pages/listicle-test-1"
    methods = [c["method"] for c in transport.calls]
    assert methods == ["POST", "GET", "PUT"]
    put_sent = json.loads(transport.calls[-1]["body"])
    assert put_sent == {
        "redirect": {"id": 77, "path": "/listicle-test-1", "target": "/pages/listicle-test-1"}
    }


def test_create_redirect_raises_on_422_when_no_existing_redirect_found():
    transport = FakeTransport()
    transport.set_response(
        "POST", "redirects.json", 422,
        {"errors": {"path": ["has already been taken"]}},
    )
    transport.set_response("GET", "redirects.json?path=%2Flisticle-test-1", 200, {"redirects": []})
    publisher = ShopifyPublisher(store="acme.myshopify.com", token="tok", transport=transport)
    with pytest.raises(RuntimeError):
        publisher.create_redirect("/listicle-test-1", "/pages/listicle-test-1")


def test_create_redirect_raises_on_other_non_2xx():
    transport = FakeTransport()
    transport.set_response("POST", "redirects.json", 500, {"errors": "server error"})
    publisher = ShopifyPublisher(store="acme.myshopify.com", token="tok", transport=transport)
    with pytest.raises(RuntimeError):
        publisher.create_redirect("/listicle-test-1", "/pages/listicle-test-1")


# ---------------------------------------------------------------------------
# upload_assets -- GraphQL staged-upload flow (Cycle 39)
#
# Shopify has no REST endpoint for file bytes; every upload_assets call now
# goes stagedUploadsCreate (GraphQL) -> multipart POST straight to the
# staged target (no Admin API host, no token) -> fileCreate (GraphQL) ->
# poll node(id) until fileStatus == READY. All three GraphQL calls hit the
# same POST .../graphql.json URL, so `transport`'s per-key response queue
# (see FakeTransport) is what lets a test give each call its own body.
# ---------------------------------------------------------------------------

def _staged_uploads_create_response(upload_path="upload-1", parameters=None):
    parameters = parameters or [
        {"name": "key", "value": "tmp/1/pk-article-01-a.jpg"},
        {"name": "policy", "value": "policy-abc"},
        {"name": "x-goog-signature", "value": "sig-abc"},
    ]
    return {
        "data": {
            "stagedUploadsCreate": {
                "stagedTargets": [{
                    "url": f"https://storage.googleapis.com/shopify-staged-uploads/{upload_path}",
                    "resourceUrl": f"https://storage.googleapis.com/shopify-staged-uploads/{upload_path}?done",
                    "parameters": parameters,
                }],
                "userErrors": [],
            }
        }
    }


def _file_create_response(file_id="gid://shopify/MediaImage/1", status="UPLOADED"):
    return {
        "data": {
            "fileCreate": {
                "files": [{"id": file_id, "fileStatus": status, "alt": "hero", "image": None}],
                "userErrors": [],
            }
        }
    }


def _node_status_response(status, url=None):
    image = {"url": url} if url else None
    return {"data": {"node": {"fileStatus": status, "image": image}}}


_ONE_ASSET_ITEM = {
    "local_path": "assets/a.jpg",
    "cdn_filename": "pk-article-01-a.jpg",
    "alt": "hero",
    "bytes": b"\xff\xd8\xff\xe0fake-jpeg",
}


def test_upload_assets_staged_upload_happy_path():
    transport = FakeTransport()
    transport.set_response("POST", "graphql.json", 200, _staged_uploads_create_response())
    transport.set_response("POST", "graphql.json", 200, _file_create_response(status="UPLOADED"))
    transport.set_response("POST", "graphql.json", 200, _node_status_response("PROCESSING"))
    transport.set_response(
        "POST", "graphql.json", 200,
        _node_status_response("READY", url="https://cdn.shopify.com/files/a.jpg"),
    )
    upload_transport = FakeTransport()
    upload_transport.set_response("POST", "shopify-staged-uploads/upload-1", 201, {})

    sleeps = []
    publisher = ShopifyPublisher(
        store="example.myshopify.com", token="tok",
        transport=transport, upload_transport=upload_transport,
        sleep=lambda s: sleeps.append(s),
    )
    mapping = publisher.upload_assets([_ONE_ASSET_ITEM])

    assert mapping == {"assets/a.jpg": "https://cdn.shopify.com/files/a.jpg"}
    assert sleeps == [1]  # one sleep, between the PROCESSING and READY polls

    graphql_calls = transport.calls
    assert len(graphql_calls) == 4
    assert all(c["headers"]["X-Shopify-Access-Token"] == "tok" for c in graphql_calls)

    staged_sent = json.loads(graphql_calls[0]["body"])
    staged_input = staged_sent["variables"]["input"][0]
    assert staged_input["filename"] == "pk-article-01-a.jpg"
    assert staged_input["mimeType"] == "image/jpeg"
    assert staged_input["fileSize"] == str(len(_ONE_ASSET_ITEM["bytes"]))

    file_create_sent = json.loads(graphql_calls[1]["body"])
    assert file_create_sent["variables"]["files"][0]["originalSource"] == (
        "https://storage.googleapis.com/shopify-staged-uploads/upload-1?done"
    )

    # The staged upload itself: no Shopify token, `file` field last.
    upload_call = upload_transport.calls[0]
    assert "X-Shopify-Access-Token" not in upload_call["headers"]
    boundary = upload_call["headers"]["Content-Type"].split("boundary=")[1]
    assert _multipart_field_names(upload_call["body"], boundary) == [
        "key", "policy", "x-goog-signature", "file",
    ]


def test_upload_assets_raises_on_staged_uploads_create_user_errors():
    transport = FakeTransport()
    transport.set_response("POST", "graphql.json", 200, {
        "data": {"stagedUploadsCreate": {
            "stagedTargets": [],
            "userErrors": [{"field": ["input", "0", "filename"], "message": "can't be blank"}],
        }}
    })
    publisher = ShopifyPublisher(
        store="example.myshopify.com", token="tok", transport=transport, upload_transport=FakeTransport(),
    )
    with pytest.raises(RuntimeError):
        publisher.upload_assets([_ONE_ASSET_ITEM])


def test_upload_assets_raises_on_file_create_user_errors():
    transport = FakeTransport()
    transport.set_response("POST", "graphql.json", 200, _staged_uploads_create_response())
    transport.set_response("POST", "graphql.json", 200, {
        "data": {"fileCreate": {
            "files": [],
            "userErrors": [{"field": ["files", "0", "originalSource"], "message": "invalid resource"}],
        }}
    })
    upload_transport = FakeTransport()
    upload_transport.set_response("POST", "shopify-staged-uploads/upload-1", 201, {})
    publisher = ShopifyPublisher(
        store="example.myshopify.com", token="tok", transport=transport, upload_transport=upload_transport,
    )
    with pytest.raises(RuntimeError):
        publisher.upload_assets([_ONE_ASSET_ITEM])


def test_upload_assets_raises_when_poll_reports_failed():
    transport = FakeTransport()
    transport.set_response("POST", "graphql.json", 200, _staged_uploads_create_response())
    transport.set_response("POST", "graphql.json", 200, _file_create_response(status="UPLOADED"))
    transport.set_response("POST", "graphql.json", 200, _node_status_response("FAILED"))
    upload_transport = FakeTransport()
    upload_transport.set_response("POST", "shopify-staged-uploads/upload-1", 201, {})
    publisher = ShopifyPublisher(
        store="example.myshopify.com", token="tok", transport=transport, upload_transport=upload_transport,
        sleep=lambda s: None,
    )
    with pytest.raises(RuntimeError):
        publisher.upload_assets([_ONE_ASSET_ITEM])


def test_upload_assets_raises_when_never_ready_within_max_polls():
    transport = FakeTransport()
    transport.set_response("POST", "graphql.json", 200, _staged_uploads_create_response())
    transport.set_response("POST", "graphql.json", 200, _file_create_response(status="UPLOADED"))
    transport.set_response("POST", "graphql.json", 200, _node_status_response("PROCESSING"))
    upload_transport = FakeTransport()
    upload_transport.set_response("POST", "shopify-staged-uploads/upload-1", 201, {})

    sleeps = []
    publisher = ShopifyPublisher(
        store="example.myshopify.com", token="tok", transport=transport, upload_transport=upload_transport,
        sleep=lambda s: sleeps.append(s),
    )
    with pytest.raises(RuntimeError):
        publisher.upload_assets([_ONE_ASSET_ITEM])
    assert len(sleeps) == 29  # 30 polls total, sleeping between each but not after the last


def test_upload_assets_raises_on_non_2xx_staged_upload():
    transport = FakeTransport()
    transport.set_response("POST", "graphql.json", 200, _staged_uploads_create_response())
    upload_transport = FakeTransport()
    upload_transport.set_response("POST", "shopify-staged-uploads/upload-1", 403, {"error": "Forbidden"})
    publisher = ShopifyPublisher(
        store="example.myshopify.com", token="tok", transport=transport, upload_transport=upload_transport,
    )
    with pytest.raises(RuntimeError):
        publisher.upload_assets([_ONE_ASSET_ITEM])


def test_mime_type_for_derives_from_extension():
    assert ShopifyPublisher._mime_type_for({"cdn_filename": "a.webp"}) == "image/webp"
    assert ShopifyPublisher._mime_type_for({"cdn_filename": "a.jpg"}) == "image/jpeg"
    assert ShopifyPublisher._mime_type_for({"cdn_filename": "a.png"}) == "image/png"
    assert ShopifyPublisher._mime_type_for({"cdn_filename": "a.unknownext"}) == "image/jpeg"


# ---------------------------------------------------------------------------
# src/srcset rewriting
# ---------------------------------------------------------------------------

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
