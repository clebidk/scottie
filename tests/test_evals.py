"""Kimi long-run phase 5 (docs/KIMI-LONG-RUN.md): the dataset export and the
eval report (harness/evals.py), plus record_score's new home. The export
rebuilds one record per (run, cartridge) from existing run artifacts; the
report aggregates scores.jsonl by cartridge, block, angle, and reviewer.
"""
import json

from harness import cli
from harness import evals as evals_mod
from tests.support import TENANT, newest_run_dir


def _fake_run():
    """One real fake-client run to export from (the suite's own dry run)."""
    from evals import fake_run

    assert fake_run.main(["tenants/peak-saunas/fixtures/founder-warranty-demo.txt", "--tenant", "peak-saunas"]) == 0
    return newest_run_dir(TENANT.out_dir, "*-founder-warranty-demo-*")


# ---------------------------------------------------------------------------
# record_score / load_scores
# ---------------------------------------------------------------------------

def test_record_score_and_load_scores_round_trip(monkeypatch, tmp_path):
    monkeypatch.setattr(type(TENANT), "evals_path", property(lambda self: tmp_path / "scores.jsonl"))
    evals_mod.record_score(TENANT, run_dir="/x/20260911-run-a", angle=4, brand=5, claims=5, publish=4,
                           by="caleb@peaksaunas.com", note="looks good", page="article")
    evals_mod.record_score(TENANT, run_dir="/x/20260911-run-a", angle=3, brand=4, claims=5, publish=3,
                           by="michael@peaksaunas.com")
    scores = evals_mod.load_scores(TENANT)
    assert len(scores) == 2
    assert scores[0]["page"] == "article"
    assert "page" not in scores[1]  # the run-level score keeps the old shape
    assert scores[1]["by"] == "michael@peaksaunas.com"


def test_load_scores_missing_file_and_malformed_lines(monkeypatch, tmp_path):
    monkeypatch.setattr(type(TENANT), "evals_path", property(lambda self: tmp_path / "scores.jsonl"))
    assert evals_mod.load_scores(TENANT) == []
    (tmp_path / "scores.jsonl").write_text('{"angle": 4}\nnot json\n{"angle": 5}\n')
    assert [s["angle"] for s in evals_mod.load_scores(TENANT)] == [4, 5]


# ---------------------------------------------------------------------------
# gate-history parsing and content versions
# ---------------------------------------------------------------------------

def test_parse_gate_history_reads_the_review_md_table():
    text = """## Gate history

| Cartridge | Attempts | Failures per attempt | Deterministic fixes | Result |
|---|---|---|---|---|
| article | 2 | attempt 1: 2 failure(s); attempt 2: 0 failure(s) | attempt 1: 1; attempt 2: 0 | PASS |
| longform | 1 | attempt 1: 0 failure(s) | attempt 1: 0 | PASS |
"""
    history = evals_mod.parse_gate_history(text)
    assert history["article"] == {"attempts": [2, 0], "deterministic_fixes": [1, 0], "result": "PASS"}
    assert history["longform"] == {"attempts": [0], "deterministic_fixes": [0], "result": "PASS"}


def test_cartridge_and_block_versions_are_content_hashes():
    v1 = evals_mod.cartridge_version("article")
    assert v1.startswith("sha256:")
    assert evals_mod.cartridge_version("article") == v1  # deterministic
    assert evals_mod.cartridge_version("longform") != v1
    bv = evals_mod.block_version("proof-stat-row")
    assert bv and bv.startswith("sha256:")
    assert evals_mod.block_version("not-a-block") is None


# ---------------------------------------------------------------------------
# dataset export
# ---------------------------------------------------------------------------

