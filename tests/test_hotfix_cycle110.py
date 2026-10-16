"""Cycle 110 hotfixes from the first fresh run on master (run 64wr, the sale
still ad, 2026-10-16 00:56 UTC): H1 the run date is the tenant's day; H2 an
unverified offer in the hook is not a dead end for landing:hook_match."""
import datetime

from harness import offer, pipeline
from tests.support import TENANT
from tests.test_presell_cycle110 import AIIR, PROMO

# the real offer.render_today (tests/conftest.py pins it for every test)
REAL_RENDER_TODAY = offer.render_today
RUN_64WR = datetime.datetime(2026, 10, 16, 0, 56, tzinfo=datetime.timezone.utc)


class _Zone:
    def __init__(self, name):
        self.name = name

    def get(self, key, default=None):
        return self.name if key == "timezone" and self.name else default


# ---------------------------------------------------------------------------
# H1: grounding uses the tenant's day
# ---------------------------------------------------------------------------

def test_h1_at_00_56_utc_oct_16_denver_is_still_oct_15_and_the_sale_matches():
    brief_offer = offer.extract_offer(AIIR)          # "$300 off every indoor sauna ... Prices go up October 16"
    denver = REAL_RENDER_TODAY(_Zone("America/Denver"), now=RUN_64WR)
    assert denver == datetime.date(2026, 10, 15)
    assert offer.match_offer(brief_offer, [PROMO], denver.isoformat())["id"] == PROMO["id"]
    utc = REAL_RENDER_TODAY(_Zone(None), now=RUN_64WR)
    assert utc == datetime.date(2026, 10, 16)
    assert offer.match_offer(brief_offer, [PROMO], utc.isoformat()) is None


def test_h1_the_run_date_comes_from_render_today(monkeypatch):
    # the conftest pin reaches the pipeline too
    assert pipeline.RunState(tenant=TENANT, args=None, client=None).today_iso == "2000-01-01"
    monkeypatch.setattr(offer, "render_today", lambda tenant=None, now=None: REAL_RENDER_TODAY(tenant, now=RUN_64WR))
    assert TENANT.get("timezone") == "America/Denver"
    assert pipeline.RunState(tenant=TENANT, args=None, client=None).today_iso == "2026-10-15"


# ---------------------------------------------------------------------------
# H2: an unverified offer in the hook falls back to the ad's promise
# ---------------------------------------------------------------------------

def _64wr(name):
    import json

    from tests.support import REPO_ROOT

    return json.loads((REPO_ROOT / "tests" / "fixtures" / "c110" / "64wr" / f"{name}.json").read_text())


def _hook_keys(headline, brief, fp):
    from harness import persuasion

    return [p["key"] for p in persuasion.find_hook_match_violations({"hero": {"headline": headline}}, brief,
                                                                     facts_pack=fp, exclude={"peak", "fuji"})]


def test_h2_the_64wr_hook_states_an_unverified_offer_so_the_promise_is_matched():
    from harness import landing

    brief, fp = _64wr("ad_brief"), _64wr("facts_pack")
    assert brief["hook"] == "$300 off every indoor sauna" and brief["offer"] and offer.offer_view(fp) is None
    assert _hook_keys("A full-spectrum infrared sauna built for two", brief, fp) == ["landing:hook_match"]  # attempt 2
    assert _hook_keys("Save on a two-person sauna before the price goes up", brief, fp) == []
    assert _hook_keys("$300 off every indoor sauna", brief, fp) == ["landing:hook_match"]   # the unverified offer
    text = " ".join(landing.writer_lines(fp, TENANT, look="peak", ad_brief=brief))
    assert "The ad's offer is not verified; do not state it; lead with the ad's promise instead." in text


# ---------------------------------------------------------------------------
# R2: the listicle H1 itself shares a hook word
# ---------------------------------------------------------------------------

def _aqdh(name):
    import json

    from tests.support import REPO_ROOT

    return json.loads((REPO_ROOT / "tests" / "fixtures" / "c110c" / "aqdh" / f"{name}.json").read_text())


