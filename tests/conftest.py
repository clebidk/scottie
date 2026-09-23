"""Shared test doubles and the two suite-wide guarantees.

No network calls anywhere in tests -- every Anthropic client is this fake,
injected explicitly, and since Cycle 22 that is enforced rather than assumed
(see _no_network below).
"""
import hashlib
import json
import socket

import os
import pytest

from harness import tenant as tenant_mod
from harness import vocab
from tests.support import TENANT


class FakeUsage:
    def __init__(self, input_tokens=10, output_tokens=10, *,
                 cache_creation_input_tokens=0, cache_read_input_tokens=0):
        self.input_tokens = input_tokens
        self.output_tokens = output_tokens
        # Fix cycle 17: real Anthropic SDK Usage objects always carry these
        # two fields (zero when a call has no cache_control breakpoint) --
        # defaulted here so every existing test double keeps working.
        self.cache_creation_input_tokens = cache_creation_input_tokens
        self.cache_read_input_tokens = cache_read_input_tokens


class FakeBlock:
    def __init__(self, text):
        self.type = "text"
        self.text = text


class FakeResponse:
    def __init__(self, text, input_tokens=10, output_tokens=10, *,
                 cache_creation_input_tokens=0, cache_read_input_tokens=0):
        self.content = [FakeBlock(text)]
        self.usage = FakeUsage(
            input_tokens, output_tokens,
            cache_creation_input_tokens=cache_creation_input_tokens,
            cache_read_input_tokens=cache_read_input_tokens,
        )


class FakeMessages:
    def __init__(self, responses):
        # each item is either a raw string (JSON or garbage), a FakeResponse,
        # or an Exception instance to raise.
        self._responses = list(responses)
        self.calls = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        if not self._responses:
            raise AssertionError("FakeClient ran out of canned responses")
        item = self._responses.pop(0)
        if isinstance(item, Exception):
            raise item
        if isinstance(item, str):
            return FakeResponse(item)
        return item


class FakeClient:
    def __init__(self, responses):
        self.messages = FakeMessages(responses)


def json_response(obj):
    return json.dumps(obj)


def block_text(content):
    """Fix cycle 17: a system/message "content" value sent to
    client.messages.create is now either a plain string, or a list of
    {"type": "text", "text": ..., ["cache_control": ...]} blocks (write.py's
    cache_control breakpoints) -- joins either shape into one string so a
    test can keep doing plain substring assertions regardless of which shape
    a given call used."""
    if isinstance(content, str):
        return content
    return "".join(b["text"] for b in content if isinstance(b, dict) and b.get("type") == "text")


@pytest.fixture
def fake_client_factory():
    return lambda responses: FakeClient(responses)


# ---------------------------------------------------------------------------
# Cycle 22 findings R31/R32: two things that were conventions, now guarantees.
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def _restore_active_tenant():
    """harness/tenant.py and harness/vocab.py each keep the active tenant in a
    module-level global, and cli.cmd_run calls activate() without ever putting
    it back. Every test that ran after tests/test_cli_run.py therefore saw a
    different active tenant depending on collection order -- and every module
    that falls back to tenant_mod.active() / vocab.active() (render_page, the
    claims gate, the writer prompt) reads that global. Exactly one test used to
    do this correctly, with its own try/finally."""
    previous_tenant = tenant_mod.active_or_none()
    previous_vocab = vocab.active_or_none()
    try:
        yield
    finally:
        tenant_mod.set_active(previous_tenant)
        vocab.set_active(previous_vocab)


@pytest.fixture(autouse=True)
def _no_network(request, monkeypatch):
    """README.md has always said the suite makes no network calls. That was
    true, but only by convention -- nothing stopped a new test that forgot to
    inject a fake fetcher from hitting a real storefront, a real Drive file, or
    a real model. Any attempt to open a socket now fails the test that made it.

    A test marked `live` opts out. There are none today."""
    if request.node.get_closest_marker("live"):
        return

    def _blocked(*args, **kwargs):
        raise AssertionError(
            "this test tried to open a network connection -- the suite runs "
            "offline. Inject a fake client/fetcher, or mark the test `live`."
        )

    monkeypatch.setattr(socket.socket, "connect", _blocked)
    monkeypatch.setattr(socket.socket, "connect_ex", _blocked)
    monkeypatch.setattr(socket, "create_connection", _blocked)


