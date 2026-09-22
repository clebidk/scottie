"""Cycle 52 (the PEAK rebrand): the three engine-side changes it needed.

1. `product_display_strip_prefix` -- a storefront's own product titles lead
   with the full company name. That prefix must not reach a reader and must
   not reach the writer, while every slug, URL and claim id keeps it.
2. `brand.headline_case` -- a brand may want uppercase headlines. That is a
   CSS rule carried by one class, never a rewrite of the writer's copy.
3. The looks and the structural stylesheet resolve a brand token before
   their own literal, so a brand with square corners, dark-on-accent text
   or a dark band of its own can say so.
"""
import json
import re
import shutil

import pytest

from tests.support import REPO_ROOT, TENANT
from harness import render as render_mod
from harness import tenant as tenant_mod
from harness.ground import LocalFactsSource

PREFIX = "Peak Saunas "
LOOKS_DIR = REPO_ROOT / "cartridges" / "listicle" / "looks"
STRUCTURE_CSS = (REPO_ROOT / "harness" / "structure.css").read_text()
_STYLE_RE = re.compile(r"<style>(.*?)</style>", re.DOTALL)


# ---------------------------------------------------------------------------
# 1a. The rule itself
# ---------------------------------------------------------------------------

def test_the_tenant_declares_the_prefix_its_storefront_actually_uses():
    assert TENANT.get("product_display_strip_prefix") == PREFIX


@pytest.mark.parametrize("raw,expected", [
    ("Peak Saunas Fuji 2-Person Indoor Sauna", "Fuji 2-Person Indoor Sauna"),
    ("peak saunas Fuji", "Fuji"),                 # matched case-insensitively
    ("Fuji 2-Person Indoor Sauna", "Fuji 2-Person Indoor Sauna"),  # already stripped
    ("", ""),
    (None, ""),
])
def test_display_product_name_strips_the_prefix_and_nothing_else(raw, expected):
    assert TENANT.display_product_name(raw) == expected


def test_stripping_is_idempotent_so_a_call_site_may_be_defensive():
    once = TENANT.display_product_name("Peak Saunas Everest 2-Person Infrared Sauna")
    assert TENANT.display_product_name(once) == once


def test_a_title_that_is_only_the_prefix_keeps_its_name_rather_than_vanishing():
    assert TENANT.display_product_name("Peak Saunas") == "Peak Saunas"


def test_a_tenant_with_no_prefix_configured_is_unaffected():
    template = tenant_mod.load_tenant("_template")
    assert template.display_product_name("Acme Fuji") == "Acme Fuji"


# ---------------------------------------------------------------------------
# 1b. The DISPLAY path
# ---------------------------------------------------------------------------

def test_image_alt_text_never_shows_the_storefront_title_prefix():
    alt = render_mod.asset_alt({"kind": "lifestyle"}, PREFIX + "Fuji", tenant=TENANT)
    assert alt == "Fuji – lifestyle photo"


def test_a_sources_list_label_never_shows_the_prefix():
    label = render_mod.source_label(
        "https://peaksaunas.com/products/anything",
        product_name=PREFIX + "Fuji", tenant=TENANT,
    )
    assert PREFIX not in label
    assert "Fuji" in label


def test_product_json_ld_names_the_product_without_the_prefix():
    facts_pack = {"product": {"name": PREFIX + "Fuji", "short_name": PREFIX + "Fuji",
                              "url": "https://peaksaunas.com/products/fuji", "price": "8250.00"}}
    ld = render_mod.build_json_ld("product-page", {}, facts_pack, "2026-09-22", "2026-09-22", tenant=TENANT)
    assert ld["name"] == "Fuji"
    # the URL is an identifier, not copy -- untouched
    assert ld["url"] == "https://peaksaunas.com/products/fuji"


# ---------------------------------------------------------------------------
# 1c. The WRITER path
# ---------------------------------------------------------------------------

@pytest.fixture
def prefixed_claims_dir(tmp_path):
    """A copy of the real claims store whose product titles carry the
    storefront prefix, the way the live feed writes them."""
    dest = tmp_path / "claims"
    shutil.copytree(TENANT.claims_dir, dest)
    path = dest / "products.json"
    data = json.loads(path.read_text())
    for product in data["products"].values():
        for key in ("name", "short_name", "title"):
            if product.get(key):
                product[key] = PREFIX + product[key]
    path.write_text(json.dumps(data, indent=2))
    return dest


