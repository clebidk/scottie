"""Cycle 51: the listicle cartridge's five LOOKS.

The cartridge already had five COPY styles (harness/listicle.py's STYLES:
the headline formula and what a numbered item is). Every one of them
rendered through a single template, so ten runs came back looking like ten
copies of one page. The LOOK is the second, independent dimension: which
template under cartridges/listicle/looks/<look>/ lays that copy out.

What is asserted here is the part a reviewer would otherwise have to check
by eye, made offline and mechanical:

  - every look renders every section the schema provides, from one fixture
    page, so a look cannot quietly drop the fit block or the FAQ;
  - every look holds a CTA above the fold IN MARKUP ORDER (a phone reads top
    to bottom, so markup order is the fold);
  - the five looks really are five different pages -- their section-class
    sets are pairwise different, and their CSS namespaces are pairwise
    disjoint, which is what makes "two looks never collide" a fact rather
    than a convention;
  - every look survives `harness shopify-body` and the review inliner, both
    of which only keep what is inside the body;
  - the resolution rule: explicit wins, else the tenant's map, else the
    built-in style pairing; and `harness rerender --look` switches the
    template without touching the copy.
"""
import argparse
import itertools
import json
import re

import pytest

from tests.support import REPO_ROOT, TENANT
from tests.test_listicle import AD_BRIEF, RICH_FACTS_PACK, _listicle_page
from harness import cli, listicle, render as render_mod, runstate
from harness.page_body import build_shopify_body
from harness.render import render_page
from harness.review import build_review_for_page

CARTRIDGE_DIR = REPO_ROOT / "cartridges" / "listicle"
LOOKS_DIR = CARTRIDGE_DIR / "looks"

# The class-name namespace each look's own design system uses. Kept here
# rather than derived, because the property under test is exactly that these
# stay one-look-each.
LOOK_PREFIXES = {
    "editorial": "ed",
    "cards": "lst",
    "pillars": "pil",
    "scorecard": "sc",
    "lander": "ld",
}

_STYLE_RE = re.compile(r"<style>(.*?)</style>", re.DOTALL)
_CLASS_ATTR_RE = re.compile(r'class="([^"]*)"')


def _template(look):
    return (LOOKS_DIR / look / "template.html").read_text()


def _render(look, tmp_path, page=None, **kwargs):
    page = page or _listicle_page()
    page["look"] = look
    kwargs.setdefault("download_assets", False)
    return render_page(
        cartridge_name="listicle",
        page=page,
        ad_brief=AD_BRIEF,
        facts_pack=RICH_FACTS_PACK,
        cartridges_dir=REPO_ROOT / "cartridges",
        brand_dir=TENANT.brand_dir,
        templates_dir=REPO_ROOT / "harness" / "templates",
        out_dir=tmp_path / "listicle",
        published="2026-09-18",
        updated="2026-09-18",
        tenant=TENANT,
        **kwargs,
    ).read_text()


def _wrapper(html):
    """The cartridge's own markup: everything from the wrapper element to the
    start of base.html's footer. The <head>'s JSON-LD repeats the item
    headings, and the footer's Sources list repeats the product URL, so an
    order or link assertion has to look at the body the reader sees."""
    start = html.index('<div class="pk-lp')
    return html[start:html.index('<footer class="adv-footer"', start)]


def _classes(html):
    found = set()
    for value in _CLASS_ATTR_RE.findall(html):
        found.update(tok for tok in value.split() if tok)
    return found


# ---------------------------------------------------------------------------
# The five looks exist and are wired to the dispatcher
# ---------------------------------------------------------------------------

def test_every_look_has_its_own_template():
    assert set(listicle.LOOKS) == set(LOOK_PREFIXES)
    for look in listicle.LOOKS:
        assert (LOOKS_DIR / look / "template.html").exists()


