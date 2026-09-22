"""Cycle 64: one naming rule for a product, in every cartridge.

Owner decision 2026-09-22: a product's full name is "Peak <Model>" (tenant.yaml
product_name_format), used wherever a product is named as a title and at the
first mention in body copy; its short name is "<Model>", used in running text
after that and in every CTA ("Shop the Mini"). The long capacity/style title
("Mini 1-Person Infrared Sauna") is never a displayed name -- where a template
has a line for it, a short sentence-case descriptor stands on its own line.
The company is still "PEAK"; "Peak Saunas" is still a retired name.
"""
import copy
import json
import os
import re

import pytest

from tests.support import REPO_ROOT, TENANT
from harness import render as render_mod
from harness import repair
from harness import tenant as tenant_mod
from harness.ground import LocalFactsSource
from harness.prices import build_live_price_claims

AD_BRIEF = {"transcript_or_text": "", "hook": "", "promise": "", "angle": ""}

# Every product in the real catalog, and what the helper must return for it.
EXPECTED = {
    "Crown": ("Peak Crown", "Crown", "2-person infrared sauna"),
    "Mini": ("Peak Mini", "Mini", "1-person infrared sauna"),
    "Kilimanjaro": ("Peak Kilimanjaro", "Kilimanjaro", "5-person outdoor infrared sauna"),
    "Patagonia": ("Peak Patagonia", "Patagonia", "2-person outdoor infrared sauna"),
    "El Capitan": ("Peak El Capitan", "El Capitan", "4-person outdoor infrared sauna"),
    "Matterhorn": ("Peak Matterhorn", "Matterhorn", "3-person infrared sauna"),
    "Denali": ("Peak Denali", "Denali", "3-person infrared sauna"),
    "Rainier": ("Peak Rainier", "Rainier", "1-person infrared sauna"),
    "Shasta": ("Peak Shasta", "Shasta", "1-person infrared sauna"),
    "Fuji": ("Peak Fuji", "Fuji", "2-person infrared sauna"),
    "Everest": ("Peak Everest", "Everest", "2-person infrared sauna"),
}


def _catalog():
    return json.loads((TENANT.claims_dir / "products.json").read_text())["products"]


def _slug(model):
    return next(slug for slug, p in _catalog().items() if p["name"] == model)


def _names(result):
    return (result["full_name"], result["short_name"], result["descriptor"])


# ---------------------------------------------------------------------------
# 1. The helper
# ---------------------------------------------------------------------------

def test_the_tenant_declares_the_name_format():
    assert TENANT.get("product_name_format") == "Peak {model}"


def test_the_catalog_has_no_product_the_table_below_misses():
    assert sorted(p["name"] for p in _catalog().values()) == sorted(EXPECTED)


@pytest.mark.parametrize("model", sorted(EXPECTED))
def test_every_catalog_product_gets_the_owner_naming(model):
    product = next(p for p in _catalog().values() if p["name"] == model)
    assert _names(TENANT.product_names(product)) == EXPECTED[model]


@pytest.mark.parametrize("model", sorted(EXPECTED))
def test_the_model_is_derived_from_a_bare_storefront_title(model):
    """No curated `name`: the model comes from the title alone, with the old
    company prefix, the new brand word, or both in front of it."""
    product = next(p for p in _catalog().values() if p["name"] == model)
    rest = product["short_name"][len("Peak "):]
    for title in ("Peak Saunas " + rest, "Peak " + rest, "PEAK " + rest, rest):
        assert _names(TENANT.product_names({"title": title})) == EXPECTED[model], title


@pytest.mark.parametrize("title,expected", [
    ("Peak Saunas Shasta 1-Person Indoor Full Spectrum Infrared Sauna", ("Peak Shasta", "Shasta")),
    ("Peak Mini 1-Person Infrared Sauna", ("Peak Mini", "Mini")),
    ("Peak Kilimanjaro 3-Person Outdoor Infrared Sauna", ("Peak Kilimanjaro", "Kilimanjaro")),
    ("Peak Saunas El Capitan 4-Person Outdoor Infrared Sauna", ("Peak El Capitan", "El Capitan")),
    ("PEAK Mini", ("Peak Mini", "Mini")),
])
def test_raw_titles_as_the_live_feed_writes_them(title, expected):
    assert _names(TENANT.product_names(title))[:2] == expected


