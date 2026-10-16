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

pytest_plugins = ["pytester"]

# Cycle 75: the photo library's web derivatives are gitignored, so a checkout
# may or may not have them. Point the library at an empty folder for the whole
# suite (set before any test module builds a facts_pack at import time); the
# photo-library tests build their own manifest and files under tmp_path.
os.environ["HARNESS_PHOTO_LIBRARY_DIR"] = "/nonexistent/photo-library-off-in-tests"
# Cycle 76: PEAK writes two listicle drafts per run (tenant.yaml jev.drafts).
# The suite's canned FakeClient responses are one page per cartridge, so every
# test runs one draft unless it sets HARNESS_JEV_DRAFTS itself
# (tests/test_jev_cycle76.py). No test ever sees a real TypeSafe key.
os.environ["HARNESS_JEV_DRAFTS"] = "1"
os.environ.pop("TYPESAFE_API_KEY", None)
# Cycle 92: PEAK runs the design gate in enforce mode (tenant.yaml
# design_gate). The suite's canned runs have no judge answers and should not
# drive a browser, so every test runs with the gate off unless it sets
# HARNESS_DESIGN_GATE itself (tests/test_design_gate_cycle92.py).
os.environ["HARNESS_DESIGN_GATE"] = "off"
# Cycle 111: PEAK writes a non-sale twin of every offer-led page (tenant.yaml
# sale_twin). The canned runs answer one write per cartridge, so the twin is
# off unless a test removes this (tests/test_sale_cycle111.py).
os.environ["HARNESS_SALE_TWIN"] = "off"


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


class FakeStream:
    """Cycle 102b: what `client.messages.stream(...)` returns -- a context
    manager whose get_final_message() is the canned response."""

    def __init__(self, message):
        self._message = message

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def get_final_message(self):
        return self._message


class FakeMessages:
    def __init__(self, responses):
        # each item is either a raw string (JSON or garbage), a FakeResponse,
        # or an Exception instance to raise.
        self._responses = list(responses)
        self.calls = []
        # Cycle 102b: the indexes in `calls` that came through stream()
        self.streamed = []

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

    def stream(self, **kwargs):
        """Cycle 102b: messages.stream(**kwargs) -- the same canned
        responses as create(), recorded in `calls` and `streamed`."""
        self.streamed.append(len(self.calls))
        return FakeStream(self.create(**kwargs))


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


def _unconfined_browser():
    """Cycle 110: a snap-packaged Chromium (/snap/bin/chromium, first on PATH
    on the prod host) runs in a sandbox with its OWN private /tmp, so it can
    neither read a page under pytest's tmp_path (/tmp/pytest-of-<user>/...)
    nor write the screenshot there: every `real_browser` test failed with an
    empty shot/probe (the first, test_hard_checks_on_three_synthetic_pages,
    reports the page name "overflow"). A run's own pages live under the
    tenant folder in $HOME, which the snap can read, so production is not
    affected. The suite points capture at a non-snap browser when one is
    installed (google-chrome on prod) via HARNESS_CHROME; with none, the snap
    one stays (and those tests fail as before on that host)."""

    from harness import capture

    if os.environ.get(capture.BROWSER_ENV):
        return
    found = capture.find_browser()
    if not found or not os.path.realpath(found).startswith("/snap/") and "/snap/" not in found:
        return
    for name in capture.BROWSERS:
        for folder in os.environ.get("PATH", "").split(os.pathsep):
            path = os.path.join(folder, name)
            if os.access(path, os.X_OK) and "/snap/" not in os.path.realpath(path) and "/snap/" not in path:
                os.environ[capture.BROWSER_ENV] = path
                return


_unconfined_browser()

# Cycle 113: GitHub sets CI=true and its ubuntu runners ship Chrome, but the
# `real_browser` tests are for a host with a sandbox-free Chromium (prod, a
# developer Mac), not for CI. With CI set the suite sees no browser at all:
# every `needs_browser` skipif reads capture.find_browser() at import time,
# which is after this file has loaded, so those tests skip instead of failing.
if os.environ.get("CI"):
    from harness import capture as _capture

    _capture.find_browser = lambda: None


@pytest.fixture(autouse=True)
def _no_real_browser(request, monkeypatch):
    """Cycle 92: harness/capture.py finds Chrome in the macOS application
    folder too, so a test that renders a Library or runs the design gate
    would drive a real browser on a developer Mac (slow, and not what the
    test is about). Every test sees no browser unless it is marked
    `real_browser`."""
    if request.node.get_closest_marker("real_browser"):
        return
    from harness import capture

    monkeypatch.setattr(capture, "find_browser", lambda: None)


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
    # Cycle 68: `harness meta pull` writes ads and their media here. Not
    # created up front -- the inbox makes its own directories.
    monkeypatch.setattr(type(TENANT), "meta_inbox_dir", property(lambda self: base / "meta_inbox"))
    # Cycle 67: A/B/C test records and the beacon event database.
    monkeypatch.setattr(type(TENANT), "abtests_dir", property(lambda self: base / "abtests"))
    # Cycle 69: the listicle site's job queue and audit log.
    monkeypatch.setattr(type(TENANT), "jobs_dir", property(lambda self: base / "jobs"))
    # Cycle 86: the review site's Library (harness library build).
    monkeypatch.setattr(type(TENANT), "library_dir", property(lambda self: base / "library"))
    # Cycle 87: looks imported from other pages (harness/look_import.py).
    monkeypatch.setattr(type(TENANT), "looks_dir", property(lambda self: base / "looks"))
    # Cycle 103: the tenant's library.hide list is the owner's page choice;
    # tests see every Library structure unless they set the list themselves.
    monkeypatch.setitem(TENANT.config, "library", {})
    # Cycle 90: the hand-made exemplar pack (../peak-lp-pack) lives outside
    # the repo and changes; a test that wants exemplars sets its own.
    from harness import look_design
    monkeypatch.setattr(look_design, "PACK_DIR", base / "no-pack")
    # Cycle 102: an import screenshots its page when a headless browser is on
    # this host; no test starts one unless it puts its own fake here.
    from harness import look_vision
    monkeypatch.setattr(look_vision, "find_browser", lambda: None)
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
# Cycle 69: jobs/ (job queue + audit log) too.
# Cycle 86: library/ (the Library's reference renders) too.
TRACKED_TENANT_SUBDIRS = ("out", "runs", "evals", "meta_inbox", "abtests", "jobs", "library")


