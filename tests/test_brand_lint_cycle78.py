"""Cycle 78: brand lint over every cartridge and look, as rendered for PEAK.

The generator must match the live storefront theme (docs/FIXLOG.md cycle
78). Each page is rendered from the existing test fixtures and checked twice
-- the review document and the storefront export (`harness shopify-body`):

- none of the retired colours (orange, cream, sage, cedar, green, cyan);
  cycle 79: nor the stone #F0E5D3 or the grey #F1F1F1 panels;
- cycle 79: Acid Grotesk is approved (the display first screen) -- only
  through its declared family, never as an uppercase heading rule;
- cycle 79: the page background is white; no box (border or fill) on the
  listicle's byline, trust/stats, proof lines or FAQ;
- no gradient and no box-shadow other than `none`;
- no `text-transform: uppercase` that reaches an h1/h2/h3;
- exactly one <h1>;
- the version marker (data-pk-theme) is in the markup;
- in the export, every CSS rule is scoped to the page wrapper.
"""
import copy
import re

import pytest

from harness import css_scope
from harness.page_body import build_shopify_body
from harness.render import render_page
from tests.support import REPO_ROOT, TENANT

BANNED_COLOURS = ("#F27046", "#F37047", "#EFE3D2", "#EFE8DA", "#C0C8C3", "#BFC6C1",
                  "#16C47F", "#11BDFB", "#483215",
                  # cycle 79: stone and the grey panels are retired too
                  "#F0E5D3", "#F1F1F1", "#F2F2F2")
MARKER = 'data-pk-theme="open-2026-10-05"'
LISTICLE_LOOKS = ("open",)
HERO_STYLES = ("face", "story", "display")

_STYLE_RE = re.compile(r"<style\b[^>]*>(.*?)</style>", re.S | re.I)
_HEADING_RE = re.compile(r"<(h[123])\b([^>]*)>", re.I)
_CLASS_RE = re.compile(r'class="([^"]*)"')
_STYLE_ATTR_RE = re.compile(r'style="([^"]*)"')


# ---------------------------------------------------------------------------
# Rendering every cartridge/look from the fixtures other tests already use
# ---------------------------------------------------------------------------

def _listicle(look, tmp_path, hero_style=None):
    from tests.test_listicle import _listicle_page
    from tests.test_listicle_looks import _render
    page = _listicle_page()
    if hero_style:
        page["hero_style"] = hero_style
    _render(look, tmp_path, page=page)
    return tmp_path / "listicle"


def _comparison(tmp_path):
    from tests.test_comparison import _render
    _render(tmp_path)
    return tmp_path / "comparison"


def _quiz(tmp_path):
    from tests.test_quiz import _render
    _render(tmp_path)
    return tmp_path / "quiz"


def _product_page(look, tmp_path):
    from tests.test_product_page_looks import _page, _render
    _render(tmp_path, page=_page(look=look))
    return tmp_path / "product-page"


def _plain(cartridge, tmp_path):
    from tests.test_render import AD_BRIEF, ARTICLE_PAGE, FACTS_PACK, LONGFORM_PAGE
    page = {"article": ARTICLE_PAGE, "longform": LONGFORM_PAGE}[cartridge]
    render_page(
        cartridge_name=cartridge, page=copy.deepcopy(page), ad_brief=AD_BRIEF, facts_pack=FACTS_PACK,
        cartridges_dir=REPO_ROOT / "cartridges", brand_dir=TENANT.brand_dir,
        templates_dir=REPO_ROOT / "harness" / "templates", out_dir=tmp_path / cartridge,
        published="2026-10-05", updated="2026-10-05", tenant=TENANT, download_assets=False,
    )
    return tmp_path / cartridge


PAGES = (
    [pytest.param(lambda p, look=look, h=h: _listicle(look, p, h), id=f"listicle-{look}-{h}")
     for look in LISTICLE_LOOKS for h in HERO_STYLES]
    + [
        pytest.param(_comparison, id="comparison"),
        pytest.param(_quiz, id="quiz"),
        pytest.param(lambda p: _product_page("pdp", p), id="product-page-pdp"),
        pytest.param(lambda p: _product_page("classic", p), id="product-page-classic"),
        pytest.param(lambda p: _plain("article", p), id="article"),
        pytest.param(lambda p: _plain("longform", p), id="longform"),
    ]
)


