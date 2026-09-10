"""Cycle 18: Caleb's 2026-09-10 decisions for the peak-saunas tenant --
financing lender, AI renders, five approved claims, and the crate-wording
fix. Tenant-only data checks; no engine behavior is asserted here (that
lives in tests/test_tenant.py / tests/test_claims.py)."""
import json

from harness import vocab
from tests.support import TENANT

CLAIMS_CONFIG = json.loads((TENANT.claims_dir / "config.json").read_text())
VERIFIED = json.loads((TENANT.claims_dir / "verified.json").read_text())
PENDING = json.loads((TENANT.claims_dir / "pending.json").read_text())
VERIFIED_BY_ID = {c["id"]: c for c in VERIFIED}
PENDING_IDS = {c["id"] for c in PENDING}

NEW_CLAIM_IDS = (
    "pwc-included-free",
    "pwc-leaderboard-lounge",
    "pwc-expert-protocols",
    "longevity-lab-program",
    "hsa-fsa-truemed",
    "spec-mini-dimensions",
)


# ---------------------------------------------------------------------------
# 1. Financing lender is Bread Pay.
# ---------------------------------------------------------------------------

def test_financing_lender_is_bread_pay():
    assert CLAIMS_CONFIG["financing_lender"] == "Bread Pay"
    assert TENANT.claims_config["financing_lender"] == "Bread Pay"


def test_bread_pay_removed_from_forbidden_lenders_others_still_forbidden():
    forbidden = {n.lower() for n in (TENANT.vocab.get("forbidden_lender_names") or ())}
    assert "bread pay" not in forbidden
    for still_forbidden in ("affirm", "shop pay", "klarna", "afterpay", "sezzle"):
        assert still_forbidden in forbidden


def test_policy_financing_doc_stale_pending_item_exists():
    assert "policy-financing-doc-stale" in PENDING_IDS
    entry = next(c for c in PENDING if c["id"] == "policy-financing-doc-stale")
    assert "affirm" in entry["text"].lower() or "shop pay" in entry["text"].lower()


# ---------------------------------------------------------------------------
# 2. AI renders allowed.
# ---------------------------------------------------------------------------

def test_ai_renders_allowed():
    assert CLAIMS_CONFIG["allow_ai_renders"] is True
    assert TENANT.claims_config["allow_ai_renders"] is True


# ---------------------------------------------------------------------------
# 3. The six newly approved claims: present in verified.json, gone from
#    pending.json, and every one carries a source and aliases.
# ---------------------------------------------------------------------------

def test_new_claims_are_verified_not_pending():
    for claim_id in NEW_CLAIM_IDS:
        assert claim_id in VERIFIED_BY_ID, f"{claim_id} missing from verified.json"
        assert claim_id not in PENDING_IDS, f"{claim_id} still in pending.json"


def test_new_claims_have_a_source_and_aliases():
    for claim_id in NEW_CLAIM_IDS:
        claim = VERIFIED_BY_ID[claim_id]
        assert claim.get("source"), f"{claim_id} has no source"
        assert claim.get("aliases"), f"{claim_id} has no aliases"
        assert claim.get("approved_by") == "Caleb"
        assert claim.get("date") == "2026-09-10"


def test_pwc_included_free_text_and_category():
    claim = VERIFIED_BY_ID["pwc-included-free"]
    assert claim["text"] == "Peak Wellness Club is included free with every Peak sauna."
    assert claim["category"] == "trust"


def test_hsa_fsa_truemed_text_and_category():
    claim = VERIFIED_BY_ID["hsa-fsa-truemed"]
    assert claim["text"] == "Peak saunas may be eligible for HSA/FSA purchase through TrueMed."
    assert claim["category"] == "trust"


def test_longevity_lab_program_carries_no_pricing_or_application_only_wording():
    claim = VERIFIED_BY_ID["longevity-lab-program"]
    assert claim["text"] == "Peak Longevity Lab is a post-purchase program offered to Peak owners."
    lowered = claim["text"].lower()
    assert "$" not in claim["text"]
    assert "application-only" not in lowered
    assert "application only" not in lowered


def test_spec_mini_dimensions_text():
    claim = VERIFIED_BY_ID["spec-mini-dimensions"]
    assert "31.1 in wide" in claim["text"]
    assert "31.9 in deep" in claim["text"]
    assert "66.9 in tall" in claim["text"]


def test_wellness_club_pricing_conflict_note_is_gone_from_pending():
    for entry in PENDING:
        assert entry["id"] != "gbrain-peak-wellness-club-real-offering"


# ---------------------------------------------------------------------------
# 4. Crate wording: aliases on the existing shipping/crate claim, and
#    "crate-protected" forbidden with its synonym for page copy.
# ---------------------------------------------------------------------------

def test_crate_claim_has_the_new_aliases():
    claim = VERIFIED_BY_ID["gbrain-shipping-free-crate-origin"]
    aliases = claim.get("aliases") or []
    for expected in ("crate-protected delivery", "crate protected delivery", "delivered in a crate"):
        assert expected in aliases


def test_crate_protected_is_forbidden_with_ships_in_a_crate_synonym():
    v = vocab.Vocabulary(TENANT.vocab)
    assert "crate-protected" in v.always_forbidden_terms
    assert v.hype_synonyms.get("crate-protected") == "ships in a crate"
