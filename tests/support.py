"""Shared test constants. Tests run against the repo's own default tenant, so
they exercise real tenant data rather than a hand-built fixture -- the same
files a real run reads.
"""
from harness import tenant as tenant_mod

TENANT = tenant_mod.load_tenant()
REPO_ROOT = tenant_mod.REPO_ROOT


def newest_run_dir(base_dir, pattern):
    """The most recently created directory under `base_dir` matching glob
    `pattern`. Fix cycle 23 (R29): run ids now carry a random 4-char suffix,
    so two runs started in the same wall-clock second no longer sort
    newest-last by name alone -- mtime is the only reliable "newest" signal
    once names can tie on their timestamp prefix."""
    dirs = list(base_dir.glob(pattern))
    assert dirs, f"expected a directory matching {pattern!r} under {base_dir}"
    return max(dirs, key=lambda p: p.stat().st_mtime)
