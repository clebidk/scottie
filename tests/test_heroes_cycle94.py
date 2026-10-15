"""Cycle 94: pluggable hero compositions (harness/heroes/, docs/HEROES.md).

Every composition renders from a fixture with and without an ad still; the
needs/fallback rules; the resolver picks by availability and by the tenant
pin; the three shipped looks print exactly what they printed before (hash of
the page body, recorded on master bbc3ac4); offline fake runs with each new
composition pass the page checks and the Shopify export invariants; a static
390px check on every composition's CSS. No model or network call.
"""
import argparse
import copy
import hashlib
import json
import re

import pytest
from PIL import Image

from evals import fake_run
from harness import abtest, ad_frames, cli, heroes, images, library, pagechecks, runstate
from harness import photo_library as pl
from harness import render as render_mod
from harness.page_body import build_shopify_body
from tests.support import REPO_ROOT, TENANT
from tests.test_brand_lint_cycle78 import _PAGE_SCOPED, _css, _lint, _rules
from tests.test_first_screen_cycle79 import SPEAKER_BRIEF, _page as _listicle_page, _run_dir
from tests.test_landing_proof_cycle85 import _facts_pack as _landing_facts, _proof_page
from tests.test_listicle import RICH_FACTS_PACK, _make_image_bytes

NEW = ("caps_over_photo", "dark_statement", "split", "letters_pill", "buybox")
ADAPTERS = ("face", "story", "display", "ad_first")
FIXTURE = TENANT.fixtures_dir / "founder-warranty-demo.txt"


class _Log:
    def __init__(self):
        self.events = []

    def event(self, stage, message):
        self.events.append((stage, message))


def _landing_page(hero=None, look="open"):
    page = dict(copy.deepcopy(fake_run.LANDING_PAGE), look=look) if look == "open" else _proof_page()
    if hero:
        page["hero_style"] = hero
    return page


def _render(tmp_path, cartridge, page, *, frame=False, facts=None, download=True, log=None, name="r"):
    run_dir = _run_dir(tmp_path / name, with_frame=frame)
    (run_dir / cartridge).mkdir(exist_ok=True)
    facts = facts or (RICH_FACTS_PACK if cartridge == "listicle" else _landing_facts())
    html = render_mod.render_page(
        cartridge_name=cartridge, page=page, ad_brief=SPEAKER_BRIEF, facts_pack=facts,
        cartridges_dir=REPO_ROOT / "cartridges", brand_dir=TENANT.brand_dir,
        templates_dir=REPO_ROOT / "harness" / "templates", out_dir=run_dir / cartridge,
        published="2026-10-07", updated="2026-10-07", tenant=TENANT, download_assets=download,
        fetch_url=lambda url: _make_image_bytes(600, 450), log=log,
    ).read_text()
    return html, run_dir


def _pattern(html):
    m = re.search(r'data-look-pattern="hero:([a-z_]+)" data-hero-image="(\w+)"', html)
    return m.groups() if m else None


def _partial_css(hero_id):
    text = (heroes.PACKAGE_DIR / f"{hero_id}.html").read_text()
    return re.search(r"<style>(.*?)</style>", text, re.S).group(1)


# ---------------------------------------------------------------------------
# 1. The registry and the partials
# ---------------------------------------------------------------------------

