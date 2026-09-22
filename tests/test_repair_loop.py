"""Fix cycle 4 item 1/2/3/6: the writer repair loop, and the two new hard
gate checks (word range, CTA allowlist) that feed it. A page that fails a
page-level gate check gets a second (and third) chance with a "REVISION
REQUIRED" block before the run STOPs -- these tests exercise that directly
against harness.repair.write_and_gate_page, with a fake Anthropic client standing in
for the writer, rather than through the full `adv run` pipeline."""
from pathlib import Path

import pytest

from harness.budget import Budget, BudgetExceeded
from harness.claims import ClaimsGateFailure, find_financing_violations, find_warranty_violations
from harness.pagechecks import find_image_allowlist_violations
from harness.repair import (
    MAX_REPAIR_ATTEMPTS,
    apply_deterministic_fixes,
    apply_hype_synonyms,
    build_revision_note,
    convert_incidental_numerals,
    find_cta_violation,
    find_word_range_violation,
    get_cta_text,
    write_and_gate_page,
)
from harness.log import RunLog
from harness.vocab import (
    ALLOWED_WARRANTY_SENTENCE,
    ALLOWED_WARRANTY_SPEC_LABEL,
    ALLOWED_WARRANTY_SPEC_VALUE,
    ALWAYS_FORBIDDEN_TERMS,
    allowed_financing_sentence,
)
from harness.write import parse_word_range, resolve_allowed_cta_texts
from tests.conftest import FakeClient, FakeResponse, block_text, json_response
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
    second_user_msg = block_text(client.messages.calls[1]["messages"][0]["content"])
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


def test_resolve_allowed_cta_texts_substitutes_model_name():
    # Fix cycle 7 item 3: "Shop the {model_name}" -> "Shop the Fuji" (model
    # name only, not the full short_name).
    schema = {"allowed_cta_texts": ["Shop the {short_name}", "Buy the {short_name}", "Shop the {model_name}"]}
    resolved = resolve_allowed_cta_texts(schema, "Peak Fuji 2-Person Infrared Sauna", model_name="Fuji")
    assert resolved == [
        "Shop the Peak Fuji 2-Person Infrared Sauna",
        "Buy the Peak Fuji 2-Person Infrared Sauna",
        "Shop the Fuji",
    ]


def test_resolve_allowed_cta_texts_model_name_defaults_to_none():
    # A caller (or an existing test) that doesn't pass model_name is
    # unaffected as long as the schema's templates don't reference it.
    schema = {"allowed_cta_texts": ["Shop the {short_name}"]}
    assert resolve_allowed_cta_texts(schema, "Peak Fuji 2-Person Infrared Sauna") == ["Shop the Peak Fuji 2-Person Infrared Sauna"]


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


def test_find_cta_violation_passes_for_the_model_name_only_cta():
    schema = {"allowed_cta_texts": ["Shop the {short_name}", "Buy the {short_name}", "Shop the {model_name}"]}
    allowed = resolve_allowed_cta_texts(schema, "Peak Fuji 2-Person Infrared Sauna", model_name="Fuji")
    assert find_cta_violation({"cta_text": "Shop the Fuji"}, "product-page", allowed) == []


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

    third_user_msg = block_text(client.messages.calls[2]["messages"][0]["content"])
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


def test_apply_deterministic_fixes_resolves_a_safe_trigger_word_failure():
    # Regression from the hidden-costs-v2 verification run: generic
    # buyer-education prose used "study"/"studies" with nothing in
    # facts_pack.verified_claims to cite -- "research" isn't a trigger word,
    # so swapping it in drops the claim_id requirement without a repair call.
    page = {
        "body_sections": [
            {"paragraphs": [{"text": "Any brand citing a study should be citing something a reader can look up."}]}
        ]
    }
    failures = [{
        "path": "$.body_sections[0].paragraphs[0]",
        "issue": 'text needs at least one claim_id (uses the word "study") -- cite a verified claim_id, or rewrite the sentence without it',
        "text": page["body_sections"][0]["paragraphs"][0]["text"],
    }]
    fixed = apply_deterministic_fixes(page, failures, set())
    assert fixed == 1
    new_text = page["body_sections"][0]["paragraphs"][0]["text"]
    assert "study" not in new_text.lower()
    assert "research" in new_text.lower()


def test_apply_deterministic_fixes_leaves_trigger_words_with_no_safe_synonym_alone():
    page = {"hero": {"text": "Look at ratings and reviews from other buyers."}}
    failures = [{
        "path": "$.hero",
        "issue": 'text needs at least one claim_id (uses the word "reviews") -- cite a verified claim_id, or rewrite the sentence without it',
        "text": page["hero"]["text"],
    }]
    fixed = apply_deterministic_fixes(page, failures, set())
    assert fixed == 0
    assert page["hero"]["text"] == "Look at ratings and reviews from other buyers."


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


