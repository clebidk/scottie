"""Cycle 92: the design-quality gate (harness/design_gate.py), the shared
capture path (harness/capture.py) and the local brand fonts (harness/fonts.py).

No network and no real model call: the judge is the suite's FakeClient. The
hard-check tests on three synthetic pages drive a real headless Chromium when
one is installed (marked `real_browser`, skipped otherwise); every other test
replaces the screenshot step with tiny generated PNGs and synthetic probes.
"""
import base64
import hashlib
import json
import re
from pathlib import Path

import pytest
from PIL import Image

from evals import fake_run
from harness import abtest, capture, cli, design_gate, fonts, library, pipeline, review, serve
from harness import tenant as tenant_mod
from tests.conftest import FakeClient, FakeResponse
from tests.support import TENANT

FIXTURE = str(TENANT.fixtures_dir / "founder-warranty-demo.txt")
REAL_BROWSER = capture.find_browser()
needs_browser = pytest.mark.skipif(REAL_BROWSER is None, reason="no headless chromium/google-chrome here")


def _judge_json(score, verdict=None, issues=None, **subs):
    verdict = verdict or ("ship" if score >= 80 else "revise")
    return json.dumps({
        "score_0_100": score,
        "subscores": {k: subs.get(k, score) for k in design_gate.SUBSCORES},
        "top_issues": issues if issues is not None else
        [{"what": "Tiny hero image", "where": "first screen", "fix_as_layout_choice": "a different first screen"}],
        "verdict": verdict,
    })


def _cfg(**over):
    cfg = dict(design_gate.DEFAULTS, mode="enforce", min_score=75.0)
    cfg.update(over)
    return cfg


def _png(path, size=(390, 900), color=(255, 255, 255)):
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", size, color).save(path)


GOOD_PROBE = {
    "viewport_width": 390, "scroll_width": 390, "height": 900, "overflow": [], "cut": [], "taps": [],
    "hero_text": [], "placeholders": [], "images": [{"src": "a.jpg", "empty": False, "loaded": True, "w": 390, "h": 300}],
    "used_families": {"DM Sans": 10}, "fonts": {"DM Sans": {"faces": 2, "loaded": 1, "error": 0}},
}


@pytest.fixture
def fake_shots(monkeypatch):
    """take_shots writes three PNGs and returns GOOD_PROBE (or a per-variant
    probe from the list the test fills), and a browser is "found"."""
    probes = []

    def take(page_dir, design_dir, n, *, tenant, browser):
        names = {"phone": f"variant-{n}.png", "desktop": f"variant-{n}-desktop.png",
                 "desktop_full": f"variant-{n}-desktop-full.png"}
        _png(Path(design_dir) / names["phone"])
        _png(Path(design_dir) / names["desktop"], (1280, 900))
        _png(Path(design_dir) / names["desktop_full"], (1280, 1200))
        probe = probes.pop(0) if probes else GOOD_PROBE
        return names, probe, probe

    monkeypatch.setattr(design_gate, "take_shots", take)
    monkeypatch.setattr(capture, "find_browser", lambda: "/fake/chromium")
    return probes


class Log:
    def __init__(self):
        self.events, self.calls = [], []

    def event(self, stage, message):
        self.events.append((stage, message))

    def call(self, stage, model, input_tokens, output_tokens, **kw):
        self.calls.append((stage, model, input_tokens, output_tokens))


def _page_dir(tmp_path, cartridge="listicle", page=None):
    page_dir = tmp_path / "run" / cartridge
    page_dir.mkdir(parents=True)
    page = page or {"headline": "h", "look": "open", "hero_style": "story"}
    (page_dir / "page.json").write_text(json.dumps(page))
    (page_dir / "index.html").write_text(f"<html><body>{page}</body></html>")
    return page_dir, page


def _renderer(page_dir, fallback=None):
    """A render that writes page.json + index.html (face falls back to
    `fallback` when given, as the real renderer does with no ad still)."""
    calls = []

    def render(page):
        page = dict(page)
        if fallback and page.get("hero_style") == "face":
            page["hero_style"] = fallback
        calls.append((page.get("look"), page.get("hero_style")))
        (page_dir / "page.json").write_text(json.dumps(page))
        (page_dir / "index.html").write_text(f"<html><body>{page.get('look')} {page.get('hero_style')}</body></html>")
        return page

    return render, calls


# ---------------------------------------------------------------------------
# hard checks on three synthetic pages (real browser)
# ---------------------------------------------------------------------------

def _write_page(folder, body, style=""):
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "index.html").write_text(
        '<!doctype html><html><head><meta name="viewport" content="width=device-width,initial-scale=1">'
        f"<style>body{{margin:0;font:16px sans-serif}}{style}</style></head><body>{body}</body></html>")
    return folder


