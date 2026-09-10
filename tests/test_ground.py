from pathlib import Path

from harness import tenant as tenant_mod
from harness.ground import LocalFactsSource, benefit_allowlist_ids, load_claims_config, select_drive_assets
from tests.support import REPO_ROOT, TENANT

FUJI_SLUG = "peak-saunas-fuji-2-person-indoor-near-zero-emf-full-spectrum-infrared-sauna-with-medical-grade-red-light-therapy"
MINI_SLUG = "peak-saunas-mini-1-person-indoor-full-spectrum-infrared-sauna-with-medical-grade-red-light-therapy"
EL_CAPITAN_SLUG = "peak-saunas-el-capitan-4-person-outdoor-full-spectrum-infrared-sauna-with-smart-wifi-app-control"


def test_specs_carry_claim_id_and_facts_pack_includes_them():
    source = LocalFactsSource(TENANT.claims_dir)
    facts_pack = source.facts_for(FUJI_SLUG, {"transcript_or_text": "", "hook": "", "promise": "", "angle": ""})

    assert facts_pack["specs"], "expected at least one spec row"
    verified_ids = {c["id"] for c in facts_pack["verified_claims"]}
    for spec in facts_pack["specs"]:
        assert spec.get("claim_id"), f"spec row missing claim_id: {spec}"
        assert spec["claim_id"] in verified_ids, f"{spec['claim_id']} not present in facts_pack.verified_claims"


def test_default_product_is_fuji_when_nothing_named():
    source = LocalFactsSource(TENANT.claims_dir)
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
    source = LocalFactsSource(TENANT.claims_dir)
    facts_pack = source.facts_for(FUJI_SLUG, {"transcript_or_text": "", "hook": "", "promise": "", "angle": ""})
    verified_ids = {c["id"] for c in facts_pack["verified_claims"]}
    assert benefit_allowlist_ids() <= verified_ids
    for cid in benefit_allowlist_ids():
        claim = next(c for c in facts_pack["verified_claims"] if c["id"] == cid)
        assert claim["source"], f"{cid} has no source"


def test_facts_pack_stays_small():
    source = LocalFactsSource(TENANT.claims_dir)
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
    # Cycle 18 set peak-saunas' own financing_lender to "Bread Pay", so
    # "default config" is exercised against tenants/_template instead --
    # a tenant that hasn't set any of these yet.
    config = load_claims_config(tenant_mod.TEMPLATE_DIR / "claims")
    assert config["financing_lender"] is None
    assert config["show_compare_at_price"] is False
    # Fix cycle 2 item 2: no speaker name is cleared for use by default --
    # the ad speaker's story is anonymous ("a customer") unless Caleb sets one.
    assert config["speaker_name"] is None


def test_facts_for_passes_speaker_name_through_from_config():
    source = LocalFactsSource(TENANT.claims_dir)
    ad_brief = {"transcript_or_text": "", "hook": "", "promise": "", "angle": ""}

    facts_pack = source.facts_for(FUJI_SLUG, ad_brief, config={"speaker_name": None})
    assert facts_pack["speaker_name"] is None

    facts_pack = source.facts_for(FUJI_SLUG, ad_brief, config={"speaker_name": "Jamie R."})
    assert facts_pack["speaker_name"] == "Jamie R."


def test_facts_for_financing_and_compare_at_respect_config():
    source = LocalFactsSource(TENANT.claims_dir)
    ad_brief = {"transcript_or_text": "", "hook": "", "promise": "", "angle": ""}

    facts_pack = source.facts_for(FUJI_SLUG, ad_brief, config={"financing_lender": None, "show_compare_at_price": False})
    assert facts_pack["product"]["financing"] == {"available": True, "lender": None, "monthly": None}
    assert facts_pack["product"]["compare_at_price"] is None

    facts_pack = source.facts_for(FUJI_SLUG, ad_brief, config={"financing_lender": "Affirm", "show_compare_at_price": True})
    assert facts_pack["product"]["financing"]["lender"] == "Affirm"
    assert facts_pack["product"]["compare_at_price"] is not None


def test_facts_for_uses_live_price_claim_over_static_one():
    source = LocalFactsSource(TENANT.claims_dir)
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
    source = LocalFactsSource(TENANT.claims_dir)
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


