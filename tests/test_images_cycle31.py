"""Cycle 31 (docs/IMAGES-AUDIT-2026-09-14.md / docs/IMAGES.md): image
selection, markup, and delivery. Grouped separately from tests/test_ground.py
and tests/test_render.py's own pre-existing coverage since this file exists
specifically to prove the audit's ten problems are fixed and stay fixed.
"""
import io

import pytest
from PIL import Image

from harness import ground, pagechecks
from harness.render import (
    detect_near_white_border,
    generate_image_variants,
    render_image_slot,
)
from harness.review import REVIEW_HTML_MAX_BYTES, inline_assets_as_data_uris

# ---------------------------------------------------------------------------
# ground.py: hero/section slot plan and selection-policy enforcement
# ---------------------------------------------------------------------------

ASSETS = [
    {"id": "logo-1", "kind": "logo", "url": "https://x/logo.png"},
    {"id": "shopify-1", "kind": "image", "url": "https://x/1.png"},
    {"id": "shopify-2", "kind": "image", "url": "https://x/2.png"},
    {"id": "lifestyle-1", "kind": "lifestyle", "url": "https://x/l1.jpg"},
    {"id": "installation-1", "kind": "installation", "url": "https://x/i1.jpg"},
    {"id": "render-1", "kind": "render", "url": "https://x/r1.jpg"},
    {"id": "ai-1", "kind": "ai_render", "ai_generated": True, "url": "https://x/ai1.jpg"},
]


def test_pick_hero_prefers_a_real_photo_over_the_plain_shopify_shot():
    hero = ground.pick_hero(ASSETS, allow_ai_renders=False)
    assert hero["kind"] in ("lifestyle", "interior", "installation")


def test_pick_hero_never_picks_a_logo_or_ai_render_by_default():
    only_bad = [ASSETS[0], ASSETS[-1]]  # logo, ai_render
    hero = ground.pick_hero(only_bad, allow_ai_renders=False)
    assert hero is None


def test_pick_hero_allows_ai_render_only_when_tenant_allows_and_no_photo_exists():
    only_shopify_and_ai = [a for a in ASSETS if a["kind"] in ("image", "ai_render")]
    # a plain Shopify photo exists -- ai_render must not be picked even with allow_ai_renders
    hero = ground.pick_hero(only_shopify_and_ai, allow_ai_renders=True)
    assert hero["kind"] == "image"
    # no photo at all -- ai_render is the only thing left, and only eligible when allowed
    only_ai = [a for a in ASSETS if a["kind"] == "ai_render"]
    assert ground.pick_hero(only_ai, allow_ai_renders=False) is None
    assert ground.pick_hero(only_ai, allow_ai_renders=True)["kind"] == "ai_render"


def test_pick_hero_falls_back_to_shopify_when_no_real_photo_available():
    pool = [a for a in ASSETS if a["kind"] in ("logo", "image")]
    hero = ground.pick_hero(pool, allow_ai_renders=False)
    assert hero["kind"] == "image"


def test_pick_section_images_never_repeats_and_skips_excluded():
    picks = ground.pick_section_images(ASSETS, count=10, allow_ai_renders=False, exclude_ids={"shopify-1"})
    ids = [a["id"] for a in picks]
    assert len(ids) == len(set(ids)), "no repeats"
    assert "shopify-1" not in ids
    assert "logo-1" not in ids and "ai-1" not in ids


def test_build_slot_plan_hero_never_repeated_in_sections():
    plan = ground.build_slot_plan(ASSETS, allow_ai_renders=False)
    assert plan["hero"] is not None
    assert plan["hero"]["id"] not in {a["id"] for a in plan["sections"]}


def _page_with_hero(hero_asset_id):
    return {
        "hero": {"headline": "h", "hero_image": {"asset_id": hero_asset_id}},
        "how_it_works": {"steps": []},
    }


def test_enforce_slot_plan_swaps_a_disallowed_hero():
    page = _page_with_hero("logo-1")
    used = ground.enforce_slot_plan(page, ASSETS, "longform", allow_ai_renders=False)
    new_hero = page["hero"]["hero_image"]["asset_id"]
    assert new_hero != "logo-1"
    assert ground.hero_container(page, "longform")["asset_id"] in used


