"""Cycle 54: the product-page cartridge's LOOKS -- `pdp` (default) and
`classic` -- and the `pdp` look's renderer-owned sections and gates.

What is asserted here, offline:
  - look resolution: the shared rule in harness/looks.py (explicit wins,
    else the tenant's pinned looks, else pdp), the flag parsers, and
    `harness rerender --look` switching the template without touching copy;
  - every pdp section renders from one rich fixture, and each
    "only when verified" line disappears when its fact is missing (the
    25-review rating floor, the HSA/FSA line);
  - cycle 62 (simpler): the sections are the live PDP's bones in order --
    gallery + buy panel, one promise band, what's included, specs in a
    details row, exactly 5 FAQs, one closing CTA -- with no compare table,
    proof tiles, benefit cards, chips or eyebrow, one CTA text in exactly
    three buttons (buy panel, closing, sticky bar), the short model name,
    and 250-450 words of body copy; an old page.json's extra fields are
    ignored;
  - the gallery's markup: one slide per image, the first shown without JS,
    the thumbnail row as plain images, and its image order;
  - the storefront export keeps the gallery script and the sticky bar;
  - the look uses brand tokens only (no colour literal, no radius number)
    and sentence case (no uppercase transform, the headline-case class
    switched off);
  - the gates: the included list and FAQ answers need claim ids, exactly 5
    FAQs and 2 promise paragraphs, the promise line may not state an
    uncited number, power/outlet copy may not contradict the product's
    electrical claim, no stock marketing phrases, no urgency, no
    renderer-owned keys.
"""
import argparse
import copy
import json
import re

import pytest

from evals import fake_run
from harness import claims, cli, looks, pdp, render as render_mod, repair, runstate
from harness.page_body import build_shopify_body
from harness.render import render_page
from tests.support import REPO_ROOT, TENANT
from tests.test_render import AD_BRIEF, FACTS_PACK, PRODUCT_PAGE_PAGE

CARTRIDGE_DIR = REPO_ROOT / "cartridges" / "product-page"
PDP_TEMPLATE = CARTRIDGE_DIR / "looks" / "pdp" / "template.html"
SLUG = FACTS_PACK["product"]["slug"]
STOREFRONT_IDS = [f"asset-{SLUG}-{i}" for i in range(1, 9)]


def _rich_facts_pack(*, review_count=47, hsa=False, compare_models=3):
    fp = copy.deepcopy(FACTS_PACK)
    fp["assets"] = [
        {"id": i, "url": f"https://cdn.shopify.com/fuji-{n}.png", "kind": "image", "alt": "x"}
        for n, i in enumerate(STOREFRONT_IDS, 1)
    ] + [{"id": "asset-drive-lifestyle-1", "url": "https://cdn.example/l1.jpg", "kind": "lifestyle", "alt": "x"}]
    fp["specs"] = [
        {"label": "Capacity", "value": "2-Person", "claim_id": "spec-fuji-capacity"},
        {"label": "Cabin material", "value": "Canadian red cedar", "claim_id": "spec-fuji-cabin-material"},
        {"label": "Max temperature", "value": "150°F", "claim_id": "spec-fuji-max-temperature"},
        {"label": "Audio", "value": "Two Bluetooth speakers", "claim_id": "spec-fuji-audio"},
    ]
    src = "https://peaksaunas.com/products/fuji"
    fp["verified_claims"] += [
        {"id": s["claim_id"], "text": f"Fuji -- {s['label']}: {s['value']}.", "category": "spec", "source": src}
        for s in fp["specs"]
    ]
    text = f"Rated 4.79 out of 5 across {review_count} reviews on Judge.me (fetched 2026-09-19)."
    fp["reviews_summary"] = {"text": text, "claim_ids": ["reviews-live"]}
    fp["verified_claims"].append({"id": "reviews-live", "text": text, "category": "trust", "source": src})
    fp["verified_claims"].append({
        "id": "shipping-free", "text": "Free shipping on every order. Orders leave the warehouse in 2-4 business days.",
        "category": "trust", "source": src,
    })
    if hsa:
        fp["verified_claims"].append({"id": "hsa-eligible", "text": "Eligible for HSA/FSA payment with a letter of medical necessity.",
                                      "category": "trust", "source": src})
    models = [
        {"name": FACTS_PACK["product"]["short_name"], "url": FACTS_PACK["product"]["url"], "current": True},
        {"name": "Second Model", "url": "https://peaksaunas.com/products/second", "current": False},
        {"name": "Third Model", "url": "https://peaksaunas.com/products/third", "current": False},
    ][:compare_models]
    fp["model_options"] = [{"name": m["name"], "url": m["url"], "price_text": "$1", "fit": "2-Person", "claim_ids": []}
                           for m in models]
    if compare_models >= 2:
        fp["model_compare"] = {
            "models": models,
            "rows": [
                {"label": "Price", "cells": [{"text": f"${n},000", "claim_ids": ["price-fuji"]} for n in range(len(models))]},
                {"label": "Capacity", "cells": [{"text": "2-Person", "claim_ids": ["spec-fuji-capacity"]}] + [None] * (len(models) - 1)},
            ],
        }
    return fp


