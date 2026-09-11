"""Per-run budget tracker: wall clock, tokens, and Claude call count.

Never returns a partial page as success -- callers must let BudgetExceeded
propagate out of the run so the CLI exits loudly (exit code 3) instead of
writing incomplete output.

Daily spend cap (Kimi long-run phase 3; landscape borrowing #9): on top of
the per-run caps, a tenant may set a per-day dollar ceiling --
`"budget": {"daily_usd": N}` in claims/config.json (wins) or tenant.yaml's
`budget:` block. Unset means uncapped, exactly as before.

K2 fix (Cycle 28, docs/REVIEW-KIMI-LONG-RUN.md): the original design checked
the cap once, before the run, and only recorded spend after -- two runs
started in the same window both read $0 and both proceeded, and a ledger
write failure was logged and silently swallowed, making the cap infinite.
Both are fixed the same way real spend limits are: reserve first.

    reserve_spend  -- called once, at run start, before any model call.
                      Appends a `{"run_id", "reserved_usd", "ts"}` line under
                      an exclusive lock on the ledger and refuses to start
                      (BudgetExceeded, exit 3) when today's committed total
                      (finalized runs' actual cost, plus every reservation
                      whose run has not finalized yet) plus this run's
                      reservation would exceed the cap. A ledger write
                      failure raises too (LedgerWriteError, also exit 3) --
                      never silently lets the run through.
    record_spend   -- called once, at run end (success, STOP, or budget
                      abort). Appends the actual `{"run_id", "cost_estimate"}`
                      line, which reconciles the reservation (daily_spend /
                      daily_reserved below stop counting a run's reservation
                      once its final line exists). A write failure here is
                      still logged and swallowed -- the run already
                      happened; crashing over bookkeeping now would be
                      worse than a missing line -- but the reservation it
                      should have reconciled stays live until either this
                      writes successfully on a later call or the run shows
                      up as finished in `harness spend reconcile`.
    reconcile_stale_reservations -- `harness spend reconcile`. Drops
                      reservations older than 2 hours whose run directory's
                      log shows the run actually finished (crashed between
                      reserving and recording, or the final write itself
                      failed) -- a reservation for a run with no final state
                      on disk is left alone; it may still be running.
"""
import fcntl
import json
import statistics
import time
from contextlib import contextmanager
from pathlib import Path

from . import exits

# Reservations older than this are eligible for `harness spend reconcile` to
# drop, provided the run they belong to has already reached a final state.
RECONCILE_STALE_AFTER_S = 2 * 60 * 60

# Fallback per-run reservation when the tenant has no per_run_usd budget
# config and no run history to take a median from -- roughly double the
# $0.3125 a real hidden-costs-v2 run cost in the K2 review, as a cold-start
# safety margin.
DEFAULT_RESERVATION_USD = 0.60


class BudgetExceeded(Exception):
    exit_code = exits.BUDGET

    def __init__(self, message, kind):
        super().__init__(message)
        self.kind = kind


class LedgerWriteError(BudgetExceeded):
    """K2(c): the spend ledger itself could not be written (runs/ read-only,
    full disk, etc). A subclass of BudgetExceeded so it flows through every
    existing `except BudgetExceeded` abort path (pipeline.execute,
    cli.cmd_ingest) unchanged -- it must stop the run before any model call,
    the same as the cap itself being reached, not be swallowed into an
    effectively uncapped run the way the pre-K2 design did."""

    def __init__(self, message):
        super().__init__(message, "ledger_write")


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


def _lock_path(tenant):
    return spend_ledger_path(tenant).with_name(spend_ledger_path(tenant).name + ".lock")


