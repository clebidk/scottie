"""Cycle 71: listicle "mistakes" and "tested" runs STOPped on the claims gate.

Two causes, both reproduced here from real writer drafts
(tests/fixtures/listicle_c71/uncited_item_bodies.json -- attempt-1 drafts of
`--style tested` on product-features-v2.mov and `--style mistakes` on
hidden-costs-v2.mov, items only):

1. The writer cites an item once, on its proof line, and leaves the item's
   own claim_ids out -- but the body states the same spec number, and the
   gate reads the body's claim_ids, so the item fails "text needs at least
   one claim_id". The body's numbers are all in the claims the proof cites.
2. The warranty pre-repair fix replaced a whole 50-150 word item body with
   the 13-word fixed warranty sentence, which then failed
   listicle:item_words -- a failure the writer is never told the reason for.
"""
import json
from pathlib import Path

from harness import listicle
from harness.claims import find_warranty_violations, validate_page_claim_ids
from harness.repair import apply_deterministic_fixes
from harness.vocab import ALLOWED_WARRANTY_SENTENCE

FIXTURE = Path(__file__).parent / "fixtures" / "listicle_c71" / "uncited_item_bodies.json"


def _load(style):
    data = json.loads(FIXTURE.read_text())[style]
    return data["page"], data["facts_pack"]


def _uncited(page, facts_pack):
    valid = {c["id"] for c in facts_pack["verified_claims"]}
    return validate_page_claim_ids(page, valid, facts_pack["digit_exempt_terms"])


def _fix(page, failures, facts_pack):
    valid = {c["id"] for c in facts_pack["verified_claims"]}
    return apply_deterministic_fixes(
        page, failures, valid, cartridge_name="listicle", facts_pack=facts_pack,
    )


def test_real_tested_draft_item_bodies_take_the_claims_their_proof_cites():
    page, facts_pack = _load("tested")
    texts_before = [item["text"] for item in page["reasons"]]
    failures = _uncited(page, facts_pack)
    # The real failure: three item bodies state spec numbers, cited only on the proof.
    assert [f["path"] for f in failures] == ["$.reasons[0]", "$.reasons[1]", "$.reasons[4]"]

    assert _fix(page, failures, facts_pack) == 3

    assert _uncited(page, facts_pack) == []
    assert page["reasons"][0]["claim_ids"] == ["spec-mini-dimensions"]
    assert page["reasons"][1]["claim_ids"] == ["spec-mini-electrical", "pdp-mini-electrical"]
    assert page["reasons"][4]["claim_ids"] == ["shipping-policy", "returns-policy"]
    # The copy itself is never rewritten.
    assert [item["text"] for item in page["reasons"]] == texts_before


def test_real_mistakes_draft_item_bodies_take_the_claims_their_proof_cites():
    page, facts_pack = _load("mistakes")
    failures = _uncited(page, facts_pack)
    assert [f["path"] for f in failures] == ["$.reasons[0]", "$.reasons[2]", "$.reasons[4]"]

    assert _fix(page, failures, facts_pack) == 3

    assert _uncited(page, facts_pack) == []
    assert page["reasons"][0]["claim_ids"] == ["price-fuji"]
    assert page["reasons"][2]["claim_ids"] == ["spec-fuji-electrical-requirement"]
    assert page["reasons"][4]["claim_ids"] == ["spec-fuji-infrared-wavelength-range"]


def test_item_body_number_the_proof_claims_do_not_state_stays_uncited():
    # The gate is not loosened: the same real body ("120V/20A") with a proof
    # that cites a claim not stating those numbers gets no claim_ids, and the
    # failure is left for the writer.
    page, facts_pack = _load("mistakes")
    item = page["reasons"][2]
    item["proof"]["claim_ids"] = ["spec-fuji-cabin-material"]
    failures = [f for f in _uncited(page, facts_pack) if f["path"] == "$.reasons[2]"]
    assert len(failures) == 1

    _fix(page, failures, facts_pack)

    assert "claim_ids" not in item
    assert [f["path"] for f in _uncited(page, facts_pack) if f["path"] == "$.reasons[2]"] == ["$.reasons[2]"]


def test_warranty_fix_in_an_item_body_keeps_the_rest_of_the_body():
    # Real item 6 of the tested draft, with its real flagged heading claim
    # ("A lifetime warranty means ...") as the body's first sentence -- the
    # body shape that became a 13-word item on every recorded tested run.
    page, facts_pack = _load("tested")
    item = page["reasons"][5]
    item["text"] = item["heading"] + ". " + item["text"]
    item["claim_ids"] = ["returns-policy"]
    body_failures = [
        f for f in find_warranty_violations(page, facts_pack["verified_claims"])
        if f["path"] == "$.reasons[5].text"
    ]
    assert len(body_failures) == 1

    _fix(page, body_failures, facts_pack)

    assert item["text"].startswith(ALLOWED_WARRANTY_SENTENCE + " This is the myth worth reading")
    assert not [
        p for p in listicle.find_item_violations(page) if p.get("key") == "listicle:item_words:5"
    ]
    assert [
        f for f in find_warranty_violations(page, facts_pack["verified_claims"])
        if f["path"] == "$.reasons[5].text"
    ] == []
    # The body's own citation is kept; the warranty claim is added beside it.
    assert item["claim_ids"] == ["returns-policy", "warranty-terms"]


def test_warranty_fix_of_a_single_sentence_still_becomes_the_fixed_sentence():
    page = {"reasons": [{"text": "Every Peak sauna has a lifetime warranty on all parts.", "claim_ids": []}]}
    failures = find_warranty_violations(page, [{"id": "warranty-terms", "text": "warranty text"}])
    apply_deterministic_fixes(page, failures, {"warranty-terms"}, cartridge_name="listicle")
    assert page["reasons"][0]["text"] == ALLOWED_WARRANTY_SENTENCE
    assert page["reasons"][0]["claim_ids"] == ["warranty-terms"]


def test_writer_is_told_the_proof_citation_does_not_cover_the_body():
    rules = " ".join(listicle.writer_rules_lines())
    assert "a claim_id on the proof never covers the body" in rules