@pytest.mark.real_browser
@needs_browser
def test_hard_checks_on_three_synthetic_pages(tmp_path):
    overflowing = _write_page(tmp_path / "overflow", '<h1>Wide</h1><div style="width:640px;height:40px;background:#111"></div>'
                              '<p>text</p>')
    Image.new("RGB", (800, 600), (235, 235, 235)).save(tmp_path / "hero.png")
    (tmp_path / "low" / "assets").mkdir(parents=True)
    Image.new("RGB", (800, 600), (235, 235, 235)).save(tmp_path / "low" / "assets" / "hero.png")
    low = _write_page(tmp_path / "low",
                      '<section style="background:url(assets/hero.png) center/cover;height:600px;padding:40px 16px">'
                      '<h1 style="color:#ffffff;font-size:20px;font-weight:400">Pale words on a pale photo</h1></section>')
    good = _write_page(tmp_path / "good",
                       '<main style="padding:24px 16px"><h1 style="color:#161817">A clear page</h1>'
                       '<p>Body text that reads well.</p>'
                       '<a href="#" class="btn" style="display:block;padding:14px;background:#161817;color:#fff">'
                       'Shop now</a></main>')
    results = {}
    for name, folder in (("overflow", overflowing), ("low", low), ("good", good)):
        design_dir = folder / "design"
        shots, probe_phone, _desk = design_gate.take_shots(folder, design_dir, 1, tenant=None, browser=REAL_BROWSER)
        assert shots.get("phone") and probe_phone, name
        with Image.open(design_dir / shots["phone"]) as im:
            assert im.size[0] == 390
        results[name] = design_gate.hard_checks(probe_phone, design_dir / shots["phone"], folder)
    assert results["overflow"]["overflow_390"]["status"] == "fail"
    assert "640" in results["overflow"]["overflow_390"]["detail"]
    assert results["low"]["contrast_hero"]["status"] == "fail"
    assert results["low"]["contrast_hero"]["items"][0]["ratio"] < 4.5
    assert results["low"]["overflow_390"]["status"] == "pass"
    assert design_gate.hard_fail(results["good"]) == []
    assert results["good"]["contrast_hero"]["status"] == "pass"
    assert results["good"]["tap_targets"]["status"] == "pass"


@pytest.mark.real_browser
@needs_browser
def test_capture_injects_local_fonts_and_records_which_loaded(tmp_path):
    """The page's own @font-face (a CDN url that cannot load here) is pointed
    at the tenant's local copy, so the real face renders with no font
    installed; a declared family whose local file is broken (a tiny generated
    non-font) is recorded as a fallback."""
    brand = tmp_path / "brand"
    (brand / "fonts-display").mkdir(parents=True)
    real = TENANT.brand_dir / "fonts-display" / "pk-peak-grotesk-wide.woff2"
    (brand / "fonts-display" / "display.woff2").write_bytes(real.read_bytes())
    (brand / "fonts-display" / "broken.woff2").write_bytes(b"wOF2" + b"\x00" * 60)   # tiny generated, not a font

    class T:
        brand_dir = brand

    faces = ('@font-face{font-family:"Display Face";font-weight:400;src:url("https://cdn.invalid/display.woff2") format("woff2");}'
             '@font-face{font-family:"Broken Face";font-weight:400;src:url("https://cdn.invalid/broken.woff2") format("woff2");}')
    page = _write_page(tmp_path / "page", '<h1 style="font-family:\'Display Face\',monospace">Wide</h1>'
                                          '<p style="font-family:\'Broken Face\',monospace">Body</p>', style=faces)
    probe = capture.probe(REAL_BROWSER, page / "index.html", width=390, tenant=T())
    report = design_gate.font_report(probe)
    assert "Display Face" in report["loaded"]
    assert "Broken Face" in report["fallback"] and report["status"] == "fallback"
    assert not list(page.glob(".index.capture-*.html")), "the staged copy is removed"
    assert "cdn.invalid" in (page / "index.html").read_text(), "the page itself is never changed"


# ---------------------------------------------------------------------------
# hard checks without a browser
# ---------------------------------------------------------------------------

def test_contrast_math_and_parsing():
    assert design_gate.contrast_ratio(design_gate.luminance((0, 0, 0)), design_gate.luminance((255, 255, 255))) == \
        pytest.approx(21.0)
    assert design_gate.parse_color("rgba(22, 24, 23, 0.5)") == (22.0, 24.0, 23.0)
    assert design_gate.parse_color("transparent") is None


def test_hard_checks_from_a_synthetic_probe(tmp_path):
    page_dir, _ = _page_dir(tmp_path)
    probe = dict(GOOD_PROBE, images=[{"src": "x.jpg", "empty": False, "loaded": False, "w": 10, "h": 10}],
                 cut=[{"sel": "p.card", "text": "cut", "axis": "y"}],
                 taps=[{"sel": "a.btn", "w": 120, "h": 30, "text": "Buy", "cta": True}],
                 fonts={"DM Sans": {"faces": 2, "loaded": 0, "error": 2}})
    checks = design_gate.hard_checks(probe, None, page_dir)
    assert checks["empty_images"]["status"] == "fail"
    assert checks["text_cut"]["status"] == "fail"
    assert checks["tap_targets"]["status"] == "fail"
    assert checks["fonts"]["status"] == "warn" and "DM Sans" in checks["fonts"]["detail"]
    assert set(design_gate.hard_fail(checks)) == {"empty_images", "text_cut", "tap_targets"}
    unknown = design_gate.hard_checks(None, None, page_dir)
    assert unknown["overflow_390"]["status"] == "unknown" and unknown["page_weight"]["status"] == "pass"


