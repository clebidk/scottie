"""Cycle 76 addendum: what keeps a best-of-2 listicle run cheap -- one prompt
cache shared by both drafts (draft 2 starts once draft 1's response has
started), the trimmed writer copy of the facts pack, the Message Batches
path for non-interactive builds, and the minimum-margin pick. Every client
here is faked."""
import argparse
import json
import threading
import time
import types

import pytest

from evals import fake_run
from harness import abtest, batch as batch_mod, drafts, jev, pipeline, runstate, write
from tests.conftest import FakeBlock, FakeResponse, FakeUsage, block_text
from tests.support import TENANT
from tests.test_jev_cycle76 import (
    FIXTURE, PINNED_HEADLINE, FakeJev, RoutedClient, STYLE, _good_page, _bad_page, _jev_record, _log_text, _other_good_page, _run,
    _skeleton_ids, two_drafts,  # noqa: F401 -- the fixture
)


def _write_calls(client, sk):
    return client.writes_for(sk)


# ---------------------------------------------------------------------------
# one prompt cache for both drafts
# ---------------------------------------------------------------------------

def test_both_drafts_send_a_byte_identical_cached_prefix(two_drafts, monkeypatch):
    sk1, sk2 = _skeleton_ids()
    client = RoutedClient({sk1: [_good_page()], sk2: [_other_good_page()]})
    monkeypatch.setattr(jev, "api_key", lambda: "")
    rc, _run_dir = _run(client)
    assert rc == 0
    (c1,), (c2,) = _write_calls(client, sk1), _write_calls(client, sk2)
    # everything up to and including the last cache breakpoint is identical
    assert c1["system"] == c2["system"]
    assert c1["messages"][0]["content"][0] == c2["messages"][0]["content"][0]
    assert c1["messages"][0]["content"][0]["cache_control"] == {"type": "ephemeral"}
    assert "cache_control" not in c1["messages"][0]["content"][1]
    # each draft's own lines come after it
    tail1, tail2 = c1["messages"][0]["content"][1]["text"], c2["messages"][0]["content"][1]["text"]
    assert tail1 != tail2
    for tail, sk in ((tail1, sk1), (tail2, sk2)):
        assert "## Hard constraints for this page" in tail and f'"id": "{sk}"' in tail
    system = block_text(c1["system"])
    assert "Its headline must follow this formula exactly" not in system
    assert "Its headline must follow this formula exactly" in tail1
    # the shared block carries the ad brief and the listicle extras, once
    shared = json.loads(c1["messages"][0]["content"][0]["text"])
    assert set(shared) == {"facts_pack", "ad_brief", "ad_quotes", "allowed_numbers"}


def test_a_repair_reuses_draft_ones_cached_prefix(two_drafts, monkeypatch):
    sk1, sk2 = _skeleton_ids()
    client = RoutedClient({sk1: [_bad_page(), _good_page()], sk2: [_bad_page()]})
    monkeypatch.setattr(jev, "api_key", lambda: "")
    rc, _run_dir = _run(client)
    assert rc == 0
    first, repair_call = _write_calls(client, sk1)
    assert first["system"] == repair_call["system"]
    assert first["messages"][0]["content"][0] == repair_call["messages"][0]["content"][0]
    assert "REVISION REQUIRED" in repair_call["messages"][0]["content"][1]["text"]


class _Stream:
    def __init__(self, client, kwargs):
        self.client, self.kwargs = client, kwargs

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def __iter__(self):
        time.sleep(0.3)  # the prompt is being read
        self.client.timeline.append(("message_start", RoutedClient.skeleton_of(self.kwargs)))
        yield types.SimpleNamespace(type="message_start")
        yield types.SimpleNamespace(type="content_block_delta")

    def get_final_message(self):
        return self.client.create(_record=False, **self.kwargs)


class StreamingClient(RoutedClient):
    """RoutedClient whose messages can also stream (draft 1 streams)."""

    def __init__(self, by_skeleton):
        super().__init__(by_skeleton)
        self.timeline = []
        self.messages = types.SimpleNamespace(create=self.create, stream=self.stream)

    def stream(self, **kwargs):
        self.timeline.append(("request", self.skeleton_of(kwargs)))
        return _Stream(self, kwargs)

    def create(self, _record=True, **kwargs):
        if _record:
            self.timeline.append(("request", self.skeleton_of(kwargs)))
        return super().create(**kwargs)


def test_draft_two_starts_only_after_draft_ones_response_has_started(two_drafts, monkeypatch):
    sk1, sk2 = _skeleton_ids()
    client = StreamingClient({sk1: [_good_page()], sk2: [_other_good_page()]})
    monkeypatch.setattr(jev, "api_key", lambda: "")
    rc, _run_dir = _run(client)
    assert rc == 0
    events = [e for e in client.timeline if e[1]]
    assert events.index(("message_start", sk1)) < events.index(("request", sk2))
    assert events[0] == ("request", sk1)


