"""Cycle 79: first-screen styles, ad stills, product cut-outs, gate changes,
and the photo-matching fix (docs/FIXLOG.md cycle 79).

No model or network call: the vision check is a FakeClient, ffmpeg is a fake
`run`, images are made with Pillow.
"""
import argparse
import copy
import io
import json
from pathlib import Path

import pytest
from PIL import Image

from harness import abtest, ad_frames, cli, cutouts, first_screen, headlines, listicle_quality, runstate
from harness import photo_library as pl
from harness import render as render_mod
from harness.budget import Budget
from tests.conftest import FakeClient, json_response
from tests.support import REPO_ROOT, TENANT
from tests.test_listicle import AD_BRIEF, FACTS_PACK, RICH_FACTS_PACK, _listicle_page, _make_image_bytes
from tests.test_photo_library import FUJI_HANDLE, FakeTenant, _library, _photo, _tags


class _Log:
    def __init__(self):
        self.events, self.calls = [], []

    def event(self, stage, message):
        self.events.append((stage, message))

    def call(self, stage, model, i, o, **kw):
        self.calls.append((stage, model, i, o))


def _jpeg(path, size=(640, 800), color=(120, 90, 60), noise=True):
    im = Image.new("RGB", size, color)
    if noise:
        px = im.load()
        for x in range(0, size[0], 4):
            for y in range(0, size[1], 4):
                px[x, y] = (255, 255, 255) if (x * 7 + y * 3) % 11 < 5 else (0, 0, 0)
    im.save(path, format="JPEG")
    return path


SPEAKER_BRIEF = {
    **AD_BRIEF,
    "speaker_pov": "first_person",
    "transcript_or_text": "I just ordered the sauna and I could not be more excited. "
                          "It fits right into the corner of my apartment.",
}


# ---------------------------------------------------------------------------
# Hero style resolution
# ---------------------------------------------------------------------------

def test_an_explicit_hero_style_wins_and_face_falls_back_without_a_still():
    assert first_screen.resolve_hero_style("story", tenant=TENANT) == ("story", "")
    assert first_screen.resolve_hero_style("face", tenant=TENANT, frame=True)[0] == "face"
    style, note = first_screen.resolve_hero_style("face", tenant=TENANT, frame=False, quote=True)
    assert style == "story" and "no" in note
    assert first_screen.resolve_hero_style("face", tenant=TENANT, frame=False, quote=False)[0] == "display"
    with pytest.raises(ValueError):
        first_screen.resolve_hero_style("billboard", tenant=TENANT)


def test_the_seeded_pick_covers_every_style_and_is_deterministic():
    picks = {first_screen.seeded_hero_style(seed, TENANT) for seed in range(40)}
    assert picks == set(first_screen.HERO_STYLES)
    assert first_screen.seeded_hero_style(7, TENANT) == first_screen.seeded_hero_style(7, TENANT)


# ---------------------------------------------------------------------------
# Rendering the three styles
# ---------------------------------------------------------------------------

def _run_dir(tmp_path, with_frame=False):
    run_dir = tmp_path / "20261005-120000-meta-1-abcd"
    (run_dir / "listicle").mkdir(parents=True)
    if with_frame:
        image = _jpeg(tmp_path / "ad.jpg")
        client = FakeClient([json_response({"person": True, "old_logo": False, "logos": [], "claim_text": [],
                                            "face_center": [0.5, 0.3]})])
        rec = ad_frames.build_from_image(image, run_dir, client=client, budget=Budget(), log=_Log())
        assert rec["usable"]
    return run_dir


def _render(run_dir, page, *, brief=SPEAKER_BRIEF, facts=RICH_FACTS_PACK):
    return render_mod.render_page(
        cartridge_name="listicle", page=page, ad_brief=brief, facts_pack=facts,
        cartridges_dir=REPO_ROOT / "cartridges", brand_dir=TENANT.brand_dir,
        templates_dir=REPO_ROOT / "harness" / "templates", out_dir=run_dir / "listicle",
        published="2026-10-05", updated="2026-10-05", tenant=TENANT, download_assets=True,
        fetch_url=lambda url: _make_image_bytes(600, 450),
    ).read_text()


