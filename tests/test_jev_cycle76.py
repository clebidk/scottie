"""Cycle 76: best-of-N listicle drafts judged by TypeSafe's Jev
(harness/drafts.py, harness/jev.py). Every TypeSafe call here is faked: the
suite blocks sockets, and jev.api_key / jev._http_post are replaced per test.
"""
import argparse
import json
import re
import threading
import types

import pytest

from evals import fake_run
from harness import abtest, budget, cli, drafts, headlines, jev, listicle, runstate, site, skeletons
from tests.conftest import FakeResponse, block_text, json_response
from tests.support import TENANT, newest_run_dir
from tests.test_budget import _TenantDouble

FIXTURE = TENANT.fixtures_dir / "founder-warranty-demo.txt"
STYLE = "reasons"
PINNED_HEADLINE = headlines.legacy_id(STYLE)
_SKELETON_RE = re.compile(r'"skeleton": \{"id": "([^"]+)"')


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _good_page():
    return fake_run._listicle_page(STYLE)


def _bad_page():
    page = _good_page()
    page["reasons"][0]["text"] = ""  # listicle:item_words:1 -- no deterministic fix
    return page


def _other_good_page():
    """Passes the same gates; reads differently (audience order swapped), so a
    fake Jev can tell the two drafts apart."""
    page = _good_page()
    page["audience_fit"]["for_you"] = list(reversed(page["audience_fit"]["for_you"]))
    return page


class RoutedClient:
    """A fake Anthropic client for concurrent drafts: ingest/matcher calls are
    answered in order; a listicle write is answered from the queue of the
    draft whose skeleton id is in the request."""

    def __init__(self, by_skeleton):
        self.by_skeleton = {k: list(v) for k, v in by_skeleton.items()}
        brief = fake_run._brief_for(str(FIXTURE))
        self.plain = [json_response(brief)] + ([json_response({})] if brief.get("claims_made") else [])
        self.n_plain = len(self.plain)
        self.calls = []
        self._lock = threading.Lock()
        self.messages = types.SimpleNamespace(create=self.create)

    @staticmethod
    def skeleton_of(call):
        content = call["messages"][0]["content"]
        m = _SKELETON_RE.search(block_text(content) if isinstance(content, list) else content)
        return m.group(1) if m else None

    def writes_for(self, skeleton_id):
        return [c for c in self.calls if self.skeleton_of(c) == skeleton_id]

    def create(self, **kwargs):
        with self._lock:
            self.calls.append(kwargs)
            sk = self.skeleton_of(kwargs)
            queue = self.by_skeleton[sk] if sk else self.plain
            if not queue:
                raise AssertionError(f"no canned response left for {sk or 'ingest/matcher'}")
            item = queue.pop(0)
        return FakeResponse(json.dumps(item) if isinstance(item, dict) else item)


def _skeleton_ids():
    ranked = skeletons.ranked(STYLE, fake_run._brief_for(str(FIXTURE)), tenant=TENANT)
    return ranked[0]["id"], ranked[1]["id"]


class FakeJev:
    """Stands in for jev._http_post. `scores(page_text)` -> level per question."""

    def __init__(self, scores=None, status=200):
        self.bodies = []
        self.scores = scores or (lambda text: 2)
        self.status = status
        self._lock = threading.Lock()

    def __call__(self, body, key, timeout_s):
        with self._lock:
            self.bodies.append(body)
        assert key == "test-key"
        if self.status != 200:
            return self.status, '{"error": "boom"}'
        level = self.scores(body["state"]["page"])
        answers = {q: {"type": "score", "score": level, "confidence": 0.8} for q in body["questions"]}
        return 200, json.dumps({"answers": answers, "usage": {"input_tokens": 1000, "output_tokens": 50},
                                "model": "jev-test"})


@pytest.fixture
def two_drafts(monkeypatch):
    monkeypatch.setenv("HARNESS_JEV_DRAFTS", "2")
    monkeypatch.setattr(drafts, "STAGGER_S", 0)
    monkeypatch.setattr(jev, "api_key", lambda: "test-key")


