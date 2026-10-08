"""Cycle 93: the image pipeline -- richer tags, the slot resolver, focal
crops, better ad stills (docs/IMAGES.md section 11, docs/FIXLOG.md cycle 93).

No model or network call: vision is a FakeClient, ffmpeg is a fake `run`,
images are made with Pillow.
"""
import argparse
import copy
import hashlib
import json
import re

import pytest
from PIL import Image

from harness import ad_frames, asset_describe, cli, images
from harness import photo_library as pl
from harness import render as render_mod
from harness.budget import Budget
from tests.conftest import FakeClient, json_response
from tests.support import REPO_ROOT, TENANT
from tests import test_first_screen_cycle79 as fs79
from tests import test_landing_cycle82 as ld82
from tests.test_listicle import FACTS_PACK, _make_image_bytes
from tests.test_photo_library import FUJI_HANDLE, FakeTenant, _library, _photo, _tags


def _noise(im, box, step=3):
    px = im.load()
    x0, y0, x1, y1 = box
    for x in range(x0, x1, step):
        for y in range(y0, y1, step):
            px[x, y] = (250, 250, 250) if (x * 7 + y * 3) % 11 < 5 else (5, 5, 5)
    return im


# ---------------------------------------------------------------------------
# 1. Deterministic tags
# ---------------------------------------------------------------------------

def _light():
    return _noise(Image.new("RGB", (1600, 900), (205, 208, 212)), (0, 0, 1600, 900), step=9)


def _dark():
    return _noise(Image.new("RGB", (800, 1000), (28, 24, 20)), (300, 300, 500, 700), step=7)


def _white_bordered():
    im = Image.new("RGB", (1600, 1000), (255, 255, 255))
    im.paste((120, 70, 40), (500, 200, 1100, 800))       # the product on a studio white ground
    return _noise(im, (500, 200, 1100, 800), step=5)


def test_a_light_wide_photo_is_bright_and_full_bleed():
    t = images.measure_image(_light())
    assert (t["orientation"], t["mood"], t["fullbleed_ok"], t["white_edge"]) == ("landscape", "bright", True, False)
    assert t["aspect"] == pytest.approx(1.778, abs=0.001)
    assert t["dark_ok"] is False                           # too light for a dark page
    assert images.measure_image(_light()) == t             # deterministic


def test_a_dark_portrait_is_dark_page_ok_but_not_full_bleed():
    t = images.measure_image(_dark())
    assert (t["orientation"], t["mood"], t["dark_ok"], t["fullbleed_ok"]) == ("portrait", "dark", True, False)
    assert t["luminance"] < images.DARK_MAX_LUMA


def test_a_white_studio_border_is_never_full_bleed_or_dark_ok():
    t = images.measure_image(_white_bordered())
    assert t["white_edge"] is True and t["white_edge_share"] >= 0.99
    assert t["fullbleed_ok"] is False and t["dark_ok"] is False      # 1600 wide, but a studio shot
    assert t["mood"] == "bright"


def test_the_focal_guess_follows_the_detail():
    im = _noise(Image.new("RGB", (1200, 800), (90, 70, 50)), (900, 100, 1190, 400), step=4)
    focal = images.measure_image(im)["focal"]
    assert focal["x"] > 0.6 and focal["y"] < 0.45
    flat = images.measure_image(Image.new("RGB", (400, 300), (90, 70, 50)))["focal"]
    assert flat == {"x": 0.5, "y": 0.5}


def test_measuring_a_manifest_keeps_every_field_and_the_vision_part(tmp_path):
    _light().save(tmp_path / "asset-photo-000000000001.jpg")
    photo = _photo(1, _tags(product="fuji"))
    photo["image_tags"] = {"shot": "hero-wide", "quality": 5, "focal": {"x": 0.2, "y": 0.3},
                           "focal_source": "vision", "tagged_by": "m"}
    new, counts = images.measure_manifest({"version": 1, "photos": [photo]}, tmp_path)
    assert counts == {"measured": 1, "missing_file": 0, "kept": 0}
    out = new["photos"][0]
    assert {k: v for k, v in out.items() if k != "image_tags"} == {k: v for k, v in photo.items()
                                                                  if k != "image_tags"}   # additive
    it = out["image_tags"]
    assert (it["shot"], it["quality"], it["focal"], it["model"]) == ("hero-wide", 5, {"x": 0.2, "y": 0.3}, "fuji")
    assert it["mood"] == "bright" and images.validate_image_tags(it) == []
    assert "mood" not in photo["image_tags"]                                 # input not mutated