def _page(**over):
    page = copy.deepcopy(PRODUCT_PAGE_PAGE)
    page.update(over)
    return page


def _legacy_page():
    """A page.json written before cycle 62 (the two demo runs' shape): the
    long-form CTA, proof tiles, proof bullets, a trust strip, a writer spec
    table, four promise paragraphs and seven FAQs."""
    page = _page(cta_text="Shop the Peak Fuji 2-Person Infrared Sauna")
    page["ad_proof"] = [
        {"label": "Standard outlet", "text": "Plugs into any wall outlet you already have.", "claim_ids": ["price-fuji"]},
        {"label": "Red light", "text": "Red light therapy comes standard here.", "claim_ids": ["gbrain-allowlist-red-light"]},
        {"label": "Owned", "text": "The company is owned in the US.", "claim_ids": ["gbrain-allowlist-us-owned"]},
    ]
    page["proof_bullets"] = [
        {"label": "Red light therapy", "text": "Medical-grade red light therapy is included standard.", "claim_ids": ["gbrain-allowlist-red-light"]},
        {"label": "Full spectrum infrared", "text": "360 full spectrum infrared heater placement.", "claim_ids": ["gbrain-allowlist-360-full-spectrum"]},
        {"label": "US-owned", "text": "PEAK is a US-owned company.", "claim_ids": ["gbrain-allowlist-us-owned"]},
    ]
    page["trust_strip"] = {"shipping": {"text": "Trust strip shipping words.", "claim_ids": ["shipping-policy"]}}
    page["specs_table"] = [{"label": "Writer spec row", "value": "Writer value"}]
    page["tagline"] = {"lines": ["Tagline line one.", "Tagline line two."]}
    del page["included"]
    page["angle_section"]["paragraphs"] += [{"text": "Third paragraph words."}, {"text": "Fourth paragraph words."}]
    page["faq"]["questions"] += [
        {"question": "Sixth question?", "answer": "Sixth answer."},
        {"question": "Seventh question?", "answer": "Seventh answer."},
    ]
    return page


def _look_text(html):
    """The visible words inside the pdp look: no style, script or tags."""
    body = html[html.index('<div class="adv-product-page look-pdp">'):]
    body = body[:body.index("<script>")]
    body = re.sub(r"<style[^>]*>.*?</style>", " ", body, flags=re.S)
    body = re.sub(r"<[^>]+>", " ", body)
    return " ".join(body.split())


def _render(tmp_path, page=None, facts_pack=None, tenant=TENANT, **kwargs):
    kwargs.setdefault("download_assets", False)
    return render_page(
        cartridge_name="product-page", page=page or _page(), ad_brief=AD_BRIEF,
        facts_pack=facts_pack or _rich_facts_pack(), cartridges_dir=REPO_ROOT / "cartridges",
        brand_dir=TENANT.brand_dir, templates_dir=REPO_ROOT / "harness" / "templates",
        out_dir=tmp_path / "product-page", published="2026-09-22", updated="2026-09-22",
        tenant=tenant, **kwargs,
    ).read_text()


class _Pinned:
    """A tenant stand-in that answers tenant.get() from a dict, falling back
    to the real test tenant for everything else."""

    def __init__(self, **pins):
        self._pins = pins

    def get(self, key, default=None):
        if key in self._pins:
            return self._pins[key]
        return TENANT.get(key, default)

    def __getattr__(self, name):
        return getattr(TENANT, name)


# ---------------------------------------------------------------------------
# Look resolution
# ---------------------------------------------------------------------------

def test_product_page_has_two_looks_and_pdp_is_the_default():
    assert looks.cartridge_looks("product-page") == ("pdp", "classic")
    assert looks.resolve_look("product-page", tenant=TENANT) == "pdp"
    assert looks.resolve_look("product-page", "classic", tenant=TENANT) == "classic"
    assert looks.resolve_look("article", tenant=TENANT) == ""


def test_an_unknown_product_page_look_is_refused():
    with pytest.raises(ValueError):
        looks.resolve_look("product-page", "cards", tenant=TENANT)


