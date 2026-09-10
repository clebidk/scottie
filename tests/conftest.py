"""Shared test doubles. No network calls anywhere in tests -- every Anthropic
client is this fake, injected explicitly."""
import json

import pytest


class FakeUsage:
    def __init__(self, input_tokens=10, output_tokens=10, *,
                 cache_creation_input_tokens=0, cache_read_input_tokens=0):
        self.input_tokens = input_tokens
        self.output_tokens = output_tokens
        # Fix cycle 17: real Anthropic SDK Usage objects always carry these
        # two fields (zero when a call has no cache_control breakpoint) --
        # defaulted here so every existing test double keeps working.
        self.cache_creation_input_tokens = cache_creation_input_tokens
        self.cache_read_input_tokens = cache_read_input_tokens


class FakeBlock:
    def __init__(self, text):
        self.type = "text"
        self.text = text


class FakeResponse:
    def __init__(self, text, input_tokens=10, output_tokens=10, *,
                 cache_creation_input_tokens=0, cache_read_input_tokens=0):
        self.content = [FakeBlock(text)]
        self.usage = FakeUsage(
            input_tokens, output_tokens,
            cache_creation_input_tokens=cache_creation_input_tokens,
            cache_read_input_tokens=cache_read_input_tokens,
        )


class FakeMessages:
    def __init__(self, responses):
        # each item is either a raw string (JSON or garbage), a FakeResponse,
        # or an Exception instance to raise.
        self._responses = list(responses)
        self.calls = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        if not self._responses:
            raise AssertionError("FakeClient ran out of canned responses")
        item = self._responses.pop(0)
        if isinstance(item, Exception):
            raise item
        if isinstance(item, str):
            return FakeResponse(item)
        return item


class FakeClient:
    def __init__(self, responses):
        self.messages = FakeMessages(responses)


def json_response(obj):
    return json.dumps(obj)


def block_text(content):
    """Fix cycle 17: a system/message "content" value sent to
    client.messages.create is now either a plain string, or a list of
    {"type": "text", "text": ..., ["cache_control": ...]} blocks (write.py's
    cache_control breakpoints) -- joins either shape into one string so a
    test can keep doing plain substring assertions regardless of which shape
    a given call used."""
    if isinstance(content, str):
        return content
    return "".join(b["text"] for b in content if isinstance(b, dict) and b.get("type") == "text")


@pytest.fixture
def fake_client_factory():
    return lambda responses: FakeClient(responses)
