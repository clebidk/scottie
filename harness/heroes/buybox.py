"""buybox (cycle 94; reference: the owner's reference page 06 (eight-sleep)): the buy
box as the first screen -- the product cut-out, the product's full name,
the headline, the verified rating, the price and the fixed financing
sentence, the pill and the text link, the spec bullets (the landing spec
strip: verified spec rows) and the trust facts. Landing only; needs the
cut-out (falls back to ad_first without one)."""
from . import Hero, register
from ._common import fields


def context(page, ad_brief, facts_pack, tenant, image, *, cartridge, base, look):
    ctx = fields(page, ad_brief, facts_pack, tenant, cartridge=cartridge, base=base, look=look)
    # the trust facts without the rating (it has its own row here)
    rating_ids = set((ctx.get("rating") or {}).get("claim_ids") or [])
    ctx["trust"] = [t for t in ctx["trust"] if not rating_ids & set(t.get("claim_ids") or [])]
    ctx["anchor"] = ".adv-landing .hr-root"
    return ctx


register(Hero(
    id="buybox", title="Buy box", supports=("landing",), context=context,
    needs={"image": {"sources": ("cutout",), "label": "the product cut-out"}, "required": True},
    fallbacks=("ad_first",),
    summary="The buy box first: cut-out, price and financing, rating, the pill, spec bullets and trust facts.",
))
