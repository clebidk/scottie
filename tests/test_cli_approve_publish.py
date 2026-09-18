"""Cycle 20: `harness approve` / `reject` / `packet` / `publish` end to end
through the real cli.cmd_* functions -- state.json and packet.json on disk,
a fake tenant standing in for tenant.yaml (so these tests don't depend on
the real peak-saunas reviewers list), and the export publisher for the
"publish actually succeeds" path so no real Shopify credential is ever
needed. The refusal-path tests exercise the real ShopifyPublisher with no
SHOPIFY_STORE/SHOPIFY_TOKEN set, proving the no-credentials refusal is
real, not mocked away."""
import argparse
import json
import os


from harness import cli, runstate
from harness.publishers.shopify import ShopifyPublisher
from tests.test_publishers import FakeTransport


class FakeTenant:
    def __init__(self, tmp_path, *, publisher="export", reviewers=None, name="acme"):
        self.name = name
        self.display_name = "Acme"
        self._publisher = publisher
        self._reviewers = reviewers if reviewers is not None else [
            {"name": "Michael", "email": "michael@acme.com", "role": "primary"},
        ]
        self.evals_path = tmp_path / "tenant" / "evals" / "scores.jsonl"
        self.out_dir = tmp_path / "tenant" / "out"

    def get(self, key, default=None):
        if key == "reviewers":
            return self._reviewers
        if key == "publisher":
            return self._publisher
        if key == "site_host":
            return "acme.example"
        if key == "notifications":
            return {"slack": False, "email": []}
        return default

    def load_env(self):
        """No-op by default -- cmd_publish calls this unconditionally
        (Cycle 38); tests that care about real .env loading replace this
        with a real `load_dotenv` call on the instance."""
        return False


def _make_run(tmp_path, *, pages=("article",)):
    run_dir = tmp_path / "20260910-1200-test-run"
    for page in pages:
        cart_dir = run_dir / page
        (cart_dir / "assets").mkdir(parents=True)
        (cart_dir / "assets" / "hero.jpg").write_bytes(b"\xff\xd8\xff\xe0fake-jpeg")
        (cart_dir / "index.html").write_text(
            f'<html><body><h1>{page} headline</h1>'
            f'<img src="assets/hero.jpg" alt="{page} hero"></body></html>'
        )
        (cart_dir / "page.json").write_text(json.dumps({"headline": f"{page} headline"}))
    runstate.init_state(run_dir, pages=list(pages))
    runstate.init_packet(run_dir)
    return run_dir


def _patch_tenant(monkeypatch, tenant):
    monkeypatch.setattr(cli, "_resolve_tenant_for_run", lambda args: tenant)
    monkeypatch.setattr(cli.notify, "notify_approved", lambda *a, **k: None)
    monkeypatch.setattr(cli.notify, "notify_published", lambda *a, **k: None)


# ---------------------------------------------------------------------------
# approve / reject
# ---------------------------------------------------------------------------

def test_approve_refuses_unknown_reviewer_no_traceback(tmp_path, monkeypatch, capsys):
    run_dir = _make_run(tmp_path)
    tenant = FakeTenant(tmp_path)
    _patch_tenant(monkeypatch, tenant)
    args = argparse.Namespace(run_dir=str(run_dir), by="stranger@example.com", pages=None, note=None, tenant=None)
    exit_code = cli.cmd_approve(args)
    assert exit_code == 1
    err = capsys.readouterr().err
    assert "not a listed reviewer" in err


def test_approve_known_reviewer_succeeds(tmp_path, monkeypatch, capsys):
    run_dir = _make_run(tmp_path)
    tenant = FakeTenant(tmp_path)
    _patch_tenant(monkeypatch, tenant)
    args = argparse.Namespace(run_dir=str(run_dir), by="michael@acme.com", pages="article", note="lgtm", tenant=None)
    exit_code = cli.cmd_approve(args)
    assert exit_code == 0
    assert runstate.load_state(run_dir)["pages"]["article"] == "approved"
    assert "Approved" in capsys.readouterr().out


def test_reject_requires_a_listed_reviewer(tmp_path, monkeypatch):
    run_dir = _make_run(tmp_path)
    tenant = FakeTenant(tmp_path)
    _patch_tenant(monkeypatch, tenant)
    args = argparse.Namespace(run_dir=str(run_dir), by="stranger@example.com", note="no", tenant=None)
    assert cli.cmd_reject(args) == 1
    assert cli.cmd_reject(
        argparse.Namespace(run_dir=str(run_dir), by="michael@acme.com", note="wrong angle", tenant=None)
    ) == 0
    assert runstate.load_state(run_dir)["state"] == "rejected"


# ---------------------------------------------------------------------------
# packet
# ---------------------------------------------------------------------------

