"""Shared test constants. Tests run against the repo's own default tenant, so
they exercise real tenant data rather than a hand-built fixture -- the same
files a real run reads.
"""
from harness import tenant as tenant_mod

TENANT = tenant_mod.load_tenant()
REPO_ROOT = tenant_mod.REPO_ROOT

# Cycle 113: the claims file and tenant.yaml of the sale/offer/proof tests, frozen at
# 9bdb996 (before the 2026-10-16 sale sync stamped the site deadline on the promo
# claim). A module marked `frozen_tenant` gets them through tests/conftest.py; its own
# import-time reads use FROZEN_VERIFIED. Never read tenants/peak-saunas/claims/verified.json.
FROZEN_TENANT_DIR = REPO_ROOT / "tests" / "fixtures" / "tenants" / "peak-saunas-frozen"
FROZEN_VERIFIED = FROZEN_TENANT_DIR / "claims" / "verified.json"


def newest_run_dir(base_dir, pattern):
    """The most recently created directory under `base_dir` matching glob
    `pattern`. Fix cycle 23 (R29): run ids now carry a random 4-char suffix,
    so two runs started in the same wall-clock second no longer sort
    newest-last by name alone -- mtime is the only reliable "newest" signal
    once names can tie on their timestamp prefix."""
    dirs = list(base_dir.glob(pattern))
    assert dirs, f"expected a directory matching {pattern!r} under {base_dir}"
    return max(dirs, key=lambda p: p.stat().st_mtime)


def tenant_copy(monkeypatch, changes):
    """TENANT with some tenant.yaml values changed for one test, e.g.
    {"publish_locked": True} or {"abtest.library": [...]} (dotted keys walk
    into the mapping). The CLI loads its tenant by name; that load returns
    TENANT too, so `cli.main([... "--tenant", TENANT.name])` sees the copy.
    Cycle 103b: the 2026-10-12 launch config (publishing unlocked, landing
    pool) is the real tenant's; tests about the locked behaviour or the old
    listicle pool set it here."""
    for dotted, value in changes.items():
        *parents, key = dotted.split(".")
        node = TENANT.config
        for part in parents:
            node = node[part]
        monkeypatch.setitem(node, key, value)
    real = tenant_mod.load_tenant

    def load(name=None, *, require=False):
        if tenant_mod.resolve_tenant_name(name) == TENANT.name:
            return TENANT
        return real(name, require=require)

    monkeypatch.setattr(tenant_mod, "load_tenant", load)
    return TENANT