def _run(client, *, seed=42, headline_template=PINNED_HEADLINE):
    args = argparse.Namespace(
        input=str(FIXTURE), cartridges="listicle", seed=seed, style=STYLE, headline_template=headline_template,
        skeleton=None, product=None, ffmpeg_bin="/usr/bin/ffmpeg", whisper_bin="/nonexistent/whisper-cli",
        whisper_model="/nonexistent/model.bin", tenant="peak-saunas", batch=False,
    )
    with fake_run.fake_environment(client):
        rc = cli.cmd_run(args)
    run_dir = newest_run_dir(TENANT.out_dir, "*-founder-warranty-demo-*")
    return rc, run_dir


def _jev_record(run_dir):
    return runstate.load_state(run_dir).get("jev")


def _log_text(run_dir):
    return (TENANT.runs_dir / f"{run_dir.name}.log").read_text()


# ---------------------------------------------------------------------------
# jev.py
# ---------------------------------------------------------------------------

def test_rubric_has_the_nine_validated_questions_and_the_composite_excludes_overall():
    rubric = jev.load_rubric(TENANT)
    assert list(rubric["questions"]) == ["hook", "specificity", "proof", "objections", "offer", "flow", "voice",
                                         "overall", "message_match"]
    assert rubric["composite"] == ["hook", "specificity", "proof", "objections", "offer", "flow", "voice",
                                   "message_match"]
    assert all(len(q["criteria"]) == 5 for q in rubric["questions"].values())
    assert rubric["questions"]["hook"]["instructions"].startswith("How strongly do the `page` headline")
    assert "`ad`" in rubric["questions"]["message_match"]["instructions"]
    assert rubric["model"] == "jev-latest"


def test_page_text_is_reading_order_copy_without_ids_or_urls():
    page = _good_page()
    page["cta_url"] = "https://example.com/products/x"
    text = jev.page_text(page)
    lines = text.splitlines()
    assert lines[0] == page["headline"] and lines[1] == page["dek"]
    assert lines[2].startswith("1. ") and page["reasons"][0]["heading"] in lines[2]
    assert text.index(page["reasons"][0]["heading"]) < text.index(page["reasons"][1]["heading"])
    assert text.index("Who this is for:") < text.index("FAQ") < text.index(page["closing"]["headline"])
    assert lines[-1] == f"[{page['cta_text']}]"
    for leak in ("asset-", "https://", "claim_ids", "example.com"):
        assert leak not in text


def test_request_body_matches_the_systemone_shape():
    brief = {"hook": "H", "promise": "P", "angle": "A", "claims_made": ["x"]}
    body = jev.request_body(_good_page(), brief, jev.load_rubric(TENANT))
    assert body["model"] == "jev-latest"
    assert body["state"]["ad"] == {"hook": "H", "promise": "P", "angle": "A"}
    assert isinstance(body["state"]["page"], str)
    assert body["questions"]["proof"]["type"] == "score"
    assert len(body["questions"]["proof"]["criteria"]) == 5


def test_score_page_normalises_levels_and_averages_the_composite():
    rubric = jev.load_rubric(TENANT)
    levels = {"hook": 4, "specificity": 2, "proof": 2, "objections": 2, "offer": 2, "flow": 2, "voice": 2,
              "overall": 0, "message_match": 2}

    def post(body, key, timeout_s):
        return 200, json.dumps({"answers": {q: {"score": levels[q], "confidence": 0.9} for q in body["questions"]},
                                "usage": {"input_tokens": 900, "output_tokens": 40}})

    res = jev.score_page(_good_page(), {}, rubric, key="k", post=post)
    assert res["scores"]["hook"] == 1.0 and res["scores"]["proof"] == 0.5
    assert res["overall"] == 0.0  # recorded, not in the composite
    assert res["composite"] == pytest.approx((1.0 + 0.5 * 7) / 8, abs=1e-4)
    assert res["usage"] == {"input_tokens": 900, "output_tokens": 40}


