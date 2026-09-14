"""Cycle 35b: the review route rebuilds a stale review file that still
carries <picture> <source> candidates (built before the inliner fix)."""
from harness.review import inline_assets_as_data_uris


def test_stale_review_markup_is_detectable_and_rebuild_output_is_clean(tmp_path):
    assets = tmp_path / "assets"
    assets.mkdir()
    (assets / "a-1200.jpg").write_bytes(b"\xff\xd8\xff\xdbJPEG")
    stale = '<picture><source srcset="assets/a-800.webp 800w"><img src="assets/a-1200.jpg" srcset="assets/a-480.jpg 480w"></picture>'
    assert "<source" in stale  # what serve.page_review keys on
    rebuilt = inline_assets_as_data_uris(stale, assets)
    assert "<source" not in rebuilt and "srcset=" not in rebuilt
    assert 'src="data:image/jpeg;base64,' in rebuilt