def test_enforce_slot_plan_dedupes_within_one_page():
    page = {
        "hero": {"hero_image": {"asset_id": "lifestyle-1"}},
        "how_it_works": {"steps": [{"image": {"asset_id": "lifestyle-1"}}]},
    }
    ground.enforce_slot_plan(page, ASSETS, "longform", allow_ai_renders=False)
    hero_id = page["hero"]["hero_image"]["asset_id"]
    step_id = page["how_it_works"]["steps"][0]["image"]["asset_id"]
    assert hero_id != step_id


def test_enforce_slot_plan_honors_cross_cartridge_exclude_ids():
    page = _page_with_hero("lifestyle-1")
    ground.enforce_slot_plan(page, ASSETS, "product-page", allow_ai_renders=False, exclude_ids={"lifestyle-1"})
    assert page["hero"]["hero_image"]["asset_id"] != "lifestyle-1"


def test_enforce_slot_plan_skips_longforms_own_images_convenience_index():
    """cartridges/longform/schema.json documents "images" as a convenience
    index of ids used elsewhere -- it must not be treated as a slot that
    needs a distinct asset from the hero."""
    page = _page_with_hero("lifestyle-1")
    page["images"] = [{"asset_id": "lifestyle-1"}]
    ground.enforce_slot_plan(page, ASSETS, "longform", allow_ai_renders=False)
    assert page["images"][0]["asset_id"] == "lifestyle-1"
    assert page["hero"]["hero_image"]["asset_id"] == "lifestyle-1"


def test_record_and_load_used_asset_ids_roundtrip(tmp_path):
    ground.record_used_asset_ids(tmp_path, "article", ["a", "b"])
    ground.record_used_asset_ids(tmp_path, "longform", ["b", "c"])
    assert ground.all_used_asset_ids(tmp_path, exclude_cartridge="longform") == {"a", "b"}
    assert ground.all_used_asset_ids(tmp_path) == {"a", "b", "c"}


# ---------------------------------------------------------------------------
# render_image_slot: markup contract
# ---------------------------------------------------------------------------


def _asset(**overrides):
    base = {
        "id": "a1", "url": "assets/a1-1200.jpg", "alt": "Peak Fuji – product photo",
        "width": 1200, "height": 900, "aspect": "4x3",
        "variants": [
            {"width": 480, "jpg": "assets/a1-480.jpg", "webp": "assets/a1-480.webp"},
            {"width": 1200, "jpg": "assets/a1-1200.jpg", "webp": "assets/a1-1200.webp"},
        ],
    }
    base.update(overrides)
    return base


def test_render_image_slot_hero_gets_eager_and_fetchpriority():
    html = str(render_image_slot(_asset(), hero=True))
    assert 'loading="eager"' in html
    assert 'fetchpriority="high"' in html
    assert 'decoding="async"' in html
    assert 'width="1200"' in html and 'height="900"' in html


def test_render_image_slot_non_hero_is_lazy_with_no_fetchpriority():
    html = str(render_image_slot(_asset()))
    assert 'loading="lazy"' in html
    assert "fetchpriority" not in html


def test_render_image_slot_wraps_in_picture_when_webp_variants_present():
    html = str(render_image_slot(_asset()))
    assert html.startswith("<picture>")
    assert 'type="image/webp"' in html
    assert "assets/a1-480.webp" in html and "assets/a1-1200.webp" in html
    # fallback <img> src is the 1200 variant, not the largest by default
    assert 'src="assets/a1-1200.jpg"' in html


def test_render_image_slot_no_picture_wrapper_without_webp_variants():
    asset = _asset(variants=[{"width": 480, "jpg": "assets/a1-480.jpg", "webp": None}])
    html = str(render_image_slot(asset))
    assert "<picture>" not in html
    assert html.startswith("<img")


def test_render_image_slot_aspect_class_from_asset():
    assert "adv-img--1x1" in str(render_image_slot(_asset(aspect="1x1")))
    assert "adv-img--4x3" in str(render_image_slot(_asset(aspect="4x3")))


