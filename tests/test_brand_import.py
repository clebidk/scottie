"""Cycle 27: `harness brand import`.

Covers: the public-folder HTML listing parser (against a small synthetic
sample, not the real network -- see tests/conftest.py's _no_network guarantee),
the not-public refusal message, the file classifier, the tokens.json/
tenant.yaml merge rule (existing wins unless --force, new keys always added,
every imported value source-tagged), WCAG contrast math, base.css generation,
and a demo-tenant end-to-end (local brand dir -> import -> render_page) that
confirms the logo and an imported color actually reach the rendered HTML.
"""
import json
import shutil

import pytest
from PIL import Image

from harness import brand_import, render
from harness import tenant as tenant_mod
from harness.sources import drive
from tests.test_render import AD_BRIEF, FACTS_PACK, PRODUCT_PAGE_PAGE


# ---------------------------------------------------------------------------
# Folder listing parser
# ---------------------------------------------------------------------------

# Shape verified against a real Drive folder (Cycle 27 server verification,
# see docs/FIXLOG.md): the file id lives inside an `ssk='<n>:<code>:<id>-<n>-<n>'`
# attribute, not `data-id`, and the same id repeats across a name row and
# several metadata rows ("Modified ...", "Size ...", "More actions").
def _entry_html(file_id, name, *, type_word="", status="Shared"):
    label = f"{name} {type_word} {status}".replace("  ", " ").strip()
    return (
        f'<div class="JxSEve" aria-label="{label}" '
        f"data-handled-by-drag-and-drop=\"true\" ssk='5:auSv138:{file_id}-0-16'></div>\n"
    )


def _meta_row_html(file_id, label):
    """A secondary row Drive renders for the same item (same id, a different
    aria-label) -- exercises "only the first name per id is kept"."""
    return f"<div class=\"i92Sbe\" aria-label=\"{label}\" ssk='6:by9fbe38:{file_id}-0-16'></div>\n"


SAMPLE_FOLDER_HTML = (
    "<html><body>"
    + _entry_html("1AAAAAAAAAAAAAAAAAAA", "Acme Logo.svg", type_word="Image")
    + _meta_row_html("1AAAAAAAAAAAAAAAAAAA", "Modified 3. Feb.")
    + _meta_row_html("1AAAAAAAAAAAAAAAAAAA", "More actions")
    + _entry_html("1BBBBBBBBBBBBBBBBBBB", "Acme Brand Guide.pdf", type_word="PDF")
    + _entry_html("1AAAAAAAAAAAAAAAAAAA", "Acme Logo.svg", type_word="Image")  # grid+list dup, same id
    + "</body></html>"
)

SIGNIN_WALL_HTML = "<html><head><title>Sign in - Google Accounts</title></head><body>...</body></html>"


def test_list_public_folder_parses_aria_label_and_ssk_id():
    entries = drive.list_public_folder("root-folder", fetch=lambda fid: SAMPLE_FOLDER_HTML)
    assert entries == [
        {"id": "1AAAAAAAAAAAAAAAAAAA", "name": "Acme Logo.svg"},
        {"id": "1BBBBBBBBBBBBBBBBBBB", "name": "Acme Brand Guide.pdf"},
    ]


def test_list_public_folder_dedupes_repeated_ids():
    ids = [e["id"] for e in drive.list_public_folder("root-folder", fetch=lambda fid: SAMPLE_FOLDER_HTML)]
    assert len(ids) == len(set(ids))


def test_list_public_folder_refuses_a_signin_wall_with_the_exact_message():
    with pytest.raises(drive.DriveFolderNotPublic) as exc_info:
        drive.list_public_folder("private-folder", fetch=lambda fid: SIGNIN_WALL_HTML)
    assert str(exc_info.value) == (
        "Drive folder is not link-public; share it as Anyone with the link, or "
        "upload the files into tenants/<t>/brand/incoming/ and rerun with --local"
    )


def test_list_public_folder_empty_but_not_a_signin_wall_is_just_empty():
    # A real public folder that happens to be empty must not be confused with
    # the not-public case -- only the sign-in-wall markers trigger a refusal.
    assert drive.list_public_folder("empty-folder", fetch=lambda fid: "<html><body></body></html>") == []