def test_every_composition_is_registered_with_its_partial_needs_and_cartridges():
    assert set(heroes.ids()) == set(NEW) | set(ADAPTERS) | {"peak_first"}   # cycle 110
    assert heroes.ids("listicle") == ("face", "story", "display", "caps_over_photo", "dark_statement",
                                      "letters_pill", "split")
    assert set(heroes.ids("landing")) == {"ad_first", "caps_over_photo", "dark_statement", "split",
                                          "letters_pill", "buybox", "peak_first"}
    for hero_id in heroes.ids():
        hero = heroes.get(hero_id)
        assert (heroes.PACKAGE_DIR / f"{hero_id}.html").is_file(), hero_id
        assert hero.title and hero.summary and hero.supports
    assert heroes.get("buybox").supports == ("landing",)
    # the image a composition needs, in cycle 93's slot vocabulary (harness/images.py)
    assert heroes.get("caps_over_photo").needs["image"]["fullbleed_ok"] is True
    assert heroes.get("caps_over_photo").needs["image"]["sources"] == ("ad_still", "library")
    assert heroes.get("dark_statement").needs["image"]["shots"] == ("detail-macro",)
    assert heroes.get("dark_statement").needs["image"]["dark_ok"] is True
    assert heroes.get("dark_statement").needs["required"] is False
    assert heroes.get("split").needs["image"]["orientation"] == ["portrait", "square"]
    assert heroes.get("letters_pill").needs["image"]["crop"] == "pill"
    assert "cutout" in heroes.get("letters_pill").needs["image"]["sources"]
    assert heroes.get("buybox").needs["image"]["sources"] == ("cutout",)
    for hero_id in heroes.ids():
        spec = (heroes.get(hero_id).needs or {}).get("image") or {}
        assert set(spec.get("shots") or ()) <= set(images.SHOTS), hero_id


@pytest.mark.parametrize("hero_id", NEW)
def test_each_new_partial_is_scoped_tokens_only_and_marks_its_pattern(hero_id):
    text = (heroes.PACKAGE_DIR / f"{hero_id}.html").read_text()
    assert f'data-look-pattern="hero:{hero_id}"' in text
    css = _partial_css(hero_id)
    for sel, _body in _rules(css):
        for part in sel.split(","):
            part = part.strip()
            assert part.startswith(f".hr-{hero_id}") or part.startswith(".adv-wrap:has(.hr-bleed)"), part
    # tokens only: no colour literal, no gradient, no shadow
    assert not re.search(r"#[0-9a-fA-F]{3,8}\b|rgba?\(|hsla?\(|color-mix\(", css)
    assert "gradient" not in css and "box-shadow" not in css
    # every class the partial prints for itself carries its prefix
    for cls in re.findall(r'class="([^"{]*)"', text):
        for c in cls.split():
            assert c.startswith(("hr-", "adv-")), c


def test_the_shared_picker_partial_includes_the_resolved_composition():
    text = (heroes.PACKAGE_DIR / "_pick.html").read_text()
    assert '{% include "heroes/" ~ hero.id ~ ".html" %}' in text
    for look in ("listicle/looks/open", "landing/looks/open", "landing/looks/proof"):
        template = (REPO_ROOT / "cartridges" / look / "template.html").read_text()
        assert template.count('{% include "heroes/_pick.html" %}') == 1, look
        assert '<header class="op-hero' not in template and '<header class="ld-hero">' not in template


def test_look_json_declares_supported_and_default_compositions():
    assert heroes.look_heroes("listicle", "open") == ("face", "story", "display", "caps_over_photo", "split",
                                                      "dark_statement")
    assert heroes.look_spec("listicle", "open")["default_heroes"] == ("face", "story", "display")
    for look in ("open", "proof"):
        spec = heroes.look_spec("landing", look)
        assert spec["default_heroes"] == ("ad_first",)
        assert {"caps_over_photo", "split", "letters_pill", "dark_statement", "buybox"} <= set(spec["heroes"])
    # a look with no look.json (an imported look) keeps its cartridge's legacy screens
    assert heroes.look_spec("landing", "no-such-look") == {"heroes": ("ad_first",), "default_heroes": ("ad_first",)}
    assert heroes.look_heroes("listicle", None) == ("face", "story", "display")


# ---------------------------------------------------------------------------
# 2. Every composition renders, with and without an ad still
# ---------------------------------------------------------------------------

# (cartridge, hero, with a still) -> (rendered composition, image source)
EXPECTED = {
    ("listicle", "caps_over_photo", True): ("caps_over_photo", "ad_still"),
    ("listicle", "caps_over_photo", False): ("dark_statement", "none"),   # no photo: falls back
    ("listicle", "split", True): ("split", "ad_still"),
    ("listicle", "split", False): ("split", "cutout"),
    ("listicle", "dark_statement", True): ("dark_statement", "none"),
    ("listicle", "dark_statement", False): ("dark_statement", "none"),
    ("landing", "caps_over_photo", True): ("caps_over_photo", "ad_still"),
    ("landing", "caps_over_photo", False): ("dark_statement", "none"),
    ("landing", "split", True): ("split", "ad_still"),
    ("landing", "split", False): ("split", "cutout"),
    ("landing", "letters_pill", True): ("letters_pill", "cutout"),
    ("landing", "letters_pill", False): ("letters_pill", "cutout"),
    ("landing", "dark_statement", True): ("dark_statement", "none"),
    ("landing", "dark_statement", False): ("dark_statement", "none"),
    ("landing", "buybox", True): ("buybox", "cutout"),
    ("landing", "buybox", False): ("buybox", "cutout"),
}


