"""Cycle 79 review fixes, from the smoke runs of 2026-10-05:
20261005-200242-...-65ji (Mini, face), 20261005-200346-...-c3qb (athlete,
story), 20261005-200447-...-62q7 (regret image, display).

1. pronoun from evidence only (c3qb said "In the ad, she says" for a man);
2. no invented backstory of the speaker (c3qb lede);
3. a short display line, else the heading face (62q7);
4. no "(PEAK product page, 2026)" source notes in the copy;
5. one ad quote at most once on the page (65ji item 4 + hero);
6. item photos: whole unit -> cut-out, part close-ups only on their part,
   no match -> no image (65ji);
7. eager loading for the hero and the first two item images;
8. value-stack lines are whole statements.
No model or network call.
"""
import copy
import json

import pytest

from harness import ad_frames, first_screen, listicle_quality, photo_library as pl
from harness import render as render_mod
from tests.support import REPO_ROOT, TENANT
from tests.test_first_screen_cycle79 import SPEAKER_BRIEF, _Log, _jpeg, _page, _run_dir
from tests.test_listicle import FACTS_PACK, RICH_FACTS_PACK, _make_image_bytes
from tests.test_photo_library import FakeTenant, _library, _photo, _tags

# The athlete ad (c3qb): a man, first person.
ATHLETE_BRIEF = {
    **SPEAKER_BRIEF,
    "source_file": "meta-120259093529150746.mp4",
    "transcript_or_text": ("As someone who exercises six days per week, I've learned that recovery is just as "
                           "important to me as the workout itself. That's why the sauna has been a huge part of "
                           "my recovery for the last few years."),
}
C3QB_LEDE = ("Recovery took almost as much planning as the workouts themselves. Between driving to a facility "
             "and hoping a sauna was free, the routine kept slipping.")


def _keys(problems):
    return {p["key"] for p in problems}


# ---------------------------------------------------------------------------
# 1. pronoun: evidence only
# ---------------------------------------------------------------------------

def test_the_vision_answer_gives_a_pronoun_only_when_unambiguous():
    base = {"frames": [{"index": 1, "face": True, "caption": "none", "logo": False, "sharp": True}],
            "best": 1, "face_center": [0.5, 0.3]}
    assert ad_frames.parse_frames_response(json.dumps(dict(base, pronoun="he")), 1)["pronoun"] == "he"
    assert ad_frames.parse_frames_response(json.dumps(dict(base, pronoun="unclear")), 1)["pronoun"] is None
    assert ad_frames.parse_frames_response(json.dumps(base), 1)["pronoun"] is None


def test_the_athlete_video_records_he_and_the_page_says_he(tmp_path):
    video = tmp_path / "meta-120259093529150746.mp4"
    video.write_bytes(b"x")
    from tests.test_first_screen_cycle79 import _fake_ffmpeg

    answer = {"frames": [{"index": i, "face": True, "caption": "large", "logo": False, "sharp": True}
                         for i in range(1, 7)], "best": 2, "face_center": [0.5, 0.3], "pronoun": "he",
              "reason": "x"}
    from tests.conftest import FakeClient, json_response
    from harness.budget import Budget

    run_dir = tmp_path / "20261005-200346-meta-120259093529150746-c3qb"
    rec = ad_frames.build_from_video(video, run_dir, ffmpeg_bin="ffmpeg", client=FakeClient([json_response(answer)]),
                                     budget=Budget(), log=_Log(),
                                     run=_fake_ffmpeg(tmp_path, [((120, 90, 60), True)] * 8))
    assert rec["usable"] and rec["speaker_pronoun"] == "he"
    brief = dict(ATHLETE_BRIEF, speaker_pronoun=first_screen.evidence_pronoun(None, rec) or "unknown")
    assert brief["speaker_pronoun"] == "he"
    page = _page("story")
    page.update(hero_quote_id="q1", lede="You plan every workout. Where does recovery fit in your week?")
    page["reasons"][0]["proof"] = {"text": 'In the ad, she says, "That\'s why the sauna has been a huge part of my '
                                           'recovery for the last few years."', "attributed_to_customer": True}
    assert "listicle:speaker_pronoun:$.reasons[0].proof.text" in _keys(
        first_screen.find_speaker_pronoun_violations(page, brief))
    page["reasons"][0]["proof"]["text"] = page["reasons"][0]["proof"]["text"].replace("she says", "he says")
    assert first_screen.find_speaker_pronoun_violations(page, brief) == []