def test_score_page_retries_429_and_529_then_succeeds():
    statuses = [429, 529, 200]
    slept = []

    def post(body, key, timeout_s):
        status = statuses.pop(0)
        if status != 200:
            return status, "busy"
        return 200, json.dumps({"answers": {q: {"score": 1} for q in body["questions"]}})

    res = jev.score_page(_good_page(), {}, jev.load_rubric(TENANT), key="k", post=post, sleep=slept.append)
    assert slept == [1, 2]
    assert res["composite"] == 0.25


@pytest.mark.parametrize("post, match", [
    (lambda body, key, t: (500, "server error"), "HTTP 500"),
    (lambda body, key, t: (200, "not json"), "not JSON"),
    (lambda body, key, t: (200, json.dumps({"answers": {}})), "no score"),
])
def test_score_page_raises_jev_unavailable_on_errors(post, match):
    with pytest.raises(jev.JevUnavailable, match=match):
        jev.score_page(_good_page(), {}, jev.load_rubric(TENANT), key="k", post=post)


def test_no_key_and_network_timeout_raise_jev_unavailable(monkeypatch):
    with pytest.raises(jev.JevUnavailable, match="TYPESAFE_API_KEY"):
        jev.score_page(_good_page(), {}, jev.load_rubric(TENANT), key="")

    def timeout(*a, **k):
        raise TimeoutError("timed out")

    monkeypatch.setattr(jev.urllib.request, "urlopen", timeout)
    with pytest.raises(jev.JevUnavailable, match="TimeoutError"):
        jev._http_post({"x": 1}, "k", 1)


def test_settings_default_off_env_override_and_clamp(monkeypatch):
    monkeypatch.delenv("HARNESS_JEV_DRAFTS", raising=False)
    assert jev.settings(TENANT) == {"enabled": True, "drafts": 2, "timeout_s": 60.0}
    assert jev.settings(_TenantDouble()) == {"enabled": False, "drafts": 1, "timeout_s": 60}
    assert jev.drafts_for_run(TENANT, ["article"]) == 1
    assert jev.drafts_for_run(TENANT, ["listicle"]) == 2
    monkeypatch.setenv("HARNESS_JEV_DRAFTS", "1")
    assert jev.drafts_for_run(TENANT, ["listicle"]) == 1
    monkeypatch.setenv("HARNESS_JEV_DRAFTS", "9")
    assert jev.settings(TENANT)["drafts"] == jev.MAX_DRAFTS


# ---------------------------------------------------------------------------
# draft 2 is a different attempt
# ---------------------------------------------------------------------------

def _state_for_variants(seed=7, headline=None):
    from harness import ground

    brief = fake_run._brief_for(str(FIXTURE))
    source = ground.LocalFactsSource(TENANT.claims_dir)
    product, _warning = source.pick_product_with_warning(None, brief)
    facts = source.facts_for(product["slug"], brief, config=TENANT.claims_config, include_listicle=True)
    first = skeletons.ranked(STYLE, brief, tenant=TENANT)[0]
    plan = headline or headlines.resolve_plan(STYLE, facts, TENANT, seed=seed, today="2026-10-05")
    return types.SimpleNamespace(
        listicle_style=STYLE, listicle_headline=plan, listicle_skeleton=skeletons.for_writer(first, TENANT),
        ad_brief=brief, facts_pack=facts, tenant=TENANT, seed=seed, today_iso="2026-10-05",
    )


def test_draft_two_gets_another_headline_template_and_skeleton_reproducibly():
    state = _state_for_variants()
    v = drafts.variants(state, 2)
    assert v[0]["headline"]["id"] == state.listicle_headline["id"]
    assert v[1]["headline"]["id"] != v[0]["headline"]["id"]
    assert v[1]["headline"]["id"] in headlines.eligible_templates(STYLE, state.facts_pack, TENANT)
    assert v[1]["skeleton"]["id"] != v[0]["skeleton"]["id"]
    assert v[1]["skeleton"]["id"] == skeletons.ranked(STYLE, state.ad_brief, tenant=TENANT)[1]["id"]
    again = drafts.variants(_state_for_variants(), 2)
    assert [x["headline"]["id"] for x in again] == [x["headline"]["id"] for x in v]