# ---------------------------------------------------------------------------
# Fix cycle 13 item 1: warranty wording, deterministic pre-repair. Sweep
# 2026-09-10b's still-levelup-4x5.png and still-unforgettable-4x5.png both
# STOPped because the writer's own repair attempt reproduced the same
# claims.find_warranty_violations failure -- a short label or an honest-
# sounding paraphrase, never one of the allowed forms. apply_deterministic_
# fixes now resolves this the same way it already resolves a hype word or a
# leaked claim id: no model call.
# ---------------------------------------------------------------------------

_WARRANTY_VALID_CLAIM_IDS = {"warranty-terms", "price-fuji"}


def test_apply_deterministic_fixes_resolves_a_warranty_proof_point():
    # The real failure shape from the sweep: a short label, not one of the
    # three allowed forms.
    page = {"proof_points": [{
        "text": "Backed by a limited lifetime warranty covering the cabin and heaters.",
        "claim_ids": [],
    }]}
    failures = find_warranty_violations(page, [{"id": "warranty-terms", "text": "warranty text"}])
    assert len(failures) == 1
    fixed = apply_deterministic_fixes(page, failures, _WARRANTY_VALID_CLAIM_IDS)
    assert fixed == 1
    assert page["proof_points"][0]["text"] == ALLOWED_WARRANTY_SENTENCE
    assert page["proof_points"][0]["claim_ids"] == ["warranty-terms"]
    # gate re-run confirms it: no violation left.
    assert find_warranty_violations(page, [{"id": "warranty-terms", "text": "warranty text"}]) == []


def test_apply_deterministic_fixes_resolves_a_warranty_paragraph():
    page = {"body_sections": [{"paragraphs": [{
        "text": "A sauna advertised with a lifetime warranty might only apply that term to the cabinetry.",
        "claim_ids": [],
    }]}]}
    failures = find_warranty_violations(page, [{"id": "warranty-terms", "text": "warranty text"}])
    assert len(failures) == 1
    fixed = apply_deterministic_fixes(page, failures, _WARRANTY_VALID_CLAIM_IDS)
    assert fixed == 1
    para = page["body_sections"][0]["paragraphs"][0]
    assert para["text"] == ALLOWED_WARRANTY_SENTENCE
    assert para["claim_ids"] == ["warranty-terms"]


def test_apply_deterministic_fixes_resolves_a_warranty_spec_table_row():
    page = {"specs_table": [{"label": "Warranty", "value": "Lifetime warranty on everything", "claim_id": None}]}
    # specs_table rows use "value", not "text" -- confirm the gate flags the
    # value field specifically, at a path ending in ".value".
    failures = find_warranty_violations(page, [{"id": "warranty-terms", "text": "warranty text"}])
    assert len(failures) == 1
    assert failures[0]["path"].endswith(".value")
    fixed = apply_deterministic_fixes(page, failures, _WARRANTY_VALID_CLAIM_IDS)
    assert fixed == 1
    row = page["specs_table"][0]
    assert row["label"] == ALLOWED_WARRANTY_SPEC_LABEL
    assert row["value"] == ALLOWED_WARRANTY_SPEC_VALUE
    assert row["claim_id"] == "warranty-terms"


def test_apply_deterministic_fixes_does_not_turn_a_coverage_row_into_a_warranty_row():
    # Cycle 34: a specs_table row whose *value* mentions a lifetime warranty
    # but whose label is Coverage/Heaters/etc must not be rewritten into the
    # Warranty pair (that dropped the real fact and could duplicate Warranty).
    page = {
        "specs_table": [
            {"label": "Coverage", "value": "Lifetime warranty heaters included", "claim_id": None},
            {"label": "Warranty", "value": ALLOWED_WARRANTY_SPEC_VALUE, "claim_id": "warranty-terms"},
        ]
    }
    failures = find_warranty_violations(page, [{"id": "warranty-terms", "text": "warranty text"}])
    assert len(failures) == 1
    assert failures[0]["path"].endswith("specs_table[0].value")
    fixed = apply_deterministic_fixes(page, failures, _WARRANTY_VALID_CLAIM_IDS)
    assert fixed == 1
    coverage = page["specs_table"][0]
    assert coverage["label"] == "Coverage"
    assert coverage["value"] == ALLOWED_WARRANTY_SENTENCE
    assert page["specs_table"][1]["label"] == "Warranty"
    assert page["specs_table"][1]["value"] == ALLOWED_WARRANTY_SPEC_VALUE
    assert find_warranty_violations(page, [{"id": "warranty-terms", "text": "warranty text"}]) == []


