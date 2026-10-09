"""Cycle 100: named-cause pages with 3, 4 or 5 items (owner decision
2026-10-09). The skeleton's own item_range (cartridges/listicle/skeletons/
10-named-cause.json) is the one source the listicle item gate reads; every
other skeleton keeps the cartridge's 5-7. item_plans give the 3- and 4-item
maps, `--items N` pins the count, and the canned pages in evals/fake_run.py
cover 3 and 4 offline (the imported looks are gitignored; skipped where they
are not on disk)."""
import argparse
import copy
import html
import json
import shutil

import pytest

from evals import fake_run
from harness import cli, headlines, listicle, repair, revise, skeletons
from harness.pipeline import CARTRIDGES_DIR
from tests.conftest import FakeClient
from tests.support import TENANT
from tests.test_named_cause_cycle98 import IMPORTED_LOOKS, STYLE_BY_TEMPLATE, TEMPLATE_BY_STYLE
from tests.test_skeletons import FIXTURE

SKELETON = "named-cause"


def _named_cause():
    return skeletons.load_skeleton(SKELETON)


def _page(n, style="reasons"):
    """The canned named-cause page cut or grown to n items (numbered by
    position), its headline's N set to n."""
    plan = headlines.build_plan(TEMPLATE_BY_STYLE[style], style, {"verified_claims": []}, tenant=TENANT)
    page = fake_run._named_cause_page(style, items=min(max(n, 3), 5))
    while len(page["reasons"]) < n:
        page["reasons"].append({**copy.deepcopy(page["reasons"][-1]), "number": len(page["reasons"]) + 1})
    del page["reasons"][n:]
    page["headline"] = headlines.example_headline(plan, n)
    return page, plan


def _item_keys(page, plan, item_range):
    return {p["key"] for p in listicle.find_listicle_violations(page, style=page["style"], headline=plan,
                                                                item_range=item_range)}


# ---------------------------------------------------------------------------
# the range: one source, the skeleton
# ---------------------------------------------------------------------------

def test_the_range_is_read_from_the_skeleton():
    sk = _named_cause()
    assert sk["item_range"] == [3, 5]
    assert skeletons.item_range(sk) == (3, 5)
    assert skeletons.item_range(skeletons.for_writer(sk, TENANT)) == (3, 5)   # the payload keeps it
    assert skeletons.item_range({**sk, "item_range": [4, 5]}) == (4, 5)
    for other in skeletons.load_all():
        if other["id"] != SKELETON:
            assert "item_range" not in other and skeletons.item_range(other) == listicle.ITEM_COUNT_RANGE == (5, 7)
            assert "item_range" not in skeletons.for_writer(other, TENANT)
    assert skeletons.item_range(None) == (5, 7)


@pytest.mark.parametrize("n,passes", [(2, False), (3, True), (4, True), (5, True), (6, False)])
def test_named_cause_takes_3_to_5_items(n, passes):
    page, plan = _page(n)
    keys = _item_keys(page, plan, skeletons.item_range(_named_cause()))
    assert ("listicle:item_count" not in keys) is passes, keys


@pytest.mark.parametrize("n,passes", [(4, False), (5, True), (7, True), (8, False)])
def test_every_other_skeleton_keeps_5_to_7(n, passes):
    page, plan = _page(n)
    for sid in ("classic-n-reasons", "hormozi-mistakes"):
        keys = _item_keys(page, plan, skeletons.item_range(skeletons.load_skeleton(sid)))
        assert ("listicle:item_count" not in keys) is passes, (sid, keys)
    assert ("listicle:item_count" not in _item_keys(page, plan, None)) is passes


def test_check_page_gates_reads_the_range_from_the_runs_skeleton():
    exit_code, run_dir, pages = fake_run.run_once(
        str(FIXTURE), tenant="peak-saunas", cartridges="listicle", skeleton=SKELETON, items=3)
    assert exit_code == 0
    page = json.loads(pages[0].read_text())
    facts_pack = json.loads((run_dir / "facts_pack.json").read_text())
    ad_brief = json.loads((run_dir / "ad_brief.json").read_text())
    _schema, word_range, ctas = repair.cartridge_write_constraints(
        "listicle", CARTRIDGES_DIR, facts_pack, ad_brief, TENANT)

    def keys(skeleton):
        return {p.get("key") for p in repair.check_page_gates(
            page, facts_pack, "listicle", financing_lender=TENANT.claims_config.get("financing_lender"),
            speaker_pov=ad_brief.get("speaker_pov"), word_range=word_range, allowed_cta_texts=ctas,
            ad_brief=ad_brief, tenant=TENANT, listicle_style=page["style"], listicle_skeleton=skeleton)}

    # the stored page is the rendered one (its hero image is swapped after the
    # gate), so only the item-count key is compared here
    assert "listicle:item_count" not in keys(_named_cause())
    assert "listicle:item_count" in keys(None)
    assert "listicle:item_count" in keys(skeletons.load_skeleton("classic-n-reasons"))
    assert "listicle:item_count" in keys({**_named_cause(), "item_range": [4, 5]})


