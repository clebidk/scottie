"""Cycle 97: the owner's first grading pass (docs/FIXLOG.md cycle 97).

A. Phone-safe photo heroes: the caps_over_photo band is one phone screen
   whatever the photo's height, capture lays viewport-height units out
   against the device's screen, and the design gate's `first_screen_cta`
   hard check fails a page whose pill is below the first 844px.
B. A full-bleed hero takes the ad still only when it is a product/room shot
   of at least 720px on its short side; else a matching library photo.
C. The landing sticky bar names the model, the price beneath, "Shop now".
D. cinematic-light: the cinematic template on a white page (look.json palette).
E. A fake run imported by `harness library build` keeps the photo library.

No model or network call. The tests marked `real_browser` drive a headless
Chromium when one is installed (skipped otherwise).
"""
import copy
import json
import os
import subprocess
import sys

import pytest
from PIL import Image

from evals import fake_run
from harness import ad_frames, capture, design_gate, heroes, images, library, look_import, looks, pagechecks
from harness import photo_library as pl
from harness import render as render_mod
from harness.budget import Budget
from tests.conftest import FakeClient, json_response
from tests.support import REPO_ROOT, TENANT
from tests.test_design_gate_cycle92 import GOOD_PROBE, Log, _cfg, _judge_json, _page_dir, _renderer, fake_shots  # noqa: F401
from tests.test_first_screen_cycle79 import SPEAKER_BRIEF, _Log
from tests.test_blocks_cycle91 import _block_page, _render as _render_blocks
from tests.test_brand_lint_cycle78 import _lint
from tests.test_heroes_cycle94 import FIXTURE, _export_invariants, _library_dir, _partial_css
from tests.test_landing_proof_cycle85 import _facts_pack as _landing_facts
from tests.test_listicle import RICH_FACTS_PACK, _make_image_bytes

REAL_BROWSER = capture.find_browser()
needs_browser = pytest.mark.skipif(REAL_BROWSER is None, reason="no headless chromium/google-chrome here")


def _portrait_still_run(tmp_path, size=(1080, 1920)):
    """A run folder whose ad frame is a 9:16 portrait image ad (a person)."""
    run_dir = tmp_path / "20261008-120000-meta-1-abcd"
    (run_dir / "landing").mkdir(parents=True)
    image = tmp_path / "ad.jpg"
    Image.new("RGB", size, (120, 90, 70)).save(image, "JPEG")
    client = FakeClient([json_response({"person": True, "old_logo": False, "logos": [], "claim_text": [],
                                        "face_center": [0.5, 0.3]})])
    assert ad_frames.build_from_image(image, run_dir, client=client, budget=Budget(), log=_Log())["usable"]
    return run_dir


def _render_landing(run_dir, hero, look="cinematic"):
    page = dict(copy.deepcopy(fake_run.LANDING_PAGE), look=look, hero_style=hero)
    return render_mod.render_page(
        cartridge_name="landing", page=page, ad_brief=SPEAKER_BRIEF, facts_pack=_landing_facts(),
        cartridges_dir=REPO_ROOT / "cartridges", brand_dir=TENANT.brand_dir,
        templates_dir=REPO_ROOT / "harness" / "templates", out_dir=run_dir / "landing",
        published="2026-10-08", updated="2026-10-08", tenant=TENANT, download_assets=True,
        fetch_url=lambda url: _make_image_bytes(600, 450))


# ---------------------------------------------------------------------------
# A. phone-safe photo heroes
# ---------------------------------------------------------------------------

def test_caps_over_photo_caps_the_band_at_one_phone_screen_and_covers_the_photo(tmp_path):
    css = _partial_css("caps_over_photo")
    assert "min-height:clamp(560px,100svh,900px)" in css
    assert "88svh" not in css
    media = css[css.index(".hr-caps_over_photo-media .adv-img{"):]
    media = media[:media.index("}")]
    for rule in ("position:absolute", "inset:0", "height:100%", "object-fit:cover", "object-position:var(--hr-pos"):
        assert rule in media
    html = _render_landing(_portrait_still_run(tmp_path), "caps_over_photo").read_text()
    assert 'data-look-pattern="hero:caps_over_photo" data-hero-image="ad_still"' in html
    assert 'height="1600"' in html and "--hr-pos:50% 30%" in html   # the 9:16 still, on its face point


