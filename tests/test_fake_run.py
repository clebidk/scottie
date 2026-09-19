"""evals/fake_run.py is the baseline-capture driver for the long-run plan
(docs/KIMI-LONG-RUN.md phases 0/1/4): the suite's own dry run as a standalone
command. These tests pin that it keeps working and that its fakes cannot leak
into the rest of the suite."""
import json

import pytest

from harness import cli, listicle, pipeline
from harness.repair import count_words
from evals import fake_run
from tests.support import REPO_ROOT, TENANT

FIXTURE = TENANT.fixtures_dir / "founder-warranty-demo.txt"
HIDDEN_COSTS_FIXTURE = TENANT.fixtures_dir / "hidden-costs-v2.transcript.txt"
BASELINE_DIR = REPO_ROOT / "evals" / "baseline"


def test_fake_run_founder_fixture_produces_three_pages(tmp_path):
    exit_code = fake_run.main([str(FIXTURE), "--tenant", "peak-saunas", "--baseline-dir", str(tmp_path)])
    assert exit_code == 0

    for cartridge in ("article", "product-page", "longform"):
        baseline = tmp_path / f"{cartridge}.page.json"
        assert baseline.exists(), f"missing {baseline}"
        json.loads(baseline.read_text())

    manifest = json.loads((tmp_path / "manifest.json").read_text())
    assert manifest["fixture"] == FIXTURE.name
    assert manifest["tenant"] == "peak-saunas"
    assert manifest["cartridges"] == ["article", "product-page", "longform"]


def test_fake_run_restores_everything_it_patches(tmp_path):
    real_make_client = cli.make_client
    real_refresh = pipeline.refresh_price_data
    real_fetch_reviews = pipeline.fetch_reviews_claim
    real_http_fetch = pipeline.http_fetch_bytes
    real_drive_download = pipeline.download_drive_file

    assert fake_run.main([str(FIXTURE), "--tenant", "peak-saunas"]) == 0

    assert cli.make_client is real_make_client
    assert pipeline.refresh_price_data is real_refresh
    assert pipeline.fetch_reviews_claim is real_fetch_reviews
    assert pipeline.http_fetch_bytes is real_http_fetch
    assert pipeline.download_drive_file is real_drive_download


def test_fake_run_refuses_a_cartridge_with_no_canned_page():
    assert fake_run.main([str(FIXTURE), "--tenant", "peak-saunas", "--cartridges", "not-a-cartridge"]) == 1


# ---------------------------------------------------------------------------
# Cycle 41: listicle v0.2 renders offline in every style, with no API key --
# the canned page is built per style (fake_run._listicle_page) and has to pass
# the same gates a real write does, including the style/headline-formula gate.
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("style", listicle.STYLES)
def test_fake_run_renders_listicle_v2_in_every_style(tmp_path, style):
    exit_code, run_dir, pages = fake_run.run_once(
        str(FIXTURE), tenant="peak-saunas", cartridges="listicle", style=style,
    )
    assert exit_code == 0
    assert [p.parent.name for p in pages] == ["listicle"]
    page = json.loads(pages[0].read_text())
    assert page["style"] == style
    assert 900 <= count_words(page) <= 1400
    html = (run_dir / "listicle" / "index.html").read_text()
    # the one CTA in its five places, the sticky bar, and the fit block
    assert html.count(">See the models<") == 5
    assert 'class="lst-sticky"' in html
    assert "Who this is for, and who it is not for" in html
    # the model picker comes from facts_pack.model_options, not the writer
    assert 'class="lst-models"' in html
    assert "model_options" not in page


def test_fake_run_listicle_style_defaults_deterministically_from_the_seed():
    expected = listicle.resolve_style(seed=42, tenant=TENANT)
    exit_code, _run_dir, pages = fake_run.run_once(
        str(FIXTURE), tenant="peak-saunas", cartridges="listicle", seed=42,
    )
    assert exit_code == 0
    assert json.loads(pages[0].read_text())["style"] == expected


def test_fake_run_byte_matches_the_committed_baseline():
    """The phase-4 parity harness, registered in phase 1: a fake-client run of
    each dry-run fixture must reproduce evals/baseline/<fixture>/ byte for
    byte. Any behavior-preserving refactor (R1/R2, R11, R23, ...) that
    changes a page.json byte is not behavior-preserving -- this fails."""
    for fixture, baseline_name in (
        (FIXTURE, "founder-warranty-demo"),
        (HIDDEN_COSTS_FIXTURE, "hidden-costs-v2-transcript"),
    ):
        assert fake_run.main([str(fixture), "--tenant", "peak-saunas"]) == 0
        run_dir = fake_run._newest_run_dir(TENANT.out_dir, f"*-{pipeline.slugify(str(fixture))}-*")
        baseline = BASELINE_DIR / baseline_name
        for cartridge in ("article", "product-page", "longform"):
            expected = baseline / f"{cartridge}.page.json"
            actual = run_dir / cartridge / "page.json"
            assert expected.exists(), f"missing baseline {expected} -- capture it via python -m evals.fake_run"
            assert actual.read_bytes() == expected.read_bytes(), (
                f"{baseline_name}/{cartridge}: page.json drifted from evals/baseline "
                f"(run dir: {run_dir})"
            )


def test_soak_runner_two_runs_writes_a_report(tmp_path):
    """evals/soak.py is the 200-generation dry-run driver; a two-run soak must
    produce the aggregate report with per-run records."""
    from evals import soak

    out = tmp_path / "soak.json"
    assert soak.main(["--runs", "2", "--batch-size", "1", "--tenant", "peak-saunas", "--out", str(out)]) == 0
    report = json.loads(out.read_text())
    assert report["aggregate"]["runs"] == 2
    assert report["aggregate"]["pages"] == 6
    assert report["aggregate"]["by_exit_code"] == {"0": 2}
    assert len(report["runs"]) == 2
    assert report["runs"][0]["gate_history"]["article"]["result"] == "PASS"