# ---------------------------------------------------------------------------
# Cycle 37: every test that touches Tenant.out_dir/runs_dir/evals_path (via
# `harness run`, a workflow, record_score, ...) is redirected into tmp_path.
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def isolated_tenant_paths(request, tmp_path, monkeypatch):
    """Cycle 35a first wrote this fixture (as tests/test_serve.py's own,
    opt-in copy) for the handful of tests that drive `harness serve` against
    the real peak-saunas `TENANT`. Cycle 37 found it was never enough: any
    other test that calls cli.cmd_run/pipeline.prepare_run/evals.record_score
    against TENANT -- test_cli_run.py, test_comparison.py, test_evals.py,
    test_fake_run.py, test_revise.py, test_workflows.py, test_budget.py,
    test_tenant_validation.py -- had no isolation at all, because the fixture
    was opt-in and none of them opted in. Running the full suite once wrote
    156 real run dirs (state.json, rendered index.html, the lot) into
    tenants/peak-saunas/out/ and 156 logs into runs/ -- see
    tenants/peak-saunas/out/_archive-test-runs-2026-09-16/.

    Autouse fixes that: every test gets out_dir/runs_dir/evals_path redirected
    into its own tmp_path before it runs, with no per-test opt-in to forget.
    `out_dir`/`runs_dir`/`evals_path` are plain `self.root / "..."` properties
    on Tenant (harness/tenant.py), so patching them at the class level
    redirects every write any test makes -- through cli.cmd_run,
    runstate.approve/reject/request_changes, and evals.record_score alike --
    while claims_dir/brand_dir/fixtures_dir/config keep reading the real
    tenant data tests need for realistic content. A fresh Tenant instance
    (evals/fake_run.py calls tenant_mod.load_tenant() itself) is covered too,
    since the patch is on the class, not this particular TENANT object.

    One test (test_tenant.py's test_tenant_paths_all_live_under_the_tenant_root)
    asserts what these properties return when NOT patched, and opts out with
    `@pytest.mark.real_tenant_paths`."""
    if request.node.get_closest_marker("real_tenant_paths"):
        return None

    # A dedicated subdirectory, not tmp_path itself: plenty of unrelated
    # tests build their own tmp_path / "out" or tmp_path / "runs" (FakeTenant
    # in test_runstate.py, the unwritable-ledger case in test_budget.py,
    # test_path_safety.py's traversal check, test_serve_images.py's own
    # runs_dir isolation) and call .mkdir() on it with no exist_ok -- this
    # fixture running first and creating the same top-level name first would
    # collide with every one of them.
    base = tmp_path / "tenant-isolation"
    out_dir = base / "out"
    runs_dir = base / "runs"
    out_dir.mkdir(parents=True)
    runs_dir.mkdir(parents=True)
    monkeypatch.setattr(type(TENANT), "out_dir", property(lambda self: out_dir))
    monkeypatch.setattr(type(TENANT), "runs_dir", property(lambda self: runs_dir))
    monkeypatch.setattr(type(TENANT), "evals_path", property(lambda self: base / "evals" / "scores.jsonl"))
    # Cycle 67: A/B/C test records and the beacon event database.
    monkeypatch.setattr(type(TENANT), "abtests_dir", property(lambda self: base / "abtests"))
    return base


# ---------------------------------------------------------------------------
# Cycle 35a: the real tenant's evals files are never touched by the suite
# ---------------------------------------------------------------------------

def _evals_file_hashes():
    """sha256 of tenants/peak-saunas/evals/scores.jsonl and approvals.jsonl,
    or None for a file that does not exist -- both are legitimately empty as
    of Cycle 35a (Caleb has not scored anything yet)."""
    root = tenant_mod.tenant_dir("peak-saunas") / "evals"
    hashes = {}
    for name in ("scores.jsonl", "approvals.jsonl"):
        path = root / name
        hashes[name] = hashlib.sha256(path.read_bytes()).hexdigest() if path.exists() else None
    return hashes