def _page(style):
    page = _listicle_page()
    page["hero_style"] = style
    page["hero_quote_id"] = "q1"
    page["speaker_pronoun"] = "she"
    return page


def test_face_renders_the_ad_still_with_the_verbatim_quote(tmp_path):
    run_dir = _run_dir(tmp_path, with_frame=True)
    html = _render(run_dir, _page("face"))
    assert "op-hero--face" in html and 'class="op-face"' in html
    assert "assets/asset-adframe-" in html
    quote = first_screen.quote_candidates(SPEAKER_BRIEF, RICH_FACTS_PACK)[0]["text"]
    assert f"&ldquo;{quote}&rdquo;" in html.replace("“", "&ldquo;").replace("”", "&rdquo;")
    assert "In the ad she made for PEAK" in html
    assert "min read" in html and "&#10003;" in html            # one-line byline with the tick
    assert json.loads((run_dir / "listicle" / "page.json").read_text())["hero_style"] == "face"


def test_story_puts_the_quote_and_lede_on_the_first_screen(tmp_path):
    run_dir = _run_dir(tmp_path, with_frame=True)
    page = _page("story")
    html = _render(run_dir, page)
    head = html[html.index("op-hero--story"):html.index('class="op-c op-items"')]
    assert "In the ad, she says," in head and page["lede"] in head and page["eyebrow"] in head
    assert "asset-adframe-avatar" in head                         # the round crop
    assert head.index("op-h1") < head.index("op-by") < head.index("op-lede")


def test_display_shows_the_product_cutout_and_the_stats_strip(tmp_path):
    run_dir = _run_dir(tmp_path)
    html = _render(run_dir, _page("display"))
    head = html[html.index("op-hero--display"):html.index('class="op-c op-items"')]
    assert "op-display--wide" in head
    assert "assets/asset-cutout-fuji-" in head                    # the cut-out, not a photo
    assert "op-stats" in head and "Free" in head
    assert "op-cue" in head


def test_face_without_a_still_falls_back_and_says_so(tmp_path):
    run_dir = _run_dir(tmp_path)
    log = _Log()
    page = _page("face")
    render_mod.render_page(
        cartridge_name="listicle", page=page, ad_brief=SPEAKER_BRIEF, facts_pack=RICH_FACTS_PACK,
        cartridges_dir=REPO_ROOT / "cartridges", brand_dir=TENANT.brand_dir,
        templates_dir=REPO_ROOT / "harness" / "templates", out_dir=run_dir / "listicle",
        published="2026-10-05", updated="2026-10-05", tenant=TENANT, download_assets=False, log=log,
    )
    assert json.loads((run_dir / "listicle" / "page.json").read_text())["hero_style"] == "story"
    assert any("face needs a still" in m for _s, m in log.events)


def test_the_accent_phrase_is_set_in_the_brand_colour_once(tmp_path):
    page = _page("story")
    page["headline"] = "No electrician. No spare room. Still a real sauna."
    page["accent_phrase"] = "Still a real sauna."
    html = _render(_run_dir(tmp_path), page)
    assert '<span class="op-accent">Still a real sauna.</span>' in html
    assert html.count('class="op-accent"') == 1


