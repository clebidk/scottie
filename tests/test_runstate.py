"""Cycle 20: the run state machine (harness/runstate.py) -- state.json,
packet.json, approval, and the needs_review-backlog digest query."""
import json

import pytest

from harness import runstate


class FakeTenant:
    """Just enough of harness.tenant.Tenant for runstate's use of it:
    .name, .get("reviewers"), and .evals_path (for approvals.jsonl)."""

    def __init__(self, tmp_path, *, reviewers=None):
        self.name = "acme"
        self._reviewers = reviewers or []
        self.evals_path = tmp_path / "evals" / "scores.jsonl"
        self.out_dir = tmp_path / "out"

    def get(self, key, default=None):
        if key == "reviewers":
            return self._reviewers
        return default


def _make_run_dir(tmp_path, *, pages=("article", "product-page")):
    run_dir = tmp_path / "20260910-1200-test-run"
    run_dir.mkdir()
    runstate.init_state(run_dir, pages=list(pages))
    runstate.init_packet(run_dir)
    return run_dir


# ---------------------------------------------------------------------------
# state.json lifecycle
# ---------------------------------------------------------------------------

def test_init_state_writes_generated_for_every_page(tmp_path):
    run_dir = _make_run_dir(tmp_path)
    data = runstate.load_state(run_dir)
    assert data["state"] == "generated"
    assert data["pages"] == {"article": "generated", "product-page": "generated"}
    assert data["history"][0]["state"] == "generated"


def test_init_state_does_not_overwrite_an_existing_state(tmp_path):
    run_dir = _make_run_dir(tmp_path)
    runstate.mark_needs_review(run_dir)
    runstate.init_state(run_dir, pages=["article", "product-page"])
    assert runstate.load_state(run_dir)["state"] == "needs_review"


def test_load_state_raises_for_a_directory_with_no_state_json(tmp_path):
    run_dir = tmp_path / "not-a-run"
    run_dir.mkdir()
    with pytest.raises(FileNotFoundError):
        runstate.load_state(run_dir)


def test_mark_needs_review_transitions_run_and_every_generated_page(tmp_path):
    run_dir = _make_run_dir(tmp_path)
    data = runstate.mark_needs_review(run_dir, note="run passed the claims gate")
    assert data["state"] == "needs_review"
    assert data["pages"] == {"article": "needs_review", "product-page": "needs_review"}
    assert data["history"][-1]["note"] == "run passed the claims gate"


# ---------------------------------------------------------------------------
# approve / reject
# ---------------------------------------------------------------------------

def test_approve_refuses_an_unknown_reviewer(tmp_path):
    run_dir = _make_run_dir(tmp_path)
    tenant = FakeTenant(tmp_path, reviewers=[{"name": "Michael", "email": "michael@acme.com", "role": "primary"}])
    runstate.mark_needs_review(run_dir)
    with pytest.raises(runstate.UnknownReviewer):
        runstate.approve(run_dir, tenant, by="stranger@example.com")


def test_approve_partial_pages_leaves_run_needs_review(tmp_path):
    run_dir = _make_run_dir(tmp_path)
    tenant = FakeTenant(tmp_path, reviewers=[{"name": "Michael", "email": "michael@acme.com", "role": "primary"}])
    runstate.mark_needs_review(run_dir)
    data = runstate.approve(run_dir, tenant, by="michael@acme.com", pages=["article"])
    assert data["pages"]["article"] == "approved"
    assert data["pages"]["product-page"] == "needs_review"
    assert data["state"] == "needs_review"


def test_approve_all_pages_moves_run_to_approved(tmp_path):
    run_dir = _make_run_dir(tmp_path)
    tenant = FakeTenant(tmp_path, reviewers=[{"name": "Michael", "email": "michael@acme.com", "role": "primary"}])
    runstate.mark_needs_review(run_dir)
    data = runstate.approve(run_dir, tenant, by="michael@acme.com")
    assert data["state"] == "approved"
    assert all(v == "approved" for v in data["pages"].values())


def test_approve_is_case_insensitive_on_email(tmp_path):
    run_dir = _make_run_dir(tmp_path)
    tenant = FakeTenant(tmp_path, reviewers=[{"name": "Michael", "email": "michael@acme.com", "role": "primary"}])
    runstate.mark_needs_review(run_dir)
    data = runstate.approve(run_dir, tenant, by="Michael@ACME.com")
    assert data["state"] == "approved"


def test_approve_appends_one_line_to_approvals_jsonl(tmp_path):
    run_dir = _make_run_dir(tmp_path)
    tenant = FakeTenant(tmp_path, reviewers=[{"name": "Michael", "email": "michael@acme.com", "role": "primary"}])
    runstate.mark_needs_review(run_dir)
    runstate.approve(run_dir, tenant, by="michael@acme.com", pages=["article"], note="looks good")
    approvals_path = tenant.evals_path.parent / "approvals.jsonl"
    lines = approvals_path.read_text().strip().splitlines()
    assert len(lines) == 1
    entry = json.loads(lines[0])
    assert entry["by"] == "michael@acme.com"
    assert entry["pages"] == ["article"]
    assert entry["note"] == "looks good"
    assert entry["run_dir"] == str(run_dir)


