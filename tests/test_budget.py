import json
import threading
import time

import pytest

from harness.budget import (
    Budget,
    BudgetExceeded,
    LedgerWriteError,
    daily_cap_usd,
    daily_reserved,
    daily_spend,
    reconcile_stale_reservations,
    record_spend,
    reserve_spend,
    spend_ledger_path,
)


def test_budget_passes_under_caps():
    b = Budget(wall_s=300, tokens=1000, calls=5)
    b.record_call(100, 100)
    b.record_call(100, 100)
    b.check()  # should not raise
    assert b.calls_used == 2
    assert b.tokens_used == 400


def test_budget_trips_on_call_cap():
    b = Budget(wall_s=300, tokens=1_000_000, calls=2)
    b.record_call(1, 1)
    b.record_call(1, 1)
    with pytest.raises(BudgetExceeded) as exc_info:
        b.record_call(1, 1)
    assert exc_info.value.kind == "calls"


def test_budget_trips_on_token_cap():
    b = Budget(wall_s=300, tokens=100, calls=100)
    with pytest.raises(BudgetExceeded) as exc_info:
        b.record_call(60, 60)
    assert exc_info.value.kind == "tokens"


def test_budget_trips_on_wall_clock():
    b = Budget(wall_s=0.01, tokens=1_000_000, calls=100)
    time.sleep(0.05)
    with pytest.raises(BudgetExceeded) as exc_info:
        b.check()
    assert exc_info.value.kind == "wall_s"


# ---------------------------------------------------------------------------
# Fix cycle 12 item 1: raised default caps -- exemplar trimming (write.
# load_exemplars: at most 700 words of at most 2 exemplars) cut the typical
# per-call prompt enough that three cartridges at two repairs each fit inside
# a bigger budget without needing more wall clock (measured on the server,
# docs/FIXLOG.md Cycle 12).
# ---------------------------------------------------------------------------

def test_budget_defaults_raised_by_cycle_12():
    b = Budget()
    assert b.token_limit == 220_000
    assert b.call_limit == 14
    assert b.wall_s == 300


# ---------------------------------------------------------------------------
# Kimi long-run phase 3 (landscape borrowing #9): the tenant daily spend cap.
# ---------------------------------------------------------------------------

class _TenantDouble:
    """Just enough Tenant for the daily-cap functions: claims_config
    (claims/config.json's layer, which wins), tenant.yaml's budget block via
    get(), runs_dir for the ledger, and name for the refusal message."""

    def __init__(self, *, claims_config=None, yaml_budget=None, runs_dir=None, name="acme"):
        self._claims_config = claims_config or {}
        self._yaml_budget = yaml_budget
        self.runs_dir = runs_dir
        self.name = name

    @property
    def claims_config(self):
        return self._claims_config

    def get(self, dotted_key, default=None):
        if dotted_key == "budget":
            return self._yaml_budget if self._yaml_budget is not None else default
        return default


def test_daily_cap_unset_means_uncapped(tmp_path):
    tenant = _TenantDouble(runs_dir=tmp_path)
    assert daily_cap_usd(tenant) is None
    reserve_spend(tenant, run_id="run-a", today_iso="2026-09-11")  # never raises
    # K2: an uncapped tenant needs no reservation at all -- nothing to gate.
    assert not spend_ledger_path(tenant).exists()


def test_daily_cap_reads_config_json_then_tenant_yaml(tmp_path):
    assert daily_cap_usd(_TenantDouble(yaml_budget={"daily_usd": 5}, runs_dir=tmp_path)) == 5.0
    both = _TenantDouble(claims_config={"budget": {"daily_usd": 2}}, yaml_budget={"daily_usd": 5}, runs_dir=tmp_path)
    assert daily_cap_usd(both) == 2.0


def test_record_spend_and_daily_spend_sum_today_only(tmp_path):
    tenant = _TenantDouble(runs_dir=tmp_path)
    record_spend(tenant, run_id="run-a", cost=0.25, today_iso="2026-09-11")
    record_spend(tenant, run_id="run-b", cost=0.25, today_iso="2026-09-11")
    record_spend(tenant, run_id="run-c", cost=9.99, today_iso="2026-09-10")
    assert daily_spend(tenant, "2026-09-11") == 0.5
    assert daily_spend(tenant, "2026-09-10") == 9.99
    assert daily_spend(tenant, "2026-09-12") == 0.0
    # malformed lines are skipped, not fatal
    with spend_ledger_path(tenant).open("a") as f:
        f.write("not json\n")
    assert daily_spend(tenant, "2026-09-11") == 0.5


