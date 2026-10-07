"""Cycle 95: the four brand web families -- Peak Grotesk Wide, Acid Grotesk
(licensed), Montserrat, Epika -- reach every place a page is shown.

- base.css declares the four @font-face rules and the type tokens;
- the storefront export carries the four rules in the form the publisher
  rewrites (brand/fonts/<file>, or the theme CDN url for Peak Grotesk Wide);
- `harness publish` uploads the new brand/fonts files and rewrites their
  urls without dropping the absolute theme-CDN url;
- the review page and the thumbnail capture load every face from the
  local files (data: URIs), so they render offline; index.html keeps the
  publishable urls;
- no "trial" font remains in brand/ or the looks;
- the looks use Epika for the quotes and keep the display faces on the
  first screen; the brand lint passes on a fake run.
"""
import base64
import json
import re
import shutil
import types
from pathlib import Path

import pytest

from harness import cli, library, look_design, review
from harness.page_body import build_shopify_body, font_face_css
from harness.publishers import shopify as shopify_pub
from harness.review import inline_font_face_urls
from tests.support import REPO_ROOT, TENANT
from tests.test_brand_lint_cycle78 import _lint

BRAND = TENANT.brand_dir
FAMILIES = {"Peak Grotesk Wide", "Acid Grotesk", "Montserrat", "Epika"}
NEW_FILES = ["Epika-Regular.woff2", "FFF-AcidGrotesk-Regular.woff2", "Montserrat-Variable.woff2"]
PGW_CDN = "https://cdn.shopify.com/s/files/1/0825/8312/6317/t/64/assets/pk-peak-grotesk-wide.woff2"
LOOKS = {
    "landing-open": REPO_ROOT / "cartridges" / "landing" / "looks" / "open" / "template.html",
    "landing-proof": REPO_ROOT / "cartridges" / "landing" / "looks" / "proof" / "template.html",
    "listicle-open": REPO_ROOT / "cartridges" / "listicle" / "looks" / "open" / "template.html",
}

_FACE_RE = re.compile(r"@font-face\s*\{[^}]*\}", re.S)
_FAMILY_RE = re.compile(r'font-family:"([^"]+)"')
_URL_RE = re.compile(r'url\("([^"]+)"\)')


def _faces(text):
    return {_FAMILY_RE.search(f).group(1): f for f in _FACE_RE.findall(text)}


def _base_css():
    return (BRAND / "base.css").read_text()


def _render_listicle(tmp_path, hero_style="display"):
    from tests.test_listicle import _listicle_page
    from tests.test_listicle_looks import _render
    page = _listicle_page()
    page["hero_style"] = hero_style
    _render("open", tmp_path, page=page)
    return tmp_path / "listicle"


# ---------------------------------------------------------------------------
# base.css and tokens
# ---------------------------------------------------------------------------

def test_base_css_declares_the_four_families_and_their_tokens():
    css = _base_css()
    faces = _faces(css)
    assert set(faces) == FAMILIES
    assert PGW_CDN in faces["Peak Grotesk Wide"]
    for family, filename in (("Acid Grotesk", "FFF-AcidGrotesk-Regular.woff2"),
                             ("Montserrat", "Montserrat-Variable.woff2"), ("Epika", "Epika-Regular.woff2")):
        assert f'url("brand/fonts/{filename}") format("woff2")' in faces[family]
    assert "font-weight:100 900" in faces["Montserrat"]
    assert '--ps-heading:"Montserrat","Poppins"' in css
    assert '--ps-serif:"Epika",' in css
    assert '--ps-display-wide:"Peak Grotesk Wide"' in css
    assert '--ps-display-acid:"Acid Grotesk"' in css
    assert '--ps-sans:"DM Sans"' in css


def test_every_font_url_in_base_css_has_a_local_file():
    """A brand/fonts url is uploaded at publish; the theme-CDN url has its
    offline copy in brand/fonts-display under the same filename."""
    for face in _faces(_base_css()).values():
        for url in _URL_RE.findall(face):
            name = Path(url).name
            assert (BRAND / "fonts" / name).is_file() or (BRAND / "fonts-display" / name).is_file(), url


def test_the_font_files_are_real_webfonts_and_the_licences_are_listed():
    for name in NEW_FILES + ["../fonts-display/pk-peak-grotesk-wide.woff2"]:
        assert (BRAND / "fonts" / name).read_bytes()[:4] == b"wOF2", name
    licences = (BRAND / "fonts" / "LICENSES.md").read_text()
    for family in FAMILIES:
        assert family in licences
    assert "SIL Open Font License" in (BRAND / "fonts" / "OFL-Montserrat.txt").read_text()
    assert not list((BRAND / "fonts").glob("*.pdf")), "never commit the EULA PDFs"


def test_tokens_json_matches_base_css():
    tokens = json.loads((BRAND / "tokens.json").read_text())
    assert tokens["font.heading.family"] == "Montserrat"
    assert tokens["font.serif.family"] == "Epika"
    assert tokens["font.body.family"] == "DM Sans"
    assert "Acid Grotesk" in tokens["font.display.acid"]
    assert "Peak Grotesk Wide" in tokens["font.display.wide"]