def test_the_committed_peak_library_carries_measured_tags_for_every_photo():
    manifest = pl.load_manifest(TENANT)
    assert len(manifest["photos"]) == 66
    for p in manifest["photos"]:
        assert images.validate_image_tags(p["image_tags"]) == [], p["id"]
        assert p["image_tags"]["measured"] == images.MEASURE_VERSION
        assert p["image_tags"]["model"] == (None if p["tags"]["product"] == "unknown" else p["tags"]["product"])


# ---------------------------------------------------------------------------
# 2. The resolver
# ---------------------------------------------------------------------------

def _cand(cid, shot, *, model=None, mood="warm", ai=False, old=False, aspect=1.5, fullbleed=True, quality=None,
          source="library", dark_ok=True):
    return {"id": cid, "source": source, "shot": shot, "shot_source": "vision", "shots": {shot}, "mood": mood,
            "aspect": aspect, "orientation": "landscape" if aspect > 1.05 else "portrait",
            "fullbleed_ok": fullbleed, "dark_ok": dark_ok, "focal": {"x": 0.5, "y": 0.5}, "model": model,
            "quality": quality, "ai_generated": ai, "old_brand": old, "blurry": False, "cluttered": False,
            "asset": {"id": cid}}


FACTS = {"product": {"name": "Fuji", "slug": FUJI_HANDLE}, "assets": []}
CUT = _cand("asset-cutout-fuji", "cutout", model="fuji", mood="bright", fullbleed=False, source="cutout")


def _fill(slots, pool, **kw):
    return images.fill_slots(slots, FACTS, None, pool=pool, prefer_model="fuji", **kw)


def _ids(result):
    return [p["id"] for p in result["picks"]]


def test_the_wanted_shot_order_then_the_model_then_real_over_ai():
    pool = [_cand("room-agn", "room"), _cand("wide-agn", "hero-wide"), _cand("room-own", "room", model="fuji"),
            _cand("wide-own-ai", "hero-wide", model="fuji", ai=True), _cand("wide-own", "hero-wide", model="fuji")]
    r = _fill([{"name": "gallery", "want": {"shot": ["hero-wide", "room"]}, "n": 5}], pool)[0]
    # the first wanted shot beats the second; the page's model beats an
    # agnostic photo; a real photo beats an AI one even one shot lower
    assert _ids(r) == ["wide-own", "room-own", "wide-agn", "room-agn", "wide-own-ai"]
    assert [p["score"] for p in r["picks"]] == sorted((p["score"] for p in r["picks"]), reverse=True)
    assert "the page's model" in r["picks"][0]["reasons"] and "AI-generated" in r["picks"][4]["reasons"]
    assert "model-agnostic" in r["picks"][2]["reasons"]


def test_requirements_filter_and_another_model_never_appears():
    pool = [_cand("narrow", "hero-wide", model="fuji", aspect=1.1), _cand("not-fb", "hero-wide", fullbleed=False),
            _cand("everest", "hero-wide", model="everest"), _cand("ok", "hero-wide", aspect=1.6), CUT]
    slot = {"name": "hero", "want": {"shot": ["hero-wide"], "fullbleed_ok": True, "aspect_min": 1.3}, "n": 1}
    assert _ids(_fill([slot], pool)[0]) == ["ok"]
    slot["n"] = 3
    r = _fill([slot], pool)[0]
    assert "everest" not in _ids(r) and _ids(r) == ["ok", "asset-cutout-fuji"] and r["short"] == 1


def test_the_fallback_chain_then_the_cutout():
    pool = [_cand("room-1", "room", model="fuji"), CUT]
    slot = {"name": "macro", "want": {"shot": ["detail-macro"], "mood": "dark"}, "n": 3,
            "fallback": ["room", "cutout"], "above_fold": False}
    r = _fill([slot], [_cand("room-1", "room", model="fuji", mood="dark"), CUT])[0]
    assert [(p["id"], p["via"]) for p in r["picks"]] == [("room-1", "fallback room"),
                                                         ("asset-cutout-fuji", "fallback cutout")]
    assert "relaxed frame" in r["picks"][0]["reasons"]
    # mood is kept on a fallback stage: a warm room never fills a dark slot
    r = _fill([slot], pool)[0]
    assert _ids(r) == ["asset-cutout-fuji"] and r["short"] == 2
    # no declared fallback: the last resort is the product cut-out, unless the slot says none
    r = _fill([{"name": "x", "want": {"shot": ["detail-macro"]}, "n": 1}], pool)[0]
    assert r["picks"][0]["via"] == "last resort"
    r = _fill([{"name": "x", "want": {"shot": ["detail-macro"]}, "n": 1, "last_resort": None}], pool)[0]
    assert r["picks"] == [] and r["short"] == 1