def test_draft_two_does_not_wait_forever_when_draft_one_never_starts(two_drafts, monkeypatch):
    sk1, sk2 = _skeleton_ids()
    client = RoutedClient({sk1: [_good_page()], sk2: [_other_good_page()]})
    monkeypatch.setattr(jev, "api_key", lambda: "")
    monkeypatch.setattr(drafts, "CACHE_WAIT_S", 0.2)
    release = threading.Event()
    real_create = client.create

    def slow_create(**kwargs):
        if client.skeleton_of(kwargs) == sk1:
            release.wait(3)
        elif client.skeleton_of(kwargs) == sk2:
            release.set()
        return real_create(**kwargs)

    client.messages = types.SimpleNamespace(create=slow_create)
    rc, run_dir = _run(client)
    assert rc == 0
    assert "draft 1 had not started after 0.2s" in _log_text(run_dir)


def test_create_message_without_a_callback_or_a_stream_is_a_plain_create():
    calls = []
    client = types.SimpleNamespace(messages=types.SimpleNamespace(create=lambda **k: calls.append(k) or "r"))
    assert write._create_message(client, None, model="m") == "r"
    started = []
    assert write._create_message(client, lambda: started.append(1), model="m") == "r"
    assert started == [1]


# ---------------------------------------------------------------------------
# the writer's copy of the facts pack
# ---------------------------------------------------------------------------

def test_writer_facts_pack_drops_urls_the_listicle_writer_never_cites():
    fp = {
        "product": {"name": "X", "url": "https://shop/x", "image_urls": ["https://cdn/a.jpg"]},
        "assets": [{"id": "asset-1", "url": "https://cdn/a.jpg", "kind": "image", "alt": "front"}],
        "verified_claims": [{"id": "c1", "text": "t", "category": "spec", "source": "https://shop/x"}],
        "specs": {"a": 1},
    }
    out = write.writer_facts_pack("listicle", fp)
    assert out["product"] == {"name": "X", "url": "https://shop/x"}
    assert out["assets"] == [{"id": "asset-1", "kind": "image", "alt": "front"}]
    assert out["verified_claims"] == [{"id": "c1", "text": "t", "category": "spec"}]
    assert out["specs"] == {"a": 1}
    assert fp["assets"][0]["url"] == "https://cdn/a.jpg"  # the run's own pack is untouched
    assert write.writer_facts_pack("article", fp) is fp


# ---------------------------------------------------------------------------
# minimum margin
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("draft2_level, shipped", [(2.04, 1), (2.2, 2)])
def test_a_later_draft_ships_only_when_it_wins_by_the_minimum_margin(two_drafts, monkeypatch, draft2_level,
                                                                      shipped):
    sk1, sk2 = _skeleton_ids()
    client = RoutedClient({sk1: [_good_page()], sk2: [_other_good_page()]})
    second = _other_good_page()["audience_fit"]["for_you"][0]["text"]
    monkeypatch.setattr(jev, "_http_post", FakeJev(
        scores=lambda text: draft2_level if text.split("Who this is for:\n- ", 1)[1].startswith(second) else 2.0))
    rc, run_dir = _run(client)
    assert rc == 0
    record = _jev_record(run_dir)
    assert record["shipped"] == shipped and record["min_margin"] == 0.02
    if shipped == 1:
        assert "min_margin" in record["reason"]


# ---------------------------------------------------------------------------
# Message Batches
# ---------------------------------------------------------------------------

class _Batches:
    def __init__(self, client, status="ended"):
        self.client, self.status = client, status
        self.requests, self.cancelled = None, []

    def create(self, requests):
        self.requests = requests
        return types.SimpleNamespace(id="batch_1", processing_status=self.status)

    def retrieve(self, batch_id):
        return types.SimpleNamespace(id=batch_id, processing_status=self.status)

    def cancel(self, batch_id):
        self.cancelled.append(batch_id)

    def results(self, batch_id):
        for r in self.requests:
            sk = RoutedClient.skeleton_of(r["params"])
            page = self.client.by_skeleton[sk].pop(0)
            msg = types.SimpleNamespace(content=[FakeBlock(json.dumps(page))],
                                        usage=FakeUsage(100, 50, cache_read_input_tokens=7))
            yield types.SimpleNamespace(custom_id=r["custom_id"], result=types.SimpleNamespace(type="succeeded",
                                                                                              message=msg))


class BatchClient(RoutedClient):
    def __init__(self, by_skeleton, status="ended"):
        super().__init__(by_skeleton)
        self.batches = _Batches(self, status)
        self.messages = types.SimpleNamespace(create=self.create, batches=self.batches)