def test_render_image_slot_aspect_box_false_omits_aspect_class():
    html = str(render_image_slot(_asset(), aspect_box=False))
    assert "adv-img--4x3" not in html and "adv-img--1x1" not in html


def test_render_image_slot_caption_wraps_in_figure():
    html = str(render_image_slot(_asset(), caption="A real caption"))
    assert html.startswith("<figure>")
    assert "<figcaption>A real caption</figcaption>" in html


def test_render_image_slot_no_asset_renders_nothing():
    assert str(render_image_slot(None)) == ""


def test_render_image_slot_escapes_alt_and_caption():
    html = str(render_image_slot(_asset(alt='"><script>x</script>'), caption="<b>hi</b>"))
    assert "<script>" not in html
    assert "<b>hi</b>" not in html  # escaped, not rendered as a tag


# ---------------------------------------------------------------------------
# Aspect detection and srcset/WebP variant generation
# ---------------------------------------------------------------------------


def _image_bytes(width, height, color, fmt="PNG"):
    buf = io.BytesIO()
    Image.new("RGB", (width, height), color=color).save(buf, format=fmt)
    return buf.getvalue()


def test_detect_near_white_border_true_for_a_white_background_cutout():
    img = Image.new("RGB", (400, 400), color=(255, 255, 255))
    assert detect_near_white_border(img) is True


def test_detect_near_white_border_false_for_a_colorful_photo():
    img = Image.new("RGB", (400, 400), color=(30, 90, 140))
    assert detect_near_white_border(img) is False


def test_generate_image_variants_never_upscales_past_source_width(tmp_path):
    data = _image_bytes(300, 300, (10, 10, 10))
    result = generate_image_variants(data, tmp_path, "small")
    assert all(v["width"] <= 300 for v in result["variants"])
    assert result["variants"], "a narrower-than-smallest-width source still gets one variant"


def test_generate_image_variants_produces_multiple_widths_for_a_large_source(tmp_path):
    data = _image_bytes(2000, 2000, (10, 10, 10))
    result = generate_image_variants(data, tmp_path, "big")
    widths = {v["width"] for v in result["variants"]}
    assert widths == {480, 800, 1200, 1600}
    for v in result["variants"]:
        assert (tmp_path / v["jpg"].split("/")[-1]).exists()


def test_generate_image_variants_sets_aspect_from_near_white_detection(tmp_path):
    white = _image_bytes(600, 600, (255, 255, 255))
    result = generate_image_variants(white, tmp_path, "white")
    assert result["aspect"] == "1x1"
    colorful = _image_bytes(600, 450, (40, 80, 120))
    result2 = generate_image_variants(colorful, tmp_path / "b", "color")
    assert result2["aspect"] == "4x3"


def test_generate_image_variants_returns_empty_for_undecodable_bytes(tmp_path):
    result = generate_image_variants(b"not an image", tmp_path, "bad")
    assert result["variants"] == []
    assert result["width"] is None


# ---------------------------------------------------------------------------
# pagechecks: fire on bad markup / bad page.json
# ---------------------------------------------------------------------------


def test_find_image_markup_violations_flags_missing_width_height():
    html = '<img src="assets/a.jpg" alt="A real description">'
    problems = pagechecks.find_image_markup_violations(html)
    assert any("width/height" in p["issue"] for p in problems)


def test_find_image_markup_violations_flags_empty_and_filename_alt():
    html = (
        '<img src="assets/a.jpg" alt="" width="400" height="300">'
        '<img src="assets/b.jpg" alt="b.jpg" width="400" height="300">'
    )
    problems = pagechecks.find_image_markup_violations(html)
    assert len(problems) == 2
    assert any("empty" in p["issue"] for p in problems)
    assert any("filename" in p["issue"] for p in problems)


def test_find_image_markup_violations_flags_favicon_sized_image():
    html = '<img src="assets/a.jpg" alt="a real photo" width="64" height="64">'
    problems = pagechecks.find_image_markup_violations(html)
    assert any("favicon" in p["issue"] for p in problems)


def test_find_image_markup_violations_ignores_the_brand_logo():
    html = '<img src="assets/logo.png" alt="Peak logo" class="adv-brand-logo">'
    assert pagechecks.find_image_markup_violations(html) == []