# ---------------------------------------------------------------------------
# the judge
# ---------------------------------------------------------------------------

def test_parse_judge_scores_verdicts_and_bad_answers():
    parsed = design_gate.parse_judge("```json\n" + _judge_json(140, "ship") + "\n```")
    assert parsed["score"] == 100 and parsed["verdict"] == "ship"
    assert set(parsed["subscores"]) == set(design_gate.SUBSCORES)
    assert parsed["top_issues"][0]["fix_as_layout_choice"] == "a different first screen"
    for bad in ("not json", json.dumps({"score_0_100": 80, "verdict": "great"}), json.dumps({"verdict": "ship"})):
        with pytest.raises(design_gate.JudgeError):
            design_gate.parse_judge(bad)


def test_judge_call_uses_opus_effort_no_thinking_and_records_cost():
    client = FakeClient([FakeResponse(_judge_json(82), input_tokens=9000, output_tokens=3000)])
    log = Log()
    result, cost = design_gate.judge(client, model="claude-opus-5-5", effort="high",
                                     images=[("Phone part 1", "AAAA")], context="ctx", log=log, tenant=TENANT)
    call = client.messages.calls[0]
    assert call["model"] == "claude-opus-5-5" and call["output_config"] == {"effort": "high"}
    assert "thinking" not in call
    assert call["messages"][0]["content"][1]["type"] == "image"
    assert "PEAK" in call["system"] and "Ridge" in call["system"]   # the tenant's brand and reference pages
    assert "score_0_100" in call["system"]
    assert result["score"] == 82
    assert cost == pytest.approx((9000 * 4 + 3000 * 20) / 1e6)
    assert log.calls == [("design_gate", "claude-opus-5-5", 9000, 3000)]


def test_judge_refusal_and_api_errors_are_judge_errors():
    refusal = FakeResponse(_judge_json(90))
    refusal.stop_reason = "refusal"
    with pytest.raises(design_gate.JudgeError, match="refus"):
        design_gate.judge(FakeClient([refusal]), model="claude-opus-5-5", effort="high", images=[], context="c")
    with pytest.raises(design_gate.JudgeError, match="RuntimeError"):
        design_gate.judge(FakeClient([RuntimeError("529 overloaded")]), model="claude-opus-5-5", effort="high",
                          images=[], context="c")


def test_settings_defaults_env_override_and_bad_values(monkeypatch):
    monkeypatch.delenv("HARNESS_DESIGN_GATE", raising=False)
    cfg = design_gate.settings(TENANT)
    # cycle 103b: the tenant's own values (2026-10-12 launch: enforce at 65)
    configured = TENANT.get("design_gate")
    assert cfg["mode"] == configured["mode"] and cfg["model"] == configured["model"]
    assert cfg["variants"] == configured["variants"] and cfg["min_score"] == float(configured["min_score"])
    assert cfg["max_usd_per_page"] == float(configured["max_usd_per_page"])
    other = "warn" if configured["mode"] != "warn" else "enforce"
    monkeypatch.setenv("HARNESS_DESIGN_GATE", other)
    assert design_gate.settings(TENANT)["mode"] == other
    monkeypatch.setenv("HARNESS_DESIGN_GATE", "loud")
    with pytest.raises(design_gate.DesignGateError):
        design_gate.settings(TENANT)

    class T:
        def __init__(self, cfg):
            self.cfg = cfg

        def get(self, key, default=None):
            return self.cfg if key == "design_gate" else default

    monkeypatch.delenv("HARNESS_DESIGN_GATE")
    assert design_gate.settings(T({"mode": False}))["mode"] == "off"   # YAML's bare `off`
    with pytest.raises(design_gate.DesignGateError, match="price"):
        design_gate.settings(T({"model": "claude-unknown-9"}))


# ---------------------------------------------------------------------------
# variants: best of N, revise round, STOP / warn, cost cap
# ---------------------------------------------------------------------------

def test_best_of_three_picks_the_best_and_records_every_variant(tmp_path, fake_shots):
    page_dir, page = _page_dir(tmp_path)
    render, calls = _renderer(page_dir)
    client = FakeClient([_judge_json(70), _judge_json(86), _judge_json(78)])
    log = Log()
    result = design_gate.run_for_page(cartridge="listicle", page_dir=page_dir, base_page=page, render=render,
                                      tenant=TENANT, client=client, cfg=_cfg(), log=log)
    assert result["status"] == "pass" and result["score"] == 86 and result["chosen"] == 2
    assert [v["hero_style"] for v in result["variants"]] == ["story", "face", "display"]
    # the best layout is what stays in the page folder
    assert json.loads((page_dir / "page.json").read_text())["hero_style"] == "face"
    assert calls[-1] == ("open", "face")
    scores = json.loads((page_dir / "design" / "scores.json").read_text())
    assert [v["judge"]["score"] for v in scores["variants"]] == [70, 86, 78]
    for n in (1, 2, 3):
        assert (page_dir / "design" / f"variant-{n}.png").is_file()
    assert not (page_dir / "design" / "work").exists()
    assert scores["judge_calls"] == 3 and scores["cost_usd"] > 0
    assert any("design gate: 3 variants, 3 judge calls" in m for _s, m in log.events)


