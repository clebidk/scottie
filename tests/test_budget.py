import time

import pytest

from harness.budget import (
    Budget,
    BudgetExceeded,
    check_daily_cap,
    daily_cap_usd,
    daily_spend,
    record_spend,
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
    get(), and runs_dir for the ledger."""

    def __init__(self, *, claims_config=None, yaml_budget=None, runs_dir=None):
        self._claims_config = claims_config or {}
        self._yaml_budget = yaml_budget
        self.runs_dir = runs_dir

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
    check_daily_cap(tenant, today_iso="2026-09-11")  # never raises


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


def test_check_daily_cap_raises_at_the_cap(tmp_path):
    tenant = _TenantDouble(claims_config={"budget": {"daily_usd": 0.5}}, runs_dir=tmp_path)
    record_spend(tenant, run_id="run-a", cost=0.5, today_iso="2026-09-11")
    with pytest.raises(BudgetExceeded) as exc_info:
        check_daily_cap(tenant, today_iso="2026-09-11")
    assert exc_info.value.kind == "daily_usd"


def test_check_daily_cap_passes_under_the_cap(tmp_path):
    tenant = _TenantDouble(claims_config={"budget": {"daily_usd": 5}}, runs_dir=tmp_path)
    record_spend(tenant, run_id="run-a", cost=0.5, today_iso="2026-09-11")
    check_daily_cap(tenant, today_iso="2026-09-11")


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
