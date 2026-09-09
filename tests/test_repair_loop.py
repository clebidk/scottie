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
    apply_deterministic_fixes,
    apply_hype_synonyms,
    build_revision_note,
    find_cta_violation,
    find_word_range_violation,
    get_cta_text,
    write_and_gate_page,
)
from adv.log import RunLog
from adv.vocab import ALWAYS_FORBIDDEN_TERMS
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
    (page, attempts, deterministic_fixes), client = _write_and_gate(
        [json_response(BAD_ARTICLE_PAGE), json_response(ARTICLE_PAGE)], tmp_path
    )
    assert page == ARTICLE_PAGE
    assert len(client.messages.calls) == 2
    assert len(attempts) == 2
    assert attempts[0] and attempts[1] == []  # attempt 1 failed, attempt 2 passed
    assert deterministic_fixes == [0, 0]  # BAD_ARTICLE_PAGE's CTA violation isn't deterministically fixable

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
    assert exc_info.value.deterministic_fixes == [0] * (MAX_REPAIR_ATTEMPTS + 1)


# ---------------------------------------------------------------------------
# fix cycle 4 item 2: word-range gate check + cartridge.md parsing.
# ---------------------------------------------------------------------------

def test_parse_word_range_reads_each_real_cartridge_md():
    for name, expected in [("article", (1000, 1600)), ("longform", (800, 1400)), ("product-page", (250, 500))]:
        cartridge_md = (REPO_ROOT / "cartridges" / name / "cartridge.md").read_text()
        assert parse_word_range(cartridge_md) == expected


def test_real_cartridge_md_files_have_exactly_one_word_range_pattern():
    # Regression: cycle 4's per-paragraph/step/answer length guidance (added
    # after a real run undershot its word range) is phrased as "N to M
    # words", not "N-M words", specifically so it can never collide with
    # parse_word_range's regex, which takes the *first* "N-M words" match in
    # the file -- a second match earlier in the file (e.g. inside the
    # Structure section, which comes before the Rules section's real range)
    # would silently parse the wrong range instead of the cartridge's actual
    # one.
    import re
    word_range_pattern = re.compile(r"[\d,]+[–-][\d,]+ *words")
    for name in ("article", "longform", "product-page"):
        cartridge_md = (REPO_ROOT / "cartridges" / name / "cartridge.md").read_text()
        assert len(word_range_pattern.findall(cartridge_md)) == 1


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


# ---------------------------------------------------------------------------
# fix cycle 5: build_revision_note calls out a leaked-claim-id failure
# specifically -- observed recurring across repair attempts on the real
# hidden-costs-v2 verification run because the rewrite moved the same
# parenthetical id onto a different sentence instead of deleting it.
# ---------------------------------------------------------------------------

def test_build_revision_note_calls_out_leaked_claim_id_failures():
    failures = [{
        "path": "$.close.paragraphs[0].text",
        "issue": "claim id leaked into copy: spec-fuji-capacity in $.close.paragraphs[0].text",
        "text": "... (spec-fuji-capacity) ...",
    }]
    note = build_revision_note(1, failures)
    assert "Delete the id from that sentence" in note
    assert "do not move the same id onto another sentence" in note


def test_build_revision_note_omits_leaked_claim_id_guidance_when_not_applicable():
    failures = [{"path": "$.hero.text", "term": "unlock", "issue": "forbidden term 'unlock' found"}]
    note = build_revision_note(1, failures)
    assert "Delete the id from that sentence" not in note


# ---------------------------------------------------------------------------
# Fix cycle 6 item 3: the forbidden-word list, verbatim, at the top of the
# REVISION REQUIRED block, plus the closing "fix all of these" instruction.
# ---------------------------------------------------------------------------

def test_build_revision_note_includes_forbidden_word_list_verbatim():
    note = build_revision_note(1, [{"path": "$.hero.text", "term": "unlock", "issue": "forbidden term 'unlock' found"}])
    for word in ALWAYS_FORBIDDEN_TERMS:
        assert word in note


def test_build_revision_note_ends_with_the_required_instruction_sentence():
    note = build_revision_note(1, [{"path": "$.hero.text", "issue": "some issue"}])
    assert note.strip().endswith(
        "Fix all of these. Do not introduce any new violation. Before answering, re-read the "
        "forbidden word list and remove every occurrence."
    )