def test_capture_pins_viewport_height_units_to_the_device_screen():
    css = ".a{min-height:88svh}.b{height:100vh;max-height:calc(50dvh - 8px)}.c{--x-vh:2}.d{top:.5lvh}"
    out = capture.pin_viewport_height(css, 844)
    assert out == ".a{min-height:742.72px}.b{height:844px;max-height:calc(422px - 8px)}.c{--x-vh:2}.d{top:4.22px}"


def test_first_screen_cta_from_a_probe(tmp_path):
    page_dir, _ = _page_dir(tmp_path)
    ok = design_gate.hard_checks(dict(GOOD_PROBE, cta_expected=True, cta={"sel": "a.ld-btn", "top": 699}),
                                 None, page_dir)
    assert ok["first_screen_cta"]["status"] == "pass"
    low = design_gate.hard_checks(dict(GOOD_PROBE, cta_expected=True, cta={"sel": "a.ld-btn", "top": 2400}),
                                  None, page_dir)
    assert low["first_screen_cta"]["status"] == "fail" and "2400px" in low["first_screen_cta"]["detail"]
    assert "first_screen_cta" in design_gate.hard_fail(low)
    none = design_gate.hard_checks(dict(GOOD_PROBE, cta_expected=True, cta=None), None, page_dir)
    assert none["first_screen_cta"]["status"] == "fail"
    article = design_gate.hard_checks(dict(GOOD_PROBE, cta=None), None, page_dir)   # listicle story/face/display
    assert article["first_screen_cta"]["status"] == "pass"
    assert design_gate.hard_checks(None, None, page_dir)["first_screen_cta"]["status"] == "unknown"


def test_a_variant_with_its_cta_below_the_fold_is_never_judged(tmp_path, fake_shots):
    """The broken variant costs no judge call; the clean one is judged and chosen."""
    fake_shots.append(dict(GOOD_PROBE, cta_expected=True, cta={"sel": "a.ld-btn", "top": 2400}))
    fake_shots.append(dict(GOOD_PROBE, cta_expected=True, cta={"sel": "a.ld-btn", "top": 600}))
    page_dir, page = _page_dir(tmp_path, "landing", {"headline": "h", "look": "open", "hero_style": "ad_first"})
    render, _calls = _renderer(page_dir)
    client = FakeClient([_judge_json(81)])
    log = Log()
    result = design_gate.run_for_page(cartridge="landing", page_dir=page_dir, base_page=page, render=render,
                                      tenant=TENANT, client=client, cfg=_cfg(variants=2), log=log)
    first, second = result["variants"]
    assert first["judge"] is None and first["hard_fail"] == ["first_screen_cta"]
    assert first["judge_error"].startswith("not judged: the primary CTA starts at 2400px")
    assert result["judge_calls"] == 1 and result["chosen"] == second["n"] and result["status"] == "pass"


def test_every_variant_below_the_fold_stops_the_page(tmp_path, fake_shots):
    for mode, expected in (("enforce", "stop"), ("warn", "warn")):
        fake_shots.extend([dict(GOOD_PROBE, cta_expected=True, cta={"sel": "a.ld-btn", "top": 2400})] * 2)
        page_dir, page = _page_dir(tmp_path / mode, "landing", {"headline": "h", "look": "open"})
        render, _calls = _renderer(page_dir)
        result = design_gate.run_for_page(cartridge="landing", page_dir=page_dir, base_page=page, render=render,
                                          tenant=TENANT, client=FakeClient([]), cfg=_cfg(mode=mode, variants=1),
                                          log=Log())
        assert result["status"] == expected and result["judge_calls"] == 0
        assert len(result["rounds"]) == 2   # the revise round tried a different first screen
        assert any("below the first phone screen" in n for n in result["notes"])


@pytest.mark.real_browser
@needs_browser
def test_caps_over_photo_with_a_portrait_still_keeps_the_pill_on_the_first_screen(tmp_path):
    html = _render_landing(_portrait_still_run(tmp_path), "caps_over_photo")
    probe = capture.probe(REAL_BROWSER, html, width=390, height=844, tenant=TENANT)
    assert probe["cta_expected"] and probe["cta"]["top"] < 844
    checks = design_gate.hard_checks(probe, None, html.parent)
    assert checks["first_screen_cta"]["status"] == "pass" and checks["overflow_390"]["status"] == "pass"
    # a frame as tall as a full-page shot: the 900px cap still holds the pill up
    tall = capture.probe(REAL_BROWSER, html, width=390, height=3000, tenant=TENANT)
    assert tall["cta"]["top"] < 844


