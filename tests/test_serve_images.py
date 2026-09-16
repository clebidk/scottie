"""Cycle 36: harness/serve.py's /images human-review site -- image library
index, per-product grid, save, and thumbnail routes. Flask test client, no
network (tests/conftest.py's autouse _no_network fixture blocks sockets)."""
import base64
import json
import re

import pytest

from harness import serve
from tests.support import TENANT

REVIEWER = "caleb@peaksaunas.com"
PASSWORD = "correct-horse-battery-staple"
FUJI_SLUG = (
    "peak-saunas-fuji-2-person-indoor-near-zero-emf-full-spectrum-infrared-sauna-with-medical-grade-red-light-therapy"
)


def _basic_auth_header(username, password):
    token = base64.b64encode(f"{username}:{password}".encode()).decode()
    return {"Authorization": f"Basic {token}"}


def _auth():
    return _basic_auth_header(REVIEWER, PASSWORD)


@pytest.fixture
def review_env(monkeypatch):
    monkeypatch.setenv("REVIEW_PASSWORD", PASSWORD)
    monkeypatch.delenv("REVIEW_TRUST_CF_ACCESS", raising=False)


@pytest.fixture
def isolated_images_paths(tmp_path, monkeypatch):
    """The /images save and thumb routes write into tenant.brand_dir
    (asset-review.json) and tenant.runs_dir (the thumbnail cache) --
    redirect both into tmp_path so these tests never touch the real
    tenants/peak-saunas/brand or runs/ trees (same pattern as
    tests/test_serve.py's isolated_tenant_paths). claims_dir stays real --
    these routes need real product/asset data to render anything."""
    brand_dir = tmp_path / "brand"
    runs_dir = tmp_path / "runs"
    brand_dir.mkdir()
    runs_dir.mkdir()
    monkeypatch.setattr(type(TENANT), "brand_dir", property(lambda self: brand_dir))
    monkeypatch.setattr(type(TENANT), "runs_dir", property(lambda self: runs_dir))
    return tmp_path


@pytest.fixture
def app_client(review_env, isolated_images_paths):
    app = serve.build_app(TENANT)
    app.testing = True
    return app.test_client()


def _first_asset_id(html_text):
    m = re.search(r'name="alt:(asset-[^"]+)"', html_text)
    assert m, "expected at least one asset card on the page"
    return m.group(1)


def test_image_library_index_lists_active_products(app_client):
    resp = app_client.get("/images", headers=_auth())
    assert resp.status_code == 200
    assert b"Fuji" in resp.data


def test_image_library_requires_auth(app_client):
    resp = app_client.get("/images")
    assert resp.status_code == 401


def test_image_library_product_page_renders_cards(app_client):
    resp = app_client.get(f"/images/{FUJI_SLUG}", headers=_auth())
    assert resp.status_code == 200
    assert b"image-card" in resp.data
    _first_asset_id(resp.data.decode())  # raises if no card rendered


def test_image_library_product_unknown_slug_is_404(app_client):
    resp = app_client.get("/images/not-a-real-product", headers=_auth())
    assert resp.status_code == 404


def test_image_library_source_filter_shopify_only(app_client):
    resp = app_client.get(f"/images/{FUJI_SLUG}?source=shopify", headers=_auth())
    assert resp.status_code == 200
    assert b"drive /" not in resp.data


def test_image_library_save_writes_override_with_reviewer_email(app_client, isolated_images_paths):
    page = app_client.get(f"/images/{FUJI_SLUG}", headers=_auth())
    asset_id = _first_asset_id(page.data.decode())

    resp = app_client.post(
        f"/images/{FUJI_SLUG}/save",
        data={
            "ids": asset_id,
            f"alt:{asset_id}": "reviewer alt text",
            f"status:{asset_id}": "keep",
            f"note:{asset_id}": "",
        },
        headers=_auth(),
    )
    assert resp.status_code == 302
    assert "saved=1" in resp.headers["Location"]

    data = json.loads((isolated_images_paths / "brand" / "asset-review.json").read_text())
    entry = data["assets"][asset_id]
    assert entry["alt"] == "reviewer alt text"
    assert entry["by"] == REVIEWER
    assert entry["excluded"] is False


def test_image_library_save_excludes_asset(app_client, isolated_images_paths):
    page = app_client.get(f"/images/{FUJI_SLUG}", headers=_auth())
    asset_id = _first_asset_id(page.data.decode())

    app_client.post(
        f"/images/{FUJI_SLUG}/save",
        data={"ids": asset_id, f"alt:{asset_id}": "", f"status:{asset_id}": "exclude", f"note:{asset_id}": "blurry"},
        headers=_auth(),
    )
    data = json.loads((isolated_images_paths / "brand" / "asset-review.json").read_text())
    entry = data["assets"][asset_id]
    assert entry["excluded"] is True
    assert entry["note"] == "blurry"


def test_image_library_save_ignores_ids_not_on_the_page(app_client, isolated_images_paths):
    resp = app_client.post(
        f"/images/{FUJI_SLUG}/save",
        data={"ids": "asset-does-not-exist-1", "alt:asset-does-not-exist-1": "should not save"},
        headers=_auth(),
    )
    assert resp.status_code == 302
    assert "saved=0" in resp.headers["Location"]
    review_path = isolated_images_paths / "brand" / "asset-review.json"
    assert json.loads(review_path.read_text())["assets"] == {}


def test_image_thumb_unknown_id_is_404(app_client):
    resp = app_client.get("/images/thumb/asset-not-real-1", headers=_auth())
    assert resp.status_code == 404


def test_image_thumb_fetch_failure_returns_placeholder_svg(app_client, monkeypatch):
    page = app_client.get(f"/images/{FUJI_SLUG}", headers=_auth())
    asset_id = _first_asset_id(page.data.decode())

    def _raise(*args, **kwargs):
        raise RuntimeError("boom")

    monkeypatch.setattr(serve.render_mod, "download_asset", _raise)
    resp = app_client.get(f"/images/thumb/{asset_id}", headers=_auth())
    assert resp.status_code == 200
    assert resp.mimetype == "image/svg+xml"
    assert b"could not load" in resp.data


def test_image_thumb_download_returning_none_is_placeholder_not_error(app_client, monkeypatch):
    page = app_client.get(f"/images/{FUJI_SLUG}", headers=_auth())
    asset_id = _first_asset_id(page.data.decode())

    monkeypatch.setattr(serve.render_mod, "download_asset", lambda *a, **k: None)
    resp = app_client.get(f"/images/thumb/{asset_id}", headers=_auth())
    assert resp.status_code == 200
    assert resp.mimetype == "image/svg+xml"
