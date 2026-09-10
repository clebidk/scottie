"""One base class for every failure an operator is meant to see.

Before this module, `harness/cli.py`'s `main` caught exactly two exceptions and
everything else reached the operator as a traceback: an unknown `--product`, an
unknown workflow name, an unknown stage, a writer that never returned valid
JSON, a batch that timed out, a Shopify API error, and any malformed
`tenant.yaml` / `verified.json`.

Anything raised from this hierarchy is an *expected* failure with an operator
action behind it: `main` prints its message on one line and returns its
`exit_code`. A traceback from anything else is still a traceback on purpose --
that is a bug in this harness, and hiding it behind a friendly line would only
make it harder to report.

Several classes below also inherit a stdlib exception (`ValueError`,
`FileNotFoundError`, `RuntimeError`) so that existing `except` clauses and
tests written against the old behaviour keep matching.
"""
from . import exits


class HarnessError(Exception):
    """Base for every failure `harness` reports as a message, not a traceback."""

    exit_code = exits.USAGE


class UnknownWorkflow(HarnessError, FileNotFoundError):
    """`harness workflow run <name>` where no workflows/<name>.yaml exists."""


class UnknownProduct(HarnessError, ValueError):
    """`--product` names nothing in the tenant's claims/products.json."""


class WriterFailed(HarnessError, ValueError):
    """The writer never returned JSON matching the cartridge's schema, even
    after its own retry."""


class PublishFailed(HarnessError, RuntimeError):
    """A publisher adapter's API call failed (bad status, unexpected body)."""


class TenantFileInvalid(HarnessError):
    """A tenant file exists but does not parse, or fails validation."""

    exit_code = exits.TENANT_NOT_CONFIGURED