# ---------------------------------------------------------------------------
# Fix cycle 8 problem 1b: product picker matches real model names only, on a
# word boundary, case-insensitively; first-mentioned wins when several are
# named; falls back to the default (with a warning) when none is; ignores
# discontinued (active: false) models even if their name is literally in the
# ad.
# ---------------------------------------------------------------------------

def _ad_brief(text):
    return {"transcript_or_text": text, "hook": "", "promise": "", "angle": ""}


def test_pick_product_matches_single_named_model_case_insensitively():
    source = LocalFactsSource(TENANT.claims_dir)
    ad_brief = _ad_brief("I just ordered the Peak Sauna Mini and I could not be more excited.")
    product, warning = source.pick_product_with_warning(None, ad_brief)
    assert product["slug"] == MINI_SLUG
    assert warning is None


def test_pick_product_word_boundary_rejects_alias_only_mentions():
    source = LocalFactsSource(TENANT.claims_dir)
    # "el cap", "1-person", "2-person", "sauna mini" alone are not model
    # names -- only the product's own `name` field counts.
    ad_brief = _ad_brief("This is a 1-person sauna, great for el cap weekend trips.")
    product, warning = source.pick_product_with_warning(None, ad_brief)
    assert product["slug"] == FUJI_SLUG  # default, nothing actually named
    assert warning == "product not named in ad; defaulted to Fuji"


def test_pick_product_full_alias_phrase_still_matches_the_real_name_inside_it():
    source = LocalFactsSource(TENANT.claims_dir)
    # "sauna mini" contains the real model word "mini" as a whole word --
    # that's a legitimate match, not a false alias hit.
    ad_brief = _ad_brief("Ask about our sauna mini today.")
    product, warning = source.pick_product_with_warning(None, ad_brief)
    assert product["slug"] == MINI_SLUG
    assert warning is None


def test_pick_product_picks_first_mentioned_when_several_named():
    source = LocalFactsSource(TENANT.claims_dir)
    ad_brief = _ad_brief("Compare the El Capitan against the Fuji -- both are great.")
    product, warning = source.pick_product_with_warning(None, ad_brief)
    assert product["slug"] == EL_CAPITAN_SLUG
    assert warning is None


def test_pick_product_defaults_and_warns_when_no_model_named():
    source = LocalFactsSource(TENANT.claims_dir)
    ad_brief = _ad_brief("I love my new infrared sauna, it's changed my life.")
    product, warning = source.pick_product_with_warning(None, ad_brief)
    assert product.get("default") is True
    assert warning == "product not named in ad; defaulted to Fuji"


def test_pick_product_ignores_discontinued_model_even_if_named():
    source = LocalFactsSource(TENANT.claims_dir)
    ad_brief = _ad_brief("I've heard great things about the Peak Crown.")
    product, warning = source.pick_product_with_warning(None, ad_brief)
    assert product["name"] != "Crown"
    assert product.get("default") is True
    assert warning == "product not named in ad; defaulted to Fuji"


def test_pick_product_explicit_product_slug_wins_and_never_warns():
    source = LocalFactsSource(TENANT.claims_dir)
    ad_brief = _ad_brief("no model named here at all")
    product, warning = source.pick_product_with_warning("mini", ad_brief)
    assert product["slug"] == MINI_SLUG
    assert warning is None


def test_pick_product_backward_compatible_wrapper_returns_product_only():
    source = LocalFactsSource(TENANT.claims_dir)
    ad_brief = _ad_brief("I just ordered the Peak Sauna Mini.")
    product = source.pick_product(None, ad_brief)
    assert product["slug"] == MINI_SLUG


# ---------------------------------------------------------------------------
# Fix cycle 8 problem 2: every active model's g Brain-sourced spec claims
# (spec-<model>-*, and the older gbrain-<model>-* Fuji/Everest set) must
# reach facts_pack.verified_claims, not just the handful of fields Shopify's
# products.json specs array already carried.
# ---------------------------------------------------------------------------

def test_facts_pack_includes_gbrain_spec_claims_for_mini():
    source = LocalFactsSource(TENANT.claims_dir)
    facts_pack = source.facts_for(MINI_SLUG, _ad_brief(""))
    verified_ids = {c["id"] for c in facts_pack["verified_claims"]}
    assert "spec-mini-electrical" in verified_ids
    assert "spec-mini-red-light" in verified_ids