@pytest.mark.real_browser
@needs_browser
def test_a_synthetic_page_with_its_cta_at_2400px_fails_the_check(tmp_path):
    folder = tmp_path / "late"
    folder.mkdir()
    (folder / "index.html").write_text(
        '<!doctype html><html><head><meta name="viewport" content="width=device-width,initial-scale=1">'
        '<style>body{margin:0;font:16px sans-serif}</style></head><body><div class="adv-landing">'
        '<div style="height:2400px;background:#333"></div>'
        '<a class="ld-btn" href="#" style="display:block;min-height:52px">Shop now</a></div></body></html>')
    probe = capture.probe(REAL_BROWSER, folder / "index.html", width=390, height=844)
    assert probe["cta"]["top"] == 2400
    assert design_gate.hard_checks(probe, None, folder)["first_screen_cta"]["status"] == "fail"


# ---------------------------------------------------------------------------
# B. which still a full-bleed hero takes
# ---------------------------------------------------------------------------

def _record(tmp_path, *, still_face, still_size, face_frame=True):
    """An ad_frames.load()-shaped record: a face frame plus one extra still."""
    folder = tmp_path / "ad-frame"
    folder.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (900, 1600), (90, 80, 70)).save(folder / "frame.jpg")
    Image.new("RGB", still_size, (60, 50, 40)).save(folder / "still-1.jpg")
    return {"usable": True, "frame_path": str(folder / "frame.jpg"), "avatar_path": None, "width": 900,
            "height": 1600, "face_center": [0.5, 0.3], "face_checked": face_frame,
            "object_position": "50% 30%",
            "stills": [{"file": "still-1.jpg", "path": str(folder / "still-1.jpg"), "width": still_size[0],
                        "height": still_size[1], "face": still_face, "score": 9.0, "mood": "dark"}]}


CAPS = heroes.get("caps_over_photo").needs


def test_a_face_still_loses_to_a_matching_library_photo(tmp_path, monkeypatch):
    _library_dir(tmp_path, monkeypatch)
    record = _record(tmp_path, still_face=True, still_size=(1080, 1920))
    pick = heroes.pick_image(CAPS, facts_pack=RICH_FACTS_PACK, tenant=TENANT, assets={}, frame=record)
    assert pick["source"] == "library"


def test_a_small_product_still_loses_to_a_matching_library_photo(tmp_path, monkeypatch):
    _library_dir(tmp_path, monkeypatch)
    record = _record(tmp_path, still_face=False, still_size=(600, 1066))
    pick = heroes.pick_image(CAPS, facts_pack=RICH_FACTS_PACK, tenant=TENANT, assets={}, frame=record)
    assert pick["source"] == "library"


def test_a_1080px_product_still_wins(tmp_path, monkeypatch):
    _library_dir(tmp_path, monkeypatch)
    record = _record(tmp_path, still_face=False, still_size=(1080, 1920))
    pick = heroes.pick_image(CAPS, facts_pack=RICH_FACTS_PACK, tenant=TENANT, assets={}, frame=record)
    assert pick["source"] == "ad_still" and pick["id"] == f"{ad_frames.STILL_ID_PREFIX}1"
    assert pick["position"] == "50% 30%"


def test_with_no_library_match_the_face_still_is_still_used(tmp_path, monkeypatch):
    monkeypatch.setenv(pl.FILES_DIR_ENV, str(tmp_path / "no-files"))
    record = _record(tmp_path, still_face=True, still_size=(1080, 1920))
    pick = heroes.pick_image(CAPS, facts_pack=RICH_FACTS_PACK, tenant=TENANT, assets={}, frame=record)
    assert pick["source"] == "ad_still"


def test_the_still_rule_is_only_for_full_bleed_heroes():
    assert heroes._source_order(CAPS["image"])[:3] == [("ad_still", {"product_still": True}), ("library", {}),
                                                       ("ad_still", {})]
    split = heroes.get("split").needs["image"]
    assert heroes._source_order(split) == [("ad_still", {}), ("library", {}), ("cutout", {})]
    assert images.validate_slot({"name": "hero", "want": {"shot": ["ad-still"], "product_still": True}}) == []


