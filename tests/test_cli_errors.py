"""Cycle 22 findings R16/R17/R18: the CLI's error surface.

`harness/cli.py`'s `main` and its argparse wiring had no tests at all, which is
how a usage error came to exit 2 -- the code this harness reserves for a
claims-gate STOP -- without anyone noticing.
"""
import pytest

from harness import cli, exits
from harness.errors import (
    HarnessError,
    PublishFailed,
    UnknownProduct,
    UnknownWorkflow,
    WriterFailed,
)
from harness.tenant import TenantNotConfigured, UnknownTenant


# ---------------------------------------------------------------------------
# Usage errors are exit 1, never exit 2
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "argv",
    [
        [],                                  # no subcommand
        ["bogus"],                           # unknown subcommand
        ["run"],                             # missing positional
        ["approve", "some/run"],             # missing required --by
        ["packet", "some/run", "--stamp", "nope", "--by", "x"],  # invalid choice
    ],
)
def test_a_usage_error_exits_1_not_2(argv):
    """Exit 2 means "a claims gate stopped this run". A typo must never be
    reported with the same code, or a caller cannot tell them apart."""
    with pytest.raises(SystemExit) as exc_info:
        cli.main(argv)
    assert exc_info.value.code == exits.USAGE
    assert exc_info.value.code != exits.GATE_STOP


def test_help_lists_the_exit_codes():
    """The exit-code table was documented in three prose files and defined in
    none; --help is where someone actually looks."""
    text = cli.build_parser().format_help()
    for line in ("0  done", "2  claims-gate STOP", "4  tenant not configured yet"):
        assert line in text


# ---------------------------------------------------------------------------
# One handler turns an expected failure into a message and a code
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "error, expected_code",
    [
        (UnknownWorkflow("no workflow 'nope'"), exits.USAGE),
        (UnknownProduct("unknown --product: 'nope'"), exits.USAGE),
        (WriterFailed("write.article failed after retry"), exits.USAGE),
        (PublishFailed("page create failed: status=422"), exits.USAGE),
        (TenantNotConfigured("tenant not configured: missing claims/verified.json"),
         exits.TENANT_NOT_CONFIGURED),
        (UnknownTenant("unknown tenant 'nope'"), exits.TENANT_NOT_CONFIGURED),
    ],
)
def test_main_reports_an_expected_failure_as_one_line(error, expected_code, monkeypatch, capsys):
    def boom(_args):
        raise error

    parser = cli.build_parser()
    args = parser.parse_args("tenant list".split())
    args.func = boom
    monkeypatch.setattr(cli, "build_parser", lambda: parser)
    monkeypatch.setattr(parser, "parse_args", lambda argv=None: args)

    assert cli.main([]) == expected_code
    captured = capsys.readouterr()
    assert str(error) in captured.err
    assert "Traceback" not in captured.err


def test_an_unexpected_exception_is_not_swallowed(monkeypatch):
    """A bug in this harness must still raise. Turning every exception into a
    tidy one-liner would hide the ones worth reporting."""
    parser = cli.build_parser()
    args = parser.parse_args("tenant list".split())

    def boom(_args):
        raise ZeroDivisionError("a real bug")

    args.func = boom
    monkeypatch.setattr(cli, "build_parser", lambda: parser)
    monkeypatch.setattr(parser, "parse_args", lambda argv=None: args)

    with pytest.raises(ZeroDivisionError):
        cli.main([])


# ---------------------------------------------------------------------------
# The hierarchy itself
# ---------------------------------------------------------------------------

def test_every_expected_failure_carries_an_exit_code():
    for cls in (UnknownWorkflow, UnknownProduct, WriterFailed, PublishFailed,
                TenantNotConfigured, UnknownTenant):
        assert issubclass(cls, HarnessError)
        assert cls.exit_code in (exits.USAGE, exits.TENANT_NOT_CONFIGURED)


def test_the_stdlib_aliases_still_match():
    """Callers and tests written against the previous exception types keep
    working: an unknown workflow is still a FileNotFoundError, an unknown
    --product still a ValueError, a failed publish still a RuntimeError."""
    assert issubclass(UnknownWorkflow, FileNotFoundError)
    assert issubclass(UnknownProduct, ValueError)
    assert issubclass(WriterFailed, ValueError)
    assert issubclass(PublishFailed, RuntimeError)