def test_with_no_evidence_every_frame_is_neutral(tmp_path):
    brief = dict(ATHLETE_BRIEF, speaker_pronoun="unknown")
    page = _page("story")
    page["reasons"][0]["proof"] = {"text": 'In the ad, he says, "I\'ve learned that recovery is just as important '
                                           'to me as the workout itself."', "attributed_to_customer": True}
    assert _keys(first_screen.find_speaker_pronoun_violations(page, brief))
    page["reasons"][0]["proof"]["text"] = page["reasons"][0]["proof"]["text"].replace(
        "In the ad, he says,", first_screen.NEUTRAL_FRAME)
    assert first_screen.find_speaker_pronoun_violations(page, brief) == []
    # the neutral frame is an accepted quote frame for every other gate
    assert "listicle:meta_reference" not in " ".join(_keys(listicle_quality.find_meta_reference_violations(page)))


def test_the_renderer_never_guesses_the_pronoun(tmp_path):
    run_dir = _run_dir(tmp_path, with_frame=True)     # the still check gave no pronoun
    page = _page("face")
    page["speaker_pronoun"] = "she"                   # the writer's guess is not evidence
    html = render_mod.render_page(
        cartridge_name="listicle", page=page, ad_brief=ATHLETE_BRIEF, facts_pack=RICH_FACTS_PACK,
        cartridges_dir=REPO_ROOT / "cartridges", brand_dir=TENANT.brand_dir,
        templates_dir=REPO_ROOT / "harness" / "templates", out_dir=run_dir / "listicle",
        published="2026-10-05", updated="2026-10-05", tenant=TENANT, download_assets=True,
        fetch_url=lambda url: _make_image_bytes(600, 450),
    ).read_text()
    assert "From the creator&#39;s ad for PEAK" in html or "From the creator's ad for PEAK" in html
    assert "she made for" not in html and "she says" not in html


def test_a_tenant_override_names_the_pronoun_for_one_ad():
    class T:
        def get(self, key, default=None):
            return {"120259093529150746": "he"} if key == "first_screen.speaker_pronouns" else default

    assert first_screen.evidence_pronoun(ATHLETE_BRIEF, None, T()) == "he"
    assert first_screen.evidence_pronoun(SPEAKER_BRIEF, None, T()) is None


# ---------------------------------------------------------------------------
# 2. no invented backstory
# ---------------------------------------------------------------------------

def test_the_c3qb_lede_is_an_invented_backstory():
    page = _page("story")
    page["lede"] = C3QB_LEDE
    assert "listicle:speaker_narration:$.lede" in _keys(first_screen.find_speaker_narration_violations(page))


@pytest.mark.parametrize("field,text", [
    ("dek", "She trains six days a week and treats recovery like part of the work."),
    ("lede", "She looked at a bunch of different saunas before this one."),
    ("body", "He drove to the gym every night and hated the wait."),
])
def test_a_speaker_pronoun_outside_a_quote_is_narration(field, text):
    page = _page("story")
    if field == "body":
        page["reasons"][1]["text"] = text
    else:
        page[field] = text
    assert any(k.startswith("listicle:speaker_narration") for k in
               _keys(first_screen.find_speaker_narration_violations(page)))


def test_reader_loops_and_quoted_speech_pass():
    page = _page("story")
    page["lede"] = "You train most days. Where does recovery fit when the gym sauna is booked?"
    page["dek"] = "Recovery is half the work, and a home sauna changes where it happens."
    page["reasons"][0]["text"] = ('In the ad, he says, "That\'s why the sauna has been a huge part of my recovery '
                                  'for the last few years." The cabin is ready when you are.')
    assert first_screen.find_speaker_narration_violations(page) == []


# ---------------------------------------------------------------------------
# 3. display headline
# ---------------------------------------------------------------------------

def test_a_long_headline_never_gets_the_wide_face(tmp_path):
    page = _page("display")
    page["headline"] = "Still think logical research guarantees the right sauna choice?"
    page.pop("display_headline", None)
    assert first_screen.display_line(page) is None
    html = render_mod.render_page(
        cartridge_name="listicle", page=page, ad_brief=SPEAKER_BRIEF, facts_pack=RICH_FACTS_PACK,
        cartridges_dir=REPO_ROOT / "cartridges", brand_dir=TENANT.brand_dir,
        templates_dir=REPO_ROOT / "harness" / "templates", out_dir=tmp_path / "listicle",
        published="2026-10-05", updated="2026-10-05", tenant=TENANT, download_assets=False,
    ).read_text()
    assert '<h1 class="op-h1">' in html and "op-display--wide" not in html.split("<body>")[1].split("</style>")[-1]