def _tracked_run_artifact_paths():
    """Every path that currently exists under <tenant>/out, <tenant>/runs,
    <tenant>/evals, or <tenant>/meta_inbox (cycle 68) for every tenant
    directory, excluding anything under a path component that starts with "_archive" (tenants/peak-saunas/out/
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
        "the test suite left new files under a tenant's out/, runs/, evals/, or meta_inbox/ "
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


# ---------------------------------------------------------------------------
# Env-guard: _restore_os_environ above snapshots at function setup, which is
# AFTER any module- or session-scoped fixture has run. A module-scoped fixture
# in test_product_naming_cycle64.py called fake_run.run_once -> load_env, the
# real tenant .env (store domain, token) stayed in os.environ for every later
# module, and test_publishers' "credentials missing" test saw the real store.
# Autouse module fixtures set up before a module's requested fixtures and tear
# down after them, so this restores the environment around every module and
# fails that module loudly (key names only) if a tenant .env key leaked.
# ---------------------------------------------------------------------------

def _tenant_env_key_names():
    """The variable NAMES declared in any tenants/*/.env. Values are never
    kept or shown."""
    names = set()
    for env_path in tenant_mod.TENANTS_DIR.glob("*/.env"):
        for line in env_path.read_text().splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key = line.split("=", 1)[0].strip()
            if key.startswith("export "):
                key = key[len("export "):].strip()
            names.add(key)
    return names


def _leaked_tenant_env_keys(baseline):
    return sorted(k for k in _tenant_env_key_names() if os.environ.get(k) != baseline.get(k))


def _restore_environ(saved):
    os.environ.clear()
    os.environ.update(saved)


@pytest.fixture(scope="session", autouse=True)
def _session_environ_guard():
    baseline = dict(os.environ)
    yield baseline
    leaked = _leaked_tenant_env_keys(baseline)
    _restore_environ(baseline)
    assert not leaked, (
        f"a session-scoped fixture left tenant .env keys {leaked} in os.environ "
        "(names only). Restore os.environ in that fixture."
    )


@pytest.fixture(scope="module", autouse=True)
def _module_environ_guard(_session_environ_guard):
    saved = dict(os.environ)
    yield
    leaked = _leaked_tenant_env_keys(saved)
    _restore_environ(saved)
    assert not leaked, (
        f"a module-scoped fixture in this module left tenant .env keys {leaked} "
        "in os.environ (names only) -- something called Tenant.load_env outside "
        "a test. The environment is restored; restore it in that fixture too."
    )


# Cycle 95: the PEAK tenant ships webfonts in brand/fonts/, so every publish
# path (`harness publish`, A/B/C, post-live) would first upload them to
# Shopify Files -- a GraphQL call the publish tests' fake Shopify does not
# expect. Give the real tenant a CDN manifest that already lists every font,
# the steady state after its first real publish, in a temp file (never the
# tenant tree). tests/test_fonts_cycle95.py exercises the upload itself on a
# copy of brand/.
@pytest.fixture(scope="session")
def _real_tenant_font_manifest(tmp_path_factory):
    from harness.publishers import shopify as shopify_pub
    path = tmp_path_factory.mktemp("font-cdn") / "cdn-manifest.json"
    path.write_text(json.dumps({f.name: f"https://cdn.shopify.com/s/files/test/{f.name}"
                                for f in shopify_pub.font_files(TENANT)}))
    return path


@pytest.fixture(autouse=True)
def _real_tenant_fonts_are_already_on_the_cdn(monkeypatch, _real_tenant_font_manifest):
    from pathlib import Path
    from harness.publishers import shopify as shopify_pub
    real = shopify_pub.font_cdn_manifest_path
    real_brand = Path(TENANT.brand_dir).resolve()

    def manifest_path(tenant):
        brand_dir = getattr(tenant, "brand_dir", None)
        if brand_dir and Path(brand_dir).resolve() == real_brand:
            return _real_tenant_font_manifest
        return real(tenant)

    monkeypatch.setattr(shopify_pub, "font_cdn_manifest_path", manifest_path)


@pytest.fixture(autouse=True)
def _pin_render_today(monkeypatch):
    """Cycle 110 integration F4: a sale's deadline is compared with the render
    date (harness/offer.render_today). Every test renders on a fixed day
    before all fixture deadlines; a test that needs another day patches it."""
    import datetime

    from harness import offer

    monkeypatch.setattr(offer, "render_today", lambda tenant=None: datetime.date(2000, 1, 1))