def test_facts_pack_includes_gbrain_dimensions_claims_for_fuji_and_everest():
    source = LocalFactsSource(TENANT.claims_dir)
    fuji_pack = source.facts_for(FUJI_SLUG, _ad_brief(""))
    fuji_ids = {c["id"] for c in fuji_pack["verified_claims"]}
    assert "gbrain-fuji-dimensions" in fuji_ids
    assert "gbrain-fuji-power" in fuji_ids

    everest_slug = "peak-saunas-everest-2-person-indoor-near-zero-emf-full-spectrum-infrared-sauna-with-medical-grade-red-light-therapy"
    everest_pack = source.facts_for(everest_slug, _ad_brief(""))
    everest_ids = {c["id"] for c in everest_pack["verified_claims"]}
    assert "gbrain-everest-dimensions" in everest_ids


def test_facts_pack_never_includes_an_emf_claim():
    source = LocalFactsSource(TENANT.claims_dir)
    for slug in (MINI_SLUG, FUJI_SLUG):
        facts_pack = source.facts_for(slug, _ad_brief(""))
        for c in facts_pack["verified_claims"]:
            assert "emf" not in c["text"].lower(), c


# ---------------------------------------------------------------------------
# Fix cycle 9 item 1: PDP claims (pdp_claims.seed_pdp_claims) are in-memory
# only -- never in claims/verified.json -- and must reach facts_pack when
# passed in, scoped to the run's own product.
# ---------------------------------------------------------------------------

def test_facts_for_merges_pdp_claims_for_the_chosen_product_only():
    source = LocalFactsSource(TENANT.claims_dir)
    pdp_claims = [
        {"id": "pdp-mini-app-control", "text": "Runs from the Peak Saunas app.", "category": "spec", "source": "https://peaksaunas.com/products/mini", "approved_by": "site", "date": "2026-09-09"},
        {"id": "pdp-fuji-app-control", "text": "Some other product's claim.", "category": "spec", "source": "https://peaksaunas.com/products/fuji", "approved_by": "site", "date": "2026-09-09"},
    ]
    facts_pack = source.facts_for(MINI_SLUG, _ad_brief(""), pdp_claims=pdp_claims)
    verified_ids = {c["id"] for c in facts_pack["verified_claims"]}
    assert "pdp-mini-app-control" in verified_ids
    assert "pdp-fuji-app-control" not in verified_ids  # a different product's PDP claim


def test_facts_for_with_no_pdp_claims_is_unaffected():
    source = LocalFactsSource(TENANT.claims_dir)
    facts_pack = source.facts_for(MINI_SLUG, _ad_brief(""))
    assert not any(c["id"].startswith("pdp-") for c in facts_pack["verified_claims"])


def test_all_verified_claims_includes_extra_claims():
    source = LocalFactsSource(TENANT.claims_dir)
    extra = [{"id": "pdp-mini-app-control", "text": "Runs from the Peak Saunas app.", "category": "spec", "source": "x", "approved_by": "site", "date": "2026-09-09"}]
    claims = source.all_verified_claims(extra_claims=extra)
    assert any(c["id"] == "pdp-mini-app-control" for c in claims)


def test_all_verified_claims_with_no_extra_claims_is_unaffected():
    source = LocalFactsSource(TENANT.claims_dir)
    claims = source.all_verified_claims()
    assert not any(c["id"].startswith("pdp-") for c in claims)


# ---------------------------------------------------------------------------
# Fix cycle 9 item 2: product inference by price -- if the ad brief quotes a
# dollar amount matching exactly one active product's current price (within
# $1) and no model is named, pick that product instead of falling through to
# the default.
# ---------------------------------------------------------------------------

PRICE_COMPARISON_TRANSCRIPT = (
    "I keep hearing all the benefits of infrared saunas, so I really want to get into it. "
    "I've been seeing that the average unlimited sauna membership is around $200 a month. "
    "So say $2,400 a year. infrared sauna is on sale right now for $5,450. "
    "I think I'm going to buy the Peak sauna."
)