def test_the_cartridge_template_is_a_dispatcher_with_no_design_of_its_own():
    """cartridges/listicle/template.html is the path harness/render.py loads
    for every cartridge; for this one it only picks a look, so the renderer
    stays generic instead of learning about one cartridge's sub-templates."""
    text = (CARTRIDGE_DIR / "template.html").read_text()
    assert "{% extends" in text and "looks/" in text
    assert "<style>" not in text


@pytest.mark.parametrize("look", listicle.LOOKS)
def test_each_look_scopes_its_css_and_carries_its_look_class(look, tmp_path):
    html = _render(look, tmp_path)
    assert f"adv-listicle look-{look}" in html
    css = _STYLE_RE.search(_template(look)).group(1)
    assert f".look-{look}" in css


# ---------------------------------------------------------------------------
# Every look renders every section the schema provides
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("look", listicle.LOOKS)
def test_each_look_renders_every_section(look, tmp_path):
    page = _listicle_page()
    html = _render(look, tmp_path, page=page)

    # renderer-injected chrome that no look may drop
    assert "Advertisement" in html
    assert "is an advertisement published by" in html          # disclosure
    assert "<h3>Sources</h3>" in html                          # sources list
    assert "Written by" in html                                # byline

    # the writer's own sections
    assert page["headline"] in html
    assert page["dek"] in html
    for item in page["reasons"]:
        assert item["heading"] in html
        assert item["text"][:40] in html
        assert item["proof"]["text"] in html
    assert "Who this is for, and who it is not for" in html
    for line in page["audience_fit"]["for_you"] + page["audience_fit"]["not_for_you"]:
        assert line["text"] in html
    for entry in page["faq"]["questions"]:
        assert entry["question"] in html
    assert page["closing"]["headline"] in html
    for bullet in page["closing"]["recap"]:
        assert bullet["text"] in html
    assert page["closing"]["warranty_line"]["text"] in html

    # the renderer-owned sections built from facts_pack alone
    assert "Which model fits" in html
    assert "Model A" in html and "$7,950" in html
    assert "Rated 4.8 out of 5 across 1,200 reviews." in html   # trust line
    assert "may be eligible for HSA/FSA purchase" in html       # hsa claim
    assert "It was warm before the kettle boiled." in html      # pull quote

    # every image goes through render_image_slot
    assert 'class="adv-img' in html
    assert "decoding=\"async\"" in html


@pytest.mark.parametrize("look", listicle.LOOKS)
def test_each_look_puts_a_cta_above_the_fold_in_markup_order(look, tmp_path):
    """A phone reads the DOM top to bottom, so markup order IS the fold. The
    page's one cta_url must appear before the first numbered item."""
    page = _listicle_page()
    body = _wrapper(_render(look, tmp_path, page=page))
    first_cta = body.index(f'href="{page["cta_url"]}"')
    first_item = body.index(page["reasons"][0]["heading"])
    assert first_cta < first_item
    # and the CTA's own text is the page's one allowed CTA text
    assert page["cta_text"] in body[first_cta:first_cta + 400]


@pytest.mark.parametrize("look", listicle.LOOKS)
def test_each_look_uses_one_cta_destination_only(look, tmp_path):
    """Several anchors are fine (the lander look shows a primary button and a
    ghost link side by side); several destinations are not."""
    page = _listicle_page()
    body = _wrapper(_render(look, tmp_path, page=page))
    hrefs = set(re.findall(r'href="(https?://[^"]+)"', body))
    product_hrefs = {h for h in hrefs if "collections" in h or "products" in h}
    assert page["cta_url"] in product_hrefs
    # the model picker links are the only other product links on the page
    model_urls = {m["url"] for m in RICH_FACTS_PACK["model_options"]}
    assert product_hrefs <= {page["cta_url"]} | model_urls


# ---------------------------------------------------------------------------
# The five looks are five DIFFERENT pages
# ---------------------------------------------------------------------------

