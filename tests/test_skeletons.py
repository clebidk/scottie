"""Cycle 73: the listicle winner-skeleton library (merged from
cursor/listicle-skeletons-fdad and reworked for master) -- harness/skeletons.py,
cartridges/listicle/skeletons/, the tenant hints, the headline aliases and the
--skeleton / --headline flags."""
import argparse
import copy
import json
import re

import pytest

from evals import fake_run
from harness import cli, headlines, listicle, repair, runstate, skeletons, vocab
from harness.budget import Budget
from harness.claims import ClaimsGateFailure
from harness.log import RunLog
from harness.repair import MAX_REPAIR_ATTEMPTS, write_and_gate_page
from harness.write import write_page
from tests.conftest import FakeClient, block_text, json_response
from tests.support import REPO_ROOT, TENANT
from tests.test_headlines_cycle70 import EVIDENCE_CLAIMS, NEW_IDS, _facts, _page_for, _plan
from tests.test_listicle import AD_BRIEF, FACTS_PACK, _gate, _listicle_page

SKELETONS_DIR = REPO_ROOT / "cartridges" / "listicle" / "skeletons"
FIXTURE = TENANT.fixtures_dir / "founder-warranty-demo.txt"

BRANCH_HEADLINE_IDS = {
    "h01": "most-dont-work", "h02": "everyones-switching", "h03": "every-avatar-needs",
    "h04": "must-have-for-problem", "h05": "catching-on", "h06": "avatar-started-switching",
    "h07": "ways-helps-solve", "h08": "people-over-age", "h09": "social-proof-switched",
    "h10": "swore-they-couldnt", "h11": "concerning-in-common", "h12": "only-built-for-niche",
    "h13": "removes-without-concern", "h14": "better-fit-than", "h15": "authority-loves",
    "h16": "big-improvement-for-audience", "h17": "still-problem-after-trying",
}
# The branch ids of the three templates rewritten for the guardrails no longer
# name a template (their wording is gone).
RETIRED_BRANCH_IDS = ("going-viral", "breakthrough-crushes", "game-changer-for-avatar")


# ---------------------------------------------------------------------------
# library and schema
# ---------------------------------------------------------------------------

def test_index_lists_nine_skeletons_with_a_default_for_every_style():
    index = json.loads((SKELETONS_DIR / "index.json").read_text())
    ids = [row["id"] for row in index["skeletons"]]
    assert ids == skeletons.skeleton_ids()
    assert len(ids) == len(set(ids)) == 9
    for row in index["skeletons"]:
        assert (SKELETONS_DIR / row["file"]).exists()
    assert set(index["default_by_style"]) == set(listicle.STYLES)
    for style, sid in index["default_by_style"].items():
        assert style in skeletons.load_skeleton(sid)["styles"]


def test_every_file_in_the_folder_is_in_the_index():
    on_disk = sorted(p.name for p in SKELETONS_DIR.glob("[0-9][0-9]-*.json"))
    indexed = sorted(row["file"] for row in json.loads((SKELETONS_DIR / "index.json").read_text())["skeletons"])
    assert on_disk == indexed


@pytest.mark.parametrize("skeleton_id", skeletons.skeleton_ids())
def test_every_skeleton_validates_against_the_schema(skeleton_id):
    sk = skeletons.load_skeleton(skeleton_id)
    assert skeletons.validate(sk) == []
    assert sk["item_count"] == len(sk["items"])
    assert sk["look"] in listicle.LOOKS
    assert set(sk["styles"]) <= set(listicle.STYLES)


def test_the_schema_check_rejects_a_broken_skeleton():
    sk = skeletons.load_skeleton("classic-n-reasons")
    broken = copy.deepcopy(sk)
    del broken["items"]
    broken["styles"] = ["listicles"]
    broken["peak_saunas"] = {}
    broken["item_count"] = 9
    problems = skeletons.validate(broken)
    assert any("missing 'items'" in p for p in problems)
    assert any("'listicles' is not one of" in p for p in problems)
    assert any("unknown key 'peak_saunas'" in p for p in problems)
    assert any("9 > 7" in p for p in problems)


