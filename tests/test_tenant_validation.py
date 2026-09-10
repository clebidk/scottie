"""Cycle 22 findings R21/R22: tenant files are validated at load.

Before this, a typo'd key in tenant.yaml was silently ignored (Tenant.get
returns the default for a key that is not there), so a misconfigured tenant
produced plausible-looking wrong pages rather than stopping; and a malformed
YAML or JSON file raised a bare parser traceback with no indication of which
tenant file it came from.
"""
import pytest

from harness import tenant as tenant_mod
from harness.tenant import TENANT_SCHEMA_VERSION, Tenant, TenantFileInvalid


def _tenant(tmp_path, tenant_yaml):
    """A tenant whose claims store is filled in, so require_configured gets
    past missing_pieces and reaches validation."""
    root = tmp_path / "acme"
    (root / "claims").mkdir(parents=True)
    (root / "tenant.yaml").write_text(tenant_yaml)
    (root / "claims" / "verified.json").write_text(
        '[{"id": "spec-x", "text": "x", "category": "spec", "source": "https://acme.example"}]'
    )
    (root / "claims" / "products.json").write_text(
        '{"products": {"x": {"slug": "x", "name": "X", "url": "https://acme.example/x", "price": "1"}}}'
    )
    return Tenant("acme", root)


VALID = """
schema_version: 1
name: Acme Saunas
slug: acme-saunas
site_url: https://acme.example
site_host: acme.example
"""


# ---------------------------------------------------------------------------
# The repo's own tenants stay valid
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("name", ["peak-saunas"])
def test_a_real_tenant_validates_cleanly(name):
    errors, warnings = tenant_mod.load_tenant(name).validate()
    assert errors == []
    assert warnings == []


def test_the_template_declares_the_current_schema_version():
    template = tenant_mod.Tenant("_template", tenant_mod.TEMPLATE_DIR)
    assert template.config.get("schema_version") == TENANT_SCHEMA_VERSION


def test_the_template_still_refuses_to_run_until_it_is_filled_in():
    """The skeleton must fail validation on its placeholders -- that is the
    behaviour docs/TENANT-ONBOARDING.md promises."""
    template = tenant_mod.Tenant("_template", tenant_mod.TEMPLATE_DIR)
    errors, _ = template.validate()
    assert any("placeholder" in e for e in errors)


# ---------------------------------------------------------------------------
# schema_version
# ---------------------------------------------------------------------------

def test_a_future_schema_version_is_an_error(tmp_path):
    t = _tenant(tmp_path, VALID.replace("schema_version: 1", "schema_version: 99"))
    errors, _ = t.validate()
    assert any("schema_version is 99" in e for e in errors)
    with pytest.raises(TenantFileInvalid):
        t.require_configured()


def test_a_missing_schema_version_is_only_a_warning(tmp_path):
    """A tenant.yaml written before this key existed still runs."""
    t = _tenant(tmp_path, VALID.replace("schema_version: 1\n", ""))
    errors, warnings = t.validate()
    assert errors == []
    assert any("no schema_version" in w for w in warnings)
    assert t.schema_version == TENANT_SCHEMA_VERSION


# ---------------------------------------------------------------------------
# Required keys, types, and typos
# ---------------------------------------------------------------------------

def test_a_missing_required_key_is_an_error(tmp_path):
    t = _tenant(tmp_path, VALID.replace("site_host: acme.example\n", ""))
    errors, _ = t.validate()
    assert any("'site_host'" in e for e in errors)


def test_a_placeholder_left_in_place_is_an_error(tmp_path):
    t = _tenant(tmp_path, VALID.replace("Acme Saunas", "CHANGE ME"))
    errors, _ = t.validate()
    assert any("placeholder" in e for e in errors)


def test_a_key_of_the_wrong_type_is_an_error(tmp_path):
    """A models: block that lost its indentation used to surface as a
    TypeError inside the writer, several stages later."""
    t = _tenant(tmp_path, VALID + "models: claude-sonnet-5\n")
    errors, _ = t.validate()
    assert any("'models' must be a dict" in e for e in errors)


def test_an_unknown_key_is_reported_as_a_warning(tmp_path):
    """The whole point: a typo is invisible otherwise, because Tenant.get
    returns the default for a key that is not there."""
    t = _tenant(tmp_path, VALID + "site_hostt: acme.example\n")
    errors, warnings = t.validate()
    assert errors == []
    assert any("site_hostt" in w for w in warnings)


def test_the_known_key_set_comes_from_the_template():
    known = tenant_mod.known_tenant_keys()
    assert "schema_version" in known
    assert "default_cartridge_pool" in known
    assert "site_hostt" not in known


# ---------------------------------------------------------------------------
# Parse errors name the file
# ---------------------------------------------------------------------------

def test_malformed_yaml_names_the_file(tmp_path):
    root = tmp_path / "acme"
    root.mkdir()
    (root / "tenant.yaml").write_text("name: [unclosed\n")
    with pytest.raises(TenantFileInvalid) as exc_info:
        Tenant("acme", root)
    assert "tenant.yaml is not valid YAML" in str(exc_info.value)


def test_malformed_json_names_the_file(tmp_path):
    root = tmp_path / "acme"
    root.mkdir()
    (root / "tenant.yaml").write_text(VALID)
    (root / "claims").mkdir()
    (root / "claims" / "verified.json").write_text("{not json")
    (root / "claims" / "products.json").write_text('{"products": {}}')
    with pytest.raises(TenantFileInvalid) as exc_info:
        Tenant("acme", root).missing_pieces()
    assert "verified.json is not valid JSON" in str(exc_info.value)


def test_an_invalid_tenant_file_still_exits_4():
    """TenantFileInvalid is a TenantNotConfigured, so it keeps the exit code an
    operator's scripts already handle."""
    from harness import exits

    assert TenantFileInvalid.exit_code == exits.TENANT_NOT_CONFIGURED
