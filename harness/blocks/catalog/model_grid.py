"""model_grid: the model_card for every model that has a price claim and a
cut-out -- the category page's grid. Renderer-only.

Rows come from facts_pack["models"]: one per active product with a price
claim, cheapest first (ground.LocalFactsSource._quiz_models, the quiz's own
model list), joined to facts_pack only when the run's look declares this
block (pipeline.ground), with their price and capacity claims added to the
verified claims so Sources can cite them. A model with no cut-out is left
out. Each card shows the cut-out, the name, the verified price, the capacity
spec, and the rating on the run's own model only (the one rating the run
verifies). Every card links to the page's ONE cta_url with its ONE cta_text,
so the writer is told to pick a CTA phrase that names no model (page_gate).
"""
import re

from . import Block, _problem, register

MIN_CARDS = 2
MAX_CARDS = 12


def render(ctx):
    from ... import cutouts, first_screen
    from ...textutil import product_name_slug

    fp = ctx.facts_pack or {}
    run_slug = product_name_slug((fp.get("product") or {}).get("name") or "")
    have = {c["id"] for c in fp.get("verified_claims", [])}
    rating = first_screen.rating_stat(fp)
    cards, asset_ids, claim_ids = [], [], set()
    for row in fp.get("models") or []:
        if not isinstance(row, dict) or row.get("price_claim_id") not in have or not row.get("price_text"):
            continue
        asset = cutouts.cutout_asset(ctx.tenant, row.get("slug"))
        if asset is None:
            continue
        if asset["id"] not in ctx.assets:
            ctx.assets[asset["id"]] = dict(asset, alt=ctx.asset_alt(asset, row.get("name"))) if ctx.asset_alt else asset
        ids = [row["price_claim_id"]]
        spec = None
        if row.get("capacity_text") and row.get("capacity_claim_id") in have:
            spec = {"label": "Capacity", "value": row["capacity_text"], "claim_ids": [row["capacity_claim_id"]]}
            ids.append(row["capacity_claim_id"])
        own = row.get("slug") == run_slug
        card = {"slug": row["slug"], "name": row.get("name") or row["slug"], "asset_id": asset["id"],
                "price": {"text": row["price_text"], "claim_ids": [row["price_claim_id"]]},
                "specs": [spec] if spec else [], "rating": rating if own else None, "is_run_model": own}
        if own and rating:
            ids += rating["claim_ids"]
        cards.append(card)
        asset_ids.append(asset["id"])
        claim_ids |= set(ids)
        if len(cards) == MAX_CARDS:
            break
    if len(cards) < MIN_CARDS:
        return {"cards": [], "asset_ids": [], "claim_ids": []}
    return {"cards": cards, "asset_ids": asset_ids, "claim_ids": sorted(claim_ids)}


def _model_words(facts_pack):
    """Lower-case words of every model's own name (model_name and slug, not
    the long catalog title), 3+ letters."""
    names = set()
    rows = list((facts_pack or {}).get("models") or []) + [(facts_pack or {}).get("product") or {}]
    for row in rows:
        for key in ("model_name", "slug", "name") if row is rows[-1] else ("model_name", "slug"):
            names |= {w.lower() for w in re.findall(r"[A-Za-z]{3,}", str(row.get(key) or ""))}
    return names


def writer_lines(facts_pack, tenant):
    return ["This look shows a grid of every model, each card linking to the page's one CTA: cta_text must "
            "name no single model (pick the allowed CTA phrase without a model name)."]


def page_gate(page, facts_pack, tenant):
    text = str(page.get("cta_text") or "")
    hits = sorted({w for w in re.findall(r"[A-Za-z]{3,}", text) if w.lower() in _model_words(facts_pack)})
    if hits:
        return [_problem("model_grid", "", "cta_names_model",
                         f"cta_text {text!r} names a model ({', '.join(hits)}), but this look's model grid links "
                         "every model's card to the one CTA -- use the allowed CTA phrase that names no model")]
    return []


BLOCK = register(Block(
    id="model_grid",
    title="Model grid",
    needs=("facts_pack.models (joined when a look declares this block)", "facts_pack.verified_claims (price)",
           "tenant product cut-outs"),
    render=render,
    writer_lines=writer_lines,
    page_gate=page_gate,
))
