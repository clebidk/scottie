"""Cycle 53: a tenant that has renamed itself (tenant.yaml brand.retired_names)
must never have the old name reach fresh copy -- write.py's global_voice_block
tells the writer the current display name, and harness/repair.py's
find_retired_name_violations / apply_deterministic_fixes gate and rewrite
anything that still slips in, the same deterministic-pre-repair shape as the
warranty/financing checks. `harness fixcopy` applies the same rewrite
directly to an existing page.json -- no model call, no gate."""
import argparse
import json

import pytest

from harness import cli, runstate
from harness.budget import Budget
from harness.log import RunLog
from harness.repair import (
    apply_deterministic_fixes,
    apply_retired_name_fixes,
    find_retired_name_violations,
    write_and_gate_page,
)
from tests.conftest import FakeClient, json_response
from tests.support import REPO_ROOT, TENANT
from tests.test_render import AD_BRIEF, ARTICLE_PAGE, FACTS_PACK


class _RecordingLog:
    """A minimal log double: records every event() call as (stage, message)
    so a test can assert a "fix: ..." or "note: ..." line was logged without
    a real RunLog file on disk."""

    def __init__(self):
        self.events = []

    def event(self, stage, message):
        self.events.append((stage, message))


class _RetiredNameTenant:
    """A tenant double carrying only what find_retired_name_violations /
    _fix_retired_name_violation read -- brand.retired_names,
    brand.retired_name_exceptions, display_name -- isolated from the real
    peak-saunas TENANT so these tests do not depend on its exact
    tenant.yaml values."""

    display_name = "PEAK"
    _config = {
        "brand": {
            "retired_names": ["Peak Saunas"],
            "retired_name_exceptions": ["Peak Saunas app"],
        }
    }

    def get(self, dotted_key, default=None):
        node = self._config
        for part in dotted_key.split("."):
            if not isinstance(node, dict) or part not in node:
                return default
            node = node[part]
        return node


class _NoRetiredNameTenant:
    display_name = "PEAK"

    def get(self, dotted_key, default=None):
        return default


FACTS_PACK_NO_CLAIMS = {"verified_claims": []}


# ---------------------------------------------------------------------------
# find_retired_name_violations
# ---------------------------------------------------------------------------

def test_find_retired_name_violations_flags_a_nested_field():
    page = {
        "body_sections": [{
            "heading": "About",
            "paragraphs": [{"text": "Peak Saunas publishes its warranty online."}],
        }],
    }
    hits = find_retired_name_violations(page, FACTS_PACK_NO_CLAIMS, _RetiredNameTenant())
    assert len(hits) == 1
    assert hits[0]["path"] == "$.body_sections[0].paragraphs[0].text"
    assert hits[0]["term"] == "Peak Saunas"
    assert hits[0]["key"] == "retired_name:$.body_sections[0].paragraphs[0].text"


def test_find_retired_name_violations_preserves_an_exception_phrase():
    page = {"faq": [{"question": "App?", "text": "Download the Peak Saunas app for order tracking."}]}
    hits = find_retired_name_violations(page, FACTS_PACK_NO_CLAIMS, _RetiredNameTenant())
    assert hits == []


def test_find_retired_name_violations_flags_an_unprotected_occurrence_next_to_an_exception():
    page = {"faq": [{"question": "App?", "text": "The Peak Saunas app and Peak Saunas itself both ship fast."}]}
    hits = find_retired_name_violations(page, FACTS_PACK_NO_CLAIMS, _RetiredNameTenant())
    assert len(hits) == 1
    assert hits[0]["text"] == "The Peak Saunas app and Peak Saunas itself both ship fast."


def test_find_retired_name_violations_leaves_a_verbatim_verified_claim_quote_alone_and_logs_a_note():
    facts_pack = {"verified_claims": [{"id": "trust-1", "text": "Peak Saunas is a US-owned company."}]}
    page = {"proof_points": [{"text": "Peak Saunas is a US-owned company.", "claim_ids": ["trust-1"]}]}
    log = _RecordingLog()

    hits = find_retired_name_violations(page, facts_pack, _RetiredNameTenant(), log=log, cartridge_name="article")

    assert hits == []
    assert page["proof_points"][0]["text"] == "Peak Saunas is a US-owned company."
    assert any("note:" in msg and "Peak Saunas" in msg and "verbatim quote" in msg for _, msg in log.events)