@pytest.mark.parametrize("cartridge,hero_id,frame", sorted(EXPECTED))
def test_every_composition_renders_with_and_without_an_ad_still(tmp_path, cartridge, hero_id, frame):
    page = _listicle_page(hero_id) if cartridge == "listicle" else _landing_page(hero_id)
    log = _Log()
    html, run_dir = _render(tmp_path, cartridge, page, frame=frame, log=log)
    want_id, want_src = EXPECTED[(cartridge, hero_id, frame)]
    assert _pattern(html) == (want_id, want_src)
    assert json.loads((run_dir / cartridge / "page.json").read_text())["hero_style"] == want_id
    assert len(re.findall(r"<h1[\s>]", html)) == 1
    # the pill is the page's one CTA, on a class the A/B beacon counts
    head = html[html.index("data-look-pattern"):html.index("</header>", html.index("data-look-pattern"))]
    cta = re.search(r'<a class="([^"]*)\bhr-cta" data-el="hero" href="([^"]*)">', head)
    assert cta and set(cta.group(1).split()) & set(abtest.CTA_CLASSES) and cta.group(2) == page["cta_url"]
    messages = [m for _s, m in log.events]
    assert any(m.startswith(f"hero composition: {want_id} (") for m in messages)
    if want_id != hero_id:
        assert any(m.startswith(f"hero style: {hero_id} needs ") for m in messages)
    assert pagechecks.find_html_validity_violations(html) == []
    assert _lint(html) == []


def test_letters_come_from_the_tenant_setting_or_the_display_name(tmp_path, monkeypatch):
    html, _ = _render(tmp_path, "landing", _landing_page("letters_pill"))
    assert '<span class="hr-letters_pill-l hr-letters_pill-l1" aria-hidden="true">PE</span>' in html
    assert '<span class="hr-letters_pill-l hr-letters_pill-l2" aria-hidden="true">AK</span>' in html
    from harness.heroes._common import letters

    monkeypatch.setitem(TENANT.config, "hero", {"letters": ["pk", "lp"]})
    assert letters(TENANT) == ["PK", "LP"]
    monkeypatch.setitem(TENANT.config, "hero", {})
    assert letters(TENANT) == ["PE", "AK"]   # first two and last two of "PEAK"


def test_landing_compositions_carry_price_financing_trust_and_the_text_link(tmp_path):
    for hero_id in ("caps_over_photo", "split", "letters_pill", "dark_statement", "buybox"):
        html, _ = _render(tmp_path, "landing", _landing_page(hero_id), frame=True, name=hero_id)
        start = html.index("data-look-pattern")
        head = html[start:html.index("</header>", start)]
        assert "$" in head and "Bread Pay" in head, hero_id            # price + the fixed financing sentence
        # cycle 110: the one secondary link moved to the closing band (teardown D9)
        assert 'href="sms:+14706653747?&amp;body=' not in head, hero_id
        assert html.count('href="sms:+14706653747?&amp;body=') == 1, hero_id
        assert "Free shipping" in head, hero_id
        # the sticky bar observes the composition's root
        assert 'document.querySelector(".adv-landing .hr-root")' in html


def test_the_buybox_prints_spec_bullets_and_the_rating_once(tmp_path):
    html, _ = _render(tmp_path, "landing", _landing_page("buybox"))
    start = html.index('data-look-pattern="hero:buybox"')
    head = html[start:html.index("</header>", start)]
    assert head.count("<li><span><b>") >= 3 and 'class="hr-buybox-stars"' in head
    # the rating has its own row; the trust facts under the pill leave it out
    assert head.count("★") == 1 and "47 reviews" in head