def test_a_tenant_pin_narrows_the_product_page_looks():
    tenant = _Pinned(**{"cartridges.product-page.looks": ["classic", "brochure"]})
    assert looks.tenant_looks("product-page", tenant) == ("classic",)
    assert looks.resolve_look("product-page", tenant=tenant) == "classic"
    assert looks.resolve_look("product-page", "pdp", tenant=tenant) == "pdp"   # explicit wins
    nothing_valid = _Pinned(**{"cartridges.product-page.looks": ["brochure"]})
    assert looks.tenant_looks("product-page", nothing_valid) == ("pdp", "classic")


def test_the_run_flag_applies_only_to_the_cartridge_that_has_that_look():
    assert looks.requested_for("product-page", "pdp") == "pdp"
    assert looks.requested_for("listicle", "pdp") is None
    assert looks.requested_for("product-page", "cards") is None
    assert set(looks.all_looks()) >= {"pdp", "classic", "cards", "lander"}


def test_the_listicle_rule_is_unchanged_through_the_shared_helper():
    from harness import listicle
    for style, look in listicle.LOOK_BY_STYLE.items():
        assert looks.resolve_look("listicle", style=style, tenant=TENANT) == look


def test_the_parsers_take_the_product_page_looks():
    parser = cli.build_parser()
    assert parser.parse_args(["run", "ad.txt", "--look", "pdp"]).look == "pdp"
    assert parser.parse_args(["rerender", "out/r", "--page", "product-page", "--look", "classic"]).look == "classic"
    with pytest.raises(SystemExit):
        parser.parse_args(["run", "ad.txt", "--look", "brochure"])


def test_the_cartridge_template_is_only_a_dispatcher():
    text = (CARTRIDGE_DIR / "template.html").read_text()
    body = re.sub(r"\{#.*?#\}", "", text, flags=re.DOTALL).strip()
    assert body == '{% extends "looks/" ~ (look or "pdp") ~ "/template.html" %}'


def test_default_render_is_pdp_and_classic_is_still_available(tmp_path):
    html = _render(tmp_path)
    assert "adv-product-page look-pdp" in html
    classic = _render(tmp_path, page=_legacy_page() | {"look": "classic"})
    assert "look-pdp" not in classic
    assert 'class="adv-proof-bullets"' in classic


# ---------------------------------------------------------------------------
# Sections
# ---------------------------------------------------------------------------

def test_every_pdp_section_renders_from_a_rich_facts_pack(tmp_path):
    html = _render(tmp_path)
    # buy panel: the short model name, the promise as its one-line
    # descriptor, rating, price, financing, CTA, the fixed reassurance lines
    assert '<h1 class="pp-title">Fuji</h1>' in html
    assert PRODUCT_PAGE_PAGE["hero"]["promise"] in html
    assert ">$8,250<" in html
    assert "Financing is available at checkout." in html
    assert "Limited lifetime warranty; full terms by component are published on the warranty page." in html
    assert "Free shipping. Orders leave the warehouse in 2-4 business days." in html
    assert "Rated 4.79 out of 5 across 47 reviews on Judge.me." in html
    assert "(fetched" not in html
    # promise band, included list, specs, FAQ, closing, sticky bar
    assert PRODUCT_PAGE_PAGE["angle_section"]["heading"] in html
    for p in PRODUCT_PAGE_PAGE["angle_section"]["paragraphs"]:
        assert p["text"] in html
    for item in PRODUCT_PAGE_PAGE["included"]:
        assert f"<li>{item['text']}</li>" in html
    assert 'id="pp-specs"' in html and "Two Bluetooth speakers" in html
    assert html.count('class="pp-faq-item"') == 5
    assert 'class="pp-close"' in html
    assert "data-pp-sticky" in html


def test_pdp_sections_follow_the_live_pdp_order(tmp_path):
    html = _render(tmp_path)
    markers = ['class="pp-hero"', 'data-pp-gallery', 'class="pp-buy"', 'id="pp-promise"',
               'id="pp-included"', 'id="pp-specs"', 'id="pp-faq"', 'class="pp-close"', "data-pp-sticky"]
    positions = [html.index(m) for m in markers]
    assert positions == sorted(positions)
    # the specs sit in a details/summary row, the live PDP's collapsible tab
    specs = html[html.index('id="pp-specs"'):html.index('id="pp-faq"')]
    assert '<details class="pp-row"' in specs and '<summary class="pp-row-q">Specifications</summary>' in specs