def test_the_value_stack_cites_verified_claims_only():
    facts = copy.deepcopy(FACTS_PACK)
    facts["product"]["name"] = "Mini"
    facts["verified_claims"] += [
        {"id": "pdp-mini-electrical", "text": "Plugs into a standard 120V household outlet.", "category": "spec"},
        {"id": "pdp-mini-warranty", "text": "Limited lifetime warranty on the cabin.", "category": "spec"},
        {"id": "price-mini", "text": "The Peak Mini is priced at $5,450.", "category": "price"},
    ]
    stack = first_screen.value_stack(facts, _listicle_page(), TENANT)
    texts = [i["text"] for i in stack["items"]]
    assert texts == ["Plugs into a standard 120V household outlet"]   # warranty wording left out
    assert stack["price"] == {"text": "$5,450", "claim_ids": ["price-mini"]}
    assert stack["title"].startswith("What comes with the")


def test_rerender_switches_the_hero_style_and_records_it(tmp_path, monkeypatch):
    run_dir = _run_dir(tmp_path)
    (run_dir / "facts_pack.json").write_text(json.dumps(RICH_FACTS_PACK))
    (run_dir / "ad_brief.json").write_text(json.dumps(SPEAKER_BRIEF))
    (run_dir / "listicle" / "page.json").write_text(json.dumps(_page("story")))
    runstate.init_state(run_dir, pages=["listicle"])
    monkeypatch.setattr(render_mod, "http_fetch_bytes", lambda url: _make_image_bytes(600, 450))
    args = argparse.Namespace(run_dir=str(run_dir), page="listicle", note="", look=None,
                              hero_style="display", tenant=TENANT.name)
    assert cli.cmd_rerender(args) == 0
    assert "op-hero--display" in (run_dir / "listicle" / "index.html").read_text()
    assert runstate.load_state(run_dir)["listicle"]["hero_style"] == "display"


# ---------------------------------------------------------------------------
# Writer fields and gate changes
# ---------------------------------------------------------------------------

def _keys(problems):
    return {p["key"] for p in problems}


def test_the_accent_phrase_must_be_a_substring_of_the_headline():
    page = _page("story")
    page["accent_phrase"] = "something else"
    assert "listicle:first_screen:accent" in _keys(first_screen.find_first_screen_violations(page))
    page["accent_phrase"] = page["headline"].split()[-1]
    assert "listicle:first_screen:accent" not in _keys(first_screen.find_first_screen_violations(page))


def test_the_hero_quote_must_be_an_ad_quotes_id():
    page = _page("story")
    page["hero_quote_id"] = "q9"
    keys = _keys(first_screen.find_first_screen_violations(page, SPEAKER_BRIEF, RICH_FACTS_PACK))
    assert "listicle:first_screen:hero_quote" in keys
    page["hero_quote_id"] = "q1"
    keys = _keys(first_screen.find_first_screen_violations(page, SPEAKER_BRIEF, RICH_FACTS_PACK))
    assert "listicle:first_screen:hero_quote" not in keys


def test_quoted_words_at_the_top_must_be_verbatim():
    page = _page("story")
    page["headline"] = 'The apartment sauna she "could not be more excited" about'
    assert first_screen.find_verbatim_quote_violations(page, SPEAKER_BRIEF) == []
    page["headline"] = 'The apartment sauna she "is thrilled about"'
    assert _keys(first_screen.find_verbatim_quote_violations(page, SPEAKER_BRIEF)) == {
        "listicle:quote_verbatim:$.headline"}


def test_most_people_assertions_fail_but_questions_and_cited_lines_pass():
    page = _page("story")
    page["lede"] = "Most people rule out a home sauna before they ever check."
    assert any(k.startswith("listicle:most_people") for k in _keys(first_screen.find_most_people_violations(page)))
    page["lede"] = "Do most buyers check the outlet first?"
    assert first_screen.find_most_people_violations(page) == []
    page["reasons"][0]["text"] = "Most buyers pick the two-person model."
    page["reasons"][0]["claim_ids"] = ["spec-x"]
    assert first_screen.find_most_people_violations(page) == []


def test_a_lede_never_states_a_number():
    page = _page("story")
    page["lede"] = "It plugs into a 120V outlet."
    assert "listicle:first_screen:lede" in _keys(first_screen.find_first_screen_violations(page))