def test_r2_the_listicle_h1_must_share_a_hook_word():
    from harness import listicle_persuasion as lp

    page, brief, fp = _aqdh("page"), _aqdh("ad_brief"), _aqdh("facts_pack")
    names = ["Fuji", "Peak Fuji", "PEAK", "Peak"]
    assert lp.first_screen_h1(page) == "What medical conditions could benefit"     # the display H1
    match, _text = lp.hook_match(page, brief, fp, names)
    assert match["ok"] and match["headline_overlap"]       # passes here; listicle:health_words catches it
    quiet = dict(page, display_headline="Quiet evenings start here")
    match, _text = lp.hook_match(quiet, brief, fp, names)
    assert not match["ok"] and match["headline_overlap"] == []


def test_r2_a_templated_headline_with_no_hook_word_passes_when_the_first_screen_shares_two():
    from harness import listicle_persuasion as lp

    page, brief, fp = _aqdh("page"), _aqdh("ad_brief"), _aqdh("facts_pack")
    names = ["Fuji", "Peak Fuji", "PEAK", "Peak"]
    # a cycle 70 template headline as the H1 (display style with no display line), no hook word in it
    story = dict(page, hero_style="display", display_headline="", headline="5 Reasons Careful Buyers Are Choosing PEAK",
                 dek="What medical conditions could benefit from an infrared sauna?")
    match, _text = lp.hook_match(story, brief, fp, names)
    assert not match["ok"] and match["headline_overlap"] == []          # free headline: the H1 rule applies
    match, _text = lp.hook_match(story, brief, fp, names, headline_templated=True)
    assert match["ok"] and match["headline_overlap"] is None and len(match["overlap"]) >= 2
    match, _text = lp.hook_match(dict(story, dek="A plain look before you buy."), brief, fp, names,
                                 headline_templated=True)
    assert not match["ok"]                                              # the two-word rule still applies


# ---------------------------------------------------------------------------
# R3: the design judge advises on a pre-sell look, never stops it
# ---------------------------------------------------------------------------

from tests import test_design_gate_cycle92  # noqa: E402

fake_shots = test_design_gate_cycle92.fake_shots  # the pytest fixture, found by name in this module


def test_r3_enforce_stops_at_60_for_proof_and_advise_records_60_for_peak(tmp_path, fake_shots):
    import json

    from harness import design_gate
    from tests.test_design_gate_cycle92 import FakeClient, Log, _cfg, _judge_json, _page_dir, _renderer

    results = {}
    for look in ("proof", "peak"):
        page_dir, page = _page_dir(tmp_path / look, "landing", {"headline": "h", "look": look})
        render, _calls = _renderer(page_dir)
        log = Log()
        cfg = _cfg(mode="enforce", min_score=62.0, variants=1, advise_looks=("peak",))
        results[look] = (design_gate.run_for_page(
            cartridge="landing", page_dir=page_dir, base_page=page, render=render, tenant=TENANT,
            client=FakeClient([_judge_json(60), _judge_json(60)]), cfg=cfg, log=log), log, page_dir)
    proof, proof_log, _ = results["proof"]
    assert proof["status"] == "stop" and proof["score"] == 60
    assert not any("advise" in m for _s, m in proof_log.events)
    peak, peak_log, peak_dir = results["peak"]
    assert peak["status"] == "warn" and peak["score"] == 60 and peak["subscores"] and peak["top_issues"]
    assert ("design_gate", "design gate: advise (look peak): score 60") in peak_log.events
    saved = json.loads((design_gate.design_dir_for(peak_dir) / design_gate.SCORES_FILE).read_text())
    assert saved["score"] == 60 and saved["status"] == "warn"


def test_r3_peak_is_advise_only_in_the_tenant_and_a_hard_check_still_stops(tmp_path, fake_shots):
    from harness import design_gate
    from tests.test_design_gate_cycle92 import GOOD_PROBE, FakeClient, Log, _cfg, _judge_json, _page_dir, _renderer

    assert TENANT.get("design_gate.advise_looks") == ["peak"]
    fake_shots.extend([dict(GOOD_PROBE, cta_expected=True, cta={"sel": "a.ld-btn", "top": 2400, "bottom": 2455})] * 4)
    page_dir, page = _page_dir(tmp_path, "landing", {"headline": "h", "look": "peak"})
    render, _calls = _renderer(page_dir)
    result = design_gate.run_for_page(cartridge="landing", page_dir=page_dir, base_page=page, render=render,
                                      tenant=TENANT, client=FakeClient([_judge_json(90)] * 3),
                                      cfg=_cfg(mode="enforce", variants=1, advise_looks=("peak",)), log=Log())
    assert result["status"] == "stop"     # first_screen_cta is a hard check: advise never waives it
