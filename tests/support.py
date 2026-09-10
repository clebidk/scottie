"""Shared test constants. Tests run against the repo's own default tenant, so
they exercise real tenant data rather than a hand-built fixture -- the same
files a real run reads.
"""
from harness import tenant as tenant_mod

TENANT = tenant_mod.load_tenant()
REPO_ROOT = tenant_mod.REPO_ROOT