@pytest.mark.parametrize("skeleton_id", skeletons.skeleton_ids())
def test_every_skeleton_pairs_with_real_headline_templates_of_its_styles(skeleton_id):
    sk = skeletons.load_skeleton(skeleton_id)
    for tid in sk["headline_templates"]:
        assert set(headlines.template(tid)["styles"]) & set(sk["styles"]), (skeleton_id, tid)


def _guardrail_res():
    words = list(vocab.EMF_TERMS) + list(vocab.HYPE_WORDS)
    lists = headlines.load_library()["word_lists"]
    res = [re.compile(r"\b" + re.escape(w) + r"\b", re.IGNORECASE) for w in words]
    res += [re.compile(r"\b(?:" + p + r")\b", re.IGNORECASE) for p in lists["fear"] + lists["medical"]]
    return res


def _strings(node):
    if isinstance(node, str):
        yield node
    elif isinstance(node, dict):
        for v in node.values():
            yield from _strings(v)
    elif isinstance(node, list):
        for v in node:
            yield from _strings(v)


def test_skeletons_and_tenant_hints_carry_no_banned_wording():
    """No EMF, fear, medical or hype word and no exclamation mark in any text
    the writer is sent (skeleton files and the tenant's hints)."""
    res = _guardrail_res()
    texts = [s for sid in skeletons.skeleton_ids() for s in _strings(skeletons.load_skeleton(sid))]
    texts += list(_strings(skeletons.tenant_hints(TENANT)))
    offenders = [(t, r.pattern) for t in texts for r in res if r.search(t)]
    offenders += [(t, "!") for t in texts if "!" in t]
    assert offenders == []


def test_no_stale_branch_text_in_the_skeletons():
    text = "\n".join(p.read_text() for p in SKELETONS_DIR.rglob("*") if p.is_file())
    for stale in ("Advertisement", "ad_label", "told us", "a customer", "600", "words_per_item",
                  "peak_saunas"):
        assert stale not in text, stale


def test_tenant_hints_name_only_real_skeletons():
    hints = skeletons.tenant_hints(TENANT)
    assert hints, "PEAK ships fill hints"
    assert set(hints) <= set(skeletons.skeleton_ids())
    for entry in hints.values():
        assert set(entry) <= {"angle_fit", "item_hints"}


def test_the_headline_swipe_image_is_out_of_the_cartridge():
    assert not (SKELETONS_DIR / "headlines").exists()
    assert (REPO_ROOT / "docs" / "reference" / "listicle-headline-swipe-17.jpg").exists()


# ---------------------------------------------------------------------------
# selection
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("style", listicle.STYLES)
def test_auto_pick_returns_a_skeleton_for_every_style(style):
    sk = skeletons.select(style, {}, tenant=TENANT)
    assert style in sk["styles"]
    assert sk["id"] == skeletons.default_for_style(style)
    sk = skeletons.select(style, AD_BRIEF, tenant=TENANT)
    assert style in sk["styles"]


def test_auto_pick_follows_the_ad_angle():
    hidden = {"angle": "hidden costs of a gym membership", "hook": "the fees add up"}
    assert skeletons.select("mistakes", hidden, tenant=TENANT)["id"] == "hidden-costs"
    switch = {"angle": "why owners are switching", "hook": "switch from the spa"}
    assert skeletons.select("reasons", switch, tenant=TENANT)["id"] == "switcher-reasons"
    assert skeletons.select("reasons", {"angle": "nothing that matches xyz"}, tenant=TENANT)["id"] == "classic-n-reasons"


def test_tenant_angle_fit_counts_in_the_auto_pick():
    # "busy parents" is only in PEAK's own hints, for day-in-the-life
    brief = {"angle": "busy parents"}
    assert skeletons.select("reasons", brief, tenant=TENANT)["id"] == "day-in-the-life"


