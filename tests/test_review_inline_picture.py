from harness.review import inline_assets_as_data_uris


def test_review_inliner_drops_picture_sources_and_srcset(tmp_path):
    assets = tmp_path / "assets"
    assets.mkdir()
    (assets / "hero-1200.jpg").write_bytes(b"\xff\xd8\xff\xdbJPEG")
    html = (
        '<picture><source type="image/webp" srcset="assets/hero-800.webp 800w, assets/hero-1200.webp 1200w" sizes="100vw">'
        '<img src="assets/hero-1200.jpg" srcset="assets/hero-480.jpg 480w, assets/hero-1200.jpg 1200w" sizes="100vw" width="1200" height="800" alt="hero"></picture>'
    )
    out = inline_assets_as_data_uris(html, assets)
    assert "<source" not in out
    assert "srcset=" not in out and "sizes=" not in out
    assert 'src="data:image/jpeg;base64,' in out
    assert 'width="1200" height="800" alt="hero"' in out
