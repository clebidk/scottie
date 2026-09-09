from pathlib import Path

from adv.ground import BENEFIT_ALLOWLIST_IDS, LocalFactsSource, load_claims_config, select_drive_assets

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


# ---------------------------------------------------------------------------
# fix cycle 3 item 3: the cleared allowlist benefit claims (medical-grade red
# light therapy, full-spectrum infrared, US-owned, free shipping, limited
# lifetime warranty) must reach facts_pack -- they were already merged into
# claims/verified.json with sources, but facts_for()'s universal_ids never
# surfaced them, so the writer had nothing to cite for what the sauna does.
# ---------------------------------------------------------------------------

def test_facts_pack_includes_benefit_allowlist_claims():
    source = LocalFactsSource(REPO_ROOT / "claims")
    facts_pack = source.facts_for(FUJI_SLUG, {"transcript_or_text": "", "hook": "", "promise": "", "angle": ""})
    verified_ids = {c["id"] for c in facts_pack["verified_claims"]}
    assert BENEFIT_ALLOWLIST_IDS <= verified_ids
    for cid in BENEFIT_ALLOWLIST_IDS:
        claim = next(c for c in facts_pack["verified_claims"] if c["id"] == cid)
        assert claim["source"], f"{cid} has no source"


def test_facts_pack_stays_small():
    source = LocalFactsSource(REPO_ROOT / "claims")
    facts_pack = source.facts_for(FUJI_SLUG, {"transcript_or_text": "", "hook": "", "promise": "", "angle": ""})
    import json

    # rough proxy for "~4k tokens": chars / 4
    approx_tokens = len(json.dumps(facts_pack)) / 4
    assert approx_tokens < 4000, f"facts_pack too large: ~{approx_tokens:.0f} tokens"


# ---------------------------------------------------------------------------
# fix 1: financing never carries a lender/monthly figure unless configured;
# compare-at price is only offered when show_compare_at_price is true.
# ---------------------------------------------------------------------------

def test_default_config_has_no_lender_and_hides_compare_at():
    config = load_claims_config(REPO_ROOT / "claims")
    assert config["financing_lender"] is None
    assert config["show_compare_at_price"] is False
    # Fix cycle 2 item 2: no speaker name is cleared for use by default --
    # the ad speaker's story is anonymous ("a customer") unless Caleb sets one.
    assert config["speaker_name"] is None


def test_facts_for_passes_speaker_name_through_from_config():
    source = LocalFactsSource(REPO_ROOT / "claims")
    ad_brief = {"transcript_or_text": "", "hook": "", "promise": "", "angle": ""}

    facts_pack = source.facts_for(FUJI_SLUG, ad_brief, config={"speaker_name": None})
    assert facts_pack["speaker_name"] is None

    facts_pack = source.facts_for(FUJI_SLUG, ad_brief, config={"speaker_name": "Jamie R."})
    assert facts_pack["speaker_name"] == "Jamie R."


def test_facts_for_financing_and_compare_at_respect_config():
    source = LocalFactsSource(REPO_ROOT / "claims")
    ad_brief = {"transcript_or_text": "", "hook": "", "promise": "", "angle": ""}

    facts_pack = source.facts_for(FUJI_SLUG, ad_brief, config={"financing_lender": None, "show_compare_at_price": False})
    assert facts_pack["product"]["financing"] == {"available": True, "lender": None, "monthly": None}
    assert facts_pack["product"]["compare_at_price"] is None

    facts_pack = source.facts_for(FUJI_SLUG, ad_brief, config={"financing_lender": "Affirm", "show_compare_at_price": True})
    assert facts_pack["product"]["financing"]["lender"] == "Affirm"
    assert facts_pack["product"]["compare_at_price"] is not None