def test_the_display_line_is_short_and_used_in_the_wide_face(tmp_path):
    page = _page("display")
    page["display_headline"] = "Train hard. Recover at home."
    page["accent_phrase"] = "Recover at home."
    assert first_screen.display_line(page) == "Train hard. Recover at home."
    html = render_mod.render_page(
        cartridge_name="listicle", page=page, ad_brief=SPEAKER_BRIEF, facts_pack=RICH_FACTS_PACK,
        cartridges_dir=REPO_ROOT / "cartridges", brand_dir=TENANT.brand_dir,
        templates_dir=REPO_ROOT / "harness" / "templates", out_dir=tmp_path / "listicle",
        published="2026-10-05", updated="2026-10-05", tenant=TENANT, download_assets=False,
    ).read_text()
    assert 'class="op-h1 op-display--wide">Train hard. <span class="op-accent">Recover at home.</span>' in html


@pytest.mark.parametrize("line,ok", [
    ("No electrician. No spare room.", True),
    ("Still think logical research guarantees the right sauna choice?", False),
    ("Five reasons to switch", True),
    ("5 reasons to switch", False),
])
def test_the_display_line_gate(line, ok):
    page = _page("display")
    page["display_headline"] = line
    keys = _keys(first_screen.find_first_screen_violations(page))
    assert ("listicle:first_screen:display_headline" not in keys) is ok


# ---------------------------------------------------------------------------
# 4. source notes
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("text", [
    "It runs on a standard 120V/15A household outlet (PEAK product page, 2026).",
    "Shipping is free on every order (PEAK shipping policy; PEAK warranty page).",
])
def test_source_notes_fail_the_gate_and_never_render(text, tmp_path):
    page = _page("story")
    page["reasons"][2]["proof"] = {"text": text, "claim_ids": ["shipping-policy"]}
    assert "listicle:source_parenthetical:$.reasons[2].proof.text" in _keys(
        first_screen.find_source_parenthetical_violations(page))
    stripped = first_screen.strip_source_parentheticals(page)["reasons"][2]["proof"]["text"]
    assert "(" not in stripped and stripped.endswith(".")
    html = render_mod.render_page(
        cartridge_name="listicle", page=page, ad_brief=SPEAKER_BRIEF, facts_pack=RICH_FACTS_PACK,
        cartridges_dir=REPO_ROOT / "cartridges", brand_dir=TENANT.brand_dir,
        templates_dir=REPO_ROOT / "harness" / "templates", out_dir=tmp_path / "listicle",
        published="2026-10-05", updated="2026-10-05", tenant=TENANT, download_assets=False,
    ).read_text()
    assert "PEAK product page, 2026" not in html and "PEAK shipping policy;" not in html


def test_a_plain_parenthesis_is_not_a_source_note():
    page = _page("story")
    page["reasons"][2]["text"] = "It fits a corner (about the size of a closet)."
    assert first_screen.find_source_parenthetical_violations(page) == []


# ---------------------------------------------------------------------------
# 5. one ad quote, once
# ---------------------------------------------------------------------------

def test_the_65ji_repeat_fails():
    brief = {**SPEAKER_BRIEF, "transcript_or_text": (
        "I just ordered the Peak Sauna Mini and I could not be more excited. I've been looking at a bunch of "
        "different saunas and this one really stuck out to me because it's small footprint so it's easy to fit "
        "into my apartment.")}
    quotes = first_screen.quote_candidates(brief, FACTS_PACK)
    q2 = next(q for q in quotes if q["text"].startswith("I've been looking"))
    page = _page("story")
    page["hero_quote_id"] = "q1"
    page["reasons"][3]["text"] = f'In the ad, the creator says, "{q2["text"]}"'
    page["reasons"][3]["proof"] = {"text": f'In the ad, the creator says, "{q2["text"]}"',
                                   "attributed_to_customer": True}
    assert f"listicle:quote_repeat:{q2['id']}" in _keys(first_screen.find_quote_repeat_violations(page, brief, FACTS_PACK))
    page["reasons"][3]["proof"] = {"text": "It is 31.1 inches wide.", "claim_ids": ["spec-x"]}
    assert first_screen.find_quote_repeat_violations(page, brief, FACTS_PACK) == []
    # the hero quote counts: quoting q1 in an item is a second use
    q1 = next(q for q in quotes if q["id"] == "q1")
    page["reasons"][0]["text"] = f'In the ad, the creator says, "{q1["text"]}"'
    assert "listicle:quote_repeat:q1" in _keys(first_screen.find_quote_repeat_violations(page, brief, FACTS_PACK))
    # an older page with no hero_quote_id gets a quote its body does not use
    page.pop("hero_quote_id")
    assert first_screen.hero_quote(page, brief, FACTS_PACK)["id"] not in {"q1", q2["id"]} or len(quotes) == 2


