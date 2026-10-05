"""The listicle LOOK: cycle 51's five looks, retired into one in cycle 79.

Cycle 51 gave the listicle five layouts (editorial, cards, pillars,
scorecard, lander). On 2026-10-05 the owner approved one boxless design
(~/asset-inbox/design-approved-2026-10-05/) and asked for it everywhere, so
the five templates are gone and `open` renders every listicle. The look is
still a dimension (harness/looks.py) -- an old page.json, flag or A/B/C
entry naming a retired look resolves to `open` -- and the first screen has
its own three styles (tests/test_first_screen_cycle79.py).

What is asserted here:
  - the open look renders every section the schema provides;
  - one CTA destination, a CTA before the closing band;
  - the look's CSS is scoped and uses tokens only, survives `harness
    shopify-body` and the review inliner;
  - retired names resolve, unknown names are refused; `harness rerender
    --look` still works and changes no copy.
"""
import argparse
import json
import re

import pytest

from tests.support import REPO_ROOT, TENANT
from tests.test_listicle import AD_BRIEF, RICH_FACTS_PACK, _listicle_page
from harness import cli, listicle, looks, render as render_mod, runstate
from harness.page_body import build_shopify_body
from harness.render import image_fit, render_image_slot, render_page
from harness.review import build_review_for_page

CARTRIDGE_DIR = REPO_ROOT / "cartridges" / "listicle"
LOOKS_DIR = CARTRIDGE_DIR / "looks"

_STYLE_RE = re.compile(r"<style>(.*?)</style>", re.DOTALL)


def _template(look="open"):
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
    start = html.index('<div class="pk-lp')
    return html[start:html.index('<footer class="adv-footer"', start)]


# ---------------------------------------------------------------------------
# One look, wired to the dispatcher; retired names resolve to it
# ---------------------------------------------------------------------------

def test_the_listicle_has_one_look_and_the_five_old_ones_are_retired():
    assert listicle.LOOKS == ("open",)
    assert (LOOKS_DIR / "open" / "template.html").exists()
    for old in ("editorial", "cards", "pillars", "scorecard", "lander"):
        assert not (LOOKS_DIR / old).exists()
        assert listicle.resolve_look(old, style="reasons", tenant=TENANT) == "open"
        assert looks.requested_for("listicle", old) == "open"


def test_the_cartridge_template_is_a_dispatcher_with_no_design_of_its_own():
    text = (CARTRIDGE_DIR / "template.html").read_text()
    assert "{% extends" in text and "looks/" in text
    assert "<style>" not in text


def test_the_look_scopes_its_css_and_carries_its_look_class(tmp_path):
    html = _render("open", tmp_path)
    assert "adv-listicle look-open" in html
    css = re.sub(r"/\*.*?\*/", "", _STYLE_RE.search(_template()).group(1), flags=re.S)
    rules = [r.strip() for r in re.findall(r"([^{}]+)\{", css) if r.strip() and not r.strip().startswith("@")]
    assert rules and all(r.startswith(".adv-listicle.look-open") for r in rules), rules[:3]


@pytest.mark.parametrize("style,look", sorted(listicle.LOOK_BY_STYLE.items()))
def test_every_style_renders_in_the_open_look(style, look):
    assert look == "open"
    assert listicle.resolve_look(None, style=style, tenant=TENANT) == "open"


def test_an_unknown_look_is_refused():
    with pytest.raises(ValueError, match="unknown listicle look"):
        listicle.resolve_look("brochure", style="reasons", tenant=TENANT)


def test_a_page_json_naming_an_unknown_look_still_renders(tmp_path):
    page = _listicle_page("myths")
    page["look"] = "brochure"
    html = render_page(
        cartridge_name="listicle", page=page, ad_brief=AD_BRIEF,
        facts_pack=RICH_FACTS_PACK, cartridges_dir=REPO_ROOT / "cartridges",
        brand_dir=TENANT.brand_dir, templates_dir=REPO_ROOT / "harness" / "templates",
        out_dir=tmp_path / "listicle", published="2026-09-18", updated="2026-09-18",
        download_assets=False, tenant=TENANT,
    ).read_text()
    assert "look-open" in html


# ---------------------------------------------------------------------------
# Every section the schema provides
# ---------------------------------------------------------------------------

