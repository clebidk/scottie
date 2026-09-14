"""Cycle 26: harness/revise.py -- cut: directives (no model call), free-text
notes (one writer call + the existing bounded repair loop), versioning, and
state transitions. Builds a real run dir with `harness run` against a fake
client (same pattern as tests/test_cli_run.py), then revises one page.
"""
import json

import pytest

from harness import cli, revise, runstate
from harness.review import build_reviews
from tests.conftest import FakeClient, json_response
from tests.test_cli_run import AD_BRIEF_RESPONSE, _base_args, _patch_network
from tests.test_render import ARTICLE_PAGE, LONGFORM_PAGE, PRODUCT_PAGE_PAGE
from tests.support import TENANT, newest_run_dir


def _make_run(monkeypatch):
    responses = [
        json_response(AD_BRIEF_RESPONSE),
        json_response({}),  # semantic-match call, no mappings
        json_response(ARTICLE_PAGE),
        json_response(PRODUCT_PAGE_PAGE),
        json_response(LONGFORM_PAGE),
    ]
    client = FakeClient(responses)
    monkeypatch.setattr(cli, "make_client", lambda: client)
    _patch_network(monkeypatch)
    exit_code = cli.cmd_run(_base_args())
    assert exit_code == 0
    run_dir = newest_run_dir(TENANT.out_dir, "*-hidden-costs-v2-transcript-*")
    # Cycle 26b: `harness run` now builds every page's <page>-review.html
    # itself at the end of a successful run, but this call is kept (it's
    # idempotent) so this fixture doesn't depend on that to give a revise a
    # real "current" review.html to version.
    build_reviews(run_dir)
    return run_dir


# ---------------------------------------------------------------------------
# Pure helpers -- no run dir needed.
# ---------------------------------------------------------------------------

def test_parse_cuts_and_notes_splits_cut_lines_from_free_text():
    raw = "cut: Buyers move on when the price is hidden.\nShorten the opening paragraph.\ncut: Second sentence to cut.\n"
    cuts, notes = revise.parse_cuts_and_notes(raw)
    assert cuts == ["Buyers move on when the price is hidden.", "Second sentence to cut."]
    assert notes == "Shorten the opening paragraph."


def test_apply_cuts_removes_sentence_by_normalized_whitespace():
    page = {"open": [{"text": "Shopping   used to mean\nwaiting for a callback."}]}
    new_page, applied = revise.apply_cuts_to_page(page, ["Shopping used to mean waiting for a callback."])
    assert applied == ["Shopping used to mean waiting for a callback."]
    # The whole open[] list drops the item once its text is empty.
    assert new_page["open"] == []


def test_apply_cuts_drops_item_only_when_its_own_text_is_emptied():
    page = {
        "body_sections": [{
            "heading": "Why hidden pricing kills trust",
            "paragraphs": [
                {"text": "Buyers move on when the price is hidden."},
                {"text": "A second, unrelated paragraph stays untouched."},
            ],
        }]
    }
    new_page, applied = revise.apply_cuts_to_page(page, ["Buyers move on when the price is hidden."])
    assert applied
    paragraphs = new_page["body_sections"][0]["paragraphs"]
    assert len(paragraphs) == 1
    assert paragraphs[0]["text"] == "A second, unrelated paragraph stays untouched."


def test_apply_cuts_is_a_noop_for_a_sentence_not_on_the_page():
    page = {"open": [{"text": "Shopping used to mean waiting for a callback."}]}
    new_page, applied = revise.apply_cuts_to_page(page, ["This sentence is not on the page."])
    assert applied == []
    assert new_page == page


# ---------------------------------------------------------------------------
# Full revise_page, cut-only: no model call.
# ---------------------------------------------------------------------------

def test_revise_cut_only_makes_no_model_call_and_removes_the_sentence(monkeypatch):
    run_dir = _make_run(monkeypatch)
    cut_sentence = "Buyers move on when the price is hidden."
    runstate.request_changes(
        run_dir, TENANT, page="article", by="caleb@peaksaunas.com",
        scores={"angle": 4, "brand": 5, "claims": 5, "publish": 4},
        notes="", cuts=[cut_sentence],
    )
    assert runstate.load_state(run_dir)["pages"]["article"] == runstate.PAGE_CHANGES_REQUESTED

    def _no_client():
        raise AssertionError("cut-only revise must not call make_client()")

    result = revise.revise_page(run_dir, "article", by="caleb@peaksaunas.com", tenant=TENANT, make_client_fn=_no_client)

    assert result["model_called"] is False
    assert result["applied_cuts"] == [cut_sentence]
    assert result["version"] == 1

    cartridge_dir = run_dir / "article"
    assert (cartridge_dir / "page.v1.json").exists()
    assert (cartridge_dir / "index.v1.html").exists()
    assert (run_dir / "article-review.v1.html").exists()
    assert (cartridge_dir / "page.json").exists()
    assert (cartridge_dir / "index.html").exists()
    assert (run_dir / "article-review.html").exists()

    new_page = json.loads((cartridge_dir / "page.json").read_text())
    dumped = json.dumps(new_page)
    assert cut_sentence not in dumped

    old_page = json.loads((cartridge_dir / "page.v1.json").read_text())
    assert cut_sentence in json.dumps(old_page)

    state = runstate.load_state(run_dir)
    assert state["pages"]["article"] == "needs_review"
    assert any("revised to v1" in h.get("note", "") for h in state["history"])


