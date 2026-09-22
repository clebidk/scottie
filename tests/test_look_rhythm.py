"""Cycle 55: layout QA of the five listicle looks.

Reviewer: "some designs now look chopped with too large of spaces". Two
causes, each asserted here:

  - a product cut-out (white-border detector -> image_fit "contain") was
    placed in a full-bleed cover frame or behind the pillars hero overlay,
    so it sat as a narrow cabin in a wide dark or letterboxed strip. A
    cut-out now always sits contained on a panel, and only a photo that
    fills its frame keeps a cover band;
  - every look stacked 48-64px section paddings on top of each other. One
    scale now: sections 40px (32px on phones), items 32px, hero 32/40,
    closing band 48/48 (cartridges/listicle/cartridge.md "Rhythm").
"""
import copy
import re

import pytest

from harness import listicle
from harness.render import image_fit, render_image_slot, render_page
from tests.support import REPO_ROOT, TENANT
from tests.test_listicle import AD_BRIEF, RICH_FACTS_PACK, _listicle_page
from tests.test_listicle_looks import _render, _template, _wrapper

_STYLE_RE = re.compile(r"<style>(.*?)</style>", re.DOTALL)

# the class each look puts on a cut-out's panel
PANEL_CLASS = {
    "editorial": "ed-panel",
    "cards": "lst-media--panel",
    "pillars": "pil-panel",
    "scorecard": "sc-imgpanel",
    "lander": "ld-imgpanel",
}


def _facts(**asset_fields):
    facts = copy.deepcopy(RICH_FACTS_PACK)
    for asset in facts["assets"]:
        asset.update(asset_fields)
    return facts


def _render_with(look, tmp_path, facts):
    page = _listicle_page()
    page["look"] = look
    return render_page(
        cartridge_name="listicle", page=page, ad_brief=AD_BRIEF, facts_pack=facts,
        cartridges_dir=REPO_ROOT / "cartridges", brand_dir=TENANT.brand_dir,
        templates_dir=REPO_ROOT / "harness" / "templates", out_dir=tmp_path / "listicle",
        published="2026-09-22", updated="2026-09-22", tenant=TENANT, download_assets=False,
    ).read_text()


# ---------------------------------------------------------------------------
# image_fit: one answer for render_image_slot and for a look's layout choice
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("asset,frame,fit", [
    ({"cutout": True, "kind": "lifestyle"}, None, "contain"),
    ({"cutout": True, "kind": "lifestyle"}, "3x2", "contain"),
    ({"cutout": False, "kind": "render"}, None, "cover"),
    ({"cutout": False, "kind": "render"}, "3x2", "contain"),
    ({"cutout": False, "kind": "lifestyle"}, "3x2", "cover"),
    ({"kind": "installation"}, "4x3", "cover"),
])
def test_image_fit_matches_the_class_render_image_slot_writes(asset, frame, fit):
    asset = {"id": "a", "url": "a.jpg", "alt": "a", "width": 1067, "height": 1600, **asset}
    assert image_fit(asset, frame=frame) == fit
    assert f"adv-img--{fit}" in str(render_image_slot(asset, frame=frame))


def test_image_fit_of_no_asset_is_none():
    assert image_fit(None) is None


# ---------------------------------------------------------------------------
# Cut-outs are never cover bands
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("look", listicle.LOOKS)
def test_a_cutout_sits_on_a_panel_in_every_look(look, tmp_path):
    body = _wrapper(_render_with(look, tmp_path, _facts(cutout=True)))
    assert PANEL_CLASS[look] in body
    assert "adv-img--cover" not in body


@pytest.mark.parametrize("look", listicle.LOOKS)
def test_a_lifestyle_photo_gets_no_panel(look, tmp_path):
    body = _wrapper(_render_with(look, tmp_path, _facts(cutout=False, kind="lifestyle")))
    assert PANEL_CLASS[look] not in body


def test_pillars_cutout_hero_is_a_split_hero_never_an_overlay(tmp_path):
    body = _wrapper(_render_with("pillars", tmp_path, _facts(cutout=True)))
    assert "pil-hero--split" in body
    assert "pil-hero-scrim" not in body
    # every item is a two-column band, none a full-bleed 3:2 cover band
    assert body.count("pil-band--split") == len(_listicle_page()["reasons"])
    assert 'class="pil-media"' not in body
    assert "aspect-ratio:3 / 2" not in body
    # the CTA is still the first thing after the dek, above the image
    assert body.index("pil-btn") < body.index("pil-panel")


def test_pillars_lifestyle_hero_keeps_the_overlay_capped_at_560(tmp_path):
    body = _wrapper(_render_with("pillars", tmp_path, _facts(cutout=False, kind="lifestyle")))
    assert "pil-hero-scrim" in body and "pil-hero--split" not in body
    assert "pil-band--split" not in body and 'class="pil-media"' in body
    css = _STYLE_RE.search(_template("pillars")).group(1)
    assert ".pil-hero-media{max-height:560px}" in css
    assert ".pil-media .adv-img{width:100%;max-height:480px}" in css


# ---------------------------------------------------------------------------
# One rhythm scale
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("look", listicle.LOOKS)
def test_every_look_uses_the_one_rhythm_scale(look):
    css = _STYLE_RE.search(_template(look)).group(1)
    for token in ("--pk-gap:40px", "--pk-item-gap:32px", "--pk-hero-top:32px",
                  "--pk-close-gap:48px", "--pk-panel-pad:24px", "--pk-panel-img:420px"):
        assert token in css, f"{look}: {token}"
    # phones step the section gap down to 32px; nothing steps it back up
    assert re.search(r"@media \(max-width:600px\)\{\s*\.adv-listicle[^{]*\{--pk-gap:32px", css), look
    assert not re.findall(r"--pk-gap:(?:48|56|64)px", css), look
    # no section padding is a literal any more
    assert not re.findall(r"padding:(?:48|56|64)px", css), look


@pytest.mark.parametrize("look", listicle.LOOKS)
def test_the_rendered_page_carries_the_scale(look, tmp_path):
    """The look CSS travels in the body (shopify-body keeps only that)."""
    body = _render(look, tmp_path).split("<body>", 1)[1]
    assert "--pk-gap:40px" in body and "--pk-close-gap:48px" in body
