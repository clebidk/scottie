"""Cycle 34: RunLog must close on unexpected unwind paths.

Cycle 33 made close() idempotent and added a context manager. Call sites that
still open a RunLog without a finally / `with` leave append handles open for
in-process callers (tests, revise helpers, embedders that catch exceptions).
"""
from types import SimpleNamespace

import pytest

from harness import pipeline
from harness.budget import Budget
from harness.log import RunLog


def test_execute_closes_log_when_a_stage_raises_unexpected(tmp_path, monkeypatch):
    log = RunLog("close-test", tmp_path / "close-test.log")
    state = SimpleNamespace(
        log=log,
        budget=Budget(wall_s=60, tokens=1000, calls=10),
        tenant=SimpleNamespace(name="t"),
        run_id="close-test",
        today_iso="2026-09-14",
        gate_log={},
        run_dir=tmp_path,
        outputs=[],
    )

    def boom(_state):
        raise ValueError("stage blew up")

    monkeypatch.setitem(pipeline.STAGES, "boom_stage", boom)

    with pytest.raises(ValueError, match="stage blew up"):
        pipeline.execute(state, ["boom_stage"])

    assert log._fh.closed


def test_execute_closes_log_on_pass_path(tmp_path, monkeypatch):
    log = RunLog("close-pass", tmp_path / "close-pass.log")
    state = SimpleNamespace(
        log=log,
        budget=Budget(wall_s=60, tokens=1000, calls=10),
        tenant=SimpleNamespace(name="t"),
        run_id="close-pass",
        today_iso="2026-09-14",
        gate_log={},
        run_dir=tmp_path,
        outputs=[],
    )

    monkeypatch.setitem(pipeline.STAGES, "noop_stage", lambda _s: None)
    monkeypatch.setattr(pipeline.review_md, "log_run_result", lambda *a, **k: None)
    monkeypatch.setattr(
        "harness.review.build_reviews",
        lambda _run_dir: None,
    )

    assert pipeline.execute(state, ["noop_stage"]) == 0
    assert log._fh.closed
