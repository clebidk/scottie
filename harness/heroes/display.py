"""display (cycle 79, adapter): the wide display face (upper case) with the
accent phrase in red, the dek, the product cut-out, the stats strip and a
scroll cue. Needs nothing (the cut-out is the page's own hero slot)."""
from . import Hero, register


def context(page, ad_brief, facts_pack, tenant, image, *, cartridge, base, look):
    return {"anchor": ""}


register(Hero(
    id="display", title="Display", supports=("listicle",), context=context, adapter=True,
    summary="A wide display headline, the product cut-out and a stats strip.",
))
