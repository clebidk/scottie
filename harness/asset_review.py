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
            asset = dict(asset, alt=override["alt"])
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
