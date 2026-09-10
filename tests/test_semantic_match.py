"""Fix cycle 12 item 4: the one real Claude call that proposes a semantic
(equivalent-meaning) mapping from an ad claim to a verified claim id, before
word-overlap matching runs. Every test here uses a fake Anthropic client --
no network."""
import json

import pytest

from harness.budget import Budget
from harness.claims import match_claim
from harness.log import RunLog
from harness.semantic_match import (
    MAX_VERIFIED_CLAIMS_FOR_PROMPT,
    _trim_verified_claims_for_prompt,
    semantic_match_claims,
)
from tests.conftest import FakeClient, FakeResponse, json_response

# Real claims/verified.json text for these two ids -- neither one's text
# contains the digit "4" (or "1"), which is exactly why the "4-in-1" idiom
# exemption (claims._combo_idiom_numbers) exists: without it, the numeric
# guard would reject this real, semantically-correct mapping.
FULL_SPECTRUM_CLAIM = {
    "id": "gbrain-allowlist-360-full-spectrum",
    "text": "360° full spectrum infrared heater placement.",
    "category": "spec",
    "source": "https://peaksaunas.com/pages/technology",
}
RED_LIGHT_CLAIM = {
    "id": "gbrain-allowlist-red-light",
    "text": "Medical-grade red light therapy (included standard).",
    "category": "spec",
    "source": "https://peaksaunas.com/pages/technology",
}
VERIFIED_CLAIMS = [FULL_SPECTRUM_CLAIM, RED_LIGHT_CLAIM]


def test_semantic_match_claims_returns_empty_for_no_claims():
    client = FakeClient([])
    result = semantic_match_claims([], VERIFIED_CLAIMS, client=client, model="claude-sonnet-5")
    assert result == {}
    assert client.messages.calls == []  # never called -- nothing to map


def test_semantic_match_claims_parses_a_successful_mapping(tmp_path):
    claim = "4-in-1: near, mid, far infrared + red light"
    response = json_response({claim: "gbrain-allowlist-360-full-spectrum"})
    client = FakeClient([response])
    budget = Budget()
    log = RunLog("test-run", tmp_path / "run.log")
    result = semantic_match_claims(
        [claim], VERIFIED_CLAIMS, client=client, model="claude-sonnet-5", budget=budget, log=log
    )
    log.close()
    assert result == {claim: "gbrain-allowlist-360-full-spectrum"}
    call = client.messages.calls[0]
    assert call["model"] == "claude-sonnet-5"
    assert call["max_tokens"] == 1500
    assert call["thinking"] == {"type": "disabled"}
    # Fix cycle 12 item 4: budget is charged for this call, like every other
    # real model call in this codebase.
    assert budget.calls_used == 1


def test_semantic_match_claims_falls_back_to_empty_on_invalid_json(tmp_path):
    client = FakeClient(["not json at all"])
    log = RunLog("test-run", tmp_path / "run.log")
    result = semantic_match_claims(
        ["some claim"], VERIFIED_CLAIMS, client=client, model="claude-sonnet-5", log=log
    )
    log.close()
    assert result == {}
    assert "semantic match call failed" in (tmp_path / "run.log").read_text()


def test_semantic_match_claims_falls_back_to_empty_on_api_error(tmp_path):
    client = FakeClient([RuntimeError("connection reset")])
    log = RunLog("test-run", tmp_path / "run.log")
    result = semantic_match_claims(
        ["some claim"], VERIFIED_CLAIMS, client=client, model="claude-sonnet-5", log=log
    )
    log.close()
    assert result == {}


def test_semantic_match_claims_falls_back_when_response_is_not_an_object(tmp_path):
    client = FakeClient([json_response(["not", "a", "dict"])])
    log = RunLog("test-run", tmp_path / "run.log")
    result = semantic_match_claims(["some claim"], VERIFIED_CLAIMS, client=client, model="claude-sonnet-5", log=log)
    log.close()
    assert result == {}