def test_she_in_the_headline_is_allowed_only_with_the_speakers_quote():
    page = _page("story")
    page["headline"] = "She wanted a real sauna. Her apartment had no spare room."
    meta = "listicle:meta_reference:$.headline"
    allowed = first_screen.pronoun_allowed(page, SPEAKER_BRIEF, RICH_FACTS_PACK)
    assert allowed
    assert meta not in _keys(listicle_quality.find_meta_reference_violations(page, allow_speaker_pronoun=allowed))
    page.pop("hero_quote_id")
    allowed = first_screen.pronoun_allowed(page, SPEAKER_BRIEF, RICH_FACTS_PACK)
    assert not allowed
    assert meta in _keys(listicle_quality.find_meta_reference_violations(page, allow_speaker_pronoun=allowed))


# ---------------------------------------------------------------------------
# Open-loop headline templates
# ---------------------------------------------------------------------------

def test_the_default_pick_is_an_open_loop_template_and_speaker_ones_need_a_speaker():
    no_speaker = {**AD_BRIEF, "speaker_pov": "brand"}
    for seed in range(12):
        plan = headlines.resolve_plan("questions", FACTS_PACK, TENANT, seed=seed, ad_brief=no_speaker)
        assert plan["open_loop"] and plan["id"] in ("o2", "o3")
        plan = headlines.resolve_plan("questions", FACTS_PACK, TENANT, seed=seed, ad_brief=SPEAKER_BRIEF)
        assert plan["open_loop"]
    # a count formula is still there when an operator names it
    assert headlines.resolve_plan("questions", FACTS_PACK, TENANT, requested="s-questions")["id"] == "s-questions"


@pytest.mark.parametrize("tid,headline,ok", [
    ("o2", "No electrician. No spare room. Still a real sauna.", True),
    ("o2", "No electrician. No spare room. Still 5 great reasons.", False),
    ("o3", "Training six days a week and still recovering at the gym?", True),
    ("o4", "She Wanted a real sauna. Her apartment Had no spare room.", True),
    ("o4", "She Wanted a real sauna. His apartment Had no spare room.", False),
    ("o1", 'The apartment sauna she "could not be more excited" about', True),
])
def test_open_loop_headlines_pass_or_fail_the_template_gate(tid, headline, ok):
    plan = headlines.build_plan(tid, "reasons", FACTS_PACK, TENANT)
    page = _page("story")
    page["headline"] = headline
    problems = [p for p in headlines.find_headline_violations(page, plan) if "headline" in p["key"]]
    assert (problems == []) is ok, problems


# ---------------------------------------------------------------------------
# Ad stills
# ---------------------------------------------------------------------------

def test_an_image_ad_with_the_old_logo_and_a_review_count_is_rejected(tmp_path):
    """The 2026-10-05 regret still: old mountain mark + "Trustpilot 4.6
    based on 3,892 reviews"."""
    image = _jpeg(tmp_path / "regret.jpg")
    answer = {"person": True, "old_logo": True, "logos": ["mountain logo"],
              "claim_text": ["Trustpilot 4.6 based on 3,892 reviews"], "face_center": [0.5, 0.3]}
    client = FakeClient([json_response(answer)])
    rec = ad_frames.build_from_image(image, tmp_path / "run", client=client, budget=Budget(), log=_Log(),
                                     retired_mark="a mountain logo")
    assert rec["usable"] is False and "retired brand mark" in rec["rejected"]
    assert ad_frames.load(tmp_path / "run") is None
    assert "a mountain logo" in client.messages.calls[0]["system"]
    answer["old_logo"], answer["logos"] = False, []
    rec = ad_frames.build_from_image(image, tmp_path / "run2", client=FakeClient([json_response(answer)]),
                                     budget=Budget(), log=_Log())
    assert rec["usable"] is False and "Trustpilot" in rec["rejected"]