def test_face_fallback_is_not_judged_twice(tmp_path, fake_shots):
    page_dir, page = _page_dir(tmp_path)
    render, _calls = _renderer(page_dir, fallback="story")
    client = FakeClient([_judge_json(80), _judge_json(84)])
    result = design_gate.run_for_page(cartridge="listicle", page_dir=page_dir, base_page=page, render=render,
                                      tenant=TENANT, client=client, cfg=_cfg(), log=Log())
    assert [v["hero_style"] for v in result["variants"]] == ["story", "display"]
    assert any("already tried" in n for n in result["notes"])
    assert result["judge_calls"] == 2


def test_revise_round_translates_fixes_into_a_new_layout(tmp_path, fake_shots):
    page_dir, page = _page_dir(tmp_path, "landing", {"headline": "h", "look": "open", "hero_style": "ad_first"})
    render, calls = _renderer(page_dir)
    issues = [{"what": "Stacked boxes and a generic layout", "where": "middle",
               "fix_as_layout_choice": "use a different look with stronger structure"}]
    client = FakeClient([_judge_json(64, issues=issues), _judge_json(81)])
    result = design_gate.run_for_page(cartridge="landing", page_dir=page_dir, base_page=page, render=render,
                                      tenant=TENANT, client=client, cfg=_cfg(variants=1), log=Log())
    assert [r["round"] for r in result["rounds"]] == [1, 2]
    choice = result["rounds"][1]["choices"][0]
    assert choice["look"] == "proof" and "a different look (proof)" in choice["reasons"]
    assert result["status"] == "pass" and result["look"] == "proof" and result["chosen"] == 2
    assert calls == [("proof", "ad_first")]


def test_revise_round_tries_the_other_heroes_the_look_json_declares(tmp_path, fake_shots):
    """Cycle 94: a first-screen fix picks a composition the look declares but
    does not seed among (look.json "heroes" beyond "default_heroes")."""
    page_dir, page = _page_dir(tmp_path, "landing", {"headline": "h", "look": "open", "hero_style": "ad_first"})
    render, calls = _renderer(page_dir)
    issues = [{"what": "Tiny hero image", "where": "first screen", "fix_as_layout_choice": "a full-bleed photo hero"}]
    client = FakeClient([_judge_json(60, issues=issues)] + [_judge_json(70)] * 3)
    result = design_gate.run_for_page(cartridge="landing", page_dir=page_dir, base_page=page, render=render,
                                      tenant=TENANT, client=client, cfg=_cfg(), log=Log())
    round2 = [v for v in result["variants"] if v["round"] == 2]
    from harness import heroes
    for v in round2:
        assert v["hero_style"] in heroes.look_heroes("landing", v["look"], TENANT)
    assert any(v["hero_style"] not in ("ad_first",) for v in round2)


def test_still_below_after_the_revise_round_stops_or_warns(tmp_path, fake_shots):
    for mode, expected in (("enforce", "stop"), ("warn", "warn")):
        page_dir, page = _page_dir(tmp_path / mode, "landing", {"headline": "h", "look": "open"})
        render, _calls = _renderer(page_dir)
        client = FakeClient([_judge_json(60), _judge_json(62)])
        result = design_gate.run_for_page(cartridge="landing", page_dir=page_dir, base_page=page, render=render,
                                          tenant=TENANT, client=client, cfg=_cfg(mode=mode, variants=1), log=Log())
        assert result["status"] == expected and result["score"] == 62
        assert len(result["rounds"]) == 2


def test_no_untried_layout_skips_the_revise_round_with_a_note(tmp_path, fake_shots):
    page_dir, page = _page_dir(tmp_path, "article", {"headline": "h"})
    render, calls = _renderer(page_dir)
    result = design_gate.run_for_page(cartridge="article", page_dir=page_dir, base_page=page, render=render,
                                      tenant=TENANT, client=FakeClient([_judge_json(60)]), cfg=_cfg(), log=Log())
    assert result["status"] == "stop" and calls == []
    assert any("no untried layout" in n for n in result["notes"])