def test_each_facts_pack_shape_gives_the_same_names():
    """Old and new facts_packs carry the names under different keys; the
    renderer must get the same answer from each."""
    shapes = [
        {"name": "Mini", "short_name": "Peak Mini 1-Person Infrared Sauna"},    # facts_pack.product, before cycle 64
        {"name": "Mini", "seo_title": "Peak Mini 1-Person Infrared Sauna"},     # facts_pack.product, cycle 64
        {"name": "Mini 1-Person Infrared Sauna"},                              # model_options row, before cycle 64
        {"name": "Mini", "title": "Mini 1-Person Infrared Sauna"},             # comparison model
        {"name": "Mini 1-Person Infrared Sauna", "model_name": "Mini"},        # quiz model, before cycle 64
    ]
    for shape in shapes:
        assert _names(TENANT.product_names(shape)) == EXPECTED["Mini"], shape


def test_a_tenant_with_no_format_names_a_product_by_its_model_alone():
    template = tenant_mod.load_tenant("_template")
    names = template.product_names({"name": "One", "short_name": "One 2-Person Cabin"})
    assert _names(names) == ("One", "One", "2-person cabin")


def test_nothing_to_name_gives_empty_strings():
    assert _names(TENANT.product_names({})) == ("", "", "")


# ---------------------------------------------------------------------------
# 2. The writer is handed the names, never the long title as a name
# ---------------------------------------------------------------------------

def test_facts_pack_product_carries_full_and_short_name():
    pack = LocalFactsSource(TENANT.claims_dir).facts_for(_slug("Mini"), AD_BRIEF, include_listicle=True)
    product = pack["product"]
    assert product["full_name"] == "Peak Mini"
    assert product["name"] == "Mini"
    # the renderer derives the descriptor from the pack; the writer is not handed it
    assert TENANT.product_names(product)["descriptor"] == "1-person infrared sauna"
    # the long SEO title is kept for the JSON-LD only, under a key that says so
    assert product["seo_title"] == "Peak Mini 1-Person Infrared Sauna"
    assert "short_name" not in product


def test_model_rows_the_writer_sees_are_named_by_full_name():
    pack = LocalFactsSource(TENANT.claims_dir).facts_for(
        _slug("Fuji"), AD_BRIEF, include_listicle=True, include_comparison=True, include_quiz=True,
    )
    for row in pack["model_options"]:
        assert re.fullmatch(r"Peak [A-Z][\w ]+", row["name"]), row["name"]
        assert "-Person" not in row["name"]
    for m in pack["comparison"]["models"]:
        assert m["full_name"].startswith("Peak ") and "-Person" not in m["full_name"]
    for m in pack["quiz"]["models"]:
        assert TENANT.product_names(m)["full_name"] == "Peak " + m["model_name"]


def test_the_live_price_claim_names_the_product_by_full_name():
    claims = build_live_price_claims({"fuji": {"name": "Fuji", "price": "8250.00", "url": "u"}},
                                     "2026-09-22", show_compare_at_price=False)
    assert claims[0]["text"] == "The Peak Fuji is priced at $8,250."


def test_the_writer_prompt_states_the_rule():
    from harness import write
    text = write.global_voice_block(TENANT)
    assert "facts_pack.product.full_name" in text
    assert "facts_pack.product.seo_title" in text
    assert "facts_pack.product.short_name" not in text


@pytest.mark.parametrize("cartridge", ["article", "product-page", "longform", "listicle", "comparison", "quiz"])
def test_no_cartridge_tells_the_writer_to_name_the_product_by_short_name(cartridge):
    for name in ("cartridge.md", "schema.json"):
        text = (REPO_ROOT / "cartridges" / cartridge / name).read_text()
        assert "facts_pack.product.short_name" not in text, (cartridge, name)


# ---------------------------------------------------------------------------
# 3. The check: long title flagged, wrong-case brand word repaired
# ---------------------------------------------------------------------------

def _pack():
    return {"product": {"name": "Mini", "full_name": "Peak Mini", "url": "https://peaksaunas.com/products/x"},
            "verified_claims": [{"id": "price-mini", "text": "The PEAK Mini is priced at $5,450."}]}


def _page(text):
    return {"headline": "A plain headline", "hero": {"promise": text}}


def test_the_long_title_in_copy_is_flagged():
    problems = repair.find_product_name_violations(
        _page("The Mini 1-Person Infrared Sauna fits a small room."), _pack(), TENANT)
    assert [p["key"] for p in problems] == ["product_name:long_title:$.hero.promise"]
    assert "Peak Mini" in problems[0]["issue"]