def test_pdp_has_no_compare_table_proof_tiles_chips_or_extra_ctas(tmp_path):
    # an old page.json (the demo runs' shape) and a facts pack that still
    # carries model_compare and a review count: none of it becomes a
    # compare table, a tile, a card, a chip or a second CTA phrase
    html = _render(tmp_path, page=_legacy_page())
    for gone in ("pp-compare", "pp-proof", "pp-tile", "pp-benefit", "pp-reviews", "pp-eyebrow",
                 "pp-link", "pp-btn--ghost", "pp-quote", "adv-tagline", "Third Model",
                 "Plugs into any wall outlet", "Trust strip shipping words", "Writer spec row",
                 "Tagline line one", "Third paragraph words", "Sixth question"):
        assert gone not in html, gone
    # exactly three CTA buttons -- buy panel, closing, sticky bar -- one
    # text, one destination
    buttons = re.findall(r'<a class="pp-btn[^"]*" href="([^"]+)">([^<]+)</a>', html)
    assert len(buttons) == 3
    assert {href for href, _ in buttons} == {PRODUCT_PAGE_PAGE["cta_url"]}
    assert {text for _, text in buttons} == {"Shop the Fuji"}


def test_the_cta_uses_the_short_model_name(tmp_path):
    # the fixture's own CTA is already short; an old page's long-form CTA
    # ("Shop the <long catalog title>") is shown with the short name, which
    # is the allowed text harness/repair.py resolves today
    for page in (_page(), _legacy_page()):
        html = _render(tmp_path, page=page)
        assert html.count(">Shop the Fuji<") == 3
        assert "Shop the Peak Fuji 2-Person Infrared Sauna" not in html


def test_exactly_five_faqs_and_two_promise_paragraphs_render(tmp_path):
    html = _render(tmp_path, page=_legacy_page())
    assert html.count('class="pp-faq-item"') == 5
    promise = html[html.index('id="pp-promise"'):html.index('id="pp-included"')]
    assert promise.count('<p class="pp-body">') == 2


def test_an_old_page_without_an_included_list_uses_its_proof_bullet_labels(tmp_path):
    html = _render(tmp_path, page=_legacy_page())
    included = html[html.index('id="pp-included"'):html.index('id="pp-specs"')]
    assert re.findall(r"<li>([^<]+)</li>", included) == ["Red light therapy", "Full spectrum infrared", "US-owned"]


def test_the_body_copy_is_250_to_450_words(tmp_path):
    words = len(_look_text(_render(tmp_path)).split())
    assert 250 <= words <= 450, words


def test_no_compare_at_price_unless_the_facts_pack_carries_one(tmp_path):
    assert 'class="pp-price-was"' not in _render(tmp_path)
    fp = _rich_facts_pack()
    fp["product"]["compare_at_price"] = "9995.00"
    assert '<s class="pp-price-was">$9,995</s>' in _render(tmp_path, facts_pack=fp)


def test_the_rating_line_respects_the_review_floor(tmp_path):
    html = _render(tmp_path, facts_pack=_rich_facts_pack(review_count=10))
    assert "across 10 reviews" not in html
    assert 'id="pp-reviews"' not in html


def test_the_hsa_line_renders_only_when_verified(tmp_path):
    assert "HSA" not in _render(tmp_path)
    assert "Eligible for HSA/FSA payment" in _render(tmp_path, facts_pack=_rich_facts_pack(hsa=True))


def test_the_disclosure_label_renders_only_when_the_tenant_sets_one(tmp_path):
    body = _render(tmp_path).split("<body>", 1)[1]
    assert "adv-badge" not in body
    html = _render(tmp_path, tenant=_Pinned(disclosure_label="Paid Partnership"))
    assert '<span class="adv-badge">Paid Partnership</span>' in html


def test_renderer_owned_claims_join_the_sources_list(tmp_path):
    html = _render(tmp_path)
    sources = html[html.index('class="adv-sources"'):]
    assert "https://peaksaunas.com/products/fuji" in sources


def test_ground_builds_a_claim_backed_compare_from_the_tenants_own_models():
    from harness.ground import LocalFactsSource
    fp = LocalFactsSource(TENANT.claims_dir).facts_for(SLUG, AD_BRIEF, include_product_page=True)
    compare = fp["model_compare"]
    assert 2 <= len(compare["models"]) <= 3
    assert compare["models"][0]["current"] is True
    assert compare["rows"][0]["label"] == "Price"
    assert 2 <= len(compare["rows"]) <= 6
    verified = {c["id"] for c in fp["verified_claims"]}
    for row in compare["rows"]:
        assert len(row["cells"]) == len(compare["models"])
        for cell in row["cells"]:
            assert cell is None or (cell["claim_ids"] and set(cell["claim_ids"]) <= verified)
    # a run without product-page selected is unchanged
    assert "model_compare" not in LocalFactsSource(TENANT.claims_dir).facts_for(SLUG, AD_BRIEF)


# ---------------------------------------------------------------------------
# Gallery
# ---------------------------------------------------------------------------