def test_apply_deterministic_fixes_leaves_an_allowed_warranty_form_unchanged():
    page = {"trust_strip": {"warranty": {"text": ALLOWED_WARRANTY_SENTENCE, "claim_ids": ["warranty-terms"]}}}
    # An allowed form never produces a failure in the first place -- the
    # gate simply passes it, so there is nothing for the deterministic pass
    # to touch.
    assert find_warranty_violations(page, [{"id": "warranty-terms", "text": "warranty text"}]) == []
    fixed = apply_deterministic_fixes(page, [], _WARRANTY_VALID_CLAIM_IDS)
    assert fixed == 0
    assert page["trust_strip"]["warranty"]["text"] == ALLOWED_WARRANTY_SENTENCE


def test_write_and_gate_page_resolves_warranty_violation_via_deterministic_fix_without_a_repair_call(tmp_path):
    bad_page = dict(
        ARTICLE_PAGE,
        close={"paragraphs": [{
            "text": "Backed by a limited lifetime warranty covering the cabin and heaters.",
            "claim_ids": [],
        }]},
    )

    (page, attempts, deterministic_fixes), client = _write_and_gate([json_response(bad_page)], tmp_path)

    assert len(client.messages.calls) == 1  # no repair call needed
    assert attempts == [[]]
    assert deterministic_fixes == [1]
    assert page["close"]["paragraphs"][0]["text"] == ALLOWED_WARRANTY_SENTENCE


# ---------------------------------------------------------------------------
# Fix cycle 21: financing wording, deterministic pre-repair -- same shape as
# the warranty fix above (fix cycle 13 item 1). Before this cycle, no
# deterministic fix existed for a financing_line violation at all (the gate
# itself no-op'd once a lender was configured, so nothing was ever flagged
# to fix in the first place -- docs/FIXLOG.md Cycle 18's own real-run
# verification found every rendered page kept the stale no-lender sentence).
# ---------------------------------------------------------------------------

def test_apply_deterministic_fixes_resolves_a_financing_line_with_no_lender_configured():
    page = {"hero": {"financing_line": {"text": "Financing is available for as low as $75/mo.", "claim_ids": []}}}
    failures = find_financing_violations(page, financing_lender=None)
    assert len(failures) == 1
    fixed = apply_deterministic_fixes(page, failures, set(), financing_lender=None)
    assert fixed == 1
    assert page["hero"]["financing_line"]["text"] == allowed_financing_sentence(None)
    assert find_financing_violations(page, financing_lender=None) == []


def test_apply_deterministic_fixes_resolves_a_financing_line_once_a_lender_is_configured():
    # The exact stale-sentence bug docs/FIXLOG.md Cycle 18 flagged: the page
    # still states the no-lender sentence even though a lender is configured.
    page = {"hero": {"financing_line": {"text": "Financing is available at checkout.", "claim_ids": []}}}
    failures = find_financing_violations(page, financing_lender="Bread Pay")
    assert len(failures) == 1
    fixed = apply_deterministic_fixes(page, failures, set(), financing_lender="Bread Pay")
    assert fixed == 1
    assert page["hero"]["financing_line"]["text"] == "Financing is available through Bread Pay at checkout."
    assert find_financing_violations(page, financing_lender="Bread Pay") == []


def test_apply_deterministic_fixes_leaves_the_allowed_with_lender_sentence_unchanged():
    page = {"hero": {"financing_line": {"text": "Financing is available through Bread Pay at checkout.", "claim_ids": []}}}
    assert find_financing_violations(page, financing_lender="Bread Pay") == []
    fixed = apply_deterministic_fixes(page, [], set(), financing_lender="Bread Pay")
    assert fixed == 0
    assert page["hero"]["financing_line"]["text"] == "Financing is available through Bread Pay at checkout."


def test_write_and_gate_page_resolves_financing_violation_via_deterministic_fix_without_a_repair_call(tmp_path):
    # Mirrors test_write_and_gate_page_resolves_warranty_violation_via_
    # deterministic_fix_without_a_repair_call above, but for financing wording
    # once a lender is configured -- exercised through the real
    # write_and_gate_page path (not just apply_deterministic_fixes directly),
    # confirming financing_lender reaches the deterministic pass end to end.
    # article's schema.json declares financing_line as a bare string, not an
    # object -- matches that shape (unlike hero/final_cta's {"text": ...}
    # object in product-page/longform).
    bad_page = dict(ARTICLE_PAGE, financing_line="Financing is available at checkout.")
    client = FakeClient([json_response(bad_page)])
    budget = Budget()
    log = RunLog("test-run", tmp_path / "run.log")
    try:
        page, attempts, deterministic_fixes = write_and_gate_page(
            cartridge_name="article",
            cartridges_dir=REPO_ROOT / "cartridges",
            ad_brief=AD_BRIEF,
            facts_pack=FACTS_PACK,
            client=client,
            model="claude-sonnet-5",
            budget=budget,
            log=log,
            financing_lender="Bread Pay",
            speaker_pov=AD_BRIEF["speaker_pov"],
        )
    finally:
        log.close()

    assert len(client.messages.calls) == 1  # no repair call needed
    assert attempts == [[]]
    assert deterministic_fixes == [1]
    assert page["financing_line"] == "Financing is available through Bread Pay at checkout."


