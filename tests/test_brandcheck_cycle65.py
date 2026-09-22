"""Cycle 65: graphics in the retired brand look (mint backgrounds, green icon
chips, a dark green power panel) are flagged old_brand and kept out of every
page's asset pool; photos -- including the cabin's own green chromotherapy
light and plain cedar -- are not."""

import json
import random

from PIL import Image, ImageDraw

from harness import asset_review, brandcheck
from tests.test_serve_images import (  # noqa: F401 -- fixtures
    FUJI_SLUG,
    _auth,
    _first_asset_id,
    app_client,
    isolated_images_paths,
    review_env,
)


def _save(im, path):
    im.save(path, quality=90)
    return path


def _mint_infographic(path):
    """ "Modern Infrared Luxury": mint page, pale green chips, dark text."""
    im = Image.new("RGB", (480, 480), (231, 243, 236))
    d = ImageDraw.Draw(im)
    for y in (100, 180, 260, 340):
        d.rounded_rectangle((30, y, 230, y + 60), 12, fill=(206, 233, 216))
        d.rectangle((50, y + 20, 200, y + 30), fill=(40, 40, 40))
    d.rectangle((40, 30, 300, 50), fill=(30, 30, 30))
    d.rectangle((280, 120, 440, 380), fill=(150, 90, 50))
    return _save(im, path)


def _green_panel_infographic(path):
    """ "The essentials": cream page, white cards, one dark green panel."""
    im = Image.new("RGB", (480, 480), (250, 247, 240))
    d = ImageDraw.Draw(im)
    d.rounded_rectangle((20, 70, 460, 150), 10, fill=(38, 78, 58))
    for row in range(3):
        for col in range(2):
            x, y = 20 + col * 225, 170 + row * 95
            d.rounded_rectangle((x, y, x + 215, y + 85), 8, fill=(255, 255, 255))
            d.rectangle((x + 15, y + 30, x + 150, y + 38), fill=(40, 40, 40))
    return _save(im, path)


def _noisy_photo(path, base, region=None, region_color=None, seed=1):
    """A photo stand-in: every pixel jittered, so no flat color areas."""
    rnd = random.Random(seed)
    im = Image.new("RGB", (240, 240))
    px = im.load()
    for x in range(240):
        for y in range(240):
            color = region_color if region and region[0] <= x < region[2] and region[1] <= y < region[3] else base
            px[x, y] = tuple(max(0, min(255, c + rnd.randint(-40, 40))) for c in color)
    return _save(im, path)


def test_the_mint_infographic_is_old_brand(tmp_path):
    scores = brandcheck.image_scores(_mint_infographic(tmp_path / "a.jpg"))
    assert brandcheck.is_old_brand(scores), scores


def test_the_green_power_panel_infographic_is_old_brand(tmp_path):
    scores = brandcheck.image_scores(_green_panel_infographic(tmp_path / "a.jpg"))
    assert brandcheck.is_old_brand(scores), scores


def test_a_photo_with_the_cabin_green_light_on_is_not_old_brand(tmp_path):
    path = _noisy_photo(tmp_path / "a.jpg", (160, 110, 70), region=(60, 20, 180, 220), region_color=(60, 200, 90))
    scores = brandcheck.image_scores(path)
    assert scores["green"] > 0.3  # plenty of green -- the flat check is what keeps it
    assert not brandcheck.is_old_brand(scores), scores


def test_a_cedar_photo_is_not_old_brand(tmp_path):
    scores = brandcheck.image_scores(_noisy_photo(tmp_path / "a.jpg", (170, 100, 60)))
    assert not brandcheck.is_old_brand(scores), scores


def test_a_plain_white_product_shot_is_not_old_brand(tmp_path):
    im = Image.new("RGB", (480, 480), (255, 255, 255))
    ImageDraw.Draw(im).rectangle((150, 60, 330, 420), fill=(150, 90, 50))
    scores = brandcheck.image_scores(_save(im, tmp_path / "a.jpg"))
    assert not brandcheck.is_old_brand(scores), scores


