"""Training wheels (2026-10-05): with publish_locked, nothing goes live."""
import pytest

from harness import cli
from harness.publishers.shopify import ShopifyCredentialsMissing, ShopifyPublisher


class _Tenant:
    def __init__(self, **kv):
        self.kv = kv

    def get(self, key, default=None):
        return self.kv.get(key, default)


@pytest.fixture
def creds(monkeypatch):
    monkeypatch.setenv("SHOPIFY_STORE", "example.myshopify.com")
    monkeypatch.setenv("SHOPIFY_TOKEN", "test-token")


def test_locked_tenant_gets_the_locked_publisher(creds, tmp_path):
    p = cli._make_publisher(_Tenant(publisher="shopify", publish_locked=True), export_dir=tmp_path)
    assert isinstance(p, cli.LockedShopifyPublisher)


def test_unlocked_tenant_gets_the_plain_publisher(creds, tmp_path):
    p = cli._make_publisher(_Tenant(publisher="shopify"), export_dir=tmp_path)
    assert type(p) is ShopifyPublisher


@pytest.mark.parametrize("call", [
    lambda p: p.publish({"title": "x"}, unpublished=False),
    lambda p: p.update_page(1, {"title": "x"}, unpublished=False),
    lambda p: p.create_redirect("/a", "/b"),
])
def test_going_live_raises_before_any_network_call(creds, call):
    with pytest.raises(cli.PublishLocked) as e:
        call(cli.LockedShopifyPublisher())
    assert isinstance(e.value, ShopifyCredentialsMissing)  # every publish path prints it, exits 1
    assert "publish_locked" in str(e.value)


def test_hidden_draft_still_goes_through(creds, monkeypatch):
    seen = {}
    monkeypatch.setattr(ShopifyPublisher, "publish", lambda self, page, *, unpublished=True: seen.setdefault("u", unpublished) or {"id": 1})
    cli.LockedShopifyPublisher().publish({"title": "x"}, unpublished=True)
    assert seen["u"] is True


def test_peak_tenant_is_locked_and_auto_publish_is_off():
    import yaml
    from pathlib import Path
    cfg = yaml.safe_load(Path("tenants/peak-saunas/tenant.yaml").read_text())
    assert cfg["publish_locked"] is True
    assert cfg["abtest"]["auto_publish"] is False
