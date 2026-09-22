"""Cycle 57: the quiz cartridge.

The scoring rubric is tenant data (tenants/<t>/quiz/rubric.yaml); these tests
prove the Peak rubric is valid and complete (every combination of answers
recommends an active model, every active model can be recommended), that the
gates catch what the writer can break, and that the rendered page carries one
self-contained script and one card per active model.

quiz_page() builds the canned writer output the fake-client driver
(evals/fake_run.py) and the end-to-end test share, from the tenant's own
rubric, so it can never drift from the rubric the gate checks against.
"""
import copy
import itertools
import json
import re
import shutil
import subprocess
import sys

import pytest

from harness import quiz
from harness import repair
from harness import simplicity
from harness.claims import gate_page_json
from harness.ground import LocalFactsSource
from harness.page_body import build_shopify_body
from harness.render import render_page
from tests.conftest import FakeClient, json_response
from tests.support import REPO_ROOT, TENANT, newest_run_dir
from tests.test_render import AD_BRIEF, _FILLER_SENTENCES

FUJI_SLUG = "peak-saunas-fuji-2-person-indoor-near-zero-emf-full-spectrum-infrared-sauna-with-medical-grade-red-light-therapy"
FUJI_URL = f"https://peaksaunas.com/products/{FUJI_SLUG}"
RUBRIC_PATH = quiz.rubric_path(TENANT.root)
OUTDOOR = {"patagonia", "kilimanjaro", "el-capitan"}

_INTERSTITIAL_LINES = {
    "placement": ("Every model here is full spectrum, with heater placement all around the cabin.",
                  ["gbrain-allowlist-360-full-spectrum"]),
    "power": ("Some cabins plug into an ordinary wall outlet; others need a dedicated circuit that an "
              "electrician adds, so this answer narrows the list more than any other.", []),
    "budget": ("Whatever the price, shipping is free and red light therapy comes standard.",
               ["gbrain-allowlist-free-shipping", "gbrain-allowlist-red-light"]),
}


def _filler(n, offset=0):
    words = " ".join(_FILLER_SENTENCES * 3).split()
    return " ".join(words[offset:offset + n])


def quiz_page(rubric=None):
    rubric = rubric or quiz.load_rubric(RUBRIC_PATH)[0]
    return {
        "headline": "Which Home Infrared Sauna Is Right for Busy Parents? Take the 60-Second Quiz",
        "dek": "Answer a few plain questions and see the one model that fits your home, your outlet and your budget.",
        "hero": {"asset_id": f"asset-{FUJI_SLUG}-1"},
        "questions": [
            {"id": q["id"], "prompt": q["prompt"], "options": [o["label"] for o in q["options"]]}
            for q in rubric["questions"]
        ],
        "interstitials": [
            {"after": e["after"], "line": _INTERSTITIAL_LINES[e["after"]][0],
             "claim_ids": _INTERSTITIAL_LINES[e["after"]][1]}
            for e in rubric["interstitials"]
        ],
        "faq": {"questions": [
            {"question": f"What should a careful buyer check before choosing, part {i}?",
             "answer": _filler(70, offset=i * 23)}
            for i in range(1, 5)
        ]},
        "cta_text": "Shop the Fuji",
        "cta_url": FUJI_URL,
    }


def _facts_pack():
    return LocalFactsSource(TENANT.claims_dir).facts_for(FUJI_SLUG, AD_BRIEF, include_quiz=True)


def _active_slugs(pack):
    return [m["slug"] for m in pack["quiz"]["models"]]


# ---------------------------------------------------------------------------
# The Peak rubric: valid, complete, placement acts as a filter
# ---------------------------------------------------------------------------

def test_peak_rubric_validates_against_the_active_models():
    pack = _facts_pack()
    assert quiz.validate_rubric(pack["quiz"]["rubric"], _active_slugs(pack)) == []


