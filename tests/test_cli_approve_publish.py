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


from harness import cli, runstate


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

def _publish_args(run_dir, *, page="article", live=False, dry_run=False):
    return argparse.Namespace(run_dir=str(run_dir), page=page, live=live, dry_run=dry_run, tenant=None)


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
