"""Cycle 82: the longform cartridge's looks -- `open` (the cycle 79 design
language, the ad first) and `classic` (the template before cycle 82).

Asserted offline: the look resolution (classic is the harness default, the
peak-saunas tenant pins open, classic on request); an open render carries
the first-screen hooks (eyebrow line, accent span, stats strip from verified
claims, closing band) and the FAQPage JSON-LD; the CTA phrase appears in
the first screen, the closing band and the sticky bar only; the storefront
export keeps its invariants; a classic render is the old markup.
"""
import copy
import re

from harness import first_screen, looks
from harness.page_body import build_shopify_body
from harness.render import render_page
from tests.support import REPO_ROOT, TENANT
from tests.test_brand_lint_cycle78 import _PAGE_SCOPED, _css, _lint, _rules
from tests.test_product_page_looks import _rich_facts_pack
from tests.test_render import AD_BRIEF, LONGFORM_PAGE


def _render(tmp_path, look=None):
    page = copy.deepcopy(LONGFORM_PAGE)
    if look:
        page["look"] = look
    out = tmp_path / "longform"
    render_page(
        cartridge_name="longform", page=page, ad_brief=AD_BRIEF, facts_pack=_rich_facts_pack(),
        cartridges_dir=REPO_ROOT / "cartridges", brand_dir=TENANT.brand_dir,
        templates_dir=REPO_ROOT / "harness" / "templates", out_dir=out,
        published="2026-10-07", updated="2026-10-07", tenant=TENANT, download_assets=False,
    )
    return out


def test_open_is_the_peak_default_and_classic_is_on_request():
    assert looks.cartridge_looks("longform") == ("open", "classic")
    assert looks.DEFAULT_LOOKS["longform"] == "classic"
    assert looks.resolve_look("longform", tenant=TENANT) == "open"
    assert looks.resolve_look("longform", "classic", tenant=TENANT) == "classic"


def test_the_accent_span_is_always_part_of_the_headline():
    assert first_screen.longform_accent("The hidden cost of a hidden price") == "a hidden price"
    assert first_screen.longform_accent("Two sentences here. The second one wins.") == "The second one wins."
    assert first_screen.longform_accent("A sauna: built for small rooms") == "built for small rooms"
    assert first_screen.longform_accent("Too short") == ""


def test_an_open_render_leads_with_the_first_screen(tmp_path):
    html = (_render(tmp_path) / "index.html").read_text()
    assert 'class="pk-lp adv-longform look-open"' in html
    assert '<span class="lo-accent">a hidden price</span>' in html
    assert 'class="lo-stat"' in html and "47 reviews" in html      # verified rating in the strip
    assert 'class="lo-close"' in html
    assert '"@type": "FAQPage"' in html


def test_the_cta_phrase_is_in_three_places_only(tmp_path):
    html = (_render(tmp_path) / "index.html").read_text()
    assert html.count(">See pricing<") == 3
    hero, rest = html.split('class="lo-act"', 1)[1].split("</header>", 1)
    assert ">See pricing<" in hero
    assert ">See pricing<" in rest.split('class="lo-close"', 1)[1].split("</section>", 1)[0]
    assert ">See pricing<" in html.split('class="adv-sticky-cta lo-sticky"', 1)[1]


def test_the_open_export_keeps_its_invariants_and_the_brand_lint(tmp_path):
    out = _render(tmp_path)
    export, _ = build_shopify_body(out)
    assert export.startswith("<style>\n") and export.count("</style>") == 1
    assert not re.search(r'href="https?://(?:www\.)?peaksaunas\.com', export)
    assert [s for s, _b in _rules(_css(export)) if not s.startswith(".adv-wrap") and not _PAGE_SCOPED.search(s)] == []
    assert _lint((out / "index.html").read_text()) == [] and _lint(export) == []


def test_classic_renders_the_old_markup(tmp_path):
    html = (_render(tmp_path, look="classic") / "index.html").read_text()
    assert 'class="adv-longform"' in html and "look-open" not in html
    assert 'class="adv-final-cta"' in html