def test_hard_check_failure_cannot_ship_however_high_the_score(tmp_path, fake_shots):
    fake_shots.append(dict(GOOD_PROBE, scroll_width=640, overflow=[{"sel": "div.wide", "right": 640}]))
    page_dir, page = _page_dir(tmp_path, "landing", {"headline": "h", "look": "open"})
    render, _calls = _renderer(page_dir)
    client = FakeClient([_judge_json(95), _judge_json(79)])
    result = design_gate.run_for_page(cartridge="landing", page_dir=page_dir, base_page=page, render=render,
                                      tenant=TENANT, client=client, cfg=_cfg(variants=1), log=Log())
    assert result["variants"][0]["hard_fail"] == ["overflow_390"]
    assert result["chosen"] == 2 and result["status"] == "pass"


def test_cost_cap_stops_further_rounds_with_a_note(tmp_path, fake_shots):
    page_dir, page = _page_dir(tmp_path)
    render, _calls = _renderer(page_dir)
    big = (9000, 4000)   # $0.116 per call on Opus 5.5
    client = FakeClient([FakeResponse(_judge_json(60), *big), FakeResponse(_judge_json(90), *big)])
    result = design_gate.run_for_page(cartridge="listicle", page_dir=page_dir, base_page=page, render=render,
                                      tenant=TENANT, client=client, cfg=_cfg(max_usd_per_page=0.15), log=Log())
    assert result["judge_calls"] == 1
    assert result["variants"][1]["budget_stop"] is True
    assert any("max_usd_per_page $0.15 reached" in n for n in result["notes"])
    assert result["status"] == "stop", "a money cap never lowers the bar"
    assert len(result["rounds"]) == 1


def test_no_browser_leaves_the_page_unscored_never_a_crash(tmp_path, monkeypatch):
    monkeypatch.setattr(capture, "find_browser", lambda: None)
    page_dir, page = _page_dir(tmp_path)
    render, calls = _renderer(page_dir)
    result = design_gate.run_for_page(cartridge="listicle", page_dir=page_dir, base_page=page, render=render,
                                      tenant=TENANT, client=FakeClient([]), cfg=_cfg(), log=Log())
    assert result["status"] == "unscored" and calls == []
    assert any("UNSCORED" in n for n in result["notes"])


def test_pinned_look_and_hero_are_never_changed():
    space = design_gate.layout_space("listicle", TENANT, {"hero_style": "story"})
    assert {(x["look"], x["hero_style"]) for x in space} == {("open", "story")}
    assert {x["look"] for x in design_gate.extra_layouts("landing", TENANT, {"look": "open"})} == {"open"}
    assert {x["hero_style"] for x in design_gate.extra_layouts("landing", TENANT, {"hero_style": "split"})} == {"split"}
    # every look the tenant allows, each with its look.json default heroes
    from harness import heroes
    for lay in design_gate.layout_space("landing", TENANT):
        assert lay["hero_style"] in heroes.default_pool("landing", lay["look"], TENANT)
    assert {x["look"] for x in design_gate.layout_space("landing", TENANT)} >= {"open", "proof", "cinematic", "buybox"}
    assert design_gate.block_sets("listicle") == (None,)   # a look's blocks come with the look (cycle 91)


# ---------------------------------------------------------------------------
# REVIEW.md
# ---------------------------------------------------------------------------

def test_review_md_design_section(tmp_path, fake_shots):
    page_dir, page = _page_dir(tmp_path)
    render, _calls = _renderer(page_dir)
    result = design_gate.run_for_page(cartridge="listicle", page_dir=page_dir, base_page=page, render=render,
                                      tenant=TENANT, client=FakeClient([_judge_json(70), _judge_json(88),
                                                                        _judge_json(72)]),
                                      cfg=_cfg(), log=Log())
    result.pop("final_page")
    text = "\n".join(design_gate.review_lines({"listicle": result}))
    assert "## Design" in text and "Result: PASS" in text
    assert "Chosen: variant 2 (look open, face first screen), score 88" in text
    assert "Subscores: hierarchy 88" in text and "Tiny hero image" in text
    assert "](listicle/design/variant-2.png)" in text and "| 2 (chosen) |" in text
    assert "Fonts: loaded" in text and "Design gate total cost: $" in text
    assert design_gate.review_lines({}) == []


# ---------------------------------------------------------------------------
# the pipeline: enforce STOP, warn, and off is byte-identical
# ---------------------------------------------------------------------------

def _run(monkeypatch, mode, judge_answers, *, variants=1, **kw):
    monkeypatch.setenv("HARNESS_DESIGN_GATE", mode)
    real_settings = design_gate.settings
    monkeypatch.setattr(design_gate, "settings", lambda tenant: dict(real_settings(tenant), variants=variants))
    return fake_run.run_once(FIXTURE, tenant=TENANT.name, cartridges="landing", seed=42,
                             extra_responses=judge_answers, **kw)