def test_no_trial_font_remains_in_brand_or_the_looks():
    hits = []
    for path in list(BRAND.rglob("*")) + list((REPO_ROOT / "cartridges").glob("*/looks/*/template.html")):
        if path.is_file() and "trial" in path.name.lower():
            hits.append(str(path))
        elif path.is_file() and path.suffix in (".css", ".json", ".md", ".html", ".yaml"):
            if re.search(r"trial", path.read_text(errors="ignore"), re.I):
                hits.append(str(path))
    if re.search(r"trial", (TENANT.root / "tenant.yaml").read_text(), re.I):
        hits.append("tenant.yaml")
    assert hits == []


def test_font_token_knows_the_new_families():
    assert look_design.font_token('"Epika",Georgia,serif') == "serif"
    assert look_design.font_token("var(--pk-serif)") == "serif"
    assert look_design.font_token('"Montserrat","Poppins",sans-serif') == "heading"
    assert look_design.font_token('"Acid Grotesk",sans-serif') == "display-acid"


# ---------------------------------------------------------------------------
# The storefront export and the publisher
# ---------------------------------------------------------------------------

def test_the_export_carries_the_four_font_faces_in_the_publishable_form(tmp_path):
    body, _manifest = build_shopify_body(_render_listicle(tmp_path))
    faces = _faces(body)
    assert set(faces) == FAMILIES
    assert set(_faces(font_face_css(TENANT))) == FAMILIES
    assert PGW_CDN in faces["Peak Grotesk Wide"]
    for name in NEW_FILES:
        assert f'url("brand/fonts/{name}")' in body
    assert "data:font" not in body  # the review inlining never reaches the export
    assert "fonts.googleapis.com" not in body


def test_font_files_lists_the_new_brand_fonts():
    assert [p.name for p in shopify_pub.font_files(TENANT)] == NEW_FILES


def test_rewriting_keeps_an_absolute_theme_cdn_url():
    """Regression: an entry was matched by filename only, so the theme-CDN
    Peak Grotesk Wide url (never uploaded by us) was dropped -- and its
    whole block with it -- as soon as brand/fonts held any file."""
    css = font_face_css(TENANT)
    out = shopify_pub.rewrite_font_face_urls(css, {n: f"https://cdn.shopify.com/files/{n}" for n in NEW_FILES})
    faces = _faces(out)
    assert set(faces) == FAMILIES
    assert PGW_CDN in faces["Peak Grotesk Wide"]
    assert "brand/fonts/" not in out


def _brand_copy(tmp_path):
    brand = tmp_path / "brand"
    shutil.copytree(BRAND / "fonts", brand / "fonts")
    shutil.copy(BRAND / "base.css", brand / "base.css")
    return types.SimpleNamespace(brand_dir=brand)


def test_publish_uploads_the_new_fonts_once_and_rewrites_every_face(tmp_path):
    tenant = _brand_copy(tmp_path)
    uploads = []

    class FakePublisher:
        def upload_fonts(self, manifest):
            uploads.append([m["filename"] for m in manifest])
            assert all(m["bytes"][:4] == b"wOF2" for m in manifest)
            return {m["filename"]: f"https://cdn.shopify.com/files/{m['filename']}" for m in manifest}, {}

    body = "<style>\n" + font_face_css(tenant) + "\n</style>"
    out = cli._publish_brand_fonts(tenant, FakePublisher(), body)
    assert uploads == [NEW_FILES]
    assert set(_faces(out)) == FAMILIES
    assert PGW_CDN in out and "brand/fonts/" not in out
    # a second publish reads the manifest and uploads nothing
    assert cli._publish_brand_fonts(tenant, FakePublisher(), body) == out
    assert uploads == [NEW_FILES]


def test_publish_dry_run_lists_the_fonts_the_next_publish_uploads(tmp_path, monkeypatch, capsys):
    from tests.test_cli_approve_publish import FakeTenant, _make_run, _patch_tenant, _publish_args
    monkeypatch.delenv("SHOPIFY_STORE", raising=False)
    monkeypatch.delenv("SHOPIFY_TOKEN", raising=False)
    tenant = FakeTenant(tmp_path, publisher="shopify")
    tenant.brand_dir = _brand_copy(tmp_path).brand_dir
    shopify_pub.save_font_cdn_manifest(tenant, {"Epika-Regular.woff2": "https://cdn.shopify.com/files/e.woff2"})
    _patch_tenant(monkeypatch, tenant)
    cli.cmd_publish(_publish_args(_make_run(tmp_path), dry_run=True))
    out = json.loads(capsys.readouterr().out)
    assert out["fonts"] == {"upload_on_next_publish": NEW_FILES[1:], "already_on_cdn": ["Epika-Regular.woff2"]}


# ---------------------------------------------------------------------------
# The review page and the thumbnail load the local files
# ---------------------------------------------------------------------------

def _data_uri(path):
    return "data:font/woff2;base64," + base64.b64encode(Path(path).read_bytes()).decode("ascii")