def test_no_photo_repeats_on_a_page_and_page_ids_count_as_used():
    pool = [_cand(f"room-{i}", "room", model="fuji") for i in range(3)] + [CUT]
    slots = [{"name": "a", "want": {"shot": ["room"]}, "n": 2, "last_resort": None},
             {"name": "b", "want": {"shot": ["room"]}, "n": 2, "last_resort": None}]
    a, b = _fill(slots, pool, used={"room-0"})
    assert _ids(a) == ["room-1", "room-2"] or _ids(a) == ["room-2", "room-1"]
    assert _ids(b) == [] and b["short"] == 2 and "not filled" in b["notes"][0]


def test_the_cutout_repeats_at_most_three_times():
    slots = [{"name": f"s{i}", "want": {"shot": ["cutout"]}, "n": 1} for i in range(5)]
    got = [_ids(r) for r in _fill(slots, [CUT])]
    assert got == [["asset-cutout-fuji"]] * 3 + [[], []]


def test_a_retired_mark_photo_never_fills_an_above_the_fold_slot():
    pool = [_cand("old", "room", model="fuji", old=True), _cand("new", "room")]
    hero, lower = _fill([{"name": "hero", "want": {"shot": ["room"]}, "n": 1},
                         {"name": "lower", "want": {"shot": ["room"]}, "n": 1}], pool)
    assert _ids(hero) == ["new"] and _ids(lower) == ["old"]
    assert "retired mark (below the fold)" in lower["picks"][0]["reasons"]


def test_the_seed_only_breaks_ties():
    pool = [_cand(f"r{i}", "room") for i in range(6)]
    slot = [{"name": "s", "want": {"shot": ["room"]}, "n": 6}]
    one = _ids(_fill(slot, pool, seed=1)[0])
    assert one == _ids(_fill(slot, pool, seed=1)[0])
    assert any(_ids(_fill(slot, pool, seed=s)[0]) != one for s in range(2, 8))
    better = pool + [_cand("q5", "room", quality=5)]
    assert all(_ids(_fill(slot, better, seed=s)[0])[0] == "q5" for s in range(5))


def test_an_invalid_slot_is_reported_not_raised():
    r = _fill([{"name": "x", "want": {"shot": ["selfie"]}, "n": 0, "crop": "7:3"}], [CUT])[0]
    assert r["picks"] == [] and any("want.shot" in n for n in r["notes"]) and any("crop" in n for n in r["notes"])


def test_a_bind_slot_records_what_the_page_already_shows():
    page = {"hero": {"asset_id": "asset-cutout-fuji"},
            "reasons": [{"image": {"asset_id": "room-1"}}, {"image": {"asset_id": None}}, {"heading": "x"}]}
    pool = [_cand("room-1", "room"), CUT]
    hero, items = _fill([{"name": "hero", "bind": "hero"}, {"name": "items", "bind": "reasons[].image"}], pool,
                        page=page)
    assert [(p["id"], p["path"]) for p in items["picks"]] == [("room-1", "reasons[0].image")]
    assert hero["picks"][0]["reasons"] == ["bound to page hero", "cutout"]