def test_pipeline_enforce_stops_with_exit_2_and_writes_the_design_section(monkeypatch, fake_shots, capsys):
    rc, _run_dir, _pages = _run(monkeypatch, "enforce", [_judge_json(55, issues=[]), _judge_json(58, issues=[])])
    assert rc == 2
    run_dir = max(TENANT.out_dir.iterdir(), key=lambda p: p.stat().st_mtime)
    scores = json.loads((run_dir / "landing" / "design" / "scores.json").read_text())
    assert scores["status"] == "stop" and len(scores["variants"]) == 2
    review_text = (run_dir / "REVIEW.md").read_text()
    assert "## Design" in review_text and "STOP (below min_score; mode enforce)" in review_text
    log = next(TENANT.runs_dir.glob(f"{run_dir.name}.log")).read_text()
    assert "design gate: 2 variants, 2 judge calls, $" in log and "gate_result: STOP stage=design_gate" in log
    assert "model=claude-opus-5-5" in log and "run_result: STOP" in log
    ledger = [json.loads(x) for x in (TENANT.runs_dir / "spend-ledger.jsonl").read_text().splitlines()] \
        if (TENANT.runs_dir / "spend-ledger.jsonl").exists() else []
    assert not ledger or any(e.get("run_id") == run_dir.name for e in ledger)
    assert "Design gate STOPPED" in capsys.readouterr().err
    state = json.loads((run_dir / "state.json").read_text())
    assert state["state"] == "generated", "a STOPped page is never sent for review"


def test_pipeline_warn_goes_on_and_pass_records_the_choice(monkeypatch, fake_shots):
    rc, run_dir, _pages = _run(monkeypatch, "warn", [_judge_json(55, issues=[]), _judge_json(58, issues=[])])
    assert rc == 0
    assert json.loads((run_dir / "landing" / "design" / "scores.json").read_text())["status"] == "warn"
    assert "WARN" in (run_dir / "REVIEW.md").read_text()

    rc, run_dir, _pages = _run(monkeypatch, "enforce", [_judge_json(80), _judge_json(91, "ship")], variants=2)
    assert rc == 0
    scores = json.loads((run_dir / "landing" / "design" / "scores.json").read_text())
    # landing open (as rendered) and proof: both judged, the better one stays in the folder
    assert scores["status"] == "pass" and scores["judge_calls"] == 2 and scores["chosen"] == 2
    assert scores["look"] == "proof" and json.loads((run_dir / "landing" / "page.json").read_text())["look"] == "proof"
    assert json.loads((run_dir / "state.json").read_text())["landing"]["look"] == "proof"
    assert json.loads((run_dir / "state.json").read_text())["state"] == "needs_review"


def _artifact_hashes(run_dir):
    out = {}
    for path in sorted(run_dir.rglob("*")):
        if not path.is_file() or path.suffix not in (".html", ".json", ".md") or path.name in ("state.json",
                                                                                              "packet.json"):
            continue
        text = path.read_text(errors="ignore").replace(run_dir.name, "<run>")
        text = re.sub(r"- elapsed_s: [\d.]+", "- elapsed_s: <t>", text)
        out[str(path.relative_to(run_dir))] = hashlib.sha256(text.encode()).hexdigest()
    return out


def test_mode_off_leaves_every_output_byte_identical(monkeypatch):
    rc, with_stage, _ = _run(monkeypatch, "off", [], )
    assert rc == 0
    monkeypatch.setattr(pipeline, "DEFAULT_STAGES", tuple(s for s in pipeline.DEFAULT_STAGES if s != "design_gate"))
    rc, without_stage, _ = _run(monkeypatch, "off", [])
    assert rc == 0
    a, b = _artifact_hashes(with_stage), _artifact_hashes(without_stage)
    assert a == b and a
    assert not (with_stage / "landing" / "design").exists()
    assert "## Design" not in (with_stage / "REVIEW.md").read_text()


# ---------------------------------------------------------------------------
# A/B/C: the variant carries its design score
# ---------------------------------------------------------------------------

def test_abtest_variant_records_its_design_score(tmp_path):
    run_dir = tmp_path / "run1"
    (run_dir / "listicle" / "design").mkdir(parents=True)
    (run_dir / "listicle" / "index.html").write_text("<html></html>")
    (run_dir / "listicle" / "page.json").write_text(json.dumps({"hero_style": "story", "look": "open"}))
    (run_dir / "listicle" / "design" / "scores.json").write_text(
        json.dumps({"score": 83, "status": "pass", "cost_usd": 0.31}))
    rec = {"test_id": "t1", "seed": 1, "input": "x", "variants": [], "failed_arms": [], "ranking": []}
    variant = {"key": "A", "arm": "listicle:reasons:open:story", "attempts": []}
    rec["variants"].append(variant)
    saved = []
    original = abtest.save_test
    abtest.save_test = lambda tenant, r: saved.append(1)
    try:
        assert abtest._build_variant(TENANT, rec, variant, lambda *a: (0, run_dir)) == "built"
    finally:
        abtest.save_test = original
    assert variant["design_score"] == 83 and variant["design_status"] == "pass"
    assert variant["attempts"][0]["design_score"] == 83


# ---------------------------------------------------------------------------
# the review site and the Library
# ---------------------------------------------------------------------------

PASSWORD = "pw-cycle-92"


def _auth():
    return {"Authorization": "Basic " + base64.b64encode(f"caleb@peaksaunas.com:{PASSWORD}".encode()).decode()}