# ---------------------------------------------------------------------------
# Fix cycle 25: financing wording is now gated cartridge-independently
# (claims.find_financing_violations' new prose scan), closing the gap Cycle
# 24's final sweep found -- article's writer left financing_line unset and
# paraphrased the offer into close.paragraphs prose instead. The
# deterministic fix mirrors the dedicated-field case above: same
# apply_deterministic_fixes/_fix_financing_violation path, just at a prose
# field's path instead of financing_line's.
# ---------------------------------------------------------------------------

def test_apply_deterministic_fixes_resolves_a_paraphrased_financing_sentence_in_article_prose():
    page = {
        "close": {
            "paragraphs": [{
                "text": "The Peak Fuji is priced at $5,450, with financing available through Bread "
                        "Pay at checkout, so the decision becomes easier."
            }]
        }
    }
    failures = find_financing_violations(page, financing_lender="Bread Pay")
    assert len(failures) == 1
    fixed = apply_deterministic_fixes(page, failures, set(), financing_lender="Bread Pay")
    assert fixed == 1
    assert page["close"]["paragraphs"][0]["text"] == "Financing is available through Bread Pay at checkout."
    assert find_financing_violations(page, financing_lender="Bread Pay") == []


def test_write_and_gate_page_resolves_a_paraphrased_article_financing_sentence_without_a_repair_call(tmp_path):
    # Mirrors test_write_and_gate_page_resolves_financing_violation_via_
    # deterministic_fix_without_a_repair_call above, but for the Cycle 24
    # gap: the writer leaves financing_line unset and paraphrases financing
    # into close.paragraphs prose instead.
    bad_page = dict(
        ARTICLE_PAGE,
        close={"paragraphs": [{
            "text": "The Peak Fuji is priced at $5,450, and financing is available through "
                    "Bread Pay at checkout. See the models next."
        }]},
    )
    client = FakeClient([json_response(bad_page)])
    budget = Budget()
    log = RunLog("test-run", tmp_path / "run.log")
    try:
        page, attempts, deterministic_fixes = write_and_gate_page(
            cartridge_name="article",
            cartridges_dir=REPO_ROOT / "cartridges",
            ad_brief=AD_BRIEF,
            facts_pack=FACTS_PACK,
            client=client,
            model="claude-sonnet-5",
            budget=budget,
            log=log,
            financing_lender="Bread Pay",
            speaker_pov=AD_BRIEF["speaker_pov"],
        )
    finally:
        log.close()

    assert len(client.messages.calls) == 1  # no repair call needed
    assert attempts == [[]]
    assert deterministic_fixes == [1]
    assert page["close"]["paragraphs"][0]["text"] == "Financing is available through Bread Pay at checkout."


def test_write_and_gate_page_passes_an_article_page_with_no_financing_mention(tmp_path):
    # article without any financing text at all -- financing_line stays
    # unset (schema allows it) and no field mentions "financ" -- must pass
    # cleanly, cartridge-independent scan or not.
    client = FakeClient([json_response(ARTICLE_PAGE)])
    budget = Budget()
    log = RunLog("test-run", tmp_path / "run.log")
    try:
        page, attempts, deterministic_fixes = write_and_gate_page(
            cartridge_name="article",
            cartridges_dir=REPO_ROOT / "cartridges",
            ad_brief=AD_BRIEF,
            facts_pack=FACTS_PACK,
            client=client,
            model="claude-sonnet-5",
            budget=budget,
            log=log,
            financing_lender="Bread Pay",
            speaker_pov=AD_BRIEF["speaker_pov"],
        )
    finally:
        log.close()

    assert len(client.messages.calls) == 1  # no repair call needed
    assert attempts == [[]]
    assert deterministic_fixes == [0]
    assert page["close"]["paragraphs"][0]["text"] == "Peak Saunas is one brand that does this."


