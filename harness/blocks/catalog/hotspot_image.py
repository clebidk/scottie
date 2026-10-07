"""hotspot_image: one product image with 3-5 labelled dots.

The image is the run's product cut-out (harness/cutouts.py), else the best
exterior photo the photo library offers this run. Every label is built by
the renderer from this model's verified spec rows and claims -- never by
the writer -- with the claim ids that back it:

    heater   the infrared range spec ("700–25,000nm infrared"), else the max
             temperature ("Up to 150°F")
    bench    the capacity spec ("2-person bench")
    panel    a claim of this model that names red light ("Red light panel")
    glass    a claim of this model that names glass ("Glass door")
    control  a claim of this model that names a control panel
    wood     the cabin material spec ("Canadian red cedar")

A key with no backing fact has no dot. Positions come from the tenant's
per-model table (tenants/<t>/brand/hotspots.json: {"models": {"<model
slug>": {"<key>": [x%, y%]}}}) when it has the model, else the dots are
placed evenly down the image. With fewer than MIN_DOTS labels the block
shows the image and its labels as a list, without dots. Option `hero:
true` loads the image eagerly (a look whose first screen it is).
"""
import json
import re
from pathlib import Path

from . import Block, register

MIN_DOTS = 3
MAX_DOTS = 5
KEYS = ("heater", "bench", "panel", "glass", "control", "wood")
MANIFEST = "brand/hotspots.json"
_PAREN_RE = re.compile(r"\s*\([^)]*\)\s*$")


def _spec(facts_pack, pattern):
    from ... import pdp

    for row in pdp.spec_rows(facts_pack, limit=50):
        if re.search(pattern, row["label"], re.I):
            return row
    return None


def _model_claim(facts_pack, slug, pattern):
    """The first verified claim scoped to this model (its id names the model:
    spec-<m>-*, gbrain-<m>-*, pdp-<m>-*) whose text matches `pattern`."""
    for claim in (facts_pack or {}).get("verified_claims", []):
        cid = str(claim.get("id") or "")
        if re.match(rf"^(?:spec|gbrain|pdp)-{re.escape(slug)}-", cid) and re.search(pattern, claim.get("text", ""), re.I):
            return claim
    return None


def labels(facts_pack, slug):
    """{key: {label, claim_ids}} for every key a verified fact backs."""
    out = {}
    row = _spec(facts_pack, r"infrared|wavelength")
    if row:
        out["heater"] = {"label": f"{_PAREN_RE.sub('', row['value'])} infrared", "claim_ids": row["claim_ids"]}
    else:
        row = _spec(facts_pack, r"max(?:imum)?\s+temp")
        if row:
            out["heater"] = {"label": f"Up to {row['value']}", "claim_ids": row["claim_ids"]}
    row = _spec(facts_pack, r"capacity|persons?|seats?")
    if row:
        out["bench"] = {"label": f"{row['value']} bench".capitalize(), "claim_ids": row["claim_ids"]}
    claim = _model_claim(facts_pack, slug, r"\bred light\b")
    if claim:
        included = re.search(r"\bincluded\b", claim["text"], re.I)
        out["panel"] = {"label": "Red light panel" + (", included" if included else ""), "claim_ids": [claim["id"]]}
    claim = _model_claim(facts_pack, slug, r"\bglass\b")
    if claim:
        door = re.search(r"\bdoor\b", claim["text"], re.I)
        out["glass"] = {"label": "Glass door" if door else "Glass front", "claim_ids": [claim["id"]]}
    claim = _model_claim(facts_pack, slug, r"\bcontrol (?:panel|pad)\b|\btouch ?(?:screen|panel)\b")
    if claim:
        out["control"] = {"label": "Control panel", "claim_ids": [claim["id"]]}
    row = _spec(facts_pack, r"material|wood")
    if row:
        out["wood"] = {"label": _PAREN_RE.sub("", row["value"]), "claim_ids": row["claim_ids"]}
    return out


def load_positions(tenant):
    """{model slug: {key: (x, y)}} from the tenant's hotspots.json, {} when
    it has none or it does not parse."""
    root = getattr(tenant, "root", None)
    path = Path(root) / MANIFEST if root else None
    if path is None or not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text())
    except (OSError, ValueError):
        return {}
    out = {}
    for slug, table in ((data or {}).get("models") or {}).items():
        if not isinstance(table, dict):
            continue
        out[slug] = {k: (float(v[0]), float(v[1])) for k, v in table.items()
                     if k in KEYS and isinstance(v, (list, tuple)) and len(v) == 2
                     and all(isinstance(n, (int, float)) and 0 <= n <= 100 for n in v)}
    return out


def _even(n):
    """n positions placed evenly down the image, alternating sides."""
    return [(38.0 if i % 2 == 0 else 62.0, round(18 + i * (64 / max(n - 1, 1)), 1)) for i in range(n)]


def image_asset(ctx, slug):
    """(asset dict, id) of the cut-out, else the first exterior library photo."""
    from ... import cutouts, photo_library

    asset = cutouts.cutout_for_product(ctx.tenant, (ctx.facts_pack or {}).get("product"))
    if asset is not None:
        return asset
    try:
        manifest = photo_library.photos_by_id(photo_library.load_manifest(ctx.tenant))
    except Exception:  # noqa: BLE001 -- no library: no photo fallback
        manifest = {}
    for a in (ctx.facts_pack or {}).get("assets") or []:
        tags = (manifest.get(a.get("id")) or {}).get("tags") or {}
        if tags.get("shot") == "exterior" and tags.get("product") == slug and not a.get("ai_generated"):
            return dict(a)
    return None


def render(ctx):
    from ...textutil import product_name_slug

    product = (ctx.facts_pack or {}).get("product") or {}
    slug = product_name_slug(product.get("name") or "")
    asset = image_asset(ctx, slug) if slug else None
    if asset is None:
        return {}
    if asset["id"] not in ctx.assets:
        if ctx.asset_alt:
            asset = dict(asset, alt=ctx.asset_alt(asset))
        ctx.assets[asset["id"]] = asset
    found = labels(ctx.facts_pack, slug)
    table = load_positions(ctx.tenant).get(slug) or {}
    # A model in the table shows the keys the table places (a part the table
    # leaves out is not on that model's picture); else every backed key,
    # placed evenly.
    keys = [k for k in KEYS if k in found and (not table or k in table)][:MAX_DOTS]
    spots = [table[k] for k in keys] if table else _even(len(keys))
    # numbered top to bottom, so the list under the image reads down the picture
    placed = sorted(zip(keys, spots), key=lambda kv: (kv[1][1], kv[1][0]))
    dots = [{"n": i + 1, "key": k, "label": found[k]["label"], "claim_ids": found[k]["claim_ids"],
             "x": x, "y": y, "side": "left" if x > 55 else "right"}
            for i, (k, (x, y)) in enumerate(placed)]
    return {
        "asset_id": asset["id"],
        "hero": bool(ctx.options.get("hero")),
        "dots": dots,
        "show_dots": len(dots) >= MIN_DOTS,
        "asset_ids": [asset["id"]],
        "claim_ids": sorted({c for d in dots for c in d["claim_ids"]}),
    }


BLOCK = register(Block(
    id="hotspot_image",
    title="Hotspot image",
    needs=("tenant product cut-outs", "facts_pack.specs", "facts_pack.verified_claims",
           "tenants/<t>/brand/hotspots.json"),
    render=render,
    options={"hero": False},
))