def test_the_five_looks_have_five_different_dom_skeletons(tmp_path):
    """The reviewer's complaint in one assertion: ten pages came back with
    one layout. A look whose section classes matched another look's would be
    the same page again."""
    skeletons = {}
    for look in listicle.LOOKS:
        html = _render(look, tmp_path / look)
        wrapper = _wrapper(html)
        skeletons[look] = frozenset(
            c for c in _classes(wrapper) if re.match(r"^(ed|lst|pil|sc|ld)-", c)
        )
    for look, classes in skeletons.items():
        assert classes, f"{look} emitted no section classes"
    for a, b in itertools.combinations(listicle.LOOKS, 2):
        assert skeletons[a] != skeletons[b], f"{a} and {b} render the same skeleton"
    assert len(set(skeletons.values())) == len(listicle.LOOKS)


def test_no_two_looks_share_a_css_namespace():
    """Each look's own class prefix appears in that look's stylesheet and in
    no other's, which is what keeps two looks from colliding."""
    css = {look: _STYLE_RE.search(_template(look)).group(1) for look in listicle.LOOKS}
    for look, prefix in LOOK_PREFIXES.items():
        pattern = re.compile(r"\." + prefix + r"-[a-z0-9-]+")
        assert pattern.search(css[look]), f"{look} defines no .{prefix}- rules"
        for other in listicle.LOOKS:
            if other == look:
                continue
            assert not pattern.search(css[other]), f".{prefix}- leaked into {other}"


@pytest.mark.parametrize("look", listicle.LOOKS)
def test_each_look_declares_its_colours_through_pk_tokens_only(look):
    """No cartridge may hardcode a brand colour: every colour resolves
    --pk-* -> --ps-* -> --adv-*. White, black and plain rgba scrims over a
    photograph are the only literals allowed."""
    css = _STYLE_RE.search(_template(look)).group(1)
    literals = set(re.findall(r"#[0-9a-fA-F]{3,8}\b", css))
    assert literals <= {"#fff", "#ffffff", "#f4f5f6"}, f"{look}: {sorted(literals)}"
    assert "--pk-accent:var(--ps-accent,var(--adv-accent))" in css.replace(" ", "")


# ---------------------------------------------------------------------------
# Export and review paths
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("look", listicle.LOOKS)
def test_each_look_survives_the_shopify_body_export(look, tmp_path):
    _render(look, tmp_path)
    body, _manifest = build_shopify_body(tmp_path / "listicle")
    # the cartridge's own CSS travels in the body, since structure.css lives
    # in the document head and is dropped
    assert body.startswith("<style>")
    assert f".look-{look}" in body
    # the head tokens are re-emitted on the export root (cycle 48)
    assert ".adv-wrap{" in body
    assert "--adv-accent" in body
    # no bare document chrome: a classed cartridge band becomes a div
    for tag in ("<header", "</header>", "<nav", "</nav>", "<footer", "</footer>"):
        assert tag not in body
    assert "pk-lp" in body


@pytest.mark.parametrize("look", listicle.LOOKS)
def test_each_look_survives_the_review_inliner(look, tmp_path, monkeypatch):
    def fake_download(asset, dest_dir, **kwargs):
        dest_dir.mkdir(parents=True, exist_ok=True)
        name = f"{asset['id']}-800.jpg"
        (dest_dir / name).write_bytes(b"fake-jpeg")
        return {
            "path": dest_dir / name,
            "width": 900, "height": 1200,
            "variants": [{"width": 800, "jpg": f"assets/{name}", "webp": None}],
            "cutout": True,
        }

    monkeypatch.setattr(render_mod, "download_asset", fake_download)
    run_dir = tmp_path / "20260922-000000-looks-test-aaaa"
    (run_dir / "listicle").mkdir(parents=True)
    _render(look, run_dir, download_assets=True)
    review_path = build_review_for_page(run_dir, "listicle")
    assert review_path is not None
    review = review_path.read_text()
    assert f"look-{look}" in review
    assert "src=\"data:image/jpeg;base64," in review
    assert 'src="assets/' not in review