def test_explicit_skeleton_wins_and_must_fit_the_style():
    assert skeletons.select("reasons", {"angle": "hidden costs"}, requested="hormozi-value-stack")["id"] == "hormozi-value-stack"
    with pytest.raises(skeletons.SkeletonError, match="fits"):
        skeletons.select("questions", {}, requested="hormozi-value-stack")
    with pytest.raises(skeletons.SkeletonError, match="unknown skeleton"):
        skeletons.select("reasons", {}, requested="simplified-pdp")


def test_style_for_takes_the_skeletons_first_style_unless_one_is_given():
    assert skeletons.style_for("myth-bust") == "myths"
    assert skeletons.style_for("myth-bust", "tested") == "tested"
    with pytest.raises(skeletons.SkeletonError):
        skeletons.style_for("myth-bust", "reasons")


def test_writer_payload_is_compact_and_carries_the_tenant_hints():
    payload = skeletons.for_writer(skeletons.load_skeleton("classic-n-reasons"), TENANT)
    assert payload["id"] == "classic-n-reasons"
    assert len(payload["items"]) == payload["item_count"]
    assert payload["tenant_item_hints"]
    for key in ("sources", "tags", "look", "headline_templates", "styles"):
        assert key not in payload


# ---------------------------------------------------------------------------
# headlines: one library, aliases, one flag
# ---------------------------------------------------------------------------

def test_the_branch_swipe_ids_are_aliases_of_h01_to_h17():
    for tid, alias in BRANCH_HEADLINE_IDS.items():
        assert headlines.template(alias) is headlines.template(tid)
        assert headlines.canonical_id(alias) == tid
    assert sorted(BRANCH_HEADLINE_IDS) == NEW_IDS
    for retired in RETIRED_BRANCH_IDS:
        with pytest.raises(headlines.HeadlineTemplateError):
            headlines.template(retired)


def test_an_alias_cannot_shadow_an_id():
    data = headlines.load_library(raw=True)
    data["templates"][5]["alias"] = "h04"
    with pytest.raises(headlines.HeadlineLibraryError, match="already a template id"):
        headlines.validate_library(data)


def test_headline_and_headline_template_flags_resolve_to_the_same_template():
    parser = cli.build_parser()
    a = parser.parse_args(["run", "ad.mp4", "--headline", "must-have-for-problem"])
    b = parser.parse_args(["run", "ad.mp4", "--headline-template", "h04"])
    assert headlines.template(a.headline_template) is headlines.template(b.headline_template)
    fp = _facts(*EVIDENCE_CLAIMS)
    plan_a = headlines.resolve_plan("reasons", fp, TENANT, requested=a.headline_template, today="2026-10-05")
    plan_b = headlines.resolve_plan("reasons", fp, TENANT, requested=b.headline_template, today="2026-10-05")
    assert plan_a == plan_b
    assert plan_a["id"] == "h04"


def test_skeleton_flag_lists_the_library():
    args = cli.build_parser().parse_args(["run", "ad.mp4", "--skeleton", "myth-bust"])
    assert args.skeleton == "myth-bust"
    with pytest.raises(SystemExit):
        cli.build_parser().parse_args(["run", "ad.mp4", "--skeleton", "simplified-pdp"])


@pytest.mark.parametrize("template_id", headlines.template_ids())
def test_every_headline_template_renders_for_the_peak_tenant(template_id):
    plan = _plan(template_id)
    text = headlines.example_headline(plan, 5)
    assert not re.search(r"[<>\[\]{}]", text), text
    assert re.search(r"\b5\b", text)
    page = _page_for(plan)
    problems = [p for p in _gate(page, listicle_headline=plan) if "headline" in p.get("key", "")]
    assert problems == [], problems