def test_dataset_export_writes_one_record_per_run_cartridge(monkeypatch, tmp_path):
    run_dir = _fake_run()
    monkeypatch.setattr(type(TENANT), "evals_path", property(lambda self: tmp_path / "scores.jsonl"))
    evals_mod.record_score(TENANT, run_dir=run_dir, angle=4, brand=5, claims=5, publish=4,
                           by="caleb@peaksaunas.com", page="article")

    out = tmp_path / "dataset.jsonl"
    count, written = evals_mod.export_dataset(TENANT, out_path=out)
    assert written == out
    assert count >= 3

    records = [json.loads(line) for line in out.read_text().splitlines()]
    mine = [r for r in records if r["run_id"] == run_dir.name]
    assert {r["cartridge"] for r in mine} == {"article", "product-page", "longform"}

    article = next(r for r in mine if r["cartridge"] == "article")
    assert article["input"] == "founder-warranty-demo.txt"
    assert article["angle"].startswith("A founder")
    assert article["state"] == "needs_review"
    assert article["facts_pack_summary"]["product"].startswith("peak-saunas-fuji")
    assert article["cartridge_version"].startswith("sha256:")
    assert article["page"]["headline"]
    assert article["gate_history"]["result"] == "PASS"
    assert article["check_results"]["image_allowlist"] == []
    assert article["check_results"]["html_validity"] == []
    assert article["check_results"]["json_ld"] == []
    # the human score joined by run id + page
    assert len(article["scores"]) == 1
    assert article["scores"][0]["publish"] == 4
    # the score did not leak onto the run's other pages
    longform = next(r for r in mine if r["cartridge"] == "longform")
    assert longform["scores"] == []
    # longform's declared proof slot resolves to its default block
    assert longform["blocks"] == {"proof": "proof-stat-row"}
    assert longform["block_versions"]["proof-stat-row"].startswith("sha256:")


def test_dataset_export_handles_an_empty_tenant(tmp_path):
    class _EmptyTenant:
        name = "empty"
        out_dir = tmp_path / "out"
        evals_path = tmp_path / "evals" / "scores.jsonl"

    count, _ = evals_mod.export_dataset(_EmptyTenant(), out_path=tmp_path / "out.jsonl")
    assert count == 0


# ---------------------------------------------------------------------------
# eval report
# ---------------------------------------------------------------------------

def test_eval_report_handles_no_scores_cleanly(monkeypatch, tmp_path):
    monkeypatch.setattr(type(TENANT), "evals_path", property(lambda self: tmp_path / "scores.jsonl"))
    report = evals_mod.build_report(TENANT)
    assert report["scores_total"] == 0
    text = evals_mod.format_report(report)
    assert "No human scores recorded yet" in text


def test_eval_report_aggregates_by_cartridge_angle_and_reviewer(monkeypatch, tmp_path):
    run_dir = _fake_run()
    monkeypatch.setattr(type(TENANT), "evals_path", property(lambda self: tmp_path / "scores.jsonl"))
    evals_mod.record_score(TENANT, run_dir=run_dir, angle=4, brand=5, claims=5, publish=4,
                           by="caleb@peaksaunas.com", page="article")
    evals_mod.record_score(TENANT, run_dir=run_dir, angle=5, brand=5, claims=5, publish=5,
                           by="michael@peaksaunas.com", page="article")
    evals_mod.record_score(TENANT, run_dir=run_dir, angle=3, brand=4, claims=5, publish=3,
                           by="caleb@peaksaunas.com", page="longform")

    report = evals_mod.build_report(TENANT)
    assert report["scores_total"] == 3
    assert report["by_cartridge"]["article"]["n"] == 2
    assert report["by_cartridge"]["article"]["publish"] == 4.5
    assert report["by_cartridge"]["article"]["publish_bar_met"] is True
    assert report["by_cartridge"]["longform"]["publish_bar_met"] is False
    assert report["by_reviewer"]["caleb@peaksaunas.com"]["n"] == 2
    # both pages carry the run's angle, so the angle bucket sees all three scores
    angle_key = next(iter(report["by_angle"]))
    assert "founder" in angle_key.lower()
    assert report["by_angle"][angle_key]["n"] == 3
    # longform's default proof block picks up the longform score
    assert report["by_block"]["proof-stat-row"]["n"] == 1

    text = evals_mod.format_report(report)
    assert "## By cartridge" in text
    assert "## By reviewer" in text


# ---------------------------------------------------------------------------
# CLI wiring
# ---------------------------------------------------------------------------

def test_cli_dataset_export_and_eval_report(monkeypatch, tmp_path, capsys):
    _fake_run()
    monkeypatch.setattr(type(TENANT), "evals_path", property(lambda self: tmp_path / "scores.jsonl"))
    assert cli.main(["dataset", "export", "--tenant", "peak-saunas", "--out", str(tmp_path / "d.jsonl")]) == 0
    assert (tmp_path / "d.jsonl").exists()
    assert cli.main(["eval", "report", "--tenant", "peak-saunas"]) == 0
    assert "Eval report" in capsys.readouterr().out
