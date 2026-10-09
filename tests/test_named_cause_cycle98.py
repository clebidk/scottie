"""Cycle 98: the named-cause listicle skeleton (cartridges/listicle/skeletons/
10-named-cause.json), its two headline templates (nc1 "N Signs <cause> Is
<harm>", nc2 "N Reasons Why <common_solution> Isn't Enough"), the canned
named-cause page in evals/fake_run.py, and the four imported tenant looks
(gitignored; skipped where they are not on disk) rendering it offline."""
import copy
import html
import json
import shutil

import pytest

from evals import fake_run
from harness import cli, headlines, repair, runstate, skeletons
from tests.support import TENANT
from tests.test_skeletons import FIXTURE

SKELETON = "named-cause"
IMPORTED_LOOKS = ("nourial-ten-reasons", "jevi-five-signs", "swarva-doctor-byline", "citrusroot-named-cause")
TEMPLATE_BY_STYLE = {"reasons": "nc1", "mistakes": "nc2"}
STYLE_BY_TEMPLATE = {v: k for k, v in TEMPLATE_BY_STYLE.items()}


# ---------------------------------------------------------------------------
# the skeleton and its flag
# ---------------------------------------------------------------------------

def test_named_cause_is_a_skeleton_choice():
    assert SKELETON in skeletons.skeleton_ids()
    args = cli.build_parser().parse_args(["run", "ad.mp4", "--cartridges", "listicle", "--skeleton", SKELETON])
    assert args.skeleton == SKELETON
    assert skeletons.style_for(SKELETON) == "reasons"
    assert skeletons.style_for(SKELETON, "mistakes") == "mistakes"
    with pytest.raises(skeletons.SkeletonError):
        skeletons.style_for(SKELETON, "myths")


def test_the_item_map_is_cause_signs_then_past_fixes():
    sk = skeletons.load_skeleton(SKELETON)
    assert skeletons.validate(sk) == []
    assert [i["role"] for i in sk["items"]] == [
        "named_cause", "first_sign", "second_sign", "third_sign", "past_fixes_failed"]
    assert sk["headline_templates"] == ["nc1", "nc2"]
    assert any("health term" in line for line in sk["compliance"])
    payload = skeletons.for_writer(sk, TENANT)
    hints = " ".join(payload["tenant_item_hints"])
    # PEAK's causes are ones its verified claims carry (the electrical and the
    # 360-degree heater claims), never a body outcome
    assert "electrical claim" in hints and "360-degree" in hints


# ---------------------------------------------------------------------------
# the headline templates
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("tid,n,values,expected", [
    ("nc1", 5, None, "5 Signs Your Sauna Search Is Hiding the Wiring Question"),
    ("nc1", 3, {"cause": "a Gym Sauna Habit", "harm": "Costing You Sessions"},
     "3 Signs a Gym Sauna Habit Is Costing You Sessions"),
    ("nc2", 5, None, "5 Reasons Why a Sauna Sales Call Isn't Enough"),
    ("nc2", 7, {"common_solution": "a Gym Sauna Pass"}, "7 Reasons Why a Gym Sauna Pass Isn't Enough"),
])
def test_the_headline_templates_produce_the_expected_forms(tid, n, values, expected):
    plan = headlines.build_plan(tid, STYLE_BY_TEMPLATE[tid], {"verified_claims": []}, tenant=TENANT)
    text = headlines.example_headline(plan, n) if values is None else headlines.render(plan, n, values)
    assert text == expected
    parsed = headlines.parse_headline(text, plan)
    assert parsed is not None and parsed["n"] == str(n)


def test_the_aliases_resolve():
    assert headlines.canonical_id("signs-cause-is-harm") == "nc1"
    assert headlines.canonical_id("reasons-not-enough") == "nc2"


@pytest.mark.parametrize("headline", [
    "5 Signs Your Cortisol Is Running the Show",
    "5 Signs Your Hormones Are Working Against You",
    "5 Signs Your Office Chair Is Causing Back Pain",
])
def test_a_body_chemistry_or_health_cause_is_refused(headline):
    plan = headlines.build_plan("nc1", "reasons", {"verified_claims": []}, tenant=TENANT)
    page = fake_run._named_cause_page("reasons", headline)
    keys = {p["key"] for p in headlines.find_headline_violations(page, plan)}
    assert keys & {"listicle:headline_medical", "listicle:headline_formula"}, keys


