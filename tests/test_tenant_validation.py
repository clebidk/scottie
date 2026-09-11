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


# ---------------------------------------------------------------------------
# Review 2026-09-11 R23: tenant.yaml vs claims/config.json overlap -- the
# precedence (config.json wins) is deliberate and unchanged; disagreements
# must be VISIBLE (run log + doctor), not silently absorbed.
# ---------------------------------------------------------------------------

def _tenant_with_configs(tmp_path, *, yaml_extra="", json_config=None, name="acme"):
    import json as _json

    root = tmp_path / name
    (root / "claims").mkdir(parents=True)
    (root / "tenant.yaml").write_text(VALID + yaml_extra)
    (root / "claims" / "verified.json").write_text(
        '[{"id": "spec-x", "text": "x", "category": "spec", "source": "https://acme.example"}]'
    )
    (root / "claims" / "products.json").write_text(
        '{"products": {"x": {"slug": "x", "name": "X", "url": "https://acme.example/x", "price": "1"}}}'
    )
    if json_config is not None:
        (root / "claims" / "config.json").write_text(_json.dumps(json_config))
    return Tenant(name, root)


def test_config_disagreement_is_detected(tmp_path):
    t = _tenant_with_configs(
        tmp_path,
        yaml_extra="ad_overclaim_policy: warn\n",
        json_config={"ad_overclaim_policy": "stop"},
    )
    assert t.config_disagreements() == [("ad_overclaim_policy", "warn", "stop")]


def test_no_disagreement_when_values_agree_or_a_key_is_only_in_one_file(tmp_path):
    agree = _tenant_with_configs(
        tmp_path, yaml_extra="ad_overclaim_policy: warn\n", json_config={"ad_overclaim_policy": "warn"}, name="agree"
    )
    assert agree.config_disagreements() == []
    only_yaml = _tenant_with_configs(tmp_path, yaml_extra="ad_overclaim_policy: warn\n", json_config={}, name="only-yaml")
    assert only_yaml.config_disagreements() == []
    only_json = _tenant_with_configs(tmp_path, json_config={"ad_overclaim_policy": "stop"}, name="only-json")
    assert only_json.config_disagreements() == []
    no_json = _tenant_with_configs(tmp_path, yaml_extra="ad_overclaim_policy: warn\n", name="no-json")
    assert no_json.config_disagreements() == []


def test_config_json_still_wins_precedence_is_unchanged(tmp_path):
    """The behavior-preserving pin: R23 adds visibility, not a precedence
    change -- claims/config.json's value is the one the run reads."""
    t = _tenant_with_configs(
        tmp_path,
        yaml_extra="ad_overclaim_policy: warn\n",
        json_config={"ad_overclaim_policy": "stop"},
    )
    assert t.claims_config["ad_overclaim_policy"] == "stop"


def test_doctor_warns_on_a_config_disagreement(tmp_path):
    from harness import doctor

    t = _tenant_with_configs(
        tmp_path,
        yaml_extra="ad_overclaim_policy: warn\n",
        json_config={"ad_overclaim_policy": "stop"},
    )
    rows = doctor.check_config(t)
    warn_rows = [c for c in rows if c.name == "config ad_overclaim_policy"]
    assert len(warn_rows) == 1
    assert warn_rows[0].status == doctor.WARN
    assert "claims/config.json" in warn_rows[0].detail


def test_prepare_run_logs_a_config_disagreement(monkeypatch, tmp_path):
    import argparse

    from harness import pipeline
    from tests.support import TENANT

    monkeypatch.setattr(
        type(TENANT), "config_disagreements",
        lambda self: [("ad_overclaim_policy", "warn", "stop")],
    )
    args = argparse.Namespace(
        input="tenants/peak-saunas/fixtures/founder-warranty-demo.txt",
        cartridges="article", seed=42, product=None,
    )
    state = pipeline.RunState(tenant=TENANT, args=args, client=None)
    pipeline.prepare_run(state)
    log_text = (TENANT.runs_dir / f"{state.run_id}.log").read_text()
    assert "config disagreement" in log_text
    assert "claims/config.json wins" in log_text
