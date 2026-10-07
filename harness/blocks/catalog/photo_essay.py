"""photo_essay: 2-4 photos, each with one short line from the writer.

The renderer picks the photos (never the writer): facts_pack's photo-library
photos, by their tags -- a room shot (interior / lifestyle / people in use),
a detail, an exterior, then the next best of each -- best first by the
library's own quality rank, never an AI render, a blurry photo or one with
a text overlay. The pick depends on facts_pack and the tenant only, so the
writer is told the same photos, in the same order, and writes line i for
photo i. A photo the page already shows elsewhere (hero, room photo) is
dropped with its line at render time. Fewer photos: fewer pairs; none: the
block renders nothing. Option `ad_still: true` adds the run's ad still as a
last, uncaptioned photo (off by default: a look whose hero is the still
would show it twice). Option `layout`: "contained" (a grid, the default) or
"bleed" (each photo edge to edge).

Writer: `blocks.photo_essay.lines` -- one {text, claim_ids} per photo, each
at most LINE_MAX_WORDS words; a line with a number, price or trigger word
cites a verified claim.
"""
import json

from . import Block, _problem, check_claim_ids, number_reason, register, words

MIN_PHOTOS = 2
MAX_PHOTOS = 4
LINE_MAX_WORDS = 14
LAYOUTS = ("contained", "bleed")
# photo library shot -> the essay's three groups
GROUPS = {"interior": "room", "lifestyle": "room", "people-in-use": "room", "detail": "detail",
          "exterior": "exterior"}
GROUP_ORDER = ("room", "detail", "exterior")


def candidates(facts_pack, tenant):
    """The photos the essay shows, in order: [{asset_id, group, description}],
    at most MAX_PHOTOS. Only facts_pack assets that are photo-library photos
    (their tags come from the library manifest)."""
    from ... import photo_library

    try:
        manifest = photo_library.photos_by_id(photo_library.load_manifest(tenant))
    except Exception:  # noqa: BLE001 -- no library for this tenant: no essay
        return []
    groups = {g: [] for g in GROUP_ORDER}
    for asset in (facts_pack or {}).get("assets") or []:
        photo = manifest.get(asset.get("id")) if isinstance(asset, dict) else None
        tags = (photo or {}).get("tags") or {}
        if not photo or asset.get("ai_generated") or photo.get("ai_generated") or photo.get("excluded"):
            continue
        if tags.get("has_text_overlay") or tags.get("blurry") or tags.get("shot") not in GROUPS:
            continue
        groups[GROUPS[tags["shot"]]].append((photo_library.quality_rank(photo), photo["id"], tags))
    for g in groups.values():
        g.sort(key=lambda t: (-t[0], t[1]))
    out = []
    while len(out) < MAX_PHOTOS and any(groups.values()):
        for g in GROUP_ORDER:
            if groups[g] and len(out) < MAX_PHOTOS:
                _rank, pid, tags = groups[g].pop(0)
                out.append({"asset_id": pid, "group": g, "description": tags.get("description") or ""})
    return out if len(out) >= MIN_PHOTOS else []


def _page_asset_ids(page):
    from ...render import collect_asset_ids

    return collect_asset_ids({k: v for k, v in (page or {}).items() if k != "blocks"})


def render(ctx):
    layout = ctx.options.get("layout") if ctx.options.get("layout") in LAYOUTS else "contained"
    fields = (ctx.page.get("blocks") or {}).get("photo_essay") if isinstance(ctx.page.get("blocks"), dict) else None
    lines = (fields or {}).get("lines") if isinstance(fields, dict) else None
    lines = lines if isinstance(lines, list) else []
    used = _page_asset_ids(ctx.page)
    have = {c["id"] for c in (ctx.facts_pack or {}).get("verified_claims", [])}
    items, asset_ids, claim_ids = [], [], set()
    for i, cand in enumerate(candidates(ctx.facts_pack, ctx.tenant)):
        if cand["asset_id"] in used:
            continue
        line = lines[i] if i < len(lines) and isinstance(lines[i], dict) else {}
        text = str(line.get("text") or "").strip()
        ids = [c for c in (line.get("claim_ids") or []) if c]
        if ids and not all(c in have for c in ids):
            text, ids = "", []   # the gate fails this; never print an unverified line
        if text and number_reason(text, ctx.facts_pack) and not ids:
            text = ""
        asset = ctx.assets.get(cand["asset_id"])
        if asset is None:
            asset = next((dict(a) for a in (ctx.facts_pack or {}).get("assets") or []
                          if a.get("id") == cand["asset_id"]), None)
            if asset is None:
                continue
            if ctx.asset_alt:
                asset["alt"] = ctx.asset_alt(asset)
            ctx.assets[cand["asset_id"]] = asset
        items.append({"asset_id": cand["asset_id"], "line": text, "group": cand["group"]})
        asset_ids.append(cand["asset_id"])
        claim_ids |= set(ids)
    if ctx.options.get("ad_still") and ctx.frame_asset and len(items) < MAX_PHOTOS:
        items.append({"asset_id": ctx.frame_asset["id"], "line": "", "group": "ad_still"})
        asset_ids.append(ctx.frame_asset["id"])
    if len(items) < MIN_PHOTOS:
        return {"layout": layout, "photos": [], "asset_ids": [], "claim_ids": []}
    return {"layout": layout, "photos": items, "asset_ids": asset_ids, "claim_ids": sorted(claim_ids)}