def test_listicle_compositions_keep_the_byline_and_the_writer_headline(tmp_path):
    page = _listicle_page("split")
    html, _ = _render(tmp_path, "listicle", page)
    head = html[html.index('data-look-pattern="hero:split"'):]
    assert 'class="hr-split-by"' in head[:4000] and "PEAK founder" in head[:4000]
    assert page["headline"].split()[0] in head[:4000]


# ---------------------------------------------------------------------------
# 3. Needs, fallbacks and the resolver
# ---------------------------------------------------------------------------

def test_the_resolver_honours_the_request_and_falls_back_with_a_reason():
    assert heroes.resolve("landing", "split", look="open")[0] == "split"
    hero_id, reason, note = heroes.resolve("landing", "buybox", look="open",
                                           available=lambda h: (h != "buybox", "the product cut-out"))
    assert hero_id == "ad_first" and note == "buybox needs the product cut-out; none usable, so ad_first"
    hero_id, _r, note = heroes.resolve("listicle", "caps_over_photo", look="open",
                                       available=lambda h: (h != "caps_over_photo", "a wide photo"))
    assert hero_id == "dark_statement" and "caps_over_photo needs a wide photo" in note
    # the cycle 79 face rule, word for word
    hero_id, _r, note = heroes.resolve("listicle", "face", look="open", quote=True,
                                       available=lambda h: (h != "face", "a still from the ad"))
    assert (hero_id, note) == ("story", "face needs a still from the ad; none usable, so story")
    # a composition of another cartridge is refused; one the look does not render is replaced
    with pytest.raises(ValueError):
        heroes.resolve("listicle", "buybox", look="open")
    with pytest.raises(ValueError):
        heroes.resolve("landing", "face", look="open")
    hero_id, _r, note = heroes.resolve("listicle", "letters_pill", look="open")
    assert hero_id in ("face", "story", "display") and "does not render letters_pill" in note


def test_the_default_is_the_looks_own_behaviour():
    for seed in range(20):
        assert heroes.resolve("landing", None, look="open", seed=seed)[0] == "ad_first"
    picks = {heroes.resolve("listicle", None, look="open", seed=s, tenant=TENANT)[0] for s in range(40)}
    assert picks == {"face", "story", "display"}


def test_the_tenant_pin_narrows_the_seeded_pool_and_availability_picks_within_it(monkeypatch):
    monkeypatch.setitem(TENANT.config["cartridges"]["landing"], "hero_styles", ["split", "buybox", "face"])
    assert heroes.default_pool("landing", "open", TENANT) == ("split", "buybox")
    picks = {heroes.resolve("landing", None, look="open", seed=s, tenant=TENANT)[0] for s in range(30)}
    assert picks == {"split", "buybox"}
    no_split = lambda h: (h != "split", "a portrait or room photo")  # noqa: E731
    picks = {heroes.resolve("landing", None, look="open", seed=s, tenant=TENANT, available=no_split)[0]
             for s in range(30)}
    assert picks == {"buybox"}
    # a pin naming nothing the look renders is ignored
    monkeypatch.setitem(TENANT.config["cartridges"]["landing"], "hero_styles", ["face"])
    assert heroes.default_pool("landing", "open", TENANT) == ("ad_first",)


def _library_dir(tmp_path, monkeypatch, *, dark=True):
    """Image files for every photo in the committed manifest (the files are
    not in git), small, dark or light, so the library is 'on this machine'."""
    root = tmp_path / "photo-files"
    folder = root / TENANT.root.name   # photo_library.files_dir: <override>/<tenant folder name>
    folder.mkdir(parents=True)
    manifest = pl.load_manifest(TENANT)
    for photo in manifest["photos"]:
        w, h = photo["width"] // 2, photo["height"] // 2   # not favicon-sized (pagechecks)
        Image.new("RGB", (w, h), (30, 30, 32) if dark else (230, 225, 220)).save(folder / photo["file"], "JPEG")
    monkeypatch.setenv(pl.FILES_DIR_ENV, str(root))
    return manifest