def test_a_pinned_headline_template_or_skeleton_stays_pinned_in_draft_two():
    state = _state_for_variants()
    v = drafts.variants(state, 2, requested_headline=state.listicle_headline["id"])
    assert v[1]["headline"]["id"] == v[0]["headline"]["id"]
    v = drafts.variants(state, 2, requested_skeleton=state.listicle_skeleton["id"])
    assert v[1]["skeleton"]["id"] == v[0]["skeleton"]["id"]


def test_ranked_starts_with_what_select_picks():
    brief = fake_run._brief_for(str(FIXTURE))
    for style in listicle.STYLES:
        assert skeletons.ranked(style, brief, tenant=TENANT)[0]["id"] == skeletons.select(
            style, brief, tenant=TENANT)["id"]
        assert skeletons.ranked(style, None)[0]["id"] == skeletons.default_for_style(style)


# ---------------------------------------------------------------------------
# a whole run, two drafts
# ---------------------------------------------------------------------------

def test_both_pass_the_higher_composite_ships(two_drafts, monkeypatch):
    sk1, sk2 = _skeleton_ids()
    client = RoutedClient({sk1: [_good_page()], sk2: [_other_good_page()]})
    better = _other_good_page()["audience_fit"]["for_you"][0]["text"]
    fake = FakeJev(scores=lambda text: 4 if text.split("Who this is for:\n- ", 1)[1].startswith(better) else 1)
    monkeypatch.setattr(jev, "_http_post", fake)
    rc, run_dir = _run(client)
    assert rc == 0
    assert len(fake.bodies) == 2
    record = _jev_record(run_dir)
    assert record["status"] == "scored" and record["shipped"] == 2
    assert [d["gate"] for d in record["drafts"]] == ["PASS", "PASS"]
    assert record["drafts"][1]["composite"] == 1.0 and record["drafts"][0]["composite"] == 0.25
    assert record["drafts"][1]["scores"]["hook"] == 1.0
    assert record["usage"] == {"input_tokens": 2000, "output_tokens": 100}
    page = json.loads((run_dir / "listicle" / "page.json").read_text())
    assert page["audience_fit"]["for_you"][0]["text"] == better
    assert runstate.load_state(run_dir)["listicle"]["skeleton_id"] == sk2
    assert sorted(p.name for p in (run_dir / "drafts").iterdir()) == ["listicle-draft-1.json",
                                                                      "listicle-draft-2.json"]
    log = _log_text(run_dir)
    assert "write.listicle[draft 1]" in log and "write.listicle[draft 2]" in log
    assert "shipped draft 2" in log and "jev: usage input_tokens=2000" in log
    assert "test-key" not in log and "test-key" not in json.dumps(record)


def test_a_tie_ships_the_first_draft(two_drafts, monkeypatch):
    sk1, sk2 = _skeleton_ids()
    client = RoutedClient({sk1: [_good_page()], sk2: [_other_good_page()]})
    monkeypatch.setattr(jev, "_http_post", FakeJev(scores=lambda text: 3))
    rc, run_dir = _run(client)
    assert rc == 0
    record = _jev_record(run_dir)
    assert record["shipped"] == 1 and "tie" in record["reason"]


@pytest.mark.parametrize("failing", [1, 2])
def test_a_draft_that_fails_a_gate_never_ships_and_jev_is_not_asked(two_drafts, monkeypatch, failing):
    sk1, sk2 = _skeleton_ids()
    pages = {1: _good_page(), 2: _other_good_page()}
    pages[failing] = _bad_page()
    client = RoutedClient({sk1: [pages[1]], sk2: [pages[2]]})
    fake = FakeJev(scores=lambda text: 4)
    monkeypatch.setattr(jev, "_http_post", fake)
    rc, run_dir = _run(client)
    assert rc == 0
    assert fake.bodies == []
    record = _jev_record(run_dir)
    passing = 3 - failing
    assert record["shipped"] == passing and record["status"] == "single_pass"
    assert record["drafts"][failing - 1]["gate"] == "FAIL"
    assert any(f.startswith("listicle:item_words") for f in record["drafts"][failing - 1]["failures"])
    # no repair call for either draft: one draft passed on attempt 1
    assert len(client.writes_for(sk1)) == 1 and len(client.writes_for(sk2)) == 1
    page = json.loads((run_dir / "listicle" / "page.json").read_text())
    assert page["reasons"][0]["text"]


