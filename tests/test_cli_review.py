"""fix cycle 3 item 8: `adv review <run-dir>` writes one self-contained
<cartridge>-review.html per cartridge, with every asset image inlined as a
data URI (regex on src="assets/...") -- for sending to Caleb without a
folder of loose image files."""
import argparse
import base64

from adv import cli
from adv.cli import count_words


def _make_run_dir(tmp_path):
    run_dir = tmp_path / "20260909-0000-test-run"
    for cartridge in ("article", "product-page"):
        cart_dir = run_dir / cartridge
        (cart_dir / "assets").mkdir(parents=True)
        (cart_dir / "assets" / "hero.png").write_bytes(b"\x89PNG\r\n\x1a\nfake-png-bytes")
        (cart_dir / "index.html").write_text(
            f'<html><body><img src="assets/hero.png" alt="{cartridge} hero"></body></html>'
        )
    return run_dir


def test_cmd_review_writes_one_review_html_per_cartridge(tmp_path):
    run_dir = _make_run_dir(tmp_path)
    exit_code = cli.cmd_review(argparse.Namespace(run_dir=str(run_dir)))
    assert exit_code == 0
    assert (run_dir / "article-review.html").exists()
    assert (run_dir / "product-page-review.html").exists()


def test_cmd_review_inlines_asset_as_data_uri(tmp_path):
    run_dir = _make_run_dir(tmp_path)
    cli.cmd_review(argparse.Namespace(run_dir=str(run_dir)))
    review_html = (run_dir / "article-review.html").read_text()
    assert 'src="assets/' not in review_html
    expected_b64 = base64.b64encode(b"\x89PNG\r\n\x1a\nfake-png-bytes").decode("ascii")
    assert f"data:image/png;base64,{expected_b64}" in review_html


def test_cmd_review_returns_1_for_missing_run_dir(tmp_path):
    exit_code = cli.cmd_review(argparse.Namespace(run_dir=str(tmp_path / "does-not-exist")))
    assert exit_code == 1


def test_cmd_review_returns_1_when_no_cartridge_output_found(tmp_path):
    run_dir = tmp_path / "empty-run"
    run_dir.mkdir()
    exit_code = cli.cmd_review(argparse.Namespace(run_dir=str(run_dir)))
    assert exit_code == 1


# ---------------------------------------------------------------------------
# fix cycle 3 item 7: product-page's 250-500 word budget is a soft check --
# a REVIEW.md warning line, never a run failure.
# ---------------------------------------------------------------------------

def test_write_review_md_warns_when_product_page_is_outside_word_budget(tmp_path):
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    short_page = {"hero": {"promise": "Too short."}}
    cli.write_review_md(
        run_dir,
        ad_brief={"angle": "a"},
        facts_pack={"verified_claims": []},
        product_name="Fuji",
        selected=["product-page"],
        pages={"product-page": short_page},
        budget=type("B", (), {"summary": lambda self: {}})(),
        cost=0.0,
        gate_matched=[],
    )
    review_md = (run_dir / "REVIEW.md").read_text()
    assert "WARNING: product-page is" in review_md
    assert "250-500" in review_md


def test_count_words_excludes_top_level_cta_url():
    # Regression: fix cycle 3 item 4's flattened "cta_url" field must stay
    # excluded from the word count the same way the old nested "cta.url" was.
    page = {"hero": {"promise": "one two three"}, "cta_text": "Shop Fuji", "cta_url": "https://peaksaunas.com/products/fuji"}
    assert count_words(page) == 3 + 2  # "one two three" + "Shop Fuji"; cta_url not counted


def test_write_review_md_no_warning_when_within_word_budget(tmp_path):
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    words = " ".join(["word"] * 300)
    ok_page = {"hero": {"promise": words}}
    cli.write_review_md(
        run_dir,
        ad_brief={"angle": "a"},
        facts_pack={"verified_claims": []},
        product_name="Fuji",
        selected=["product-page"],
        pages={"product-page": ok_page},
        budget=type("B", (), {"summary": lambda self: {}})(),
        cost=0.0,
        gate_matched=[],
    )
    review_md = (run_dir / "REVIEW.md").read_text()
    assert "WARNING" not in review_md
