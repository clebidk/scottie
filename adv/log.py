"""Per-run log at runs/<run-id>.log: timestamps, stage, model id, token usage per
call, seed, cartridges chosen, gate result, budget totals, an estimated cost line.
"""
import time
from pathlib import Path

from .config import INPUT_COST_PER_M, OUTPUT_COST_PER_M


class RunLog:
    def __init__(self, run_id, path):
        self.run_id = run_id
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._fh = open(self.path, "a")
        self.total_input_tokens = 0
        self.total_output_tokens = 0
        self._write(f"run_id: {run_id}")

    def _write(self, line):
        ts = time.strftime("%Y-%m-%dT%H:%M:%S")
        self._fh.write(f"[{ts}] {line}\n")
        self._fh.flush()

    def event(self, stage, message):
        self._write(f"{stage}: {message}")

    def call(self, stage, model, input_tokens, output_tokens):
        self.total_input_tokens += input_tokens
        self.total_output_tokens += output_tokens
        self._write(
            f"{stage}: model={model} input_tokens={input_tokens} output_tokens={output_tokens}"
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
        cost = (self.total_input_tokens / 1_000_000) * INPUT_COST_PER_M + (
            self.total_output_tokens / 1_000_000
        ) * OUTPUT_COST_PER_M
        self._write(
            "estimated_cost_usd (estimate, "
            f"${INPUT_COST_PER_M}/M in + ${OUTPUT_COST_PER_M}/M out): {cost:.4f} "
            f"total_input_tokens={self.total_input_tokens} "
            f"total_output_tokens={self.total_output_tokens}"
        )
        return cost

    def close(self):
        self._fh.close()