def test_facts_for_uses_live_price_claim_over_static_one():
    source = LocalFactsSource(REPO_ROOT / "claims")
    ad_brief = {"transcript_or_text": "", "hook": "", "promise": "", "angle": ""}
    live_claim = {
        "id": "price-fuji",
        "text": "The Peak Saunas Fuji is priced at $7999.99.",
        "category": "price",
        "source": "https://peaksaunas.com/products/fuji-live",
    }
    facts_pack = source.facts_for(FUJI_SLUG, ad_brief, live_price_claim=live_claim)
    price_claim = next(c for c in facts_pack["verified_claims"] if c["id"] == "price-fuji")
    assert price_claim["text"] == "The Peak Saunas Fuji is priced at $7999.99."
    assert price_claim["source"] == "https://peaksaunas.com/products/fuji-live"


# ---------------------------------------------------------------------------
# fix 8: Drive asset selection -- lifestyle/interior, then render, then
# installation; never video/logo/ugc; up to 6, excluded assets skipped.
# ---------------------------------------------------------------------------

FAKE_ASSETS_INDEX = {
    "download_url_pattern": "https://drive.google.com/uc?export=download&id={id}",
    "assets": [
        {"id": "v1", "title": "video", "model": "fuji", "kind": "video"},
        {"id": "l1", "title": "logo", "model": "fuji", "kind": "logo"},
        {"id": "u1", "title": "ugc", "model": "fuji", "kind": "ugc"},
        {"id": "i1", "title": "installation 1", "model": "fuji", "kind": "installation"},
        {"id": "r1", "title": "render 1", "model": "fuji", "kind": "render"},
        {"id": "r2", "title": "render 2", "model": "fuji", "kind": "render"},
        {"id": "s1", "title": "lifestyle 1", "model": "fuji", "kind": "lifestyle"},
        {"id": "x1", "title": "excluded lifestyle", "model": "fuji", "kind": "lifestyle", "excluded": True},
        {"id": "o1", "title": "other model", "model": "everest", "kind": "lifestyle"},
    ],
}


def test_select_drive_assets_prefers_lifestyle_then_render_then_installation():
    selected = select_drive_assets(FAKE_ASSETS_INDEX, "fuji")
    kinds = [a["kind"] for a in selected]
    assert kinds == ["lifestyle", "render", "render", "installation"]


def test_select_drive_assets_never_returns_video_logo_ugc_or_excluded_or_other_model():
    selected = select_drive_assets(FAKE_ASSETS_INDEX, "fuji")
    ids = {a["drive_id"] for a in selected}
    assert ids == {"s1", "r1", "r2", "i1"}
    assert "v1" not in ids and "l1" not in ids and "u1" not in ids and "x1" not in ids and "o1" not in ids


def test_select_drive_assets_caps_at_six():
    many = {
        "download_url_pattern": FAKE_ASSETS_INDEX["download_url_pattern"],
        "assets": [{"id": f"r{i}", "model": "fuji", "kind": "render"} for i in range(10)],
    }
    selected = select_drive_assets(many, "fuji")
    assert len(selected) == 6


def test_select_drive_assets_ids_and_url_pattern():
    selected = select_drive_assets(FAKE_ASSETS_INDEX, "fuji")
    lifestyle = next(a for a in selected if a["kind"] == "lifestyle")
    assert lifestyle["id"] == "asset-drive-s1"
    assert lifestyle["url"] == "https://drive.google.com/uc?export=download&id=s1"


def test_facts_for_merges_shopify_images_first_then_drive_assets(monkeypatch):
    source = LocalFactsSource(REPO_ROOT / "claims")
    monkeypatch.setattr(source, "_load_assets_index", lambda: FAKE_ASSETS_INDEX)
    ad_brief = {"transcript_or_text": "", "hook": "", "promise": "", "angle": ""}
    facts_pack = source.facts_for(FUJI_SLUG, ad_brief)
    assets = facts_pack["assets"]
    shopify_count = sum(1 for a in assets if a["kind"] == "image")
    assert shopify_count > 0
    assert all(a["kind"] == "image" for a in assets[:shopify_count])
    drive_kinds = [a["kind"] for a in assets[shopify_count:]]
    assert set(drive_kinds) <= {"lifestyle", "interior", "render", "installation"}
    assert "video" not in drive_kinds and "logo" not in drive_kinds and "ugc" not in drive_kinds
