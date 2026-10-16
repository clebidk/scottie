"""Cycle 113: the sale, offer and proof tests read a frozen tenant, never the live files.

On 2026-10-16 at 16:58 UTC the owner's sale sync stamped the site's deadline on
the live promo claim (tenants/peak-saunas/claims/verified.json) and turned
sale_sweep on in tenants/peak-saunas/tenant.yaml. 18 tests that assert on the
earlier state went red on master, and GitHub CI with them. These six modules
now run with the `frozen_tenant` marker: tests/conftest.py gives them the
claims file and the tenant.yaml of 9bdb996
(tests/fixtures/tenants/peak-saunas-frozen/), and their import-time reads use
tests.support.FROZEN_VERIFIED. The tests below fail if a module goes back to
the live files, so the next sale sync cannot break CI the same way.
"""
import ast
import importlib
import json
import re
from pathlib import Path

import pytest
import yaml

from tests.support import FROZEN_TENANT_DIR, FROZEN_VERIFIED, REPO_ROOT, TENANT

FROZEN_MODULES = (
    "test_offer_cycle109", "test_presell_cycle110", "test_proof_cycle110",
    "test_integration_cycle110", "test_hotfix_cycle110", "test_sale_cycle111",
)
TESTS_DIR = Path(__file__).parent

# A literal path into the live tenant folder, or another way to reach it.
_LIVE_PATH = re.compile(r"""tenants["']?\s*[/,]\s*["']?peak-saunas|tenant_dir\(|TENANTS_DIR|TENANT\.root\b""")


def _import_time_claims_dir_reads(tree):
    """Every `.claims_dir` that runs when the module is imported (outside any
    def or lambda). The frozen claims folder exists only while a test runs, so
    an import-time read sees the live file."""
    found = []

    def visit(node):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
            return
        if isinstance(node, ast.Attribute) and node.attr == "claims_dir":
            found.append(node.lineno)
        for child in ast.iter_child_nodes(node):
            visit(child)

    visit(tree)
    return found


@pytest.mark.parametrize("name", FROZEN_MODULES)
def test_the_module_never_reads_the_live_tenant_files(name):
    source = (TESTS_DIR / f"{name}.py").read_text()
    live = [m.group(0) for m in _LIVE_PATH.finditer(source)]
    assert not live, (
        f"tests/{name}.py reads the live tenant folder ({live}). Use tests.support.FROZEN_VERIFIED at import "
        "time and TENANT.claims_dir inside a test (the frozen_tenant marker points it at the frozen copy).")
    lines = _import_time_claims_dir_reads(ast.parse(source))
    assert not lines, (
        f"tests/{name}.py reads .claims_dir when it is imported (line {lines}), which is the live folder. "
        "Use tests.support.FROZEN_VERIFIED.")


@pytest.mark.parametrize("name", FROZEN_MODULES)
def test_the_module_carries_the_frozen_tenant_marker(name):
    marks = getattr(importlib.import_module(f"tests.{name}"), "pytestmark", [])
    marks = marks if isinstance(marks, list) else [marks]
    assert any(m.name == "frozen_tenant" for m in marks), f"tests/{name}.py lost `pytestmark = pytest.mark.frozen_tenant`"


@pytest.mark.frozen_tenant
def test_a_frozen_test_reads_the_frozen_claims_and_tenant_yaml():
    claims = TENANT.claims_dir
    assert REPO_ROOT / "tenants" not in claims.parents
    assert (claims / "verified.json").read_bytes() == FROZEN_VERIFIED.read_bytes()
    assert (claims / "products.json").exists()      # the rest of the claims folder stays the live one
    live = yaml.safe_load((TENANT.root / "tenant.yaml").read_text())
    frozen = yaml.safe_load((FROZEN_TENANT_DIR / "tenant.yaml").read_text())
    for key in (k for k in frozen if live.get(k) != frozen[k]):
        assert TENANT.config[key] == frozen[key], key


def test_an_unmarked_test_still_reads_the_real_tenant_folder():
    assert TENANT.claims_dir == REPO_ROOT / "tenants" / "peak-saunas" / "claims"


def test_the_frozen_promo_claim_is_the_one_before_the_sale_sync():
    """Refreshing the freeze from the live file would put the synced deadline
    back in and turn the 18 tests red again."""
    promo = [c for c in json.loads(FROZEN_VERIFIED.read_text()) if c["category"] == "promo"]
    assert [c["id"] for c in promo] == ["promo-community-savings-sale-2026-10"]
    assert "last_sale_day" not in promo[0] and "deadline_end" not in promo[0]