def test_every_answer_combination_recommends_an_active_model_and_every_model_can_win():
    pack = _facts_pack()
    slugs = set(_active_slugs(pack))
    wins, empty, total = quiz.rubric_reachability(pack["quiz"]["rubric"], slugs)
    expected_total = 1
    for q in pack["quiz"]["rubric"]["questions"]:
        expected_total *= len(q["options"])
    assert total == expected_total
    assert empty == 0
    assert set(wins) == slugs
    assert sum(wins.values()) == total


def test_placement_answer_never_returns_a_model_for_the_other_placement():
    rubric = quiz.load_rubric(RUBRIC_PATH)[0]
    questions = rubric["questions"]
    placement_index = [q["id"] for q in questions].index("placement")
    for combo in itertools.product(*[q["options"] for q in questions]):
        winner = quiz.pick_model(combo, rubric["tiebreak"])
        outside = combo[placement_index]["label"].lower().startswith("outside")
        assert (winner in OUTDOOR) == outside, (combo[placement_index]["label"], winner)


def test_retired_models_never_appear():
    pack = _facts_pack()
    assert "crown" not in _active_slugs(pack)
    text = RUBRIC_PATH.read_text()
    assert not re.search(r"\bcrown\b", text)


def test_rubric_generator_reproduces_the_committed_rubric(tmp_path):
    """The committed rubric.yaml is exactly what build_rubric.py derives from
    the verified claims today -- a hand edit shows up here as a diff, which
    is the point: re-derive, or update this test with the reason."""
    script = RUBRIC_PATH.parent / "build_rubric.py"
    work = tmp_path / "tenant"
    shutil.copytree(TENANT.claims_dir, work / "claims")
    (work / "quiz").mkdir()
    shutil.copy(script, work / "quiz" / "build_rubric.py")
    subprocess.run([sys.executable, str(work / "quiz" / "build_rubric.py")], check=True, capture_output=True)
    assert (work / "quiz" / "rubric.yaml").read_text() == RUBRIC_PATH.read_text()


# ---------------------------------------------------------------------------
# validate_rubric catches what a hand edit can break
# ---------------------------------------------------------------------------

def _rubric():
    return copy.deepcopy(quiz.load_rubric(RUBRIC_PATH)[0])


def _keys(problems):
    return {p.get("key") for p in problems}


def test_rubric_missing_file_is_a_load_problem(tmp_path):
    rubric, error = quiz.load_rubric(tmp_path / "nope.yaml")
    assert rubric is None
    assert _keys(quiz.validate_rubric(rubric, ["a"], load_error=error)) == {"quiz:rubric:load"}


def test_rubric_scoring_a_retired_model_fails():
    pack = _facts_pack()
    rubric = _rubric()
    rubric["questions"][0]["options"][0]["scores"]["crown"] = 3
    keys = _keys(quiz.validate_rubric(rubric, _active_slugs(pack)))
    assert "quiz:rubric:unknown_model:household:0" in keys


def test_rubric_option_that_scores_nobody_fails():
    pack = _facts_pack()
    rubric = _rubric()
    rubric["questions"][0]["options"][0]["scores"] = {}
    assert "quiz:rubric:option_scores:household:0" in _keys(quiz.validate_rubric(rubric, _active_slugs(pack)))


def test_rubric_with_an_unreachable_model_fails():
    pack = _facts_pack()
    rubric = _rubric()
    for q in rubric["questions"]:
        for o in q["options"]:
            o["scores"].pop("shasta", None)
    rubric["tiebreak"].remove("shasta")
    # every option still scores someone, so only reachability can catch it
    keys = _keys(quiz.validate_rubric(rubric, _active_slugs(pack)))
    assert "quiz:rubric:unreachable:shasta" in keys


def test_rubric_with_a_combination_that_scores_nothing_fails():
    def rubric_with(q0a, q1a):
        questions = [
            {"id": f"q{i}", "prompt": "Which?", "options": [
                {"label": "a", "scores": {"x": 1}}, {"label": "b", "scores": {"y": 1}},
            ]}
            for i in range(5)
        ]
        questions[0]["options"][0]["scores"] = q0a
        questions[1]["options"][0]["scores"] = q1a
        return {"questions": questions, "tiebreak": ["x", "y"]}

    assert quiz.validate_rubric(rubric_with({"x": 1}, {"x": 1}), ["x", "y"]) == []
    # answers (a, a, b, b, b) now total x = -19, y = -16: nobody scores
    broken = rubric_with({"y": 1, "x": -20}, {"x": 1, "y": -20})
    assert "quiz:rubric:no_result" in _keys(quiz.validate_rubric(broken, ["x", "y"]))


