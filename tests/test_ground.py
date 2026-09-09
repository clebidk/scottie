from pathlib import Path

from adv.ground import LocalFactsSource

REPO_ROOT = Path(__file__).resolve().parent.parent
FUJI_SLUG = "peak-saunas-fuji-2-person-indoor-near-zero-emf-full-spectrum-infrared-sauna-with-medical-grade-red-light-therapy"


def test_specs_carry_claim_id_and_facts_pack_includes_them():
    source = LocalFactsSource(REPO_ROOT / "claims")
    facts_pack = source.facts_for(FUJI_SLUG, {"transcript_or_text": "", "hook": "", "promise": "", "angle": ""})

    assert facts_pack["specs"], "expected at least one spec row"
    verified_ids = {c["id"] for c in facts_pack["verified_claims"]}
    for spec in facts_pack["specs"]:
        assert spec.get("claim_id"), f"spec row missing claim_id: {spec}"
        assert spec["claim_id"] in verified_ids, f"{spec['claim_id']} not present in facts_pack.verified_claims"


def test_default_product_is_fuji_when_nothing_named():
    source = LocalFactsSource(REPO_ROOT / "claims")
    facts_pack = source.facts_for(None, {"transcript_or_text": "just a generic ad about saunas", "hook": "", "promise": "", "angle": ""})
    assert facts_pack["product"]["slug"] == FUJI_SLUG


def test_facts_pack_stays_small():
    source = LocalFactsSource(REPO_ROOT / "claims")
    facts_pack = source.facts_for(FUJI_SLUG, {"transcript_or_text": "", "hook": "", "promise": "", "angle": ""})
    import json

    # rough proxy for "~4k tokens": chars / 4
    approx_tokens = len(json.dumps(facts_pack)) / 4
    assert approx_tokens < 4000, f"facts_pack too large: ~{approx_tokens:.0f} tokens"