def test_candidates_honour_exclusion_ai_and_the_product_rules(tmp_path, monkeypatch):
    monkeypatch.setenv(pl.FILES_DIR_ENV, str(tmp_path / "files"))
    t = FakeTenant(tmp_path / "acme")
    own = _photo(1, _tags(product="fuji", shot="lifestyle"))
    excluded = dict(_photo(2, _tags(product="fuji", shot="lifestyle")), excluded=True)
    reviewed_out = _photo(3, _tags(product="fuji", shot="exterior"))
    ai = _photo(4, _tags(product="fuji", shot="detail"), ai=True)
    other = _photo(5, _tags(product="everest", shot="lifestyle"))
    agnostic = _photo(6, _tags(product="unknown", shot="people-in-use", agnostic=True, people=1))
    _library(t, [own, excluded, reviewed_out, ai, other, agnostic])
    (t.brand_dir / "asset-review.json").write_text(json.dumps(
        {"version": 1, "assets": {reviewed_out["id"]: {"excluded": True}}}))
    facts = {"product": {"name": "Fuji", "slug": FUJI_HANDLE}, "assets": [{"id": f"asset-{FUJI_HANDLE}-1", "url": "u"}]}
    pool = images.candidates(facts, t, allow_ai_renders=False)
    by_id = {c["id"]: c for c in pool}
    assert set(by_id) == {own["id"], agnostic["id"], f"asset-{FUJI_HANDLE}-1"}
    assert by_id[own["id"]]["shot"] == "room" and by_id[own["id"]]["shot_source"] == "legacy"
    assert by_id[agnostic["id"]]["shot"] == "lifestyle-person" and by_id[agnostic["id"]]["model"] is None
    assert ai["id"] in {c["id"] for c in images.candidates(facts, t, allow_ai_renders=True)}


# ---------------------------------------------------------------------------
# 3. Focal crops
# ---------------------------------------------------------------------------

def test_the_crop_window_sits_on_the_focal_point_inside_the_frame():
    assert images.crop_window(1600, 1200, "16:9", {"x": 0.5, "y": 0.5}) == (0, 150, 1600, 1050)
    assert images.crop_window(1600, 1200, "16:9", {"x": 0.5, "y": 0.95}) == (0, 300, 1600, 1200)
    assert images.crop_window(1600, 1200, "1:1", {"x": 0.9, "y": 0.5}) == (400, 0, 1600, 1200)
    assert images.crop_window(1600, 1200, "1:1", {"x": 0.3, "y": 0.5}) == (0, 0, 1200, 1200)
    left, top, right, bottom = images.crop_window(1600, 1200, "pill", None)
    assert (right - left) / (bottom - top) == pytest.approx(2.2, abs=0.01)


def _downloaded(tmp_path, size=(1600, 1200), focal=None):
    im = Image.new("RGB", size, (90, 70, 50))
    im.paste((250, 30, 30), (1200, 0, 1600, 400))           # a red corner to find again
    buf = __import__("io").BytesIO()
    im.save(buf, format="JPEG", quality=95)
    info = render_mod.generate_image_variants(buf.getvalue(), tmp_path / "assets", "asset-photo-000000000009")
    src = tmp_path / "assets" / info["variants"][-1]["jpg"].split("/")[-1]
    return {"id": "asset-photo-000000000009", "variants": info["variants"], "width": info["width"],
            "height": info["height"], "alt": "x", "src_path": str(src), "focal": focal}


def test_a_crop_makes_its_own_variants_round_the_focal_point_and_is_cached(tmp_path):
    asset = _downloaded(tmp_path, focal={"x": 0.9, "y": 0.1})
    cut = images.crop_asset(asset, "1:1")
    assert (cut["width"], cut["height"]) == (1200, 1200) and cut["crop"] == "1:1"
    assert all("-1x1-" in v["jpg"] for v in cut["variants"])
    with Image.open(tmp_path / "assets" / cut["variants"][-1]["jpg"].split("/")[-1]) as im:
        assert im.getpixel((1150, 50))[0] > 200              # the red corner is in the window
    files = sorted(p.name for p in (tmp_path / "assets").iterdir())
    again = images.crop_asset(asset, "1:1")
    assert again["variants"] == cut["variants"] and sorted(p.name for p in (tmp_path / "assets").iterdir()) == files
    other = images.crop_asset(dict(asset, focal={"x": 0.1, "y": 0.9}), "1:1")
    assert other["variants"][0]["jpg"] != cut["variants"][0]["jpg"]     # a new focal is a new cache key


