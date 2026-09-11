"""Fix cycle 23: the renderer used to load EITHER a tenant's brand/base.css
OR harness/fallback.css, never both -- so a tenant stylesheet that never
defines .adv-cta/.adv-sticky-cta/.adv-financing/image sizing (as a real
tenant's own base.css doesn't -- it styles only its own theme classes) left
every cartridge template's structural classes with no matching CSS rule at
all (21 of 23 swept pages had unsized images; every longform page's sticky
CTA bar had no CSS at all). Now harness/structure.css always loads first,
and the tenant's base.css (if any) loads second as an override layer.
"""
from io import BytesIO

from PIL import Image

from harness.render import load_structure_css, load_tenant_css, render_page
from tests.support import REPO_ROOT
from tests.test_render import AD_BRIEF, ARTICLE_PAGE, FACTS_PACK


def _make_image_bytes(width, height, fmt="PNG", color=(10, 20, 30)):
    img = Image.new("RGB", (width, height), color=color)
    buf = BytesIO()
    img.save(buf, format=fmt)
    return buf.getvalue()


# ---------------------------------------------------------------------------
# load_structure_css / load_tenant_css
# ---------------------------------------------------------------------------


def test_load_structure_css_defines_the_structural_classes():
    css = load_structure_css()
    for cls in (".adv-cta", ".adv-sticky-cta", ".adv-financing"):
        assert cls in css
    assert "max-width: 100%" in css or "max-width:100%" in css


def test_load_tenant_css_is_empty_when_no_brand_dir(tmp_path):
    assert load_tenant_css(tmp_path / "no-such-brand") == ""


def test_load_tenant_css_is_empty_when_base_css_is_blank(tmp_path):
    brand_dir = tmp_path / "brand"
    brand_dir.mkdir()
    (brand_dir / "base.css").write_text("   \n")
    assert load_tenant_css(brand_dir) == ""


def test_load_tenant_css_returns_the_tenants_own_file(tmp_path):
    brand_dir = tmp_path / "brand"
    brand_dir.mkdir()
    (brand_dir / "base.css").write_text(".btn-primary{color:red}")
    assert load_tenant_css(brand_dir) == ".btn-primary{color:red}"


# ---------------------------------------------------------------------------
# render_page: ordering and override behaviour
# ---------------------------------------------------------------------------


def _render(tmp_path, brand_dir, **overrides):
    kwargs = dict(
        cartridge_name="article",
        page=ARTICLE_PAGE,
        ad_brief=AD_BRIEF,
        facts_pack=FACTS_PACK,
        cartridges_dir=REPO_ROOT / "cartridges",
        brand_dir=brand_dir,
        templates_dir=REPO_ROOT / "harness" / "templates",
        out_dir=tmp_path / "article",
        published="2026-09-10",
        updated="2026-09-10",
        download_assets=False,
    )
    kwargs.update(overrides)
    return render_page(**kwargs).read_text()


def test_structure_css_is_inlined_even_with_no_tenant_css(tmp_path):
    html = _render(tmp_path, tmp_path / "brand-does-not-exist")
    assert ".adv-cta" in html
    assert ".adv-sticky-cta" in html


def test_tenant_css_loads_after_structure_css_as_an_override(tmp_path):
    brand_dir = tmp_path / "brand"
    brand_dir.mkdir()
    (brand_dir / "base.css").write_text(".btn-primary{color:red} /* TENANT_MARKER */")
    html = _render(tmp_path, brand_dir)

    structure_pos = html.index(".adv-wrap")  # a rule only structure.css defines
    tenant_pos = html.index("TENANT_MARKER")
    assert structure_pos < tenant_pos, "structure.css must precede the tenant stylesheet"
    assert ".btn-primary" in html


def test_a_tenant_stylesheet_that_never_touches_adv_classes_still_gets_them(tmp_path):
    """The exact cycle-23 bug: a real tenant's base.css styles only its own
    theme classes, never redefining .adv-cta et al -- those must still come
    from structure.css."""
    brand_dir = tmp_path / "brand"
    brand_dir.mkdir()
    (brand_dir / "base.css").write_text(".container{max-width:1320px} .btn-primary{color:green}")
    html = _render(tmp_path, brand_dir)
    assert ".adv-cta {" in html or ".adv-cta{" in html
    assert ".adv-sticky-cta" in html
    assert ".container{max-width:1320px}" in html


# ---------------------------------------------------------------------------
# Image width/height attributes
# ---------------------------------------------------------------------------


def test_render_page_sets_img_width_and_height_when_the_renderer_knows_them(tmp_path):
    image_bytes = _make_image_bytes(400, 300)

    html = _render(
        tmp_path,
        tmp_path / "brand-does-not-exist",
        download_assets=True,
        fetch_url=lambda url: image_bytes,
    )
    assert 'width="400"' in html
    assert 'height="300"' in html