def _fuji_slug():
    data = json.loads((TENANT.claims_dir / "products.json").read_text())
    return next(slug for slug, p in data["products"].items() if p["name"] == "Fuji")


def test_the_writer_is_handed_product_names_without_the_prefix(prefixed_claims_dir):
    source = LocalFactsSource(prefixed_claims_dir)
    pack = source.facts_for(_fuji_slug(),
                            {"transcript_or_text": "", "hook": "", "promise": "", "angle": ""},
                            include_listicle=True)
    assert not pack["product"]["name"].startswith(PREFIX)
    assert not pack["product"]["short_name"].startswith(PREFIX)
    assert pack["product"]["name"] == "Fuji"
    for option in pack["model_options"]:
        assert not option["name"].startswith(PREFIX), option["name"]


def test_the_slug_and_the_url_keep_the_prefix_they_are_identifiers(prefixed_claims_dir):
    slug = _fuji_slug()
    source = LocalFactsSource(prefixed_claims_dir)
    pack = source.facts_for(slug, {"transcript_or_text": "", "hook": "", "promise": "", "angle": ""})
    assert pack["product"]["slug"] == slug
    assert pack["product"]["url"].startswith("https://")
    assert "peak-saunas" in pack["product"]["url"]


def test_digit_exempt_terms_carry_both_forms(prefixed_claims_dir):
    """claims.py subtracts these before looking for an uncited bare digit. A
    page quotes the display form, but a verified claim's own text can still
    carry the raw catalog one, so both have to be in the list."""
    source = LocalFactsSource(prefixed_claims_dir)
    pack = source.facts_for(_fuji_slug(), {"transcript_or_text": "", "hook": "", "promise": "", "angle": ""})
    terms = set(pack["digit_exempt_terms"])
    assert PREFIX + "Fuji" in terms
    assert "Fuji" in terms


# ---------------------------------------------------------------------------
# 2. Headline case
# ---------------------------------------------------------------------------

def test_the_tenant_asks_for_uppercase_headlines():
    assert TENANT.get("brand.headline_case") == "upper"
    assert render_mod.wrap_class(TENANT) == "adv-wrap adv-case-upper"


def test_a_tenant_that_does_not_ask_for_it_keeps_the_plain_wrapper():
    assert render_mod.wrap_class(tenant_mod.load_tenant("_template")) == "adv-wrap"


def test_the_case_rule_exists_in_the_structural_layer_and_in_every_look():
    assert ".adv-case-upper h1" in STRUCTURE_CSS
    for template in sorted(LOOKS_DIR.glob("*/template.html")):
        css = _STYLE_RE.search(template.read_text()).group(1)
        assert ".adv-case-upper" in css, template.parent.name
        assert "text-transform:uppercase" in css.replace(" ", "")


def test_headline_case_is_css_only_and_never_rewrites_the_copy(tmp_path):
    """The gate, the claims scan and REVIEW.md must all still see the
    headline the writer actually wrote."""
    from tests.test_listicle import AD_BRIEF, RICH_FACTS_PACK, _listicle_page
    page = _listicle_page()
    page["look"] = "cards"
    out = render_mod.render_page(
        cartridge_name="listicle", page=page, ad_brief=AD_BRIEF, facts_pack=RICH_FACTS_PACK,
        cartridges_dir=REPO_ROOT / "cartridges", brand_dir=TENANT.brand_dir,
        templates_dir=REPO_ROOT / "harness" / "templates", out_dir=tmp_path / "listicle",
        published="2026-09-22", updated="2026-09-22", tenant=TENANT, download_assets=False,
    )
    html = out.read_text()
    assert 'class="adv-wrap adv-case-upper"' in html
    assert page["headline"] in html            # the writer's own casing, verbatim
    assert page["headline"].upper() not in html


# ---------------------------------------------------------------------------
# 3. Brand tokens reach the shapes and the on-accent colour
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("look", ["cards", "editorial", "lander", "pillars", "scorecard"])
def test_no_look_hardcodes_the_colour_of_text_on_the_accent(look):
    css = _STYLE_RE.search((LOOKS_DIR / look / "template.html").read_text()).group(1)
    assert "--pk-on-accent:var(--ps-on-accent,#fff)" in css.replace(" ", "")


@pytest.mark.parametrize("look", ["cards", "editorial", "lander", "pillars", "scorecard"])
def test_every_radius_in_a_look_resolves_a_brand_token(look):
    """A brand with square corners sets one token; no look may pin a number
    that the brand cannot reach."""
    css = _STYLE_RE.search((LOOKS_DIR / look / "template.html").read_text()).group(1)
    hardcoded = re.findall(r"border-radius:\s*(?!var\()([^;}]+)", css)
    assert hardcoded == [], f"{look}: {hardcoded}"


