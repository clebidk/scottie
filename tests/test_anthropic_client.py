"""Fix cycle 17 item 4 (model tiering): Haiku 4.5 doesn't accept a `thinking`
param at all -- thinking_kwargs is how every call site (write.py, ingest.py,
semantic_match.py) keeps sending `{"type": "disabled"}` for every other model
while sending nothing at all for Haiku."""
from harness.anthropic_client import thinking_kwargs


def test_thinking_kwargs_disabled_for_sonnet():
    assert thinking_kwargs("claude-sonnet-5") == {"thinking": {"type": "disabled"}}


def test_thinking_kwargs_empty_for_haiku():
    assert thinking_kwargs("claude-haiku-4-5") == {}


def test_thinking_kwargs_can_be_unpacked_into_kwargs():
    kwargs = {"model": "claude-haiku-4-5", **thinking_kwargs("claude-haiku-4-5")}
    assert "thinking" not in kwargs
    kwargs = {"model": "claude-sonnet-5", **thinking_kwargs("claude-sonnet-5")}
    assert kwargs["thinking"] == {"type": "disabled"}