def required(facts_pack, tenant):
    return bool(candidates(facts_pack, tenant))


def writer_lines(facts_pack, tenant):
    photos = candidates(facts_pack, tenant)
    if not photos:
        return ["blocks.photo_essay: this run has no library photos for the photo essay -- omit "
                "blocks.photo_essay."]
    shown = [{"photo": i + 1, "shows": p["description"] or p["group"]} for i, p in enumerate(photos)]
    return [
        f"blocks.photo_essay.lines has exactly {len(photos)} entries, one per photo below, in this order -- "
        + json.dumps(shown, ensure_ascii=False) + f" -- each {{text, claim_ids}}: text is one short line of at "
        f"most {LINE_MAX_WORDS} words about what that photo shows and why it matters to the buyer (no caption "
        "words like 'pictured' or 'photo'); a line with a number, price, spec or trigger word carries claim_ids "
        "from facts_pack.verified_claims, else claim_ids is []."
    ]


def gate(fields, facts_pack, tenant):
    lines = fields.get("lines")
    if not isinstance(lines, list) or not MIN_PHOTOS <= len(lines) <= MAX_PHOTOS:
        n = len(lines) if isinstance(lines, list) else 0
        return [_problem("photo_essay", ".lines", "lines_count",
                         f"blocks.photo_essay.lines has {n} entries; it needs {MIN_PHOTOS}-{MAX_PHOTOS}, one per photo")]
    problems = []
    offered = len(candidates(facts_pack, tenant))
    if offered and len(lines) != offered:
        problems.append(_problem("photo_essay", ".lines", "lines_match",
                                 f"blocks.photo_essay.lines has {len(lines)} entries; the essay shows {offered} "
                                 "photos -- write exactly one line per photo, in order"))
    for i, line in enumerate(lines):
        path = f".lines[{i}]"
        if not isinstance(line, dict) or not str(line.get("text") or "").strip():
            problems.append(_problem("photo_essay", path, f"line_shape:{i}",
                                     f"blocks.photo_essay.lines[{i}] is {{text, claim_ids}} with a text"))
            continue
        text = str(line["text"])
        if words(text) > LINE_MAX_WORDS:
            problems.append(_problem("photo_essay", f"{path}.text", f"line_words:{i}",
                                     f"blocks.photo_essay.lines[{i}].text is {words(text)} words; at most "
                                     f"{LINE_MAX_WORDS}", text=text))
        reason = number_reason(text, facts_pack)
        problems += check_claim_ids("photo_essay", f"{path}.claim_ids", f"line:{i}", line.get("claim_ids"),
                                    facts_pack, required=bool(reason),
                                    why=f" (it states {reason})" if reason else "")
    return problems


BLOCK = register(Block(
    id="photo_essay",
    title="Photo essay",
    needs=("facts_pack.assets (photo library)", "tenant photo library manifest", "page.blocks.photo_essay"),
    render=render,
    writer={"type": "object", "properties": {"lines": {
        "type": "array", "description": f"One per photo, in order, {MIN_PHOTOS}-{MAX_PHOTOS}.",
        "items": {"type": "object", "properties": {
            "text": {"type": "string", "description": f"At most {LINE_MAX_WORDS} words."},
            "claim_ids": {"type": "array", "description": "Verified claim ids; required when the line has a number."},
        }},
    }}},
    writer_lines=writer_lines,
    gate=gate,
    required=required,
    options={"layout": "contained", "ad_still": False},
))