def test_find_retired_name_violations_is_a_noop_without_retired_names_configured():
    page = {"open": [{"text": "Peak Saunas is one brand that does this."}]}
    hits = find_retired_name_violations(page, FACTS_PACK_NO_CLAIMS, _NoRetiredNameTenant())
    assert hits == []


def test_find_retired_name_violations_leaves_the_display_name_alone():
    page = {"open": [{"text": "PEAK is one brand that does this."}]}
    hits = find_retired_name_violations(page, FACTS_PACK_NO_CLAIMS, _RetiredNameTenant())
    assert hits == []


def test_find_retired_name_violations_skips_non_prose_fields():
    page = {"cta_url": "https://peaksaunas.com/pages/peak-saunas-app", "claim_ids": ["peak-saunas-claim"]}
    hits = find_retired_name_violations(page, FACTS_PACK_NO_CLAIMS, _RetiredNameTenant())
    assert hits == []


# ---------------------------------------------------------------------------
# _fix_retired_name_violation / apply_deterministic_fixes / apply_retired_name_fixes
# ---------------------------------------------------------------------------

def test_apply_deterministic_fixes_resolves_a_retired_name_in_a_nested_field():
    page = {"body_sections": [{"paragraphs": [{"text": "Peak Saunas publishes its warranty online."}]}]}
    tenant = _RetiredNameTenant()
    failures = find_retired_name_violations(page, FACTS_PACK_NO_CLAIMS, tenant)
    log = _RecordingLog()

    fixed = apply_deterministic_fixes(page, failures, set(), log=log, cartridge_name="article", tenant=tenant)

    assert fixed == 1
    assert page["body_sections"][0]["paragraphs"][0]["text"] == "PEAK publishes its warranty online."
    assert find_retired_name_violations(page, FACTS_PACK_NO_CLAIMS, tenant) == []
    assert any('fix: retired name "Peak Saunas" -> "PEAK"' in msg for _, msg in log.events)


def test_apply_deterministic_fixes_ignores_a_retired_name_failure_without_a_tenant():
    # tenant defaults to None -- the branch is a no-op rather than a crash,
    # matching every other apply_deterministic_fixes branch's shape when its
    # own extra context (facts_pack, financing_lender) is withheld.
    page = {"open": [{"text": "Peak Saunas is one brand that does this."}]}
    failures = [{
        "path": "$.open[0].text", "term": "Peak Saunas",
        "issue": "retired brand name 'Peak Saunas' found; use 'PEAK' instead",
        "key": "retired_name:$.open[0].text",
    }]
    fixed = apply_deterministic_fixes(page, failures, set())
    assert fixed == 0
    assert page["open"][0]["text"] == "Peak Saunas is one brand that does this."


def test_apply_retired_name_fixes_reports_what_changed():
    page = {"open": [{"text": "Peak Saunas is one brand that does this."}]}
    tenant = _RetiredNameTenant()

    changes = apply_retired_name_fixes(page, FACTS_PACK_NO_CLAIMS, tenant)

    assert changes == [("Peak Saunas", "PEAK", "$.open[0].text")]
    assert page["open"][0]["text"] == "PEAK is one brand that does this."


def test_apply_retired_name_fixes_is_a_noop_when_the_display_name_is_already_present():
    page = {"open": [{"text": "PEAK is one brand that does this."}]}
    changes = apply_retired_name_fixes(page, FACTS_PACK_NO_CLAIMS, _RetiredNameTenant())
    assert changes == []
    assert page["open"][0]["text"] == "PEAK is one brand that does this."


# ---------------------------------------------------------------------------
# Full writer-repair-loop integration, against the real peak-saunas TENANT
# (tenant.yaml brand.retired_names: ["Peak Saunas"]).
# ---------------------------------------------------------------------------