def test_none_pass_draft_one_takes_todays_repair_path(two_drafts, monkeypatch):
    sk1, sk2 = _skeleton_ids()
    client = RoutedClient({sk1: [_bad_page(), _good_page()], sk2: [_bad_page()]})
    fake = FakeJev()
    monkeypatch.setattr(jev, "_http_post", fake)
    rc, run_dir = _run(client)
    assert rc == 0
    assert fake.bodies == []
    assert len(client.writes_for(sk1)) == 2  # attempt 1 + one repair
    assert len(client.writes_for(sk2)) == 1  # never repaired
    assert "REVISION REQUIRED" in block_text(client.writes_for(sk1)[1]["messages"][0]["content"])
    record = _jev_record(run_dir)
    assert record["shipped"] == 1 and record["status"] == "repaired"
    assert "repair" in record["reason"]


def test_none_pass_and_repairs_fail_is_the_same_stop_as_today(two_drafts, monkeypatch):
    sk1, sk2 = _skeleton_ids()
    client = RoutedClient({sk1: [_bad_page()] * 3, sk2: [_bad_page()]})
    monkeypatch.setattr(jev, "_http_post", FakeJev())
    rc, run_dir = _run(client)
    assert rc == 2
    assert len(client.writes_for(sk1)) == 3 and len(client.writes_for(sk2)) == 1
    assert _jev_record(run_dir)["status"] == "none_passed"
    assert not (run_dir / "listicle" / "index.html").exists()


@pytest.mark.parametrize("mode", ["no_key", "http_500", "timeout"])
def test_jev_unavailable_ships_the_first_passing_draft_and_the_run_passes(two_drafts, monkeypatch, mode):
    sk1, sk2 = _skeleton_ids()
    client = RoutedClient({sk1: [_good_page()], sk2: [_other_good_page()]})
    if mode == "no_key":
        monkeypatch.setattr(jev, "api_key", lambda: "")
    elif mode == "http_500":
        monkeypatch.setattr(jev, "_http_post", FakeJev(status=500))
    else:
        def urlopen(*a, **k):
            raise TimeoutError("timed out")

        monkeypatch.setattr(jev.urllib.request, "urlopen", urlopen)
    rc, run_dir = _run(client)
    assert rc == 0
    record = _jev_record(run_dir)
    assert record["status"] == "jev_unavailable" and record["shipped"] == 1
    assert {"no_key": "TYPESAFE_API_KEY", "http_500": "HTTP 500", "timeout": "TimeoutError"}[mode] in record["reason"]
    assert (run_dir / "listicle" / "index.html").exists()
    assert "Jev unavailable" in _log_text(run_dir)


def test_one_draft_is_exactly_todays_run(monkeypatch):
    monkeypatch.setenv("HARNESS_JEV_DRAFTS", "1")
    sk1, _sk2 = _skeleton_ids()
    client = RoutedClient({sk1: [_good_page()]})
    rc, run_dir = _run(client)
    assert rc == 0
    assert len(client.writes_for(sk1)) == 1
    assert len(client.calls) == client.n_plain + 1
    assert "jev" not in runstate.load_state(run_dir)
    assert not (run_dir / "drafts").exists()
    log = _log_text(run_dir)
    assert "[draft" not in log and "listicle drafts:" not in log
    ledger = [json.loads(x) for x in budget.spend_ledger_path(TENANT).read_text().splitlines()]
    assert all("drafts" not in e for e in ledger if e["run_id"] == run_dir.name)