def test_packet_stamp_ship(tmp_path):
    run_dir = _make_run(tmp_path)
    args = argparse.Namespace(run_dir=str(run_dir), stamp="ship", by="caleb@acme.com", note=None, tenant=None)
    assert cli.cmd_packet(args) == 0
    assert runstate.load_packet(run_dir)["stamp"] == "ship"


def test_packet_refuses_a_directory_that_is_not_a_run(tmp_path, capsys):
    not_a_run = tmp_path / "not-a-run"
    not_a_run.mkdir()
    args = argparse.Namespace(run_dir=str(not_a_run), stamp="ship", by="caleb@acme.com", note=None, tenant=None)
    assert cli.cmd_packet(args) == 1
    assert "no state.json" in capsys.readouterr().err


# ---------------------------------------------------------------------------
# publish -- refusals
# ---------------------------------------------------------------------------

def _publish_args(run_dir, *, page="article", live=False, dry_run=False, handle=None, redirect_from=None):
    return argparse.Namespace(
        run_dir=str(run_dir), page=page, live=live, dry_run=dry_run,
        handle=handle, redirect_from=redirect_from, tenant=None,
    )


def test_publish_refuses_without_approval(tmp_path, monkeypatch, capsys):
    run_dir = _make_run(tmp_path)
    tenant = FakeTenant(tmp_path, publisher="export")
    _patch_tenant(monkeypatch, tenant)
    exit_code = cli.cmd_publish(_publish_args(run_dir))
    assert exit_code == 1
    err = capsys.readouterr().err
    assert "not 'approved'" in err
    assert "harness approve" in err


def test_publish_refuses_without_ship_stamp(tmp_path, monkeypatch, capsys):
    run_dir = _make_run(tmp_path)
    tenant = FakeTenant(tmp_path, publisher="export")
    _patch_tenant(monkeypatch, tenant)
    runstate.approve(run_dir, tenant, by="michael@acme.com")
    exit_code = cli.cmd_publish(_publish_args(run_dir))
    assert exit_code == 1
    err = capsys.readouterr().err
    assert "packet stamp is" in err
    assert "harness packet" in err


def test_publish_refuses_without_shopify_credentials(tmp_path, monkeypatch, capsys):
    monkeypatch.delenv("SHOPIFY_STORE", raising=False)
    monkeypatch.delenv("SHOPIFY_TOKEN", raising=False)
    run_dir = _make_run(tmp_path)
    tenant = FakeTenant(tmp_path, publisher="shopify")
    _patch_tenant(monkeypatch, tenant)
    runstate.approve(run_dir, tenant, by="michael@acme.com")
    runstate.set_packet_stamp(run_dir, stamp="ship", by="michael@acme.com")
    exit_code = cli.cmd_publish(_publish_args(run_dir))
    assert exit_code == 1
    err = capsys.readouterr().err
    assert "SHOPIFY_STORE" in err and "SHOPIFY_TOKEN" in err
    assert "Traceback" not in err


def test_publish_dry_run_reports_missing_credentials_without_network(tmp_path, monkeypatch, capsys):
    monkeypatch.delenv("SHOPIFY_STORE", raising=False)
    monkeypatch.delenv("SHOPIFY_TOKEN", raising=False)

    def boom(*a, **k):
        raise AssertionError("dry-run must not make a network call with no credentials")

    monkeypatch.setattr("urllib.request.urlopen", boom)
    run_dir = _make_run(tmp_path)
    tenant = FakeTenant(tmp_path, publisher="shopify")
    _patch_tenant(monkeypatch, tenant)
    exit_code = cli.cmd_publish(_publish_args(run_dir, dry_run=True))
    assert exit_code == 1
    out = json.loads(capsys.readouterr().out)
    assert out["ok"] is False
    assert "SHOPIFY_STORE" in out["reason"]


# ---------------------------------------------------------------------------
# publish -- success path via the export adapter (no credentials needed)
# ---------------------------------------------------------------------------

def test_publish_succeeds_with_export_adapter_once_approved_and_stamped(tmp_path, monkeypatch, capsys):
    run_dir = _make_run(tmp_path)
    tenant = FakeTenant(tmp_path, publisher="export")
    _patch_tenant(monkeypatch, tenant)
    runstate.approve(run_dir, tenant, by="michael@acme.com")
    runstate.set_packet_stamp(run_dir, stamp="ship", by="michael@acme.com")

    exit_code = cli.cmd_publish(_publish_args(run_dir))
    assert exit_code == 0
    assert runstate.load_state(run_dir)["pages"]["article"] == "published"
    export_dir = run_dir / "article" / "export"
    assert (export_dir / "shopify-body.html").exists()
    assert (export_dir / "README.md").exists()


