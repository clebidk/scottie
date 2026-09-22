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
              "electrician adds.", []),
    "budget": ("Whatever the price, shipping is free and red light therapy comes standard.",
               ["gbrain-allowlist-free-shipping", "gbrain-allowlist-red-light"]),
}


def _filler(n, offset=0):
    words = " ".join(_FILLER_SENTENCES * 3).split()
    return " ".join(words[offset:offset + n])


def quiz_page(rubric=None):
    rubric = rubric or quiz.load_rubric(RUBRIC_PATH)[0]
    return {
        "headline": "Which home infrared sauna is right for busy parents?",
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


def test_rubric_generator_output_is_valid_and_matches_the_committed_questions(tmp_path):
    """build_rubric.py derives a valid rubric from today's verified claims,
    with the same questions and labels as the committed rubric.yaml. Scores
    are not compared: Caleb tunes the weights by hand in the committed file."""
    script = RUBRIC_PATH.parent / "build_rubric.py"
    work = tmp_path / "tenant"
    shutil.copytree(TENANT.claims_dir, work / "claims")
    (work / "quiz").mkdir()
    shutil.copy(script, work / "quiz" / "build_rubric.py")
    subprocess.run([sys.executable, str(work / "quiz" / "build_rubric.py")], check=True, capture_output=True)
    generated, error = quiz.load_rubric(work / "quiz" / "rubric.yaml")
    assert error is None
    assert quiz.validate_rubric(generated, _active_slugs(_facts_pack())) == []
    committed = quiz.load_rubric(RUBRIC_PATH)[0]

    def shape(r):
        return [(q["id"], [o["label"] for o in q["options"]]) for q in r["questions"]]

    assert shape(generated) == shape(committed)


# ---------------------------------------------------------------------------
# validate_rubric catches what a hand edit can break
# ---------------------------------------------------------------------------

def _rubric():
    return copy.deepcopy(quiz.load_rubric(RUBRIC_PATH)[0])


def _keys(problems):
    return {p.get("key") for p in problems}


def test_template_tenant_rubric_is_a_valid_example():
    rubric, error = quiz.load_rubric(quiz.rubric_path(REPO_ROOT / "tenants" / "_template"))
    assert error is None
    assert quiz.validate_rubric(rubric, ["model-one", "model-two", "model-three"]) == []


def test_fake_run_renders_a_quiz_page_offline():
    from evals import fake_run

    code, run_dir, pages = fake_run.run_once(
        str(TENANT.fixtures_dir / "founder-warranty-demo.txt"), cartridges="quiz", seed=42)
    assert code == 0
    assert [p.parent.name for p in pages] == ["quiz"]
    assert (run_dir / "quiz" / "index.html").exists()


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
    page["headline"] = "Which home infrared sauna is right for people?"
    assert "quiz:headline_slots" in _keys(_gate(page))
    page["headline"] = "Which Fuji is right for busy parents?"
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
        (" ".join(f"{s}:{v}" for s, v in sorted(o["scores"].items())), quiz.option_display(o))
        for q in rubric["questions"] for o in q["options"]
    ]
    assert [(s, label.replace("&#39;", "'").replace("&amp;", "&")) for s, label in rendered] == expected
    # cycle 61: every scored control is a real <button>, never a div or a link
    assert html.count("data-qz-scores=") == len(rendered)
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


def test_eyebrow_renders_only_when_the_tenant_sets_a_disclosure_label(tmp_path, monkeypatch):
    """Cycle 55 convention: no hardcoded label; the tenant's disclosure_label
    is the eyebrow when set, and nothing renders when it is unset."""
    monkeypatch.delitem(TENANT.config, "disclosure_label", raising=False)
    html, _ = _render(tmp_path / "unset")
    assert 'class="qz-eyebrow"' not in html  # cycle 61: the disclosure is the page's only eyebrow
    hero = html.split('<header class="qz-hero">', 1)[1].split("</header>", 1)[0]
    assert "qz-eyebrow" not in hero and "Advertisement" not in hero
    monkeypatch.setitem(TENANT.config, "disclosure_label", "Paid content")
    html, _ = _render(tmp_path / "set")
    assert '<p class="qz-eyebrow">Paid content</p>' in html


# ---------------------------------------------------------------------------
# Cycle 61: polish (owner review of run 20260922-213606: "a good start, but
# very rough") -- sentence case, short copy, a real product-finder card
# ---------------------------------------------------------------------------

def test_headline_is_sentence_case_with_no_quiz_suffix():
    page = quiz_page()
    assert quiz.find_headline_violations(page) == []
    page["headline"] = "Which Home Infrared Sauna Is Right for Busy Parents? Take the 60-Second Quiz"
    assert "quiz:headline_formula" in _keys(_gate(page))
    page["headline"] = "Which Home Infrared Sauna is right for busy parents?"
    assert "quiz:headline_case" in _keys(_gate(page))
    # an all-caps brand word is not title case
    page["headline"] = "Which home infrared sauna is right for NYC renters?"
    assert "quiz:headline_case" not in _keys(quiz.find_headline_violations(page))


def test_dek_and_interstitial_lengths_are_gated():
    page = quiz_page()
    page["dek"] = " ".join(["Answer"] * (quiz.DEK_MAX_WORDS + 1)) + "."
    assert "quiz:dek_length" in _keys(_gate(page))
    page = quiz_page()
    page["interstitials"][0]["line"] = " ".join(["heat"] * (quiz.INTERSTITIAL_MAX_WORDS + 1)) + "."
    assert "quiz:interstitial_length:0" in _keys(_gate(page))


