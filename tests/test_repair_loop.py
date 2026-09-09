"""Fix cycle 4 item 1/2/3/6: the writer repair loop, and the two new hard
gate checks (word range, CTA allowlist) that feed it. A page that fails a
page-level gate check gets a second (and third) chance with a "REVISION
REQUIRED" block before the run STOPs -- these tests exercise that directly
against adv.cli.write_and_gate_page, with a fake Anthropic client standing in
for the writer, rather than through the full `adv run` pipeline."""
from pathlib import Path

import pytest

from adv.budget import Budget
from adv.claims import ClaimsGateFailure
from adv.cli import (
    MAX_REPAIR_ATTEMPTS,
    find_cta_violation,
    find_word_range_violation,
    get_cta_text,
    write_and_gate_page,
)
from adv.log import RunLog
from adv.write import parse_word_range, resolve_allowed_cta_texts
from tests.conftest import FakeClient, json_response
from tests.test_render import AD_BRIEF, ARTICLE_PAGE, FACTS_PACK

REPO_ROOT = Path(__file__).resolve().parent.parent

# ARTICLE_PAGE (from test_render.py) is schema-valid, in the article word
# range (1,000-1,600), and its cta.text ("See the models") is already one of
# article's allowed_cta_texts -- it's the "good" page below. BAD_ARTICLE_PAGE
# is otherwise identical but its CTA text isn't on the allowlist, which is
# enough on its own to fail check_page_gates.
BAD_ARTICLE_PAGE = dict(ARTICLE_PAGE, cta={"text": "Buy now", "url": ARTICLE_PAGE["cta"]["url"]})


def _write_and_gate(responses, tmp_path):
    client = FakeClient(responses)
    budget = Budget()
    log = RunLog("test-run", tmp_path / "run.log")
    try:
        result = write_and_gate_page(
            cartridge_name="article",
            cartridges_dir=REPO_ROOT / "cartridges",
            ad_brief=AD_BRIEF,
            facts_pack=FACTS_PACK,
            client=client,
            model="claude-sonnet-5",
            budget=budget,
            log=log,
            financing_lender=None,
            speaker_pov=AD_BRIEF["speaker_pov"],
        )
    finally:
        log.close()
    return result, client


# ---------------------------------------------------------------------------
# fix cycle 4 item 1: repair loop happy path and exhaustion.
# ---------------------------------------------------------------------------

def test_repair_loop_recovers_after_one_failed_attempt(tmp_path):
    (page, attempts), client = _write_and_gate(
        [json_response(BAD_ARTICLE_PAGE), json_response(ARTICLE_PAGE)], tmp_path
    )
    assert page == ARTICLE_PAGE
    assert len(client.messages.calls) == 2
    assert len(attempts) == 2
    assert attempts[0] and attempts[1] == []  # attempt 1 failed, attempt 2 passed

    # the second call's user message carries the REVISION REQUIRED block
    # naming the CTA failure from the first attempt.
    second_user_msg = client.messages.calls[1]["messages"][0]["content"]
    assert "REVISION REQUIRED" in second_user_msg
    assert "Buy now" in second_user_msg


def test_repair_loop_stops_after_max_repair_attempts(tmp_path):
    # 1 initial + MAX_REPAIR_ATTEMPTS repairs, all failing -> STOP, never a
    # 4th call.
    responses = [json_response(BAD_ARTICLE_PAGE)] * (MAX_REPAIR_ATTEMPTS + 1)
    client = FakeClient(responses)
    budget = Budget()
    log = RunLog("test-run", tmp_path / "run.log")
    try:
        with pytest.raises(ClaimsGateFailure) as exc_info:
            write_and_gate_page(
                cartridge_name="article",
                cartridges_dir=REPO_ROOT / "cartridges",
                ad_brief=AD_BRIEF,
                facts_pack=FACTS_PACK,
                client=client,
                model="claude-sonnet-5",
                budget=budget,
                log=log,
                financing_lender=None,
                speaker_pov=AD_BRIEF["speaker_pov"],
            )
    finally:
        log.close()

    assert exc_info.value.stage == "page_json:article"
    assert len(client.messages.calls) == MAX_REPAIR_ATTEMPTS + 1
    assert len(exc_info.value.attempts) == MAX_REPAIR_ATTEMPTS + 1
    assert all(a for a in exc_info.value.attempts)  # every attempt still failing


# ---------------------------------------------------------------------------
# fix cycle 4 item 2: word-range gate check + cartridge.md parsing.
# ---------------------------------------------------------------------------

def test_parse_word_range_reads_each_real_cartridge_md():
    for name, expected in [("article", (1000, 1600)), ("longform", (800, 1400)), ("product-page", (250, 500))]:
        cartridge_md = (REPO_ROOT / "cartridges" / name / "cartridge.md").read_text()
        assert parse_word_range(cartridge_md) == expected


def test_parse_word_range_returns_none_without_a_match():
    assert parse_word_range("no word range mentioned here") is None


def test_find_word_range_violation_passes_within_range():
    assert find_word_range_violation(ARTICLE_PAGE, (1000, 1600)) == []


def test_find_word_range_violation_flags_too_short():
    short_page = {"open": [{"text": "Only a few words here."}]}
    problems = find_word_range_violation(short_page, (1000, 1600))
    assert len(problems) == 1
    assert "1000-1600" in problems[0]["issue"]


def test_find_word_range_violation_noop_without_a_range():
    assert find_word_range_violation({"open": [{"text": "short"}]}, None) == []


# ---------------------------------------------------------------------------
# fix cycle 4 item 3: CTA allowlist.
# ---------------------------------------------------------------------------

def test_resolve_allowed_cta_texts_substitutes_short_name():
    schema = {"allowed_cta_texts": ["Shop the {short_name}", "Buy the {short_name}"]}
    resolved = resolve_allowed_cta_texts(schema, "Peak Fuji 2-Person Infrared Sauna")
    assert resolved == [
        "Shop the Peak Fuji 2-Person Infrared Sauna",
        "Buy the Peak Fuji 2-Person Infrared Sauna",
    ]


def test_resolve_allowed_cta_texts_empty_without_the_schema_field():
    assert resolve_allowed_cta_texts({}, "Peak Fuji 2-Person Infrared Sauna") == []


def test_get_cta_text_reads_articles_nested_cta_and_others_top_level():
    assert get_cta_text({"cta": {"text": "See the models"}}, "article") == "See the models"
    assert get_cta_text({"cta_text": "See pricing"}, "longform") == "See pricing"
    assert get_cta_text({"cta_text": "See pricing"}, "product-page") == "See pricing"


def test_find_cta_violation_passes_for_an_allowed_text():
    assert find_cta_violation({"cta": {"text": "See the models"}}, "article", ["See the models", "Read the specs"]) == []


def test_find_cta_violation_flags_text_not_on_the_allowlist():
    problems = find_cta_violation({"cta_text": "Buy / Shop Peak Fuji 2-Person Infrared Sauna"}, "product-page",
                                   ["Shop the Peak Fuji 2-Person Infrared Sauna", "Buy the Peak Fuji 2-Person Infrared Sauna"])
    assert len(problems) == 1
    assert "Buy / Shop Peak Fuji 2-Person Infrared Sauna" in problems[0]["issue"]


def test_find_cta_violation_noop_without_an_allowlist():
    assert find_cta_violation({"cta_text": "anything"}, "product-page", []) == []