@pytest.fixture(params=PAGES)
def rendered(request, tmp_path):
    cartridge_dir = request.param(tmp_path)
    html = (cartridge_dir / "index.html").read_text()
    export, _manifest = build_shopify_body(cartridge_dir)
    return html, export


# ---------------------------------------------------------------------------
# The checks
# ---------------------------------------------------------------------------

def _css(doc):
    return "\n".join(_STYLE_RE.findall(doc))


def _rules(css):
    """(selector, body) for every style rule, at-rule blocks recursed."""
    out = []
    for item in css_scope.parse_stylesheet(css):
        if item[0] != "block":
            continue
        prelude, body = item[1].strip(), item[2]
        if prelude.startswith("@"):
            if prelude.split(None, 1)[0].split("(")[0].lower() in ("@media", "@supports", "@container", "@layer"):
                out += _rules(body)
            continue
        out += [(sel.strip(), body) for sel in css_scope._split_top_level(prelude)]
    return out


def _uppercase_heading_hits(doc):
    """Every way an h1/h2/h3 on this page could render uppercase: a rule
    that sets it and whose selector targets a heading element (by type or by
    a class a heading carries), or an inline style. A rule gated on
    .adv-case-upper is inert when the page wrapper does not carry it."""
    heading_classes = set()
    hits = []
    for tag, attrs in _HEADING_RE.findall(doc):
        m = _CLASS_RE.search(attrs)
        if m:
            heading_classes.update(m.group(1).split())
        s = _STYLE_ATTR_RE.search(attrs)
        if s and "uppercase" in s.group(1).lower():
            hits.append(f"inline style on <{tag}>")
    wrapper_upper = 'class="adv-wrap adv-case-upper' in doc
    for sel, body in _rules(_css(doc)):
        if not re.search(r"text-transform\s*:\s*uppercase", body, re.I):
            continue
        # Cycle 79: the owner-approved display headline is Peak Grotesk Wide
        # in upper case -- allowed only together with that family.
        if re.search(r"font-family\s*:\s*var\(--pk-display-wide\)", body):
            continue
        if ".adv-case-upper" in sel and not wrapper_upper:
            continue
        last = re.split(r"[\s>+~]+", sel.strip())[-1]
        last = re.sub(r"::?[\w-]+(\([^)]*\))?", "", last)
        type_sel = re.match(r"^[a-zA-Z][\w-]*", last)
        classes = set(re.findall(r"\.([\w-]+)", last))
        if (type_sel and type_sel.group(0).lower() in ("h1", "h2", "h3")) or (classes and classes <= heading_classes):
            hits.append(sel)
    return hits


def _lint(doc):
    problems = []
    upper = doc.upper()
    problems += [f"banned colour {c}" for c in BANNED_COLOURS if c in upper]
    for m in re.finditer(r"font-family\s*:\s*([^;}]*)", doc, re.I):
        if "ACID GROTESK" in m.group(1).upper() and "PK ACID GROTESK" not in m.group(1).upper():
            problems.append("Acid Grotesk outside its declared family")
    css = _css(doc)
    if re.search(r"gradient\s*\(", css, re.I) or re.search(r"gradient\s*\(", doc, re.I):
        problems.append("gradient")
    for value in re.findall(r"box-shadow\s*:\s*([^;}\"]+)", doc, re.I):
        if value.replace("!important", "").strip().lower() != "none":
            problems.append(f"box-shadow: {value.strip()}")
    problems += [f"uppercase heading: {h}" for h in _uppercase_heading_hits(doc)]
    if MARKER not in doc:
        problems.append("no version marker")
    return problems


def test_the_review_page_passes_the_brand_lint(rendered):
    html, _export = rendered
    assert _lint(html) == []


def test_the_review_page_has_exactly_one_h1(rendered):
    html, _export = rendered
    assert len(re.findall(r"<h1[\s>]", html, re.I)) == 1


def test_the_storefront_export_passes_the_brand_lint(rendered):
    _html, export = rendered
    assert _lint(export) == []
    assert len(re.findall(r"<h1[\s>]", export, re.I)) == 1