def test_render_image_slot_crops_and_a_pill_is_rounded_in_css(tmp_path):
    asset = _downloaded(tmp_path)
    html = str(render_mod.render_image_slot(asset, crop="16:9"))
    assert "aspect-ratio:16 / 9" in html and "-16x9-" in html
    pill = str(render_mod.render_image_slot(asset, crop="pill"))
    assert "adv-img--pill" in pill and "border-radius:999px" in pill and "aspect-ratio:11 / 5" in pill
    with Image.open(tmp_path / "assets" / re.search(r'src="assets/([^"]+)"', pill).group(1)) as im:
        assert im.getpixel((0, 0)) != (255, 255, 255)        # square pixel corners: the mask is CSS
    # no source file: the same window in CSS, at the focal point
    plain = dict(asset, src_path=None, focal={"x": 0.25, "y": 0.75})
    css = str(render_mod.render_image_slot(plain, crop="4:5"))
    assert "aspect-ratio:4 / 5;object-position:25% 75%" in css and "adv-img--cover" in css
    assert str(render_mod.render_image_slot(asset)) == str(render_mod.render_image_slot(asset, crop=None))


# ---------------------------------------------------------------------------
# 4. Ad stills
# ---------------------------------------------------------------------------

def test_the_still_score_prefers_sharp_mid_exposed_faces():
    sharp = {"laplacian": 900.0, "brightness": 128}
    assert ad_frames.still_score(sharp, face=True) > ad_frames.still_score(sharp) > ad_frames.still_score(sharp, face=False)
    assert ad_frames.still_score(sharp) > ad_frames.still_score({"laplacian": 4.0, "brightness": 128})
    assert ad_frames.still_score(sharp) > ad_frames.still_score({"laplacian": 900.0, "brightness": 245})
    assert ad_frames.still_score(sharp, face=True, caption="large") < ad_frames.still_score(sharp, face=True)


def test_laplacian_variance_separates_sharp_from_flat():
    flat = Image.new("L", (200, 200), 120)
    sharp = _noise(Image.new("RGB", (200, 200), (120, 120, 120)), (0, 0, 200, 200), step=2).convert("L")
    assert images.laplacian_variance(flat) == 0
    assert images.laplacian_variance(sharp) > 1000


def _ffmpeg_with_scenes(tmp_path, frames, scenes):
    """The cycle 79 fake plus the scene call: writes `scenes` [(color, noisy)]
    and prints their pts_time like ffmpeg's showinfo."""
    even = fs79._fake_ffmpeg(tmp_path, frames)

    class R:
        def __init__(self, stderr=""):
            self.stderr, self.stdout = stderr, ""

    def run(cmd, **kw):
        if any("select=" in str(c) for c in cmd):
            pattern = str(cmd[-1])
            for i, (color, noisy) in enumerate(scenes, 1):
                fs79._jpeg(pattern.replace("%02d", f"{i:02d}"), color=color, noise=noisy)
            return R("".join(f"n:{i} pts_time:{1.5 * i}\n" for i in range(1, len(scenes) + 1)))
        return even(cmd, **kw)
    return run


def test_a_video_gives_three_scored_stills_from_even_and_scene_frames(tmp_path):
    video = tmp_path / "ad.mp4"
    video.write_bytes(b"x")
    frames = [((120, 90, 60), False)] + [((120, 90, 60), True)] * 7       # frame 1 is flat
    answer = {"frames": [{"index": i, "face": i != 2, "caption": "none", "logo": False, "sharp": True}
                         for i in range(1, 7)], "best": 1, "face_center": [0.4, 0.25], "reason": "x"}
    rec = ad_frames.build_from_video(
        video, tmp_path / "run", ffmpeg_bin="ffmpeg", client=FakeClient([json_response(answer)]),
        budget=Budget(), log=fs79._Log(),
        run=_ffmpeg_with_scenes(tmp_path, frames, [((60, 60, 60), True), ((200, 200, 200), False)]))
    assert rec["usable"]
    stills = rec["stills"]
    assert len(stills) == ad_frames.STILLS
    assert [s["score"] for s in stills] == sorted((s["score"] for s in stills), reverse=True)
    assert rec["chosen"]["file"].split("/")[-1] not in {s.get("file") for s in stills}
    assert len(rec["scene_candidates"]) == 2 and rec["scene_candidates"][1]["usable"] is False
    assert all((tmp_path / "run" / "ad-frame" / s["file"]).exists() for s in stills)
    assert all(s["face"] is not False for s in stills)        # the no-face frame (2) ranks below
    loaded = ad_frames.load(tmp_path / "run")
    assert [s["file"] for s in loaded["stills"]] == [s["file"] for s in stills]
    # a slot asking for ad stills gets the face-checked first-screen frame first, then the stills
    pool = images.ad_still_candidates(loaded)
    r = images.fill_slots([{"name": "film", "want": {"shot": ["ad-still"]}, "n": 4, "last_resort": None}],
                          FACTS, None, pool=pool)[0]
    assert _ids(r) == [ad_frames.FRAME_ID] + [f"{ad_frames.STILL_ID_PREFIX}{i}" for i in (1, 2, 3)]


