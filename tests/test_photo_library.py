"""Cycle 75: the tenant photo library (harness/photo_library.py) -- schema,
import, the product decision, selection, topic matching, the wrong-product
gate and the unknown-asset-id fix. Fixture tags only; no network."""
import io
import json

import pytest
from PIL import Image

from harness import photo_library as pl
from harness.textutil import product_name_slug
from tests.support import TENANT

FEATURES = list(pl.DEFAULT_FEATURES)
MODELS = ["everest", "fuji", "patagonia"]
KEYWORDS = {
    "red-light-panel": ["red light", "led"],
    "outlet-plug": ["outlet", "plug", "plugs in", "120v"],
    "size-in-room": ["space", "fits", "apartment"],
    "speakers": ["bluetooth", "music"],
    "roof-outdoor-weather": ["outdoor", "backyard", "snow"],
    "setting:outdoor": ["outdoor", "backyard"],
}
PRODUCTS = {
    "acme-fuji-2-person-indoor-sauna-with-red-light-therapy": {
        "name": "Fuji", "specs": [{"label": "Capacity", "value": "2-Person"}], "image_urls": ["u1", "u2"]},
    "acme-everest-2-person-indoor-sauna-with-red-light-therapy": {
        "name": "Everest", "specs": [{"label": "Capacity", "value": "2-Person"}], "image_urls": ["u1"]},
    "acme-patagonia-2-person-outdoor-sauna": {
        "name": "Patagonia", "specs": [{"label": "Placement", "value": "Outdoor"}], "image_urls": ["u1"]},
}
FUJI_HANDLE = "acme-fuji-2-person-indoor-sauna-with-red-light-therapy"


class FakeTenant:
    def __init__(self, root):
        self.root = root
        self.name = root.name
        self.brand_dir = root / "brand"
        self.claims_dir = root / "claims"
        self.runs_dir = root / "runs"
        self.brand_dir.mkdir(parents=True)
        self.claims_dir.mkdir(parents=True)
        (self.claims_dir / "products.json").write_text(json.dumps({"products": PRODUCTS}))
        (self.claims_dir / "verified.json").write_text("[]")
        self.config = {"photo_library": {"features": FEATURES, "topic_keywords": KEYWORDS}}

    def get(self, key, default=None):
        node = self.config
        for part in key.split("."):
            if not isinstance(node, dict) or part not in node:
                return default
            node = node[part]
        return node


def _tags(product="fuji", shot="lifestyle", setting="indoor", features=(), agnostic=False, **kw):
    t = {
        "product": product, "product_confidence": 0.9 if product != "unknown" else 0.8,
        "model_agnostic": agnostic, "setting": setting, "shot": shot,
        "features_visible": list(features), "has_text_overlay": False, "people": 0,
        "description": "A sauna.", "blurry": False, "cluttered": False,
    }
    t.update(kw)
    return t


def _photo(n, tags, *, ai=False):
    pid = f"asset-photo-{n:012x}"
    return {"id": pid, "file": f"{pid}.jpg", "source_name": f"{n}.jpg", "drive_id": None, "sha256": "0" * 64,
            "width": 1600, "height": 1200, "ai_generated": ai, "excluded": False, "tags": tags, "decision": None}


@pytest.fixture
def tenant(tmp_path, monkeypatch):
    monkeypatch.setenv(pl.FILES_DIR_ENV, str(tmp_path / "files"))
    return FakeTenant(tmp_path / "acme")


def _library(tenant, photos):
    pl.save_manifest(tenant, {"version": 1, "photos": photos})
    files = pl.files_dir(tenant)
    files.mkdir(parents=True, exist_ok=True)
    for p in photos:
        (files / p["file"]).write_bytes(b"x")
    return {p["id"]: p for p in photos}


