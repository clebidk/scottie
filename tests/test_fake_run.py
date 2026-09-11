"""evals/fake_run.py is the baseline-capture driver for the long-run plan
(docs/KIMI-LONG-RUN.md phases 0/1/4): the suite's own dry run as a standalone
command. These tests pin that it keeps working and that its fakes cannot leak
into the rest of the suite."""
import json

from harness import cli, pipeline
from evals import fake_run
from tests.support import TENANT

FIXTURE = TENANT.fixtures_dir / "founder-warranty-demo.txt"


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
    assert fake_run.main([str(FIXTURE), "--tenant", "peak-saunas", "--cartridges", "listicle"]) == 1