def test_rubric_counts_are_bounded():
    rubric = _rubric()
    rubric["questions"] = rubric["questions"][:4]
    assert "quiz:rubric:question_count" in _keys(quiz.validate_rubric(rubric, ["mini"]))


def test_pick_model_tie_goes_to_the_tiebreak_order_then_the_slug():
    a = {"scores": {"b": 2, "a": 2, "c": 1}}
    assert quiz.pick_model([a], ["b", "a"]) == "b"
    assert quiz.pick_model([a], []) == "a"
    assert quiz.pick_model([{"scores": {"a": 0}}], []) is None


# ---------------------------------------------------------------------------
# Grounding
# ---------------------------------------------------------------------------

def test_quiz_models_are_active_priced_and_their_claims_are_citable():
    pack = _facts_pack()
    models = pack["quiz"]["models"]
    ids = {c["id"]: c for c in pack["verified_claims"]}
    assert len(models) == 10
    for m in models:
        assert m["price_claim_id"] in ids
        assert m["price_text"] in ids[m["price_claim_id"]]["text"]
        assert m["capacity_claim_id"] in ids
        assert m["url"].startswith("https://peaksaunas.com/products/")
        assert m["image"]["url"].startswith("https://")
        assert not m["name"].lower().startswith("peak saunas")
    assert pack["quiz"]["featured"] == "fuji"


def test_no_quiz_block_without_the_flag():
    assert "quiz" not in LocalFactsSource(TENANT.claims_dir).facts_for(FUJI_SLUG, AD_BRIEF)


# ---------------------------------------------------------------------------
# The page gate
# ---------------------------------------------------------------------------

def _gate(page, pack=None):
    pack = pack or _facts_pack()
    schema, word_range, allowed = repair.cartridge_write_constraints(
        "quiz", REPO_ROOT / "cartridges", pack, AD_BRIEF, TENANT)
    return repair.check_page_gates(
        page, pack, "quiz", financing_lender="Bread Pay", speaker_pov="first_person",
        word_range=word_range, allowed_cta_texts=allowed, ad_brief=AD_BRIEF, tenant=TENANT,
    )


def test_canned_quiz_page_passes_every_gate():
    page = quiz_page()
    assert _gate(page) == []
    assert 400 <= repair.count_words(page) <= 700
    gate_page_json(page, _facts_pack(), "quiz", financing_lender="Bread Pay", speaker_pov="first_person", ad_brief=AD_BRIEF)


def test_headline_formula_and_slots():
    page = quiz_page()
    page["headline"] = "Find Your Perfect Sauna Today"
    assert "quiz:headline_formula" in _keys(_gate(page))
    page["headline"] = "Which Home Infrared Sauna Is Right for People? Take the 60-Second Quiz"
    assert "quiz:headline_slots" in _keys(_gate(page))
    page["headline"] = "Which Fuji Is Right for Busy Parents? Take the 60-Second Quiz"
    assert "quiz:headline_slots" in _keys(_gate(page))


def test_question_count_and_option_order_are_gated_and_options_are_fixed_deterministically():
    pack = _facts_pack()
    page = quiz_page()
    page["questions"] = page["questions"][:-1]
    assert "quiz:question_count" in _keys(_gate(page, pack))

    page = quiz_page()
    page["questions"][0]["options"] = list(reversed(page["questions"][0]["options"]))
    page["questions"][1]["id"] = "where"
    page["interstitials"][0]["after"] = "space"
    page["cta_url"] = "https://peaksaunas.com/collections/all"
    problems = _gate(page, pack)
    keys = _keys(problems)
    assert {"quiz:options:household", "quiz:question_id:1", "quiz:interstitial_after:0", "quiz:cta_url"} <= keys
    fixed = repair.apply_deterministic_fixes(page, problems, {c["id"] for c in pack["verified_claims"]},
                                             cartridge_name="quiz", facts_pack=pack)
    assert fixed >= 4
    assert _gate(page, pack) == []