FUJI_EXTERIOR = _photo(1, _tags(shot="exterior", setting="studio", features=["glass-door", "wood-grain"]))
FUJI_RED = _photo(2, _tags(shot="interior", features=["red-light-panel", "bench"]))
FUJI_ROOM = _photo(3, _tags(shot="lifestyle", features=["size-in-room"]))
FUJI_PLUG = _photo(4, _tags(shot="detail", features=["outlet-plug"]))
FUJI_PERSON = _photo(5, _tags(shot="people-in-use", people=1, features=["bench"]))
EVEREST_RED = _photo(6, _tags(product="everest", shot="interior", features=["red-light-panel"]))
AGNOSTIC_SPEAKER = _photo(7, _tags(product="unknown", shot="detail", features=["speakers"], agnostic=True))
UNKNOWN_CABIN = _photo(8, _tags(product="unknown", shot="exterior", features=["red-light-panel"]))
FUJI_AD_STILL = _photo(9, _tags(shot="ad-still-with-text", has_text_overlay=True, features=["red-light-panel"]))
AGNOSTIC_ROOF = _photo(10, _tags(product="unknown", shot="detail", setting="outdoor",
                                  features=["roof-outdoor-weather"], agnostic=True))
ALL = [FUJI_EXTERIOR, FUJI_RED, FUJI_ROOM, FUJI_PLUG, FUJI_PERSON, EVEREST_RED, AGNOSTIC_SPEAKER,
       UNKNOWN_CABIN, FUJI_AD_STILL, AGNOSTIC_ROOF]


def _facts_pack(tenant, extra_storefront=2):
    lib = pl.facts_pack_assets(tenant, "fuji", allow_ai_renders=True)
    store = [{"id": f"asset-{FUJI_HANDLE}-{i}", "url": f"u{i}", "kind": "image"} for i in range(1, extra_storefront + 1)]
    return {"product": {"name": "Fuji", "slug": FUJI_HANDLE}, "assets": lib + store}


# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------

def test_manifest_schema_accepts_valid_tags_and_untagged_photos():
    data = {"version": 1, "photos": [FUJI_EXTERIOR, _photo(99, None)]}
    assert pl.validate_manifest(data, features=FEATURES, models=MODELS) == []


@pytest.mark.parametrize("change, expect", [
    ({"product": "matterhorn"}, "product"),
    ({"shot": "aerial"}, "shot"),
    ({"setting": "space"}, "setting"),
    ({"features_visible": ["wifi-router"]}, "features_visible"),
    ({"model_agnostic": True}, "model_agnostic"),          # agnostic but tagged fuji
    ({"product_confidence": 1.5}, "product_confidence"),
    ({"people": -1}, "people"),
    ({"description": ""}, "description"),
    ({"blurry": "no"}, "blurry"),
    ({"old_logo_visible": "yes"}, "old_logo_visible"),
])
def test_manifest_schema_rejects_bad_tags(change, expect):
    photo = dict(FUJI_EXTERIOR, tags=dict(FUJI_EXTERIOR["tags"], **change))
    errors = pl.validate_manifest({"version": 1, "photos": [photo]}, features=FEATURES, models=MODELS)
    assert errors and expect in " ".join(errors)


def test_manifest_schema_rejects_duplicate_and_malformed_ids():
    bad = dict(FUJI_RED, id="asset-photo-XYZ", file="asset-photo-XYZ.jpg")
    errors = pl.validate_manifest({"version": 1, "photos": [FUJI_EXTERIOR, FUJI_EXTERIOR, bad]},
                                  features=FEATURES, models=MODELS)
    assert any("duplicate" in e for e in errors)
    assert any("bad id" in e for e in errors)


def test_committed_manifest_validates_against_the_tenant_config():
    data = json.loads(pl.manifest_path(TENANT).read_text())
    assert pl.validate_manifest(data, features=pl.features_for(TENANT), models=pl.model_slugs(TENANT)) == []
    text = json.dumps([[p.get("tags"), p.get("decision")] for p in data["photos"]]).lower()
    assert "emf" not in text  # never in tags, descriptions or decision notes


def test_invalid_manifest_is_ignored_not_fatal(tenant):
    pl.manifest_path(tenant).write_text(json.dumps({"version": 1, "photos": [{"id": "nope"}]}))
    assert pl.load_manifest(tenant)["photos"] == []


