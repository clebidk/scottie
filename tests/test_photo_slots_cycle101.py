"""Cycle 101: the owner's grading of 21 real pages (docs/FIXLOG.md cycle 101).

A. Slot order: every slot ranks room and exterior shots first, then people in
   use, then detail macros, then the studio cut-out (images.SHOT_TIERS).
B. The five tenant landing looks declare hero sets (look.json) and include the
   shared hero partial, so the design gate can try photo heroes on them.
C. The Nourial (and Swarva) listicle looks show an item photo in its own ratio:
   no empty band above and below a photo of another ratio on a phone.
D. The Eight Sleep buy-box look puts its pill inside the first phone screen.

The tenant looks are gitignored (copied from prod); their tests skip where the
look is not on disk. No model or network call. The tests marked `real_browser`
drive a headless Chromium when one is installed (skipped otherwise).
"""
import copy
import json
import re
import shutil

import pytest

from evals import fake_run
from harness import capture, design_gate, heroes, images, pagechecks
from harness import render as render_mod
from tests.support import REPO_ROOT, TENANT
from tests.test_first_screen_cycle79 import SPEAKER_BRIEF, _page as _listicle_page, _run_dir
from tests.test_grading_cycle97 import _portrait_still_run, _render_landing
from tests.test_brand_lint_cycle78 import _lint
from tests.test_heroes_cycle94 import FIXTURE, _library_dir
from tests.test_images_cycle93 import CUT, _cand, _fill, _ids
from tests.test_listicle import RICH_FACTS_PACK, _make_image_bytes

REAL_BROWSER = capture.find_browser()
needs_browser = pytest.mark.skipif(REAL_BROWSER is None, reason="no headless chromium/google-chrome here")

TENANT_LOOKS = TENANT.root / "looks"
LANDING_LOOKS = ("eightsleep-buybox", "function-cinematic", "oura-restraint", "plunge-category", "rivian-letters")


def _tenant_look(cartridge, slug):
    """Copies the real tenant look into the test's looks folder (conftest
    points looks_dir there); skips when it is not on this machine."""
    source = TENANT_LOOKS / cartridge / slug
    if not (source / "template.html").exists():
        pytest.skip(f"tenant look {slug} is not on this machine (gitignored; copied from prod)")
    dest = TENANT.looks_dir / cartridge / slug
    if not dest.exists():
        shutil.copytree(source, dest)
    return dest


def _look_heroes(slug):
    path = TENANT_LOOKS / "landing" / slug / "look.json"
    return json.loads(path.read_text())["heroes"] if path.is_file() else [None]


LOOK_HEROES = [(slug, hero) for slug in LANDING_LOOKS for hero in _look_heroes(slug)]


# ---------------------------------------------------------------------------
# A. slot order
# ---------------------------------------------------------------------------

HERO = {"name": "hero", "want": {"shot": "any"}, "n": 1}
# the page's model, quality 5: on score alone the person shot would win
PERSON = _cand("person", "lifestyle-person", model="fuji", quality=5)
ROOM = _cand("room", "room")
MACRO = _cand("macro", "detail-macro", model="fuji", quality=5)
EXTERIOR = _cand("exterior", "exterior")


def test_the_order_is_one_table_room_and_exterior_first_and_the_cutout_last():
    assert images.SHOT_TIERS[0] == ("hero-wide", "room", "exterior")
    assert [images.SHOT_TIER[s] for s in ("room", "lifestyle-person", "detail-macro", "cutout")] == [0, 1, 2, 4]
    assert set(images.SHOT_TIER) == set(images.ALL_SHOTS) - {"ad-still"}


def test_the_hero_slot_takes_the_room_shot_over_a_person_a_macro_and_the_cutout():
    r = _fill([HERO], [PERSON, MACRO, CUT, ROOM])[0]
    assert _ids(r) == ["room"]
    assert r["picks"][0]["score"] < images.score_candidate(PERSON, shots=None, prefer_model="fuji")[0]
    # the slot's own shot order does not lift a person over a room shot
    caps = {"name": "hero", "want": {"shot": list(heroes.get("caps_over_photo").needs["image"]["shots"])}, "n": 1}
    assert caps["want"]["shot"].index("lifestyle-person") < caps["want"]["shot"].index("room")
    assert _ids(_fill([caps], [PERSON, ROOM])[0]) == ["room"]