# ---------------------------------------------------------------------------
# Fix cycle 11 problem C: the price-comparison-v2.mov run hit the token cap
# mid-repair and exhausted budget. The repair loop now logs the remaining
# budget at every attempt, and skips a repair (STOPping with a clear reason
# instead of a BudgetExceeded exception) once the remaining token budget is
# below the average cost of one writer call for this cartridge so far.
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Fix cycle 12 item 2: incidental numerals. "driving to a studio at 7 a.m."
# tripped the plain digit rule -- convert_incidental_numerals writes a
# numeral time or a standalone 1-12 out as words before ever calling the
# writer again, without touching a claim-cited sentence (it only ever runs
# on a text field the "contains a number" gate already flagged as having no
# claim_ids -- see apply_deterministic_fixes's own "(contains a number)"
# branch below).
# ---------------------------------------------------------------------------

def test_convert_incidental_numerals_handles_am_pm_and_oclock():
    assert convert_incidental_numerals("driving to a studio at 7 a.m.") == "driving to a studio at seven in the morning"
    assert convert_incidental_numerals("she left around 3pm") == "she left around three in the afternoon"
    assert convert_incidental_numerals("back by 9 o'clock") == "back by nine o'clock"


def test_convert_incidental_numerals_handles_standalone_counts():
    assert convert_incidental_numerals("It has 2 rooms and 3 doors.") == "It has two rooms and three doors."
    assert convert_incidental_numerals("a 5-figure purchase") == "a five-figure purchase"


def test_convert_incidental_numerals_capitalizes_at_sentence_start():
    assert convert_incidental_numerals("7 a.m. is early.") == "Seven in the morning is early."


def test_convert_incidental_numerals_leaves_numbers_outside_1_to_12_alone():
    assert convert_incidental_numerals("13 hours later") == "13 hours later"
    assert convert_incidental_numerals("a 20 minute drive") == "a 20 minute drive"


def test_convert_incidental_numerals_leaves_prices_percentages_and_thousands_alone():
    assert convert_incidental_numerals("priced at $5,450") == "priced at $5,450"
    assert convert_incidental_numerals("9,000 reviews") == "9,000 reviews"
    assert convert_incidental_numerals("a 4.9 rating") == "a 4.9 rating"
    assert convert_incidental_numerals("up 5% this year") == "up 5% this year"


def test_convert_incidental_numerals_leaves_the_capacity_token_alone():
    # claims._CAPACITY_TOKEN_RE / digit_exempt_terms already handle this
    # sentence shape upstream -- convert_incidental_numerals must not fight
    # that by rewriting the capacity digit itself.
    assert convert_incidental_numerals("the Peak Fuji 2-Person Infrared Sauna") == "the Peak Fuji 2-Person Infrared Sauna"


def test_apply_deterministic_fixes_resolves_an_incidental_numeral_failure():
    page = {"open": [{"text": "She was driving to a studio at 7 a.m. when it happened."}]}
    failures = [{
        "path": "$.open[0]",
        "issue": "text needs at least one claim_id (contains a number) -- cite a verified claim_id, or rewrite the sentence without it",
        "text": page["open"][0]["text"],
    }]
    fixed = apply_deterministic_fixes(page, failures, set())
    assert fixed == 1
    new_text = page["open"][0]["text"]
    assert "7 a.m." not in new_text
    assert "seven in the morning" in new_text


def test_apply_deterministic_fixes_does_not_touch_dollar_or_percentage_failures():
    # These reasons are distinct from "(contains a number)" -- the numeral
    # conversion branch must not fire for them (there's no safe deterministic
    # fix for an invented dollar amount or percentage).
    page = {"hero": {"text": "It costs $50 more."}}
    failures = [{
        "path": "$.hero",
        "issue": "text needs at least one claim_id (contains a dollar amount) -- cite a verified claim_id, or rewrite the sentence without it",
        "text": page["hero"]["text"],
    }]
    fixed = apply_deterministic_fixes(page, failures, set())
    assert fixed == 0
    assert page["hero"]["text"] == "It costs $50 more."


# ---------------------------------------------------------------------------
# Cycle 50: asset_id pool-prefix rewrite. harness/ground.py builds ids with
# two different prefixes for the same underlying Drive file --
# asset-drive-<driveid> (brand/assets.json) and asset-listicle-<driveid>
# (brand/assets-listicle-pack.json) -- and a writer that has both pools in
# view sometimes emits the sibling prefix, tripping
# find_image_allowlist_violations even though the same asset IS in the
# manifest under its other prefix. These are direct unit tests against
# apply_deterministic_fixes and find_image_allowlist_violations, same shape
# as the hype-word/leaked-claim-id tests above.
# ---------------------------------------------------------------------------

_ASSET_TAIL = "1FYCbReXveSzJxIpNGxNWrMP_X6xbXP7f"


def _asset_facts_pack(*asset_ids):
    return {"assets": [{"id": aid} for aid in asset_ids]}