def _offered(manifest, ids=None):
    return {p["id"]: pl.facts_pack_asset(p) for p in manifest["photos"] if ids is None or p["id"] in ids}


def test_pick_image_asks_the_cycle_93_resolver(tmp_path, monkeypatch):
    """Hero images come from images.fill_slots: richer tags (shot, fullbleed_ok,
    dark_ok, focal), the retired mark kept off the first screen, no photo the
    page already shows, the ad still first, then the cut-out."""
    manifest = _library_dir(tmp_path, monkeypatch)
    photos = {p["id"]: p for p in manifest["photos"]}
    caps = heroes.get("caps_over_photo").needs
    pick = heroes.pick_image(caps, facts_pack=RICH_FACTS_PACK, tenant=TENANT, assets={})
    photo = photos[pick["id"]]
    assert pick["source"] == "library" and pick["fit"] == "cover"
    assert images.photo_shots(photo) & {"hero-wide", "lifestyle-person", "exterior", "room"}
    assert not photo["tags"].get("old_logo_visible") and not photo["tags"]["has_text_overlay"]
    focal = photo["image_tags"]["focal"]
    assert pick["focal"] == focal and pick["position"] == f"{round(focal['x'] * 100)}% {round(focal['y'] * 100)}%"
    # the strict stage first: a full-bleed photo when one fits (cycle 101: or a
    # portrait room/exterior photo with a 1600px long side)
    if pick["why"].startswith("want"):
        assert photo["image_tags"]["fullbleed_ok"] or (
            max(photo["width"], photo["height"]) >= images.FULLBLEED_MIN_WIDTH
            and images.photo_shots(photo) & {"room", "exterior"})
    # a photo already on the page is never picked again
    again = heroes.pick_image(caps, facts_pack=RICH_FACTS_PACK, tenant=TENANT, assets={}, exclude_ids={pick["id"]})
    assert again is None or again["id"] != pick["id"]
    # dark macro: only a dark-page-ok detail photo
    macro = heroes.pick_image(heroes.get("dark_statement").needs, facts_pack=RICH_FACTS_PACK, tenant=TENANT,
                              assets={})
    if macro is not None:
        assert "detail-macro" in images.photo_shots(photos[macro["id"]])
        assert photos[macro["id"]]["image_tags"]["dark_ok"]
    # letters_pill cuts its photo to the pill window
    pill = heroes.pick_image(heroes.get("letters_pill").needs, facts_pack=RICH_FACTS_PACK, tenant=TENANT, assets={})
    assert pill["crop"] == ("pill" if pill["source"] == "library" else None)
    # cycle 97: a face (talking-head) still loses the full-bleed hero to a
    # matching library photo, and is its fallback (tests/test_grading_cycle97.py)
    run_dir = _run_dir(tmp_path / "still", with_frame=True)
    record = ad_frames.load(run_dir)
    assert heroes.pick_image(caps, facts_pack=RICH_FACTS_PACK, tenant=TENANT, assets={}, frame=record)["source"] == \
        "library"
    # no file on this machine: no library photo, and only the cut-out where the need allows it
    monkeypatch.setenv(pl.FILES_DIR_ENV, str(tmp_path / "empty"))
    assets = {}
    still = heroes.pick_image(caps, facts_pack=RICH_FACTS_PACK, tenant=TENANT, assets=assets, frame=record)
    assert still["source"] == "ad_still" and still["id"].startswith(ad_frames.FRAME_ID) and still["id"] in assets
    assert heroes.pick_image(caps, facts_pack=RICH_FACTS_PACK, tenant=TENANT, assets={}) is None
    split = heroes.pick_image(heroes.get("split").needs, facts_pack=RICH_FACTS_PACK, tenant=TENANT, assets={})
    assert split["source"] == "cutout" and split["fit"] == "contain"


def test_with_a_library_photo_caps_over_photo_renders_as_itself(tmp_path, monkeypatch):
    manifest = _library_dir(tmp_path, monkeypatch)
    facts = _landing_facts()
    facts["assets"] = list(facts.get("assets") or []) + list(_offered(manifest).values())
    html, run_dir = _render(tmp_path, "landing", _landing_page("caps_over_photo"), facts=facts)
    assert _pattern(html) == ("caps_over_photo", "library")
    assert (run_dir / "landing" / "assets").is_dir()