def test_the_items_refuse_health_and_fear_wording():
    plan = headlines.build_plan("nc2", "mistakes", {"verified_claims": []}, tenant=TENANT)
    page = fake_run._named_cause_page("mistakes", headlines.example_headline(plan, 5))
    assert [p for p in headlines.find_headline_violations(page, plan) if "item_" in p["key"]] == []
    bad = copy.deepcopy(page)
    bad["reasons"][1]["text"] += " It is why your stress hormones stay high."
    bad["reasons"][2]["heading"] = "Toxins in the gym air"
    keys = {p["key"] for p in headlines.find_headline_violations(bad, plan)}
    assert "listicle:item_medical:1" in keys and "listicle:item_fear:2" in keys


def test_the_writer_is_told_what_a_cause_may_be():
    plan = headlines.build_plan("nc1", "reasons", {"verified_claims": []}, tenant=TENANT)
    lines = " ".join(headlines.writer_lines(plan))
    assert "<cause> names the ONE cause" in lines and "never a body part" in lines
    assert "Never write EMF" in lines and "Items never make a health claim" in lines


# ---------------------------------------------------------------------------
# offline runs
# ---------------------------------------------------------------------------

def _run(monkeypatch, **kwargs):
    gated = []
    real_gate = repair.check_page_gates

    def spy(page, facts_pack, cartridge_name, **kw):
        problems = real_gate(page, facts_pack, cartridge_name, **kw)
        gated.append(problems)
        return problems

    monkeypatch.setattr(repair, "check_page_gates", spy)
    exit_code, run_dir, pages = fake_run.run_once(
        str(FIXTURE), tenant="peak-saunas", cartridges="listicle", skeleton=SKELETON, **kwargs)
    return exit_code, run_dir, pages, gated


@pytest.mark.parametrize("style", sorted(TEMPLATE_BY_STYLE))
def test_an_offline_run_passes_the_claims_and_page_gates(monkeypatch, style):
    exit_code, run_dir, pages, gated = _run(monkeypatch, style=style)
    assert exit_code == 0
    assert gated == [[]]   # one write, no repair round
    page = json.loads(pages[0].read_text())
    assert page["style"] == style
    assert page["headline_template_id"] == TEMPLATE_BY_STYLE[style]
    assert runstate.load_state(run_dir)["listicle"]["skeleton_id"] == SKELETON
    verified = {c["id"] for c in json.loads((run_dir / "facts_pack.json").read_text())["verified_claims"]}
    cited = {cid for item in page["reasons"] for cid in item.get("claim_ids", []) + item["proof"].get("claim_ids", [])}
    assert cited and cited <= verified
    assert page["reasons"][0]["proof"]["attributed_to_customer"] is True   # the cause in the ad's own words


@pytest.mark.parametrize("slug", IMPORTED_LOOKS)
def test_each_imported_look_renders_the_named_cause_page(slug):
    source = TENANT.root / "looks" / "listicle" / slug
    if not (source / "template.html").exists():
        pytest.skip(f"tenant look {slug} is not on this machine (gitignored; copied from prod)")
    # conftest points looks_dir at the test's own folder; the real look goes there
    shutil.copytree(source, TENANT.looks_dir / "listicle" / slug)
    exit_code, _run_dir, pages = fake_run.run_once(
        str(FIXTURE), tenant="peak-saunas", cartridges="listicle", skeleton=SKELETON, look=slug, hero_style="story")
    assert exit_code == 0
    page = json.loads(pages[0].read_text())
    assert page["look"] == slug
    out = (pages[0].parent / "index.html").read_text()
    assert "{{" not in out and "{%" not in out
    texts = [page["headline"]] + [r["heading"] for r in page["reasons"]] + [r["proof"]["text"] for r in page["reasons"]]
    texts += [q["question"] for q in page["faq"]["questions"]] + [b["text"] for b in page["closing"]["recap"]]
    shown = html.unescape(out)
    missing = [t for t in texts if t not in shown]
    assert missing == []
