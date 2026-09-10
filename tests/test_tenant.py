"""Tenant resolution, the tenant skeleton, and the rule that the engine and the
cartridges carry no company's words."""
import json
import re

import pytest
import yaml

from harness import tenant as tenant_mod
from harness import vocab
from tests.support import REPO_ROOT, TENANT
from harness import config as harness_config

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

# Cycle 22 finding R7: the list above is matched case-sensitively and holds
# four proper nouns, so it missed a lowercase "peak-saunas" in a docstring and
# every model name in the catalog -- fifteen leaks in harness/ comments and
# docstrings. This is the wider list, applied to harness/ only.
#
# It is deliberately NOT applied to cartridges/: three cartridge.md files name
# a model in a worked CTA example, and a cartridge.md is prompt text, so
# rewriting one changes what the writer is told. That is tracked as an open
# finding (R39) rather than fixed here.
ENGINE_TENANT_WORDS = TENANT_WORDS + (
    "peak-saunas", "Peak Saunas", "Caleb", "Niednagel", "Laudenslager",
    "Fuji", "Everest", "Rainier", "Shasta", "Denali", "Matterhorn",
    "Patagonia", "El Capitan", "Kilimanjaro",
)

# An adapter is named after the outside system it talks to -- harness/sources/
# judgeme.py, harness/publishers/shopify.py -- the same way a database driver
# is. That is a dependency's name, not a tenant's value, so the module's own
# path is exempt; its CONTENTS are scanned like anything else.
_VENDOR_ADAPTER_PATHS = ("sources/judgeme.py", "publishers/shopify.py")

# One known, deliberate exception. harness/cli.py's build_revision_note quotes
# a real claim id as the worked example of "a claim id printed as text", and
# that string is PROMPT text -- it is sent to the model inside every REVISION
# REQUIRED block. harness/write.py's own system prompt already uses the
# generic "spec-model-capacity" form for the same example, so making the two
# agree is a one-word prompt edit and a product decision, not a mechanical
# cleanup. Tracked as an open finding; exempted here rather than silently
# widening the whole rule.
_PROMPT_TEXT_EXEMPTIONS = {("harness/cli.py", "Fuji")}


def _files(root, suffixes):
    return [p for p in root.rglob("*") if p.is_file() and p.suffix in suffixes]


@pytest.mark.parametrize("root", ["harness", "cartridges"])
def test_no_tenant_specific_words_in_the_engine_or_the_cartridges(root):
    # Whole words, case-insensitively: a bare substring match would flag
    # "speaker"/"speaks" for containing "peak", which is why the original
    # list had to stay case-sensitive to be usable at all.
    patterns = [(w, re.compile(r"\b" + re.escape(w) + r"\b", re.IGNORECASE))
                for w in (ENGINE_TENANT_WORDS if root == "harness" else TENANT_WORDS)]
    offenders = []
    for path in _files(REPO_ROOT / root, {".py", ".md", ".json", ".html", ".css", ".yaml"}):
        text = path.read_text(errors="ignore")
        for i, line in enumerate(text.splitlines(), 1):
            for word, pattern in patterns:
                rel = str(path.relative_to(REPO_ROOT))
                if pattern.search(line) and (rel, word) not in _PROMPT_TEXT_EXEMPTIONS:
                    offenders.append(f"{rel}:{i}: {word}")
    assert offenders == [], "tenant-specific words leaked into tenant-neutral code:\n" + "\n".join(offenders)


def test_cartridges_use_placeholders_where_a_company_belongs():
    text = (REPO_ROOT / "cartridges" / "article" / "cartridge.md").read_text()
    assert "{{ tenant.name }}" in text


# Fix cycle 21: financing_line is now a fixed sentence formatted with the
# tenant's actual configured lender at prompt-build time (harness/write.py's
# global_voice_block) -- cartridges must stay tenant-neutral and never name
# a lender directly, the same way they never name the company (above). Every
# forbidden_lender_name across both tenants' vocab.yaml, plus the one real
# configured lender (Bread Pay), covers every lender name currently known to
# this codebase.
LENDER_WORDS = ("Bread Pay", "Affirm", "Shop Pay", "Klarna", "Afterpay", "Sezzle")


def test_cartridges_never_name_a_financing_lender():
    offenders = []
    for path in _files(REPO_ROOT / "cartridges", {".md", ".json"}):
        text = path.read_text(errors="ignore")
        for word in LENDER_WORDS:
            for i, line in enumerate(text.splitlines(), 1):
                if word.lower() in line.lower():
                    offenders.append(f"{path.relative_to(REPO_ROOT)}:{i}: {word}")
    assert offenders == [], "a lender name leaked into a tenant-neutral cartridge:\n" + "\n".join(offenders)


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
# Fix cycle 21: allowed_financing_sentence_with_lender_template -- both
# tenants' vocab.yaml declare it (checked generically above by
# test_template_vocab_declares_every_key_the_engine_reads and
# test_template_yaml_files_all_parse already parsing every key), and the
# with-lender sentence formats correctly from it.
# ---------------------------------------------------------------------------

