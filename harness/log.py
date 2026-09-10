"""Per-run log at runs/<run-id>.log: timestamps, stage, model id, token usage per
call, seed, cartridges chosen, gate result, budget totals, an estimated cost line.
"""
import time
from pathlib import Path

from . import pricing


class RunLog:
    def __init__(self, run_id, path):
        self.run_id = run_id
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._fh = open(self.path, "a")
        self.total_input_tokens = 0
        self.total_output_tokens = 0
        self.total_cache_creation_input_tokens = 0
        self.total_cache_read_input_tokens = 0
        # Fix cycle 17: one call needs its own model (and cache/batch token
        # breakdown) to price correctly -- a run can mix claude-sonnet-5 and
        # claude-haiku-4-5 calls, and a blended single rate can't tell them
        # apart. cost_estimate() below sums pricing.calculate_cost() per call.
        self._calls = []
        self._write(f"run_id: {run_id}")

    def _write(self, line):
        ts = time.strftime("%Y-%m-%dT%H:%M:%S")
        self._fh.write(f"[{ts}] {line}\n")
        self._fh.flush()

    def event(self, stage, message):
        self._write(f"{stage}: {message}")

    def call(self, stage, model, input_tokens, output_tokens, *,
             cache_creation_input_tokens=0, cache_read_input_tokens=0, batch=False):
        """Records one Claude call's usage. cache_creation_input_tokens and
        cache_read_input_tokens are the Anthropic SDK Usage object's own
        fields (cache write / cache read token counts -- zero for a call with
        no cache_control breakpoint). batch=True marks a call made through
        the Message Batches API (harness/cli.py's --batch flag), priced at
        pricing.BATCH_MULTIPLIER."""
        self.total_input_tokens += input_tokens
        self.total_output_tokens += output_tokens
        self.total_cache_creation_input_tokens += cache_creation_input_tokens
        self.total_cache_read_input_tokens += cache_read_input_tokens
        self._calls.append({
            "model": model,
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "cache_creation_input_tokens": cache_creation_input_tokens,
            "cache_read_input_tokens": cache_read_input_tokens,
            "batch": batch,
        })
        self._write(
            f"{stage}: model={model} input_tokens={input_tokens} output_tokens={output_tokens} "
            f"cache_creation_input_tokens={cache_creation_input_tokens} "
            f"cache_read_input_tokens={cache_read_input_tokens}"
            + (" batch=true" if batch else "")
        )

    def seed(self, seed):
        self._write(f"seed: {seed}")

    def cartridges(self, names):
        self._write(f"cartridges: {','.join(names)}")

    def gate_result(self, result, detail=""):
        self._write(f"gate_result: {result} {detail}".rstrip())

    def result(self, result, attempts, repairs):
        """Fix cycle 4 item 5: one final line per run, e.g.
        "run_result: PASS attempts=4 repairs=1"."""
        self._write(f"run_result: {result} attempts={attempts} repairs={repairs}")

    def budget_summary(self, summary):
        self._write(f"budget: {summary}")

    def cost_estimate(self):
        """Sum of pricing.calculate_cost() over every recorded call, each
        priced at its own model's real rate (fix cycle 17: replaces the old
        single hard-coded $3/$15-per-million blended estimate, which mispriced
        every call this harness actually makes)."""
        cost = sum(
            pricing.calculate_cost(
                c["model"], c["input_tokens"], c["output_tokens"],
                cache_creation_input_tokens=c["cache_creation_input_tokens"],
                cache_read_input_tokens=c["cache_read_input_tokens"],
                batch=c["batch"],
            )
            for c in self._calls
        )
        self._write(
            "estimated_cost_usd (estimate, real per-model pricing incl. cache/batch): "
            f"{cost:.4f} total_input_tokens={self.total_input_tokens} "
            f"total_output_tokens={self.total_output_tokens} "
            f"total_cache_creation_input_tokens={self.total_cache_creation_input_tokens} "
            f"total_cache_read_input_tokens={self.total_cache_read_input_tokens}"
        )
        return cost

    def close(self):
        self._fh.close()