def test_the_look_renders_every_section(tmp_path):
    page = _listicle_page()
    page["hero_style"] = "story"
    html = _render("open", tmp_path, page=page)

    assert "This page is published by" in html                 # disclosure
    assert "<h3>Sources</h3>" in html                          # sources list
    assert "Austin Laudenslager" in html                       # the one-line byline
    assert page["dek"] in html or "op-lede" in html
    for item in page["reasons"]:
        assert item["heading"] in html
        assert item["text"][:40] in html
        assert item["proof"]["text"] in html
    for line in page["audience_fit"]["for_you"] + page["audience_fit"]["not_for_you"]:
        assert line["text"] in html
    for entry in page["faq"]["questions"]:
        assert entry["question"] in html
    assert page["closing"]["headline"] in html
    for bullet in page["closing"]["recap"]:
        assert bullet["text"] in html
    assert page["closing"]["warranty_line"]["text"] in html
    assert "Model A" in html and "$7,950" in html                # model rows
    assert "may be eligible for HSA/FSA purchase" in html       # hsa claim
    assert "It was warm before the kettle boiled." in html      # pull quote
    assert 'class="adv-img' in html and 'decoding="async"' in html


def test_the_page_uses_one_cta_destination_only(tmp_path):
    page = _listicle_page()
    body = _wrapper(_render("open", tmp_path, page=page))
    hrefs = set(re.findall(r'href="(https?://[^"]+)"', body))
    product_hrefs = {h for h in hrefs if "collections" in h or "products" in h}
    assert page["cta_url"] in product_hrefs
    model_urls = {m["url"] for m in RICH_FACTS_PACK["model_options"]}
    assert product_hrefs <= {page["cta_url"]} | model_urls
    # a CTA comes before the closing band (after item 3)
    assert body.index(f'href="{page["cta_url"]}"') < body.index('class="op-close"')


def test_the_look_has_no_boxes_and_one_dark_band(tmp_path):
    """The owner's rule: white page, no panels or cards, one dark band."""
    css = _STYLE_RE.search(_template()).group(1)
    backgrounds = re.findall(r"background:([^;}]+)", css)
    assert all(b.strip() in ("var(--pk-bg)", "var(--pk-dark)", "var(--pk-on-dark)", "var(--pk-red)")
               for b in backgrounds), backgrounds
    assert "<details" not in _template()
    body = _wrapper(_render("open", tmp_path))
    assert body.count('class="op-close"') == 1


def test_the_look_declares_its_colours_through_tokens_only():
    css = _STYLE_RE.search(_template()).group(1)
    literals = set(re.findall(r"#[0-9a-fA-F]{3,8}\b", css))
    assert literals == set(), sorted(literals)


# ---------------------------------------------------------------------------
# image_fit (cycle 55), unchanged
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("asset,frame,fit", [
    ({"cutout": True, "kind": "lifestyle"}, None, "contain"),
    ({"cutout": True, "kind": "lifestyle"}, "3x2", "contain"),
    ({"cutout": False, "kind": "render"}, None, "cover"),
    ({"cutout": False, "kind": "render"}, "3x2", "contain"),
    ({"cutout": False, "kind": "lifestyle"}, "3x2", "cover"),
    ({"kind": "installation"}, "4x3", "cover"),
    ({"kind": "ad-frame"}, "4x5", "cover"),
])
def test_image_fit_matches_the_class_render_image_slot_writes(asset, frame, fit):
    asset = {"id": "a", "url": "a.jpg", "alt": "a", "width": 1067, "height": 1600, **asset}
    assert image_fit(asset, frame=frame) == fit
    assert f"adv-img--{fit}" in str(render_image_slot(asset, frame=frame))


# ---------------------------------------------------------------------------
# Export and review paths
# ---------------------------------------------------------------------------

def test_the_look_survives_the_shopify_body_export(tmp_path):
    _render("open", tmp_path)
    body, _manifest = build_shopify_body(tmp_path / "listicle")
    assert body.startswith("<style>")
    assert ".look-open" in body
    assert ".adv-wrap{" in body
    for tag in ("<header", "</header>", "<nav", "</nav>", "<footer", "</footer>"):
        assert tag not in body
    assert "pk-lp" in body