def test_interstitial_with_a_number_needs_a_claim_id():
    page = quiz_page()
    page["interstitials"][1] = {"after": "power", "line": "Most cabins draw about 1,800 watts.", "claim_ids": []}
    assert "quiz:interstitial_claims:1" in _keys(_gate(page))


def test_renderer_owned_keys_and_offer_language_are_rejected():
    page = quiz_page()
    page["models"] = [{"name": "x"}]
    page["dek"] = "Take the quiz now -- limited time, and a discount waits at the end."
    keys = _keys(_gate(page))
    assert "quiz:renderer_owned:models" in keys
    assert "quiz:discount" in keys
    assert "quiz:urgency:limited time" in keys


def test_faq_count_is_gated():
    page = quiz_page()
    page["faq"]["questions"] = page["faq"]["questions"][:2]
    assert "quiz:faq_count" in _keys(_gate(page))


def test_writer_lines_quote_the_rubric():
    lines = "\n".join(quiz.writer_lines(_facts_pack()))
    assert quiz.HEADLINE_FORMULA in lines
    rubric = quiz.load_rubric(RUBRIC_PATH)[0]
    for q in rubric["questions"]:
        assert f'id "{q["id"]}"' in lines
        for o in q["options"]:
            assert o["label"] in lines


def test_simplicity_quiz_fold_has_no_page_json_link_and_headline_in_band():
    page = quiz_page()
    assert simplicity.find_simplicity_violations(page, "quiz") == []


# ---------------------------------------------------------------------------
# Render
# ---------------------------------------------------------------------------

def _render(tmp_path, page=None):
    pack = _facts_pack()
    index = render_page(
        cartridge_name="quiz", page=page or quiz_page(), ad_brief=AD_BRIEF, facts_pack=pack,
        cartridges_dir=REPO_ROOT / "cartridges", brand_dir=TENANT.brand_dir,
        templates_dir=REPO_ROOT / "harness" / "templates", out_dir=tmp_path / "quiz",
        published="2026-09-22", updated="2026-09-22", download_assets=False, tenant=TENANT,
    )
    return index.read_text(), pack


def test_render_one_card_per_active_model_and_only_the_featured_one_visible(tmp_path):
    html, pack = _render(tmp_path)
    cards = re.findall(r'<article class="qz-card" data-qz-model="([^"]+)"([^>]*)>', html)
    assert [slug for slug, _ in cards] == _active_slugs(pack)
    visible = [slug for slug, attrs in cards if "hidden" not in attrs]
    assert visible == ["fuji"]
    assert "data-qz-featured" in dict(cards)["fuji"]
    for m in pack["quiz"]["models"]:
        assert m["price_text"] in html
        assert f'href="{m["url"]}"' in html
    assert ">Shop the Fuji<" in html
    assert ">Shop the Kilimanjaro<" in html
    assert '"@type": "FAQPage"' in html


def test_render_questions_are_buttons_carrying_the_rubric_scores(tmp_path):
    html, pack = _render(tmp_path)
    rubric = pack["quiz"]["rubric"]
    assert len(re.findall(r'data-qz-question="', html)) == len(rubric["questions"])
    rendered = re.findall(r'<button type="button" class="qz-option"[^>]*data-qz-scores="([^"]*)">([^<]*)</button>', html)
    expected = [
        (" ".join(f"{s}:{v}" for s, v in sorted(o["scores"].items())), o["label"])
        for q in rubric["questions"] for o in q["options"]
    ]
    assert [(s, label.replace("&#39;", "'").replace("&amp;", "&")) for s, label in rendered] == expected
    # the only links above the result are the start anchor(s); options are never links
    assert not re.search(r"<a[^>]*data-qz-scores", html)
    assert html.count('href="#qz-quiz"') == 2