def test_a_pinned_skeletons_headlines_narrow_the_seeded_pick():
    fp = _facts(*EVIDENCE_CLAIMS)
    prefer = skeletons.load_skeleton("day-in-the-life")["headline_templates"]
    eligible = set(headlines.eligible_templates("reasons", fp, TENANT))
    for seed in range(20):
        plan = headlines.resolve_plan("reasons", fp, TENANT, seed=seed, today="2026-10-05", prefer=prefer)
        assert plan["id"] in set(prefer) & eligible
    # nothing eligible in the preference: the pick is unchanged
    plain = headlines.resolve_plan("reasons", fp, TENANT, seed=3, today="2026-10-05")
    assert headlines.resolve_plan("reasons", fp, TENANT, seed=3, today="2026-10-05", prefer=["s-myths"]) == plain


# ---------------------------------------------------------------------------
# the writer and the gates
# ---------------------------------------------------------------------------

def _skeleton(sid="hormozi-mistakes"):
    return skeletons.for_writer(skeletons.load_skeleton(sid), TENANT)


def test_write_page_sends_the_skeleton_and_keeps_it_on_repair(tmp_path):
    page = _listicle_page("mistakes")
    client = FakeClient([json_response(page), json_response(page)])
    log = RunLog("test-skel", tmp_path / "run.log")
    kwargs = dict(cartridge_name="listicle", cartridges_dir=REPO_ROOT / "cartridges", ad_brief=AD_BRIEF,
                  facts_pack=FACTS_PACK, client=client, model="fake", budget=Budget(), log=log,
                  tenant=TENANT, listicle_style="mistakes", listicle_skeleton=_skeleton())
    try:
        write_page(**kwargs)
        write_page(revision_note="REVISION REQUIRED: fix something", **kwargs)
    finally:
        log.close()
    first, repair_call = client.messages.calls
    for call in (first, repair_call):
        user = block_text(call["messages"][0]["content"])
        assert '"skeleton"' in user and '"hormozi-mistakes"' in user
        assert "top_objection" in user
        # Cycle 76: a draft's own lines follow the last cache breakpoint.
        assert 'adapts the winner skeleton "Mistakes that point to the fix"' in user
        assert "Mistakes that point to the fix" not in block_text(call["system"])
    assert '"exemplars"' not in block_text(repair_call["messages"][0]["content"])


def test_a_non_listicle_page_never_gets_the_skeleton(tmp_path):
    from tests.test_render import AD_BRIEF as ARTICLE_BRIEF, ARTICLE_PAGE, FACTS_PACK as ARTICLE_FACTS

    client = FakeClient([json_response(ARTICLE_PAGE)])
    log = RunLog("test-skel", tmp_path / "run.log")
    try:
        write_page(cartridge_name="article", cartridges_dir=REPO_ROOT / "cartridges", ad_brief=ARTICLE_BRIEF,
                   facts_pack=ARTICLE_FACTS, client=client, model="fake", budget=Budget(), log=log,
                   tenant=TENANT, listicle_skeleton=_skeleton())
    finally:
        log.close()
    call = client.messages.calls[0]
    assert '"skeleton"' not in block_text(call["messages"][0]["content"])
    assert "winner skeleton" not in block_text(call["system"])


def test_a_skeleton_never_bypasses_the_gates(tmp_path):
    """A page that breaks a gate fails every attempt with a skeleton exactly
    as it would without one -- here the absolute EMF ban."""
    bad = _listicle_page("mistakes")
    bad["reasons"][0]["text"] += " It also has low EMF."
    client = FakeClient([json_response(bad)] * (MAX_REPAIR_ATTEMPTS + 1))
    log = RunLog("test-skel", tmp_path / "run.log")
    try:
        with pytest.raises(ClaimsGateFailure) as exc:
            write_and_gate_page(
                cartridge_name="listicle", cartridges_dir=REPO_ROOT / "cartridges", ad_brief=AD_BRIEF,
                facts_pack=FACTS_PACK, client=client, model="fake", budget=Budget(), log=log,
                financing_lender=None, speaker_pov="third_person", tenant=TENANT,
                listicle_style="mistakes", listicle_skeleton=_skeleton(),
            )
    finally:
        log.close()
    assert all(exc.value.attempts)
    assert any("emf" in json.dumps(a).lower() for a in exc.value.attempts[0])
    for call in client.messages.calls:
        assert '"skeleton"' in block_text(call["messages"][0]["content"])