def test_semantic_match_claims_logs_each_mapping(tmp_path):
    claims = ["4-in-1: near, mid, far infrared + red light", "medical-grade panel"]
    response = json_response(
        {claims[0]: "gbrain-allowlist-360-full-spectrum", claims[1]: "gbrain-allowlist-red-light"}
    )
    client = FakeClient([response])
    log = RunLog("test-run", tmp_path / "run.log")
    semantic_match_claims(claims, VERIFIED_CLAIMS, client=client, model="claude-sonnet-5", log=log)
    log.close()
    log_text = (tmp_path / "run.log").read_text()
    assert "gbrain-allowlist-360-full-spectrum" in log_text
    assert "gbrain-allowlist-red-light" in log_text


def test_trim_verified_claims_for_prompt_keeps_only_id_and_text_up_to_limit():
    claims = [
        {"id": f"c{i}", "text": f"text {i}", "category": "spec", "source": "https://x"} for i in range(200)
    ]
    trimmed = _trim_verified_claims_for_prompt(claims)
    assert len(trimmed) == MAX_VERIFIED_CLAIMS_FOR_PROMPT
    assert set(trimmed[0].keys()) == {"id", "text"}


# ---------------------------------------------------------------------------
# match_claim's own numeric-token guard (fix cycle 12 item 4: "Accept a
# mapping only if the numeric-token guard also passes in code").
# ---------------------------------------------------------------------------

def test_match_claim_accepts_a_semantic_mapping_when_numeric_guard_passes():
    claim = "4-in-1: near, mid, far infrared + red light"
    mapping = {claim: "gbrain-allowlist-360-full-spectrum"}
    matched, ratio = match_claim(claim, VERIFIED_CLAIMS, semantic_mapping=mapping)
    assert matched is not None
    assert matched["id"] == "gbrain-allowlist-360-full-spectrum"
    assert ratio == 1.0


def test_match_claim_accepts_medical_grade_panel_mapping_to_red_light_claim():
    claim = "medical-grade panel"
    mapping = {claim: "gbrain-allowlist-red-light"}
    matched, ratio = match_claim(claim, VERIFIED_CLAIMS, semantic_mapping=mapping)
    assert matched is not None
    assert matched["id"] == "gbrain-allowlist-red-light"


def test_match_claim_rejects_a_semantic_mapping_when_the_ad_claim_has_a_number_the_verified_claim_lacks():
    # The model proposed a match, but the ad claim's own number ("2x") isn't
    # in the verified claim's text -- the numeric guard in code overrides the
    # model's semantic judgment.
    claim = "2x more powerful full spectrum heat"
    mapping = {claim: "gbrain-allowlist-360-full-spectrum"}
    matched, ratio = match_claim(claim, VERIFIED_CLAIMS, semantic_mapping=mapping)
    assert matched is None


def test_match_claim_ignores_a_mapping_to_an_unknown_id():
    claim = "some claim"
    mapping = {claim: "not-a-real-id"}
    matched, ratio = match_claim(claim, VERIFIED_CLAIMS, semantic_mapping=mapping)
    assert matched is None


def test_match_claim_falls_through_to_overlap_when_mapping_is_null():
    # Close enough to the verified claim's own wording to clear the ordinary
    # word-overlap bar on its own -- proves a null mapping doesn't block the
    # pre-existing overlap path.
    claim = "360 degree full spectrum infrared heater placement"
    mapping = {claim: None}
    matched, ratio = match_claim(claim, VERIFIED_CLAIMS, semantic_mapping=mapping)
    assert matched is not None
    assert matched["id"] == "gbrain-allowlist-360-full-spectrum"


def test_match_claim_with_no_semantic_mapping_behaves_exactly_as_before():
    matched, ratio = match_claim("something with no overlap at all", VERIFIED_CLAIMS)
    assert matched is None