def _fake_download(asset, dest_dir, **kwargs):
    dest_dir.mkdir(parents=True, exist_ok=True)
    name = f"{asset['id']}-800.jpg"
    (dest_dir / name).write_bytes(b"fake-jpeg")
    return {
        "path": dest_dir / name,
        "width": 900, "height": 1200,
        "variants": [{"width": 800, "jpg": f"assets/{name}", "webp": None}],
        "cutout": True,
    }


def test_the_look_survives_the_review_inliner(tmp_path, monkeypatch):
    monkeypatch.setattr(render_mod, "download_asset", _fake_download)
    run_dir = tmp_path / "20260922-000000-looks-test-aaaa"
    (run_dir / "listicle").mkdir(parents=True)
    _render("open", run_dir, download_assets=True)
    review_path = build_review_for_page(run_dir, "listicle")
    assert review_path is not None
    review = review_path.read_text()
    assert "look-open" in review
    assert 'src="data:image/jpeg;base64,' in review
    assert 'src="assets/' not in review


# ---------------------------------------------------------------------------
# harness run --look / harness rerender --look
# ---------------------------------------------------------------------------

def test_the_run_parser_takes_a_look_flag_and_refuses_an_unknown_one():
    parser = cli.build_parser()
    assert parser.parse_args(["run", "ad.txt", "--look", "open"]).look == "open"
    # a retired name is still a valid flag value (it renders in open)
    assert parser.parse_args(["run", "ad.txt", "--look", "scorecard"]).look == "scorecard"
    with pytest.raises(SystemExit):
        parser.parse_args(["run", "ad.txt", "--look", "brochure"])


@pytest.fixture
def run_dir(tmp_path, monkeypatch):
    run_dir = tmp_path / "20260922-031254-looks-run-abcd"
    (run_dir / "listicle").mkdir(parents=True)
    (run_dir / "facts_pack.json").write_text(json.dumps(RICH_FACTS_PACK))
    (run_dir / "ad_brief.json").write_text(json.dumps(AD_BRIEF))
    page = _listicle_page("myths")
    page["look"] = "pillars"   # a page built before cycle 79
    (run_dir / "listicle" / "page.json").write_text(json.dumps(page))
    runstate.init_state(run_dir, pages=["listicle"])
    monkeypatch.setattr(render_mod, "download_asset", _fake_download)
    return run_dir


def _args(run_dir, **over):
    base = dict(run_dir=str(run_dir), page="listicle", note="", look=None, hero_style=None, tenant=TENANT.name)
    base.update(over)
    return argparse.Namespace(**base)


def test_rerender_of_a_page_built_in_a_retired_look_renders_open(run_dir):
    assert cli.cmd_rerender(_args(run_dir)) == 0
    assert "look-open" in (run_dir / "listicle" / "index.html").read_text()


def test_rerender_look_changes_no_copy_and_makes_no_model_call(run_dir, monkeypatch):
    def explode(*a, **kw):
        raise AssertionError("switching a look must not build an API client")

    monkeypatch.setattr(cli, "make_client", explode)
    before = json.loads((run_dir / "listicle" / "page.json").read_text())
    assert cli.cmd_rerender(_args(run_dir, look="lander")) == 0
    after = json.loads((run_dir / "listicle" / "page.json").read_text())
    assert after["look"] == "open"
    skip = {"look", "hero_style", "hero"}
    assert {k: v for k, v in after.items() if k not in skip} == {k: v for k, v in before.items() if k not in skip}
    assert runstate.load_state(run_dir)["listicle"]["look"] == "open"


def test_rerender_leaves_the_approval_state_alone(run_dir):
    data = runstate.load_state(run_dir)
    data["state"] = "published"
    data["pages"]["listicle"] = "published"
    runstate.save_state(run_dir, data)
    cli.cmd_rerender(_args(run_dir, look="open", note="cycle 79 look"))
    after = runstate.load_state(run_dir)
    assert after["state"] == "published" and after["pages"]["listicle"] == "published"


def test_rerender_look_is_refused_for_another_cartridge(run_dir, capsys):
    (run_dir / "longform").mkdir()
    (run_dir / "longform" / "page.json").write_text("{}")
    assert cli.cmd_rerender(_args(run_dir, page="longform", look="open")) == 1
    assert "--look applies to a cartridge with looks" in capsys.readouterr().err
