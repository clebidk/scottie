"""feature_grid: 3-6 cells, each an icon, a label of at most 3 words and a
line of at most 14 words.

The writer supplies `blocks.feature_grid.cells[]` -- {label, line,
claim_ids} -- and the gate is the proof look's icon_row gate plus the line
rule. The ICON is the renderer's: it is keyed by what the cell's cited
claims state (red light, full-spectrum heat, wood, shipping, warranty, a
US-owned company, delivery, assembly -- landing.ICONS, the proof look's
fixed set), so an icon can never contradict its claim (the cycle 85 open
issue: a warranty icon over a cedar claim). A cell whose claims match no
icon topic shows a short accent rule instead.
"""
import re

from markupsafe import Markup

from . import Block, _problem, check_claim_ids, number_reason, register, words

CELLS_RANGE = (3, 6)
LABEL_MAX_WORDS = 3
LINE_MAX_WORDS = 14

# The proof look's line icons (cartridges/landing/looks/proof/template.html
# `icons`), path data only, 24x24, stroke currentColor. A test keeps the two
# copies identical.
ICON_PATHS = {
    "shipping": '<path d="M2.5 6.5h11v9h-11z"/><path d="M13.5 9.5h4l3 3v3h-7"/><circle cx="6.5" cy="17.5" r="1.75"/><circle cx="16.5" cy="17.5" r="1.75"/>',
    "warranty": '<path d="M12 3l7 3v5.5c0 4.2-2.9 7.7-7 9.5-4.1-1.8-7-5.3-7-9.5V6z"/><path d="M9 12l2.2 2.2L15.5 10"/>',
    "red_light": '<circle cx="12" cy="12" r="4"/><path d="M12 2.5v2M12 19.5v2M2.5 12h2M19.5 12h2M5.3 5.3l1.4 1.4M17.3 17.3l1.4 1.4M5.3 18.7l1.4-1.4M17.3 6.7l1.4-1.4"/>',
    "full_spectrum": '<path d="M3 7.5c3-3 6 3 9 0s6 3 9 0"/><path d="M3 12c3-3 6 3 9 0s6 3 9 0"/><path d="M3 16.5c3-3 6 3 9 0s6 3 9 0"/>',
    "wood": '<path d="M12 2.5l5.5 7.5h-3l4 6h-13l4-6h-3z"/><path d="M12 16v5.5"/>',
    "us_owned": '<path d="M5 21.5v-18"/><path d="M5 4h13l-2.5 4.5L18 13H5"/>',
    "delivery": '<rect x="3" y="4.5" width="18" height="16" rx="2"/><path d="M3 9.5h18M8 2.5v4M16 2.5v4M8.5 15l2.2 2 4.8-4.5"/>',
    "assembly": '<path d="M14.5 6.2a4 4 0 0 0 5.3 5.3l-.1.1-2.6-.6-.6-2.6zM14.5 6.2a4 4 0 0 0-5 5.1L3.5 17.3a1.9 1.9 0 0 0 2.7 2.7l6-6a4 4 0 0 0 5.1-5"/>',
}

# claim topic -> icon key, first match wins (id and text both searched)
TOPICS = (
    ("red_light", re.compile(r"red[ -]light|\brlt\b", re.I)),
    ("full_spectrum", re.compile(r"full[ -]spectrum|infrared|wavelength|\bnm\b", re.I)),
    ("wood", re.compile(r"cedar|hemlock|\bwood|basswood|spruce", re.I)),
    ("shipping", re.compile(r"ship", re.I)),
    ("warranty", re.compile(r"warrant", re.I)),
    ("us_owned", re.compile(r"\bu\.?s\.?[ -]owned|us-owned|american[ -]owned", re.I)),
    ("delivery", re.compile(r"deliver", re.I)),
    ("assembly", re.compile(r"assembl", re.I)),
)

_SVG = ('<svg viewBox="0 0 24 24" width="24" height="24" fill="none" stroke="currentColor" stroke-width="1.5" '
        'stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" focusable="false">{}</svg>')


def icon_for(claim_ids, facts_pack):
    """The icon key the cited claims' topic picks (the first claim with a
    topic wins; within one claim, TOPICS order), or None."""
    by_id = {c["id"]: c for c in (facts_pack or {}).get("verified_claims", []) if c.get("id")}
    for cid in claim_ids:
        text = (by_id.get(cid) or {}).get("text") or ""
        for key, pattern in TOPICS:
            if pattern.search(cid) or pattern.search(text):
                return key
    return None


