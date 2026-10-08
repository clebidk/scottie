"""trust_bar: the top-of-page trust facts, renderer-only.

The warranty term (the fixed warranty sentence's first clause, with the
warranty claim id), "Free shipping" (only when a verified claim says so),
the fixed financing sentence verbatim (vocab.yaml: the only way financing
may be stated) and the tenant's HSA/FSA claim verbatim when one is verified.
Nothing else, and nothing the facts do not state.
"""
from . import Block, register


def items(facts_pack):
    from ... import landing, listicle, pdp

    out = []
    term = landing.warranty_term(facts_pack)
    if term:
        out.append({"text": term["text"], "claim_ids": term["claim_ids"], "kind": "warranty"})
    ship = listicle.free_shipping_claim(facts_pack)
    if ship:
        out.append({"text": "Free shipping", "claim_ids": [ship["id"]], "kind": "shipping"})
    financing = pdp.financing_sentence(facts_pack or {})
    if financing:
        out.append({"text": financing, "claim_ids": [], "kind": "financing"})
    hsa = listicle.hsa_claim(facts_pack)
    if hsa:
        out.append({"text": str(hsa.get("text") or "").strip(), "claim_ids": [hsa["id"]], "kind": "hsa"})
    return [i for i in out if i["text"]]


def render(ctx):
    rows = items(ctx.facts_pack)
    return {"facts": rows, "claim_ids": sorted({c for r in rows for c in r["claim_ids"]})}


BLOCK = register(Block(
    id="trust_bar",
    title="Trust bar",
    needs=("facts_pack.verified_claims (warranty, shipping, HSA/FSA)", "facts_pack.product.financing"),
    render=render,
    covers=(("cartridge_data", "trust"),),
))