def test_write_and_gate_page_resolves_a_retired_name_via_deterministic_fix_without_a_repair_call(tmp_path):
    bad_page = dict(
        ARTICLE_PAGE,
        close={"paragraphs": [{"text": "Peak Saunas is one brand that does this."}]},
    )
    client = FakeClient([json_response(bad_page)])
    budget = Budget()
    log = RunLog("test-run", tmp_path / "run.log")
    try:
        page, attempts, deterministic_fixes = write_and_gate_page(
            cartridge_name="article",
            cartridges_dir=REPO_ROOT / "cartridges",
            ad_brief=AD_BRIEF,
            facts_pack=FACTS_PACK,
            client=client,
            model="claude-sonnet-5",
            budget=budget,
            log=log,
            financing_lender=None,
            speaker_pov=AD_BRIEF["speaker_pov"],
            tenant=TENANT,
        )
    finally:
        log.close()

    assert len(client.messages.calls) == 1  # no repair call needed
    assert attempts == [[]]
    assert deterministic_fixes == [1]
    assert page["close"]["paragraphs"][0]["text"] == "PEAK is one brand that does this."


# ---------------------------------------------------------------------------
# `harness fixcopy`
# ---------------------------------------------------------------------------

@pytest.fixture
def run_dir(tmp_path):
    run_dir = tmp_path / "20260922-000000-test-run-abcd"
    (run_dir / "article").mkdir(parents=True)
    (run_dir / "facts_pack.json").write_text(json.dumps(FACTS_PACK))
    (run_dir / "ad_brief.json").write_text(json.dumps(AD_BRIEF))
    bad_page = dict(
        ARTICLE_PAGE,
        close={"paragraphs": [{"text": "Peak Saunas is one brand that does this."}]},
    )
    (run_dir / "article" / "page.json").write_text(json.dumps(bad_page))
    runstate.init_state(run_dir, pages=["article"])
    return run_dir


def _args(run_dir, **over):
    base = dict(run_dir=str(run_dir), page="article", tenant=TENANT.name)
    base.update(over)
    return argparse.Namespace(**base)


def test_fixcopy_rewrites_page_json_and_reports_the_change(run_dir, capsys):
    assert cli.cmd_fixcopy(_args(run_dir)) == 0

    page = json.loads((run_dir / "article" / "page.json").read_text())
    assert page["close"]["paragraphs"][0]["text"] == "PEAK is one brand that does this."

    out = capsys.readouterr().out
    assert 'fix: retired name "Peak Saunas" -> "PEAK" at $.close.paragraphs[0].text' in out
    assert "Now run: harness rerender" in out


def test_fixcopy_leaves_approval_state_alone_and_records_history(run_dir):
    data = runstate.load_state(run_dir)
    data["state"] = "approved"
    data["pages"]["article"] = "approved"
    runstate.save_state(run_dir, data)

    cli.cmd_fixcopy(_args(run_dir))

    after = runstate.load_state(run_dir)
    assert after["state"] == "approved"
    assert after["pages"]["article"] == "approved"
    last = after["history"][-1]
    assert last["fixcopy_at"]
    assert last["state"] == "approved"  # the note reads in order, same as mark_rerendered
    assert "fixcopy page=article" in last["note"]
    assert "1 retired-name fix" in last["note"]


def test_fixcopy_is_a_noop_when_nothing_needs_fixing(run_dir, capsys):
    # ARTICLE_PAGE unmodified already reads "PEAK", not the retired name.
    (run_dir / "article" / "page.json").write_text(json.dumps(ARTICLE_PAGE))
    before = runstate.load_state(run_dir)

    assert cli.cmd_fixcopy(_args(run_dir)) == 0

    assert "No copy fixes needed" in capsys.readouterr().out
    assert runstate.load_state(run_dir) == before  # no history entry added


def test_fixcopy_refuses_a_page_with_no_page_json(run_dir, capsys):
    (run_dir / "article" / "page.json").unlink()
    assert cli.cmd_fixcopy(_args(run_dir)) == 1
    assert "page.json" in capsys.readouterr().err


def test_fixcopy_refuses_a_run_with_no_facts_pack(run_dir, capsys):
    (run_dir / "facts_pack.json").unlink()
    assert cli.cmd_fixcopy(_args(run_dir)) == 1
    assert "facts_pack.json" in capsys.readouterr().err


def test_fixcopy_makes_no_model_call(run_dir, monkeypatch):
    def explode(*a, **kw):
        raise AssertionError("fixcopy must not build an API client")

    monkeypatch.setattr(cli, "make_client", explode)
    assert cli.cmd_fixcopy(_args(run_dir)) == 0


def test_cli_parser_wires_fixcopy():
    parser = cli.build_parser()
    args = parser.parse_args(["fixcopy", "out/run", "--page", "article"])
    assert args.func is cli.cmd_fixcopy
    assert args.page == "article"