def test_the_structural_stylesheet_resolves_brand_tokens_for_shape_and_cta():
    assert "border-radius: var(--ps-radius-btn, 4px)" in STRUCTURE_CSS   # .adv-cta
    assert "background: var(--ps-accent, var(--adv-accent))" in STRUCTURE_CSS
    assert "color: var(--ps-on-accent, #fff)" in STRUCTURE_CSS
    # and the harness's own defaults are untouched for a tenant with no brand
    root = re.search(r":root \{(.*?)\}", STRUCTURE_CSS, re.DOTALL).group(1)
    assert "--adv-accent: #b3541e" in root
    assert "--adv-bg: #ffffff" in root


def test_the_tenants_own_tokens_carry_the_finalized_palette():
    css = (TENANT.brand_dir / "base.css").read_text()
    for token, value in [
        ("--ps-accent", "#F27046"), ("--ps-on-accent", "#181918"),
        ("--ps-bg", "#EFE3D2"), ("--ps-bg-muted", "#C0C8C3"),
        ("--ps-ink-dark", "#181918"), ("--ps-on-dark", "#EFE3D2"),
        ("--ps-editorial", "#702B33"), ("--ps-band", "#483215"),
        ("--ps-star-on", "#181918"),
        ("--ps-radius-card", "0px"), ("--ps-radius-btn", "0px"),
        ("--ps-radius-pill", "0px"), ("--ps-radius-round", "0px"),
    ]:
        assert f"{token}:{value}" in css.replace(" ", ""), token
    assert "16C47F" not in css.upper()


# ---------------------------------------------------------------------------
# 4. The self-hosted webfont reaches the storefront export
# ---------------------------------------------------------------------------

def test_the_tenant_self_hosts_the_licensed_face_and_falls_back_for_the_other():
    css = (TENANT.brand_dir / "base.css").read_text()
    assert "@font-face" in css
    assert (TENANT.brand_dir / "fonts" / "Epika-Regular.woff2").exists()
    # the unlicensed face is a fallback stack only -- no @font-face for it
    assert "Acid Grotesk" in css
    assert "Acid Grotesk license pending; fallback in use" in css
    font_faces = re.findall(r"@font-face\s*\{[^}]*\}", css, re.DOTALL)
    assert len(font_faces) == 1
    assert "Acid" not in font_faces[0]


def test_the_export_carries_the_font_face_rules_the_document_head_owns(tmp_path):
    """`harness shopify-body` keeps only what was inside <body>, so a brand's
    @font-face lived in the head and never reached a published page."""
    from harness.page_body import build_shopify_body, font_face_css
    from tests.test_listicle import AD_BRIEF, RICH_FACTS_PACK, _listicle_page

    page = _listicle_page()
    page["look"] = "editorial"
    render_mod.render_page(
        cartridge_name="listicle", page=page, ad_brief=AD_BRIEF, facts_pack=RICH_FACTS_PACK,
        cartridges_dir=REPO_ROOT / "cartridges", brand_dir=TENANT.brand_dir,
        templates_dir=REPO_ROOT / "harness" / "templates", out_dir=tmp_path / "listicle",
        published="2026-09-22", updated="2026-09-22", tenant=TENANT, download_assets=False,
    )
    body, _manifest = build_shopify_body(tmp_path / "listicle")
    assert "@font-face" in body
    assert "Epika-Regular.woff2" in body
    assert font_face_css(TENANT) in body


def test_publishing_rewrites_the_font_urls_to_the_cdn(tmp_path):
    """The relative brand/fonts/... url in the export would 404 on a
    storefront; the publisher swaps in the uploaded CDN url."""
    from harness.page_body import font_face_css
    from harness.publishers import shopify as shopify_pub

    exported = "<style>\n" + font_face_css(TENANT) + "\n</style>"
    rewritten = shopify_pub.rewrite_font_face_urls(exported, {
        "Epika-Regular.woff2": "https://cdn.shopify.com/files/Epika-Regular.woff2",
        "Epika-Regular.otf": "https://cdn.shopify.com/files/Epika-Regular.otf",
    })
    assert "brand/fonts/" not in rewritten
    assert "https://cdn.shopify.com/files/Epika-Regular.woff2" in rewritten

