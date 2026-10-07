"""model_card: the run's product as a card -- cut-out, name, price, rating,
two specs and the CTA. Renderer-only.

Price is the product's own verified price claim (landing.price), the
rating the verified rating (first_screen.rating_stat, shown only when the
listicle rating rule shows it), the two specs the first two cells of the
landing spec strip; `spec_rows` carries every verified spec row for a buy
panel's bullet list. The CTA is the page's one cta_url / cta_text, printed
by the partial. No writer field. Option `variant`: "card" (compact, with
the cut-out, two specs) or "panel" (a buy panel beside a gallery: no image,
every spec row as a list).
"""
from . import Block, register

SPECS_ON_CARD = 2
SPEC_ROWS_MAX = 7


def card_for_run(ctx):
    from ... import cutouts, first_screen, landing, pdp

    fp = ctx.facts_pack or {}
    product = fp.get("product") or {}
    if not product.get("name"):
        return {}
    names = ctx.tenant.product_names(product)
    asset = cutouts.cutout_for_product(ctx.tenant, product)
    asset_ids = []
    if asset is not None:
        if asset["id"] not in ctx.assets:
            ctx.assets[asset["id"]] = dict(asset, alt=ctx.asset_alt(asset)) if ctx.asset_alt else asset
        asset_ids.append(asset["id"])
    price = landing.price(fp)
    rating = first_screen.rating_stat(fp)
    specs = landing.spec_strip(fp, cells=SPECS_ON_CARD)
    rows = pdp.spec_rows(fp, limit=SPEC_ROWS_MAX)
    claim_ids = set()
    for item in [price, rating] + specs + rows:
        if item:
            claim_ids |= set(item.get("claim_ids") or ())
    return {
        "name": names["full_name"] or product["name"],
        "asset_id": asset["id"] if asset else None,
        "price": price,
        "financing": pdp.financing_sentence(fp),
        "rating": rating,
        "specs": specs,
        "spec_rows": rows,
        "asset_ids": asset_ids,
        "claim_ids": sorted(claim_ids),
    }


VARIANTS = ("card", "panel")


def render(ctx):
    card = card_for_run(ctx)
    if card:
        card["variant"] = ctx.options.get("variant") if ctx.options.get("variant") in VARIANTS else "card"
    return card


BLOCK = register(Block(
    id="model_card",
    title="Model card",
    needs=("facts_pack.product", "facts_pack.verified_claims (price, rating)", "facts_pack.specs",
           "tenant product cut-outs"),
    render=render,
    options={"variant": "card"},
    covers=(("cartridge_data", "price"), ("cartridge_data", "rating"), ("cartridge_data", "financing")),
))
