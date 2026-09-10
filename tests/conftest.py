"""Shared test doubles and the two suite-wide guarantees.

No network calls anywhere in tests -- every Anthropic client is this fake,
injected explicitly, and since Cycle 22 that is enforced rather than assumed
(see _no_network below).
"""
import json
import socket

import pytest

from harness import tenant as tenant_mod
from harness import vocab


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


# ---------------------------------------------------------------------------
# Cycle 22 findings R31/R32: two things that were conventions, now guarantees.
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def _restore_active_tenant():
    """harness/tenant.py and harness/vocab.py each keep the active tenant in a
    module-level global, and cli.cmd_run calls activate() without ever putting
    it back. Every test that ran after tests/test_cli_run.py therefore saw a
    different active tenant depending on collection order -- and every module
    that falls back to tenant_mod.active() / vocab.active() (render_page, the
    claims gate, the writer prompt) reads that global. Exactly one test used to
    do this correctly, with its own try/finally."""
    previous_tenant = tenant_mod.active_or_none()
    previous_vocab = vocab.active_or_none()
    try:
        yield
    finally:
        tenant_mod.set_active(previous_tenant)
        vocab.set_active(previous_vocab)


@pytest.fixture(autouse=True)
def _no_network(request, monkeypatch):
    """README.md has always said the suite makes no network calls. That was
    true, but only by convention -- nothing stopped a new test that forgot to
    inject a fake fetcher from hitting a real storefront, a real Drive file, or
    a real model. Any attempt to open a socket now fails the test that made it.

    A test marked `live` opts out. There are none today."""
    if request.node.get_closest_marker("live"):
        return

    def _blocked(*args, **kwargs):
        raise AssertionError(
            "this test tried to open a network connection -- the suite runs "
            "offline. Inject a fake client/fetcher, or mark the test `live`."
        )

    monkeypatch.setattr(socket.socket, "connect", _blocked)
    monkeypatch.setattr(socket.socket, "connect_ex", _blocked)
    monkeypatch.setattr(socket, "create_connection", _blocked)