def test_a_failing_scene_call_leaves_the_even_frames(tmp_path):
    video = tmp_path / "ad.mp4"
    video.write_bytes(b"x")
    rec = ad_frames.build_from_video(video, tmp_path / "run", ffmpeg_bin="ffmpeg",
                                     run=fs79._fake_ffmpeg(tmp_path, [((120, 90, 60), True)] * 8))
    assert rec["usable"] and rec["method"] == "heuristic"      # the first-screen choice is as before
    assert "scene_candidates" not in rec and len(rec["stills"]) == ad_frames.STILLS


# ---------------------------------------------------------------------------
# 5. Audit
# ---------------------------------------------------------------------------

def _measured(n, shot, mood, **kw):
    p = _photo(n, _tags(product="fuji", shot=shot, **kw))
    p["image_tags"] = {"orientation": "landscape", "aspect": 1.5, "mood": mood, "fullbleed_ok": mood != "dark",
                       "dark_ok": mood == "dark", "focal": {"x": 0.5, "y": 0.5}, "shot": None, "quality": None,
                       "measured": images.MEASURE_VERSION}
    return p


def test_the_audit_counts_shots_and_moods_and_names_the_gaps():
    manifest = {"version": 1, "photos": [
        _measured(1, "lifestyle", "warm"), _measured(2, "lifestyle", "bright"), _measured(3, "detail", "bright"),
        _measured(4, "exterior", "dark"), dict(_measured(5, "detail", "dark"), excluded=True),
    ]}
    report = images.audit(manifest)
    assert report["usable"] == 4 and report["measured"] == 4 and report["vision_tagged"] == 0
    assert report["by_shot"]["room"]["warm"] == 1 and report["by_shot"]["hero-wide"]["bright"] == 1
    assert "no dark detail-macro" in report["gaps"] and "no lifestyle-person" in report["gaps"]
    text = "\n".join(images.audit_lines(report))
    assert "detail-macro" in text and "gaps:" in text and "  - no dark detail-macro" in text


def test_the_audit_command_reads_another_copy_and_writes_nothing(tmp_path, capsys):
    files = tmp_path / "files"
    files.mkdir()
    photo = _photo(1, _tags(product="fuji", shot="exterior"))
    _dark().save(files / photo["file"])
    path = tmp_path / "photo-library.json"
    path.write_text(json.dumps({"version": 1, "photos": [photo]}))
    before = path.read_bytes()
    args = argparse.Namespace(tenant=TENANT.name, manifest=str(path), files=str(files), measure=True)
    assert cli.cmd_photos_audit(args) == 0
    out = capsys.readouterr().out
    assert "1 usable, 1 measured" in out and "only 1 dark exterior" in out
    assert path.read_bytes() == before and sorted(p.name for p in files.iterdir()) == [photo["file"]]


# ---------------------------------------------------------------------------
# Vision part: harness photos tag --only-missing (FakeClient)
# ---------------------------------------------------------------------------