# ---------------------------------------------------------------------------
# Import
# ---------------------------------------------------------------------------

def _jpeg_with_exif(size, color):
    im = Image.new("RGB", size, color)
    exif = Image.Exif()
    exif[0x010F] = "CameraMaker"  # Make
    exif[0x0112] = 6               # Orientation: rotate 90
    buf = io.BytesIO()
    im.save(buf, format="JPEG", exif=exif)
    return buf.getvalue()


def test_import_makes_web_derivatives_with_stable_ids(tenant, tmp_path):
    src = tmp_path / "inbox"
    src.mkdir()
    big = _jpeg_with_exif((4000, 3000), "red")
    (src / "asset-drive-1AbCdEfGhIjKlMnOpQrStUvWxYz012345___MG_1.JPG").write_bytes(big)
    (src / "copy of the same.jpg").write_bytes(big)
    (src / "Firefly_Gemini Flash_a sauna.png").write_bytes(_png((300, 200)))
    (src / "notes.txt").write_text("not an image")

    counts = pl.import_folder(tenant, src, log=lambda *_: None)

    assert counts == {"added": 2, "existing": 0, "duplicates": 1, "skipped": 0}
    photos = {p["source_name"]: p for p in pl.load_manifest(tenant)["photos"]}
    cam = photos["asset-drive-1AbCdEfGhIjKlMnOpQrStUvWxYz012345___MG_1.JPG"]
    assert "copy of the same.jpg" not in photos  # byte-identical: one id
    assert cam["id"] == pl.photo_id(big)
    assert cam["drive_id"] == "1AbCdEfGhIjKlMnOpQrStUvWxYz012345"
    with Image.open(pl.local_file(tenant, cam)) as im:
        assert max(im.size) == pl.DERIVATIVE_MAX_EDGE
        assert im.size == (1200, 1600)  # EXIF orientation applied
        assert not im.getexif()
        assert "exif" not in im.info
    ai = photos["Firefly_Gemini Flash_a sauna.png"]
    assert ai["ai_generated"] is True and ai["tags"] is None
    assert cam["ai_generated"] is False
    # re-import: same ids, tags kept
    assert pl.import_folder(tenant, src, log=lambda *_: None)["existing"] == 2


def test_drive_id_parse_keeps_a_triple_underscore_out_of_the_id():
    m = pl._DRIVE_ID_RE.match("asset-drive-1QVjuYpxyYGj9WnelYH1936I7H2LMJ-AB___MG_4234.JPG")
    assert m.group(1) == "1QVjuYpxyYGj9WnelYH1936I7H2LMJ-AB"


def _png(size):
    buf = io.BytesIO()
    Image.new("RGBA", size, (0, 0, 255, 128)).save(buf, format="PNG")
    return buf.getvalue()


# ---------------------------------------------------------------------------
# Vision response + the product decision
# ---------------------------------------------------------------------------

def test_parse_tag_response_never_invents_a_model_and_filters_features():
    text = json.dumps({
        "product": "matterhorn-xl", "product_confidence": 0.9, "product_reason": "r", "candidates": ["fuji", "zzz"],
        "model_agnostic": False, "setting": "indoor", "shot": "assembly", "people": 2,
        "features_visible": ["bench", "jacuzzi"], "has_text_overlay": False,
        "description": "Two people build a zap cabin.", "blurry": False, "cluttered": False,
    })
    tags, vision = pl.parse_tag_response(text, features=FEATURES, models=MODELS, forbidden_terms=["zap"])
    assert tags["product"] == "unknown"
    assert tags["shot"] == "people-in-use" and "assembly" in tags["features_visible"]
    assert "jacuzzi" not in tags["features_visible"]
    assert "zap" not in tags["description"]
    assert vision["candidates"] == ["fuji"]