def test_apply_deterministic_fixes_rewrites_asset_id_to_the_prefix_present_in_manifest():
    # writer wrote the listicle prefix; the manifest only has the drive one.
    wrong_id = f"asset-listicle-{_ASSET_TAIL}"
    right_id = f"asset-drive-{_ASSET_TAIL}"
    page = {"hero": {"asset_id": wrong_id}}
    facts_pack = _asset_facts_pack(right_id)
    failures = find_image_allowlist_violations(page, facts_pack)
    assert len(failures) == 1

    fixed = apply_deterministic_fixes(page, failures, set(), facts_pack=facts_pack)

    assert fixed == 1
    assert page["hero"]["asset_id"] == right_id


def test_apply_deterministic_fixes_rewrites_the_other_direction_too():
    # writer wrote the drive prefix; the manifest only has the listicle one.
    wrong_id = f"asset-drive-{_ASSET_TAIL}"
    right_id = f"asset-listicle-{_ASSET_TAIL}"
    page = {"hero": {"asset_id": wrong_id}}
    facts_pack = _asset_facts_pack(right_id)
    failures = find_image_allowlist_violations(page, facts_pack)

    fixed = apply_deterministic_fixes(page, failures, set(), facts_pack=facts_pack)

    assert fixed == 1
    assert page["hero"]["asset_id"] == right_id


def test_apply_deterministic_fixes_leaves_asset_id_alone_when_neither_prefix_is_in_the_manifest():
    unknown_id = f"asset-listicle-{_ASSET_TAIL}"
    page = {"hero": {"asset_id": unknown_id}}
    facts_pack = _asset_facts_pack("asset-drive-some-other-file")
    failures = find_image_allowlist_violations(page, facts_pack)
    assert len(failures) == 1

    fixed = apply_deterministic_fixes(page, failures, set(), facts_pack=facts_pack)

    assert fixed == 0
    assert page["hero"]["asset_id"] == unknown_id


def test_apply_deterministic_fixes_leaves_asset_id_alone_when_both_siblings_are_in_the_manifest():
    # Ambiguous -- never guess which one the writer meant. The page's own
    # id is always one of the two sibling forms of its own tail, so "both
    # present" can only arise if the manifest itself lists both prefixes
    # for that tail (a run whose asset pools overlap) -- exercised here
    # directly against the fix function rather than through the id in
    # `page`, which by construction can't itself be the failing id AND
    # have both siblings present (see harness/repair.py's
    # _fix_asset_id_prefix_violation docstring, cycle 50).
    from harness.repair import _fix_asset_id_prefix_violation

    tail = f"{_ASSET_TAIL}-both"
    page = {"hero": {"asset_id": f"asset-listicle-{tail}"}}
    facts_pack = _asset_facts_pack(f"asset-drive-{tail}", f"asset-listicle-{tail}")

    result = _fix_asset_id_prefix_violation(page, "$.hero.asset_id", facts_pack)

    assert result is None
    assert page["hero"]["asset_id"] == f"asset-listicle-{tail}"


def test_apply_deterministic_fixes_leaves_asset_id_alone_when_it_is_already_valid():
    valid_id = f"asset-drive-{_ASSET_TAIL}"
    page = {"hero": {"asset_id": valid_id}}
    facts_pack = _asset_facts_pack(valid_id)
    assert find_image_allowlist_violations(page, facts_pack) == []

    fixed = apply_deterministic_fixes(page, [], set(), facts_pack=facts_pack)

    assert fixed == 0
    assert page["hero"]["asset_id"] == valid_id


def test_apply_deterministic_fixes_leaves_a_non_pool_asset_id_alone():
    # Shopify-style id (asset-<slug>-<n>) never matches the drive/listicle
    # prefix shape -- left alone, same as a truly unknown id.
    shopify_id = "asset-fuji-1"
    page = {"hero": {"asset_id": shopify_id}}
    facts_pack = _asset_facts_pack("asset-drive-something-else")
    failures = find_image_allowlist_violations(page, facts_pack)
    assert len(failures) == 1

    fixed = apply_deterministic_fixes(page, failures, set(), facts_pack=facts_pack)

    assert fixed == 0
    assert page["hero"]["asset_id"] == shopify_id


def test_apply_deterministic_fixes_asset_id_rewrite_is_skipped_without_a_facts_pack():
    wrong_id = f"asset-listicle-{_ASSET_TAIL}"
    right_id = f"asset-drive-{_ASSET_TAIL}"
    page = {"hero": {"asset_id": wrong_id}}
    facts_pack = _asset_facts_pack(right_id)
    failures = find_image_allowlist_violations(page, facts_pack)

    # facts_pack not passed through -- same as every caller before cycle 50.
    fixed = apply_deterministic_fixes(page, failures, set())

    assert fixed == 0
    assert page["hero"]["asset_id"] == wrong_id