def test_the_vision_look_fields_extend_the_describe_call(tmp_path, monkeypatch):
    monkeypatch.setenv(pl.FILES_DIR_ENV, str(tmp_path / "files"))
    t = FakeTenant(tmp_path / "acme")
    a, b = _photo(1, _tags(product="fuji")), _photo(2, _tags(product="unknown", agnostic=True))
    done = dict(_photo(3, _tags(product="fuji")))
    _library(t, [a, b, done])
    for p in (a, b, done):
        _light().save(pl.files_dir(t) / p["file"])
    manifest = pl.load_manifest(t)
    manifest["photos"][2]["image_tags"] = dict(images.measure_image(_light()), shot="room", quality=4,
                                               model="fuji", focal_source="edges")
    pl.save_manifest(t, manifest)
    client = FakeClient([
        json_response({"name": "a", "alt": "A sauna.", "tags": [], "shot": "hero-wide", "quality": 5,
                       "focal": [0.3, 0.6]}),
        json_response({"name": "b", "alt": "A bench.", "tags": [], "shot": "selfie", "quality": 2}),
    ])
    result = images.tag_missing(t, client=client, model="claude-haiku-4-5", budget=Budget(), log=fs79._Log())
    assert result == {"tagged": 1, "failed": 1, "skipped": 1}
    system = client.messages.calls[0]["system"]
    assert "hero-wide" in system and "quality" in system and "focal" in system
    by_id = pl.photos_by_id(pl.load_manifest(t))
    it = by_id[a["id"]]["image_tags"]
    assert (it["shot"], it["quality"], it["focal"], it["focal_source"]) == ("hero-wide", 5, {"x": 0.3, "y": 0.6}, "vision")
    assert it["mood"] == "bright" and it["model"] == "fuji"            # measured first
    assert images.needs_vision(by_id[b["id"]])                        # the invalid answer left it untagged
    assert by_id[done["id"]]["image_tags"]["shot"] == "room"           # --only-missing skipped it


def test_the_plain_describe_prompt_is_unchanged_without_look_fields():
    product = {"name": "Fuji", "title": "Fuji sauna"}
    assert "quality" not in asset_describe.build_vision_system_prompt(product, [])
    with pytest.raises(asset_describe.VisionResponseInvalid):
        asset_describe._parse_vision_response('{"name": "a", "alt": "b", "tags": [], "shot": "room", "quality": 9}',
                                               look_fields=True)


# ---------------------------------------------------------------------------
# 6. Shipped looks unchanged; a tenant look's declared slots
# ---------------------------------------------------------------------------

@pytest.fixture
def library_on_disk(tmp_path, monkeypatch):
    files = tmp_path / "files"
    monkeypatch.setenv(pl.FILES_DIR_ENV, str(files))
    (files / TENANT.name).mkdir(parents=True)
    for p in fs79.POOL:
        fs79._jpeg(files / TENANT.name / p["file"], size=(400, 300))
    manifest = {"version": 1, "photos": fs79.POOL}
    monkeypatch.setattr(pl, "load_manifest", lambda tenant, log=None: manifest)
    return tmp_path


def _with_frame(run_dir, tmp_path):
    image = fs79._jpeg(tmp_path / f"ad-{run_dir.parent.name}.jpg")
    client = FakeClient([json_response({"person": True, "old_logo": False, "logos": [], "claim_text": [],
                                        "face_center": [0.5, 0.3]})])
    ad_frames.build_from_image(image, run_dir, client=client, budget=Budget(), log=fs79._Log())


def _render_listicle(tmp_path, style, tag):
    facts = copy.deepcopy(FACTS_PACK)
    facts["assets"] = [pl.facts_pack_asset(p) for p in fs79.POOL] + facts["assets"]
    page = fs79._page(style)
    page["reasons"] = [dict(r, heading=h) for r, h in zip(page["reasons"], fs79.HEADINGS)]
    run_dir = tmp_path / f"{tag}-{style}" / "20261005-172955-meta-1-cgkv"
    (run_dir / "listicle").mkdir(parents=True)
    _with_frame(run_dir, tmp_path)
    render_mod.render_page(
        cartridge_name="listicle", page=page, ad_brief=fs79.SPEAKER_BRIEF, facts_pack=facts,
        cartridges_dir=REPO_ROOT / "cartridges", brand_dir=TENANT.brand_dir,
        templates_dir=REPO_ROOT / "harness" / "templates", out_dir=run_dir / "listicle",
        published="2026-10-05", updated="2026-10-05", tenant=TENANT, download_assets=True,
        fetch_url=lambda url: _make_image_bytes(600, 450))
    return run_dir, (run_dir / "listicle" / "index.html").read_bytes()


def _render_landing(tmp_path, look, tag, *, frame=True, looks_dir=None):
    facts = ld82._facts_pack()
    facts["assets"] = [pl.facts_pack_asset(p) for p in fs79.POOL] + facts["assets"]
    page = ld82._page(look=look)
    page["room_photo"] = {"asset_id": fs79.PERSON["id"]}
    run_dir = tmp_path / f"{tag}-{look}-{frame}" / "20261007-120000-meta-1-land"
    (run_dir / "landing").mkdir(parents=True)
    if frame:
        _with_frame(run_dir, tmp_path)
    render_mod.render_page(
        cartridge_name="landing", page=page, ad_brief=ld82.AD_BRIEF, facts_pack=facts,
        cartridges_dir=REPO_ROOT / "cartridges", brand_dir=TENANT.brand_dir,
        templates_dir=REPO_ROOT / "harness" / "templates", out_dir=run_dir / "landing",
        published="2026-10-07", updated="2026-10-07", tenant=TENANT, download_assets=True,
        fetch_url=lambda url: _make_image_bytes(600, 450), looks_dir=looks_dir)
    return run_dir, (run_dir / "landing" / "index.html").read_bytes()


