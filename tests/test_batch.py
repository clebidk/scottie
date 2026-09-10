"""Fix cycle 17 item 5: Message Batches API support. `harness run --batch`
submits the three cartridges' initial writes as one batch at 50% off; these
tests exercise harness.batch directly against a fake batch client -- no
network, and no real sleeping (poll_batch's sleep/clock are injected)."""
import json
from types import SimpleNamespace

import pytest

from harness import batch as batch_mod
from tests.conftest import FakeBlock, FakeUsage
from tests.support import REPO_ROOT, TENANT
from tests.test_render import AD_BRIEF, ARTICLE_PAGE, FACTS_PACK, PRODUCT_PAGE_PAGE


# ---------------------------------------------------------------------------
# Fake batch client -- just enough of client.messages.batches' surface
# (create/retrieve/results) for these tests.
# ---------------------------------------------------------------------------

class _FakeBatch:
    def __init__(self, id, processing_status):
        self.id = id
        self.processing_status = processing_status


class _FakeBatchResult:
    def __init__(self, custom_id, result_type, message=None):
        self.custom_id = custom_id
        self.result = SimpleNamespace(type=result_type, message=message)


def _fake_message(page_obj, **usage_kwargs):
    return SimpleNamespace(content=[FakeBlock(json.dumps(page_obj))], usage=FakeUsage(**usage_kwargs))


class _FakeBatchesAPI:
    def __init__(self, results, statuses=("ended",)):
        self.created_requests = None
        self._results = results
        self._statuses = list(statuses)
        self.retrieve_calls = 0

    def create(self, requests):
        self.created_requests = requests
        return _FakeBatch("batch_123", self._statuses[0])

    def retrieve(self, batch_id):
        idx = min(self.retrieve_calls, len(self._statuses) - 1)
        self.retrieve_calls += 1
        return _FakeBatch(batch_id, self._statuses[idx])

    def results(self, batch_id):
        return self._results


class _FakeBatchClient:
    def __init__(self, results, statuses=("ended",)):
        self.messages = SimpleNamespace(batches=_FakeBatchesAPI(results, statuses))


# ---------------------------------------------------------------------------
# build_batch_requests
# ---------------------------------------------------------------------------

def test_build_batch_requests_one_request_per_cartridge():
    requests, schemas = batch_mod.build_batch_requests(
        cartridge_names=["article", "product-page"],
        cartridges_dir=REPO_ROOT / "cartridges",
        ad_brief=AD_BRIEF,
        facts_pack=FACTS_PACK,
        model="claude-sonnet-5",
        tenant=TENANT,
    )
    assert len(requests) == 2
    assert {r["custom_id"] for r in requests} == {"article", "product-page"}
    assert set(schemas) == {"article", "product-page"}


def test_build_batch_requests_carries_the_cache_control_breakpoints():
    requests, _ = batch_mod.build_batch_requests(
        cartridge_names=["article"],
        cartridges_dir=REPO_ROOT / "cartridges",
        ad_brief=AD_BRIEF,
        facts_pack=FACTS_PACK,
        model="claude-sonnet-5",
        tenant=TENANT,
    )
    params = requests[0]["params"]
    assert params["model"] == "claude-sonnet-5"
    assert params["system"][0]["cache_control"] == {"type": "ephemeral"}
    assert params["messages"][0]["content"][0]["cache_control"] == {"type": "ephemeral"}


def test_build_batch_requests_never_carries_a_revision_note():
    # A batched request is always an "attempt 1" shape -- no repair note.
    requests, _ = batch_mod.build_batch_requests(
        cartridge_names=["article"],
        cartridges_dir=REPO_ROOT / "cartridges",
        ad_brief=AD_BRIEF,
        facts_pack=FACTS_PACK,
        model="claude-sonnet-5",
        tenant=TENANT,
    )
    volatile_block_text = requests[0]["params"]["messages"][0]["content"][1]["text"]
    assert "REVISION REQUIRED" not in volatile_block_text


# ---------------------------------------------------------------------------
# poll_batch -- sleep/clock are injected so this never really waits.
# ---------------------------------------------------------------------------

def test_poll_batch_returns_immediately_when_already_ended():
    client = _FakeBatchClient(results=[], statuses=("ended",))
    sleeps = []
    batch = batch_mod.poll_batch(client, "batch_123", sleep=sleeps.append, clock=lambda: 0.0)
    assert batch.processing_status == "ended"
    assert sleeps == []


def test_poll_batch_polls_until_ended():
    client = _FakeBatchClient(results=[], statuses=("in_progress", "in_progress", "ended"))
    sleeps = []
    clock_calls = iter([0.0, 1.0, 2.0, 3.0])
    batch = batch_mod.poll_batch(
        client, "batch_123", sleep=sleeps.append, clock=lambda: next(clock_calls), poll_interval_s=5,
    )
    assert batch.processing_status == "ended"
    assert sleeps == [5, 5]  # slept once per non-"ended" poll


def test_poll_batch_raises_batch_timeout_past_the_cap():
    client = _FakeBatchClient(results=[], statuses=("in_progress",))
    # clock() jumps straight past timeout_s on the first check inside the loop.
    clock_values = iter([0.0, 5000.0])
    with pytest.raises(batch_mod.BatchTimeout):
        batch_mod.poll_batch(
            client, "batch_123", sleep=lambda s: None, clock=lambda: next(clock_values),
            timeout_s=1200,
        )


# ---------------------------------------------------------------------------
# collect_batch_results
# ---------------------------------------------------------------------------