def render(ctx):
    fields = (ctx.page.get("blocks") or {}).get("feature_grid") if isinstance(ctx.page.get("blocks"), dict) else None
    cells = (fields or {}).get("cells") if isinstance(fields, dict) else None
    have = {c["id"] for c in (ctx.facts_pack or {}).get("verified_claims", [])}
    out, claim_ids = [], set()
    for cell in cells if isinstance(cells, list) else []:
        if not isinstance(cell, dict):
            continue
        label, line = str(cell.get("label") or "").strip(), str(cell.get("line") or "").strip()
        ids = [c for c in (cell.get("claim_ids") or []) if c]
        if not label or not ids or not all(c in have for c in ids):
            continue  # the gate fails this cell; never print an unverified one
        key = icon_for(ids, ctx.facts_pack)
        out.append({"label": label, "line": line, "claim_ids": ids, "icon": key,
                    "icon_svg": Markup(_SVG.format(ICON_PATHS[key])) if key else None})
        claim_ids |= set(ids)
    out = out[:CELLS_RANGE[1]]
    if len(out) < CELLS_RANGE[0]:
        return {"cells": [], "claim_ids": []}
    return {"cells": out, "claim_ids": sorted(claim_ids)}


def writer_lines(facts_pack, tenant):
    lo, hi = CELLS_RANGE
    return [
        f"blocks.feature_grid.cells has {lo}-{hi} cells, each {{label, line, claim_ids}}: label is at most "
        f"{LABEL_MAX_WORDS} words (a noun phrase, no full stop); line is one sentence of at most {LINE_MAX_WORDS} "
        "words saying what that feature does for the buyer; claim_ids cite the verified claims that state it "
        "(at least one per cell). The renderer draws each cell's icon from what its claims state (red light, "
        "full-spectrum heat, wood, shipping, warranty, a US-owned company, delivery, assembly), so cite the "
        "claim the cell is about first. No two cells about the same feature."
    ]


def gate(fields, facts_pack, tenant):
    cells = fields.get("cells")
    lo, hi = CELLS_RANGE
    if not isinstance(cells, list) or not lo <= len(cells) <= hi:
        n = len(cells) if isinstance(cells, list) else 0
        return [_problem("feature_grid", ".cells", "cells_count",
                         f"blocks.feature_grid.cells has {n} cells; it needs {lo}-{hi}")]
    problems, labels = [], set()
    for i, cell in enumerate(cells):
        path = f".cells[{i}]"
        if not isinstance(cell, dict):
            problems.append(_problem("feature_grid", path, f"cell_shape:{i}",
                                     f"blocks.feature_grid.cells[{i}] is {{label, line, claim_ids}}"))
            continue
        label = str(cell.get("label") or "").strip()
        if not label or words(label) > LABEL_MAX_WORDS:
            problems.append(_problem("feature_grid", f"{path}.label", f"label_words:{i}",
                                     f"blocks.feature_grid.cells[{i}].label is 1-{LABEL_MAX_WORDS} words", text=label))
        elif label.lower() in labels:
            problems.append(_problem("feature_grid", f"{path}.label", f"label_duplicate:{i}",
                                     f"blocks.feature_grid.cells[{i}].label {label!r} is used twice"))
        labels.add(label.lower())
        line = str(cell.get("line") or "").strip()
        if not line or words(line) > LINE_MAX_WORDS:
            problems.append(_problem("feature_grid", f"{path}.line", f"line_words:{i}",
                                     f"blocks.feature_grid.cells[{i}].line is 1-{LINE_MAX_WORDS} words", text=line))
        reason = number_reason(line, facts_pack) or number_reason(label, facts_pack)
        problems += check_claim_ids("feature_grid", f"{path}.claim_ids", f"cell:{i}", cell.get("claim_ids"),
                                    facts_pack, required=True,
                                    why=f" (it states {reason})" if reason else " that state it")
    return problems


BLOCK = register(Block(
    id="feature_grid",
    title="Feature grid",
    needs=("page.blocks.feature_grid", "facts_pack.verified_claims"),
    render=render,
    writer={"type": "object", "properties": {"cells": {
        "type": "array", "description": f"{CELLS_RANGE[0]}-{CELLS_RANGE[1]} cells.",
        "items": {"type": "object", "properties": {
            "label": {"type": "string", "description": f"At most {LABEL_MAX_WORDS} words."},
            "line": {"type": "string", "description": f"At most {LINE_MAX_WORDS} words."},
            "claim_ids": {"type": "array", "description": "At least 1 verified claim id that states it."},
        }},
    }}},
    writer_lines=writer_lines,
    gate=gate,
))
