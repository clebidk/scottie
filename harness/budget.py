"""Per-run budget tracker: wall clock, tokens, and Claude call count.

Never returns a partial page as success -- callers must let BudgetExceeded
propagate out of the run so the CLI exits loudly (exit code 3) instead of
writing incomplete output.

Daily spend cap (Kimi long-run phase 3; landscape borrowing #9): on top of
the per-run caps, a tenant may set a per-day dollar ceiling --
`"budget": {"daily_usd": N}` in claims/config.json (wins) or tenant.yaml's
`budget:` block. Unset means uncapped, exactly as before. Every run appends
its estimated cost to runs/spend-ledger.jsonl; prepare_run refuses to start
a new run once today's recorded spend has reached the cap. The cap gates the
START of the next run -- the per-run Budget above is still what bounds a run
already in flight.
"""
import json
import time
from pathlib import Path

from . import exits


class BudgetExceeded(Exception):
    exit_code = exits.BUDGET

    def __init__(self, message, kind):
        super().__init__(message)
        self.kind = kind


class Budget:
    # Fix cycle 12 item 1: exemplar trimming (write.load_exemplars -- at most
    # the first 700 words of each of at most 2 exemplars) cuts a typical
    # writer call's prompt from ~12,000-12,700 tokens (measured, see below)
    # down from the pre-fix ~36,800-token average, so three cartridges with
    # real repairs now fit comfortably inside a bigger token/call budget
    # without needing the wall clock raised past 300s -- measured directly
    # on the server during Cycle 12 verification (docs/FIXLOG.md Cycle 12):
    # a real `adv run fixtures/price-comparison-v2.mov` (the fixture Cycles
    # 10-11 flagged as the slowest/most repair-prone) hit 2 real repairs
    # across its 3 cartridges (article + product-page, 1 each) and finished
    # in 173.3s elapsed -- 42% of the 300s cap, with 117,672/220,000 tokens
    # (53%) and 7/14 calls used. No real run this cycle came close to
    # needing 420s; 300s stays as-is. tokens/calls raised to 220,000/14
    # (from 150,000/12) so a numeric-heavy fixture has real headroom for a
    # second repair on every cartridge instead of hitting the budget-aware
    # repair skip (fix cycle 11 problem C) as often.
    def __init__(self, wall_s=300, tokens=220_000, calls=14):
        self.wall_s = wall_s
        self.token_limit = tokens
        self.call_limit = calls
        self._start = time.monotonic()
        self.tokens_used = 0
        self.calls_used = 0

    def check(self):
        """Raise BudgetExceeded if any cap has been passed. Call before each stage
        and after every model call."""
        elapsed = time.monotonic() - self._start
        if elapsed > self.wall_s:
            raise BudgetExceeded(
                f"wall clock budget exceeded: {elapsed:.1f}s > {self.wall_s}s", "wall_s"
            )
        if self.tokens_used > self.token_limit:
            raise BudgetExceeded(
                f"token budget exceeded: {self.tokens_used} > {self.token_limit}", "tokens"
            )
        if self.calls_used > self.call_limit:
            raise BudgetExceeded(
                f"call budget exceeded: {self.calls_used} > {self.call_limit}", "calls"
            )

    def record_call(self, input_tokens, output_tokens):
        self.calls_used += 1
        self.tokens_used += input_tokens + output_tokens
        self.check()

    def summary(self):
        return {
            "elapsed_s": round(time.monotonic() - self._start, 2),
            "wall_s_limit": self.wall_s,
            "tokens_used": self.tokens_used,
            "tokens_limit": self.token_limit,
            "calls_used": self.calls_used,
            "calls_limit": self.call_limit,
        }


def daily_cap_usd(tenant):
    """The tenant's daily spend ceiling in dollars, or None (uncapped).
    claims/config.json's "budget" object wins over tenant.yaml's, the same
    precedence every overlapping config key already follows."""
    budget_cfg = tenant.claims_config.get("budget") or tenant.get("budget") or {}
    cap = budget_cfg.get("daily_usd")
    return float(cap) if cap is not None else None


def spend_ledger_path(tenant):
    return Path(tenant.runs_dir) / "spend-ledger.jsonl"


def record_spend(tenant, *, run_id, cost, today_iso, log=None):
    """Append one run's estimated cost to the tenant's daily ledger. A ledger
    write failure is logged and swallowed -- the run already finished; crashing
    it over bookkeeping would be worse than a missing line."""
    try:
        path = spend_ledger_path(tenant)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a") as f:
            f.write(json.dumps({"date": today_iso, "run_id": run_id, "cost_estimate": round(float(cost), 4)}) + "\n")
    except (OSError, TypeError, ValueError) as e:
        if log:
            log.event("run", f"spend ledger write failed (cap bookkeeping only): {e}")


def daily_spend(tenant, today_iso):
    """Today's recorded estimated spend from the ledger."""
    path = spend_ledger_path(tenant)
    total = 0.0
    if path.exists():
        for line in path.read_text().splitlines():
            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                continue
            if entry.get("date") == today_iso:
                total += float(entry.get("cost_estimate") or 0)
    return total


def check_daily_cap(tenant, *, today_iso, log=None):
    """Raise BudgetExceeded when today's recorded spend has reached the
    tenant's daily cap. Called once per run, before any stage does work."""
    cap = daily_cap_usd(tenant)
    if cap is None:
        return
    spent = daily_spend(tenant, today_iso)
    if spent >= cap:
        if log:
            log.event("run", f"daily spend cap reached: ${spent:.4f} of ${cap:.2f} already spent today")
        raise BudgetExceeded(
            f"daily spend cap reached: ${spent:.4f} already spent today against a ${cap:.2f} daily cap",
            "daily_usd",
        )