# ---------------------------------------------------------------------------
# 4. The shipped looks print what they printed before (master c3be6ba)
# ---------------------------------------------------------------------------

# sha256[:16] of <main ...> up to <footer> -- the page body without the
# tenant's head stylesheets -- rendered on master c3be6ba (brand fonts) without this cycle.
# If one changes, the look (or something it prints) changed: re-record only
# for an intended change, never to make this pass.
# Cycle 97 re-pin of the four landing keys (owner grading pass): the sticky bar
# shows the model name with the price beneath and its button reads "Shop now";
# the body diff against master ee2bd55 is that bar only.
# Cycle 110 re-pin of the four landing keys: the reasons section ("Why it
# works", mid CTA) replaces the checked claims, CTAs carry data-el, "Text us"
# is in the closing band, the closing band drops the warranty sentence.
# Cycle 110 (C) re-pin of the six listicle keys: the proof row and CTA under
# the first screen, data-el on every CTA, the value stack's fallback rows,
# the display strip led by the rating, and the shorter test filler (items at
# most 45 words, short sentences: the new reasons and reading-level gates).
SHIPPED = {
    "landing-open-dry": "7513037f31a2955a",
    "landing-open-frame": "b3a118f453e25126",
    "landing-proof-dry": "f35169064da7c94a",
    "landing-proof-frame": "1d8e0624c049b59f",
    "listicle-display-dry": "2ed5289c14ee0300",
    "listicle-display-frame": "2e8522597af3b4b7",
    "listicle-face-dry": "154c8161d82df44a",     # no still: face falls back to story
    "listicle-face-frame": "8d9000aced9fb089",
    "listicle-story-dry": "154c8161d82df44a",
    "listicle-story-frame": "e938914183542a78",
}


def _body_hash(html):
    return hashlib.sha256(html[html.index("<main"):html.index("<footer")].encode()).hexdigest()[:16]


@pytest.mark.parametrize("key", sorted(SHIPPED))
def test_the_shipped_looks_are_unchanged(tmp_path, key):
    cartridge, name, mode = key.split("-")
    frame = mode == "frame"
    if cartridge == "listicle":
        page = _listicle_page(name)
        run_dir = _run_dir(tmp_path / key, with_frame=frame)
        html = render_mod.render_page(
            cartridge_name="listicle", page=page, ad_brief=SPEAKER_BRIEF, facts_pack=RICH_FACTS_PACK,
            cartridges_dir=REPO_ROOT / "cartridges", brand_dir=TENANT.brand_dir,
            templates_dir=REPO_ROOT / "harness" / "templates", out_dir=run_dir / "listicle",
            published="2026-10-05", updated="2026-10-05", tenant=TENANT, download_assets=frame,
            fetch_url=lambda url: _make_image_bytes(600, 450)).read_text()
    elif frame:
        page = _landing_page(look=name)
        run_dir = _run_dir(tmp_path / key, with_frame=True)
        (run_dir / "landing").mkdir(exist_ok=True)
        html = render_mod.render_page(
            cartridge_name="landing", page=page, ad_brief=SPEAKER_BRIEF, facts_pack=_landing_facts(),
            cartridges_dir=REPO_ROOT / "cartridges", brand_dir=TENANT.brand_dir,
            templates_dir=REPO_ROOT / "harness" / "templates", out_dir=run_dir / "landing",
            published="2026-10-07", updated="2026-10-07", tenant=TENANT, download_assets=True,
            fetch_url=lambda url: _make_image_bytes(600, 450)).read_text()
    else:
        from tests.test_landing_cycle82 import _render as render82

        page = copy.deepcopy(fake_run.LANDING_PAGE) if name == "open" else _proof_page()
        facts = None if name == "open" else _landing_facts()
        html = (render82(tmp_path / key, page=page, facts_pack=facts) / "index.html").read_text()
    assert _body_hash(html) == SHIPPED[key]


# ---------------------------------------------------------------------------
# 5. Offline fake runs with each new composition
# ---------------------------------------------------------------------------