def test_check_assets_reads_the_cache_and_downloads_only_what_is_missing(tmp_path):
    cache = tmp_path / "cache"
    cache.mkdir()
    _mint_infographic(cache / "asset-a-800.jpg")
    _noisy_photo(cache / "asset-a-480.jpg", (170, 100, 60))  # smallest width wins, so this is read
    downloads = []

    def fake_download(asset, dest_dir, log=None):
        downloads.append(asset["id"])
        return {"path": _green_panel_infographic(tmp_path / "b.jpg")}

    results, unreadable = brandcheck.check_assets(
        [{"id": "asset-a"}, {"id": "asset-b"}], [cache], download=fake_download
    )
    assert downloads == ["asset-b"]
    assert unreadable == []
    assert [r["old_brand"] for r in results] == [False, True]


def _result(asset, old_brand):
    return {"asset": asset, "old_brand": old_brand, "scores": {"green": 0.07, "mint": 0.0, "flat": 0.88}}


def test_apply_flags_adds_updates_and_clears_without_touching_other_keys():
    shop = {"id": "asset-shop-14", "source": "shopify", "url": "https://cdn/x.png"}
    drive = {"id": "asset-drive-1", "source": "drive"}
    old = {"id": "asset-drive-2", "source": "drive"}
    review = {"version": 1, "assets": {
        "asset-shop-14": {"alt": "human alt", "excluded": False, "note": "n", "by": "a@b", "at": "t",
                          "url": "https://cdn/x.png"},
        "asset-drive-2": {"alt": "", "excluded": False, "old_brand": True, "old_brand_scores": {}},
        "asset-other": {"alt": "untouched"},
    }}
    new, flagged, cleared = brandcheck.apply_flags(
        review, [_result(shop, True), _result(drive, True), _result(old, False)], now="now"
    )
    assets = new["assets"]
    assert flagged == ["asset-shop-14", "asset-drive-1"] and cleared == ["asset-drive-2"]
    assert assets["asset-shop-14"]["alt"] == "human alt" and assets["asset-shop-14"]["old_brand"] is True
    assert assets["asset-drive-1"] == {"alt": "", "excluded": False, "note": "", "by": brandcheck.FLAGGED_BY,
                                       "at": "now", "old_brand": True,
                                       "old_brand_scores": {"green": 0.07, "mint": 0.0, "flat": 0.88}}
    assert "old_brand" not in assets["asset-drive-2"]
    assert assets["asset-other"] == {"alt": "untouched"}
    assert "old_brand" not in review["assets"]["asset-shop-14"]  # input not mutated


def test_apply_flags_never_re_points_a_stale_shopify_override():
    asset = {"id": "asset-shop-14", "source": "shopify", "url": "https://cdn/new.png"}
    review = {"assets": {"asset-shop-14": {"alt": "alt for the OLD image", "excluded": True,
                                            "url": "https://cdn/old.png"}}}
    new, _, _ = brandcheck.apply_flags(review, [_result(asset, True)], now="now")
    entry = new["assets"]["asset-shop-14"]
    assert entry["url"] == "https://cdn/new.png" and entry["alt"] == "" and entry["excluded"] is False


def test_an_old_brand_asset_is_excluded_from_the_pool():
    review = {"assets": {"asset-a": {"alt": "", "excluded": False, "old_brand": True}}}
    assets = [{"id": "asset-a", "url": "u"}, {"id": "asset-b", "url": "u"}]
    assert asset_review.excluded_ids(review, assets) == {"asset-a"}
    assert [a["id"] for a in asset_review.apply_asset_review(assets, review)] == ["asset-b"]


def test_a_reviewer_save_keeps_the_old_brand_flag(app_client, isolated_images_paths):  # noqa: F811
    page = app_client.get(f"/images/{FUJI_SLUG}", headers=_auth())
    asset_id = _first_asset_id(page.data.decode())
    path = isolated_images_paths / "brand" / "asset-review.json"
    path.write_text(json.dumps({"version": 1, "assets": {asset_id: {
        "alt": "", "excluded": False, "old_brand": True, "old_brand_scores": {"green": 0.07}}}}))
    app_client.post(
        f"/images/{FUJI_SLUG}/save",
        data={"ids": asset_id, f"alt:{asset_id}": "new alt", f"status:{asset_id}": "keep", f"note:{asset_id}": ""},
        headers=_auth(),
    )
    entry = json.loads(path.read_text())["assets"][asset_id]
    assert entry["alt"] == "new alt"
    assert entry["old_brand"] is True and entry["old_brand_scores"] == {"green": 0.07}
