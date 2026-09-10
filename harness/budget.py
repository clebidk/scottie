"""Per-run budget tracker: wall clock, tokens, and Claude call count.

Never returns a partial page as success -- callers must let BudgetExceeded
propagate out of the run so the CLI exits loudly (exit code 3) instead of
writing incomplete output.
"""
import time

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