def test_parse_tag_response_rejects_a_bad_shot():
    text = json.dumps({"product": "fuji", "product_confidence": 0.9, "setting": "indoor", "shot": "aerial",
                       "description": "x"})
    with pytest.raises(pl.TagResponseInvalid):
        pl.parse_tag_response(text, features=FEATURES, models=MODELS)


@pytest.mark.parametrize("vision, folder, verify, expect", [
    ({"product": "fuji", "confidence": 0.6}, "fuji", None, "fuji"),            # agree
    ({"product": "fuji", "confidence": 0.4}, "fuji", None, "unknown"),         # agree, too unsure
    ({"product": "fuji", "confidence": 0.75}, None, None, "fuji"),             # vision only
    ({"product": "fuji", "confidence": 0.65}, None, None, "unknown"),          # vision only, unsure
    ({"product": "fuji", "confidence": 0.6}, "everest", None, "unknown"),      # conflict
    ({"product": "fuji", "confidence": 0.6}, "everest",
     {"verdict": "match", "confidence": 0.8}, "everest"),                      # folder confirmed
    ({"product": "fuji", "confidence": 0.85}, "everest",
     {"verdict": "match", "confidence": 0.8}, "unknown"),                      # pass 1 too sure to overrule
    ({"product": "unknown", "confidence": 0.7, "candidates": ["everest", "fuji"]}, "everest", None, "everest"),
    ({"product": "unknown", "confidence": 0.7, "candidates": MODELS + ["a", "b"]}, "everest", None, "unknown"),
    ({"product": "unknown", "confidence": 0.7, "candidates": ["fuji"]}, "everest",
     {"verdict": "mismatch", "confidence": 0.9}, "unknown"),
])
def test_decide_product_rules(vision, folder, verify, expect):
    product, _conf, rule = pl.decide_product(dict(vision, reason=""), folder, verify)
    assert product == expect, rule


# ---------------------------------------------------------------------------
# Selection
# ---------------------------------------------------------------------------

def test_selection_never_offers_another_models_photo(tenant):
    _library(tenant, ALL)
    ids = [a["id"] for a in pl.facts_pack_assets(tenant, "fuji", allow_ai_renders=True)]

    assert EVEREST_RED["id"] not in ids          # another model
    assert UNKNOWN_CABIN["id"] not in ids        # unidentified cabin (could be another model)
    assert FUJI_AD_STILL["id"] not in ids        # text overlay never offered for a slot
    assert AGNOSTIC_SPEAKER["id"] in ids         # shows no model at all
    assert AGNOSTIC_ROOF["id"] not in ids        # outdoor roof: Fuji is an indoor model
    assert ids[0] == FUJI_EXTERIOR["id"]         # hero candidate first
    assert set(ids) >= {FUJI_RED["id"], FUJI_ROOM["id"], FUJI_PLUG["id"], FUJI_PERSON["id"]}

    patagonia = [a["id"] for a in pl.facts_pack_assets(tenant, "patagonia", allow_ai_renders=True)]
    assert patagonia == []                       # no photo of it -> old pools unchanged


def test_selection_offers_nothing_when_the_derivative_files_are_missing(tenant):
    pl.save_manifest(tenant, {"version": 1, "photos": ALL})
    assert pl.facts_pack_assets(tenant, "fuji", allow_ai_renders=True) == []


def test_selection_respects_allow_ai_renders(tenant):
    ai_photo = _photo(20, _tags(shot="exterior"), ai=True)
    _library(tenant, [ai_photo])
    assert pl.facts_pack_assets(tenant, "fuji", allow_ai_renders=False) == []
    assert pl.facts_pack_assets(tenant, "fuji", allow_ai_renders=True)[0]["ai_generated"] is True


# ---------------------------------------------------------------------------
# Topic matching (render-time assignment)
# ---------------------------------------------------------------------------

def _listicle(*texts):
    return {
        "headline": "Five things", "dek": "",
        "hero": {"asset_id": f"asset-{FUJI_HANDLE}-1"},
        "reasons": [{"heading": t, "text": "", "image": {"asset_id": f"asset-{FUJI_HANDLE}-1"}} for t in texts],
    }