def test_with_only_a_person_and_the_cutout_the_person_wins():
    assert _ids(_fill([HERO], [CUT, PERSON])[0]) == ["person"]


def test_with_nothing_tagged_the_cutout_fills_the_hero():
    r = _fill([HERO], [CUT])[0]
    assert _ids(r) == ["asset-cutout-fuji"] and r["picks"][0]["via"] == "want"


def test_an_item_slot_after_the_hero_follows_the_order_and_never_reuses_the_hero_photo():
    items = {"name": "items", "want": {"shot": "any"}, "n": 5, "above_fold": False, "last_resort": None}
    hero, rest = _fill([HERO, items], [CUT, MACRO, PERSON, ROOM, EXTERIOR])
    assert _ids(hero) == ["exterior"] or _ids(hero) == ["room"]
    assert hero["picks"][0]["id"] not in _ids(rest)
    assert [images.SHOT_TIER[_cand_shot(i)] for i in _ids(rest)] == [0, 1, 2, 4]
    assert _ids(rest)[1:] == ["person", "macro", "asset-cutout-fuji"]


def _cand_shot(cid):
    return {"room": "room", "exterior": "exterior", "person": "lifestyle-person", "macro": "detail-macro",
            "asset-cutout-fuji": "cutout"}[cid]


def test_an_ad_still_ranks_with_the_rooms_only_when_it_is_a_product_still():
    still = _cand("asset-adframe-still-1", "ad-still", source="ad-still")
    assert images.shot_tier(dict(still, face=False, short_side=1080)) == images.SHOT_TIER["room"]
    assert images.shot_tier(dict(still, face=True, short_side=1080)) == images.SHOT_TIER["lifestyle-person"]
    assert images.shot_tier(dict(still, face=False, short_side=600)) == images.SHOT_TIER["lifestyle-person"]


FULL_BLEED = {"name": "hero", "want": {"shot": list(heroes.get("caps_over_photo").needs["image"]["shots"]),
                                      "fullbleed_ok": True}, "n": 1, "last_resort": None}
# owner decision 2026-10-09: a portrait room photo (1067 x 1600) may lead a full-bleed hero
PORTRAIT_ROOM = dict(_cand("portrait-room", "room", aspect=0.667, fullbleed=False), long_side=1600, white_edge=False)
WIDE_PERSON = dict(_cand("wide-person", "lifestyle-person", model="fuji", quality=5), long_side=1600, white_edge=False)
WIDE_ROOM = dict(_cand("wide-room", "room"), long_side=1600, white_edge=False)


def test_a_portrait_room_photo_beats_a_landscape_person_for_a_full_bleed_hero():
    r = _fill([FULL_BLEED], [WIDE_PERSON, PORTRAIT_ROOM, CUT])[0]
    assert _ids(r) == ["portrait-room"] and r["picks"][0]["via"] == "want"
    assert images.portrait_fullbleed(PORTRAIT_ROOM) and not images.portrait_fullbleed(WIDE_ROOM)


def test_a_landscape_room_photo_beats_the_portrait_one():
    better = dict(PORTRAIT_ROOM, model="fuji", quality=5)   # a higher score does not lift it
    assert _ids(_fill([dict(FULL_BLEED, n=2)], [better, WIDE_PERSON, WIDE_ROOM])[0]) == ["wide-room", "portrait-room"]


def test_the_portrait_rule_needs_a_long_side_of_1600_no_white_edge_and_a_room_or_exterior():
    for c in (dict(PORTRAIT_ROOM, long_side=1200), dict(PORTRAIT_ROOM, white_edge=True),
              dict(PORTRAIT_ROOM, white_edge=None),   # not measured
              dict(_cand("p", "lifestyle-person", aspect=0.667, fullbleed=False), long_side=1600, white_edge=False)):
        assert not images.portrait_fullbleed(c)
        assert _ids(_fill([FULL_BLEED], [c])[0]) == []


