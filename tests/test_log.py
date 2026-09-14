"""Fix cycle 17 item 1: RunLog.cost_estimate() prices each recorded call at
its own model's real rate (harness/pricing.py), instead of one hard-coded
blended $3/$15-per-million rate -- a run can mix claude-sonnet-5 and
claude-haiku-4-5 calls (model tiering, fix cycle 17 item 4), and each needs
its own price."""
import pytest

from harness import pricing
from harness.log import RunLog


def test_cost_estimate_prices_a_single_sonnet_call(tmp_path):
    log = RunLog("test-run", tmp_path / "run.log")
    log.call("write.article", "claude-sonnet-5", 1_000_000, 1_000_000)
    cost = log.cost_estimate()
    log.close()
    assert cost == pytest.approx(12.00)


def test_cost_estimate_prices_each_call_at_its_own_models_rate(tmp_path):
    log = RunLog("test-run", tmp_path / "run.log")
    log.call("ingest.ad_brief", "claude-haiku-4-5", 1_000_000, 1_000_000)  # $1/$5 -> $6
    log.call("write.article", "claude-sonnet-5", 1_000_000, 1_000_000)  # $2/$10 -> $12
    cost = log.cost_estimate()
    log.close()
    assert cost == pytest.approx(18.00)


def test_cost_estimate_includes_cache_read_and_cache_write_tokens(tmp_path):
    log = RunLog("test-run", tmp_path / "run.log")
    log.call(
        "write.article", "claude-sonnet-5", 0, 0,
        cache_creation_input_tokens=1_000_000, cache_read_input_tokens=1_000_000,
    )
    cost = log.cost_estimate()
    log.close()
    expected = pricing.calculate_cost(
        "claude-sonnet-5", cache_creation_input_tokens=1_000_000, cache_read_input_tokens=1_000_000,
    )
    assert cost == pytest.approx(expected)
    assert log.total_cache_creation_input_tokens == 1_000_000
    assert log.total_cache_read_input_tokens == 1_000_000


def test_cost_estimate_applies_batch_discount_per_call(tmp_path):
    log = RunLog("test-run", tmp_path / "run.log")
    log.call("write.article", "claude-sonnet-5", 1_000_000, 1_000_000, batch=True)
    cost = log.cost_estimate()
    log.close()
    assert cost == pytest.approx(12.00 * pricing.BATCH_MULTIPLIER)


def test_call_writes_cache_and_batch_fields_to_the_log_line(tmp_path):
    log_path = tmp_path / "run.log"
    log = RunLog("test-run", log_path)
    log.call(
        "write.article", "claude-sonnet-5", 10, 20,
        cache_creation_input_tokens=30, cache_read_input_tokens=40, batch=True,
    )
    log.close()
    text = log_path.read_text()
    assert "cache_creation_input_tokens=30" in text
    assert "cache_read_input_tokens=40" in text
    assert "batch=true" in text


def test_cost_estimate_line_names_total_tokens(tmp_path):
    log_path = tmp_path / "run.log"
    log = RunLog("test-run", log_path)
    log.call("ingest.ad_brief", "claude-haiku-4-5", 100, 50)
    log.cost_estimate()
    log.close()
    text = log_path.read_text()
    assert "total_input_tokens=100" in text
    assert "total_output_tokens=50" in text


# ---------------------------------------------------------------------------
# Cycle 33: RunLog as a context manager + idempotent close
# ---------------------------------------------------------------------------

def test_run_log_context_manager_writes_and_closes(tmp_path):
    log_path = tmp_path / "run.log"
    with RunLog("test-run", log_path) as log:
        log.event("stage", "hello")
        assert not log._fh.closed
    assert log._fh.closed
    assert "stage: hello" in log_path.read_text()


def test_run_log_close_is_idempotent(tmp_path):
    log = RunLog("test-run", tmp_path / "run.log")
    log.close()
    log.close()  # must not raise
    assert log._fh.closed


def test_run_log_context_manager_closes_on_exception(tmp_path):
    log_path = tmp_path / "run.log"
    with pytest.raises(RuntimeError, match="boom"):
        with RunLog("test-run", log_path) as log:
            log.event("stage", "before-raise")
            raise RuntimeError("boom")
    assert log._fh.closed
    assert "before-raise" in log_path.read_text()
