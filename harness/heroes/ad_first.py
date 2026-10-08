"""ad_first (cycle 82/85, adapter): the landing page's own first screen --
eyebrow, headline, subhead, price + the fixed financing sentence, the pill
and the "Text us" link, the trust line, then the ad still (with the
speaker's quote) or the product cut-out. The proof look adds the rating row,
the review quote and its trust line under the pill. Both looks keep their
CSS; the sticky bar observes `.ld-hero`."""
from . import Hero, register


def context(page, ad_brief, facts_pack, tenant, image, *, cartridge, base, look):
    return {"anchor": ".adv-landing .ld-hero"}


register(Hero(
    id="ad_first", title="Ad first", supports=("landing",), context=context, adapter=True,
    summary="Headline, price and the pill first, then the ad's still or the product cut-out.",
))