def test_pick_product_infers_from_quoted_price_when_no_model_named():
    source = LocalFactsSource(TENANT.claims_dir)
    ad_brief = {
        "transcript_or_text": PRICE_COMPARISON_TRANSCRIPT,
        "hook": "",
        "promise": "",
        "angle": "",
        "claims_made": ["infrared sauna is on sale right now for $5,450"],
    }
    product, warning = source.pick_product_with_warning(None, ad_brief)
    assert product["slug"] == MINI_SLUG
    assert warning == "product inferred from quoted price $5,450 = Mini"


def test_pick_product_price_inference_ignores_amounts_that_match_no_product():
    source = LocalFactsSource(TENANT.claims_dir)
    ad_brief = {
        "transcript_or_text": "",
        "hook": "",
        "promise": "",
        "angle": "",
        "claims_made": ["memberships run about $200 a month, or $2,400 a year"],
    }
    product, warning = source.pick_product_with_warning(None, ad_brief)
    # neither $200 nor $2,400 is within $1 of any active product's price --
    # falls through to the ordinary default, unaffected by this fix.
    assert product.get("default") is True
    assert warning == "product not named in ad; defaulted to Fuji"


def test_pick_product_named_model_still_wins_over_a_quoted_price():
    source = LocalFactsSource(TENANT.claims_dir)
    ad_brief = {
        "transcript_or_text": "I love my Peak Fuji, on sale right now for $5,450.",
        "hook": "",
        "promise": "",
        "angle": "",
        "claims_made": ["on sale right now for $5,450"],
    }
    product, warning = source.pick_product_with_warning(None, ad_brief)
    # Fuji is named explicitly -- wins outright even though $5,450 happens to
    # be the Mini's price, because a named model is checked first.
    assert product["slug"] == FUJI_SLUG
    assert warning is None


# ---------------------------------------------------------------------------
# Fix cycle 15 item 2: brand/assets-listicle-pack.json wiring for Mini/
# Matterhorn -- real photos preferred, an ai_generated:true row only ever
# selected when claims/config.json's allow_ai_renders is true (default
# False).
# ---------------------------------------------------------------------------

from harness.ground import select_listicle_pack_assets

MATTERHORN_SLUG = "peak-saunas-matterhorn-3-person-full-spectrum-infrared-sauna-with-two-xl-medical-grade-red-light-therapy-smart-wifi-app-control"

FAKE_LISTICLE_PACK_INDEX = {
    "download_url_pattern": "https://drive.google.com/uc?export=download&id={id}",
    "assets": [
        {"id": "p1", "title": "MINI__MG_1225.JPG", "model": "mini", "kind": "photo_product", "ai_generated": False},
        {"id": "p2", "title": "MINI-install_09.jpg", "model": "mini", "kind": "photo_install", "ai_generated": False},
        {"id": "s1", "title": "Stills_Office Views 001.jpg", "model": None, "kind": "still_video", "ai_generated": False},
        {"id": "a1", "title": "AIrender_mini.png", "model": "mini", "kind": "ai_render", "ai_generated": True},
        {"id": "x1", "title": "excluded", "model": "mini", "kind": "photo_product", "ai_generated": False, "excluded": True},
        {"id": "m1", "title": "Matterhorn__MG_3224.JPG", "model": "matterhorn", "kind": "photo_product", "ai_generated": False},
    ],
}


def test_select_listicle_pack_assets_prefers_real_photos_over_ai_render():
    selected = select_listicle_pack_assets(FAKE_LISTICLE_PACK_INDEX, "mini", allow_ai_renders=True)
    kinds = [a["kind"] for a in selected]
    # real photos (photo_product/photo_install/still_video) sort before the
    # ai_render, even though allow_ai_renders is true here.
    assert kinds.index("ai_render") > max(
        i for i, k in enumerate(kinds) if k in ("photo_product", "photo_install", "still_video")
    )


def test_select_listicle_pack_assets_never_selects_ai_generated_by_default():
    selected = select_listicle_pack_assets(FAKE_LISTICLE_PACK_INDEX, "mini", allow_ai_renders=False)
    assert all(not a["ai_generated"] for a in selected)
    assert not any(a["drive_id"] == "a1" for a in selected)