# ---------------------------------------------------------------------------
# K2 fix (Cycle 28, docs/REVIEW-KIMI-LONG-RUN.md): reserve at run start
# (before any model call), reconcile at run end, fail closed on a ledger
# write failure, and a reconcile job for a crash between the two.
# ---------------------------------------------------------------------------

def test_reserve_spend_raises_when_committed_plus_reservation_exceeds_the_cap(tmp_path):
    tenant = _TenantDouble(claims_config={"budget": {"daily_usd": 0.5}}, runs_dir=tmp_path, name="acme")
    record_spend(tenant, run_id="run-a", cost=0.5, today_iso="2026-09-11")
    with pytest.raises(BudgetExceeded) as exc_info:
        reserve_spend(tenant, run_id="run-b", today_iso="2026-09-11")
    assert exc_info.value.kind == "daily_usd"
    assert "acme" in str(exc_info.value)
    assert "0.50" in str(exc_info.value)


def test_reserve_spend_passes_under_the_cap_and_writes_a_reservation(tmp_path):
    tenant = _TenantDouble(claims_config={"budget": {"daily_usd": 5, "per_run_usd": 0.5}}, runs_dir=tmp_path)
    record_spend(tenant, run_id="run-a", cost=0.5, today_iso="2026-09-11")
    reserve_spend(tenant, run_id="run-b", today_iso="2026-09-11")
    # run-a is finalized (counts toward daily_spend); run-b is only reserved
    # (counts toward daily_reserved) until its own record_spend lands.
    assert daily_spend(tenant, "2026-09-11") == 0.5
    assert daily_reserved(tenant, "2026-09-11") == 0.5


def test_record_spend_reconciles_the_reservation(tmp_path):
    """Once record_spend writes run-b's final cost, its reservation must stop
    counting -- otherwise a tenant's own spend would double-count forever."""
    tenant = _TenantDouble(claims_config={"budget": {"daily_usd": 5, "per_run_usd": 0.5}}, runs_dir=tmp_path)
    reserve_spend(tenant, run_id="run-b", today_iso="2026-09-11")
    assert daily_reserved(tenant, "2026-09-11") == 0.5
    record_spend(tenant, run_id="run-b", cost=0.31, today_iso="2026-09-11")
    assert daily_reserved(tenant, "2026-09-11") == 0.0
    assert daily_spend(tenant, "2026-09-11") == 0.31


def test_reservation_estimate_prefers_per_run_config_then_recent_median_then_default(tmp_path):
    from harness.budget import reservation_estimate_usd

    # No config, no history -> the fallback.
    bare = _TenantDouble(runs_dir=tmp_path)
    assert reservation_estimate_usd(bare) == 0.60

    # History, no explicit per-run cap -> median of recent finalized costs.
    with_history = _TenantDouble(runs_dir=tmp_path / "with-history")
    for i, cost in enumerate([0.20, 0.40, 0.30]):
        record_spend(with_history, run_id=f"run-{i}", cost=cost, today_iso="2026-09-11")
    assert reservation_estimate_usd(with_history) == 0.30

    # An explicit per_run_usd always wins over history.
    configured = _TenantDouble(claims_config={"budget": {"per_run_usd": 1.25}}, runs_dir=tmp_path / "with-history")
    assert reservation_estimate_usd(configured) == 1.25