# ---------------------------------------------------------------------------
# 6. item photos (the 65ji items)
# ---------------------------------------------------------------------------

MINI_POOL = [
    _photo(0xd279, _tags(product="fuji", shot="interior", setting="studio", old_logo_visible=True,
                         features=["bench", "control-panel", "heaters", "red-light-panel", "wood-grain"])),
    _photo(0x5d15, _tags(product="unknown", shot="detail", agnostic=True, old_logo_visible=True,
                         features=["control-panel", "glass-door", "red-light-panel", "wood-grain"])),
    _photo(0xfcac, _tags(product="unknown", shot="people-in-use", agnostic=True, people=1)),
    _photo(0xa4d7, _tags(product="unknown", shot="detail", agnostic=True, features=["speakers", "wood-grain"])),
    _photo(0x84d9, _tags(product="unknown", shot="detail", agnostic=True, old_logo_visible=True,
                         features=["chromotherapy"])),
    _photo(0x0d82, _tags(product="unknown", shot="detail", agnostic=True, old_logo_visible=True,
                         features=["outlet-plug", "red-light-panel", "wood-grain"])),
    _photo(0x6208, _tags(product="unknown", shot="detail", agnostic=True, old_logo_visible=True,
                         features=["control-panel", "wood-grain"])),
    _photo(0xff0e, _tags(product="unknown", shot="detail", setting="studio", agnostic=True,
                         features=["red-light-panel", "wood-grain"])),
]
ID = {p["id"][-4:]: p["id"] for p in MINI_POOL}
HEADINGS_65JI = [
    ("A full cabin built for one", "The core of the offer is a complete infrared cabin, not a stripped-down "
                                   "starter unit, in a footprint sized for one."),
    ("Red light that's already in the box", "Some sauna brands sell red light as an upgrade."),
    ("Ready the day it's plugged in", "No electrician, no rewiring. Plug it into the wall you already have."),
    ("A footprint sized for a bedroom corner", "It fits a small apartment."),
    ("Control and sound built in, not bolted on", "Adjust the session from your phone and play music."),
    ("Delivery and coverage that follow it home", "Getting it to your door and standing behind it afterward."),
]
CUTOUT = "asset-cutout-fuji"


@pytest.fixture
def mini_tenant(tmp_path, monkeypatch):
    monkeypatch.setenv(pl.FILES_DIR_ENV, str(tmp_path / "files"))
    t = FakeTenant(tmp_path / "acme")
    t.config["photo_library"] = copy.deepcopy(TENANT.get("photo_library"))
    t.config["photo_library"]["features"] = list(pl.DEFAULT_FEATURES)
    _library(t, MINI_POOL)
    return t


def _assign(tenant):
    from tests.test_photo_library import FUJI_HANDLE

    page = {"headline": "x", "dek": "", "hero": {"asset_id": CUTOUT},
            "reasons": [{"heading": h, "text": t, "image": {"asset_id": f"asset-{FUJI_HANDLE}-1"}}
                        for h, t in HEADINGS_65JI]}
    facts = {"product": {"name": "Fuji", "slug": FUJI_HANDLE},
             "assets": [pl.facts_pack_asset(p) for p in MINI_POOL]
             + [{"id": f"asset-{FUJI_HANDLE}-{i}", "url": f"u{i}", "kind": "image"} for i in (1, 2)]}
    result = pl.assign_page_images(page, facts, "listicle", tenant=tenant, allow_ai_renders=True,
                                   keep_hero=True, cutout_id=CUTOUT)
    return [r["image"]["asset_id"] for r in page["reasons"]], result


def test_whole_unit_items_show_the_cutout(mini_tenant):
    got, _ = _assign(mini_tenant)
    assert got[0] == CUTOUT          # "A full cabin built for one"
    assert got[3] == CUTOUT          # "A footprint sized for a bedroom corner"
    assert got[3] != ID["fcac"]      # never the man with the towel


