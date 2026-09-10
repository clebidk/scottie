from unittest.mock import patch

import pytest

from harness import ingest
from harness.budget import Budget
from harness.log import RunLog
from tests.conftest import FakeClient, json_response


# ---------------------------------------------------------------------------
# Fix cycle 8 problem 1a: whisper-cli's initial prompt biases decoding toward
# Peak Saunas' own vocabulary so it stops mishearing "Sauna" as "Sonna".
# ---------------------------------------------------------------------------

def test_video_to_transcript_passes_whisper_initial_prompt(tmp_path):
    with patch("harness.ingest.subprocess.run") as mock_run:
        mock_run.return_value.stdout = "a transcript"
        (tmp_path / "in.mov").write_bytes(b"fake")
        result = ingest.video_to_transcript(
            tmp_path / "in.mov", tmp_path, "ffmpeg", "whisper-cli", "model.bin",
        )
    assert result == "a transcript"
    whisper_call = mock_run.call_args_list[1]
    whisper_cmd = whisper_call.args[0]
    assert "--prompt" in whisper_cmd
    prompt_arg = whisper_cmd[whisper_cmd.index("--prompt") + 1]
    assert prompt_arg == ingest.WHISPER_INITIAL_PROMPT
    # every model name the picker needs to recognize is in the prompt
    for name in ("Fuji", "Everest", "Rainier", "Shasta", "Denali", "Matterhorn", "Patagonia", "El Capitan", "Kilimanjaro", "Mini"):
        assert name in ingest.WHISPER_INITIAL_PROMPT
    assert "Peak Saunas" in ingest.WHISPER_INITIAL_PROMPT
    assert "Sauna" in ingest.WHISPER_INITIAL_PROMPT


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
    "audience": "",
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


# ---------------------------------------------------------------------------
# Fix cycle 9 item 3: a statement framed as the speaker's own research,
# estimate, or hedge ("I've been seeing", "around $", "say $", "I did the
# math", "I realized") goes into speaker_experience, not claims_made, even
# when it carries a number -- it's the speaker's own approximation, not an
# independently checkable fact. Only a plain factual assertion with no hedge
# belongs in claims_made.
# ---------------------------------------------------------------------------

def test_ad_brief_prompt_instructs_hedged_statements_into_speaker_experience():
    for phrase in ("I've been seeing", "around $", "say $", "I did the math", "I realized"):
        assert phrase in ingest.AD_BRIEF_SYSTEM
    assert "speaker_experience" in ingest.AD_BRIEF_SYSTEM
    assert "$5,450" in ingest.AD_BRIEF_SYSTEM  # the real, un-hedged claim example


PRICE_COMPARISON_TRANSCRIPT = (
    "I keep hearing all the benefits of infrared saunas, so I really want to get into it. "
    "I've been taking some time to research different studios and memberships. And during "
    "my research, I realized that at-home saunas are an option. So I decided to do the math "
    "and see if it made sense financially to buy one of these. I've been seeing that the "
    "average unlimited sauna membership is around $200 a month. So say $2,400 a year. "
    "infrared sauna is on sale right now for $5,450. That means after 28 months, I will "
    "spend the same amount of money on a membership as I would actually owning a sauna."
)

# The intended, correctly-classified shape of this transcript's ad_brief
# (fix cycle 9 item 3's target contract) -- claims_made carries only the one
# un-hedged factual assertion, matching the Mini's live price claim.
PRICE_COMPARISON_AD_BRIEF = dict(
    VALID_AD_BRIEF,
    claims_made=["infrared sauna is on sale right now for $5,450"],
    speaker_experience=[
        "I've been seeing that the average unlimited sauna membership is around $200 a month.",
        "So say $2,400 a year.",
        "I decided to do the math and see if it made sense financially to buy one of these.",
        "After 28 months, I will spend the same amount of money on a membership as I would actually owning a sauna.",
    ],
    source_file="price-comparison-v2.transcript.txt",
    transcript_or_text=PRICE_COMPARISON_TRANSCRIPT,
)


def test_build_ad_brief_accepts_the_correctly_hedged_classification(tmp_path):
    """Not a test of real model behavior (that's verified live) -- this locks
    in the target contract: once the ingest model classifies the transcript
    this way, claims_made contains only the un-hedged $5,450 item, which is
    exactly the Mini's price claim."""
    client = FakeClient([json_response(PRICE_COMPARISON_AD_BRIEF)])
    budget, log = _budget_log(tmp_path)
    result = ingest.build_ad_brief(
        transcript_or_text=PRICE_COMPARISON_TRANSCRIPT, source_file="price-comparison-v2.transcript.txt",
        input_type="text", client=client, model="claude-sonnet-5", budget=budget, log=log,
    )
    log.close()
    assert result["claims_made"] == ["infrared sauna is on sale right now for $5,450"]
    assert not any("200" in s or "2,400" in s for s in result["claims_made"])


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


# ---------------------------------------------------------------------------
# Fix cycle 16 item 7 (Thursday queue item 3, "audience named in H1"):
# ad_brief gains an "audience" field.
# ---------------------------------------------------------------------------


def test_valid_ad_brief_requires_the_audience_key():
    bad = dict(VALID_AD_BRIEF)
    del bad["audience"]
    with pytest.raises(ValueError, match="audience"):
        ingest.validate_ad_brief(bad)


def test_validate_ad_brief_accepts_an_empty_audience_string():
    ad_brief = dict(VALID_AD_BRIEF, audience="")
    ingest.validate_ad_brief(ad_brief)  # does not raise


def test_validate_ad_brief_accepts_a_named_audience():
    ad_brief = dict(VALID_AD_BRIEF, audience="busy parents")
    ingest.validate_ad_brief(ad_brief)  # does not raise


def test_build_ad_brief_rejects_a_non_string_audience(tmp_path):
    bad = dict(VALID_AD_BRIEF, audience=None)
    client = FakeClient([json_response(bad), json_response(VALID_AD_BRIEF)])
    budget, log = _budget_log(tmp_path)
    result = ingest.build_ad_brief(
        transcript_or_text="hello", source_file="ad.txt", input_type="text",
        client=client, model="claude-sonnet-5", budget=budget, log=log,
    )
    log.close()
    assert result == VALID_AD_BRIEF
    assert len(client.messages.calls) == 2


def test_ad_brief_system_prompt_asks_for_audience():
    assert "audience" in ingest.AD_BRIEF_SYSTEM


# ---------------------------------------------------------------------------
# Fix cycle 17 item 4 (model tiering): Haiku 4.5 rejects a `thinking` param
# outright -- build_ad_brief must send no `thinking` key at all for it.
# ---------------------------------------------------------------------------

def test_build_ad_brief_omits_thinking_for_a_haiku_model(tmp_path):
    client = FakeClient([json_response(VALID_AD_BRIEF)])
    budget, log = _budget_log(tmp_path)
    ingest.build_ad_brief(
        transcript_or_text="hello", source_file="ad.txt", input_type="text",
        client=client, model="claude-haiku-4-5", budget=budget, log=log,
    )
    log.close()
    assert "thinking" not in client.messages.calls[0]


def test_build_ad_brief_keeps_thinking_disabled_for_sonnet(tmp_path):
    client = FakeClient([json_response(VALID_AD_BRIEF)])
    budget, log = _budget_log(tmp_path)
    ingest.build_ad_brief(
        transcript_or_text="hello", source_file="ad.txt", input_type="text",
        client=client, model="claude-sonnet-5", budget=budget, log=log,
    )
    log.close()
    assert client.messages.calls[0]["thinking"] == {"type": "disabled"}