def test_publish_refuses_a_redo_stamped_packet(tmp_path, monkeypatch, capsys):
    run_dir = _make_run(tmp_path)
    tenant = FakeTenant(tmp_path, publisher="export")
    _patch_tenant(monkeypatch, tenant)
    runstate.approve(run_dir, tenant, by="michael@acme.com")
    runstate.set_packet_stamp(run_dir, stamp="redo", by="michael@acme.com")
    exit_code = cli.cmd_publish(_publish_args(run_dir))
    assert exit_code == 1
    assert "'redo'" in capsys.readouterr().err


# ---------------------------------------------------------------------------
# publish -- --handle and --redirect-from (Cycle 38), exercised through the
# real ShopifyPublisher with a fake transport (no network, matches
# tests/test_publishers.py's pattern).
# ---------------------------------------------------------------------------

def _shopify_ready_transport():
    """A fake transport pre-loaded for the whole GraphQL staged-upload flow
    (Cycle 39: see tests/test_publishers.py) plus the REST pages.json call --
    fileCreate reports READY immediately so no poll loop, and no injected
    sleep, is needed here; the poll loop itself is covered in
    tests/test_publishers.py. `upload_transport` is the same instance as
    `transport` (see _patch_shopify_publisher) since FakeTransport matches
    by URL suffix regardless of which injectable it's passed as."""
    transport = FakeTransport()
    transport.set_response("POST", "graphql.json", 200, {
        "data": {"stagedUploadsCreate": {
            "stagedTargets": [{
                "url": "https://storage.googleapis.com/shopify-staged-uploads/hero",
                "resourceUrl": "https://storage.googleapis.com/shopify-staged-uploads/hero?done",
                "parameters": [{"name": "key", "value": "tmp/hero.jpg"}],
            }],
            "userErrors": [],
        }}
    })
    transport.set_response("POST", "graphql.json", 200, {
        "data": {"fileCreate": {
            "files": [{
                "id": "gid://shopify/MediaImage/1",
                "fileStatus": "READY",
                "alt": "hero",
                "image": {"url": "https://cdn.shopify.com/x/hero.jpg"},
            }],
            "userErrors": [],
        }}
    })
    transport.set_response("POST", "shopify-staged-uploads/hero", 201, {})
    transport.set_response("POST", "pages.json", 201, {"page": {"id": 1, "handle": "listicle-test-1"}})
    return transport


def _patch_shopify_publisher(monkeypatch, transport):
    publisher = ShopifyPublisher(
        store="acme.myshopify.com", token="tok", transport=transport, upload_transport=transport,
    )
    monkeypatch.setattr(cli, "_make_publisher", lambda tenant, *, export_dir: publisher)
    # verify_cache's own 8-pulls behavior is covered in tests/test_publishers.py;
    # here it would otherwise hit the real network.
    monkeypatch.setattr(ShopifyPublisher, "verify_cache", lambda self, *a, **k: (8, 8))
    return publisher


def test_publish_refuses_invalid_handle(tmp_path, monkeypatch, capsys):
    run_dir = _make_run(tmp_path)
    tenant = FakeTenant(tmp_path, publisher="shopify")
    _patch_tenant(monkeypatch, tenant)
    exit_code = cli.cmd_publish(_publish_args(run_dir, handle="Bad Handle!"))
    assert exit_code == 1
    assert "invalid" in capsys.readouterr().err.lower()


def test_publish_forwards_handle_into_page_payload(tmp_path, monkeypatch, capsys):
    run_dir = _make_run(tmp_path)
    tenant = FakeTenant(tmp_path, publisher="shopify")
    _patch_tenant(monkeypatch, tenant)
    runstate.approve(run_dir, tenant, by="michael@acme.com")
    runstate.set_packet_stamp(run_dir, stamp="ship", by="michael@acme.com")
    transport = _shopify_ready_transport()
    _patch_shopify_publisher(monkeypatch, transport)

    exit_code = cli.cmd_publish(_publish_args(run_dir, handle="listicle-test-1"))
    assert exit_code == 0
    pages_call = next(c for c in transport.calls if c["url"].endswith("pages.json"))
    sent = json.loads(pages_call["body"])
    assert sent["page"]["handle"] == "listicle-test-1"


def test_publish_redirect_from_requires_live(tmp_path, monkeypatch, capsys):
    run_dir = _make_run(tmp_path)
    tenant = FakeTenant(tmp_path, publisher="shopify")
    _patch_tenant(monkeypatch, tenant)
    exit_code = cli.cmd_publish(_publish_args(run_dir, redirect_from="/listicle-test-1", live=False))
    assert exit_code == 1
    assert "--live" in capsys.readouterr().err