@contextmanager
def _locked_ledger(tenant):
    """Exclusive lock on a file beside the ledger, held for an entire
    read-decide-append critical section -- K2: without this, two runs
    starting at once both read today's total before either one appends, and
    both proceed."""
    lock_path = _lock_path(tenant)
    try:
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        lockfile = open(lock_path, "a")
    except OSError as e:
        # K2(c): an unwritable runs/ dir must refuse the run cleanly (a typed
        # BudgetExceeded subclass), not surface as a raw, uncaught OSError.
        raise LedgerWriteError(f"spend ledger lock unavailable ({lock_path}): {e}") from e
    try:
        fcntl.flock(lockfile.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(lockfile.fileno(), fcntl.LOCK_UN)
    finally:
        lockfile.close()


def _read_ledger_entries(tenant):
    path = spend_ledger_path(tenant)
    if not path.exists():
        return []
    entries = []
    for line in path.read_text().splitlines():
        if not line.strip():
            continue
        try:
            entries.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return entries


def _append_ledger_line(tenant, entry):
    """Append one JSON line to the ledger. Raises LedgerWriteError on any
    write failure -- callers that must fail closed (reserve_spend) let it
    propagate; record_spend catches it itself and swallows (see module
    docstring)."""
    path = spend_ledger_path(tenant)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a") as f:
            f.write(json.dumps(entry) + "\n")
    except (OSError, TypeError, ValueError) as e:
        raise LedgerWriteError(f"spend ledger write failed ({path}): {e}") from e


def _committed_and_reserved(entries):
    """(finalized total, open-reservation total) for one date's ledger
    entries. A reservation counts only when its run_id has no matching
    `cost_estimate` line yet -- once record_spend writes the final line, the
    reservation is reconciled and drops out of the total on its own."""
    finalized_run_ids = {e["run_id"] for e in entries if "cost_estimate" in e}
    finalized_total = sum(float(e.get("cost_estimate") or 0) for e in entries if "cost_estimate" in e)
    reserved_total = sum(
        float(e.get("reserved_usd") or 0)
        for e in entries
        if "reserved_usd" in e and e.get("run_id") not in finalized_run_ids
    )
    return finalized_total, reserved_total


def daily_spend(tenant, today_iso):
    """Today's recorded FINAL (finalized-run) spend from the ledger --
    excludes open reservations."""
    entries = [e for e in _read_ledger_entries(tenant) if e.get("date") == today_iso]
    finalized_total, _ = _committed_and_reserved(entries)
    return finalized_total


def daily_reserved(tenant, today_iso):
    """Today's sum of reservations whose run has not finalized (recorded its
    actual cost) yet."""
    entries = [e for e in _read_ledger_entries(tenant) if e.get("date") == today_iso]
    _, reserved_total = _committed_and_reserved(entries)
    return reserved_total


def _recent_final_costs(tenant, limit=10):
    """The last `limit` finalized runs' recorded costs, oldest first, across
    every date in the ledger (not just today) -- used to estimate a fresh
    reservation."""
    costs = [float(e["cost_estimate"]) for e in _read_ledger_entries(tenant) if "cost_estimate" in e]
    return costs[-limit:]


def reservation_estimate_usd(tenant):
    """Dollar amount reserved for one run before it starts: the tenant's own
    configured per-run cap if set (`budget.per_run_usd`, same claims/
    config.json-wins-over-tenant.yaml precedence as daily_usd), else the
    median of the last 10 finalized runs' recorded cost, else
    DEFAULT_RESERVATION_USD for a tenant with no history yet."""
    budget_cfg = tenant.claims_config.get("budget") or tenant.get("budget") or {}
    per_run = budget_cfg.get("per_run_usd")
    if per_run is not None:
        return float(per_run)
    recent = _recent_final_costs(tenant)
    if recent:
        return float(statistics.median(recent))
    return DEFAULT_RESERVATION_USD


def reserve_spend(tenant, *, run_id, today_iso, log=None):
    """K2(a): reserve this run's estimated cost against the tenant's daily
    cap BEFORE any model call. Called once, from pipeline.prepare_run, in
    place of the old check-then-record-later check_daily_cap.

    Raises BudgetExceeded (kind="daily_usd") when today's committed total --
    finalized runs' actual cost, plus every not-yet-finalized reservation --
    plus this run's own reservation would exceed the cap. Raises
    LedgerWriteError when the ledger itself can't be written. Either way the
    run stops here, before ingest/ground/write ever spend a token (K2(c))."""
    cap = daily_cap_usd(tenant)
    if cap is None:
        return
    reservation = reservation_estimate_usd(tenant)
    with _locked_ledger(tenant):
        entries = [e for e in _read_ledger_entries(tenant) if e.get("date") == today_iso]
        finalized_total, reserved_total = _committed_and_reserved(entries)
        committed = finalized_total + reserved_total
        if committed + reservation > cap:
            message = f"daily spend cap reached: ${committed:.2f} of ${cap:.2f} (tenant {tenant.name})"
            if log:
                log.event("run", message)
            raise BudgetExceeded(message, "daily_usd")
        _append_ledger_line(tenant, {
            "date": today_iso, "run_id": run_id,
            "reserved_usd": round(reservation, 4), "ts": time.time(),
        })


def record_spend(tenant, *, run_id, cost, today_iso, log=None):
    """K2(b): append this run's actual estimated cost, reconciling its
    reserve_spend reservation (daily_spend/daily_reserved stop counting the
    reservation once this line exists). Called once per run, at the end --
    success, claims-gate STOP, or budget abort.

    A write failure here is logged and swallowed, not raised: the run
    already happened, so crashing over bookkeeping now would be worse than
    a missing line (same argument the original design made, and the K2
    review agreed with -- see the module docstring). The reservation this
    should have reconciled simply stays live until `harness spend reconcile`
    drops it once the run shows a final state and the reservation is stale."""
    try:
        with _locked_ledger(tenant):
            _append_ledger_line(tenant, {
                "date": today_iso, "run_id": run_id, "cost_estimate": round(float(cost), 4),
            })
    except (LedgerWriteError, TypeError, ValueError) as e:
        if log:
            log.event("run", f"spend ledger write failed (cap bookkeeping only): {e}")


def _run_has_final_state(tenant, run_id):
    """True once runs/<run_id>.log carries its `run_result:` line --
    review_md.log_run_result writes exactly one, on every path out of
    pipeline.execute (PASS, claims-gate STOP, or budget abort). A run with
    no such line yet may still be in flight or may have crashed outright;
    either way its reservation is left alone."""
    log_path = Path(tenant.runs_dir) / f"{run_id}.log"
    if not log_path.exists():
        return False
    return "run_result:" in log_path.read_text(errors="ignore")


def reconcile_stale_reservations(tenant, *, now=None, max_age_s=RECONCILE_STALE_AFTER_S):
    """`harness spend reconcile`: drops reservation lines older than
    `max_age_s` whose run has a final state on disk but never got a matching
    `cost_estimate` line -- a crash, or record_spend's own write failing,
    between reserving and finishing. Returns the list of dropped entries.

    Runs under the same lock as reserve_spend/record_spend so a concurrent
    reservation or finalization can't race the rewrite."""
    now = time.time() if now is None else now
    with _locked_ledger(tenant):
        entries = _read_ledger_entries(tenant)
        finalized_run_ids = {e["run_id"] for e in entries if "cost_estimate" in e}
        kept, dropped = [], []
        for entry in entries:
            is_open_reservation = "reserved_usd" in entry and entry.get("run_id") not in finalized_run_ids
            if (is_open_reservation
                    and now - float(entry.get("ts") or 0) > max_age_s
                    and _run_has_final_state(tenant, entry.get("run_id"))):
                dropped.append(entry)
            else:
                kept.append(entry)
        if dropped:
            path = spend_ledger_path(tenant)
            try:
                path.write_text("".join(json.dumps(e) + "\n" for e in kept))
            except OSError as e:
                raise LedgerWriteError(f"spend ledger reconcile write failed ({path}): {e}") from e
        return dropped
