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
from pathlib import Path

import pytest
from PIL import Image

from harness import brand_import, render
from harness import tenant as tenant_mod
from harness.budget import Budget
from harness.log import RunLog
from harness.sources import drive
from tests.conftest import FakeClient, json_response
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
        {"id": "1AAAAAAAAAAAAAAAAAAA", "name": "Acme Logo.svg", "is_folder": False},
        {"id": "1BBBBBBBBBBBBBBBBBBB", "name": "Acme Brand Guide.pdf", "is_folder": False},
    ]


# ---------------------------------------------------------------------------
# Folder marker (Cycle 35c) -- verified against a real Drive folder ("Peak
# Toolbox", id 1D8cG1cAY9sH0_fzMsUhseK_5sSWa_KCD, and its subfolders): a
# folder's aria-label ends in the literal word "folder" ("1 - Peak Logotype
# Shared folder"), a file's ends in the status word itself ("Peak_Infos.pdf
# PDF Shared"). See tests/fixtures/drive-folder-listing-sample.html for a
# trimmed real sample carrying both shapes plus a nested nesting-friendly
# nested-folder marker.
# ---------------------------------------------------------------------------

def test_list_public_folder_marks_folders_via_the_real_aria_label_shape():
    entries = drive.list_public_folder("root-folder", fetch=lambda fid: SAMPLE_FOLDER_HTML)
    assert all(e["is_folder"] is False for e in entries)

    html = (
        "<html><body>"
        + _entry_html("1FOLDERAAAAAAAAAAAAA", "1 - Peak Logotype", type_word="", status="Shared folder")
        + _entry_html("1FILEAAAAAAAAAAAAAAA", "Peak_Infos.pdf", type_word="PDF")
        + "</body></html>"
    )
    entries = drive.list_public_folder("root", fetch=lambda fid: html)
    by_id = {e["id"]: e for e in entries}
    assert by_id["1FOLDERAAAAAAAAAAAAA"] == {
        "id": "1FOLDERAAAAAAAAAAAAA", "name": "1 - Peak Logotype", "is_folder": True,
    }
    assert by_id["1FILEAAAAAAAAAAAAAAA"] == {
        "id": "1FILEAAAAAAAAAAAAAAA", "name": "Peak_Infos.pdf", "is_folder": False,
    }