@pytest.fixture
def web(monkeypatch):
    monkeypatch.setenv("REVIEW_PASSWORD", PASSWORD)
    monkeypatch.delenv("REVIEW_TRUST_CF_ACCESS", raising=False)
    app = serve.build_app(TENANT)
    app.testing = True
    return app.test_client()


def test_site_shows_the_design_score_card_page_and_variant_strip(monkeypatch, fake_shots, web):
    rc, run_dir, _ = _run(monkeypatch, "enforce", [_judge_json(91, "ship"), _judge_json(80)])
    assert rc == 0
    state = json.loads((run_dir / "state.json").read_text())
    state["dry_run"] = False   # the home page hides FakeClient runs; show this one like a real run
    (run_dir / "state.json").write_text(json.dumps(state))
    home = web.get("/", headers=_auth()).get_data(as_text=True)
    assert "Design <b>91</b>" in home
    page = web.get(f"/gen/{run_dir.name}/landing", headers=_auth()).get_data(as_text=True)
    assert "<h3>Design</h3>" in page and "Design variants" in page and "Design score" in page
    shot_url = f"/run/{run_dir.name}/design/landing/variant-1.png"
    assert shot_url in page
    resp = web.get(shot_url, headers=_auth())
    assert resp.status_code == 200 and resp.mimetype == "image/png"
    thumb = web.get(shot_url + "?thumb=1", headers=_auth())
    assert thumb.status_code == 200 and thumb.mimetype == "image/jpeg"
    assert web.get(shot_url).status_code == 401, "behind the same login as every preview"
    assert web.get(f"/run/{run_dir.name}/design/landing/..%2Fpage.json", headers=_auth()).status_code == 404


def test_library_card_shows_the_design_score(monkeypatch, fake_shots, web):
    version = library.find(TENANT, "landing/proof")
    folder = TENANT.library_dir / version.key
    folder.mkdir(parents=True)
    (folder / "index.html").write_text("<html><body>proof</body></html>")
    (folder / "meta.json").write_text(json.dumps(library.describe(TENANT, version)))
    todo = library.versions_to_score(TENANT)
    assert [v.key for v in todo] == ["landing/proof"]
    scored = library.score_versions(TENANT, todo, client=FakeClient([_judge_json(78)]), cfg=_cfg(), say=lambda m: None)
    assert scored == 1
    meta = json.loads((folder / "meta.json").read_text())
    assert meta["design_score"] == 78 and meta["design_status"] == "pass"
    assert library.versions_to_score(TENANT) == []
    html = web.get("/library", headers=_auth()).get_data(as_text=True)
    assert "Design <b>78</b>" in html


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def test_cli_design_score_and_report(monkeypatch, fake_shots, capsys):
    rc, run_dir, _ = _run(monkeypatch, "off", [])
    assert rc == 0
    monkeypatch.setattr(cli, "make_client", lambda: FakeClient([_judge_json(77)]))
    assert cli.main(["design", "score", str(run_dir / "landing"), "--tenant", TENANT.name]) == 0
    out = capsys.readouterr().out
    assert "design gate: 1 variants, 1 judge calls" in out and "PASS score 77" in out
    scores = design_gate.load_scores(run_dir / "landing")
    assert scores["status"] == "pass" and scores["mode"] == "score"
    assert cli.main(["design", "report", "--tenant", TENANT.name]) == 0
    report_out = capsys.readouterr().out
    assert "1 scored page(s)" in report_out and "landing:open" in report_out and "Tiny hero image" in report_out
    assert cli.main(["design", "score", str(run_dir / "nope"), "--tenant", TENANT.name]) == 1


# ---------------------------------------------------------------------------
# local brand fonts
# ---------------------------------------------------------------------------

def _font_tenant(tmp_path):
    brand = tmp_path / "brand"
    (brand / "fonts-display").mkdir(parents=True)
    (brand / "fonts-display" / "wide.woff2").write_bytes(b"wOF2tiny-generated-font")

    class T:
        brand_dir = brand
        cfg = {"brand": {"font_stylesheet": "https://fonts.googleapis.com/css2?family=Body+Sans:wght@400&display=swap"}}

        def get(self, key, default=None):
            node = self.cfg
            for part in key.split("."):
                if not isinstance(node, dict) or part not in node:
                    return default
                node = node[part]
            return node

    return T()


PAGE_FACE = '@font-face{font-family:"Wide Face";font-weight:400;src:url("https://cdn.example/x/wide.woff2") format("woff2");}'


GOOGLE_CSS = """/* latin-ext */
@font-face {
  font-family: 'Body Sans';
  font-style: normal;
  font-weight: 400;
  src: url(https://fonts.gstatic.com/s/bodysans/v1/ext.woff2) format('woff2');
  unicode-range: U+0100-02BA;
}
/* latin */
@font-face {
  font-family: 'Body Sans';
  font-style: normal;
  font-weight: 400;
  src: url(https://fonts.gstatic.com/s/bodysans/v1/latin.woff2) format('woff2');
  unicode-range: U+0000-00FF;
}
/* cyrillic */
@font-face {
  font-family: 'Body Sans';
  font-style: normal;
  font-weight: 400;
  src: url(https://fonts.gstatic.com/s/bodysans/v1/cyr.woff2) format('woff2');
  unicode-range: U+0400-045F;
}
"""