# ---------------------------------------------------------------------------
# C. the sticky bar
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("look", ["open", "proof", "cinematic", "buybox", "cinematic-light"])
def test_the_sticky_bar_names_the_model_and_says_shop_now(tmp_path, look):
    page = _block_page(look) if look not in ("open", "proof") else (
        fake_run.LANDING_PAGE if look == "open" else fake_run.LANDING_PROOF_PAGE)
    html = (_render_blocks(tmp_path, page, look=look) / "index.html").read_text()
    bar = html[html.index('<div class="ld-bar">'):]
    bar = bar[:bar.index("</div>")]
    assert '<p class="ld-bar-name">Peak Fuji<span>$8,250</span></p>' in bar
    assert bar.count("<a ") == 1 and ">Shop now</a>" in bar and 'class="ld-btn"' in bar
    # the bar is shown once the first screen scrolls out (cinematic and buybox lost this in 0e5ee61)
    assert 'IntersectionObserver' in html and 'document.querySelector(".adv-landing' in html
    assert _lint(html) == []


def test_the_import_validator_allows_shop_now_only_in_the_sticky_bar():
    demo = look_import.load_demo(TENANT, "landing")
    page = demo["page"]
    from harness import landing
    ctx = landing.render_context(page, demo["facts_pack"], ad_brief=demo["ad_brief"], tenant=TENANT)
    url, text = page["cta_url"], page["cta_text"]
    text_link = f'<a class="ld-text" href="{ctx["closing_link"]["href"]}">Text us</a>'

    def errors(body):
        return look_import.cta_errors(f'<html><body><div class="pk-lp adv-landing">{body}{text_link}</div>'
                                      '</body></html>', "landing", page, ctx)

    assert errors(f'<a class="ld-btn" href="{url}">{text}</a>'
                  f'<div class="ld-bar"><p>Peak Fuji</p><a class="ld-btn" href="{url}">Shop now</a></div>') == []
    wrong = errors(f'<a class="ld-btn" href="{url}">Shop now</a>')
    assert len(wrong) == 1 and "one CTA phrase" in wrong[0]
    assert "Shop now" in look_import.allowed_links_text("landing")


# ---------------------------------------------------------------------------
# D. cinematic-light
# ---------------------------------------------------------------------------

def test_cinematic_light_is_a_landing_look_with_the_same_heroes_and_blocks():
    assert "cinematic-light" in looks.cartridge_looks("landing")
    assert looks.palette("landing", "cinematic-light") == "light"
    assert looks.palette("landing", "cinematic") is None and looks.palette("landing", "open") is None
    assert heroes.look_spec("landing", "cinematic-light") == heroes.look_spec("landing", "cinematic")
    from harness.blocks import catalog
    assert catalog.look_spec("landing", "cinematic-light") == catalog.look_spec("landing", "cinematic")
    template = (REPO_ROOT / "cartridges/landing/looks/cinematic-light/template.html").read_text()
    assert '{% extends "looks/cinematic/template.html" %}' in template and "<style>" not in template
    keys = {v.key for v in library.versions(TENANT)}
    assert {"landing/cinematic-light"} | {f"landing/cinematic-light/{h}" for h in
                                          ("dark_statement", "split", "letters_pill")} <= keys
    assert "on white" in library.describe(TENANT, library.find(TENANT, "landing/cinematic-light"))["notes"]


@pytest.mark.parametrize("hero", ["caps_over_photo", "dark_statement", "split", "letters_pill"])
def test_cinematic_light_renders_light_and_the_dark_look_does_not(tmp_path, hero):
    light = (_render_blocks(tmp_path / "l", dict(_block_page("cinematic-light"), hero_style=hero),
                            look="cinematic-light") / "index.html").read_text()
    dark = (_render_blocks(tmp_path / "d", dict(_block_page("cinematic"), hero_style=hero),
                           look="cinematic") / "index.html").read_text()
    assert 'class="pk-lp adv-landing cn-light"' in light and ".adv-landing.cn-light .hr-dark_statement{" in light
    assert 'class="cn-stats cn-stats--light"' in light
    assert "cn-light" not in dark
    assert ('class="cn-stats cn-stats--dark"' in dark) == (hero in ("caps_over_photo", "dark_statement"))
    assert _lint(light) == []