def test_an_image_ad_is_never_used_without_the_check(tmp_path):
    rec = ad_frames.build_from_image(_jpeg(tmp_path / "a.jpg"), tmp_path / "run")
    assert rec["usable"] is False


def _fake_ffmpeg(tmp_path, frames):
    """frames: [(color, noisy)] -- what each cut frame looks like."""
    state = {"i": 0}

    class R:
        def __init__(self, stderr=""):
            self.stderr, self.stdout = stderr, ""

    def run(cmd, **kw):
        if "-frames:v" in cmd:
            color, noisy = frames[state["i"]]
            state["i"] += 1
            _jpeg(cmd[-1], color=color, noise=noisy)
            return R()
        return R("Duration: 00:00:20.00, start: 0")
    return run


def test_the_video_picker_drops_blurry_frames_and_takes_the_vision_pick(tmp_path):
    video = tmp_path / "ad.mp4"
    video.write_bytes(b"x")
    frames = [((120, 90, 60), False)] + [((120, 90, 60), True)] * 7     # frame 1 is flat (blurry)
    answer = {"frames": [{"index": i, "face": i != 2, "caption": "large" if i == 3 else "none",
                          "logo": False, "sharp": True} for i in range(1, 7)],
              "best": 3, "face_center": [0.4, 0.25], "reason": "x"}
    client = FakeClient([json_response(answer)])
    rec = ad_frames.build_from_video(video, tmp_path / "run", ffmpeg_bin="ffmpeg", client=client,
                                     budget=Budget(), log=_Log(), run=_fake_ffmpeg(tmp_path, frames))
    assert rec["usable"] and rec["method"] == "vision"
    assert [c["usable"] for c in rec["candidates"]][0] is False
    # best (3) has a large caption -> the first face frame without one wins (1 = sharpest shown)
    assert rec["chosen"]["file"] != rec["candidates"][0]["file"]
    assert (tmp_path / "run" / "ad-frame" / "avatar.jpg").exists()
    assert len(client.messages.calls) == 1                       # one vision call per ad
    shown = [b for b in client.messages.calls[0]["messages"][0]["content"] if b["type"] == "image"]
    assert len(shown) <= ad_frames.VISION_MAX


def test_no_face_in_any_frame_means_no_still(tmp_path):
    video = tmp_path / "ad.mp4"
    video.write_bytes(b"x")
    answer = {"frames": [{"index": i, "face": False, "caption": "none", "logo": False, "sharp": True}
                         for i in range(1, 7)], "best": None, "face_center": None, "reason": "product only"}
    rec = ad_frames.build_from_video(video, tmp_path / "run", ffmpeg_bin="ffmpeg",
                                     client=FakeClient([json_response(answer)]), budget=Budget(), log=_Log(),
                                     run=_fake_ffmpeg(tmp_path, [((120, 90, 60), True)] * 8))
    assert rec["usable"] is False and "face" in rec["rejected"]


# ---------------------------------------------------------------------------
# Cut-outs
# ---------------------------------------------------------------------------

def test_every_model_has_a_real_cutout_image():
    manifest = cutouts.load_manifest(TENANT)
    assert len(manifest["models"]) == 11
    for slug in manifest["models"]:
        asset = cutouts.cutout_asset(TENANT, slug)
        with Image.open(asset["local_path"]) as im:
            assert im.mode == "RGBA" and min(im.size) > 600
            assert im.getchannel("A").getextrema()[0] == 0          # transparent background