def test_the_spend_reservation_covers_two_drafts(two_drafts, monkeypatch):
    sk1, sk2 = _skeleton_ids()
    client = RoutedClient({sk1: [_good_page()], sk2: [_other_good_page()]})
    monkeypatch.setattr(jev, "api_key", lambda: "")
    rc, run_dir = _run(client)
    assert rc == 0
    lines = [json.loads(x) for x in budget.spend_ledger_path(TENANT).read_text().splitlines()]
    mine = [e for e in lines if e["run_id"] == run_dir.name]
    assert mine[0]["reserved_usd"] == pytest.approx(budget.DEFAULT_RESERVATION_USD * 2)
    assert mine[-1]["drafts"] == 2


def test_reservation_estimate_scales_by_drafts_and_reads_history_per_draft(tmp_path):
    bare = _TenantDouble(runs_dir=tmp_path / "bare")
    assert budget.reservation_estimate_usd(bare, 2) == pytest.approx(budget.DEFAULT_RESERVATION_USD * 2)
    configured = _TenantDouble(claims_config={"budget": {"per_run_usd": 0.3}}, runs_dir=tmp_path / "c")
    assert budget.reservation_estimate_usd(configured, 2) == pytest.approx(0.6)
    hist = _TenantDouble(runs_dir=tmp_path / "h")
    budget.record_spend(hist, run_id="a", cost=0.10, today_iso="2026-10-05")
    budget.record_spend(hist, run_id="b", cost=0.20, today_iso="2026-10-05", drafts=2)
    budget.record_spend(hist, run_id="c", cost=0.10, today_iso="2026-10-05")
    assert budget.reservation_estimate_usd(hist) == pytest.approx(0.10)
    assert budget.reservation_estimate_usd(hist, 2) == pytest.approx(0.20)


def test_abtest_builds_use_the_same_best_of_two_path(two_drafts, monkeypatch):
    sk1, sk2 = _skeleton_ids()
    client = RoutedClient({sk1: [_good_page()], sk2: [_other_good_page()]})
    monkeypatch.setattr(jev, "api_key", lambda: "")
    monkeypatch.setattr("harness.anthropic_client.make_client", lambda: client)
    arm = abtest.parse_arm(f"listicle:{STYLE}", TENANT)
    # default_runner has no --headline-template; pin the run's template so the
    # canned page's headline fits both drafts.
    monkeypatch.setattr(headlines, "eligible_templates", lambda *a, **k: [PINNED_HEADLINE])
    with fake_run.fake_environment(client):
        rc, run_dir = abtest.default_runner(TENANT, str(FIXTURE), arm, 42)
    assert rc == 0
    assert len(client.writes_for(sk1)) == 1 and len(client.writes_for(sk2)) == 1
    assert len(_jev_record(run_dir)["drafts"]) == 2


# ---------------------------------------------------------------------------
# the review site
# ---------------------------------------------------------------------------

def test_generation_page_shows_the_jev_table():
    state = {"jev": {
        "shipped": 2, "status": "scored", "reason": "draft 2 composite 0.700 vs draft 1 0.500",
        "usage": {"input_tokens": 2000, "output_tokens": 100},
        "drafts": [
            {"draft": 1, "gate": "PASS", "headline_template_id": "h01", "skeleton_id": "classic-n-reasons",
             "scores": {"hook": 0.5, "overall": 0.25}, "composite": 0.5},
            {"draft": 2, "gate": "PASS", "headline_template_id": "h04", "skeleton_id": "switcher-reasons",
             "scores": {"hook": 0.75, "overall": 0.5}, "composite": 0.7},
        ],
    }}
    html = site._jev_card(state, "listicle")
    assert "<table" in html and "Composite" in html and "Ad match" in html
    assert "2 <b>shipped</b>" in html
    assert "0.75" in html and "0.70" in html and "switcher-reasons" in html
    assert "2000 in, 100 out" in html
    assert site._jev_card(state, "article") == ""
    assert site._jev_card({}, "listicle") == ""