def test_inline_font_face_urls_uses_the_local_bytes_and_leaves_the_rest():
    css = 'a{color:red}\n' + font_face_css(TENANT)
    out = inline_font_face_urls(css, BRAND)
    assert out.startswith("a{color:red}")
    faces = _faces(out)
    assert set(faces) == FAMILIES
    assert _data_uri(BRAND / "fonts-display" / "pk-peak-grotesk-wide.woff2") in faces["Peak Grotesk Wide"]
    for family, name in (("Acid Grotesk", NEW_FILES[1]), ("Montserrat", NEW_FILES[2]), ("Epika", NEW_FILES[0])):
        assert _data_uri(BRAND / "fonts" / name) in faces[family]
    assert "brand/fonts/" not in out and "cdn.shopify.com" not in out
    # a face with no local file keeps its url untouched
    missing = '@font-face{font-family:"X";src:url("brand/fonts/X.woff2") format("woff2");}'
    assert inline_font_face_urls(missing, BRAND) == missing


def test_the_review_page_carries_every_face_from_the_local_files(tmp_path):
    _render_listicle(tmp_path)
    review_path = review.build_review_for_page(tmp_path, "listicle", tenant=TENANT)
    html = review_path.read_text()
    faces = _faces(html)
    assert set(faces) == FAMILIES
    assert all("data:font/woff2;base64," in f for f in faces.values())
    assert not re.search(r"url\(\"?brand/fonts", html)
    # index.html (what text checks and the export read) keeps the publishable urls
    index = (tmp_path / "listicle" / "index.html").read_text()
    assert "data:font" not in index and 'url("brand/fonts/Epika-Regular.woff2")' in index


def test_the_thumbnail_capture_frames_a_page_that_loads_the_local_fonts(tmp_path):
    """capture_thumb screenshots the library's review.html inside a frame
    page; the framed document must carry the four faces as data: URIs (the
    library build runs offline)."""
    _render_listicle(tmp_path)
    review_path = review.build_review_for_page(tmp_path, "listicle", tenant=TENANT)
    seen = {}

    class FakeProc:
        def poll(self):
            return 0

    def fake_popen(cmd, **_kwargs):
        frame = Path(cmd[-1].removeprefix("file://"))
        src = re.search(r'<iframe src="file://([^"]+)"', frame.read_text()).group(1)
        seen["faces"] = _faces(Path(src).read_text())
        png = next(a for a in cmd if a.startswith("--screenshot=")).split("=", 1)[1]
        Path(png).write_bytes(b"\x89PNG")
        return FakeProc()

    assert library.capture_thumb("chromium", review_path, tmp_path / "thumb.png", popen=fake_popen)
    assert set(seen["faces"]) == FAMILIES
    assert all("data:font/woff2;base64," in f for f in seen["faces"].values())


# ---------------------------------------------------------------------------
# The looks
# ---------------------------------------------------------------------------

_QUOTE_RULES = {
    "landing-open": r"\.adv-landing \.ld-quote p\{[^}]*var\(--pk-serif\)",
    "landing-proof": r"\.adv-landing \.ld-hq p\{[^}]*var\(--pk-serif\)",
    "listicle-open": r"\.op-pull blockquote\{[^}]*var\(--pk-serif\)",
}


@pytest.mark.parametrize("name", sorted(LOOKS))
def test_the_looks_set_the_quote_in_epika_and_keep_the_display_faces(name):
    text = LOOKS[name].read_text()
    assert "--pk-serif:var(--ps-serif,var(--pk-heading));" in text
    assert re.search(_QUOTE_RULES[name], text), name
    assert re.search(r"--wide\{[^}]*font-family:var\(--pk-display-wide\)", text)
    if name == "listicle-open":
        assert re.search(r"op-display--acid\{font-family:var\(--pk-display-acid\)", text)


def test_the_brand_lint_passes_on_a_fake_run_with_every_face(tmp_path):
    from tests.test_landing_cycle82 import _render as render_landing
    from tests.test_landing_proof_cycle85 import QUOTE, _facts_pack, _proof_page, _with_quote_source
    docs = []
    listicle_dir = _render_listicle(tmp_path / "l")
    docs.append((listicle_dir / "index.html").read_text())
    docs.append(build_shopify_body(listicle_dir)[0])
    quote = {"text": QUOTE, "author": "Dana R.", "claim_ids": ["reviews-live"]}
    landing_dir = render_landing(tmp_path / "p", page=_proof_page(proof={"quote": quote}),
                                 facts_pack=_with_quote_source(_facts_pack()))
    assert 'class="ld-hq"' in (landing_dir / "index.html").read_text()  # the Epika review quote renders
    docs.append((landing_dir / "index.html").read_text())
    docs.append(build_shopify_body(landing_dir)[0])
    for doc in docs:
        assert _lint(doc) == []
        assert set(_faces(doc)) == FAMILIES


def test_the_brand_lint_still_flags_a_stale_acid_grotesk_name():
    bad = ('<style>.x{font-family:"PK Acid Grotesk",sans-serif}</style>'
           '<main class="adv-wrap" data-pk-theme="open-2026-10-05"><h1>A</h1></main>')
    assert "Acid Grotesk outside its declared family" in _lint(bad)
