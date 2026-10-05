"""Cycle 79: the tenant's product cut-outs -- one image per model.

Owner rule (2026-10-05, "for now"): every generation shows the page's model
as the owner's product cut-out, never a photo that shows the retired mark.
The product hero and every other product-of-X image slot the renderer owns
(a comparison column, a quiz result card) use the cut-out of X. The cut-out
set the owner sent still carries the old mark; he accepted that for now, so
the cycle 78 old-logo rule does not apply to it (it still applies to library
PHOTOS).

The set is tenant data in one place, so it can be swapped for new-mark
cut-outs later without a code change:

  tenant.yaml   product_cutouts: {manifest: brand/cutouts/cutouts.json}
  manifest      {"version": 1, "set": "<name>", "models": {"<model slug>":
                 {"file": "<file next to the manifest>", "old_logo": bool}}}

A model slug is textutil.product_name_slug(product name) ("mini",
"el-capitan"). A cut-out is offered to the renderer as an ordinary asset
dict with id `asset-cutout-<slug>`, kind "cutout" and a `local_path`, so
render.download_asset reads it from disk like a photo-library derivative.
"""
import json
from pathlib import Path

from .textutil import product_name_slug

ID_PREFIX = "asset-cutout-"
DEFAULT_MANIFEST = "brand/cutouts/cutouts.json"


def manifest_path(tenant):
    """The tenant's cut-out manifest, or None when the tenant sets none and
    has no file at the default place."""
    if tenant is None:
        return None
    rel = (tenant.get("product_cutouts.manifest") if hasattr(tenant, "get") else None) or DEFAULT_MANIFEST
    path = Path(tenant.root) / rel
    return path if path.exists() else None


def load_manifest(tenant):
    """{"models": {...}} -- empty when there is no manifest or it is not
    readable (no cut-out is then used and pages render as before)."""
    path = manifest_path(tenant)
    if path is None:
        return {"models": {}}
    try:
        data = json.loads(path.read_text())
    except (OSError, ValueError):
        return {"models": {}}
    if not isinstance(data, dict) or not isinstance(data.get("models"), dict):
        return {"models": {}}
    return data


def cutout_id(model_slug):
    return f"{ID_PREFIX}{model_slug}"


def is_cutout_id(asset_id):
    return isinstance(asset_id, str) and asset_id.startswith(ID_PREFIX)


def model_of(asset_id):
    return asset_id[len(ID_PREFIX):] if is_cutout_id(asset_id) else None


def cutout_asset(tenant, model_slug):
    """The asset dict for this model's cut-out, or None (no manifest entry,
    or the file is missing)."""
    if not model_slug:
        return None
    path = manifest_path(tenant)
    entry = load_manifest(tenant)["models"].get(model_slug)
    if path is None or not isinstance(entry, dict) or not entry.get("file"):
        return None
    file_path = path.parent / entry["file"]
    if not file_path.exists():
        return None
    rel = file_path.relative_to(Path(tenant.root)) if Path(tenant.root) in file_path.parents else file_path
    return {
        "id": cutout_id(model_slug),
        "kind": "cutout",
        "url": str(rel),
        "local_path": str(file_path),
        "model": model_slug,
        "old_logo": bool(entry.get("old_logo")),
    }


def cutout_for_product(tenant, product):
    """The cut-out asset for a product dict (facts_pack.product, a model
    option row, a quiz card): matched by product_name_slug(name)."""
    name = (product or {}).get("name") or ""
    return cutout_asset(tenant, product_name_slug(name)) if name else None