def test_both_tenants_vocab_yaml_declare_the_with_lender_financing_template():
    template_data = yaml.safe_load((tenant_mod.TEMPLATE_DIR / "vocab.yaml").read_text())
    live_data = yaml.safe_load((TENANT.root / "vocab.yaml").read_text())
    assert "allowed_financing_sentence_with_lender_template" in template_data
    assert "{lender}" in template_data["allowed_financing_sentence_with_lender_template"]
    assert "{lender}" in live_data["allowed_financing_sentence_with_lender_template"]
    # Tenant-neutral: no lender name baked into the template itself.
    assert "bread pay" not in live_data["allowed_financing_sentence_with_lender_template"].lower()


def test_allowed_financing_sentence_formats_the_with_lender_template():
    data = yaml.safe_load((TENANT.root / "vocab.yaml").read_text())
    v = vocab.Vocabulary(data)
    assert v.allowed_financing_sentence("Bread Pay") == "Financing is available through Bread Pay at checkout."
    # No lender given (or falsy) falls back to the no-lender sentence --
    # same value as ALLOWED_FINANCING_SENTENCE_NO_LENDER for this tenant.
    assert v.allowed_financing_sentence(None) == v.allowed_financing_sentence_no_lender
    assert v.allowed_financing_sentence("") == v.allowed_financing_sentence_no_lender


def test_allowed_financing_sentence_module_function_matches_the_active_vocabulary():
    assert vocab.allowed_financing_sentence("Bread Pay") == "Financing is available through Bread Pay at checkout."
    assert vocab.allowed_financing_sentence(None) == vocab.ALLOWED_FINANCING_SENTENCE_NO_LENDER


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


def test_policy_financing_doc_stale_claim_is_pending_not_verified():
    # Cycle 18 approved hsa-fsa-truemed into verified.json, so this now
    # exercises a claim that genuinely stays pending: policy-financing-doc-stale
    # (g Brain's financing policy page still needs a team update).
    pending = json.loads((TENANT.claims_dir / "pending.json").read_text())
    ids = {c["id"] for c in pending}
    assert "policy-financing-doc-stale" in ids
    entry = next(c for c in pending if c["id"] == "policy-financing-doc-stale")
    assert entry["status"] in ("pending_review", "needs-caleb", "needs-team-update")

    verified = json.loads((TENANT.claims_dir / "verified.json").read_text())
    verified_ids = {c["id"] for c in verified}
    assert "policy-financing-doc-stale" not in verified_ids
    # No verified claim states the specific stale-policy line itself.
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


# ---------------------------------------------------------------------------
# Fix cycle 17 item 4 (model tiering): Tenant.model_for resolves
# tenant.yaml's models.<stage>, falling back to config.DEFAULT_MODELS.
# ---------------------------------------------------------------------------



class _FakeModelsTenant:
    """Just enough of the Tenant interface model_for reads."""

    def __init__(self, config):
        self._config = config

    def get(self, dotted_key, default=None):
        node = self._config
        for part in dotted_key.split("."):
            if not isinstance(node, dict) or part not in node:
                return default
            node = node[part]
        return node

    model_for = tenant_mod.Tenant.model_for


def test_model_for_falls_back_to_engine_defaults_when_tenant_yaml_has_no_models_section():
    tenant = _FakeModelsTenant({})
    for stage, default_model in harness_config.DEFAULT_MODELS.items():
        assert tenant.model_for(stage) == default_model


def test_model_for_tenant_yaml_override_wins_for_just_that_stage():
    tenant = _FakeModelsTenant({"models": {"matcher": "claude-sonnet-5"}})
    assert tenant.model_for("matcher") == "claude-sonnet-5"
    # every other stage still falls back to the engine default
    assert tenant.model_for("write") == harness_config.DEFAULT_MODELS["write"]
    assert tenant.model_for("ingest") == harness_config.DEFAULT_MODELS["ingest"]


def test_model_for_every_stage_is_independently_overridable():
    overrides = {
        "write": "claude-haiku-4-5",
        "repair_first": "claude-sonnet-5",
        "repair_next": "claude-haiku-4-5",
        "ingest": "claude-sonnet-5",
        "matcher": "claude-sonnet-5",
    }
    tenant = _FakeModelsTenant({"models": overrides})
    for stage, model in overrides.items():
        assert tenant.model_for(stage) == model


def test_peak_saunas_models_section_matches_the_engine_defaults():
    # Peak Saunas' tenant.yaml states the defaults explicitly (fix cycle 17)
    # rather than omitting the section -- this is a regression check that a
    # future default change in config.py doesn't silently change what Peak
    # Saunas actually runs without a deliberate tenant.yaml edit.
    for stage, default_model in harness_config.DEFAULT_MODELS.items():
        assert TENANT.model_for(stage) == default_model


def test_template_models_section_declares_every_stage():
    template = yaml.safe_load((tenant_mod.TEMPLATE_DIR / "tenant.yaml").read_text())
    assert set(template["models"]) == set(harness_config.DEFAULT_MODELS)