def test_topic_matching_puts_each_item_on_the_photo_that_shows_it(tenant):
    _library(tenant, ALL)
    page = _listicle(
        "It plugs into a standard 120V outlet",
        "Built-in Bluetooth music",
        "Red light therapy in the same session",
        "Fits a small apartment",
    )
    result = pl.assign_page_images(page, _facts_pack(tenant), "listicle", tenant=tenant, allow_ai_renders=True)

    got = [r["image"]["asset_id"] for r in page["reasons"]]
    assert page["hero"]["asset_id"] == FUJI_EXTERIOR["id"]
    assert got == [FUJI_PLUG["id"], AGNOSTIC_SPEAKER["id"], FUJI_RED["id"], FUJI_ROOM["id"]]
    assert len(set(got + [page["hero"]["asset_id"]])) == 5          # no reuse on the page
    assert not result["notes"]


def test_topic_matching_never_picks_another_models_red_light_photo(tenant):
    _library(tenant, ALL)
    page = _listicle("Red light therapy", "More red light")
    pl.assign_page_images(page, _facts_pack(tenant), "listicle", tenant=tenant, allow_ai_renders=True)
    ids = {r["image"]["asset_id"] for r in page["reasons"]}
    assert EVEREST_RED["id"] not in ids and FUJI_AD_STILL["id"] not in ids
    assert FUJI_RED["id"] in ids


def test_no_topic_match_uses_the_best_photo_of_the_product_and_logs_it(tenant):
    _library(tenant, ALL)
    page = _listicle("A note on the warranty")
    result = pl.assign_page_images(page, _facts_pack(tenant), "listicle", tenant=tenant, allow_ai_renders=True)
    pick = page["reasons"][0]["image"]["asset_id"]
    assert pl.photos_by_id(pl.load_manifest(tenant))[pick]["tags"]["product"] == "fuji"
    assert result["notes"] == ["no topic match for reasons[0].image"]


def test_storefront_images_are_the_fallback_once_the_library_runs_out(tenant):
    _library(tenant, [FUJI_EXTERIOR, FUJI_RED])
    page = _listicle("Red light therapy", "Something else", "And another")
    result = pl.assign_page_images(page, _facts_pack(tenant, extra_storefront=2), "listicle",
                                   tenant=tenant, allow_ai_renders=True)
    ids = [r["image"]["asset_id"] for r in page["reasons"]]
    assert ids[0] == FUJI_RED["id"]
    assert ids[1:] == [f"asset-{FUJI_HANDLE}-1", f"asset-{FUJI_HANDLE}-2"]
    assert any("storefront fallback" in n for n in result["notes"])


# ---------------------------------------------------------------------------
# Cycle 78: photos that show the retired brand mark stay below the fold
# ---------------------------------------------------------------------------

def _old_logo(photo):
    return dict(photo, tags=dict(photo["tags"], old_logo_visible=True))


def test_the_hero_is_never_a_photo_that_shows_the_old_logo(tenant):
    clean = _photo(11, _tags(shot="exterior", setting="studio", blurry=True))
    _library(tenant, [_old_logo(FUJI_EXTERIOR), clean, FUJI_RED])
    page = _listicle("Red light therapy")
    pl.assign_page_images(page, _facts_pack(tenant), "listicle", tenant=tenant, allow_ai_renders=True)
    # the flagged exterior ranks higher (sharp) but the clean one wins
    assert page["hero"]["asset_id"] == clean["id"]


def test_with_only_flagged_exteriors_the_hero_falls_back_to_the_storefront(tenant):
    _library(tenant, [_old_logo(FUJI_EXTERIOR), FUJI_RED])
    page = _listicle("Red light therapy")
    result = pl.assign_page_images(page, _facts_pack(tenant), "listicle", tenant=tenant, allow_ai_renders=True)
    assert page["hero"]["asset_id"] == f"asset-{FUJI_HANDLE}-1"
    assert any("storefront fallback" in n for n in result["notes"])