def test_writer_lines_state_the_length_limits():
    lines = "\n".join(quiz.writer_lines(_facts_pack()))
    assert f"at most {quiz.DEK_MAX_WORDS} words" in lines
    assert f"at most {quiz.INTERSTITIAL_MAX_WORDS} words" in lines
    assert "Take the 60-Second Quiz" not in lines


def test_a_legacy_headline_displays_in_the_new_form():
    legacy = "Which Home Infrared Sauna Is Right for Apartment Dwellers? Take the 60-Second Quiz"
    assert quiz.display_headline(legacy) == "Which home infrared sauna is right for apartment dwellers?"
    current = "Which home infrared sauna is right for busy parents?"
    assert quiz.display_headline(current) == current
    assert quiz.display_headline("Which Home Sauna Is Right for NYC Renters? Take the 60-Second Quiz") == \
        "Which home sauna is right for NYC renters?"


def test_peak_rubric_option_text_is_short_and_every_question_has_a_result_label():
    rubric = quiz.load_rubric(RUBRIC_PATH)[0]
    for q in rubric["questions"]:
        assert q.get("result_label"), q["id"]
        for o in q["options"]:
            assert len(quiz.option_display(o).split()) <= quiz.OPTION_DISPLAY_MAX_WORDS, o["label"]


def test_rubric_display_text_over_the_limit_fails():
    rubric = _rubric()
    rubric["questions"][0]["options"][0]["display"] = "one two three four five six seven"
    keys = _keys(quiz.validate_rubric(rubric, _active_slugs(_facts_pack())))
    assert "quiz:rubric:option_display:household:0" in keys


def test_result_title_uses_the_brand_short_name_and_splits_the_descriptor():
    assert quiz.model_title("Peak Mini 1-Person Infrared Sauna", "Mini", "PEAK") == ("PEAK Mini", "1-Person Infrared Sauna")
    assert quiz.model_title("PEAK El Capitan 4-Person Outdoor Infrared Sauna", "El Capitan", "PEAK") == \
        ("PEAK El Capitan", "4-Person Outdoor Infrared Sauna")
    # no brand word in front: the name is shown whole, nothing is invented
    assert quiz.model_title("Mini 1-Person Infrared Sauna", "Mini", "PEAK") == ("Mini", "1-Person Infrared Sauna")
    assert quiz.model_title("Something Else", "Mini", "PEAK") == ("Something Else", None)


def test_rendered_result_cards_show_the_display_title_never_the_storefront_prefix(tmp_path):
    html, pack = _render(tmp_path)
    result = html.split('data-qz-result', 1)[1].split('class="qz-faq"', 1)[0]
    names = re.findall(r'<h3 class="qz-card-name">([^<]*)</h3>', result)
    assert len(names) == len(pack["quiz"]["models"])
    assert "PEAK Fuji" in names and "PEAK Mini" in names
    visible = re.sub(r"<[^>]+>", " ", result)
    assert not re.search(r"\bPeak Saunas\b", visible)
    assert not re.search(r"\bPeak (Fuji|Mini|Denali)\b", visible)
    assert "1-Person Infrared Sauna" in visible
    assert 'data-claim-id="price-fuji"' in result


def test_quiz_card_has_a_progress_indicator_back_and_a_live_region(tmp_path):
    html, pack = _render(tmp_path)
    n = len(pack["quiz"]["rubric"]["questions"])
    assert 'role="progressbar"' in html and f'aria-valuemax="{n}"' in html
    assert re.search(r'<p class="qz-count"[^>]*aria-live="polite"[^>]*>Question <span data-qz-num>1</span> of '
                     rf'{n}</p>', html)
    assert re.search(r'<button type="button" class="qz-back[^"]*" data-qz-back', html)
    hero = html.split('<header class="qz-hero">', 1)[1].split("</header>", 1)[0]
    assert f"{n} questions" in hero and "Start the quiz" in hero
    assert 'data-qz-start' in hero
    # every question and option carries what the "why it fits you" list needs
    assert html.count("data-qz-label=") == n


def test_no_uppercase_micro_labels_and_headings_stay_sentence_case(tmp_path):
    html, _ = _render(tmp_path)
    style = next(block for block in re.findall(r"<style>(.*?)</style>", html, re.DOTALL) if ".adv-quiz" in block)
    # the only uppercase rule is the tenant disclosure eyebrow (unset for Peak)
    assert style.count("text-transform:uppercase") == 1
    assert re.search(r"\.qz-eyebrow\{[^}]*text-transform:uppercase", style)
    assert "text-transform:none" in style  # overrides the tenant's uppercase headline rule
    for gone in ("qz-why-h", "Good to know</p>", "Your match</p>"):
        assert gone not in html
    assert "font-weight:700" not in style and "font-weight:600" not in style  # brand: one weight


def test_faq_is_a_native_details_accordion(tmp_path):
    page = quiz_page()
    html, _ = _render(tmp_path, page)
    faq = html.split('class="qz-faq"', 1)[1]
    assert faq.count('<details class="qz-faq-item"') == len(page["faq"]["questions"])
    assert faq.count('<summary class="qz-faq-q"') == len(page["faq"]["questions"])


def test_result_links_are_the_card_cta_and_the_tenant_all_models_page(tmp_path):
    html, pack = _render(tmp_path)
    assert '<a class="qz-link" href="/collections/all">See all models</a>' in html
    assert re.search(r'<button type="button" class="qz-restart qz-link"[^>]*data-qz-restart', html)
    assert not re.search(r"<script[^>]*\bsrc=", html)
