"""stat_strip: 3-4 stats inside or under a hero, renderer-built.

The values come from the same source as the listicle's first-screen stats
strip (first_screen.stats: the rating when it shows, a standard outlet, the
width, the capacity, free shipping), filled up to MAX_STATS from the
landing spec strip's verified spec rows (landing.spec_strip). No writer
field. Option `layout`: "band" (a row of its own under the hero, the
default) or "inline" (inside a dark hero, light text).
"""
import re

from . import Block, register

MIN_STATS = 3
MAX_STATS = 4
LAYOUTS = ("band", "inline")
# "700–25,000nm (near, mid, far)" -> "700–25,000nm": a stat shows the figure;
# the parenthetical gloss is dropped, never reworded.
_PAREN_RE = re.compile(r"\s*\([^)]*\)\s*$")


def stats(facts_pack):
    from ... import first_screen, landing

    out, seen = [], set()
    rows = list(first_screen.stats(facts_pack)) + [
        {"value": r["value"], "label": r["label"], "claim_ids": r["claim_ids"]}
        for r in landing.spec_strip(facts_pack, cells=MAX_STATS + 2)
    ]
    for row in rows:
        ids = tuple(row.get("claim_ids") or ())
        if not ids or ids in seen:
            continue
        seen.add(ids)
        value = _PAREN_RE.sub("", str(row["value"])).strip()
        if value:
            out.append({"value": value, "label": str(row["label"]), "claim_ids": list(ids)})
        if len(out) == MAX_STATS:
            break
    return out


def render(ctx):
    rows = stats(ctx.facts_pack)
    layout = ctx.options.get("layout") if ctx.options.get("layout") in LAYOUTS else "band"
    if len(rows) < MIN_STATS:
        return {"layout": layout, "stats": [], "claim_ids": []}
    return {"layout": layout, "stats": rows, "claim_ids": sorted({c for r in rows for c in r["claim_ids"]})}


BLOCK = register(Block(
    id="stat_strip",
    title="Stat strip",
    needs=("facts_pack.verified_claims", "facts_pack.specs", "facts_pack.reviews_summary"),
    render=render,
    options={"layout": "band"},
    covers=(("cartridge_data", "specs"),),
))
