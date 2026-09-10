import time

import pytest

from adv.budget import Budget, BudgetExceeded


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