# ---------------------------------------------------------------------------
# B. hero sets for the tenant landing looks (and D's first screen in each)
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("slug,hero", LOOK_HEROES)
def test_each_tenant_landing_look_renders_each_hero_it_declares(tmp_path, monkeypatch, slug, hero):
    _tenant_look("landing", slug)
    if hero is None:
        pytest.skip(f"{slug} has no look.json on this machine")
    spec = heroes.look_spec("landing", slug, TENANT)
    assert "ad_first" in spec["heroes"] and len(spec["default_heroes"]) >= 3
    assert set(spec["default_heroes"]) <= set(spec["heroes"])
    # a composition that falls back stays inside the look's own set
    for h in spec["heroes"]:
        assert all(fb in spec["heroes"] for fb in heroes.get(h).fallback_ids())
    _library_dir(tmp_path, monkeypatch)   # library photos for the photo heroes, offline
    rc, run_dir, pages = fake_run.run_once(str(FIXTURE), tenant="peak-saunas", cartridges="landing", look=slug,
                                           hero_style=hero)
    assert rc == 0
    page = json.loads(pages[0].read_text())
    assert page["look"] == slug and page["hero_style"] == hero
    html = (run_dir / "landing" / "index.html").read_text()
    assert "{{" not in html and "{%" not in html
    if hero == "ad_first":
        assert "data-look-pattern=\"hero:" not in html   # the look's own first screen
    else:
        assert f'data-look-pattern="hero:{hero}"' in html
    assert len(re.findall(r"<h1[ >]", html)) == 1
    for check in (pagechecks.find_html_validity_violations(html),
                  pagechecks.find_rendered_json_ld_violations(html, "landing"),
                  pagechecks.find_image_markup_violations(html)):
        assert check == []


@pytest.mark.parametrize("slug", LANDING_LOOKS)
def test_each_tenant_landing_look_has_the_cycle_97_sticky_bar(slug):
    """The cycle 97 bar check (tests/test_grading_cycle97.py): the model name,
    the price beneath, one link that says exactly "Shop now"."""
    _tenant_look("landing", slug)
    rc, run_dir, _pages = fake_run.run_once(str(FIXTURE), tenant="peak-saunas", cartridges="landing", look=slug)
    assert rc == 0
    html = (run_dir / "landing" / "index.html").read_text()
    bar = html[html.index('<div class="ld-bar">'):]
    bar = bar[:bar.index("</div>")]
    assert '<p class="ld-bar-name">Peak Fuji<span>$8,250</span></p>' in bar
    # oura-restraint's bar is dark: the brand's white pill (ld-btn--light, brand/base.css)
    assert bar.count("<a ") == 1 and ">Shop now</a>" in bar and re.search(r'class="ld-btn( ld-btn--light)?"', bar)
    assert "IntersectionObserver" in html and 'b=document.querySelector(".adv-landing' in html
    assert _lint(html) == []


@pytest.mark.real_browser
@needs_browser
@pytest.mark.parametrize("slug,hero", LOOK_HEROES)
def test_each_tenant_look_and_hero_keeps_the_pill_on_the_first_phone_screen(tmp_path, slug, hero):
    _tenant_look("landing", slug)
    if hero is None:
        pytest.skip(f"{slug} has no look.json on this machine")
    html = _render_landing(_portrait_still_run(tmp_path), hero, look=slug)   # a 9:16 ad still
    probe = capture.probe(REAL_BROWSER, html, width=390, height=844, tenant=TENANT)
    checks = design_gate.hard_checks(probe, None, html.parent)
    for name in ("overflow_390", "first_screen_cta"):
        assert checks[name]["status"] == "pass", (name, checks[name])


# ---------------------------------------------------------------------------
# C. an item photo in its own ratio (Nourial, Swarva)
# ---------------------------------------------------------------------------

ITEM_MEDIA = {"nourial-ten-reasons": "nt-media", "swarva-doctor-byline": "sw-media"}
# per item photo: the empty band above it inside its box (a contained photo of
# another ratio sits on a neutral band) and the box's own height
_BAND_JS = r"""
(function(){ window.addEventListener('load', function(){ setTimeout(function(){
  var out = {items: []};
  document.querySelectorAll('.nt-media img,.sw-media img').forEach(function(img){
    var r = img.getBoundingClientRect(), cs = getComputedStyle(img), ch = r.height;
    var nw = img.naturalWidth, nh = img.naturalHeight;
    if (cs.objectFit === 'contain' && nw && nh) { ch = nh * Math.min(r.width / nw, r.height / nh); }
    out.items.push({band: Math.round((r.height - ch) / 2), h: Math.round(r.height), w: Math.round(r.width)});
  });
  parent.postMessage({__designProbe: out}, '*');
}, 300); }); })();
"""