def test_find_image_markup_violations_passes_a_well_formed_image():
    html = '<img src="assets/a.jpg" alt="Peak Fuji – lifestyle photo" width="1200" height="900" loading="lazy">'
    assert pagechecks.find_image_markup_violations(html) == []


def test_find_image_markup_violations_ignores_the_literal_text_img_in_a_comment():
    """Regression: a naive <img\\b[^>]*> regex also matches the literal
    text "<img>" inside an HTML comment (structure.css's own cycle-23
    note does this) -- caught during this cycle's own audit script."""
    html = "<!-- some old <img> tags had no width/height --><p>hi</p>"
    assert pagechecks.find_image_markup_violations(html) == []


def test_find_duplicate_asset_violations_flags_a_repeat():
    page = {"a": {"asset_id": "x"}, "b": {"asset_id": "x"}}
    problems = pagechecks.find_duplicate_asset_violations(page)
    assert len(problems) == 1


def test_find_duplicate_asset_violations_excludes_longforms_images_field():
    page = {"hero": {"hero_image": {"asset_id": "x"}}, "images": [{"asset_id": "x"}]}
    assert pagechecks.find_duplicate_asset_violations(page, "longform") == []
    # but the same shape on a different cartridge is still a real duplicate
    assert len(pagechecks.find_duplicate_asset_violations(page, "product-page")) == 1


def test_find_hero_requirement_violations_fires_when_hero_missing():
    problems = pagechecks.find_hero_requirement_violations({"hero": {}}, "longform")
    assert len(problems) == 1


def test_find_hero_requirement_violations_noop_for_article_and_listicle():
    assert pagechecks.find_hero_requirement_violations({}, "article") == []
    assert pagechecks.find_hero_requirement_violations({}, "listicle") == []


# ---------------------------------------------------------------------------
# review.py: picture/srcset inlining and the size cap
# ---------------------------------------------------------------------------


def test_inline_assets_as_data_uris_inlines_src_and_drops_srcset_and_sources(tmp_path):
    (tmp_path / "a-1200.jpg").write_bytes(_image_bytes(4, 4, (1, 2, 3), fmt="JPEG"))
    html = (
        '<picture><source type="image/webp" srcset="assets/a-480.webp 480w">'
        '<img src="assets/a-1200.jpg" srcset="assets/a-480.jpg 480w, assets/a-1200.jpg 1200w">'
        "</picture>"
    )
    out = inline_assets_as_data_uris(html, tmp_path)
    assert 'src="data:image/jpeg;base64,' in out
    # the srcset attributes (webp source and the img's own) are untouched --
    # a standalone browser can't resolve them anyway and falls through to
    # the inlined src, which is exactly the "inline only the 1200 variant" ask
    # Cycle 35b: browsers pick <source>/srcset candidates and never fall back to the
    # inlined src, so the review copy must carry no srcset and no <source> at all.
    assert 'srcset=' not in out
    assert '<source' not in out


def test_review_html_size_cap_constant_is_twelve_megabytes():
    assert REVIEW_HTML_MAX_BYTES == 12 * 1024 * 1024


def test_build_review_for_page_warns_but_does_not_raise_when_over_the_cap(tmp_path, capsys, monkeypatch):
    from harness import review as review_mod

    monkeypatch.setattr(review_mod, "REVIEW_HTML_MAX_BYTES", 10)  # trivially small, for the test
    run_dir = tmp_path / "run"
    cartridge_dir = run_dir / "listicle"
    cartridge_dir.mkdir(parents=True)
    (cartridge_dir / "index.html").write_text("<p>" + "x" * 100 + "</p>")
    path = review_mod.build_review_for_page(run_dir, "listicle")
    assert path is not None and path.exists()
    assert "over the" in capsys.readouterr().err


@pytest.mark.parametrize("hero_flag", [True, False])
def test_render_image_slot_sizes_attribute_only_present_with_srcset(hero_flag):
    with_srcset = str(render_image_slot(_asset(), hero=hero_flag))
    assert "sizes=" in with_srcset
    without_srcset = str(render_image_slot(_asset(variants=[]), hero=hero_flag))
    assert "sizes=" not in without_srcset
    assert "srcset=" not in without_srcset