def test_render_script_is_inline_self_contained_and_sources_cite_the_cards(tmp_path):
    html, pack = _render(tmp_path)
    assert quiz.find_rendered_quiz_violations(html, {"cards": pack["quiz"]["models"],
                                                     "question_count": len(pack["quiz"]["rubric"]["questions"])}) == []
    assert not re.search(r"<script[^>]*\bsrc=", html)
    sources = html.split('class="adv-sources"', 1)[1]
    assert "price-kilimanjaro" not in re.sub(r"<[^>]+>", " ", html.split("<body>", 1)[1])  # never visible copy
    assert sources.count("<li>") >= 3
    assert "Financing is available through Bread Pay at checkout." in html
    assert "Limited lifetime warranty" in html


def test_rendered_check_catches_an_external_script_and_a_missing_card(tmp_path):
    html, pack = _render(tmp_path)
    ctx = {"cards": pack["quiz"]["models"], "question_count": len(pack["quiz"]["rubric"]["questions"])}
    bad = html.replace("</body>", '<script src="https://example.com/x.js"></script></body>')
    assert "quiz:script_external" in _keys(quiz.find_rendered_quiz_violations(bad, ctx))
    bad = re.sub(r'data-qz-model="shasta"', 'data-qz-model="crown"', html)
    assert "quiz:result_cards" in _keys(quiz.find_rendered_quiz_violations(bad, ctx))


def test_script_survives_the_storefront_export(tmp_path):
    html, _ = _render(tmp_path)
    script = re.search(r"<script>\s*\(function\(\)\{.*?</script>", html, re.DOTALL).group(0)
    assert len(script) > 3000
    body, _manifest = build_shopify_body(tmp_path / "quiz")
    assert script in body
    assert "data-qz-tiebreak" in body


@pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
def test_script_parses_as_javascript(tmp_path):
    html, _ = _render(tmp_path)
    body = re.search(r"<script>\s*(\(function\(\)\{.*?)</script>", html, re.DOTALL).group(1)
    path = tmp_path / "quiz.js"
    path.write_text(body)
    subprocess.run(["node", "--check", str(path)], check=True, capture_output=True)


# ---------------------------------------------------------------------------
# End to end (fake client)
# ---------------------------------------------------------------------------

def _run(monkeypatch, responses):
    from harness import cli
    from tests.test_cli_run import _base_args, _patch_network

    client = FakeClient(responses)
    monkeypatch.setattr(cli, "make_client", lambda: client)
    _patch_network(monkeypatch)
    code = cli.cmd_run(_base_args(input=str(TENANT.fixtures_dir / "founder-warranty-demo.txt"), cartridges="quiz"))
    return code, client


def test_run_quiz_cartridge_end_to_end(monkeypatch):
    code, _client = _run(monkeypatch, [
        json_response(dict(AD_BRIEF, source_file="founder-warranty-demo.txt", audience="")),
        json_response(quiz_page()),
    ])
    assert code == 0
    run_dir = newest_run_dir(TENANT.out_dir, "*-founder-warranty-demo-*")
    facts_pack = json.loads((run_dir / "facts_pack.json").read_text())
    assert facts_pack["quiz"]["featured"] == "fuji"
    html = (run_dir / "quiz" / "index.html").read_text()
    assert "data-qz-tiebreak" in html


def test_an_invalid_rubric_stops_the_run_before_any_writer_call(monkeypatch, tmp_path):
    bad = _rubric()
    bad["questions"][0]["options"][0]["scores"]["crown"] = 3
    path = tmp_path / "rubric.yaml"
    path.write_text(json.dumps(bad))
    monkeypatch.setattr(quiz, "rubric_path", lambda root: path)
    code, client = _run(monkeypatch, [
        json_response(dict(AD_BRIEF, source_file="founder-warranty-demo.txt", audience="")),
    ])
    assert code == 2
    assert len(client.messages.calls) == 1  # ingest only -- no write call
    run_dir = newest_run_dir(TENANT.out_dir, "*-founder-warranty-demo-*")
    stop = json.loads((run_dir / "unmatched_claims.json").read_text())
    assert stop["stage"] == "quiz_rubric"
    assert any(i["key"].startswith("quiz:rubric:unknown_model") for i in stop["items"])