def test_gallery_markup_and_no_js_fallback(tmp_path):
    html = _render(tmp_path)
    slides = re.findall(r'<figure class="pp-slide( is-active)?" data-pp-slide="(\d+)">', html)
    thumbs = re.findall(r'data-pp-thumb="(\d+)"', html)
    assert 4 <= len(slides) <= pdp.GALLERY_MAX
    assert len(thumbs) == len(slides)
    # only the first slide is shown in markup, so a reader with no JS sees it
    assert [bool(active) for active, _ in slides] == [True] + [False] * (len(slides) - 1)
    css = html[html.index(".adv-product-page.look-pdp{"):]
    assert ".adv-product-page.look-pdp .pp-slide{margin:0;display:none}" in css
    assert ".adv-product-page.look-pdp .pp-slide.is-active{display:block}" in css
    # thumbnails are plain images inside buttons, and the first slide loads eagerly
    first = html[html.index('data-pp-slide="0"'):html.index('data-pp-slide="1"')]
    assert 'loading="eager"' in first
    assert re.search(r'<button type="button" class="pp-thumb[^"]*" data-pp-thumb="1"[^>]*><img ', html)
    assert "data-pp-thumb" in html[html.index("<script>", html.index("look-pdp")):]


def test_gallery_order_hero_first_then_writer_picks_then_unused_storefront_images():
    page = _page(gallery_order=[STOREFRONT_IDS[4], "asset-not-in-the-pack"])
    page["angle_section"]["image"] = {"asset_id": STOREFRONT_IDS[2]}
    ids = pdp.gallery_asset_ids(page, _rich_facts_pack())
    assert ids[0] == STOREFRONT_IDS[0]                       # the hero
    assert ids[1] == STOREFRONT_IDS[4]                       # the writer's pick
    assert "asset-not-in-the-pack" not in ids                # unknown ids are ignored
    assert STOREFRONT_IDS[2] not in ids                      # already shown in the promise band
    assert len(ids) == pdp.GALLERY_MAX


def test_the_promise_image_never_repeats_the_gallery():
    fp = _rich_facts_pack()
    page = _page()
    gallery = pdp.gallery_asset_ids(page, fp)
    assert pdp.promise_asset_id(page, fp, gallery) == "asset-drive-lifestyle-1"   # a lifestyle photo
    page["angle_section"]["image"] = {"asset_id": STOREFRONT_IDS[7]}
    gallery = pdp.gallery_asset_ids(page, fp)
    assert STOREFRONT_IDS[7] not in gallery
    assert pdp.promise_asset_id(page, fp, gallery) == STOREFRONT_IDS[7]           # the writer's own pick
    page["angle_section"]["image"] = {"asset_id": gallery[1]}
    assert pdp.promise_asset_id(page, fp, gallery) == "asset-drive-lifestyle-1"   # never a gallery image


def test_the_storefront_export_keeps_the_gallery_script_and_sticky_bar(tmp_path):
    _render(tmp_path)
    body, _manifest = build_shopify_body(tmp_path / "product-page")
    assert "data-pp-thumb" in body and "data-pp-slide" in body
    assert "data-pp-sticky" in body
    assert "[data-pp-thumb]" in body                         # the swap script itself
    assert ".pp-sticky--hidden" in body                      # its CSS travelled too
    assert '<div class="pp-top">' in body                    # a classed header becomes a div


# ---------------------------------------------------------------------------
# Brand tokens only
# ---------------------------------------------------------------------------

def test_the_pdp_look_has_no_colour_literal_and_no_radius_number():
    text = PDP_TEMPLATE.read_text()
    assert re.findall(r"#[0-9a-fA-F]{3,8}\b", text) == []
    assert re.findall(r"\brgba?\(", text) == []
    css = text[text.index("<style>\n/*"):]
    assert re.findall(r"border-radius:\s*(?!var\()([^;}]+)", css) == []
    for colour_prop in re.findall(r"(?:^|[;{])\s*(?:color|background|border-color):([^;}]+)", css):
        value = colour_prop.strip()
        assert value.startswith("var(") or value in ("transparent", "inherit"), value
    # Cycle 63 (owner override, 2026-09-22): 4px on boxes on every page
    # type, not square -- --pp-radius/--pp-radius-btn must resolve through
    # --ps-radius-box now, and every pdp box (gallery main image frame,
    # thumbnails, the buy/sticky/closing button -- they all share .pp-btn)
    # still reaches it through one of those two vars.
    stripped = css.replace(" ", "")
    assert "--pp-radius:var(--ps-radius-box,4px);" in stripped
    assert "--pp-radius-btn:var(--ps-radius-box,4px);" in stripped
    for selector in [".pp-stage{", ".pp-promise-media{"]:
        rule = stripped[stripped.index(selector):stripped.index("}", stripped.index(selector)) + 1]
        assert "border-radius:var(--pp-radius)" in rule, rule
    for selector in [".pp-thumb{", ".pp-btn{"]:
        rule = stripped[stripped.index(selector):stripped.index("}", stripped.index(selector)) + 1]
        assert "border-radius:var(--pp-radius-btn)" in rule, rule