# ---------------------------------------------------------------------------
# Full revise_page, free-text notes: one writer call, then the gate.
# ---------------------------------------------------------------------------

def test_revise_with_notes_calls_writer_once_and_gates(monkeypatch):
    run_dir = _make_run(monkeypatch)
    runstate.request_changes(
        run_dir, TENANT, page="article", by="michael@peaksaunas.com",
        notes="Shorten the opening paragraph by half.", cuts=[],
    )

    revised_article = dict(ARTICLE_PAGE, headline="A shorter, punchier headline for the checkout page")
    writer_client = FakeClient([json_response(revised_article)])

    result = revise.revise_page(
        run_dir, "article", by="michael@peaksaunas.com", tenant=TENANT,
        make_client_fn=lambda: writer_client,
    )

    assert result["model_called"] is True
    assert len(writer_client.messages.calls) == 1
    assert result["gate_problems"] == []
    assert result["version"] == 1

    cartridge_dir = run_dir / "article"
    assert (cartridge_dir / "page.v1.json").exists()
    new_page = json.loads((cartridge_dir / "page.json").read_text())
    assert new_page["headline"] == "A shorter, punchier headline for the checkout page"

    # The revision note the writer actually saw carries the reviewer's notes
    # and says to keep everything else.
    sent = writer_client.messages.calls[0]
    user_text = "".join(
        b["text"] for m in sent["messages"] for b in (m["content"] if isinstance(m["content"], list) else [{"type": "text", "text": m["content"]}])
        if isinstance(b, dict) and b.get("type") == "text"
    )
    assert "REVIEWER NOTES" in user_text
    assert "Shorten the opening paragraph by half." in user_text
    assert "keep everything else" in user_text

    state = runstate.load_state(run_dir)
    assert state["pages"]["article"] == "needs_review"

    review_md = (run_dir / "REVIEW.md").read_text()
    assert "## Revision: article v1" in review_md
    assert "Writer called: yes" in review_md


def test_revise_second_call_produces_v2(monkeypatch):
    run_dir = _make_run(monkeypatch)
    runstate.request_changes(run_dir, TENANT, page="article", by="caleb@peaksaunas.com", cuts=["Buyers move on when the price is hidden."])
    revise.revise_page(run_dir, "article", tenant=TENANT, make_client_fn=lambda: (_ for _ in ()).throw(AssertionError))

    runstate.request_changes(run_dir, TENANT, page="article", by="caleb@peaksaunas.com", cuts=["Peak Saunas is one brand that does this."])
    result = revise.revise_page(run_dir, "article", tenant=TENANT, make_client_fn=lambda: (_ for _ in ()).throw(AssertionError))

    assert result["version"] == 2
    cartridge_dir = run_dir / "article"
    assert (cartridge_dir / "page.v1.json").exists()
    assert (cartridge_dir / "page.v2.json").exists()
    assert (cartridge_dir / "page.json").exists()


def test_revise_raises_without_feedback(monkeypatch):
    run_dir = _make_run(monkeypatch)
    with pytest.raises(revise.ReviseError):
        revise.revise_page(run_dir, "article", tenant=TENANT)


def test_revise_raises_for_unknown_page(monkeypatch):
    run_dir = _make_run(monkeypatch)
    with pytest.raises(revise.ReviseError):
        revise.revise_page(run_dir, "no-such-cartridge", tenant=TENANT)


# ---------------------------------------------------------------------------
# Cycle 34: notes-driven revise participates in the daily spend ledger.
# ---------------------------------------------------------------------------

def test_revise_with_notes_reserves_and_records_daily_spend(monkeypatch, tmp_path):
    """A notes revise must reserve_spend before the writer runs and
    record_spend when it finishes -- otherwise `harness revise` bypasses the
    daily_usd cap that harness run already honors."""
    from harness import budget as budget_mod

    run_dir = _make_run(monkeypatch)
    ledger = tmp_path / "spend-ledger.jsonl"
    monkeypatch.setattr(budget_mod, "spend_ledger_path", lambda tenant: ledger)

    config = dict(TENANT.claims_config, budget={"daily_usd": 5, "per_run_usd": 0.5})
    monkeypatch.setattr(type(TENANT), "claims_config", property(lambda self: config))

    runstate.request_changes(
        run_dir, TENANT, page="article", by="caleb@peaksaunas.com",
        notes="Tighten the second section.", cuts=[],
    )
    revised = dict(ARTICLE_PAGE, headline="A revised headline after spend-ledger wiring")
    writer = FakeClient([json_response(revised)])

    result = revise.revise_page(
        run_dir, "article", by="caleb@peaksaunas.com", tenant=TENANT,
        make_client_fn=lambda: writer,
    )
    assert result["model_called"] is True
    assert ledger.exists()
    lines = [ln for ln in ledger.read_text().splitlines() if ln.strip()]
    assert len(lines) >= 2  # reservation + final cost
    assert any("__revise__article__v" in ln for ln in lines)