def test_approve_unknown_page_raises_key_error(tmp_path):
    run_dir = _make_run_dir(tmp_path)
    tenant = FakeTenant(tmp_path, reviewers=[{"name": "Michael", "email": "michael@acme.com", "role": "primary"}])
    runstate.mark_needs_review(run_dir)
    with pytest.raises(KeyError):
        runstate.approve(run_dir, tenant, by="michael@acme.com", pages=["no-such-page"])


def test_reject_refuses_an_unknown_reviewer(tmp_path):
    run_dir = _make_run_dir(tmp_path)
    tenant = FakeTenant(tmp_path, reviewers=[{"name": "Michael", "email": "michael@acme.com", "role": "primary"}])
    runstate.mark_needs_review(run_dir)
    with pytest.raises(runstate.UnknownReviewer):
        runstate.reject(run_dir, tenant, by="stranger@example.com", note="no")


def test_reject_sets_every_page_and_the_run_to_rejected(tmp_path):
    run_dir = _make_run_dir(tmp_path)
    tenant = FakeTenant(tmp_path, reviewers=[{"name": "Michael", "email": "michael@acme.com", "role": "primary"}])
    runstate.mark_needs_review(run_dir)
    data = runstate.reject(run_dir, tenant, by="michael@acme.com", note="wrong angle")
    assert data["state"] == "rejected"
    assert all(v == "rejected" for v in data["pages"].values())
    assert data["history"][-1]["note"] == "wrong angle"


def test_mark_published_moves_one_page_and_run_state_once_all_published(tmp_path):
    run_dir = _make_run_dir(tmp_path, pages=("article",))
    runstate.mark_needs_review(run_dir)
    runstate.mark_published(run_dir, page="article", by="operator")
    data = runstate.load_state(run_dir)
    assert data["pages"]["article"] == "published"
    assert data["state"] == "published"


# ---------------------------------------------------------------------------
# packet.json
# ---------------------------------------------------------------------------

def test_new_run_packet_defaults_to_bot_draft_not_sent(tmp_path):
    run_dir = _make_run_dir(tmp_path)
    packet = runstate.load_packet(run_dir)
    assert packet["stamp"] == runstate.DEFAULT_PACKET_STAMP == "BOT DRAFT · NOT SENT"


def test_set_packet_stamp_ship(tmp_path):
    run_dir = _make_run_dir(tmp_path)
    data = runstate.set_packet_stamp(run_dir, stamp="ship", by="caleb@peaksaunas.com", note="go")
    assert data["stamp"] == "ship"
    assert runstate.load_packet(run_dir)["stamp"] == "ship"


def test_set_packet_stamp_rejects_an_invalid_value(tmp_path):
    run_dir = _make_run_dir(tmp_path)
    with pytest.raises(ValueError):
        runstate.set_packet_stamp(run_dir, stamp="ship-it-now", by="caleb@peaksaunas.com")


# ---------------------------------------------------------------------------
# needs_review_runs (the weekly digest query)
# ---------------------------------------------------------------------------

def test_needs_review_runs_finds_only_old_enough_needs_review_runs(tmp_path):
    tenant = FakeTenant(tmp_path)
    tenant.out_dir.mkdir()

    old_run = tenant.out_dir / "20260101-0000-old"
    old_run.mkdir()
    runstate.init_state(old_run, pages=["article"])
    data = runstate.load_state(old_run)
    data["state"] = "needs_review"
    data["history"].append({"state": "needs_review", "by": "system", "at": "2026-01-01T00:00:00", "note": ""})
    runstate.save_state(old_run, data)

    fresh_run = tenant.out_dir / "20260910-0000-fresh"
    fresh_run.mkdir()
    runstate.init_state(fresh_run, pages=["article"])
    runstate.mark_needs_review(fresh_run)  # "at" is now, well inside 3 days

    approved_run = tenant.out_dir / "20260101-0000-approved"
    approved_run.mkdir()
    runstate.init_state(approved_run, pages=["article"])
    runstate.mark_needs_review(approved_run)
    data = runstate.load_state(approved_run)
    data["state"] = "approved"
    data["pages"]["article"] = "approved"
    runstate.save_state(approved_run, data)

    rows = runstate.needs_review_runs(tenant, older_than_days=3)
    assert [r[0].name for r in rows] == ["20260101-0000-old"]


def test_needs_review_runs_empty_when_out_dir_missing(tmp_path):
    tenant = FakeTenant(tmp_path)
    assert runstate.needs_review_runs(tenant) == []