# ---------------------------------------------------------------------------
# Resolution: explicit, tenant pin, style pairing
# ---------------------------------------------------------------------------

def test_every_style_pairs_with_a_look():
    assert set(listicle.LOOK_BY_STYLE) == set(listicle.STYLES)
    assert set(listicle.LOOK_BY_STYLE.values()) == set(listicle.LOOKS)


@pytest.mark.parametrize("style,look", sorted(listicle.LOOK_BY_STYLE.items()))
def test_an_unset_look_falls_back_to_the_style_pairing(style, look):
    assert listicle.resolve_look(None, style=style, tenant=TENANT) == look


def test_an_explicit_look_always_wins():
    assert listicle.resolve_look("pillars", style="reasons", tenant=TENANT) == "pillars"


def test_an_unknown_look_is_refused():
    with pytest.raises(ValueError, match="unknown listicle look"):
        listicle.resolve_look("brochure", style="reasons", tenant=TENANT)


class _Pinned:
    """A stand-in tenant carrying one pinned key, the same shape
    tests/test_listicle.py uses for the style pins. A stub rather than a
    monkeypatch of the shared real tenant: patching `get` on that object
    leaks into later tests in the same session."""

    def __init__(self, **pins):
        self._pins = pins

    def get(self, key, default=None):
        return self._pins.get(key, default)


def test_a_tenant_may_pin_a_subset_of_looks():
    tenant = _Pinned(**{"cartridges.listicle.looks": ["lander", "editorial", "not-a-look"]})
    assert listicle.tenant_looks(tenant) == ("lander", "editorial")
    # "reasons" pairs with cards, which this tenant does not allow
    assert listicle.resolve_look(None, style="reasons", tenant=tenant) == "lander"
    # an explicit choice still wins over the pin
    assert listicle.resolve_look("cards", style="reasons", tenant=tenant) == "cards"


def test_a_tenant_may_repoint_one_style_at_another_look():
    tenant = _Pinned(**{"cartridges.listicle.look_by_style": {"reasons": "pillars", "myths": "nope"}})
    assert listicle.resolve_look(None, style="reasons", tenant=tenant) == "pillars"
    # an entry naming an unknown look is ignored, and a style the map does
    # not name at all keeps the built-in pairing
    assert listicle.resolve_look(None, style="myths", tenant=tenant) == "pillars"
    assert listicle.resolve_look(None, style="mistakes", tenant=tenant) == "editorial"


def test_a_pin_that_names_nothing_valid_is_ignored():
    assert listicle.tenant_looks(_Pinned(**{"cartridges.listicle.looks": ["brochure"]})) == listicle.LOOKS


def test_a_page_json_naming_an_unknown_look_still_renders(tmp_path):
    """Only reachable from a hand-edited page.json -- both flags are argparse
    `choices` -- and a layout typo must not take a re-render down."""
    page = _listicle_page("myths")
    page["look"] = "brochure"
    html = render_page(
        cartridge_name="listicle", page=page, ad_brief=AD_BRIEF,
        facts_pack=RICH_FACTS_PACK, cartridges_dir=REPO_ROOT / "cartridges",
        brand_dir=TENANT.brand_dir, templates_dir=REPO_ROOT / "harness" / "templates",
        out_dir=tmp_path / "listicle", published="2026-09-18", updated="2026-09-18",
        download_assets=False, tenant=TENANT,
    ).read_text()
    assert "look-pillars" in html          # the myths pairing


# ---------------------------------------------------------------------------
# harness run --look / harness rerender --look
# ---------------------------------------------------------------------------

def test_the_run_parser_takes_a_look_flag_and_refuses_an_unknown_one():
    parser = cli.build_parser()
    args = parser.parse_args(["run", "ad.txt", "--look", "scorecard"])
    assert args.look == "scorecard"
    with pytest.raises(SystemExit):
        parser.parse_args(["run", "ad.txt", "--look", "brochure"])


