import json

import pytest

from adv import ingest
from adv.budget import Budget
from adv.log import RunLog
from tests.conftest import FakeClient, json_response


# ---------------------------------------------------------------------------
# Drive id parsing
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "url,expected_id",
    [
        ("https://drive.google.com/file/d/1zTKy1NWK8QB8w5hnxoP9z4bXN-M34rtZ/view?usp=sharing", "1zTKy1NWK8QB8w5hnxoP9z4bXN-M34rtZ"),
        ("https://drive.google.com/uc?export=download&id=1zTKy1NWK8QB8w5hnxoP9z4bXN-M34rtZ", "1zTKy1NWK8QB8w5hnxoP9z4bXN-M34rtZ"),
        ("1zTKy1NWK8QB8w5hnxoP9z4bXN-M34rtZ", "1zTKy1NWK8QB8w5hnxoP9z4bXN-M34rtZ"),
    ],
)
def test_parse_drive_id(url, expected_id):
    assert ingest.parse_drive_id(url) == expected_id


def test_parse_drive_id_none_for_junk():
    # too short to plausibly be a Drive id, and not a Drive URL shape
    assert ingest.parse_drive_id("short") is None
    assert ingest.parse_drive_id("plain sentence with spaces") is None


# ---------------------------------------------------------------------------
# Input type detection
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "filename,expected",
    [
        ("clip.mov", "video"),
        ("clip.mp4", "video"),
        ("clip.m4v", "video"),
        ("clip.webm", "video"),
        ("still.png", "still"),
        ("still.jpg", "still"),
        ("still.jpeg", "still"),
        ("still.webp", "still"),
        ("notes.txt", "text"),
        ("notes.md", "text"),
    ],
)
def test_detect_type(filename, expected):
    assert ingest.detect_type(filename) == expected


def test_detect_type_unknown_extension():
    with pytest.raises(ValueError):
        ingest.detect_type("song.mp3")


# ---------------------------------------------------------------------------
# ad_brief validation + retry-once
# ---------------------------------------------------------------------------

VALID_AD_BRIEF = {
    "hook": "hook text",
    "promise": "promise text",
    "angle": "angle text",
    "claims_made": ["The Peak Saunas Fuji is priced at $8250."],
    "speaker_experience": ["I hated calling for a price."],
    "features_shown": ["price shown up front"],
    "objections_raised": ["brands hide the price"],
    "cta": "See pricing",
    "tone": "candid",
    "speaker_pov": "first_person",
    "source_file": "ad.txt",
    "input_type": "text",
    "transcript_or_text": "full transcript here",
}


def _budget_log(tmp_path):
    budget = Budget()
    log = RunLog("test-run", tmp_path / "run.log")
    return budget, log


def test_build_ad_brief_valid_on_first_try(tmp_path):
    client = FakeClient([json_response(VALID_AD_BRIEF)])
    budget, log = _budget_log(tmp_path)
    result = ingest.build_ad_brief(
        transcript_or_text="hello", source_file="ad.txt", input_type="text",
        client=client, model="claude-sonnet-5", budget=budget, log=log,
    )
    log.close()
    assert result == VALID_AD_BRIEF
    assert len(client.messages.calls) == 1


def test_build_ad_brief_retries_once_on_bad_json(tmp_path):
    client = FakeClient(["not json at all", json_response(VALID_AD_BRIEF)])
    budget, log = _budget_log(tmp_path)
    result = ingest.build_ad_brief(
        transcript_or_text="hello", source_file="ad.txt", input_type="text",
        client=client, model="claude-sonnet-5", budget=budget, log=log,
    )
    log.close()
    assert result == VALID_AD_BRIEF
    assert len(client.messages.calls) == 2


def test_build_ad_brief_fails_after_two_bad_responses(tmp_path):
    client = FakeClient(["garbage 1", "garbage 2"])
    budget, log = _budget_log(tmp_path)
    with pytest.raises(ValueError):
        ingest.build_ad_brief(
            transcript_or_text="hello", source_file="ad.txt", input_type="text",
            client=client, model="claude-sonnet-5", budget=budget, log=log,
        )
    log.close()
    assert len(client.messages.calls) == 2


def test_build_ad_brief_rejects_missing_key(tmp_path):
    bad = dict(VALID_AD_BRIEF)
    del bad["speaker_experience"]
    client = FakeClient([json_response(bad), json_response(VALID_AD_BRIEF)])
    budget, log = _budget_log(tmp_path)
    result = ingest.build_ad_brief(
        transcript_or_text="hello", source_file="ad.txt", input_type="text",
        client=client, model="claude-sonnet-5", budget=budget, log=log,
    )
    log.close()
    assert result == VALID_AD_BRIEF
    assert len(client.messages.calls) == 2