def test_the_comparison_and_quiz_rows_get_each_models_cutout():
    fp = {"comparison": {"models": [{"name": "Fuji", "image": {"id": "asset-x-1", "url": "u"}}]},
          "quiz": {"models": [{"slug": "mini", "name": "Mini", "image": {"id": "asset-quiz-mini", "url": "u"}},
                              {"slug": "nope", "name": "Nope", "image": {"id": "keep", "url": "u"}}]}}
    out = cutouts.with_model_cutouts(fp, TENANT)
    assert out["comparison"]["models"][0]["image"]["id"] == "asset-cutout-fuji"
    assert out["quiz"]["models"][0]["image"]["id"] == "asset-cutout-mini"
    assert out["quiz"]["models"][1]["image"]["id"] == "keep"
    assert fp["quiz"]["models"][0]["image"]["id"] == "asset-quiz-mini"   # input untouched


@pytest.mark.parametrize("cartridge,path", [("longform", ("hero", "hero_image")), ("listicle", ("hero",))])
def test_the_product_hero_is_the_cutout(cartridge, path):
    page = {"hero": {"hero_image": {"asset_id": "a"}} if cartridge == "longform" else {"asset_id": "a"}}
    assets = {}
    render_mod._apply_hero_cutout(page, cartridge, FACTS_PACK, TENANT, assets)
    node = page
    for key in path:
        node = node[key]
    assert node["asset_id"] == "asset-cutout-fuji" and "asset-cutout-fuji" in assets


# ---------------------------------------------------------------------------
# Photo matching: the 20261005-172955-...-cgkv regression
# ---------------------------------------------------------------------------

KEYWORDS = {
    "red-light-panel": ["red light"],
    "outlet-plug": ["outlet", "electrician"],
    "size-in-room": ["room"],
    "assembly": ["setup", "delivery"],
}
# The real tags (photo-library.json) of that run's pool, after the cycle 79
# correction of 8401eb5859e7 (no outlet in it).
RED_HEATER = _photo(0x8401, _tags(product="unknown", shot="detail", agnostic=True,
                                  features=["heaters", "red-light-panel", "wood-grain"]))
PLUG = _photo(0x0d82, _tags(product="unknown", shot="detail", agnostic=True, old_logo_visible=True,
                            features=["outlet-plug", "red-light-panel", "wood-grain"]))
FRONT = _photo(0xd279, _tags(product="fuji", shot="interior", setting="studio", old_logo_visible=True,
                             features=["bench", "control-panel", "heaters", "red-light-panel", "wood-grain"]))
RED_PANEL = _photo(0xff0e, _tags(product="unknown", shot="detail", setting="studio", agnostic=True,
                                 features=["red-light-panel", "wood-grain"]))
PERSON = _photo(0xfcac, _tags(product="unknown", shot="people-in-use", agnostic=True, people=1))
SPEAKER = _photo(0xa4d7, _tags(product="unknown", shot="detail", agnostic=True, features=["speakers", "wood-grain"]))
POOL = [RED_HEATER, PLUG, FRONT, RED_PANEL, PERSON, SPEAKER]
HEADINGS = ["You need an electrician to install a home sauna", "A home sauna takes over a room",
            "Red light therapy is always a paid add-on", "A warranty this long must have fine print",
            "Delivery and setup are a hassle"]


@pytest.fixture
def lib_tenant(tmp_path, monkeypatch):
    monkeypatch.setenv(pl.FILES_DIR_ENV, str(tmp_path / "files"))
    t = FakeTenant(tmp_path / "acme")
    t.config["photo_library"]["topic_keywords"] = KEYWORDS
    _library(t, POOL)
    return t


def _lib_facts(tenant):
    store = [{"id": f"asset-{FUJI_HANDLE}-{i}", "url": f"u{i}", "kind": "image"} for i in (1, 2)]
    return {"product": {"name": "Fuji", "slug": FUJI_HANDLE},
            "assets": [pl.facts_pack_asset(p) for p in POOL] + store}


def _items_page():
    return {"headline": "x", "dek": "", "hero": {"asset_id": "asset-cutout-fuji"},
            "reasons": [{"heading": h, "text": "", "image": {"asset_id": f"asset-{FUJI_HANDLE}-1"}} for h in HEADINGS]}