def test_the_pdp_look_is_sentence_case_everywhere():
    # cycle 62: the owner asked for sentence case everywhere but the
    # wordmark, so the look switches the tenant's uppercase-headline class
    # off inside itself (structure.css would otherwise uppercase its h1-h3)
    # and never uppercases a button, label or table header of its own
    css = PDP_TEMPLATE.read_text().replace(" ", "")
    assert "text-transform:uppercase" not in css
    assert ".adv-case-upper.adv-product-page.look-pdph1" in css
    assert "text-transform:none" in css


def test_no_tenant_words_in_the_new_engine_and_look_files():
    from tests.test_tenant import CARTRIDGE_AND_BLOCKS_TENANT_WORDS
    for path in (PDP_TEMPLATE, REPO_ROOT / "harness" / "pdp.py", REPO_ROOT / "harness" / "looks.py"):
        text = path.read_text()
        for word in CARTRIDGE_AND_BLOCKS_TENANT_WORDS:
            assert not re.search(r"\b" + re.escape(word) + r"\b", text, re.IGNORECASE), (path.name, word)


# ---------------------------------------------------------------------------
# Gates
# ---------------------------------------------------------------------------

def _keys(problems):
    return {p["key"] for p in problems}


def test_the_canned_page_passes_the_product_page_gates():
    assert pdp.find_product_page_violations(_page(), FACTS_PACK) == []


def test_the_included_list_needs_three_to_six_items_each_with_a_claim_id():
    page = _page(included=[{"text": "Plain words."}])
    keys = _keys(pdp.find_product_page_violations(page, FACTS_PACK))
    assert {"product-page:included_count", "product-page:included_claims:0"} <= keys
    page = _page(included=[{"text": "Item", "claim_ids": ["price-fuji"]}] * 7)
    assert "product-page:included_count" in _keys(pdp.find_product_page_violations(page, FACTS_PACK))


def test_faq_needs_exactly_five_and_cited_facts():
    page = _page(faq={"questions": [{"question": "How hot?", "answer": "It reaches 150F."}]})
    keys = _keys(pdp.find_product_page_violations(page, FACTS_PACK))
    assert {"product-page:faq_count", "product-page:faq_claims:0"} <= keys
    six = _page()
    six["faq"]["questions"].append({"question": "One more?", "answer": "Yes."})
    assert "product-page:faq_count" in _keys(pdp.find_product_page_violations(six, FACTS_PACK))
    # the listicle keeps its own 5-7 rule
    from harness import listicle
    assert "listicle:faq_count" not in _keys(listicle.find_faq_violations(six))


def test_the_promise_band_needs_exactly_two_paragraphs():
    page = _page()
    page["angle_section"]["paragraphs"].append({"text": "A third paragraph."})
    assert "product-page:angle_paragraphs" in _keys(pdp.find_product_page_violations(page, FACTS_PACK))


def _power_facts(text):
    fp = copy.deepcopy(FACTS_PACK)
    fp["verified_claims"].append({"id": "spec-fuji-electrical-requirement", "text": text, "category": "spec",
                                  "source": "https://peaksaunas.com/products/fuji"})
    fp["specs"] = fp["specs"] + [{"label": "Electrical requirement", "value": text,
                                  "claim_id": "spec-fuji-electrical-requirement"}]
    return fp


def test_power_copy_may_not_contradict_the_products_electrical_claim():
    dedicated = _power_facts("Fuji -- Electrical requirement: 120V / 20A dedicated outlet.")
    page = _page()
    page["angle_section"]["paragraphs"][0]["text"] = "Assembly is simple, with no electrician needed."
    page["faq"]["questions"][4]["answer"] = "It plugs into a standard household outlet."
    keys = _keys(pdp.find_product_page_violations(page, dedicated))
    assert {"product-page:power:$.angle_section.paragraphs[0].text",
            "product-page:power:$.faq.questions[4].answer"} <= keys
    # the same words are fine for a model whose claim says a standard outlet
    standard = _power_facts("Mini -- Plugs into a standard 120V household outlet, no electrician needed.")
    assert not [k for k in _keys(pdp.find_product_page_violations(page, standard)) if "power" in k]
    # ...and for that model, a stated dedicated requirement is the
    # contradiction, while a question or a negated phrase is not
    page["faq"]["questions"][0]["question"] = "Do I need an electrician to install it?"
    page["faq"]["questions"][1]["answer"] = "No. There is no electrician or dedicated circuit required."
    page["faq"]["questions"][2]["answer"] = "It needs a dedicated 20A circuit."
    power = [k for k in _keys(pdp.find_product_page_violations(page, standard)) if "power" in k]
    assert power == ["product-page:power:$.faq.questions[2].answer"]
    page["faq"] = copy.deepcopy(PRODUCT_PAGE_PAGE["faq"])
    # and a page that says what the claim says passes
    page["angle_section"]["paragraphs"][0]["text"] = "It runs on a dedicated 120V / 20A outlet."
    page["angle_section"]["paragraphs"][0]["claim_ids"] = ["spec-fuji-electrical-requirement"]
    page["faq"]["questions"][4]["answer"] = "Financing is offered at checkout."
    assert not [k for k in _keys(pdp.find_product_page_violations(page, dedicated)) if "power" in k]