def test_the_shipped_looks_render_the_same_with_and_without_the_resolver(library_on_disk, monkeypatch):
    tmp_path = library_on_disk
    cases = [lambda tag: _render_listicle(tmp_path, "face", tag),
             lambda tag: _render_listicle(tmp_path, "display", tag),
             lambda tag: _render_landing(tmp_path, "open", tag),
             lambda tag: _render_landing(tmp_path, "proof", tag, frame=False)]
    with_resolver = []
    for case in cases:
        run_dir, html = case("on")
        slots = json.loads((run_dir / ".image-selection.json").read_text())["slots"]
        assert len(slots) == 1 and next(iter(slots.values()))      # the resolver ran and recorded its picks
        with_resolver.append(hashlib.sha256(html).hexdigest())
    monkeypatch.setattr(images, "resolve_page_images", lambda *a, **k: ({}, []))
    without = [hashlib.sha256(case("off")[1]).hexdigest() for case in cases]
    assert with_resolver == without


def test_the_shipped_listicle_records_the_ad_still_and_its_bound_images(library_on_disk):
    run_dir, _html = _render_listicle(library_on_disk, "face", "rec")
    record = json.loads((run_dir / ".image-selection.json").read_text())["slots"]["listicle"]
    by_slot = {r["slot"]: r for r in record}
    assert by_slot["ad-still"]["picks"][0]["id"] == ad_frames.FRAME_ID
    assert by_slot["hero"]["picks"][0]["id"] == "asset-cutout-fuji"
    assert [p["path"] for p in by_slot["items"]["picks"]] == ["reasons[0].image", "reasons[1].image",
                                                             "reasons[2].image"]
    lines = images.review_lines(run_dir, ["listicle"])
    assert any(line.startswith("- listicle / ad-still: `asset-adframe` (want, score") for line in lines)


def test_a_tenant_look_declares_slots_and_gets_cropped_pictures(library_on_disk):
    tmp_path = library_on_disk
    looks = tmp_path / "looks"
    folder = looks / "landing" / "essay-test"
    folder.mkdir(parents=True)
    template = (REPO_ROOT / "cartridges" / "landing" / "looks" / "open" / "template.html").read_text()
    block = ('{% if image_slots.essay %}<section class="ld-essay">{% for img in image_slots.essay %}'
             '{{ render_image_slot(img, crop=img.slot_crop) }}{% endfor %}</section>{% endif %}\n'
             '  <section class="ld-close">')
    (folder / "template.html").write_text(template.replace('  <section class="ld-close">', block, 1))
    (folder / "library.json").write_text(json.dumps({"name": "essay-test", "image_slots": [
        {"name": "essay", "want": {"shot": ["detail-macro", "room"]}, "n": 2, "crop": "4:3", "above_fold": True},
    ]}))
    run_dir, html = _render_landing(tmp_path, "essay-test", "tenant", looks_dir=looks)
    html = html.decode()
    essay = re.search(r'<section class="ld-essay">(.*?)</section>', html, re.S).group(1)
    shown = re.findall(r'src="assets/(asset-photo-[0-9a-f]{12})-4x3-', essay)
    assert len(shown) == 2 and len(set(shown)) == 2 and "aspect-ratio:4 / 3" in essay
    assert fs79.PERSON["id"] not in shown                       # already the page's room photo
    assert fs79.PLUG["id"] not in shown and fs79.FRONT["id"] not in shown   # retired mark, above the fold
    record = json.loads((run_dir / ".image-selection.json").read_text())
    essay_rec = next(r for r in record["slots"]["landing"] if r["slot"] == "essay")
    assert [p["id"] for p in essay_rec["picks"]] == shown
    assert set(shown) <= set(record["landing"])                  # cross-page dedupe sees them
