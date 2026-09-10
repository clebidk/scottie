"""The workflow runner must be the same pipeline `harness run` is.

A workflow YAML owns the ORDER of stages; harness/pipeline.py owns what each
stage does. These tests hold that line: the same input, seed, and canned model
responses must produce the same pages through either entry point.
"""
import json
from pathlib import Path

import pytest

from harness import cli, pipeline, workflows
from tests.conftest import json_response
from tests.test_cli_run import (
    AD_BRIEF_RESPONSE,
    _base_args,
    _patch_network,
)
from tests.conftest import FakeClient
from tests.support import TENANT
from tests.test_render import ARTICLE_PAGE, LONGFORM_PAGE, PRODUCT_PAGE_PAGE

RESPONSES = [
    json_response(AD_BRIEF_RESPONSE),
    json_response({}),  # semantic-match call, no mappings
    json_response(ARTICLE_PAGE),
    json_response(PRODUCT_PAGE_PAGE),
    json_response(LONGFORM_PAGE),
]


# ---------------------------------------------------------------------------
# The YAML files themselves
# ---------------------------------------------------------------------------

def test_every_workflow_parses_and_names_itself():
    names = workflows.list_workflows()
    assert {"ad-to-pages", "sweep", "score", "packet"} <= set(names)
    for name in names:
        workflow = workflows.load_workflow(name)
        assert workflow.get("name") == name
        assert workflow.get("description")


def test_every_stage_a_workflow_names_is_a_real_stage():
    for name in workflows.list_workflows():
        for stage in workflows.stage_names(workflows.load_workflow(name)):
            assert stage in pipeline.STAGES, f"{name}.yaml names an unknown stage: {stage}"


def test_ad_to_pages_runs_exactly_the_default_pipeline():
    """If these ever diverge, `harness workflow run ad-to-pages` stops being the
    same thing as `harness run` -- which is the one promise this file exists to
    keep."""
    stages = workflows.stage_names(workflows.load_workflow("ad-to-pages"))
    assert tuple(stages) == pipeline.DEFAULT_STAGES


def test_unknown_workflow_names_what_is_available():
    with pytest.raises(FileNotFoundError) as e:
        workflows.load_workflow("no-such-workflow")
    assert "ad-to-pages" in str(e.value)


def test_execute_rejects_an_unknown_stage():
    state = pipeline.RunState(tenant=TENANT, args=_base_args(), client=None)
    with pytest.raises(pipeline.UnknownStage):
        pipeline.execute(state, ["prepare_run", "no_such_stage"])


# ---------------------------------------------------------------------------
# Parity
# ---------------------------------------------------------------------------

def _pages(run_dir):
    return {
        p.parent.name: json.loads(p.read_text())
        for p in sorted(Path(run_dir).glob("*/page.json"))
    }


def _newest_run_dir():
    dirs = sorted(TENANT.out_dir.glob("*-hidden-costs-v2-transcript"))
    assert dirs, "expected a run dir under the tenant's out/"
    return dirs[-1]


def test_workflow_run_reproduces_harness_run_on_the_dry_run_fixture(monkeypatch):
    monkeypatch.setattr(cli, "make_client", lambda: FakeClient(list(RESPONSES)))
    _patch_network(monkeypatch)
    assert cli.cmd_run(_base_args()) == 0
    direct_pages = _pages(_newest_run_dir())

    monkeypatch.setattr(cli, "make_client", lambda: FakeClient(list(RESPONSES)))
    _patch_network(monkeypatch)
    workflow_args = _base_args(name="ad-to-pages")
    assert cli.cmd_workflow_run(workflow_args) == 0
    workflow_pages = _pages(_newest_run_dir())

    assert set(workflow_pages) == {"article", "product-page", "longform"}
    assert workflow_pages == direct_pages


def test_workflow_run_writes_the_same_run_artifacts(monkeypatch):
    monkeypatch.setattr(cli, "make_client", lambda: FakeClient(list(RESPONSES)))
    _patch_network(monkeypatch)
    assert cli.cmd_workflow_run(_base_args(name="ad-to-pages")) == 0
    run_dir = _newest_run_dir()
    for name in ("ad_brief.json", "facts_pack.json", "REVIEW.md"):
        assert (run_dir / name).exists()
    for cartridge in ("article", "product-page", "longform"):
        assert (run_dir / cartridge / "index.html").exists()
