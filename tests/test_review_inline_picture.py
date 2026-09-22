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


def test_review_inliner_forces_eager_loading_on_thumbnails_and_hidden_slides(tmp_path):
    # Fix cycle 58 item 2: the pdp gallery's hidden slides (display:none
    # until their thumbnail is clicked) and every thumbnail get
    # render_image_slot's loading="lazy" -- correct for the storefront,
    # where it's a real network fetch to defer. In a review file every
    # image is already inlined as a data: URI (no fetch to defer), but
    # `loading="lazy"` survives inlining untouched, so the browser's
    # native lazy-loading still gates *painting* the image on visibility --
    # a hidden slide never has a box for that heuristic to consider "near
    # the viewport", so it can be left showing the plain gray `.adv-img`
    # placeholder background (harness/structure.css) instead of the photo.
    # The inliner now forces every image to loading="eager" too. The
    # storefront path is untouched: render_image_slot (harness/render.py)
    # itself still emits loading="lazy" for a non-hero slot -- see
    # test_render_image_slot_non_hero_is_lazy_with_no_fetchpriority in
    # tests/test_images_cycle31.py -- and this function is only ever
    # called for the review file (harness/cli.py's cmd_review,
    # harness/serve.py's page_review), never for index.html/
    # shopify-body.html.
    assets = tmp_path / "assets"
    assets.mkdir()
    (assets / "slide-2-800.jpg").write_bytes(b"\xff\xd8\xff\xdbJPEG")
    (assets / "thumb-2-120.jpg").write_bytes(b"\xff\xd8\xff\xdbJPEG")
    html = (
        '<figure class="pp-slide" data-pp-slide="1" style="display:none">'
        '<picture><img src="assets/slide-2-800.jpg" alt="slide 2" loading="lazy"></picture></figure>'
        '<button class="pp-thumb" data-pp-thumb="1">'
        '<picture><img src="assets/thumb-2-120.jpg" alt="thumb 2" loading="lazy"></picture></button>'
    )
    out = inline_assets_as_data_uris(html, assets)
    assert 'loading="lazy"' not in out
    assert out.count('loading="eager"') == 2
    assert out.count('src="data:image/jpeg;base64,') == 2