def test_the_rerender_parser_takes_a_look_flag():
    parser = cli.build_parser()
    args = parser.parse_args(["rerender", "out/run", "--page", "listicle", "--look", "editorial"])
    assert args.look == "editorial"
    assert parser.parse_args(["rerender", "out/run", "--page", "listicle"]).look is None


@pytest.fixture
def run_dir(tmp_path, monkeypatch):
    run_dir = tmp_path / "20260922-031254-looks-run-abcd"
    (run_dir / "listicle").mkdir(parents=True)
    (run_dir / "facts_pack.json").write_text(json.dumps(RICH_FACTS_PACK))
    (run_dir / "ad_brief.json").write_text(json.dumps(AD_BRIEF))
    (run_dir / "listicle" / "page.json").write_text(json.dumps(_listicle_page("myths")))
    runstate.init_state(run_dir, pages=["listicle"])

    def fake_download(asset, dest_dir, **kwargs):
        dest_dir.mkdir(parents=True, exist_ok=True)
        name = f"{asset['id']}-800.jpg"
        (dest_dir / name).write_bytes(b"fake-jpeg")
        return {
            "path": dest_dir / name,
            "width": 900, "height": 1200,
            "variants": [{"width": 800, "jpg": f"assets/{name}", "webp": None}],
            "cutout": True,
        }

    monkeypatch.setattr(render_mod, "download_asset", fake_download)
    return run_dir


def _args(run_dir, **over):
    base = dict(run_dir=str(run_dir), page="listicle", note="", look=None, tenant=TENANT.name)
    base.update(over)
    return argparse.Namespace(**base)


def test_rerender_with_no_look_uses_the_pages_style_pairing(run_dir):
    assert cli.cmd_rerender(_args(run_dir)) == 0
    html = (run_dir / "listicle" / "index.html").read_text()
    assert "look-pillars" in html          # myths -> pillars


def test_rerender_look_switches_the_template_without_touching_the_copy(run_dir):
    before = json.loads((run_dir / "listicle" / "page.json").read_text())
    assert cli.cmd_rerender(_args(run_dir, look="editorial")) == 0
    html = (run_dir / "listicle" / "index.html").read_text()
    assert "look-editorial" in html
    assert "look-pillars" not in html

    after = json.loads((run_dir / "listicle" / "page.json").read_text())
    assert after["look"] == "editorial"
    assert {k: v for k, v in after.items() if k != "look"} == before
    # and it is recorded next to the style, so the next re-render repeats it
    assert runstate.load_state(run_dir)["listicle"]["look"] == "editorial"
    assert cli.cmd_rerender(_args(run_dir)) == 0
    assert "look-editorial" in (run_dir / "listicle" / "index.html").read_text()


def test_rerender_look_makes_no_model_call(run_dir, monkeypatch):
    def explode(*a, **kw):
        raise AssertionError("switching a look must not build an API client")

    monkeypatch.setattr(cli, "make_client", explode)
    assert cli.cmd_rerender(_args(run_dir, look="lander")) == 0


def test_rerender_look_leaves_the_approval_state_alone(run_dir):
    data = runstate.load_state(run_dir)
    data["state"] = "published"
    data["pages"]["listicle"] = "published"
    runstate.save_state(run_dir, data)

    cli.cmd_rerender(_args(run_dir, look="scorecard", note="cycle 51 look"))

    after = runstate.load_state(run_dir)
    assert after["state"] == "published"
    assert after["pages"]["listicle"] == "published"


def test_rerender_look_is_refused_for_another_cartridge(run_dir, capsys):
    (run_dir / "longform").mkdir()
    (run_dir / "longform" / "page.json").write_text("{}")
    assert cli.cmd_rerender(_args(run_dir, page="longform", look="cards")) == 1
    assert "--look applies to the listicle cartridge" in capsys.readouterr().err