def test_enumerate_source_recurses_one_level_into_a_subfolder():
    root_id = "root-folder-id-0000001"
    sub_id = "sub-folder-id-00000001"
    root_html = _entry_html("root-file-id-000000001", "Acme Wordmark.png", type_word="Image") + _entry_html(
        sub_id, "Logo Files", type_word="", status="Shared folder"
    )
    sub_html = _entry_html("sub-file-id-0000000001", "acme-mark-alt.png")
    pages = {root_id: root_html, sub_id: sub_html}
    entries = brand_import.enumerate_source(drive_folder=root_id, fetch=lambda fid: pages[fid])
    names = {e["name"] for e in entries}
    assert names == {"acme-mark-alt.png", "Acme Wordmark.png"}


# ---------------------------------------------------------------------------
# Classifier
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("name,expected", [
    ("acme-logo.svg", "logo"),
    ("Wordmark.png", "logo"),
    ("favicon.png", "logo"),
    ("photo-of-mark-on-wall.jpg", "logo"),  # "mark" substring, per the task's own rule
    ("Acme Brand Guide.pdf", "guide"),
    ("style-guide.png", "guide"),
    ("random.pdf", "guide"),
    ("Font-Bold.ttf", "font"),
    ("Font-Regular.otf", "font"),
    ("Font-Regular.woff2", "font"),
    ("colors.json", "palette"),
    ("palette.txt", "palette"),
    ("Tokens.json", "palette"),
    ("outdoor-01.jpg", "photo"),
    ("interior-shot.png", "photo"),
    ("readme.docx", "other"),
    ("notes.txt", "other"),
])
def test_classify_file(name, expected):
    assert brand_import.classify_file(name) == expected


def test_looks_like_folder():
    assert brand_import.looks_like_folder("Logo Files")
    assert not brand_import.looks_like_folder("logo.svg")
    # A real extension this classifier has no bucket for (.ai/.eps design
    # source files, found in a real Drive folder during server verification)
    # must still read as a file, not a folder -- see choose_guide's sibling
    # fix in harness/brand_import.py for the incident this guards against.
    assert not brand_import.looks_like_folder("Acme blue on the left CMYK.ai")
    assert not brand_import.looks_like_folder("Acme blue on the left RGB.eps")


def test_choose_guide_prefers_a_name_that_says_guide_over_a_bigger_non_guide_pdf(tmp_path):
    # Regression for a real finding: a folder with two one-page color-variant
    # PDFs (whose names don't say "guide") listed before the actual,
    # comprehensive brand guide PDF used to make guide_entries[0] pick the
    # wrong one.
    small_variant = tmp_path / "Acme blue on the left.pdf"
    small_variant.write_bytes(b"x" * 100)
    other_variant = tmp_path / "Acme blue on the right.pdf"
    other_variant.write_bytes(b"x" * 100)
    real_guide = tmp_path / "Acme BRAND GUIDE.pdf"
    real_guide.write_bytes(b"x" * 10_000)
    entries = [
        {"name": small_variant.name, "path": small_variant},
        {"name": other_variant.name, "path": other_variant},
        {"name": real_guide.name, "path": real_guide},
    ]
    chosen = brand_import.choose_guide(entries)
    assert chosen["name"] == "Acme BRAND GUIDE.pdf"


def test_choose_logo_prefers_png_over_a_bigger_jpg(tmp_path):
    # Regression for a real finding: a big JPEG photo of the logo must not
    # beat a smaller, cleaner PNG just because it has more bytes.
    small_png = tmp_path / "favicon-logo.png"
    small_png.write_bytes(b"x" * 100)
    big_jpg = tmp_path / "logo-social-profile.jpg"
    big_jpg.write_bytes(b"x" * 50_000)
    assert brand_import.choose_logo([small_png, big_jpg]) == small_png


def test_choose_logo_prefers_svg_over_any_raster(tmp_path):
    svg = tmp_path / "logo.svg"
    svg.write_bytes(b"x" * 10)
    png = tmp_path / "logo.png"
    png.write_bytes(b"x" * 50_000)
    assert brand_import.choose_logo([svg, png]) == svg


def test_choose_guide_falls_back_to_largest_when_none_say_guide(tmp_path):
    small = tmp_path / "Acme style-a.pdf"
    small.write_bytes(b"x" * 100)
    big = tmp_path / "Acme style-b.pdf"
    big.write_bytes(b"x" * 5_000)
    chosen = brand_import.choose_guide([
        {"name": small.name, "path": small}, {"name": big.name, "path": big},
    ])
    assert chosen["name"] == "Acme style-b.pdf"