def test_the_red_light_item_gets_the_red_light_close_up_not_the_cabin_front(lib_tenant):
    page = _items_page()
    pl.assign_page_images(page, _lib_facts(lib_tenant), "listicle", tenant=lib_tenant,
                          allow_ai_renders=True, keep_hero=True)
    got = [r["image"]["asset_id"] for r in page["reasons"]]
    assert got[2] == RED_PANEL["id"]                     # equal score; the photo that is mostly the topic
    assert page["hero"]["asset_id"] == "asset-cutout-fuji"   # keep_hero: no photo spent on the hero
    assert got[0] not in (PLUG["id"], FRONT["id"])        # cycle 78: no old mark on the first item
    assert got[0] != RED_HEATER["id"]                     # the corrected tag no longer says outlet
    # a slot that names no topic gets a general photo, not a feature close-up
    assert got[0] == PERSON["id"]
    assert len(set(got)) == 5


def test_a_photo_whose_file_is_missing_is_never_assigned(lib_tenant):
    (pl.files_dir(lib_tenant) / RED_PANEL["file"]).unlink()
    page = _items_page()
    pl.assign_page_images(page, _lib_facts(lib_tenant), "listicle", tenant=lib_tenant,
                          allow_ai_renders=True, keep_hero=True)
    assert RED_PANEL["id"] not in {r["image"]["asset_id"] for r in page["reasons"]}


def test_the_committed_library_carries_the_8401_correction_and_keeps_it_on_retag():
    photo = pl.photos_by_id(pl.load_manifest(TENANT))["asset-photo-8401eb5859e7"]
    assert "outlet-plug" not in photo["tags"]["features_visible"]
    retagged = pl.apply_corrections(photo, dict(photo["tags"], features_visible=["outlet-plug", "heaters"]))
    assert retagged["features_visible"] == ["heaters"]


def test_topic_matching_runs_and_is_recorded_on_build_and_on_rerender(tmp_path, monkeypatch):
    """The cycle 79 report said no .image-selection.json was written. Every
    real render writes it; this pins it for the build path (render_page, as
    pipeline.render_pages calls it) and the rerender path (cli.cmd_rerender),
    with the real tenant and photo files on disk."""
    files = tmp_path / "files"
    monkeypatch.setenv(pl.FILES_DIR_ENV, str(files))
    (files / TENANT.name).mkdir(parents=True)
    for p in POOL:
        _jpeg(files / TENANT.name / p["file"], size=(400, 300))
    manifest = {"version": 1, "photos": POOL}
    monkeypatch.setattr(pl, "load_manifest", lambda tenant, log=None: manifest)
    facts = copy.deepcopy(FACTS_PACK)
    facts["assets"] = [pl.facts_pack_asset(p) for p in POOL] + facts["assets"]
    page = _listicle_page()
    page["reasons"] = [dict(r, heading=h) for r, h in zip(page["reasons"], HEADINGS)]
    page["hero_style"] = "story"
    run_dir = tmp_path / "20261005-172955-meta-1-cgkv"
    (run_dir / "listicle").mkdir(parents=True)
    render_mod.render_page(
        cartridge_name="listicle", page=page, ad_brief=AD_BRIEF, facts_pack=facts,
        cartridges_dir=REPO_ROOT / "cartridges", brand_dir=TENANT.brand_dir,
        templates_dir=REPO_ROOT / "harness" / "templates", out_dir=run_dir / "listicle",
        published="2026-10-05", updated="2026-10-05", tenant=TENANT, download_assets=True,
        fetch_url=lambda url: _make_image_bytes(600, 450),
    )
    sel = json.loads((run_dir / ".image-selection.json").read_text())
    built = {m["path"]: m["new_id"] for m in sel["matches"]["listicle"]}
    assert built["reasons[2].image"] == RED_PANEL["id"]
    assert built["reasons[0].image"] not in (PLUG["id"], FRONT["id"])
    html = (run_dir / "listicle" / "index.html").read_text()
    assert f"assets/{RED_PANEL['id']}-" in html

    (run_dir / ".image-selection.json").unlink()
    (run_dir / "facts_pack.json").write_text(json.dumps(facts))
    (run_dir / "ad_brief.json").write_text(json.dumps(AD_BRIEF))
    runstate.init_state(run_dir, pages=["listicle"])
    monkeypatch.setattr(render_mod, "http_fetch_bytes", lambda url: _make_image_bytes(600, 450))
    args = argparse.Namespace(run_dir=str(run_dir), page="listicle", note="", look=None, hero_style=None,
                              tenant=TENANT.name)
    assert cli.cmd_rerender(args) == 0
    sel = json.loads((run_dir / ".image-selection.json").read_text())
    assert {m["path"]: m["new_id"] for m in sel["matches"]["listicle"]} == built