@pytest.fixture(scope="session", autouse=True)
def _real_tenant_evals_unchanged():
    """Cycle 35 found ~89 lines each of test-generated junk in
    tenants/peak-saunas/evals/scores.jsonl and approvals.jsonl -- a test
    that calls record_score/approve/reject/request_changes against the real
    peak-saunas Tenant instead of a tmp_path/FakeTenant one (exactly the bug
    fixed in tests/test_serve.py this cycle) corrupts Caleb's real review
    history silently, since nothing about that failed. Hashed once before
    the first test in the session and once after the last -- this is a
    session-scoped fixture, so its teardown (after the `yield`) runs only
    once every other test has finished, regardless of file or test order.
    tests/test_suite_guards.py's test_real_tenant_evals_files_are_unchanged
    exercises the baseline this captures; the enforcement is this assert."""
    before = _evals_file_hashes()
    yield before
    after = _evals_file_hashes()
    assert after == before, (
        "the test suite modified tenants/peak-saunas/evals/scores.jsonl and/or "
        f"approvals.jsonl -- before={before} after={after}. Some test wrote to the "
        "real tenant's evals files instead of a tmp_path/FakeTenant one."
    )


# ---------------------------------------------------------------------------
# Cycle 37: the suite-wide backstop -- isolated_tenant_paths above stops the
# writes it knows about; this catches anything it doesn't.
# ---------------------------------------------------------------------------

# Cycle 67: abtests/ (test records, events.sqlite) is tracked like out/.
TRACKED_TENANT_SUBDIRS = ("out", "runs", "evals", "abtests")


def _tracked_run_artifact_paths():
    """Every path that currently exists under <tenant>/out, <tenant>/runs, or
    <tenant>/evals for every tenant directory, excluding anything under a
    path component that starts with "_archive" (tenants/peak-saunas/out/
    _archive-test-runs-2026-09-16/ and tenants/*/evals/_archive/ are
    reference/history, not something a clean session should add to or
    remove from). Pure reads -- no directory is created here, so a suite run
    that touches nothing real leaves this fixture's own footprint at zero."""
    paths = set()
    for tenant_root in sorted(p for p in tenant_mod.TENANTS_DIR.iterdir() if p.is_dir()):
        for sub in TRACKED_TENANT_SUBDIRS:
            base = tenant_root / sub
            if not base.exists():
                continue
            for path in base.rglob("*"):
                rel_parts = path.relative_to(tenant_mod.TENANTS_DIR).parts
                if any(part.startswith("_archive") for part in rel_parts):
                    continue
                paths.add(path)
    return paths


@pytest.fixture(scope="session", autouse=True)
def _no_new_tenant_run_artifacts():
    """Cycle 37: isolated_tenant_paths (above) redirects every write that
    goes through Tenant.out_dir/runs_dir/evals_path, which is how the 156
    leaked run dirs from Cycle 37's incident were made -- but a future test
    that reaches a tenant's out/runs/evals tree some other way (a hardcoded
    "tenants/peak-saunas/out/..." string, a Tenant built before this session's
    fixtures ran, a new write path evals.py grows) would slip past it
    silently, the same way the old opt-in fixture did. This is the backstop:
    snapshot the *set* of paths under every tenant's out/, runs/, and evals/
    at session start, diff against the same snapshot at session end, and fail
    loudly with the exact new paths if the set grew by even one file --
    whether this session ran the full suite or a single test file, since the
    snapshot is taken fresh each time rather than assuming a fixed baseline."""
    before = _tracked_run_artifact_paths()
    yield
    after = _tracked_run_artifact_paths()
    new_paths = sorted(str(p) for p in (after - before))
    assert not new_paths, (
        "the test suite left new files under a tenant's out/, runs/, or evals/ "
        "directory -- isolated_tenant_paths should have redirected this write "
        "into tmp_path:\n  " + "\n  ".join(new_paths)
    )


# ---------------------------------------------------------------------------
# Cycle 38: any test that calls a real command (cmd_run, cmd_publish, ...)
# runs Tenant.load_env, which load_dotenv()s the real tenants/<t>/.env into
# os.environ with no cleanup -- and load_dotenv bypasses monkeypatch, so a
# later test that expects e.g. SHOPIFY_TOKEN to be unset would see the real
# credential. Snapshot and restore the whole environment around every test.
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def _restore_os_environ():
    saved = dict(os.environ)
    try:
        yield
    finally:
        os.environ.clear()
        os.environ.update(saved)