@pytest.mark.parametrize("text", [
    "The Peak Mini fits a small room.",
    "The Mini fits a small room.",
    "Shop the Mini",
    "A 1-person sauna fits a small room.",
    "Download the Peak Saunas app to preheat.",
])
def test_the_owner_names_are_not_flagged(text):
    assert repair.find_product_name_violations(_page(text), _pack(), TENANT) == []


@pytest.mark.parametrize("text,fixed", [
    ("The PEAK Mini fits a small room.", "The Peak Mini fits a small room."),
    ("The PEAK MINI fits a small room.", "The Peak Mini fits a small room."),
    ("The Peak Saunas Mini fits a small room.", "The Peak Mini fits a small room."),
    ("PEAK El Capitan and PEAK Fuji", "Peak El Capitan and Peak Fuji"),
])
def test_a_wrong_brand_form_is_flagged_and_repaired(text, fixed):
    page = _page(text)
    problems = repair.check_page_gates(
        page, _pack(), "article", financing_lender=None, speaker_pov="brand",
        word_range=None, allowed_cta_texts=None, tenant=TENANT,
    )
    keys = [p.get("key") or "" for p in problems]
    assert "product_name:brand_form:$.hero.promise" in keys
    repair.apply_deterministic_fixes(page, problems, set(), cartridge_name="article",
                                     facts_pack=_pack(), tenant=TENANT)
    assert page["hero"]["promise"] == fixed
    assert repair.find_product_name_violations(page, _pack(), TENANT) == []
    assert repair.find_retired_name_violations(page, _pack(), TENANT) == []


def test_a_verbatim_verified_claim_quote_is_left_alone():
    page = _page("The PEAK Mini is priced at $5,450.")
    assert repair.find_product_name_violations(page, _pack(), TENANT) == []


def test_the_retired_name_gate_does_not_fire_on_the_owner_names():
    for text in ("The Peak Mini fits.", "Peak Fuji, Peak El Capitan and the Mini.", "Shop the Mini"):
        assert repair.find_retired_name_violations(_page(text), _pack(), TENANT) == [], text


def test_a_tenant_with_no_format_is_never_rewritten():
    template = tenant_mod.load_tenant("_template")
    page = _page("The ACME One fits.")
    assert repair.find_product_name_violations(page, {"product": {"name": "One"}}, template) == []


# ---------------------------------------------------------------------------
# 4. Every cartridge's rendered page
# ---------------------------------------------------------------------------

_LONG_RE = re.compile(r"\b(?:%s)\s+\d+-Person\b" % "|".join(re.escape(m) for m in EXPECTED), re.IGNORECASE)
_WRONG_BRAND_RE = re.compile(r"\b(?:PEAK|Peak Saunas)\s+(?:%s)\b" % "|".join(re.escape(m) for m in EXPECTED))


def _visible_text(html):
    html = re.sub(r"<(script|style)\b.*?</\1>", " ", html, flags=re.DOTALL)
    html = re.sub(r"<title>.*?</title>", " ", html, flags=re.DOTALL)
    return " ".join(re.sub(r"<[^>]+>", " | ", html).split())


def _assert_owner_naming(html):
    text = _visible_text(html)
    assert not _LONG_RE.search(text), _LONG_RE.search(text).group(0)
    assert not _WRONG_BRAND_RE.search(text), _WRONG_BRAND_RE.search(text).group(0)
    title = re.search(r"<title>(.*?)</title>", html, flags=re.DOTALL)
    if title:
        assert not _LONG_RE.search(title.group(1)), title.group(1)


@pytest.fixture(scope="module")
def fake_run_dir(tmp_path_factory):
    """One offline run of all four cartridges, shared by the tests below.
    Module-scoped, so it runs before conftest's per-test isolated_tenant_paths
    -- it redirects the run's writes into tmp itself, the same way."""
    from evals import fake_run

    base = tmp_path_factory.mktemp("cycle64-isolation")
    (base / "out").mkdir()
    (base / "runs").mkdir()
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(type(TENANT), "out_dir", property(lambda self: base / "out"))
        mp.setattr(type(TENANT), "runs_dir", property(lambda self: base / "runs"))
        mp.setattr(type(TENANT), "evals_path", property(lambda self: base / "evals" / "scores.jsonl"))
        # run_once loads the tenant .env into os.environ; conftest's per-test
        # environ restore does not cover a module-scoped fixture, so restore
        # here or later tests see the real store credentials.
        saved_environ = dict(os.environ)
        try:
            code, run_dir, pages = fake_run.run_once(
                str(TENANT.fixtures_dir / "founder-warranty-demo.txt"), tenant="peak-saunas",
                cartridges="listicle,comparison,quiz,product-page", seed=42,
            )
        finally:
            os.environ.clear()
            os.environ.update(saved_environ)
    assert code == 0
    return run_dir


