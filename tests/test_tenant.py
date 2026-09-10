"""Tenant resolution, the tenant skeleton, and the rule that the engine and the
cartridges carry no company's words."""
import json
import re

import pytest
import yaml

from harness import tenant as tenant_mod
from harness import vocab
from tests.support import REPO_ROOT, TENANT

# ---------------------------------------------------------------------------
# Resolution order: --tenant flag > HARNESS_TENANT env > tenants/default.txt
# ---------------------------------------------------------------------------

def test_flag_wins_over_env_and_default(monkeypatch):
    monkeypatch.setenv(tenant_mod.TENANT_ENV_VAR, "from-env")
    assert tenant_mod.resolve_tenant_name("from-flag") == "from-flag"


def test_env_wins_over_default(monkeypatch):
    monkeypatch.setenv(tenant_mod.TENANT_ENV_VAR, "from-env")
    assert tenant_mod.resolve_tenant_name(None) == "from-env"


def test_default_file_is_the_last_resort(monkeypatch):
    monkeypatch.delenv(tenant_mod.TENANT_ENV_VAR, raising=False)
    assert tenant_mod.resolve_tenant_name(None) == tenant_mod.DEFAULT_FILE.read_text().strip()


def test_default_file_names_the_first_tenant():
    assert tenant_mod.DEFAULT_FILE.read_text().strip() == "peak-saunas"


def test_blank_env_falls_through_to_the_default(monkeypatch):
    monkeypatch.setenv(tenant_mod.TENANT_ENV_VAR, "   ")
    assert tenant_mod.resolve_tenant_name(None) == "peak-saunas"


def test_unknown_tenant_is_a_clear_error_not_a_traceback():
    with pytest.raises(tenant_mod.UnknownTenant) as e:
        tenant_mod.load_tenant("no-such-company")
    assert "unknown tenant" in str(e.value)


# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

def test_tenant_paths_all_live_under_the_tenant_root():
    for path in (
        TENANT.claims_dir, TENANT.brand_dir, TENANT.fixtures_dir, TENANT.env_path,
        TENANT.out_dir, TENANT.runs_dir, TENANT.evals_path, TENANT.inbox_dir,
    ):
        assert TENANT.root in path.parents


def test_exemplars_and_overrides_resolve_per_cartridge():
    assert TENANT.exemplars_dir("article") is not None
    assert TENANT.exemplars_dir("no-such-cartridge") is None
    assert TENANT.cartridge_overrides("no-such-cartridge") is None


# ---------------------------------------------------------------------------
# Config layering: defaults < tenant.yaml < claims/config.json
# ---------------------------------------------------------------------------

def test_claims_config_json_wins_over_tenant_yaml():
    on_disk = json.loads((TENANT.claims_dir / "config.json").read_text())
    config = TENANT.claims_config
    for key, value in on_disk.items():
        assert config[key] == value


def test_claims_config_fills_in_every_default_key():
    config = TENANT.claims_config
    for key in tenant_mod.DEFAULT_CLAIMS_CONFIG:
        assert key in config


# ---------------------------------------------------------------------------
# Placeholder rendering
# ---------------------------------------------------------------------------

def test_render_substitutes_tenant_and_author_placeholders():
    out = TENANT.render("{{ tenant.name }} / {{ authors.author.name }}")
    assert out == f"{TENANT.display_name} / {TENANT.author('author')['name']}"


def test_render_leaves_an_unknown_placeholder_alone():
    assert TENANT.render("{{ tenant.no_such_key }}") == "{{ tenant.no_such_key }}"


# ---------------------------------------------------------------------------
# The engine and the cartridges carry no company's words
# ---------------------------------------------------------------------------

TENANT_WORDS = ("Peak", "Austin", "Judge.me", "Aurora")


def _files(root, suffixes):
    return [p for p in root.rglob("*") if p.is_file() and p.suffix in suffixes]


@pytest.mark.parametrize("root", ["harness", "cartridges"])
def test_no_tenant_specific_words_in_the_engine_or_the_cartridges(root):
    offenders = []
    for path in _files(REPO_ROOT / root, {".py", ".md", ".json", ".html", ".css", ".yaml"}):
        text = path.read_text(errors="ignore")
        for word in TENANT_WORDS:
            for i, line in enumerate(text.splitlines(), 1):
                if word in line:
                    offenders.append(f"{path.relative_to(REPO_ROOT)}:{i}: {word}")
    assert offenders == [], "tenant-specific words leaked into tenant-neutral code:\n" + "\n".join(offenders)


def test_cartridges_use_placeholders_where_a_company_belongs():
    text = (REPO_ROOT / "cartridges" / "article" / "cartridge.md").read_text()
    assert "{{ tenant.name }}" in text


# ---------------------------------------------------------------------------
# The tenant skeleton
# ---------------------------------------------------------------------------

def test_template_has_every_file_a_new_tenant_needs():
    root = tenant_mod.TEMPLATE_DIR
    for rel in (
        "tenant.yaml", "authors.yaml", "vocab.yaml", "guardrails.md", "README.md",
        ".env.example", "claims/verified.json", "claims/pending.json",
        "claims/products.json", "claims/config.json", "brand/tokens.json",
        "brand/base.css", "brand/byline.html",
    ):
        assert (root / rel).exists(), f"missing from the tenant template: {rel}"


