"""Cycle 36: human review of the writer's asset pool. A reviewer looks at
every image a product could place on a page (harness/serve.py's /images
routes) and writes alt text or excludes an image entirely; those decisions
live in tenants/<t>/brand/asset-review.json and are applied here, in
ground.LocalFactsSource.facts_for, before the writer or ground.pick_hero/
build_slot_plan ever see the pool. See docs/IMAGES.md.

`{"version": 1, "assets": {"<asset id>": {"alt": str, "excluded": bool,
"note": str, "by": str, "at": iso8601, "url": str (Shopify only)}}}`. The
asset id is the full id as it appears in facts_for()'s pool (`asset-<slug>-
<n>`, `asset-drive-<id>`, `asset-listicle-<id>`).
"""
import datetime
import json
from pathlib import Path

# Cycle 44: the reviewed alt can be a full vision-drafted sentence (~20
# words, tenants/<t>/brand/asset-review.json). That is fine for a human
# reviewing one image at a time (harness/serve.py's /images site reads
# the override directly, uncapped), but facts_for()'s facts_pack carries
# every pool asset's alt at once, and with hundreds of reviewed assets a
# full sentence each blows the writer prompt's token cap (see
# tests/test_ground.py::test_facts_pack_stays_small). Neither of the
# alt's other two readers needs the uncapped text: render.render_page
# always overwrites an asset's alt with a kind-derived one before it
# reaches HTML (render.py, "alt text is always renderer-derived"), and
# ground.match_images_to_text re-reads asset-review.json itself rather
# than trusting facts_pack's copy. So capping here is safe.
#
# The number itself: measured against a real tenant's own 21-asset
# product pool (the one tests/test_ground.py::test_facts_pack_stays_small
# checks), facts_pack was already at ~3988/4000 tokens before this
# cycle's reviewed alts existed for that pool -- there was only ~12
# tokens (~48 chars) of headroom to begin with, spread across the whole
# pack, not just assets. Measured empirically (script run against real
# tenant data, not a fixture): every cap above 15 chars pushes that same
# pool over 4000 tokens once every asset in it carries a reviewed alt --
# 14 is used rather than the exact 15-char edge, to keep a small (~11
# token) buffer rather than shipping at the knife's edge. A future pool
# this size but with more reviewed assets, or growth elsewhere in
# facts_pack, can still blow the budget -- watch
# test_facts_pack_stays_small.
ALT_MAX_CHARS = 14


def _cap_alt(alt):
    """The first ALT_MAX_CHARS of `alt`, cut at a word boundary with no
    trailing punctuation. `alt` is assumed non-empty (callers only call
    this on a truthy override alt)."""
    alt = alt.strip()
    if len(alt) <= ALT_MAX_CHARS:
        return alt
    cut = alt[:ALT_MAX_CHARS]
    # Only back up to the previous word when the cap actually lands
    # mid-word (the next original character continues the same word) --
    # when it lands right on a word boundary already (the next character
    # is a space), the full ALT_MAX_CHARS-th character is the end of a
    # whole word and nothing needs trimming.
    if alt[ALT_MAX_CHARS] != " " and " " in cut:
        cut = cut.rsplit(" ", 1)[0]
    return cut.rstrip(" .,;:!?-")


def load_asset_review(brand_dir, *, log=None):
    """The tenant's asset-review.json as {"assets": {...}}, or {} when the
    file is missing or malformed. Never raises -- a hand-edited or broken
    override file must never take down a run; it just stops applying."""
    path = Path(brand_dir) / "asset-review.json"
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError) as e:
        if log:
            log.event("ground", f"asset-review.json unreadable, ignoring: {e}")
        return {}
    if not isinstance(data, dict) or not isinstance(data.get("assets"), dict):
        if log:
            log.event("ground", "asset-review.json malformed (expected {'assets': {...}}), ignoring")
        return {}
    return data


def excluded_ids(review, assets=()):
    """Cycle 36 fix: the set of ids `review` marks excluded=True, honoring
    the same url-mismatch rule apply_asset_review applies -- an override
    carrying a stored url that no longer matches the matching asset's
    current url (looked up in `assets`) is not treated as excluded. Meant
    to be computed BEFORE select_drive_assets/select_listicle_pack_assets
    run, and passed to them as `exclude_ids`, so an excluded top-tier asset
    is skipped before tiering/capping -- not dropped afterward, which would
    just shrink the capped pool instead of letting the next eligible asset
    fill the spot. `assets` only needs to cover ids whose override might
    carry a url (Shopify assets, in practice; a drive/listicle override
    never has one -- see save_asset_review's caller in harness/serve.py) --
    an excluded id with no url in its override, or no matching asset passed
    in at all, is still excluded."""
    overrides = (review or {}).get("assets") or {}
    by_id = {a["id"]: a for a in assets}
    result = set()
    for asset_id, override in overrides.items():
        if not override.get("excluded"):
            continue
        if "url" in override:
            current = by_id.get(asset_id)
            if current is not None and override["url"] != current.get("url"):
                continue  # stale override (url changed) -- not applicable
        result.add(asset_id)
    return result


def apply_asset_review(assets, review, *, log=None):
    """`assets` (facts_for()'s shopify+drive+listicle pool) with each asset's
    override applied: excluded assets are dropped, and a non-empty override
    alt replaces the default. A Shopify override also carries the url it was
    written against -- if that no longer matches the asset's current url
    (the product's image list was re-ordered or changed), the override is
    treated as not applicable for that id (neither the alt nor the
    exclusion is applied) and a warning is logged."""
    overrides = (review or {}).get("assets") or {}
    kept = []
    for asset in assets:
        override = overrides.get(asset["id"])
        if not override:
            kept.append(asset)
            continue
        if "url" in override and override["url"] != asset.get("url"):
            if log:
                log.event("ground", f"asset-review override for {asset['id']} ignored: url changed")
            kept.append(asset)
            continue
        if override.get("excluded"):
            continue
        if override.get("alt"):
            asset = dict(asset, alt=_cap_alt(override["alt"]))
        kept.append(asset)
    return kept


def save_asset_review(brand_dir, review):
    """Atomic write (tmp file + rename) of asset-review.json."""
    path = Path(brand_dir) / "asset-review.json"
    tmp_path = path.with_suffix(".json.tmp")
    tmp_path.write_text(json.dumps(review, indent=2) + "\n")
    tmp_path.replace(path)


def now_iso():
    return datetime.datetime.now().isoformat(timespec="seconds")