def test_gate_no_longer_fails_on_the_rewritten_page():
    wrong_id = f"asset-listicle-{_ASSET_TAIL}"
    right_id = f"asset-drive-{_ASSET_TAIL}"
    page = {"hero": {"asset_id": wrong_id}}
    facts_pack = _asset_facts_pack(right_id)
    failures = find_image_allowlist_violations(page, facts_pack)

    apply_deterministic_fixes(page, failures, set(), facts_pack=facts_pack)

    assert find_image_allowlist_violations(page, facts_pack) == []


def test_manifest_gate_still_fails_for_a_truly_unknown_asset_id():
    page = {"hero": {"asset_id": "asset-drive-totally-unknown-file"}}
    facts_pack = _asset_facts_pack(f"asset-drive-{_ASSET_TAIL}")
    failures = find_image_allowlist_violations(page, facts_pack)
    assert len(failures) == 1

    apply_deterministic_fixes(page, failures, set(), facts_pack=facts_pack)

    assert len(find_image_allowlist_violations(page, facts_pack)) == 1


def test_write_and_gate_page_resolves_incidental_numeral_via_deterministic_fix(tmp_path):
    bad_page = dict(ARTICLE_PAGE, open=[{"text": "She was up by 7 a.m. most mornings."}])

    (page, attempts, deterministic_fixes), client = _write_and_gate([json_response(bad_page)], tmp_path)

    assert len(client.messages.calls) == 1  # no repair call needed
    assert attempts == [[]]
    assert deterministic_fixes == [1]
    assert "7 a.m." not in page["open"][0]["text"]


def test_repair_skipped_when_budget_cannot_afford_another_average_call(tmp_path):
    # Every response costs 80 tokens (input+output) and always fails the
    # gate (bad CTA) -- with a 100-token budget, attempt 1 leaves 20 tokens
    # remaining, well under the 80-token average call cost, so attempt 2 is
    # skipped rather than attempted (and rather than blowing the budget).
    responses = [FakeResponse(json_response(BAD_ARTICLE_PAGE), input_tokens=40, output_tokens=40)] * 3
    client = FakeClient(responses)
    budget = Budget(wall_s=300, tokens=100, calls=12)
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

    # Only the initial write happened -- the repair was skipped, not attempted.
    assert len(client.messages.calls) == 1
    assert getattr(exc_info.value, "budget_skipped", False) is True

    log_text = (tmp_path / "run.log").read_text()
    assert "repair skipped: budget" in log_text
    # budget remaining was logged on every attempt that did run.
    assert "budget remaining" in log_text