def test_the_first_item_image_never_shows_the_old_logo_but_a_later_one_may(tenant):
    _library(tenant, [FUJI_EXTERIOR, _old_logo(FUJI_RED), FUJI_ROOM])
    page = _listicle("Red light therapy", "Red light, again")
    pl.assign_page_images(page, _facts_pack(tenant), "listicle", tenant=tenant, allow_ai_renders=True)
    first, second = (r["image"]["asset_id"] for r in page["reasons"])
    assert first != FUJI_RED["id"]
    assert second == FUJI_RED["id"]


def test_the_committed_library_flags_the_photos_that_show_the_old_logo():
    photos = pl.photos_by_id(pl.load_manifest(TENANT))
    for pid in ("asset-photo-84d9846da050", "asset-photo-ccdc91d82bd4"):
        assert photos[pid]["tags"]["old_logo_visible"] is True
    assert all(isinstance(p["tags"].get("old_logo_visible"), bool) for p in photos.values() if p.get("tags"))


def test_assignment_is_a_no_op_without_library_photos(tenant):
    page = _listicle("Red light")
    fp = {"product": {"name": "Fuji", "slug": FUJI_HANDLE}, "assets": [{"id": f"asset-{FUJI_HANDLE}-1"}]}
    assert pl.assign_page_images(page, fp, "listicle", tenant=tenant, allow_ai_renders=True) is None


# ---------------------------------------------------------------------------
# Gate: wrong product
# ---------------------------------------------------------------------------

def test_wrong_product_gate_fails_another_models_photo_and_passes_its_own(tenant):
    _library(tenant, ALL)
    fp = _facts_pack(tenant)
    ok = {"hero": {"asset_id": FUJI_EXTERIOR["id"]}, "reasons": [{"image": {"asset_id": AGNOSTIC_SPEAKER["id"]}}]}
    assert pl.find_wrong_product_image_violations(ok, fp, "listicle", tenant=tenant, products=PRODUCTS) == []

    bad = {"hero": {"asset_id": FUJI_EXTERIOR["id"]}, "reasons": [{"image": {"asset_id": EVEREST_RED["id"]}}]}
    problems = pl.find_wrong_product_image_violations(bad, fp, "listicle", tenant=tenant, products=PRODUCTS)
    assert len(problems) == 1
    assert problems[0]["path"] == "$.reasons[0].image.asset_id"
    assert "everest" in problems[0]["issue"] and "fuji" in problems[0]["issue"]


def test_wrong_product_gate_fails_an_unknown_hero_and_another_products_storefront_image(tenant):
    _library(tenant, ALL)
    fp = _facts_pack(tenant)
    page = {"hero": {"asset_id": AGNOSTIC_SPEAKER["id"]},
            "reasons": [{"image": {"asset_id": "asset-acme-everest-2-person-indoor-sauna-with-red-light-therapy-1"}}]}
    problems = pl.find_wrong_product_image_violations(page, fp, "listicle", tenant=tenant, products=PRODUCTS)
    issues = " ".join(p["issue"] for p in problems)
    assert len(problems) == 2
    assert "hero" in issues and "storefront photo of the everest model" in issues


# ---------------------------------------------------------------------------
# Unknown asset id
# ---------------------------------------------------------------------------

def test_unknown_asset_id_is_replaced_by_the_best_topic_match(tenant):
    _library(tenant, ALL)
    fp = _facts_pack(tenant)
    page = _listicle("Plugs into any outlet")
    page["hero"]["asset_id"] = FUJI_EXTERIOR["id"]
    page["reasons"][0]["image"]["asset_id"] = f"asset-{FUJI_HANDLE}-9"   # the 2026-10-05 guess
    result = pl.replace_unknown_asset_id(page, "$.reasons[0].image.asset_id", fp, tenant=tenant)
    assert result == (f"asset-{FUJI_HANDLE}-9", FUJI_PLUG["id"])
    assert page["reasons"][0]["image"]["asset_id"] == FUJI_PLUG["id"]


