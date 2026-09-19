"""Cycle 46: evals/soak.py may delete only the run directory it created."""
import json
from pathlib import Path

from evals import soak
from tests.support import TENANT


def test_soak_never_deletes_a_pre_existing_out_entry(tmp_path):
    out_dir = TENANT.out_dir  # isolated by the autouse fixture
    out_dir.mkdir(parents=True, exist_ok=True)
    archive = out_dir / "_archive-precious"
    archive.mkdir()
    (archive / "keep.txt").write_text("do not delete")
    older_run = out_dir / "20260101-000000-older-run-abcd"
    older_run.mkdir()
    (older_run / "state.json").write_text("{}")
    # make the archive the most recently modified entry, the exact trap
    archive.touch()
    report = tmp_path / "soak.json"
    assert soak.main(["--runs", "1", "--batch-size", "1", "--tenant", "peak-saunas", "--out", str(report)]) == 0
    assert (archive / "keep.txt").exists()
    assert older_run.exists()
    data = json.loads(report.read_text())
    assert data["runs"][0]["exit_code"] == 0
    # its own run dir was cleaned up (keep_runs is off by default)
    leftovers = [p for p in out_dir.iterdir() if p not in {archive, older_run}]
    assert leftovers == []
