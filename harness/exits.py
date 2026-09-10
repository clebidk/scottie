"""Process exit codes, in one place.

Every command in this harness returns one of these. They were previously
documented in three prose locations (README.md, docs/ARCHITECTURE.md, and a
comment in cli.py) and defined in none, with bare `return 1` / `return 2`
literals scattered through cli.py and pipeline.py.

    0  the command did what it was asked
    1  bad usage, or an operator-level refusal (unknown tenant flag combination,
       a run directory that is not one, a publish refused by the approval gate)
    2  a claims-gate STOP -- see unmatched_claims.json in the run directory
    3  a budget cap was hit
    4  the tenant exists but has not been configured yet

Exit 2 is reserved for a claims-gate STOP. argparse's own default for a usage
error is also 2, which made a typo indistinguishable from a content failure,
so `harness/cli.py`'s parser overrides `error()` to exit USAGE instead.
"""

OK = 0
USAGE = 1
GATE_STOP = 2
BUDGET = 3
TENANT_NOT_CONFIGURED = 4

# Rendered into `harness --help` so the table lives next to the commands it
# describes rather than only in the README.
HELP_EPILOG = """exit codes:
  0  done
  1  bad usage, or a refused operation
  2  claims-gate STOP (see unmatched_claims.json in the run directory)
  3  budget cap exceeded
  4  tenant not configured yet
"""