def test_no_stock_marketing_phrases():
    page = _page()
    page["angle_section"]["heading"] = "Transform your evenings"
    page["faq"]["questions"][0]["answer"] = "Whether you're new to sauna or not, it is simple."
    page["included"][0]["text"] = "A home sanctuary"
    keys = _keys(pdp.find_product_page_violations(page, FACTS_PACK))
    assert {"product-page:stock_phrase:$.angle_section.heading",
            "product-page:stock_phrase:$.faq.questions[0].answer",
            "product-page:stock_phrase:$.included[0].text"} <= keys


def test_the_fixed_strings_and_writer_lines_carry_no_stock_phrases():
    template = re.sub(r"<(style|script)>.*?</\1>", " ", PDP_TEMPLATE.read_text(), flags=re.S)
    text = template + " ".join(pdp.writer_rules_lines())
    text += (CARTRIDGE_DIR / "cartridge.md").read_text() + (CARTRIDGE_DIR / "schema.json").read_text()
    for phrase in ("whether you", "elevate", "unlock", "transform", "sanctuary", "game-changer"):
        assert phrase not in text.lower(), phrase


def test_the_promise_line_may_not_state_an_uncited_number():
    page = _page(hero={**PRODUCT_PAGE_PAGE["hero"], "promise": "Save 30% on your sauna"})
    assert "product-page:promise_claims" in _keys(pdp.find_product_page_violations(page, FACTS_PACK))
    page["hero"]["claim_ids"] = ["price-fuji"]
    assert "product-page:promise_claims" not in _keys(pdp.find_product_page_violations(page, FACTS_PACK))
    # a capacity token is not an asserted number
    fp = {**FACTS_PACK, "digit_exempt_terms": []}
    ok = _page(hero={**PRODUCT_PAGE_PAGE["hero"], "promise": "A 2-Person cabin with the price shown"})
    assert "product-page:promise_claims" not in _keys(pdp.find_product_page_violations(ok, fp))


def test_no_urgency_and_no_renderer_owned_keys():
    page = _page(gallery=["x"])
    page["faq"]["questions"][0]["answer"] = "Limited time only, so act now."
    keys = _keys(pdp.find_product_page_violations(page, FACTS_PACK))
    assert "product-page:renderer_owned:gallery" in keys
    assert "product-page:urgency:limited time" in keys
    assert "product-page:urgency:act now" in keys


def test_check_page_gates_runs_the_product_page_checks():
    page = _page()
    del page["included"]
    problems = repair.check_page_gates(
        page, FACTS_PACK, "product-page", financing_lender=None, speaker_pov="third_person",
        word_range=None, allowed_cta_texts=None,
    )
    assert "product-page:included_count" in _keys([p for p in problems if "key" in p])


def test_the_included_list_counts_as_the_product_benefit_section():
    # claims.MIN_BENEFIT_CLAIMS is unchanged (3); the new page carries its
    # product-benefit claim ids on the included list, an old page on its
    # proof bullets
    assert claims.find_benefit_claim_shortfall(_page(), FACTS_PACK, "product-page") == []
    thin = _page(included=[{"text": "Price on the page", "claim_ids": ["price-fuji"]}])
    assert claims.find_benefit_claim_shortfall(thin, FACTS_PACK, "product-page")
    assert claims.find_benefit_claim_shortfall(_legacy_page(), FACTS_PACK, "product-page") == []


def test_an_invented_claim_id_on_an_included_item_fails_the_shared_gate():
    page = _page()
    page["included"][0]["claim_ids"] = ["spec-made-up"]
    with pytest.raises(claims.ClaimsGateFailure):
        claims.gate_page_json(page, FACTS_PACK, "product-page")