def _export_invariants(cartridge_dir):
    export, _manifest = build_shopify_body(cartridge_dir)
    assert export.startswith("<style>\n") and export.count("</style>") == 1
    assert not re.search(r'href="https?://(?:www\.)?peaksaunas\.com', export)
    unscoped = [sel for sel, _b in _rules(_css(export)) if not sel.startswith(".adv-wrap") and not _PAGE_SCOPED.search(sel)]
    assert unscoped == []
    assert export.count('class="pk-lp') == 1
    assert _lint(export) == [] and len(re.findall(r"<h1[\s>]", export)) == 1
    return export


FAKE_RUNS = [("landing", h) for h in ("caps_over_photo", "split", "letters_pill", "dark_statement", "buybox")] + \
            [("listicle", h) for h in ("caps_over_photo", "split", "dark_statement")]


@pytest.mark.parametrize("cartridge,hero_id", FAKE_RUNS)
def test_an_offline_fake_run_with_each_new_composition_passes_the_checks(tmp_path, monkeypatch, cartridge, hero_id):
    _library_dir(tmp_path, monkeypatch)   # so caps_over_photo has a photo offline
    rc, run_dir, pages = fake_run.run_once(str(FIXTURE), tenant="peak-saunas", cartridges=cartridge,
                                           hero_style=hero_id, style="reasons" if cartridge == "listicle" else None)
    assert rc == 0
    page = json.loads(pages[0].read_text())
    assert page["hero_style"] == hero_id
    state = json.loads((run_dir / "state.json").read_text())
    assert state[cartridge]["hero_style"] == hero_id
    html = (run_dir / cartridge / "index.html").read_text()
    assert f'data-look-pattern="hero:{hero_id}"' in html
    for check in (pagechecks.find_html_validity_violations(html),
                  pagechecks.find_rendered_json_ld_violations(html, cartridge),
                  pagechecks.find_rendered_internal_link_violations(html, tenant=TENANT),
                  pagechecks.find_image_markup_violations(html),
                  _lint(html)):
        assert check == []
    _export_invariants(run_dir / cartridge)


# ---------------------------------------------------------------------------
# 6. 390px: a CSS-only static check (the screenshots are in the FIXLOG)
# ---------------------------------------------------------------------------

_FIXED_WIDTH_RE = re.compile(r"(?<![-\w])(?:min-)?width:\s*(\d+)px")


@pytest.mark.parametrize("hero_id", NEW)
def test_no_fixed_width_wider_than_a_phone_and_44px_targets(hero_id):
    css = _partial_css(hero_id)
    phone_css = re.split(r"@media", css)[0]   # what a 390px screen applies
    for sel, body in _rules(phone_css):
        for value in _FIXED_WIDTH_RE.findall(body):
            assert int(value) <= 390 or "max-width" in body, (sel, value)
        assert "100vw" not in body, sel
        # no negative side margin that would push past the 16px gutter
        assert not re.search(r"margin(?:-left|-right)?:[^;}]*-\d", body), sel
    # every link the composition prints has a 44px+ target
    assert re.search(r"\.hr-cta\{[^}]*min-height:52px", css)
    link = "ghost" if hero_id == "letters_pill" else "text"
    assert re.search(rf"\.hr-{hero_id}-{link}\{{[^}}]*min-height:(?:44|52)px", css), hero_id
    # the root contains its own overflow or sits inside the page gutter
    assert re.search(rf"\.hr-{hero_id}\{{[^}}]*(?:overflow:hidden|max-width)", css), hero_id


# ---------------------------------------------------------------------------
# 7. The rest of the harness takes the new ids
# ---------------------------------------------------------------------------