def test_fonts_fetch_writes_files_manifest_and_licence(tmp_path):
    tenant = _font_tenant(tmp_path)
    seen = []

    def get(url, headers=None):
        seen.append(url)
        assert "Chrome" in (headers or {}).get("User-Agent", "")
        return GOOGLE_CSS.encode() if "googleapis" in url else b"wOF2" + url.encode()

    manifest = fonts.fetch_body_fonts(tenant, get=get, today="2026-10-07")
    folder = tenant.brand_dir / "fonts-body"
    assert {f["subset"] for f in manifest["faces"]} == {"latin", "latin-ext"}   # cyrillic skipped
    assert (folder / "body-sans-400-normal-latin.woff2").read_bytes().endswith(b"latin.woff2")
    assert json.loads((folder / "manifest.json").read_text())["licence"].startswith("SIL Open Font License")
    assert "Body+Sans/license" in (folder / "LICENCE.md").read_text()
    faces = fonts.body_faces(tenant)
    assert [f["family"] for f in faces] == ["Body Sans", "Body Sans"]
    assert faces[0]["unicode_range"] == "U+0100-02BA"
    with pytest.raises(fonts.FontFetchError):
        fonts.fetch_body_fonts(tenant, get=lambda url, headers=None: (_ for _ in ()).throw(OSError("offline")))


def test_localize_and_body_faces_in_both_modes_injected_before_the_page_css(tmp_path):
    tenant = _font_tenant(tmp_path)
    folder = tenant.brand_dir / "fonts-body"
    folder.mkdir()
    (folder / "body-400.woff2").write_bytes(b"wOF2body")
    (folder / "manifest.json").write_text(json.dumps({"faces": [
        {"family": "Body Sans", "weight": "400", "style": "normal", "file": "body-400.woff2"}]}))
    as_file = fonts.localize_font_faces(PAGE_FACE, tenant.brand_dir, "file")
    assert (tenant.brand_dir / "fonts-display" / "wide.woff2").resolve().as_uri() in as_file
    inline = fonts.localize_font_faces(PAGE_FACE, tenant.brand_dir, "inline")
    assert "data:font/woff2;base64," + base64.b64encode(b"wOF2tiny-generated-font").decode() in inline
    keep = '@font-face{font-family:"X";src:url("https://cdn.example/none.woff2")}'
    assert fonts.localize_font_faces(keep, tenant.brand_dir) == keep
    assert "file://" in fonts.body_font_css(tenant, "file") and "data:font/woff2" in fonts.body_font_css(tenant)
    html = f'<html><head><link rel="stylesheet" href="x.css"><style>{PAGE_FACE}</style></head><body></body></html>'
    staged = fonts.for_capture(html, tenant)
    assert staged.index("data-brand-fonts") < staged.index('<link rel="stylesheet"')
    assert "cdn.example" not in staged and fonts.inject(staged, "x") == staged


def test_review_html_inlines_local_fonts(tmp_path):
    tenant = _font_tenant(tmp_path)
    folder = tenant.brand_dir / "fonts-body"
    folder.mkdir()
    (folder / "body-400.woff2").write_bytes(b"wOF2body")
    (folder / "manifest.json").write_text(json.dumps({"faces": [
        {"family": "Body Sans", "weight": "400", "style": "normal", "file": "body-400.woff2"}]}))
    run_dir = tmp_path / "out" / "r1"
    (run_dir / "landing").mkdir(parents=True)
    (run_dir / "landing" / "index.html").write_text(f"<html><head><style>{PAGE_FACE}</style></head><body>x</body></html>")
    text = review.build_review_for_page(run_dir, "landing", tenant=tenant).read_text()
    assert "cdn.example" not in text and text.count("data:font/woff2;base64,") == 2
    assert "cdn.example" in (run_dir / "landing" / "index.html").read_text()


def test_cli_review_carries_the_local_display_face(monkeypatch):
    rc, run_dir, _ = _run(monkeypatch, "off", [])
    assert rc == 0
    assert cli.main(["review", str(run_dir), "--tenant", TENANT.name]) == 0
    text = (run_dir / "landing-review.html").read_text()
    assert "pk-peak-grotesk-wide.woff2" not in text and "data:font/woff2;base64," in text


def test_storefront_export_keeps_the_cdn_font_urls(monkeypatch):
    rc, run_dir, _ = _run(monkeypatch, "off", [])
    assert rc == 0
    tenant_mod.activate(TENANT)
    assert cli.main(["shopify-body", str(run_dir / "landing"), "--tenant", TENANT.name]) == 0
    body = (run_dir / "landing" / "shopify-body.html").read_text()
    assert "data-brand-fonts" not in body and "file://" not in body and "review-fonts" not in body