def test_concurrent_reserve_spend_only_one_passes_when_the_cap_allows_one(tmp_path):
    """K2's actual bug: two runs starting at the same instant must not both
    read $0 committed and both proceed. Real threads, real file locking --
    not a mocked race."""
    tenant = _TenantDouble(claims_config={"budget": {"daily_usd": 0.5, "per_run_usd": 0.5}}, runs_dir=tmp_path)
    results = {}
    barrier = threading.Barrier(2)

    def attempt(run_id):
        barrier.wait()
        try:
            reserve_spend(tenant, run_id=run_id, today_iso="2026-09-11")
            results[run_id] = "ok"
        except BudgetExceeded:
            results[run_id] = "refused"

    threads = [threading.Thread(target=attempt, args=(f"run-{i}",)) for i in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert sorted(results.values()) == ["ok", "refused"]
    # Exactly one reservation made it into the ledger.
    assert daily_reserved(tenant, "2026-09-11") == 0.5


def test_reserve_spend_with_an_unwritable_ledger_directory_refuses_cleanly(tmp_path):
    runs_dir = tmp_path / "runs"
    runs_dir.mkdir()
    tenant = _TenantDouble(claims_config={"budget": {"daily_usd": 5}}, runs_dir=runs_dir)
    runs_dir.chmod(0o500)  # read + execute, no write
    try:
        with pytest.raises(LedgerWriteError):
            reserve_spend(tenant, run_id="run-a", today_iso="2026-09-11")
    finally:
        runs_dir.chmod(0o700)  # let tmp_path clean the directory up


def test_reconcile_drops_stale_reservations_whose_run_finished(tmp_path):
    tenant = _TenantDouble(runs_dir=tmp_path)
    stale_ts = time.time() - 3 * 60 * 60  # 3h ago: past the 2h staleness window
    with spend_ledger_path(tenant).open("a") as f:
        # Reserved, then crashed (or record_spend's own write failed) after
        # the run actually finished -- its log carries the final line.
        f.write(json.dumps({"date": "2026-09-11", "run_id": "crashed-run", "reserved_usd": 0.6, "ts": stale_ts}) + "\n")
        # Reserved just as long ago, but never reached a final state --
        # might still be running. Must be left alone.
        f.write(json.dumps({"date": "2026-09-11", "run_id": "still-running", "reserved_usd": 0.6, "ts": stale_ts}) + "\n")
        # Reserved recently -- too fresh to touch even though it has no
        # final state either.
        f.write(json.dumps({"date": "2026-09-11", "run_id": "fresh-run", "reserved_usd": 0.6, "ts": time.time()}) + "\n")
    (tmp_path / "crashed-run.log").write_text("run_id: crashed-run\nrun_result: PASS attempts=1 repairs=0\n")

    dropped = reconcile_stale_reservations(tenant)

    assert [e["run_id"] for e in dropped] == ["crashed-run"]
    assert round(daily_reserved(tenant, "2026-09-11"), 2) == 1.2  # still-running + fresh-run


def test_run_exits_3_once_the_daily_cap_is_spent(monkeypatch, tmp_path):
    """Pipeline level: a tenant at its daily cap gets exit 3 before any model
    call -- the fake client must never be invoked."""
    import argparse

    from harness import cli
    from harness import budget as budget_mod
    from tests.conftest import FakeClient
    from tests.support import TENANT

    ledger = tmp_path / "spend-ledger.jsonl"
    ledger.write_text('{"date": "2026-09-11", "run_id": "old", "cost_estimate": 9.0}\n')
    monkeypatch.setattr(budget_mod, "spend_ledger_path", lambda tenant: ledger)
    # pipeline.py imported the module object, so patching the module attribute
    # is enough -- but the tenant's config must name a cap under that amount.
    config = dict(TENANT.claims_config, budget={"daily_usd": 5})
    monkeypatch.setattr(type(TENANT), "claims_config", property(lambda self: config))

    import datetime
    class _Today(datetime.date):
        @classmethod
        def today(cls):
            return cls(2026, 9, 11)
    monkeypatch.setattr(datetime, "date", _Today)

    client = FakeClient([])
    monkeypatch.setattr(cli, "make_client", lambda: client)
    args = argparse.Namespace(
        input="tenants/peak-saunas/fixtures/founder-warranty-demo.txt",
        cartridges="article", seed=42, product=None,
        ffmpeg_bin="/usr/bin/ffmpeg", whisper_bin="/nonexistent/whisper-cli",
        whisper_model="/nonexistent/model.bin", tenant=None, batch=False,
    )
    assert cli.cmd_run(args) == 3
    assert client.messages.calls == []