# ---------------------------------------------------------------------------
# Merge rules
# ---------------------------------------------------------------------------

def test_merge_existing_scalar_wins_by_default():
    existing = {"a": 1, "nested": {"x": "old"}}
    imported = {"a": 2, "nested": {"x": "new", "y": "added"}}
    merged = brand_import.merge_dict(existing, imported, force=False)
    assert merged == {"a": 1, "nested": {"x": "old", "y": "added"}}


def test_merge_force_overwrites():
    existing = {"a": 1, "nested": {"x": "old"}}
    imported = {"a": 2, "nested": {"x": "new"}}
    merged = brand_import.merge_dict(existing, imported, force=True)
    assert merged == {"a": 2, "nested": {"x": "new"}}


def test_merge_never_mutates_inputs():
    existing = {"a": {"b": 1}}
    imported = {"a": {"c": 2}}
    brand_import.merge_dict(existing, imported, force=False)
    assert existing == {"a": {"b": 1}}
    assert imported == {"a": {"c": 2}}


def test_imported_tokens_are_source_tagged(tmp_path):
    logo = tmp_path / "acme-logo.png"
    Image.new("RGB", (20, 20), (22, 196, 127)).save(logo)
    tenant = _make_demo_tenant(tmp_path, "srctag-co")
    brand_dir = tmp_path / "src"
    brand_dir.mkdir()
    shutil.copy(logo, brand_dir / "acme-logo.png")
    result = brand_import.import_brand_kit(tenant, local_dir=brand_dir, dry_run=False, force=False)
    tokens = json.loads((tenant.brand_dir / "tokens.json").read_text())
    for entry in tokens["brand_import"]["colors"].values():
        assert entry["source"].startswith("brand-import:")
    assert result.logo_path == "brand/logo.png"


def test_tokens_json_rerun_without_force_keeps_hand_edit(tmp_path):
    tenant = _make_demo_tenant(tmp_path, "rerun-co")
    tenant.brand_dir.mkdir(parents=True, exist_ok=True)
    (tenant.brand_dir / "tokens.json").write_text(json.dumps({
        "brand_import": {"colors": {"accent": {"hex": "#111111", "role": "accent", "source": "hand-edit"}}}
    }))
    logo_dir = tmp_path / "src"
    logo_dir.mkdir()
    Image.new("RGB", (20, 20), (17, 23, 43)).save(logo_dir / "acme-logo.png")
    brand_import.import_brand_kit(tenant, local_dir=logo_dir, dry_run=False, force=False)
    tokens = json.loads((tenant.brand_dir / "tokens.json").read_text())
    # The hand-edited accent survives; a re-import without --force never
    # clobbers a value a human already set.
    assert tokens["brand_import"]["colors"]["accent"]["hex"] == "#111111"
    assert tokens["brand_import"]["colors"]["accent"]["source"] == "hand-edit"


def test_tokens_json_force_overwrites_hand_edit(tmp_path):
    tenant = _make_demo_tenant(tmp_path, "force-co")
    tenant.brand_dir.mkdir(parents=True, exist_ok=True)
    (tenant.brand_dir / "tokens.json").write_text(json.dumps({
        "brand_import": {"colors": {"accent": {"hex": "#111111", "role": "accent", "source": "hand-edit"}}}
    }))
    logo_dir = tmp_path / "src"
    logo_dir.mkdir()
    Image.new("RGB", (20, 20), (17, 23, 43)).save(logo_dir / "acme-logo.png")
    brand_import.import_brand_kit(tenant, local_dir=logo_dir, dry_run=False, force=True)
    tokens = json.loads((tenant.brand_dir / "tokens.json").read_text())
    assert tokens["brand_import"]["colors"]["accent"]["hex"] != "#111111"