def test_repair_proceeds_when_budget_can_still_afford_another_average_call(tmp_path):
    # Same shape, but a much larger budget -- the repair loop should behave
    # exactly as before this fix: a second call happens and recovers.
    responses = [
        FakeResponse(json_response(BAD_ARTICLE_PAGE), input_tokens=40, output_tokens=40),
        FakeResponse(json_response(ARTICLE_PAGE), input_tokens=40, output_tokens=40),
    ]
    client = FakeClient(responses)
    budget = Budget(wall_s=300, tokens=150_000, calls=12)
    log = RunLog("test-run", tmp_path / "run.log")
    try:
        page, attempts, deterministic_fixes = write_and_gate_page(
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

    assert page == ARTICLE_PAGE
    assert len(client.messages.calls) == 2
    log_text = (tmp_path / "run.log").read_text()
    assert "repair skipped: budget" not in log_text


def test_write_and_gate_page_never_raises_bare_budget_exceeded_from_a_skipped_repair(tmp_path):
    # The whole point of fix cycle 11 problem C: a run that would previously
    # hit BudgetExceeded mid-repair now STOPs with a clear ClaimsGateFailure
    # reason instead -- cli.cmd_run's two except clauses treat these very
    # differently (STOP vs. a generic "budget exceeded" exit).
    responses = [FakeResponse(json_response(BAD_ARTICLE_PAGE), input_tokens=40, output_tokens=40)] * 3
    client = FakeClient(responses)
    budget = Budget(wall_s=300, tokens=100, calls=12)
    log = RunLog("test-run", tmp_path / "run.log")
    try:
        try:
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
            raise AssertionError("expected ClaimsGateFailure")
        except BudgetExceeded:
            pytest.fail("budget skip should STOP with ClaimsGateFailure, not raise BudgetExceeded")
        except ClaimsGateFailure:
            pass
    finally:
        log.close()


# ---------------------------------------------------------------------------
# Fix cycle 17 item 4 (model tiering): attempt 1 stays on `model`; attempt 2
# (the first repair) tries repair_first_model; attempt 3+ (a further repair)
# uses repair_next_model. Either falls back to `model` when not given.
# ---------------------------------------------------------------------------

def test_write_and_gate_page_uses_repair_first_model_on_the_first_repair(tmp_path):
    client = FakeClient([json_response(BAD_ARTICLE_PAGE), json_response(ARTICLE_PAGE)])
    budget = Budget()
    log = RunLog("test-run", tmp_path / "run.log")
    try:
        write_and_gate_page(
            cartridge_name="article",
            cartridges_dir=REPO_ROOT / "cartridges",
            ad_brief=AD_BRIEF,
            facts_pack=FACTS_PACK,
            client=client,
            model="claude-sonnet-5",
            repair_first_model="claude-haiku-4-5",
            budget=budget,
            log=log,
            financing_lender=None,
            speaker_pov=AD_BRIEF["speaker_pov"],
        )
    finally:
        log.close()
    assert client.messages.calls[0]["model"] == "claude-sonnet-5"
    assert client.messages.calls[1]["model"] == "claude-haiku-4-5"


def test_write_and_gate_page_uses_repair_next_model_on_the_second_repair(tmp_path):
    attempt1_bad = BAD_ARTICLE_PAGE
    attempt2_bad = dict(ARTICLE_PAGE, open=[{"text": "This sauna avoids EMF entirely."}])
    attempt3_good = ARTICLE_PAGE
    client = FakeClient([json_response(attempt1_bad), json_response(attempt2_bad), json_response(attempt3_good)])
    budget = Budget()
    log = RunLog("test-run", tmp_path / "run.log")
    try:
        write_and_gate_page(
            cartridge_name="article",
            cartridges_dir=REPO_ROOT / "cartridges",
            ad_brief=AD_BRIEF,
            facts_pack=FACTS_PACK,
            client=client,
            model="claude-haiku-4-5",
            repair_first_model="claude-sonnet-5",
            repair_next_model="claude-haiku-4-5",
            budget=budget,
            log=log,
            financing_lender=None,
            speaker_pov=AD_BRIEF["speaker_pov"],
        )
    finally:
        log.close()
    assert client.messages.calls[0]["model"] == "claude-haiku-4-5"   # attempt 1: `model`
    assert client.messages.calls[1]["model"] == "claude-sonnet-5"    # attempt 2: repair_first_model
    assert client.messages.calls[2]["model"] == "claude-haiku-4-5"   # attempt 3: repair_next_model


def test_write_and_gate_page_repair_models_default_to_the_initial_model(tmp_path):
    # No repair_first_model/repair_next_model given -- every attempt stays on
    # `model`, exactly as before fix cycle 17.
    attempt1_bad = BAD_ARTICLE_PAGE
    attempt2_bad = dict(ARTICLE_PAGE, open=[{"text": "This sauna avoids EMF entirely."}])
    attempt3_good = ARTICLE_PAGE
    client = FakeClient([json_response(attempt1_bad), json_response(attempt2_bad), json_response(attempt3_good)])
    budget = Budget()
    log = RunLog("test-run", tmp_path / "run.log")
    try:
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
    assert [c["model"] for c in client.messages.calls] == ["claude-sonnet-5"] * 3


# ---------------------------------------------------------------------------
# Fix cycle 17 item 5 (batch mode): initial_page lets a caller (cli.py's
# --batch path) skip write_and_gate_page's own attempt-1 write_page call.
# ---------------------------------------------------------------------------

def test_write_and_gate_page_uses_initial_page_without_calling_write_page(tmp_path):
    client = FakeClient([])  # no canned response needed -- attempt 1 never calls the client
    budget = Budget()
    log = RunLog("test-run", tmp_path / "run.log")
    try:
        page, attempts, deterministic_fixes = write_and_gate_page(
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
            initial_page=ARTICLE_PAGE,
            initial_call_tokens=12345,
        )
    finally:
        log.close()
    assert page == ARTICLE_PAGE
    assert client.messages.calls == []
    assert len(attempts) == 1
    assert attempts[0] == []


def test_write_and_gate_page_falls_back_to_a_synchronous_call_when_initial_page_fails_gate(tmp_path):
    # initial_page has a bad CTA -- gate fails on "attempt 1" (no client call
    # made for it), then attempt 2 makes a real, synchronous repair call.
    client = FakeClient([json_response(ARTICLE_PAGE)])
    budget = Budget()
    log = RunLog("test-run", tmp_path / "run.log")
    try:
        page, attempts, deterministic_fixes = write_and_gate_page(
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
            initial_page=BAD_ARTICLE_PAGE,
        )
    finally:
        log.close()
    assert page == ARTICLE_PAGE
    assert len(client.messages.calls) == 1  # only the attempt-2 repair made a real call
    assert len(attempts) == 2
    assert attempts[0] and attempts[1] == []