def test_list_public_folder_marker_matches_the_real_fixture_sample():
    # A trimmed but real sample from the actual "Peak Toolbox" Drive folder
    # (root level): 8 subfolders, all marked is_folder=True, correctly named
    # with the "Shared folder" suffix stripped.
    html = (Path(__file__).parent / "fixtures" / "drive-folder-listing-sample.html").read_text()
    entries = drive.list_public_folder("1D8cG1cAY9sH0_fzMsUhseK_5sSWa_KCD", fetch=lambda fid: html)
    assert entries == [
        {"id": "1ndGlP4Tq1MZswdGZh3EwDDTqwH5EwhHS", "name": "1 - Peak Logotype", "is_folder": True},
        {"id": "1id350mX98hW5TS5irRjNx1HHx5w6aMZ3", "name": "6 - Peak Info Sheet", "is_folder": True},
        {"id": "1nVMOu99W_hNuXxjqaWfSBKZF6Q2KbRfO", "name": "Peak_Infos.pdf", "is_folder": False},
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


def test_enumerate_source_walks_three_levels_deep_and_skips_ds_store():
    # 1 - Peak Logotype/ -> RGB/ -> Peak_Logo_RGB.svg, mirroring the real
    # "Peak Toolbox" tree's depth -- .DS_Store sits at two of the three
    # levels and must never show up as an entry.
    root_id = "root-id-nested-00000001"
    l1_id = "l1-logotype-folder-id01"
    l2_id = "l2-rgb-folder-id000001"
    file_id = "file-id-peak-logo-rgb01"

    root_html = (
        _entry_html(l1_id, "1 - Peak Logotype", type_word="", status="Shared folder")
        + _entry_html("ds-store-root-id00001", ".DS_Store", type_word="Binary")
    )
    l1_html = (
        _entry_html(l2_id, "RGB", type_word="", status="Shared folder")
        + _entry_html("ds-store-l1-id000001x", ".DS_Store", type_word="Binary")
    )
    l2_html = _entry_html(file_id, "Peak_Logo_RGB.svg", type_word="Image")
    pages = {root_id: root_html, l1_id: l1_html, l2_id: l2_html}

    entries = brand_import.enumerate_source(drive_folder=root_id, fetch=lambda fid: pages[fid])

    assert [e["name"] for e in entries] == ["Peak_Logo_RGB.svg"]
    assert entries[0]["folder_path"] == ("1 - Peak Logotype", "RGB")
    # The two folder ids must never appear as an entry -- see
    # test_download_incoming_never_downloads_a_folder_id below for the
    # invariant this actually protects.
    entry_ids = {e["id"] for e in entries}
    assert l1_id not in entry_ids
    assert l2_id not in entry_ids


def test_enumerate_source_depth_cap_skips_rather_than_falls_back_to_file():
    # Regression for the reported HTTP 500: the old code, when a folder was
    # too deep to recurse into, fell back to entries.append(...) and treated
    # the folder id as a file -- download_incoming then handed it straight
    # to the file downloader, which 500'd. A too-deep folder must now just
    # be skipped, never appended.
    root_id = "root-id-depthcap-000001"
    deep_id = "deep-folder-id-00000001"
    root_html = _entry_html(deep_id, "Too Deep", type_word="", status="Shared folder")
    entries = brand_import.enumerate_source(
        drive_folder=root_id, fetch=lambda fid: {root_id: root_html}[fid], max_folder_depth=0,
    )
    assert entries == []


def test_download_incoming_never_downloads_a_folder_id(tmp_path, monkeypatch):
    root_id = "root-id-nodl-0000000001"
    folder_id = "folder-id-nodl-00000001"
    file_id = "file-id-nodl-000000001"
    root_html = (
        _entry_html(folder_id, "1 - Peak Logotype", type_word="", status="Shared folder")
        + _entry_html(file_id, "Peak_Logo.svg", type_word="Image")
    )
    pages = {root_id: root_html, folder_id: "<html><body></body></html>"}
    entries = brand_import.enumerate_source(drive_folder=root_id, fetch=lambda fid: pages[fid])

    downloaded_ids = []

    def fake_download(file_id_arg, dest_dir):
        assert file_id_arg != folder_id, "a folder id must never reach the file downloader"
        downloaded_ids.append(file_id_arg)
        dest_dir = Path(dest_dir)
        dest_dir.mkdir(parents=True, exist_ok=True)
        p = dest_dir / "Peak_Logo.svg"
        p.write_bytes(b"<svg></svg>")
        return p

    monkeypatch.setattr(drive, "download_drive_file", fake_download)
    list(brand_import.download_incoming(entries, tmp_path / "incoming"))
    assert downloaded_ids == [file_id]


def test_download_incoming_drops_zero_byte_downloads(tmp_path, monkeypatch):
    root_id = "root-id-zerobyte-000001"
    empty_id = "empty-file-id-0000000001"
    root_html = _entry_html(empty_id, "empty.png", type_word="Image")
    entries = brand_import.enumerate_source(drive_folder=root_id, fetch=lambda fid: {root_id: root_html}[fid])

    def fake_download(file_id_arg, dest_dir):
        dest_dir = Path(dest_dir)
        dest_dir.mkdir(parents=True, exist_ok=True)
        p = dest_dir / "empty.png"
        p.write_bytes(b"")
        return p

    monkeypatch.setattr(drive, "download_drive_file", fake_download)
    downloaded = list(brand_import.download_incoming(entries, tmp_path / "incoming"))
    assert downloaded == []


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


@pytest.mark.parametrize("name,folder_path,expected", [
    # .ai/.eps under a Logotype/Icon folder: a logo source, not usable
    # directly -- see LOGO_SOURCE_ONLY_EXTS / import_brand_kit's note.
    ("Acme blue on the left CMYK.ai", ("1 - Peak Logotype", "CMYK"), "logo"),
    ("Acme blue on the left RGB.eps", ("2 - Peak Icon", "RGB"), "logo"),
    # A .pdf in the same tree is a usable (rasterizable) logo candidate.
    ("Peak_Logo_RGB.pdf", ("1 - Peak Logotype", "RGB"), "logo"),
    ("Peak_Icon.pdf", ("3 - Peak with Icon", "With big icon"), "logo"),
    # A Colors-folder .pdf is the palette document, not a generic guide.
    ("Peak_Sauna_Colors.pdf", ("5 - Colors",), "palette_pdf"),
    # The .ai sibling in that same Colors folder is not a logo source (wrong
    # folder) and isn't a recognized palette extension either -- "other".
    ("Peak_Sauna_Colors.ai", ("5 - Colors",), "other"),
    # Fonts and guides classify the same regardless of folder context.
    ("Acid Grotesk TRIAL Regular-9687.otf", ("4 - Fonts",), "font"),
    ("Epika_Trial-Regular.otf", ("4 - Fonts",), "font"),
    ("Peak_Infos.pdf", ("6 - Peak Info Sheet",), "guide"),
    ("Peak Toolbox Overview.pdf", ("7 - Branding",), "guide"),
    # No folder context (a --local import, or a top-level Drive file):
    # behaves exactly like the folder-blind classify_file cases above.
    ("some-icon.ai", (), "other"),
])
def test_classify_file_is_folder_aware(name, folder_path, expected):
    assert brand_import.classify_file(name, folder_path) == expected


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


def test_write_tokens_json_brand_import_leaves_the_rest_of_the_file_byte_identical(tmp_path):
    tokens_path = tmp_path / "tokens.json"
    # A single-line array and no trailing newline between sections, like the
    # real hand-authored tenants/peak-saunas/brand/tokens.json -- a
    # json.loads/json.dumps round-trip would reformat both.
    original = (
        "{\n"
        '  "font.body.weights_used": [400, 500, 600, 700],\n'
        '  "color.background.page": "#ffffff",\n'
        '\n'
        '  "theme.name": "Live Site"\n'
        "}\n"
    )
    tokens_path.write_text(original)
    brand_import.write_tokens_json_brand_import(
        tokens_path, {"colors": {"accent": {"hex": "#16C47F", "source": "brand-import:x"}}}, force=False
    )
    text = tokens_path.read_text()
    # Everything that existed before is untouched, byte for byte.
    assert '"font.body.weights_used": [400, 500, 600, 700],' in text
    assert '"color.background.page": "#ffffff",' in text
    assert '"theme.name": "Live Site"' in text
    parsed = json.loads(text)
    assert parsed["brand_import"]["colors"]["accent"]["hex"] == "#16C47F"


def test_write_tokens_json_brand_import_rerun_without_force_keeps_hand_edit(tmp_path):
    tokens_path = tmp_path / "tokens.json"
    tokens_path.write_text(json.dumps({
        "brand_import": {"colors": {"accent": {"hex": "#111111", "source": "hand-edit"}}}
    }))
    brand_import.write_tokens_json_brand_import(
        tokens_path, {"colors": {"accent": {"hex": "#222222", "source": "brand-import:x"}}}, force=False
    )
    parsed = json.loads(tokens_path.read_text())
    assert parsed["brand_import"]["colors"]["accent"]["hex"] == "#111111"


def test_write_tokens_json_brand_import_force_overwrites(tmp_path):
    tokens_path = tmp_path / "tokens.json"
    tokens_path.write_text(json.dumps({
        "brand_import": {"colors": {"accent": {"hex": "#111111", "source": "hand-edit"}}}
    }))
    brand_import.write_tokens_json_brand_import(
        tokens_path, {"colors": {"accent": {"hex": "#222222", "source": "brand-import:x"}}}, force=True
    )
    parsed = json.loads(tokens_path.read_text())
    assert parsed["brand_import"]["colors"]["accent"]["hex"] == "#222222"


# ---------------------------------------------------------------------------
# Font-name sanity (a real vision-model finding, see FIXLOG Cycle 27)
# ---------------------------------------------------------------------------

def test_looks_like_a_real_font_name():
    assert brand_import._looks_like_a_real_font_name("Eina03")
    assert brand_import._looks_like_a_real_font_name("DM Sans")
    assert not brand_import._looks_like_a_real_font_name("Sans-serif (appears to be a modern geometric sans-serif)")


# ---------------------------------------------------------------------------
# Trial/demo font refusal (Cycle 35c: the two real .otf files found in the
# "Peak Toolbox" Fonts folder are both TRIAL builds)
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("name", [
    "Acid Grotesk TRIAL Regular-9687.otf",
    "Epika_Trial-Regular.otf",
    "SomeFont-DEMO.ttf",
    "another-eval-build.otf",
])
def test_is_unlicensed_trial_font(name):
    assert brand_import._is_unlicensed_trial_font(name)


def test_is_unlicensed_trial_font_false_for_a_normal_name():
    assert not brand_import._is_unlicensed_trial_font("DM_Sans-Regular.otf")


def test_trial_font_is_flagged_and_never_copied_even_with_force(tmp_path):
    tenant = _make_demo_tenant(tmp_path, "trialfont-co")
    src = tmp_path / "src"
    src.mkdir()
    (src / "Acid Grotesk TRIAL Regular-9687.otf").write_bytes(b"\x00" * 200)
    (src / "Epika-Regular.otf").write_bytes(b"\x00" * 200)

    result = brand_import.import_brand_kit(tenant, local_dir=src, dry_run=False, force=True)

    fonts_dir = tenant.brand_dir / "fonts"
    written = {p.name for p in fonts_dir.iterdir()} if fonts_dir.exists() else set()
    assert "Acid Grotesk TRIAL Regular-9687.otf" not in written
    assert "Epika-Regular.otf" in written
    assert any(
        "trial" in d.lower() and "not licensed" in d.lower() and "even with --force" in d.lower()
        for d in result.human_decisions
    )


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
# Colors-folder palette PDF -> vision (Cycle 35c)
# ---------------------------------------------------------------------------

def test_palette_pdf_from_a_colors_folder_extracts_hex_via_vision(tmp_path, monkeypatch):
    tenant = _make_demo_tenant(tmp_path, "palette-co")
    root_id = "root-id-palette-0000001"
    colors_folder_id = "colors-folder-id-000001"
    pdf_id = "colors-pdf-id-00000001"

    root_html = _entry_html(colors_folder_id, "5 - Colors", type_word="", status="Shared folder")
    colors_html = _entry_html(pdf_id, "Peak_Sauna_Colors.pdf", type_word="PDF")
    pages_html = {root_id: root_html, colors_folder_id: colors_html}

    def fake_download(file_id_arg, dest_dir):
        dest_dir = Path(dest_dir)
        dest_dir.mkdir(parents=True, exist_ok=True)
        p = dest_dir / "Peak_Sauna_Colors.pdf"
        p.write_bytes(b"%PDF-1.4 fake content, never actually rendered in this test")
        return p

    monkeypatch.setattr(drive, "download_drive_file", fake_download)

    # This test is about parsing the vision response, not pdftoppm -- swap
    # in a real (tiny) image as the "rendered page" directly.
    fake_page = tmp_path / "fake-colors-page.png"
    Image.new("RGB", (4, 4), (22, 196, 127)).save(fake_page)
    monkeypatch.setattr(brand_import, "render_guide_pages", lambda *a, **k: [fake_page])

    vision_response = json_response({
        "colors": [
            {"name": "Peak Green", "hex": "#16C47F", "role": "accent"},
            {"name": "Peak Navy", "hex": "#17172B", "role": "background"},
        ],
        "fonts": [], "logo_rules": [], "voice": [], "dont": [],
    })
    client = FakeClient([vision_response])
    log = RunLog("test-palette-run", tmp_path / "run.log")

    result = brand_import.import_brand_kit(
        tenant, drive_folder=root_id, fetch=lambda fid: pages_html[fid],
        dry_run=True, client=client, budget=Budget(), log=log, model="claude-sonnet-5",
    )
    log.close()

    assert result.raw_palette_json["colors"][0]["hex"] == "#16C47F"
    # Colors are keyed by normalized (lowercase) hex now -- see the "palette"
    # list, which holds every distinct color found, not just the derived
    # primary/accent/background/text picks in "colors".
    palette_hexes = {c["hex"] for c in result.brand_import_tokens["palette"]}
    assert "#16c47f" in palette_hexes
    assert "#17172b" in palette_hexes
    colors = result.brand_import_tokens["colors"]
    accent = next(c for c in colors.values() if c.get("role") == "accent")
    assert accent["source"] == "brand-import:Peak_Sauna_Colors.pdf"


# ---------------------------------------------------------------------------
# Merch/layout folder classification (Cycle 35d)
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("folder_path", [
    ("8 - Logo layout for Merch",),
    ("Merch",),
    ("Packaging Layout",),
])
def test_pdf_under_a_merch_or_layout_folder_classifies_as_merch_layout(folder_path):
    assert brand_import.classify_file("Some-Variant.pdf", folder_path) == "merch_layout"


def test_merch_layout_folder_does_not_catch_non_pdf_files():
    # Only PDFs are reclassified -- a preview image in the same folder keeps
    # its normal "photo" classification.
    assert brand_import.classify_file("preview.png", ("8 - Logo layout for Merch",)) == "photo"


def test_a_guide_ish_folder_pdf_still_classifies_as_guide():
    assert brand_import.classify_file("Peak_Infos.pdf", ("6 - Info Sheet",)) == "guide"


def test_a_pdf_with_no_folder_context_still_defaults_to_guide():
    # A flat --local import (or a top-level Drive file) has no folder to
    # gate on -- the old inclusive default is kept for that case, since
    # there's no folder signal to scope against.
    assert brand_import.classify_file("random.pdf") == "guide"


def test_a_pdf_under_an_unrelated_folder_no_longer_defaults_to_guide():
    # Cycle 35d tightening: with real folder context, an unnamed .pdf under
    # a folder that isn't guide/brand/info/deck/presentation-ish (and isn't
    # merch/layout either) is no longer assumed to be the brand guide.
    assert brand_import.classify_file("Untitled.pdf", ("9 - Misc",)) == "other"


# ---------------------------------------------------------------------------
# Color palette: keyed by hex, not role (Cycle 35d)
# ---------------------------------------------------------------------------

def test_add_palette_color_keeps_every_distinct_hex_even_with_a_repeated_role():
    # Regression for the real gap this cycle fixes: the old merge keyed on
    # role, so a second color sharing a role silently clobbered the first.
    palette, seen = [], set()
    brand_import._add_palette_color(palette, seen, "#EFE3D2", name="Stone", role="neutral", source="brand-import:a.pdf")
    brand_import._add_palette_color(palette, seen, "#F27046", name="Solar Flare", role="accent", source="brand-import:a.pdf")
    brand_import._add_palette_color(palette, seen, "#483215", name="Cedar", role="neutral", source="brand-import:a.pdf")
    assert [c["hex"] for c in palette] == ["#efe3d2", "#f27046", "#483215"]
    assert [c["rank"] for c in palette] == [1, 2, 3]


def test_add_palette_color_dedupes_an_exact_repeated_hex():
    palette, seen = [], set()
    brand_import._add_palette_color(palette, seen, "#16C47F", role="accent", source="brand-import:logo.png")
    brand_import._add_palette_color(palette, seen, "#16c47f", role="palette", source="brand-import:palette.txt")
    assert len(palette) == 1
    # First-seen metadata wins for an exact repeat.
    assert palette[0]["role"] == "accent"
    assert palette[0]["source"] == "brand-import:logo.png"


def test_guide_colors_sharing_a_role_are_all_kept(tmp_path, monkeypatch):
    tenant = _make_demo_tenant(tmp_path, "roletest-co")
    src = tmp_path / "src"
    src.mkdir()
    (src / "Brand Guide.pdf").write_bytes(b"%PDF-1.4 fake")

    fake_page = tmp_path / "fake-guide-page.png"
    Image.new("RGB", (4, 4), (0, 0, 0)).save(fake_page)
    monkeypatch.setattr(brand_import, "render_guide_pages", lambda *a, **k: [fake_page])

    vision_response = json_response({
        "colors": [
            {"name": "Fossil Dust", "hex": "#C0C8C3", "role": "neutral"},
            {"name": "Stone", "hex": "#EFE3D2", "role": "neutral"},
            {"name": "Cedar", "hex": "#483215", "role": "neutral"},
        ],
        "fonts": [], "logo_rules": [], "voice": [], "dont": [],
    })
    client = FakeClient([vision_response])
    log = RunLog("test-role-run", tmp_path / "run.log")

    result = brand_import.import_brand_kit(
        tenant, local_dir=src, dry_run=True, client=client, budget=Budget(), log=log, model="claude-sonnet-5",
    )
    log.close()

    hexes = {c["hex"] for c in result.brand_import_tokens["palette"]}
    assert {"#c0c8c3", "#efe3d2", "#483215"} <= hexes


# ---------------------------------------------------------------------------
# Derived picks: primary/accent/background/text (Cycle 35d)
# ---------------------------------------------------------------------------

def _palette_color(hex_value, role="", name="", source="brand-import:x", rank=1):
    return {"hex": hex_value, "name": name, "role": role, "source": source, "rank": rank}


def test_pick_background_prefers_an_explicit_label():
    palette = [_palette_color("#123456", role="accent"), _palette_color("#000000", role="background")]
    assert brand_import._pick_background(palette)["hex"] == "#000000"


def test_pick_background_falls_back_to_the_lightest_neutral():
    palette = [_palette_color("#16c47f", role="accent"), _palette_color("#f5f5f0", role="palette")]
    assert brand_import._pick_background(palette)["hex"] == "#f5f5f0"


def test_pick_background_never_reuses_a_color_claimed_by_another_role():
    # A single saturated accent color must not become the background too --
    # that would zero out the accent's own contrast against the page.
    palette = [_palette_color("#16c47f", role="accent")]
    assert brand_import._pick_background(palette) is None


def test_pick_accent_prefers_the_most_saturated_warm_color():
    palette = [_palette_color("#3b5bdb", role="palette"), _palette_color("#f27046", role="palette")]
    assert brand_import._pick_accent(palette)["hex"] == "#f27046"


def test_pick_accent_prefers_an_explicit_label_over_the_warm_heuristic():
    palette = [_palette_color("#f27046", role="palette"), _palette_color("#3b5bdb", role="accent")]
    assert brand_import._pick_accent(palette)["hex"] == "#3b5bdb"


def test_pick_text_prefers_highest_contrast_dark_color():
    palette = [_palette_color("#eeeeee", role="palette"), _palette_color("#111111", role="palette")]
    assert brand_import._pick_text(palette, "#ffffff")["hex"] == "#111111"


# ---------------------------------------------------------------------------
# Oversize PDF: fail soft (Cycle 35d)
# ---------------------------------------------------------------------------

def test_oversize_guide_pdf_is_skipped_with_a_report_line(tmp_path, monkeypatch):
    tenant = _make_demo_tenant(tmp_path, "oversize-co")
    src = tmp_path / "src"
    src.mkdir()
    big_guide = src / "Company Brand Guide.pdf"
    big_guide.write_bytes(b"x" * (26 * 1024 * 1024))  # over the 25 MB cap

    render_calls = []
    monkeypatch.setattr(brand_import, "render_guide_pages", lambda *a, **k: render_calls.append(a) or [])

    result = brand_import.import_brand_kit(tenant, local_dir=src, dry_run=True, force=False)

    assert render_calls == []  # never attempted to rasterize the oversize file
    assert any(
        "skipped" in d.lower() and "mb" in d.lower() and "over cap" in d.lower()
        for d in result.human_decisions
    )


def test_oversize_palette_pdf_is_skipped_with_a_report_line(tmp_path, monkeypatch):
    tenant = _make_demo_tenant(tmp_path, "oversize-palette-co")
    root_id = "root-id-oversize-0000001"
    colors_folder_id = "colors-folder-id-oversize1"
    pdf_id = "colors-pdf-id-oversize001"

    root_html = _entry_html(colors_folder_id, "5 - Colors", type_word="", status="Shared folder")
    colors_html = _entry_html(pdf_id, "Huge_Colors.pdf", type_word="PDF")
    pages_html = {root_id: root_html, colors_folder_id: colors_html}

    def fake_download(file_id_arg, dest_dir):
        dest_dir = Path(dest_dir)
        dest_dir.mkdir(parents=True, exist_ok=True)
        p = dest_dir / "Huge_Colors.pdf"
        p.write_bytes(b"x" * (26 * 1024 * 1024))
        return p

    monkeypatch.setattr(drive, "download_drive_file", fake_download)
    render_calls = []
    monkeypatch.setattr(brand_import, "render_guide_pages", lambda *a, **k: render_calls.append(a) or [])

    result = brand_import.import_brand_kit(
        tenant, drive_folder=root_id, fetch=lambda fid: pages_html[fid], dry_run=True,
    )

    assert render_calls == []
    assert any(
        "skipped" in d.lower() and "mb" in d.lower() and "over cap" in d.lower()
        for d in result.human_decisions
    )


# ---------------------------------------------------------------------------
# Guide cap: at most MAX_GUIDE_DOCS PDFs vision-read per import (Cycle 35d)
# ---------------------------------------------------------------------------

def test_guide_cap_reads_at_most_two_and_lists_the_rest(tmp_path, monkeypatch):
    tenant = _make_demo_tenant(tmp_path, "guidecap-co")
    src = tmp_path / "src"
    src.mkdir()
    (src / "Brand Guide A.pdf").write_bytes(b"x" * 3000)
    (src / "Brand Guide B.pdf").write_bytes(b"x" * 2000)
    (src / "Brand Guide C.pdf").write_bytes(b"x" * 1000)

    fake_page = tmp_path / "fake-page.png"
    Image.new("RGB", (4, 4), (0, 0, 0)).save(fake_page)
    monkeypatch.setattr(brand_import, "render_guide_pages", lambda *a, **k: [fake_page])

    vision_response = json_response({"colors": [], "fonts": [], "logo_rules": [], "voice": [], "dont": []})
    client = FakeClient([vision_response, vision_response, vision_response])
    log = RunLog("test-guidecap-run", tmp_path / "run.log")

    result = brand_import.import_brand_kit(
        tenant, local_dir=src, dry_run=True, client=client, budget=Budget(), log=log, model="claude-sonnet-5",
    )
    log.close()

    assert len(client.messages.calls) == brand_import.MAX_GUIDE_DOCS
    assert len(result.raw_model_json) == brand_import.MAX_GUIDE_DOCS
    assert {e["file"] for e in result.raw_model_json} == {"Brand Guide A.pdf", "Brand Guide B.pdf"}
    assert any(
        "cap of 2" in d.lower() and "brand guide c.pdf" in d.lower()
        for d in result.human_decisions
    )


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
        page={**PRODUCT_PAGE_PAGE, "cta_url": "/collections/all"},
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