def test_abtest_arms_take_compositions_and_the_variant_records_one(monkeypatch, tmp_path):
    arm = abtest.parse_arm("landing:open:letters_pill")
    assert (arm.id, arm.look, arm.hero_style) == ("landing:open:letters_pill", "open", "letters_pill")
    assert abtest.parse_arm("listicle:reasons:open:caps_over_photo").hero_style == "caps_over_photo"
    assert abtest.parse_arm("landing:proof").hero_style is None
    for bad in ("landing:open:face", "listicle:reasons:open:buybox", "landing:open:nope", "article:x:split"):
        with pytest.raises(abtest.AbtestError):
            abtest.parse_arm(bad)

    def runner(tenant, input_path, a, seed):
        run_dir = tmp_path / f"run-{seed}"
        (run_dir / a.cartridge).mkdir(parents=True)
        (run_dir / a.cartridge / "index.html").write_text("<html></html>")
        (run_dir / a.cartridge / "page.json").write_text(json.dumps({"look": a.look, "hero_style": "dark_statement"}))
        return 0, run_dir

    monkeypatch.setattr(abtest, "save_test", lambda tenant, rec: None)
    variant = {"key": "A", "arm": "landing:open:caps_over_photo", "attempts": []}
    rec = {"seed": 3, "input": "fixture.mov", "variants": [variant]}
    assert abtest._build_variant(TENANT, rec, variant, runner) == "built"
    assert variant["hero_style"] == "dark_statement"   # what it actually rendered


def test_cli_flags_accept_every_composition():
    parser = cli.build_parser()
    ns = parser.parse_args(["run", str(FIXTURE), "--cartridges", "landing", "--hero-style", "letters_pill"])
    assert ns.hero_style == "letters_pill"
    ns = parser.parse_args(["rerender", "x", "--page", "landing", "--hero-style", "buybox"])
    assert ns.hero_style == "buybox"
    with pytest.raises(SystemExit):
        parser.parse_args(["rerender", "x", "--page", "landing", "--hero-style", "billboard"])
    assert heroes.requested_for("listicle", "buybox") is None and heroes.requested_for("landing", "buybox") == "buybox"


def test_rerender_switches_a_landing_page_to_a_composition_and_records_it(tmp_path, monkeypatch):
    run_dir = _run_dir(tmp_path)
    (run_dir / "landing").mkdir()
    (run_dir / "facts_pack.json").write_text(json.dumps(_landing_facts()))
    (run_dir / "ad_brief.json").write_text(json.dumps(SPEAKER_BRIEF))
    (run_dir / "landing" / "page.json").write_text(json.dumps(_landing_page()))
    runstate.init_state(run_dir, pages=["landing"])
    monkeypatch.setattr(render_mod, "http_fetch_bytes", lambda url: _make_image_bytes(600, 450))
    args = argparse.Namespace(run_dir=str(run_dir), page="landing", note="", look=None,
                              hero_style="split", tenant=TENANT.name)
    assert cli.cmd_rerender(args) == 0
    assert 'data-look-pattern="hero:split"' in (run_dir / "landing" / "index.html").read_text()
    assert runstate.load_state(run_dir)["landing"]["hero_style"] == "split"
    assert json.loads((run_dir / "landing" / "page.json").read_text())["hero_style"] == "split"
    args.hero_style = "face"
    assert cli.cmd_rerender(args) == 1   # not a landing composition


def test_the_library_lists_a_card_per_supported_composition():
    by_key = {v.key: v for v in library.versions(TENANT)}
    for look in ("open", "proof"):
        assert f"landing/{look}" in by_key
        for hero_id in ("caps_over_photo", "split", "letters_pill", "dark_statement", "buybox"):
            version = by_key[f"landing/{look}/{hero_id}"]
            assert library.arm_for(version, TENANT) == f"landing:{look}:{hero_id}"
    assert library.arm_for(by_key["listicle/open/split/myths"], TENANT) == "listicle:myths:open:split"
    listicle_keys = [k for k in by_key if k.startswith("listicle/")]
    assert len(listicle_keys) == len(heroes.look_heroes("listicle", "open")) * 5
    info = library.describe(TENANT, by_key["landing/open/letters_pill"])
    assert info["title"] == "Landing page, open look, letters and pill first screen"
    assert "pill-shaped photo window" in info["notes"]
    # the upload form's pre-selection
    assert library.arm_from_fields("landing", "open", "buybox", None, tenant=TENANT).id == "landing:open:buybox"
    assert library.arm_from_fields("landing", None, "split", None, tenant=TENANT).id == "landing:open:split"
    with pytest.raises(library.LibraryError):
        library.arm_from_fields("landing", "open", "face", None, tenant=TENANT)