def _schemas_for(*names):
    _, schemas = batch_mod.build_batch_requests(
        cartridge_names=list(names),
        cartridges_dir=REPO_ROOT / "cartridges",
        ad_brief=AD_BRIEF,
        facts_pack=FACTS_PACK,
        model="claude-sonnet-5",
        tenant=TENANT,
    )
    return schemas


def test_collect_batch_results_parses_a_succeeded_result():
    schemas = _schemas_for("article")
    results = [_FakeBatchResult("article", "succeeded", _fake_message(ARTICLE_PAGE, input_tokens=100, output_tokens=50))]
    client = _FakeBatchClient(results=results)
    out = batch_mod.collect_batch_results(client, "batch_123", schemas)
    page, usage, error = out["article"]
    assert page == ARTICLE_PAGE
    assert usage.input_tokens == 100
    assert error is None


def test_collect_batch_results_reports_a_non_succeeded_result():
    schemas = _schemas_for("article")
    results = [_FakeBatchResult("article", "errored")]
    client = _FakeBatchClient(results=results)
    out = batch_mod.collect_batch_results(client, "batch_123", schemas)
    page, usage, error = out["article"]
    assert page is None
    assert usage is None
    assert "errored" in error


def test_collect_batch_results_reports_invalid_json_without_raising():
    schemas = _schemas_for("article")
    bad_message = SimpleNamespace(content=[FakeBlock("not json at all")], usage=FakeUsage())
    results = [_FakeBatchResult("article", "succeeded", bad_message)]
    client = _FakeBatchClient(results=results)
    out = batch_mod.collect_batch_results(client, "batch_123", schemas)
    page, usage, error = out["article"]
    assert page is None
    assert usage is not None  # usage is still surfaced even though parsing failed
    assert error


def test_collect_batch_results_reports_a_schema_validation_failure():
    schemas = _schemas_for("article")
    bad_page = dict(ARTICLE_PAGE)
    del bad_page["cta"]
    results = [_FakeBatchResult("article", "succeeded", _fake_message(bad_page))]
    client = _FakeBatchClient(results=results)
    out = batch_mod.collect_batch_results(client, "batch_123", schemas)
    page, usage, error = out["article"]
    assert page is None
    assert "missing required key" in error


def test_collect_batch_results_handles_multiple_cartridges_independently():
    schemas = _schemas_for("article", "product-page")
    results = [
        _FakeBatchResult("article", "succeeded", _fake_message(ARTICLE_PAGE)),
        _FakeBatchResult("product-page", "succeeded", _fake_message(PRODUCT_PAGE_PAGE)),
    ]
    client = _FakeBatchClient(results=results)
    out = batch_mod.collect_batch_results(client, "batch_123", schemas)
    assert out["article"][0] == ARTICLE_PAGE
    assert out["product-page"][0] == PRODUCT_PAGE_PAGE


# ---------------------------------------------------------------------------
# pipeline.write_pages with --batch: batch results feed write_and_gate_page
# as initial_page, and repairs (if any) still go through the same client's
# ordinary messages.create.
# ---------------------------------------------------------------------------

class _FakeCombinedMessages:
    """A fake client.messages exposing both .create() (for any synchronous
    repair calls) and .batches.* (for the batch submission itself)."""

    def __init__(self, create_responses, batch_results, statuses=("ended",)):
        from tests.conftest import FakeMessages

        self._create = FakeMessages(create_responses)
        self.batches = _FakeBatchesAPI(batch_results, statuses)

    def create(self, **kwargs):
        return self._create.create(**kwargs)

    @property
    def calls(self):
        return self._create.calls


class _FakeCombinedClient:
    def __init__(self, create_responses, batch_results, statuses=("ended",)):
        self.messages = _FakeCombinedMessages(create_responses, batch_results, statuses)


def _batch_run_state(client, log_path):
    import argparse

    from harness import pipeline
    from harness.log import RunLog

    args = argparse.Namespace(batch=True)
    state = pipeline.RunState(tenant=TENANT, args=args, client=client)
    state.log = RunLog("test-run", log_path)
    state.selected = ["article"]
    state.ad_brief = AD_BRIEF
    state.facts_pack = FACTS_PACK
    return state


def test_write_pages_batch_mode_uses_the_batch_result_with_no_synchronous_call(tmp_path):
    from harness import pipeline

    results = [_FakeBatchResult("article", "succeeded", _fake_message(ARTICLE_PAGE, input_tokens=100, output_tokens=50))]
    client = _FakeCombinedClient(create_responses=[], batch_results=results)
    state = _batch_run_state(client, tmp_path / "run.log")

    pipeline.write_pages(state)

    state.log.close()
    assert state.pages["article"] == ARTICLE_PAGE
    assert client.messages.calls == []  # no synchronous write_page call needed
    assert client.messages.batches.created_requests is not None
    assert state.budget.tokens_used == 150  # the batch result's usage was recorded


def test_write_pages_batch_mode_falls_back_to_a_synchronous_repair_on_a_bad_result(tmp_path):
    from harness import pipeline

    bad_message = _fake_message({"not": "a valid page"})
    results = [_FakeBatchResult("article", "succeeded", bad_message)]
    client = _FakeCombinedClient(create_responses=[json.dumps(ARTICLE_PAGE)], batch_results=results)
    state = _batch_run_state(client, tmp_path / "run.log")

    pipeline.write_pages(state)

    state.log.close()
    assert state.pages["article"] == ARTICLE_PAGE
    assert len(client.messages.calls) == 1  # the synchronous repair/attempt-1 fallback