def test_part_items_get_their_part_and_nothing_else(mini_tenant):
    got, _ = _assign(mini_tenant)
    assert got[1] == ID["ff0e"]      # red light -> the red light panel close-up
    assert got[2] == ID["0d82"]      # plugged in -> the cord, not the heater panel
    assert got[4] in (ID["a4d7"], ID["6208"], ID["5d15"])   # control and sound -> a control/speaker photo


def test_an_item_no_photo_matches_renders_without_an_image(mini_tenant):
    got, result = _assign(mini_tenant)
    assert got[5] is None            # delivery and coverage: no photo shows it
    assert any("rendered without an image" in n for n in result["notes"])


def test_the_cutout_is_used_at_most_three_times(mini_tenant):
    got, _ = _assign(mini_tenant)
    assert got.count(CUTOUT) + 1 <= pl.CUTOUT_MAX_USES


def test_the_duplicate_gate_allows_the_cutout_twice():
    from harness import pagechecks

    page = {"hero": {"asset_id": CUTOUT}, "reasons": [{"image": {"asset_id": CUTOUT}}]}
    assert pagechecks.find_duplicate_asset_violations(page, "listicle") == []


def test_an_item_without_an_image_renders(tmp_path):
    page = _page("story")
    page["reasons"][5 if len(page["reasons"]) > 5 else 4]["image"] = {"asset_id": None}
    html = render_mod.render_page(
        cartridge_name="listicle", page=page, ad_brief=SPEAKER_BRIEF, facts_pack=RICH_FACTS_PACK,
        cartridges_dir=REPO_ROOT / "cartridges", brand_dir=TENANT.brand_dir,
        templates_dir=REPO_ROOT / "harness" / "templates", out_dir=tmp_path / "listicle",
        published="2026-10-05", updated="2026-10-05", tenant=TENANT, download_assets=False,
    ).read_text()
    assert html.count('class="op-item"') == len(page["reasons"])


# ---------------------------------------------------------------------------
# 7. eager loading, 8. value stack
# ---------------------------------------------------------------------------

def test_the_hero_and_the_first_two_item_images_load_eagerly(tmp_path):
    run_dir = _run_dir(tmp_path)
    page = _page("story")
    html = render_mod.render_page(
        cartridge_name="listicle", page=page, ad_brief=SPEAKER_BRIEF, facts_pack=RICH_FACTS_PACK,
        cartridges_dir=REPO_ROOT / "cartridges", brand_dir=TENANT.brand_dir,
        templates_dir=REPO_ROOT / "harness" / "templates", out_dir=run_dir / "listicle",
        published="2026-10-05", updated="2026-10-05", tenant=TENANT, download_assets=True,
        fetch_url=lambda url: _make_image_bytes(600, 450),
    ).read_text()
    items = html.split('class="op-item"')[1:]
    assert 'loading="eager"' in items[0] and 'loading="eager"' in items[1]
    assert 'loading="lazy"' in items[2]


def test_value_stack_lines_are_whole_statements():
    facts = copy.deepcopy(FACTS_PACK)
    facts["product"]["name"] = "Mini"
    facts["verified_claims"] += [
        {"id": "pdp-mini-red-light", "text": "Medical-grade red light, where it works.", "category": "spec"},
        {"id": "pdp-mini-assembly", "text": "Clasp-together assembly.", "category": "spec"},
        {"id": "pdp-mini-crate-shipping", "text": "Free delivery, shipped in a custom protective crate.",
         "category": "spec"},
    ]
    texts = [i["text"] for i in first_screen.value_stack(facts, _page("story"), TENANT)["items"]]
    assert texts == ["Free delivery, shipped in a custom protective crate"]


def test_the_writer_never_sees_a_copyable_open_loop_example():
    """Smoke runs copied slot examples verbatim into unrelated ads."""
    from harness import headlines

    seen = " ".join(first_screen.writer_lines())
    for tid in ("o1", "o2", "o3", "o4"):
        plan = headlines.build_plan(tid, "reasons", FACTS_PACK, TENANT)
        seen += " ".join(headlines.writer_lines(plan))
    for phrase in ("spare room", "electrician", "six days a week", "real sauna", "could not be more excited",
                   "Recover at home"):
        assert phrase.lower() not in seen.lower(), phrase