# ---------------------------------------------------------------------------
# item plans and the writer
# ---------------------------------------------------------------------------

def test_the_item_plans_for_3_4_and_5():
    sk = _named_cause()
    assert skeletons.validate(sk) == []
    assert skeletons.item_counts(sk) == [3, 4, 5]
    roles = {n: [i["role"] for i in skeletons.items_for(sk, n)] for n in (3, 4, 5)}
    assert roles[3] == ["named_cause", "first_sign", "second_sign_past_fixes_failed"]
    assert roles[4] == ["named_cause", "first_sign", "second_sign", "past_fixes_failed"]
    assert roles[5] == ["named_cause", "first_sign", "second_sign", "third_sign", "past_fixes_failed"]
    assert skeletons.items_for(sk) == sk["items"]


def test_the_schema_rejects_counts_that_disagree():
    sk = _named_cause()
    bad = copy.deepcopy(sk)
    bad["item_count"] = 6
    bad["item_plans"]["4"] = bad["item_plans"]["4"][:3]
    bad["item_plans"]["7"] = bad["items"]
    problems = skeletons.validate(bad)
    assert any("item_count: 6 is outside item_range 3-5" in p for p in problems)
    assert any("$.items: 5 entries, item_count is 6" in p for p in problems)
    assert any("item_plans.4: 3 entries, not 4" in p for p in problems)
    assert any("item_plans.7: 7 is outside item_range 3-5" in p for p in problems)
    wrong_shape = copy.deepcopy(sk)
    wrong_shape["item_plans"]["3"][0]["image_role"] = "hero"
    assert any("item_plans.3[0].image_role" in p for p in skeletons.validate(wrong_shape))


@pytest.mark.parametrize("n", [3, 4, 5])
def test_the_writer_is_told_the_count(n):
    payload = skeletons.for_writer(_named_cause(), TENANT, items=n)
    assert payload["item_count"] == n and len(payload["items"]) == n
    assert "item_plans" not in payload
    text = " ".join(skeletons.writer_lines(payload))
    assert f"Write exactly {n} numbered items" in text and f"N is {n}" in text
    assert "allows 3 to 5 items, never more than 5" in text and "stay inside 3-5" in text
    assert "never pad" in text


def test_other_skeletons_writer_lines_are_unchanged():
    lines = skeletons.writer_lines(skeletons.for_writer(skeletons.load_skeleton("classic-n-reasons"), TENANT))
    assert lines[0].endswith("then drop that item and stay inside 5-7.")
    assert not any("Write exactly" in line for line in lines)


# ---------------------------------------------------------------------------
# --items
# ---------------------------------------------------------------------------

def test_items_is_a_run_flag():
    args = cli.build_parser().parse_args(
        ["run", "ad.mp4", "--cartridges", "listicle", "--skeleton", SKELETON, "--items", "3"])
    assert args.items == 3
    assert cli.build_parser().parse_args(["run", "ad.mp4"]).items is None


@pytest.mark.parametrize("skeleton,n,match", [
    (SKELETON, 2, "allows 3 to 5 items, not 2"),
    (SKELETON, 6, "allows 3 to 5 items, not 6"),
    ("classic-n-reasons", 6, r"has an item map for \[5\] items, not 6"),
    ("classic-n-reasons", 3, "allows 5 to 7 items, not 3"),
    (None, 3, "--items needs --skeleton"),
])
def test_items_outside_the_skeleton_is_refused_before_any_model_call(skeleton, n, match):
    client = FakeClient([])
    args = argparse.Namespace(
        input=str(FIXTURE), cartridges="listicle", seed=42, style=None, headline_template=None,
        skeleton=skeleton, items=n, product=None, ffmpeg_bin="/usr/bin/ffmpeg",
        whisper_bin="/nonexistent/whisper-cli", whisper_model="/nonexistent/model.bin",
        tenant="peak-saunas", batch=False,
    )
    with fake_run.fake_environment(client), pytest.raises(skeletons.SkeletonError, match=match):
        cli.cmd_run(args)
    assert client.messages.calls == []


def test_fake_run_refuses_items_out_of_range(capsys):
    argv = [str(FIXTURE), "--tenant", "peak-saunas", "--cartridges", "listicle", "--skeleton", SKELETON]
    assert fake_run.main(argv + ["--items", "6"]) != 0
    assert "allows 3 to 5 items, not 6" in capsys.readouterr().err
    with pytest.raises(skeletons.SkeletonError):
        fake_run.run_once(str(FIXTURE), tenant="peak-saunas", cartridges="listicle", skeleton=SKELETON, items=2)


