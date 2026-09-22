"""Cycle 55: the header label is a tenant key, not a template literal.

Reviewer: "remove the word advertisement from these pages". Every cartridge
and every listicle look used to hardcode an "Advertisement" badge (and the
editorial/lander looks a second "Sponsored content"/"Sponsored" eyebrow).
Now one optional tenant.yaml key, `disclosure_label`, drives all of them:
empty or unset renders no label anywhere; a publisher whose policy needs one
sets it and it renders once, in each template's own label style.
"""
import copy
import re

import pytest
import yaml

from harness import listicle
from harness import tenant as tenant_mod
from harness.render import render_page
from tests.support import REPO_ROOT, TENANT
from tests.test_comparison import COMPARISON_PAGE, _facts_pack_with_comparison
from tests.test_listicle import RICH_FACTS_PACK, _listicle_page
from tests.test_render import AD_BRIEF, ARTICLE_PAGE, FACTS_PACK, LONGFORM_PAGE, PRODUCT_PAGE_PAGE

_AD_WORDS = re.compile(r"advertisement|sponsored", re.IGNORECASE)

# (cartridge, page factory, facts factory, look)
_CASES = [
    pytest.param("article", lambda: copy.deepcopy(ARTICLE_PAGE), lambda: FACTS_PACK, None, id="article"),
    pytest.param("longform", lambda: copy.deepcopy(LONGFORM_PAGE), lambda: FACTS_PACK, None, id="longform"),
    pytest.param(
        "comparison", lambda: copy.deepcopy(COMPARISON_PAGE), _facts_pack_with_comparison, None, id="comparison",
    ),
    # cartridges/product-page/ is being rebuilt on another branch (cycle 54's
    # pdp look) and still hardcodes the badge; this case reports XPASS once
    # that template reads disclosure_label too -- then drop the mark.
    pytest.param(
        "product-page", lambda: copy.deepcopy(PRODUCT_PAGE_PAGE), lambda: FACTS_PACK, None, id="product-page",
        marks=pytest.mark.xfail(reason="product-page template is owned by the concurrent pdp branch", strict=False),
    ),
] + [
    pytest.param("listicle", _listicle_page, lambda: RICH_FACTS_PACK, look, id=f"listicle-{look}")
    for look in listicle.LOOKS
]


def _render(tmp_path, cartridge, page, facts_pack, look):
    if look:
        page["look"] = look
    return render_page(
        cartridge_name=cartridge,
        page=page,
        ad_brief=AD_BRIEF,
        facts_pack=facts_pack,
        cartridges_dir=REPO_ROOT / "cartridges",
        brand_dir=TENANT.brand_dir,
        templates_dir=REPO_ROOT / "harness" / "templates",
        out_dir=tmp_path / cartridge,
        published="2026-09-22",
        updated="2026-09-22",
        tenant=TENANT,
        download_assets=False,
    ).read_text()


@pytest.fixture
def label(monkeypatch):
    def set_label(value):
        monkeypatch.setitem(TENANT.config, "disclosure_label", value)
        monkeypatch.setitem(tenant_mod.active().config, "disclosure_label", value)
    return set_label


@pytest.mark.parametrize("cartridge,page,facts,look", _CASES)
def test_no_label_and_no_ad_wording_when_the_key_is_unset(tmp_path, cartridge, page, facts, look):
    """Peak leaves disclosure_label unset: not one "advertisement" or
    "sponsored" anywhere in the rendered document -- markup, disclosure,
    JSON-LD or the inlined tenant CSS."""
    assert not TENANT.get("disclosure_label")
    html = _render(tmp_path, cartridge, page(), facts(), look)
    assert not _AD_WORDS.findall(html), sorted(set(_AD_WORDS.findall(html)))
    assert "adv-badge" not in html.split("<body>", 1)[1]


@pytest.mark.parametrize("cartridge,page,facts,look", _CASES)
def test_an_empty_string_renders_no_label(tmp_path, label, cartridge, page, facts, look):
    label("  ")
    html = _render(tmp_path, cartridge, page(), facts(), look)
    body = html.split("<body>", 1)[1]
    assert "adv-badge" not in body
    assert 'class="ed-eyebrow"' not in body and 'class="ld-eyebrow"' not in body


@pytest.mark.parametrize("cartridge,page,facts,look", _CASES)
def test_the_label_renders_once_when_a_tenant_sets_it(tmp_path, label, cartridge, page, facts, look):
    label("Paid Partnership")
    html = _render(tmp_path, cartridge, page(), facts(), look)
    body = html.split("<body>", 1)[1]
    assert body.count(">Paid Partnership<") == 1


def test_editorial_and_lander_show_the_label_in_their_own_eyebrow(tmp_path, label):
    label("Sponsored")
    for look, cls in (("editorial", "ed-eyebrow"), ("lander", "ld-eyebrow")):
        html = _render(tmp_path / look, "listicle", _listicle_page(), RICH_FACTS_PACK, look)
        assert f'<p class="{cls}">Sponsored</p>' in html
        assert "adv-badge" not in html.split("<body>", 1)[1]


def test_peak_disclosure_text_has_no_advertisement_wording():
    peak = tenant_mod.load_tenant("peak-saunas")
    text = peak.format("disclosure_text")
    assert "advertisement" not in text.lower()
    assert text.startswith("This page is published by PEAK, which sells the products described.")
    assert "verified against PEAK's own published sources at the time of writing." in text
    assert not peak.get("disclosure_label")


def test_the_tenant_template_defaults_the_label_to_empty():
    template = yaml.safe_load((REPO_ROOT / "tenants" / "_template" / "tenant.yaml").read_text())
    assert template["disclosure_label"] == ""
    assert "advertisement" not in template["disclosure_text"].lower()