def test_listicle_model_picker_names_models_by_full_name(fake_run_dir):
    html = (fake_run_dir / "listicle" / "index.html").read_text()
    _assert_owner_naming(html)
    rows = json.loads((fake_run_dir / "facts_pack.json").read_text())["model_options"]
    names = [r["name"] for r in rows]
    assert names, "the run built no model picker rows"
    assert all(re.fullmatch(r"Peak [A-Z][\w ]+", n) for n in names), names
    for name in names:     # each row's own element, whichever look drew it
        assert re.search(r">\s*" + re.escape(name) + r"\s*<", html), name


def test_comparison_column_heads_use_full_name_and_links_the_model(fake_run_dir):
    html = (fake_run_dir / "comparison" / "index.html").read_text()
    _assert_owner_naming(html)
    heads = re.findall(r'<p class="cmp-model">([^<]+)</p>', html)
    assert "Peak Fuji" in heads
    assert all(h.startswith("Peak ") for h in heads), heads


def test_quiz_result_card_uses_full_name_descriptor_line_and_short_cta(fake_run_dir):
    html = (fake_run_dir / "quiz" / "index.html").read_text()
    _assert_owner_naming(html)
    titles = re.findall(r'<h3 class="qz-card-name">([^<]+)</h3>', html)
    assert "Peak Fuji" in titles and "Peak Mini" in titles
    assert '<p class="qz-card-desc">1-person infrared sauna</p>' in html
    assert "Shop the Mini" in html
    assert "Shop the Peak" not in html


def test_pdp_buy_panel_uses_full_name_and_short_cta(fake_run_dir):
    html = (fake_run_dir / "product-page" / "index.html").read_text()
    _assert_owner_naming(html)
    assert '<h1 class="pp-title">Peak Fuji</h1>' in html
    assert "Shop the Fuji" in html
    assert "<title>Peak Fuji</title>" in html


def test_classic_product_page_title_is_renderer_owned(fake_run_dir, tmp_path):
    facts_pack = json.loads((fake_run_dir / "facts_pack.json").read_text())
    page = copy.deepcopy(json.loads((fake_run_dir / "product-page" / "page.json").read_text()))
    page["look"] = "classic"
    page["hero"]["product_name"] = "Fuji 2-Person Full Spectrum Infrared Sauna"   # an older writer's field
    out = render_mod.render_page(
        cartridge_name="product-page", page=page, ad_brief={}, facts_pack=facts_pack,
        cartridges_dir=REPO_ROOT / "cartridges", brand_dir=TENANT.brand_dir,
        templates_dir=REPO_ROOT / "harness" / "templates", out_dir=tmp_path / "product-page",
        published="2026-09-22", updated="2026-09-22", tenant=TENANT, download_assets=False,
    )
    html = out.read_text()
    assert "<h1>Peak Fuji</h1>" in html
    _assert_owner_naming(html)


@pytest.mark.parametrize("page_name", ["product-page", "comparison", "quiz", "listicle"])
def test_rerendered_image_alt_text_uses_full_name(fake_run_dir, tmp_path, monkeypatch, page_name):
    """`harness rerender` of an existing run, with images: every product
    image's alt text leads with the full name, never the long title."""
    import argparse
    import shutil
    from harness import cli

    run_dir = tmp_path / fake_run_dir.name
    shutil.copytree(fake_run_dir, run_dir)

    def fake_download(asset, dest_dir, **kwargs):
        dest_dir.mkdir(parents=True, exist_ok=True)
        name = f"{asset['id']}-800.jpg"
        (dest_dir / name).write_bytes(b"fake-jpeg")
        return {"path": dest_dir / name, "width": 900, "height": 1200,
                "variants": [{"width": 800, "jpg": f"assets/{name}", "webp": None}], "cutout": True}

    monkeypatch.setattr(render_mod, "download_asset", fake_download)
    args = argparse.Namespace(run_dir=str(run_dir), page=page_name, note="", look=None, tenant=TENANT.name)
    assert cli.cmd_rerender(args) == 0
    html = (run_dir / page_name / "index.html").read_text()
    _assert_owner_naming(html)
    alts = [a for a in re.findall(r'alt="([^"]*)"', html) if not a.endswith(" logo")]
    assert alts, "no product image rendered"
    assert all(a.startswith("Peak ") for a in alts), alts
    assert not any(_LONG_RE.search(a) for a in alts), alts
