"""The live publish verifies the storefront by pulling the page up to 8
times and looking for the run id (cli.cmd_publish -> verify_cache(marker=
run_dir.name)). The export never carried the run id, so every publish
reported 0/8 even when the new body was live (seen 2026-09-23)."""
from harness import page_body


def test_exported_body_carries_the_run_id_the_cache_check_looks_for(tmp_path):
    cartridge_dir = tmp_path / "20260923-000237-hidden-costs-v2-spea" / "listicle"
    cartridge_dir.mkdir(parents=True)
    (cartridge_dir / "index.html").write_text(
        "<!doctype html><html><head><title>t</title></head>"
        "<body><main class=\"adv-wrap\"><p>x</p></main></body></html>"
    )
    body, _ = page_body.build_shopify_body(cartridge_dir)
    assert 'data-pk-run="20260923-000237-hidden-costs-v2-spea"' in body
