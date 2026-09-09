"""Shared test doubles. No network calls anywhere in tests -- every Anthropic
client is this fake, injected explicitly."""
import json

import pytest


class FakeUsage:
    def __init__(self, input_tokens=10, output_tokens=10):
        self.input_tokens = input_tokens
        self.output_tokens = output_tokens


class FakeBlock:
    def __init__(self, text):
        self.type = "text"
        self.text = text


class FakeResponse:
    def __init__(self, text, input_tokens=10, output_tokens=10):
        self.content = [FakeBlock(text)]
        self.usage = FakeUsage(input_tokens, output_tokens)


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


@pytest.fixture
def fake_client_factory():
    return lambda responses: FakeClient(responses)