def test_publish_creates_redirect_after_live_publish(tmp_path, monkeypatch, capsys):
    run_dir = _make_run(tmp_path)
    tenant = FakeTenant(tmp_path, publisher="shopify")
    _patch_tenant(monkeypatch, tenant)
    runstate.approve(run_dir, tenant, by="michael@acme.com")
    runstate.set_packet_stamp(run_dir, stamp="ship", by="michael@acme.com")
    transport = _shopify_ready_transport()
    transport.set_response(
        "POST", "redirects.json", 201,
        {"redirect": {"id": 9, "path": "/listicle-test-1", "target": "/pages/listicle-test-1"}},
    )
    _patch_shopify_publisher(monkeypatch, transport)

    exit_code = cli.cmd_publish(
        _publish_args(run_dir, live=True, handle="listicle-test-1", redirect_from="/listicle-test-1")
    )
    assert exit_code == 0
    out = capsys.readouterr().out
    assert "Redirect /listicle-test-1 -> /pages/listicle-test-1" in out

    redirect_call = next(
        c for c in transport.calls if c["method"] == "POST" and c["url"].endswith("redirects.json")
    )
    sent = json.loads(redirect_call["body"])
    assert sent == {"redirect": {"path": "/listicle-test-1", "target": "/pages/listicle-test-1"}}

    history_note = runstate.load_state(run_dir)["history"][-1]["note"]
    assert "redirect_from=/listicle-test-1" in history_note
    assert "redirect_target=/pages/listicle-test-1" in history_note


def test_publish_redirect_idempotent_on_422_already_taken(tmp_path, monkeypatch, capsys):
    run_dir = _make_run(tmp_path)
    tenant = FakeTenant(tmp_path, publisher="shopify")
    _patch_tenant(monkeypatch, tenant)
    runstate.approve(run_dir, tenant, by="michael@acme.com")
    runstate.set_packet_stamp(run_dir, stamp="ship", by="michael@acme.com")
    transport = _shopify_ready_transport()
    transport.set_response("POST", "redirects.json", 422, {"errors": {"path": ["has already been taken"]}})
    transport.set_response(
        "GET", "redirects.json?path=%2Flisticle-test-1", 200,
        {"redirects": [{"id": 4, "path": "/listicle-test-1", "target": "/pages/old-handle"}]},
    )
    transport.set_response(
        "PUT", "redirects/4.json", 200,
        {"redirect": {"id": 4, "path": "/listicle-test-1", "target": "/pages/listicle-test-1"}},
    )
    _patch_shopify_publisher(monkeypatch, transport)

    exit_code = cli.cmd_publish(
        _publish_args(run_dir, live=True, handle="listicle-test-1", redirect_from="/listicle-test-1")
    )
    assert exit_code == 0
    methods = [c["method"] for c in transport.calls if "redirect" in c["url"]]
    assert methods == ["POST", "GET", "PUT"]


# ---------------------------------------------------------------------------
# publish -- cmd_publish must load the tenant's .env itself (Cycle 38)
# ---------------------------------------------------------------------------

def test_publish_loads_tenant_env_for_shopify_credentials(tmp_path, monkeypatch, capsys):
    """`_resolve_tenant_for_run` never calls `tenant.load_env()` (unlike
    cmd_run/cmd_serve), so a SHOPIFY_STORE/SHOPIFY_TOKEN that lives only in
    tenants/<t>/.env -- not the process environment -- was invisible to
    `harness publish` until cmd_publish called load_env() itself. This
    proves --dry-run succeeds off a real tenants/<t>/.env-style file with
    neither var set in os.environ beforehand."""
    saved_env = dict(os.environ)
    try:
        monkeypatch.delenv("SHOPIFY_STORE", raising=False)
        monkeypatch.delenv("SHOPIFY_TOKEN", raising=False)

        env_file = tmp_path / "tenant.env"
        env_file.write_text("SHOPIFY_STORE=example.myshopify.com\nSHOPIFY_TOKEN=tok_from_dotenv\n")

        tenant = FakeTenant(tmp_path, publisher="shopify")

        def _load_env():
            from dotenv import load_dotenv
            load_dotenv(env_file, override=False)
            return True

        tenant.load_env = _load_env
        _patch_tenant(monkeypatch, tenant)

        def fake_transport(method, url, *, headers, body=None):
            assert headers["X-Shopify-Access-Token"] == "tok_from_dotenv"
            return 200, b'{"shop": {"name": "Acme"}}'

        monkeypatch.setattr(ShopifyPublisher, "_urllib_transport", staticmethod(fake_transport))

        run_dir = _make_run(tmp_path)
        exit_code = cli.cmd_publish(_publish_args(run_dir, dry_run=True))
        out = json.loads(capsys.readouterr().out)
        assert exit_code == 0
        assert out["ok"] is True
        assert os.environ["SHOPIFY_STORE"] == "example.myshopify.com"
    finally:
        os.environ.clear()
        os.environ.update(saved_env)