def _render_items(tmp_path, slug):
    """A listicle page in `slug` whose item photos are 4:3 room photos (kind
    "interior": contained, not cropped, in a named frame)."""
    _tenant_look("listicle", slug)
    facts = copy.deepcopy(RICH_FACTS_PACK)
    for a in facts["assets"]:
        a["kind"] = "interior"
    page = dict(_listicle_page("story"), look=slug)
    run_dir = _run_dir(tmp_path)
    return render_mod.render_page(
        cartridge_name="listicle", page=page, ad_brief=SPEAKER_BRIEF, facts_pack=facts,
        cartridges_dir=REPO_ROOT / "cartridges", brand_dir=TENANT.brand_dir,
        templates_dir=REPO_ROOT / "harness" / "templates", out_dir=run_dir / "listicle",
        published="2026-10-09", updated="2026-10-09", tenant=TENANT, download_assets=True,
        fetch_url=lambda url: _make_image_bytes(600, 450))


@pytest.mark.parametrize("slug", sorted(ITEM_MEDIA))
def test_an_item_photo_keeps_its_own_ratio(tmp_path, slug):
    html = _render_items(tmp_path, slug).read_text()
    imgs = re.findall(rf'<div class="{ITEM_MEDIA[slug]}">.*?(<img [^>]+>)', html)
    assert imgs
    for tag in imgs:
        assert "aspect-ratio:4 / 3" in tag and "adv-img--contain" not in tag


@pytest.mark.real_browser
@needs_browser
@pytest.mark.parametrize("slug", sorted(ITEM_MEDIA))
def test_an_item_photo_has_no_band_over_24px_at_390(tmp_path, monkeypatch, slug):
    html = _render_items(tmp_path, slug)
    monkeypatch.setattr(capture, "PROBE_JS", _BAND_JS)
    probe = capture.probe(REAL_BROWSER, html, width=390, height=844, tenant=TENANT)
    items = probe["items"]
    assert items and max(i["band"] for i in items) <= 24, items
    # a 4:3 photo is a 4:3 box (it was a 4:5 / 1:1 box with the photo contained)
    assert all(abs(i["h"] - i["w"] * 3 / 4) <= 2 for i in items), items


# ---------------------------------------------------------------------------
# D. Eight Sleep: the pill on the first phone screen, desktop unchanged
# ---------------------------------------------------------------------------

def test_the_eight_sleep_phone_order_is_css_only_under_1024px():
    text = _tenant_look("landing", "eightsleep-buybox").joinpath("template.html").read_text()
    phone = re.search(r"@media \(max-width:1023\.98px\)\{(.*?)\n\}", text, re.S).group(1)
    assert ".es-gallery{display:contents}" in phone
    assert ".es-buy > .ld-btn" in phone and "order:1" in phone
    # the 1024px+ rules (gallery left, sticky buy panel right) are as before
    wide = re.search(r"@media \(min-width:1024px\)\{(.*?)\n\}", text, re.S).group(1)
    assert "grid-template-columns:minmax(0,1.25fr) minmax(0,1fr)" in wide and ".es-buy{position:sticky" in wide


@pytest.mark.real_browser
@needs_browser
def test_the_eight_sleep_pill_is_inside_the_first_phone_screen(tmp_path):
    _tenant_look("landing", "eightsleep-buybox")
    html = _render_landing(_portrait_still_run(tmp_path), "ad_first", look="eightsleep-buybox")
    phone = capture.probe(REAL_BROWSER, html, width=390, height=844, tenant=TENANT)
    assert phone["cta"]["top"] < 844 and "ld-btn" in phone["cta"]["sel"]   # it was 1,206px
    assert design_gate.first_screen_cta(phone)["status"] == "pass"
    # desktop: the buy panel is sticky beside the gallery (the probe skips a
    # sticky pill, so it is not measured here); no horizontal scroll
    desktop = capture.probe(REAL_BROWSER, html, width=1280, height=900, tenant=TENANT)
    assert desktop["scroll_width"] <= 1280