# ---------------------------------------------------------------------------
# a whole run, offline (evals/fake_run.py)
# ---------------------------------------------------------------------------

class _RecordingClient(FakeClient):
    made = []

    def __init__(self, responses):
        super().__init__(responses)
        _RecordingClient.made.append(self)


def _run_with_spies(monkeypatch, **kwargs):
    _RecordingClient.made = []
    monkeypatch.setattr(fake_run, "FakeClient", _RecordingClient)
    gated = []
    real_gate = repair.check_page_gates

    def spy(page, facts_pack, cartridge_name, **kw):
        problems = real_gate(page, facts_pack, cartridge_name, **kw)
        gated.append((cartridge_name, kw.get("listicle_style"), problems))
        return problems

    monkeypatch.setattr(repair, "check_page_gates", spy)
    result = fake_run.run_once(str(FIXTURE), tenant="peak-saunas", cartridges="listicle", **kwargs)
    return result, _RecordingClient.made[0], gated


def test_a_run_with_skeleton_puts_its_item_map_in_the_prompt_and_runs_every_gate(monkeypatch):
    (exit_code, run_dir, pages), client, gated = _run_with_spies(monkeypatch, skeleton="hormozi-mistakes")
    assert exit_code == 0
    page = json.loads(pages[0].read_text())
    # the skeleton set the style (no --style) and its look (no --look)
    assert page["style"] == "mistakes"
    assert page["look"] == "editorial"
    assert runstate.load_state(run_dir)["listicle"]["skeleton_id"] == "hormozi-mistakes"
    write_call = client.messages.calls[-1]
    user = block_text(write_call["messages"][0]["content"])
    for item in skeletons.load_skeleton("hormozi-mistakes")["items"]:
        assert item["role"] in user
    assert "winner skeleton" in block_text(write_call["system"])
    # the gate ran on the listicle page, with the run's style, and passed
    assert [(c, s) for c, s, _ in gated] == [("listicle", "mistakes")]
    assert gated[0][2] == []
    # a pinned skeleton's headline list steers the headline pick
    assert page["headline_template_id"] in skeletons.load_skeleton("hormozi-mistakes")["headline_templates"]


def test_a_run_without_skeleton_auto_picks_one_and_keeps_the_style_look(monkeypatch):
    (exit_code, run_dir, pages), client, _gated = _run_with_spies(monkeypatch, style="questions")
    assert exit_code == 0
    page = json.loads(pages[0].read_text())
    assert page["look"] == listicle.LOOK_BY_STYLE["questions"]
    assert runstate.load_state(run_dir)["listicle"]["skeleton_id"] == "buyers-checklist"
    assert '"buyers-checklist"' in block_text(client.messages.calls[-1]["messages"][0]["content"])


def test_skeleton_and_style_that_disagree_stop_before_any_model_call():
    with pytest.raises(skeletons.SkeletonError):
        fake_run.run_once(str(FIXTURE), tenant="peak-saunas", cartridges="listicle",
                          skeleton="buyers-checklist", style="reasons")
    client = FakeClient([])
    args = argparse.Namespace(
        input=str(FIXTURE), cartridges="listicle", seed=42, style="reasons", headline_template=None,
        skeleton="buyers-checklist", product=None, ffmpeg_bin="/usr/bin/ffmpeg",
        whisper_bin="/nonexistent/whisper-cli", whisper_model="/nonexistent/model.bin",
        tenant="peak-saunas", batch=False,
    )
    # the same path as an ineligible --headline-template: a HarnessError that
    # cli.main turns into an exit code, raised before the first model call
    with fake_run.fake_environment(client), pytest.raises(skeletons.SkeletonError, match="fits"):
        cli.cmd_run(args)
    assert client.messages.calls == []
