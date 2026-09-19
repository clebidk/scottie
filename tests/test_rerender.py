"""Cycle 45: `harness rerender <run-dir> --page <cartridge>`.

Re-renders one page's index.html from the run's OWN page.json + facts_pack
through the current template. There is no model call in this path -- the
writer already ran, and its output is the input -- which is how a template or
CSS fix reaches pages that are already published without paying for the copy
again. The page's approval state must survive that untouched, or a layout fix
would quietly send ten live pages back through review.
"""
import argparse
import json

import pytest

from harness import cli, render as render_mod, runstate
from tests.support import TENANT

# The listicle run's own fixtures -- this command exists for the listicle
# pages that are already live, so it is tested against the same page shape
# they have rather than a hand-rolled one.
from tests.test_listicle import AD_BRIEF, FACTS_PACK, _listicle_page


@pytest.fixture
def run_dir(tmp_path, monkeypatch):
    """A run directory shaped exactly as a real one: state.json, facts_pack,
    ad_brief, and one cartridge folder holding the writer's page.json."""
    run_dir = tmp_path / "20260919-031254-test-run-abcd"
    (run_dir / "listicle").mkdir(parents=True)
    (run_dir / "facts_pack.json").write_text(json.dumps(FACTS_PACK))
    (run_dir / "ad_brief.json").write_text(json.dumps(AD_BRIEF))
    (run_dir / "listicle" / "page.json").write_text(json.dumps(_listicle_page()))
    runstate.init_state(run_dir, pages=["listicle"])

    # download_asset is looked up on the module at call time, so replacing it
    # here exercises the real render_page/enforce_slot_plan/matcher path with
    # no network. The command itself is untouched -- it has no test seam and
    # does not need one.
    def fake_download(asset, dest_dir, **kwargs):
        from pathlib import Path

        dest_dir = Path(dest_dir)
        dest_dir.mkdir(parents=True, exist_ok=True)
        name = f"{asset['id']}-800.jpg"
        (dest_dir / name).write_bytes(b"fake-jpeg")
        return {
            "path": dest_dir / name,
            "width": 900, "height": 1200,       # a 3:4 portrait
            "variants": [{"width": 800, "jpg": f"assets/{name}", "webp": None}],
            "cutout": True,
        }

    monkeypatch.setattr(render_mod, "download_asset", fake_download)
    return run_dir


def _args(run_dir, **over):
    base = dict(run_dir=str(run_dir), page="listicle", note="", tenant=TENANT.name)
    base.update(over)
    return argparse.Namespace(**base)


def test_rerender_writes_index_and_review_html(run_dir):
    assert cli.cmd_rerender(_args(run_dir)) == 0
    index = run_dir / "listicle" / "index.html"
    assert index.exists()
    html = index.read_text()
    assert _listicle_page()["headline"] in html
    assert (run_dir / "listicle-review.html").exists()


def test_rerender_makes_no_model_call(run_dir, monkeypatch):
    """The whole point of the command. If anything in this path ever reaches
    for a client, this fails rather than quietly costing money per page."""
    def explode(*a, **kw):
        raise AssertionError("rerender must not build an API client")

    monkeypatch.setattr(cli, "make_client", explode)
    assert cli.cmd_rerender(_args(run_dir)) == 0


def test_rerender_puts_the_current_template_output_on_disk(run_dir):
    """Re-rendering picks up template changes: the emitted markup comes from
    the template as it is now, not from whatever index.html held before."""
    stale = run_dir / "listicle" / "index.html"
    stale.write_text("<html><body>the old layout</body></html>")
    cli.cmd_rerender(_args(run_dir))
    assert "the old layout" not in stale.read_text()
    # and the cycle 45 image contract is applied, from the real render path
    assert 'style="aspect-ratio:3 / 4"' in stale.read_text()


def test_rerender_leaves_the_approval_state_alone(run_dir):
    data = runstate.load_state(run_dir)
    data["state"] = "published"
    data["pages"]["listicle"] = "published"
    runstate.save_state(run_dir, data)

    cli.cmd_rerender(_args(run_dir, note="cycle 45 layout"))

    after = runstate.load_state(run_dir)
    assert after["state"] == "published"
    assert after["pages"]["listicle"] == "published"
    last = after["history"][-1]
    assert last["rerendered_at"]
    assert last["state"] == "published"        # the note reads in order
    assert "rerendered page=listicle" in last["note"]
    assert "cycle 45 layout" in last["note"]


def test_rerender_keeps_the_runs_own_dates(run_dir):
    """A page that has been live for weeks must not be re-dated to today by a
    layout fix."""
    assert runstate.run_started_date(run_dir) is not None
    started = runstate.run_started_date(run_dir)
    cli.cmd_rerender(_args(run_dir))
    html = (run_dir / "listicle" / "index.html").read_text()
    assert started in html


def test_rerender_refreshes_an_existing_shopify_body_only(run_dir):
    cli.cmd_rerender(_args(run_dir))
    assert not (run_dir / "listicle" / "shopify-body.html").exists()

    (run_dir / "listicle" / "shopify-body.html").write_text("stale export")
    cli.cmd_rerender(_args(run_dir))
    refreshed = (run_dir / "listicle" / "shopify-body.html").read_text()
    assert "stale export" not in refreshed
    assert (run_dir / "listicle" / "shopify-body.assets.json").exists()


def test_rerender_refuses_a_page_with_no_page_json(run_dir, capsys):
    (run_dir / "listicle" / "page.json").unlink()
    assert cli.cmd_rerender(_args(run_dir)) == 1
    assert "page.json" in capsys.readouterr().err


def test_rerender_refuses_a_run_with_no_facts_pack(run_dir, capsys):
    (run_dir / "facts_pack.json").unlink()
    assert cli.cmd_rerender(_args(run_dir)) == 1
    assert "facts_pack.json" in capsys.readouterr().err


def test_rerender_refuses_a_cartridge_this_run_never_produced(run_dir, capsys):
    assert cli.cmd_rerender(_args(run_dir, page="longform")) == 1
    assert "page.json" in capsys.readouterr().err


def test_mark_rerendered_refuses_a_page_the_run_does_not_have(run_dir):
    with pytest.raises(KeyError):
        runstate.mark_rerendered(run_dir, page="longform")


def test_cli_parser_wires_rerender():
    parser = cli.build_parser()
    args = parser.parse_args(["rerender", "out/run", "--page", "listicle"])
    assert args.func is cli.cmd_rerender
    assert args.page == "listicle"