def test_unknown_asset_id_replacement_ignores_a_known_id_and_a_non_asset_path(tenant):
    fp = {"product": {"name": "Fuji", "slug": FUJI_HANDLE}, "assets": [{"id": "a"}, {"id": "b"}]}
    page = {"hero": {"asset_id": "a", "headline": "x"}}
    assert pl.replace_unknown_asset_id(page, "$.hero.asset_id", fp, tenant=tenant) is None
    assert pl.replace_unknown_asset_id(page, "$.hero.headline", fp, tenant=tenant) is None


# ---------------------------------------------------------------------------
# Real tenant: facts_for offers only the product's own photos
# ---------------------------------------------------------------------------

def test_facts_for_with_the_real_library_offers_only_the_products_own_photos(monkeypatch, tmp_path):
    from harness.ground import LocalFactsSource

    manifest = pl.load_manifest(TENANT)
    tagged = [p for p in manifest["photos"] if p.get("tags") and p["tags"]["product"] != "unknown"
              and pl._usable(p)]
    if not tagged:
        pytest.skip("committed library has no identified photo")
    files = tmp_path / "lib"
    monkeypatch.setenv(pl.FILES_DIR_ENV, str(files))
    (files / TENANT.root.name).mkdir(parents=True)
    for p in manifest["photos"]:
        (files / TENANT.root.name / p["file"]).write_bytes(b"x")
    model = tagged[0]["tags"]["product"]
    products = json.loads((TENANT.claims_dir / "products.json").read_text())["products"]
    handle = next(h for h, p in products.items() if product_name_slug(p["name"]) == model)

    fp = LocalFactsSource(TENANT.claims_dir).facts_for(handle, {"transcript_or_text": "", "hook": "", "promise": "", "angle": ""})

    by_id = pl.photos_by_id(manifest)
    lib = [a["id"] for a in fp["assets"] if a["id"].startswith(pl.ID_PREFIX)]
    assert lib and fp["image_slots"]["hero_id"] == lib[0]
    for asset_id in lib:
        t = by_id[asset_id]["tags"]
        assert t["product"] == model or (t["product"] == "unknown" and t["model_agnostic"])
    assert not any(a["id"].startswith(("asset-drive-", "asset-listicle-")) for a in fp["assets"])
    assert any(a["id"].startswith(f"asset-{handle}-") for a in fp["assets"])   # storefront fallback kept
    assert len(json.dumps(fp)) / 4 < 4000


def test_the_heading_topic_beats_passing_mentions_in_the_body(tenant):
    _library(tenant, ALL)
    page = _listicle("Made for a small apartment")
    page["reasons"][0]["text"] = "Play Bluetooth music while you sweat."
    pl.assign_page_images(page, _facts_pack(tenant), "listicle", tenant=tenant, allow_ai_renders=True)
    assert page["reasons"][0]["image"]["asset_id"] == FUJI_ROOM["id"]


def test_an_agnostic_photo_is_kept_off_a_model_pass_one_ruled_out(tenant):
    hemlock_only = dict(_photo(30, _tags(product="unknown", shot="detail", features=["bench"], agnostic=True)),
                        decision={"vision_candidates": ["everest", "patagonia"]})
    _library(tenant, [FUJI_EXTERIOR, hemlock_only])
    assert hemlock_only["id"] not in [a["id"] for a in pl.facts_pack_assets(tenant, "fuji", allow_ai_renders=True)]


@pytest.mark.parametrize("shot, candidates, vision_agnostic, expect", [
    ("detail", ["a", "b", "c", "d"], False, True),          # a part every model shares
    ("detail", ["a", "b"], False, False),                   # narrows to a few models
    ("people-in-use", ["a", "b", "c", "d"], False, False),  # a person shot can show a whole cabin
    ("people-in-use", [], True, True),                      # no cabin at all
])
def test_derive_agnostic(shot, candidates, vision_agnostic, expect):
    tags = _tags(product="unknown", shot=shot)
    assert pl.derive_agnostic(tags, {"candidates": candidates, "agnostic": vision_agnostic}) is expect
