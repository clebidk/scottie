import json

import pytest

from harness.jsonutil import extract_json


def test_extract_json_parses_plain_json():
    assert extract_json('{"a": 1}') == {"a": 1}


def test_extract_json_strips_markdown_fences():
    assert extract_json('```json\n{"a": 1}\n```') == {"a": 1}


def test_extract_json_strips_bare_fences_without_language_tag():
    assert extract_json('```\n{"a": 1}\n```') == {"a": 1}


# ---------------------------------------------------------------------------
# Cycle 6 verification: `adv run` crashed with
# "ValueError: write.article failed after retry: Extra data: line 1 column
# 1219" -- the model appended trailing content after an otherwise complete
# page.json object on both of write_page's retry attempts.
# ---------------------------------------------------------------------------

def test_extract_json_ignores_trailing_extra_data():
    text = json.dumps({"a": 1}) + "\n\nHope that helps!"
    assert extract_json(text) == {"a": 1}


def test_extract_json_ignores_a_duplicated_trailing_json_blob():
    text = json.dumps({"a": 1}) + json.dumps({"a": 1})
    assert extract_json(text) == {"a": 1}


def test_extract_json_still_raises_on_genuinely_malformed_json():
    with pytest.raises(json.JSONDecodeError):
        extract_json('{"a": }')