def test_write_and_gate_page_revision_note_carries_forward_failures_from_earlier_attempts(tmp_path):
    # attempt 1 fails on a bad CTA; attempt 2's rewrite fixes the CTA but
    # introduces an unrelated EMF violation instead (fix A, break B --
    # observed for real on the hidden-costs-v2 verification run). The third
    # attempt's REVISION REQUIRED block must still mention attempt 1's
    # original CTA failure alongside attempt 2's EMF failure, deduplicated,
    # not just the most recent attempt's.
    attempt1_bad = BAD_ARTICLE_PAGE  # bad CTA ("Buy now")
    attempt2_bad = dict(ARTICLE_PAGE, open=[{"text": "This sauna avoids EMF entirely."}])  # good CTA, new EMF hit
    attempt3_good = ARTICLE_PAGE

    (page, attempts, deterministic_fixes), client = _write_and_gate(
        [json_response(attempt1_bad), json_response(attempt2_bad), json_response(attempt3_good)], tmp_path
    )
    assert page == ARTICLE_PAGE
    assert len(client.messages.calls) == 3

    third_user_msg = client.messages.calls[2]["messages"][0]["content"]
    assert "REVISION REQUIRED" in third_user_msg
    assert "Buy now" in third_user_msg  # attempt 1's CTA failure, still carried forward
    assert "emf" in third_user_msg.lower()  # attempt 2's EMF failure


# ---------------------------------------------------------------------------
# Fix cycle 6 item 1: deterministic pre-repair pass. A safe text
# substitution (hype-word synonym, leaked-claim-id parenthetical) is applied
# -- no model call -- and the gate re-run, before ever building a REVISION
# REQUIRED prompt. Direct unit tests for apply_hype_synonyms/
# apply_deterministic_fixes are in test_write.py's neighbor... actually kept
# here alongside the loop they feed.
# ---------------------------------------------------------------------------

def test_apply_hype_synonyms_is_case_preserving_and_whole_word():
    assert apply_hype_synonyms("Unlock the price now.") == "Get the price now."
    assert apply_hype_synonyms("nothing to unlock here") == "nothing to get here"
    assert apply_hype_synonyms("This is a real game-changer!") == "This is a real big improvement."
    # not a substring match: "lock" inside "unlock" only, never "lock" alone
    assert apply_hype_synonyms("The lock on the door held.") == "The lock on the door held."


def test_apply_deterministic_fixes_resolves_a_hype_word_failure():
    page = {"hero": {"text": "Nothing to unlock here."}}
    failures = [{"path": "$.hero.text", "term": "unlock", "issue": "forbidden term 'unlock' found", "text": page["hero"]["text"]}]
    fixed = apply_deterministic_fixes(page, failures, set())
    assert fixed == 1
    assert page["hero"]["text"] == "Nothing to get here."


def test_apply_deterministic_fixes_removes_leaked_claim_id_parenthetical():
    page = {"hero": {"text": "Priced at $8,250 (price-fuji)."}}
    failures = [{
        "path": "$.hero.text",
        "issue": "claim id leaked into copy: price-fuji in $.hero.text",
        "text": page["hero"]["text"],
    }]
    fixed = apply_deterministic_fixes(page, failures, {"price-fuji"})
    assert fixed == 1
    assert "price-fuji" not in page["hero"]["text"]


def test_apply_deterministic_fixes_leaves_unfixable_failures_alone():
    # EMF has no safe synonym -- must fall through to a real repair call,
    # not a deterministic rewrite.
    page = {"hero": {"text": "Contains EMF testing data."}}
    failures = [{"path": "$.hero.text", "term": "emf", "issue": "forbidden term 'emf' found", "text": page["hero"]["text"]}]
    fixed = apply_deterministic_fixes(page, failures, set())
    assert fixed == 0
    assert page["hero"]["text"] == "Contains EMF testing data."


def test_write_and_gate_page_resolves_hype_word_via_deterministic_fix_without_a_repair_call(tmp_path):
    bad_page = dict(ARTICLE_PAGE, open=[{"text": "Shopping used to mean you had to unlock a callback."}])

    (page, attempts, deterministic_fixes), client = _write_and_gate([json_response(bad_page)], tmp_path)

    assert len(client.messages.calls) == 1  # no repair call needed
    assert attempts == [[]]  # gate passed after the deterministic fix, on attempt 1
    assert deterministic_fixes == [1]
    assert "unlock" not in page["open"][0]["text"].lower()


def test_write_and_gate_page_resolves_leaked_claim_id_via_deterministic_fix(tmp_path):
    bad_page = dict(
        ARTICLE_PAGE,
        close={"paragraphs": [{"text": "Peak Saunas is one brand that does this (gbrain-allowlist-red-light)."}]},
    )

    (page, attempts, deterministic_fixes), client = _write_and_gate([json_response(bad_page)], tmp_path)

    assert len(client.messages.calls) == 1
    assert attempts == [[]]
    assert deterministic_fixes == [1]
    assert "gbrain-allowlist-red-light" not in page["close"]["paragraphs"][0]["text"]