def test_revise_with_notes_refuses_when_daily_cap_already_spent(monkeypatch, tmp_path):
    """If today's ledger is already at the daily_usd cap, revise must raise
    BudgetExceeded before make_client_fn is called (no silent spend)."""
    from harness import budget as budget_mod
    from harness.budget import BudgetExceeded, record_spend

    run_dir = _make_run(monkeypatch)
    ledger = tmp_path / "spend-ledger.jsonl"
    monkeypatch.setattr(budget_mod, "spend_ledger_path", lambda tenant: ledger)

    config = dict(TENANT.claims_config, budget={"daily_usd": 0.5, "per_run_usd": 0.5})
    monkeypatch.setattr(type(TENANT), "claims_config", property(lambda self: config))

    record_spend(TENANT, run_id="prior-run", cost=0.5, today_iso=__import__("datetime").date.today().isoformat())

    runstate.request_changes(
        run_dir, TENANT, page="article", by="caleb@peaksaunas.com",
        notes="Would spend more tokens.", cuts=[],
    )

    def _boom():
        raise AssertionError("make_client_fn must not run when the daily cap blocks revise")

    with pytest.raises(BudgetExceeded) as exc_info:
        revise.revise_page(
            run_dir, "article", by="caleb@peaksaunas.com", tenant=TENANT,
            make_client_fn=_boom,
        )
    assert exc_info.value.kind == "daily_usd"


def test_revise_cuts_only_does_not_touch_spend_ledger(monkeypatch, tmp_path):
    """Cuts are free -- no reservation, no ledger write."""
    from harness import budget as budget_mod

    run_dir = _make_run(monkeypatch)
    ledger = tmp_path / "spend-ledger.jsonl"
    monkeypatch.setattr(budget_mod, "spend_ledger_path", lambda tenant: ledger)

    config = dict(TENANT.claims_config, budget={"daily_usd": 5, "per_run_usd": 0.5})
    monkeypatch.setattr(type(TENANT), "claims_config", property(lambda self: config))

    # Prefer a known cut from older tests when present; otherwise skip soft.
    cut = "Buyers move on when the price is hidden."
    runstate.request_changes(
        run_dir, TENANT, page="article", by="caleb@peaksaunas.com",
        notes="", cuts=[cut],
    )
    revise.revise_page(
        run_dir, "article", tenant=TENANT,
        make_client_fn=lambda: (_ for _ in ()).throw(AssertionError("cuts-only must not call the model")),
    )
    assert not ledger.exists()


def test_cmd_revise_exits_3_on_cap_refusal_no_traceback(monkeypatch, tmp_path, capsys):
    """Cycle 34 fix: `harness revise` must handle BudgetExceeded the same way
    `harness run` does -- clean message, exit 3, no traceback -- instead of
    letting it fall through cmd_revise's ReviseError-only except clause."""
    import argparse

    from harness import budget as budget_mod
    from harness.budget import record_spend

    run_dir = _make_run(monkeypatch)
    ledger = tmp_path / "spend-ledger.jsonl"
    monkeypatch.setattr(budget_mod, "spend_ledger_path", lambda tenant: ledger)

    config = dict(TENANT.claims_config, budget={"daily_usd": 0.5, "per_run_usd": 0.5})
    monkeypatch.setattr(type(TENANT), "claims_config", property(lambda self: config))

    record_spend(TENANT, run_id="prior-run", cost=0.5, today_iso=__import__("datetime").date.today().isoformat())

    runstate.request_changes(
        run_dir, TENANT, page="article", by="caleb@peaksaunas.com",
        notes="Would spend more tokens.", cuts=[],
    )

    # cmd_revise doesn't take a make_client_fn override -- reserve_spend still
    # raises before revise_page's default make_client is ever called, so no
    # real Anthropic client construction happens on this path either way.
    args = argparse.Namespace(run_dir=str(run_dir), page="article", by="caleb@peaksaunas.com", tenant=TENANT.name)
    exit_code = cli.cmd_revise(args)

    assert exit_code == 3
    err = capsys.readouterr().err
    assert "budget exceeded" in err
    assert "daily spend cap reached" in err
    assert "Traceback" not in err