# ---------------------------------------------------------------------------
# A/B/C
# ---------------------------------------------------------------------------

def test_an_arm_names_its_first_screen_and_results_pool_per_hero_style():
    arm = abtest.parse_arm("listicle:reasons:open:story", TENANT)
    assert (arm.id, arm.hero_style, arm.look) == ("listicle:reasons:open:story", "story", "open")
    assert abtest.parse_arm("listicle:reasons:cards", TENANT).id == "listicle:reasons:open"
    with pytest.raises(abtest.AbtestError):
        abtest.parse_arm("listicle:reasons:open:billboard", TENANT)
    heroes = {a.hero_style for a in abtest.library(TENANT) if a.cartridge == "listicle"}
    assert heroes == set(first_screen.HERO_STYLES)


def test_the_runner_passes_the_hero_style_to_the_run(monkeypatch, tmp_path):
    seen = {}

    class S:
        def __init__(self, tenant, args, client):
            seen["args"] = args
            self.run_dir = None

    monkeypatch.setattr(abtest.pipeline, "RunState", S)
    monkeypatch.setattr(abtest.pipeline, "execute", lambda state, stages: 0)
    monkeypatch.setattr("harness.anthropic_client.make_client", lambda: object())
    abtest.default_runner(TENANT, tmp_path / "ad.mp4", abtest.parse_arm("listicle:myths:open:face", TENANT), 3)
    assert seen["args"].hero_style == "face"


def test_a_captioned_throughout_video_still_gets_a_face_frame():
    vision = {"frames": {1: {"face": False, "caption": "large", "logo": True, "sharp": True},
                         2: {"face": True, "caption": "large", "logo": False, "sharp": True},
                         3: {"face": True, "caption": "large", "logo": False, "sharp": True}},
              "best": 3, "face_center": None, "reason": ""}
    assert ad_frames.choose(vision, [1, 2, 3]) == 3
    vision["frames"][2]["caption"] = "small"
    assert ad_frames.choose(vision, [1, 2, 3]) == 2      # a caption-free face wins when there is one


def test_an_image_ad_with_any_reported_mark_is_rejected():
    check = {"person": True, "old_logo": False, "logos": ["Mountain line-drawing logo on sauna glass door"],
             "claim_text": [], "face_center": None}
    assert "brand mark" in ad_frames.image_rejection(check)


def test_a_transparent_cutout_is_flattened_onto_white_not_black(tmp_path):
    asset = cutouts.cutout_asset(TENANT, "fuji")
    data = open(asset["local_path"], "rb").read()
    data, ext = render_mod.resize_asset_bytes(data, ".webp")
    info = render_mod.generate_image_variants(data, tmp_path, "asset-cutout-fuji")
    with Image.open(tmp_path / Path(info["variants"][0]["jpg"]).name) as im:
        assert im.getpixel((2, 2)) == (255, 255, 255) or min(im.getpixel((2, 2))) > 245
    assert info["cutout"] is True