def test_a_redundant_nested_cta_url_is_removed_deterministically():
    page = _page()
    page["included"][0]["cta_url"] = page["cta_url"]
    failures = claims.find_second_cta_violation(page)
    assert failures
    fixed = repair.apply_deterministic_fixes(page, failures, {c["id"] for c in FACTS_PACK["verified_claims"]},
                                             cartridge_name="product-page")
    assert fixed == 1
    assert "cta_url" not in page["included"][0]


def test_gallery_order_ids_are_never_read_as_prose():
    page = _page(gallery_order=[f"asset-{SLUG}-2"])            # the slug carries a banned product-handle word
    assert claims.find_forbidden_terms(page) == []


def test_the_writer_is_told_what_the_gate_measures():
    from harness import write
    _schema, kwargs = write.build_initial_write_request(
        cartridge_name="product-page", cartridges_dir=REPO_ROOT / "cartridges", ad_brief=AD_BRIEF,
        facts_pack=FACTS_PACK, model="m", tenant=TENANT,
    )
    system = json.dumps(kwargs["system"])
    assert "promise band and FAQ answer the ad the visitor clicked" in system
    assert "faq.questions has exactly 5 entries" in system
    assert "included has 3-6 items" in system
    assert "angle_section.paragraphs has exactly 2" in system
    assert "ad_proof" not in system and "proof_bullets" not in system
    _schema, article = write.build_initial_write_request(
        cartridge_name="article", cartridges_dir=REPO_ROOT / "cartridges", ad_brief=AD_BRIEF,
        facts_pack=FACTS_PACK, model="m", tenant=TENANT,
    )
    assert "included has" not in json.dumps(article["system"])


# ---------------------------------------------------------------------------
# A whole offline run, and harness rerender --look
# ---------------------------------------------------------------------------

def test_fake_run_product_page_renders_the_pdp_look_and_records_it():
    fixture = TENANT.fixtures_dir / "founder-warranty-demo.txt"
    exit_code, run_dir, pages = fake_run.run_once(str(fixture), tenant="peak-saunas", cartridges="product-page")
    assert exit_code == 0
    page = json.loads(pages[0].read_text())
    assert page["look"] == "pdp"
    html = (run_dir / "product-page" / "index.html").read_text()
    assert "adv-product-page look-pdp" in html
    assert 'id="pp-compare"' not in html                  # cycle 62: no compare table, even with model_compare
    assert html.count('class="pp-faq-item"') == 5
    assert runstate.load_state(run_dir)["product-page"]["look"] == "pdp"


@pytest.fixture
def run_dir(tmp_path, monkeypatch):
    run_dir = tmp_path / "20260922-120000-pdp-run-abcd"
    (run_dir / "product-page").mkdir(parents=True)
    (run_dir / "facts_pack.json").write_text(json.dumps(_rich_facts_pack()))
    (run_dir / "ad_brief.json").write_text(json.dumps(AD_BRIEF))
    (run_dir / "product-page" / "page.json").write_text(json.dumps(_legacy_page()))
    runstate.init_state(run_dir, pages=["product-page"])

    def fake_download(asset, dest_dir, **kwargs):
        dest_dir.mkdir(parents=True, exist_ok=True)
        name = f"{asset['id']}-800.jpg"
        (dest_dir / name).write_bytes(b"fake-jpeg")
        return {"path": dest_dir / name, "width": 900, "height": 1200,
                "variants": [{"width": 800, "jpg": f"assets/{name}", "webp": None}], "cutout": True}

    monkeypatch.setattr(render_mod, "download_asset", fake_download)
    return run_dir


def _args(run_dir, **over):
    base = dict(run_dir=str(run_dir), page="product-page", note="", look=None, tenant=TENANT.name)
    base.update(over)
    return argparse.Namespace(**base)


def test_rerender_look_switches_the_product_page_without_touching_the_copy(run_dir):
    before = json.loads((run_dir / "product-page" / "page.json").read_text())
    assert cli.cmd_rerender(_args(run_dir)) == 0
    assert "look-pdp" in (run_dir / "product-page" / "index.html").read_text()
    assert cli.cmd_rerender(_args(run_dir, look="classic")) == 0
    html = (run_dir / "product-page" / "index.html").read_text()
    assert "look-pdp" not in html and 'class="adv-proof-bullets"' in html
    after = json.loads((run_dir / "product-page" / "page.json").read_text())
    assert after["look"] == "classic"
    assert {k: v for k, v in after.items() if k != "look"} == before
    assert runstate.load_state(run_dir)["product-page"]["look"] == "classic"


def test_rerender_refuses_a_listicle_look_for_the_product_page(run_dir, capsys):
    assert cli.cmd_rerender(_args(run_dir, look="cards")) == 1
    assert "is not a product-page look" in capsys.readouterr().err
