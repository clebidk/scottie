"""Cycle 22 findings R31/R32: the suite's own guarantees, tested.

Both of these used to be conventions stated in README.md. A convention that
nothing checks is a convention that quietly stops holding.
"""
import socket
import urllib.request

import pytest

from harness import tenant as tenant_mod
from harness import vocab


# ---------------------------------------------------------------------------
# R32: the suite runs offline, and that is enforced
# ---------------------------------------------------------------------------

def test_opening_a_socket_fails_the_test_that_does_it():
    with pytest.raises(AssertionError, match="offline"):
        socket.create_connection(("example.test", 80), timeout=1)


def test_a_plain_socket_connect_is_blocked_too():
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        with pytest.raises(AssertionError, match="offline"):
            s.connect(("example.test", 80))
    finally:
        s.close()


def test_an_http_fetch_cannot_reach_the_network():
    """The shape that matters: harness/render.py's http_fetch_bytes,
    harness/prices.py's feed fetch and the Judge.me scrape all go through
    urllib, and every one of them is supposed to be injected as a fake."""
    with pytest.raises(AssertionError, match="offline"):
        urllib.request.urlopen("http://example.test/", timeout=1)


def test_the_markers_are_declared():
    """--strict-markers is on, so an undeclared marker is an error rather than
    a silently ignored decorator."""
    import tomllib
    from pathlib import Path

    from harness.config import REPO_ROOT

    config = tomllib.loads((Path(REPO_ROOT) / "pyproject.toml").read_text())
    markers = config["tool"]["pytest"]["ini_options"]["markers"]
    names = [m.split(":")[0] for m in markers]
    assert "slow" in names
    assert "live" in names
    assert "--strict-markers" in config["tool"]["pytest"]["ini_options"]["addopts"]


# ---------------------------------------------------------------------------
# R31: the active tenant/vocabulary globals are restored around every test
# ---------------------------------------------------------------------------

# What the globals held before the dirtying test below. The property under
# test is "restored to whatever it was", not "None" -- the baseline depends on
# what ran earlier in the session, which is exactly the coupling being removed.
_BEFORE = {}


def test_activating_a_tenant_here_does_not_leak_into_the_next_test():
    """This test deliberately dirties both globals. The autouse fixture in
    conftest.py puts them back; the test below is what proves it, since pytest
    runs tests in file order."""
    _BEFORE["globals"] = (tenant_mod.active_or_none(), vocab.active_or_none())
    tenant_mod.activate(tenant_mod.load_tenant("peak-saunas"))
    assert tenant_mod.active_or_none() is not None
    assert vocab.active_or_none() is not None


def test_the_globals_were_restored():
    assert "globals" in _BEFORE, "the dirtying test above must run first"
    assert (tenant_mod.active_or_none(), vocab.active_or_none()) == _BEFORE["globals"]


def test_set_active_round_trips():
    previous = tenant_mod.active_or_none()
    tenant = tenant_mod.load_tenant("peak-saunas")
    tenant_mod.set_active(tenant)
    assert tenant_mod.active_or_none() is tenant
    tenant_mod.set_active(previous)
    assert tenant_mod.active_or_none() is previous


def test_active_or_none_does_not_load_a_tenant_as_a_side_effect():
    """active() resolves the default tenant on first use; active_or_none() must
    not, or taking a snapshot would set the very thing being snapshotted."""
    tenant_mod.set_active(None)
    vocab.set_active(None)
    assert tenant_mod.active_or_none() is None
    assert vocab.active_or_none() is None
