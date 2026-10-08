"""story (cycle 79, adapter): text first -- eyebrow, headline, byline, then a
lede that starts on screen one with the speaker's verbatim quote next to a
small round crop of the ad still. Needs nothing; the look keeps its CSS."""
from . import Hero, register


def context(page, ad_brief, facts_pack, tenant, image, *, cartridge, base, look):
    return {"anchor": ""}


register(Hero(
    id="story", title="Story", supports=("listicle",), context=context, adapter=True,
    summary="Text first; the lede quotes the ad and item 01 starts on screen one.",
))