def _run_batch(client):
    args = argparse.Namespace(
        input=str(FIXTURE), cartridges="listicle", seed=42, style=STYLE, headline_template=PINNED_HEADLINE,
        skeleton=None, product=None, ffmpeg_bin="/usr/bin/ffmpeg", whisper_bin="/nonexistent/whisper-cli",
        whisper_model="/nonexistent/model.bin", tenant="peak-saunas", batch=True,
    )
    from harness import cli
    from tests.support import newest_run_dir

    with fake_run.fake_environment(client):
        rc = cli.cmd_run(args)
    return rc, newest_run_dir(TENANT.out_dir, "*-founder-warranty-demo-*")


def test_batch_mode_sends_both_drafts_in_one_batch_and_gates_each(two_drafts, monkeypatch):
    sk1, sk2 = _skeleton_ids()
    client = BatchClient({sk1: [_good_page()], sk2: [_other_good_page()]})
    monkeypatch.setattr(jev, "api_key", lambda: "")
    rc, run_dir = _run_batch(client)
    assert rc == 0
    assert [r["custom_id"] for r in client.batches.requests] == ["listicle", "listicle-draft-2"]
    assert [client.skeleton_of(r["params"]) for r in client.batches.requests] == [sk1, sk2]
    p1, p2 = (r["params"] for r in client.batches.requests)
    assert p1["system"] == p2["system"]
    assert p1["messages"][0]["content"][0] == p2["messages"][0]["content"][0]
    # no real-time listicle write at all
    assert client.writes_for(sk1) == [] and client.writes_for(sk2) == []
    record = _jev_record(run_dir)
    assert [d["gate"] for d in record["drafts"]] == ["PASS", "PASS"] and record["shipped"] == 1
    log = _log_text(run_dir)
    assert "write.listicle-draft-2: model=" in log and "batch=true" in log
    assert "batch wait" in log and "(batch)" in log


def test_a_batch_that_does_not_end_is_cancelled_and_the_drafts_are_written_in_real_time(two_drafts, monkeypatch):
    sk1, sk2 = _skeleton_ids()
    client = BatchClient({sk1: [_good_page()], sk2: [_other_good_page()]}, status="in_progress")
    monkeypatch.setattr(jev, "api_key", lambda: "")
    monkeypatch.setattr(batch_mod, "settings", lambda tenant: {"non_interactive": True, "timeout_s": 0})
    rc, run_dir = _run_batch(client)
    assert rc == 0
    assert client.batches.cancelled == ["batch_1"]
    assert len(client.writes_for(sk1)) == 1 and len(client.writes_for(sk2)) == 1
    assert "cancelled, writing every page in real time" in _log_text(run_dir)


def test_batch_wait_is_not_counted_in_the_wall_clock_budget(monkeypatch):
    state = types.SimpleNamespace(
        budget=pipeline.Budget(), log=types.SimpleNamespace(event=lambda *a: None, call=lambda *a, **k: None),
        tenant=TENANT, selected=["article"], listicle_style=None, listicle_headline=None, listicle_skeleton=None,
        ad_brief={}, facts_pack={}, ad_not_repeated=[],
        client=types.SimpleNamespace(messages=types.SimpleNamespace(batches=types.SimpleNamespace(
            create=lambda requests: types.SimpleNamespace(id="b")))),
    )
    monkeypatch.setattr(batch_mod, "build_batch_requests", lambda **k: ([], {}))
    monkeypatch.setattr(batch_mod, "poll_batch", lambda *a, **k: time.sleep(0.3))
    monkeypatch.setattr(batch_mod, "collect_batch_results", lambda *a: {})
    pipeline._write_initial_pages_via_batch(state, "m")
    assert state.budget.wall_s >= 300.3


def test_abtest_builds_batch_when_the_tenant_says_non_interactive(monkeypatch):
    seen = {}

    def fake_execute(state, stages):
        seen["batch"] = state.args.batch
        return 0

    monkeypatch.setattr(pipeline, "execute", fake_execute)
    monkeypatch.setattr("harness.anthropic_client.make_client", lambda: object())
    arm = abtest.parse_arm(f"listicle:{STYLE}", TENANT)
    abtest.default_runner(TENANT, str(FIXTURE), arm, 1)
    assert seen["batch"] is True
    assert batch_mod.settings(TENANT) == {"non_interactive": True, "timeout_s": 1800.0}
    monkeypatch.setattr(batch_mod, "settings", lambda tenant: {"non_interactive": False, "timeout_s": 1})
    abtest.default_runner(TENANT, str(FIXTURE), arm, 1)
    assert seen["batch"] is False