@pytest.mark.parametrize("hero", ["caps_over_photo", "dark_statement", "split", "letters_pill"])
def test_an_offline_fake_run_renders_cinematic_light_with_each_hero(tmp_path, monkeypatch, hero):
    _library_dir(tmp_path, monkeypatch)   # so caps_over_photo has a photo offline
    rc, run_dir, pages = fake_run.run_once(str(FIXTURE), tenant="peak-saunas", cartridges="landing",
                                           look="cinematic-light", hero_style=hero)
    assert rc == 0
    page = json.loads(pages[0].read_text())
    assert page["look"] == "cinematic-light" and page["hero_style"] == hero
    html = (run_dir / "landing" / "index.html").read_text()
    assert f'data-look-pattern="hero:{hero}"' in html and "cn-light" in html
    for check in (pagechecks.find_html_validity_violations(html),
                  pagechecks.find_rendered_json_ld_violations(html, "landing"),
                  pagechecks.find_image_markup_violations(html), _lint(html)):
        assert check == []
    _export_invariants(run_dir / "landing")


@pytest.mark.real_browser
@needs_browser
@pytest.mark.parametrize("hero", ["caps_over_photo", "dark_statement"])
def test_cinematic_light_passes_the_hard_checks_at_390(tmp_path, hero):
    run_dir = _portrait_still_run(tmp_path)
    html = _render_landing(run_dir, hero, look="cinematic-light")
    probe = capture.probe(REAL_BROWSER, html, width=390, height=844, tenant=TENANT)
    shot = tmp_path / "phone.png"   # the first screen is all the contrast check reads
    assert capture.screenshot(REAL_BROWSER, html, shot, width=390, height=900, tenant=TENANT, screen_height=844)
    checks = design_gate.hard_checks(probe, shot, html.parent)
    assert design_gate.hard_fail(checks) == []
    for name in ("overflow_390", "contrast_hero", "first_screen_cta"):
        assert checks[name]["status"] == "pass", (name, checks[name])


# ---------------------------------------------------------------------------
# E. the Library's fake runs keep the photo library
# ---------------------------------------------------------------------------

_LIBRARY_PROBE = """
import json, os, sys
from harness import library, tenant as tenant_mod
tenant = tenant_mod.load_tenant("peak-saunas", require=True)
fake_run = library._fake_run_module()          # imports tests/conftest.py (the suite's env)
out = {"env": os.environ.get("HARNESS_PHOTO_LIBRARY_DIR")}
version = library.find(tenant, "landing/open/caps_over_photo")
for name, files in (("with_files", sys.argv[1]), ("without_files", sys.argv[2])):
    os.environ["HARNESS_PHOTO_LIBRARY_DIR"] = files
    with library.offline_run_env(base_dir=sys.argv[3]) as scratch:
        run_dir, error = library._render_one(tenant, version, library._fixture(tenant), fake_run)
        page = json.loads((run_dir / "landing" / "page.json").read_text())
        out[name] = {"error": error, "hero": page.get("hero_style"), "mismatch": library._mismatch(version, page)}
print(json.dumps(out))
"""


def test_the_library_build_uses_local_library_photos_and_degrades_without_them(tmp_path, monkeypatch):
    """A fresh process, as `harness library build` is: importing the fake run
    (and with it tests/conftest.py) must not switch the photo library off."""
    _library_dir(tmp_path, monkeypatch)
    files = os.environ[pl.FILES_DIR_ENV]
    env = dict(os.environ, HARNESS_PHOTO_LIBRARY_DIR=files, PYTHONPATH=str(REPO_ROOT))
    proc = subprocess.run([sys.executable, "-c", _LIBRARY_PROBE, files, str(tmp_path / "no-files"),
                           str(tmp_path / "lib")], cwd=REPO_ROOT, env=env, capture_output=True, text=True,
                          timeout=300)
    assert proc.returncode == 0, proc.stderr[-2000:]
    out = json.loads(proc.stdout.strip().splitlines()[-1])
    assert out["env"] == files
    assert out["with_files"] == {"error": None, "hero": "caps_over_photo", "mismatch": None}
    assert out["without_files"]["hero"] == "dark_statement"
    assert "the photo library files are not on this machine" in out["without_files"]["mismatch"]