def test_template_yaml_files_all_parse():
    for name in ("tenant.yaml", "authors.yaml", "vocab.yaml"):
        assert isinstance(yaml.safe_load((tenant_mod.TEMPLATE_DIR / name).read_text()), dict)


def test_template_declares_every_key_the_engine_reads():
    template = yaml.safe_load((tenant_mod.TEMPLATE_DIR / "tenant.yaml").read_text())
    live = yaml.safe_load((TENANT.root / "tenant.yaml").read_text())
    missing = sorted(set(live) - set(template))
    assert missing == [], f"tenant.yaml keys the template never mentions: {missing}"


def test_template_vocab_declares_every_key_the_engine_reads():
    template = yaml.safe_load((tenant_mod.TEMPLATE_DIR / "vocab.yaml").read_text())
    live = yaml.safe_load((TENANT.root / "vocab.yaml").read_text())
    assert sorted(set(live) - set(template)) == []


def test_template_is_not_itself_a_usable_tenant():
    """A fresh copy of the template must refuse to run, with a message that says
    what is missing -- not a traceback halfway through a run."""
    skeleton = tenant_mod.Tenant("_template", tenant_mod.TEMPLATE_DIR)
    missing = skeleton.missing_pieces()
    assert missing
    with pytest.raises(tenant_mod.TenantNotConfigured) as e:
        skeleton.require_configured()
    message = str(e.value)
    assert message.startswith("tenant not configured: missing ")
    assert "claims" in message


def test_configured_tenant_passes_the_readiness_check():
    assert TENANT.missing_pieces() == []
    TENANT.require_configured()


# ---------------------------------------------------------------------------
# Vocabulary comes from tenant data, not from code
# ---------------------------------------------------------------------------

def test_vocab_reflects_the_active_tenants_yaml():
    data = yaml.safe_load((TENANT.root / "vocab.yaml").read_text())
    v = vocab.Vocabulary(data)
    assert v.emf_terms == tuple(data["emf_terms"])
    assert set(v.always_forbidden_terms) >= set(data["emf_terms"]) | set(data["banned_names"])


def test_a_tenant_with_no_bans_forbids_nothing_and_matches_nothing():
    """An empty vocab must not turn into a regex that matches every string."""
    v = vocab.Vocabulary({})
    assert v.lender_name_re.search("Affirm") is None
    assert v.trigger_word_re.search("clinical") is None


def test_activate_switches_the_active_vocabulary():
    previous = vocab.active()
    try:
        vocab.set_active(vocab.Vocabulary({"banned_names": ["acme"]}))
        assert vocab.BANNED_NAMES == ("acme",)
    finally:
        vocab.set_active(previous)


# ---------------------------------------------------------------------------
# Fix cycle 16 item 9 (Thursday queue item 3): consult-CTA config.
# ---------------------------------------------------------------------------


def test_peak_saunas_cta_mode_defaults_to_buy():
    assert TENANT.get("cta_mode") == "buy"


def test_peak_saunas_cta_variants_consult_list_is_configured():
    consult = TENANT.get("cta_variants.consult")
    assert consult
    assert any("{tenant_short_name}" in t for t in consult)


def test_peak_saunas_tenant_short_name_is_configured():
    assert TENANT.get("tenant_short_name")


# ---------------------------------------------------------------------------
# Fix cycle 16 item 10 (Thursday queue item 3): HSA/FSA via TrueMed --
# pending, never verified, until Caleb signs off.
# ---------------------------------------------------------------------------


def test_hsa_fsa_truemed_claim_is_pending_not_verified():
    pending = json.loads((TENANT.claims_dir / "pending.json").read_text())
    ids = {c["id"] for c in pending}
    assert "pending-hsa-fsa-truemed" in ids
    entry = next(c for c in pending if c["id"] == "pending-hsa-fsa-truemed")
    assert entry["status"] in ("pending_review", "needs-caleb")

    verified = json.loads((TENANT.claims_dir / "verified.json").read_text())
    verified_ids = {c["id"] for c in verified}
    assert "pending-hsa-fsa-truemed" not in verified_ids
    # No verified claim states the specific new trust line itself (an
    # existing internal financing-terms claim mentions TrueMed only as
    # background context, which is not the same as an approved page claim).
    assert not any(entry["text"].lower() in c["text"].lower() for c in verified)


# ---------------------------------------------------------------------------
# Fix cycle 16 item 11: aliases on the full-spectrum/red-light allowlist
# claims.
# ---------------------------------------------------------------------------


def test_full_spectrum_and_red_light_claims_carry_the_expected_aliases():
    verified = json.loads((TENANT.claims_dir / "verified.json").read_text())
    by_id = {c["id"]: c for c in verified}
    for cid in ("gbrain-allowlist-360-full-spectrum", "gbrain-allowlist-red-light"):
        aliases = by_id[cid].get("aliases") or []
        assert "4-in-1" in aliases
        assert "medical-grade panel" in aliases