# Rules the export deliberately writes outside the wrapper: the tenant's own
# theme fixes and harness/css_scope.py's storefront-fit layer. Each one is
# page-scoped through :has() on our own root, so it exists only on our page.
_PAGE_SCOPED = re.compile(r":has\((?:~ \* > )?\.(?:adv-wrap|pk-lp)\)|~ \* > \.adv-wrap")


def test_every_rule_in_the_export_is_scoped_to_the_page_wrapper(rendered):
    _html, export = rendered
    unscoped = [sel for sel, _body in _rules(_css(export))
                if not sel.startswith(".adv-wrap") and not _PAGE_SCOPED.search(sel)]
    assert unscoped == []


def test_the_lint_catches_what_it_is_for():
    """The checks above would pass vacuously if the helpers matched
    nothing; a page that breaks each rule fails each one."""
    bad = (
        '<style>.x{color:#f27046}.y{background:linear-gradient(red,blue)}'
        '.z{box-shadow:0 1px 2px #000}.t{text-transform:uppercase}</style>'
        '<main class="adv-wrap"><h1 class="t">A</h1></main>'
    )
    problems = _lint(bad)
    assert "banned colour #F27046" in problems
    assert "gradient" in problems
    assert any(p.startswith("box-shadow") for p in problems)
    assert "uppercase heading: .t" in problems
    assert "no version marker" in problems
    # gated on the wrapper class that this tenant no longer sets: inert
    gated = '<style>.adv-case-upper h1{text-transform:uppercase}</style><main class="adv-wrap"><h1>A</h1></main>'
    assert _uppercase_heading_hits(gated) == []


# ---------------------------------------------------------------------------
# Cycle 79: the boxless rules (owner-approved 2026-10-05)
# ---------------------------------------------------------------------------

# Classes the listicle's open look puts on the byline, the stats strip, the
# proof lines and the FAQ -- none may be a box (fill or four-sided border).
_BOXLESS_CLASSES = ("op-by", "op-stat", "op-stats", "op-proof", "op-qa", "op-faq", "op-lede", "op-said",
                    "adv-byline", "byline")


def _box_hits(doc):
    hits = []
    for sel, body in _rules(_css(doc)):
        last = re.split(r"[\s>+~]+", sel.strip())[-1]
        classes = set(re.findall(r"\.([\w-]+)", last))
        if not classes & set(_BOXLESS_CLASSES):
            continue
        for m in re.finditer(r"(?<![-\w])(background(?:-color)?|border)\s*:\s*([^;}]+)", body):
            prop, value = m.group(1), m.group(2).replace("!important", "").strip().lower()
            if prop.startswith("background") and value not in ("transparent", "none", "var(--ps-bg)", "var(--pk-bg)"):
                hits.append(f"{sel}: {prop}:{value}")
            if prop == "border" and value not in ("0", "none"):
                hits.append(f"{sel}: border:{value}")
    return hits


def _listicle_pages():
    return [p for p in PAGES if str(p.id).startswith("listicle")]


@pytest.mark.parametrize("make", _listicle_pages())
def test_the_listicle_has_no_boxes_on_byline_stats_proof_or_faq(make, tmp_path):
    cartridge_dir = make(tmp_path)
    html = (cartridge_dir / "index.html").read_text()
    export, _ = build_shopify_body(cartridge_dir)
    assert _box_hits(html) == []
    assert _box_hits(export) == []


@pytest.mark.parametrize("make", PAGES)
def test_every_page_has_a_white_ground(make, tmp_path):
    cartridge_dir = make(tmp_path)
    html = (cartridge_dir / "index.html").read_text()
    assert "--ps-bg:#FFFFFF" in html.replace(" ", "")
    assert "--ps-bg-muted:#FFFFFF" in html.replace(" ", "")
    assert re.search(r"\.adv-wrap\{background:var\(--ps-bg\)!important\}", html.replace(" ", ""))


def test_the_box_lint_catches_what_it_is_for():
    bad = ('<style>.adv-listicle .op-proof{border:1px solid #000}'
           '.adv-listicle .op-qa{background:#fafafa}.adv-listicle .op-proof{border-left:3px solid red}</style>')
    hits = _box_hits(bad)
    assert len(hits) == 2