# ---------------------------------------------------------------------------
# headlines
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("tid,n,expected", [
    ("nc1", 3, "3 Signs Your Sauna Search Is Hiding the Wiring Question"),
    ("nc1", 4, "4 Signs Your Sauna Search Is Hiding the Wiring Question"),
    ("nc2", 3, "3 Reasons Why a Sauna Sales Call Isn't Enough"),
    ("nc2", 4, "4 Reasons Why a Sauna Sales Call Isn't Enough"),
])
def test_the_headline_templates_take_3_and_4(tid, n, expected):
    plan = headlines.build_plan(tid, STYLE_BY_TEMPLATE[tid], {"verified_claims": []}, tenant=TENANT)
    assert headlines.example_headline(plan, n) == expected
    assert headlines.parse_headline(expected, plan)["n"] == str(n)
    page, _plan = _page(n, STYLE_BY_TEMPLATE[tid])
    assert headlines.find_headline_violations(page, plan) == []
    page["headline"] = headlines.example_headline(plan, n + 1)   # N must still be the item count
    assert "listicle:headline_formula" in {p["key"] for p in headlines.find_headline_violations(page, plan)}


def test_the_medical_and_fear_blocks_hold_at_3_items():
    plan = headlines.build_plan("nc1", "reasons", {"verified_claims": []}, tenant=TENANT)
    page, _plan = _page(3)
    page["headline"] = "3 Signs Your Cortisol Is Running the Show"
    page["reasons"][2]["heading"] = "Toxins in the gym air"
    keys = {p["key"] for p in headlines.find_headline_violations(page, plan)}
    assert keys & {"listicle:headline_medical", "listicle:headline_formula"}
    assert "listicle:item_fear:2" in keys


# ---------------------------------------------------------------------------
# offline runs
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("style", sorted(TEMPLATE_BY_STYLE))
@pytest.mark.parametrize("n", [3, 4, 5])
def test_an_offline_run_with_items_passes_every_gate_first_time(monkeypatch, style, n):
    gated, payloads = [], []
    real_gate, real_lines = repair.check_page_gates, skeletons.writer_lines

    def gate_spy(page, facts_pack, cartridge_name, **kw):
        problems = real_gate(page, facts_pack, cartridge_name, **kw)
        gated.append(problems)
        return problems

    def lines_spy(skeleton):
        payloads.append(skeleton)
        return real_lines(skeleton)

    monkeypatch.setattr(repair, "check_page_gates", gate_spy)
    monkeypatch.setattr(skeletons, "writer_lines", lines_spy)
    exit_code, run_dir, pages = fake_run.run_once(
        str(FIXTURE), tenant="peak-saunas", cartridges="listicle", skeleton=SKELETON, style=style, items=n)
    assert exit_code == 0
    assert gated == [[]]   # one write, no repair round
    assert payloads and all(p["item_count"] == n for p in payloads)
    page = json.loads(pages[0].read_text())
    assert len(page["reasons"]) == n and page["headline"].startswith(f"{n} ")
    assert page["headline_template_id"] == TEMPLATE_BY_STYLE[style]
    verified = {c["id"] for c in json.loads((run_dir / "facts_pack.json").read_text())["verified_claims"]}
    cited = {cid for item in page["reasons"] for cid in item.get("claim_ids", []) + item["proof"].get("claim_ids", [])}
    assert cited and cited <= verified


def test_a_revise_holds_the_page_to_the_runs_skeleton_range():
    page, _plan = _page(3)
    state = {"listicle": {"skeleton_id": SKELETON}}
    payload = revise._gate_skeleton(state, page, TENANT)
    assert skeletons.item_range(payload) == (3, 5) and payload["item_count"] == 3
    assert revise._gate_skeleton({"listicle": {"skeleton_id": "classic-n-reasons"}}, page, TENANT) is None
    assert revise._gate_skeleton({}, page, TENANT) is None


@pytest.mark.parametrize("slug", IMPORTED_LOOKS)
@pytest.mark.parametrize("n", [3, 4])
def test_each_imported_look_renders_3_and_4_items(slug, n):
    source = TENANT.root / "looks" / "listicle" / slug
    if not (source / "template.html").exists():
        pytest.skip(f"tenant look {slug} is not on this machine (gitignored; copied from prod)")
    shutil.copytree(source, TENANT.looks_dir / "listicle" / slug)
    exit_code, _run_dir, pages = fake_run.run_once(
        str(FIXTURE), tenant="peak-saunas", cartridges="listicle", skeleton=SKELETON, look=slug,
        hero_style="story", items=n)
    assert exit_code == 0
    page = json.loads(pages[0].read_text())
    assert page["look"] == slug and len(page["reasons"]) == n
    shown = html.unescape((pages[0].parent / "index.html").read_text())
    texts = [page["headline"]] + [r["heading"] for r in page["reasons"]] + [r["proof"]["text"] for r in page["reasons"]]
    assert [t for t in texts if t not in shown] == []