def test_write_tenant_yaml_brand_section_preserves_the_rest_of_the_file(tmp_path):
    tenant_yaml = tmp_path / "tenant.yaml"
    original = (
        "# a hand-written comment that must survive\n"
        "name: Acme Co\n"
        "slug: acme-co\n"
        "\n"
        "brand: {}\n"
    )
    tenant_yaml.write_text(original)
    brand_import.write_tenant_yaml_brand_section(
        tenant_yaml, {"logo_path": "brand/logo.png", "accent_hex": "#16C47F"}, force=False
    )
    text = tenant_yaml.read_text()
    assert "# a hand-written comment that must survive" in text
    assert "name: Acme Co" in text
    assert "logo_path: brand/logo.png" in text
    assert "accent_hex: '#16C47F'" in text or "accent_hex: \"#16C47F\"" in text or "accent_hex: #16C47F" in text

    # Re-run without --force, a different value: existing sub-key wins, a new
    # sub-key is still added.
    brand_import.write_tenant_yaml_brand_section(
        tenant_yaml, {"accent_hex": "#000000", "primary_hex": "#ffffff"}, force=False
    )
    text2 = tenant_yaml.read_text()
    assert "primary_hex" in text2
    assert "#000000" not in text2  # the original accent_hex was not overwritten
    assert "# a hand-written comment that must survive" in text2


# ---------------------------------------------------------------------------
# Contrast math
# ---------------------------------------------------------------------------

def test_contrast_ratio_black_on_white_is_21():
    assert brand_import.contrast_ratio("#000000", "#ffffff") == pytest.approx(21.0, abs=0.01)


def test_contrast_ratio_same_color_is_1():
    assert brand_import.contrast_ratio("#336699", "#336699") == pytest.approx(1.0, abs=0.001)


def test_contrast_ratio_symmetric():
    a = brand_import.contrast_ratio("#123456", "#fedcba")
    b = brand_import.contrast_ratio("#fedcba", "#123456")
    assert a == pytest.approx(b)


def test_accent_below_3to1_is_refused_without_force(tmp_path):
    tenant = _make_demo_tenant(tmp_path, "lowcontrast-co")
    src = tmp_path / "src"
    src.mkdir()
    # Near-white logo: its own dominant-color accent candidate will be close
    # to the (white) background, i.e. low contrast.
    Image.new("RGB", (20, 20), (250, 248, 245)).save(src / "acme-logo.png")
    result = brand_import.import_brand_kit(tenant, local_dir=src, dry_run=True, force=False)
    assert any("contrast" in d.lower() and "refused" in d.lower() for d in result.human_decisions)


# ---------------------------------------------------------------------------
# base.css generation
# ---------------------------------------------------------------------------

def test_generate_base_css_declares_adv_custom_properties_and_font_import():
    tokens = {
        "colors": {"accent": {"hex": "#16C47F"}, "text": {"hex": "#141416"}, "background": {"hex": "#ffffff"}},
        "fonts": {"body": {"family": "Eina03", "google_fonts_url": "https://fonts.googleapis.com/css2?family=Eina03", "faces": []}},
    }
    css = brand_import.generate_base_css(tokens, tenant_name="Acme Co")
    assert "--adv-accent: #16C47F;" in css
    assert "--adv-fg: #141416;" in css
    assert "--adv-bg: #ffffff;" in css
    assert "@import url('https://fonts.googleapis.com/css2?family=Eina03');" in css
    assert "font-family: 'Eina03'" in css


def test_base_css_not_overwritten_without_force(tmp_path):
    tenant = _make_demo_tenant(tmp_path, "handcss-co")
    tenant.brand_dir.mkdir(parents=True, exist_ok=True)
    (tenant.brand_dir / "base.css").write_text(":root { --adv-accent: #003366; } /* hand-tuned, never touch */\n")
    src = tmp_path / "src"
    src.mkdir()
    Image.new("RGB", (20, 20), (22, 196, 127)).save(src / "acme-logo.png")
    result = brand_import.import_brand_kit(tenant, local_dir=src, dry_run=False, force=False)
    assert result.wrote_base_css is False
    assert (tenant.brand_dir / "base.css").read_text() == ":root { --adv-accent: #003366; } /* hand-tuned, never touch */\n"


def test_base_css_overwritten_with_force(tmp_path):
    tenant = _make_demo_tenant(tmp_path, "handcss2-co")
    tenant.brand_dir.mkdir(parents=True, exist_ok=True)
    (tenant.brand_dir / "base.css").write_text(":root { --adv-accent: #003366; } /* hand-tuned, never touch */\n")
    src = tmp_path / "src"
    src.mkdir()
    Image.new("RGB", (20, 20), (22, 196, 127)).save(src / "acme-logo.png")
    result = brand_import.import_brand_kit(tenant, local_dir=src, dry_run=False, force=True)
    assert result.wrote_base_css is True
    assert "hand-tuned" not in (tenant.brand_dir / "base.css").read_text()