def test_select_listicle_pack_assets_includes_ai_generated_when_allowed():
    selected = select_listicle_pack_assets(FAKE_LISTICLE_PACK_INDEX, "mini", allow_ai_renders=True)
    ai_ids = {a["drive_id"] for a in selected if a["ai_generated"]}
    assert "a1" in ai_ids


def test_select_listicle_pack_assets_excludes_flagged_and_other_model():
    selected = select_listicle_pack_assets(FAKE_LISTICLE_PACK_INDEX, "mini", allow_ai_renders=True)
    ids = {a["drive_id"] for a in selected}
    assert "x1" not in ids  # excluded
    assert "m1" not in ids  # matterhorn, not mini
    assert "s1" in ids  # still_video has no model, eligible for either


def test_select_listicle_pack_assets_ids_and_url_pattern():
    selected = select_listicle_pack_assets(FAKE_LISTICLE_PACK_INDEX, "mini", allow_ai_renders=False)
    photo = next(a for a in selected if a["kind"] == "photo_product")
    assert photo["id"] == "asset-listicle-p1"
    assert photo["url"] == "https://drive.google.com/uc?export=download&id=p1"


def test_default_config_allow_ai_renders_is_false():
    # Cycle 18 turned peak-saunas' own allow_ai_renders on, so "default
    # config" is exercised against tenants/_template instead (see above).
    config = load_claims_config(tenant_mod.TEMPLATE_DIR / "claims")
    assert config["allow_ai_renders"] is False


def test_facts_for_wires_in_listicle_pack_assets_for_mini(monkeypatch):
    source = LocalFactsSource(TENANT.claims_dir)
    monkeypatch.setattr(source, "_load_listicle_pack_index", lambda: FAKE_LISTICLE_PACK_INDEX)
    ad_brief = {"transcript_or_text": "", "hook": "", "promise": "", "angle": ""}
    facts_pack = source.facts_for(MINI_SLUG, ad_brief, config={"allow_ai_renders": False})
    listicle_asset_ids = {a["drive_id"] for a in facts_pack["assets"] if a.get("id", "").startswith("asset-listicle-")}
    assert "p1" in listicle_asset_ids
    assert "a1" not in listicle_asset_ids  # ai_generated, not allowed by default


def test_facts_for_wires_in_listicle_pack_assets_for_matterhorn(monkeypatch):
    source = LocalFactsSource(TENANT.claims_dir)
    monkeypatch.setattr(source, "_load_listicle_pack_index", lambda: FAKE_LISTICLE_PACK_INDEX)
    ad_brief = {"transcript_or_text": "", "hook": "", "promise": "", "angle": ""}
    facts_pack = source.facts_for(MATTERHORN_SLUG, ad_brief, config={"allow_ai_renders": False})
    listicle_asset_ids = {a["drive_id"] for a in facts_pack["assets"] if a.get("id", "").startswith("asset-listicle-")}
    assert "m1" in listicle_asset_ids


def test_facts_for_does_not_wire_in_listicle_pack_assets_for_other_products(monkeypatch):
    source = LocalFactsSource(TENANT.claims_dir)
    monkeypatch.setattr(source, "_load_listicle_pack_index", lambda: FAKE_LISTICLE_PACK_INDEX)
    ad_brief = {"transcript_or_text": "", "hook": "", "promise": "", "angle": ""}
    facts_pack = source.facts_for(FUJI_SLUG, ad_brief, config={"allow_ai_renders": True})
    listicle_asset_ids = [a for a in facts_pack["assets"] if a.get("id", "").startswith("asset-listicle-")]
    assert listicle_asset_ids == []


def test_facts_for_allows_ai_generated_listicle_asset_when_config_set(monkeypatch):
    source = LocalFactsSource(TENANT.claims_dir)
    monkeypatch.setattr(source, "_load_listicle_pack_index", lambda: FAKE_LISTICLE_PACK_INDEX)
    ad_brief = {"transcript_or_text": "", "hook": "", "promise": "", "angle": ""}
    facts_pack = source.facts_for(MINI_SLUG, ad_brief, config={"allow_ai_renders": True})
    listicle_asset_ids = {a["drive_id"] for a in facts_pack["assets"] if a.get("id", "").startswith("asset-listicle-")}
    assert "a1" in listicle_asset_ids