# ---------------------------------------------------------------------------
# Palette parsing / dominant color
# ---------------------------------------------------------------------------

def test_parse_palette_file_extracts_hex_codes(tmp_path):
    p = tmp_path / "palette.txt"
    p.write_text("Primary: #16C47F\nAccent #11bdfb\nold-3-digit #abc\nnot a color: banana\n")
    assert brand_import.parse_palette_file(p) == ["#16c47f", "#11bdfb", "#aabbcc"]


def test_parse_ase_file_returns_empty_list(tmp_path):
    p = tmp_path / "swatches.ase"
    p.write_bytes(b"\x00ASEF\x00\x01\x00\x00")
    assert brand_import.parse_palette_file(p) == []


def test_dominant_colors_of_a_solid_png(tmp_path):
    p = tmp_path / "solid.png"
    Image.new("RGB", (30, 30), (22, 196, 127)).save(p)
    colors = brand_import.dominant_colors(p)
    assert colors == ["#16c47f"]


def test_dominant_colors_of_an_svg_is_empty_no_rasterizer(tmp_path):
    p = tmp_path / "logo.svg"
    p.write_text("<svg></svg>")
    assert brand_import.dominant_colors(p) == []


# ---------------------------------------------------------------------------
# Demo-tenant end-to-end: local brand dir -> import -> render_page
# ---------------------------------------------------------------------------

def _make_demo_tenant(tmp_path, slug):
    tenants_dir = tmp_path / "tenants"
    tenants_dir.mkdir(exist_ok=True)
    template_copy = tenants_dir / "_template"
    if not template_copy.exists():
        shutil.copytree(tenant_mod.TEMPLATE_DIR, template_copy)
    root = tenants_dir / slug
    shutil.copytree(template_copy, root)
    return tenant_mod.Tenant(slug, root)


def test_demo_tenant_end_to_end_logo_and_color_reach_rendered_html(tmp_path):
    tenant = _make_demo_tenant(tmp_path, "demo-co")

    brand_src = tmp_path / "brand-src"
    brand_src.mkdir()
    # A dark navy: real contrast (~17.6:1) against the default white
    # background, so it clears the 3:1 accent gate and actually ends up in
    # base.css -- a bright green like a real logo's accent color can (and,
    # per test_accent_below_3to1_is_refused_without_force above, sometimes
    # does) fail that gate on white, which is the gate doing its job, not a
    # good fixture color for this end-to-end assertion.
    Image.new("RGB", (64, 64), (23, 23, 43)).save(brand_src / "acme-logo.png")
    (brand_src / "palette.txt").write_text("Secondary: #16C47F\nBackground: #ffffff\n")

    result = brand_import.import_brand_kit(tenant, local_dir=brand_src, dry_run=False, force=False)
    assert (tenant.brand_dir / "logo.png").exists()
    assert result.logo_path == "brand/logo.png"

    # Reuse the real product-page fixture (tests/test_render.py) rather than
    # hand-building another one -- this test is scoped to "does an imported
    # brand kit actually reach the HTML", not to re-proving render_page's own
    # field-by-field behavior (that's test_render.py's job).
    out_dir = tmp_path / "out"
    index_path = render.render_page(
        cartridge_name="product-page",
        page=PRODUCT_PAGE_PAGE,
        ad_brief=AD_BRIEF,
        facts_pack=FACTS_PACK,
        cartridges_dir=tenant_mod.REPO_ROOT / "cartridges",
        brand_dir=tenant.brand_dir,
        templates_dir=tenant_mod.REPO_ROOT / "harness" / "templates",
        out_dir=out_dir,
        published="2026-09-11",
        updated="2026-09-11",
        download_assets=False,
        tenant=tenant,
    )
    html = index_path.read_text()
    assert 'class="adv-brand-logo"' in html
    assert 'src="assets/brand-logo.png"' in html
    assert (out_dir / "assets" / "brand-logo.png").exists()

    css = (tenant.brand_dir / "base.css").read_text()
    assert "--adv-accent: #17172b;" in css.lower()
