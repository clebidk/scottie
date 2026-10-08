"""face (cycle 79, adapter): a still from the ad video, 4:5, the speaker's
verbatim quote as its caption, the headline above, a one-line byline. The
listicle open look's own first screen; the look keeps its CSS (op-face)."""
from . import Hero, register


def context(page, ad_brief, facts_pack, tenant, image, *, cartridge, base, look):
    return {"anchor": ""}


register(Hero(
    id="face", title="Face", supports=("listicle",), context=context, adapter=True,
    needs={"image": {"sources": ("ad_still",), "label": "a still from the ad"}, "required": True},
    # cycle 79 rule: story when the ad has a quotable speaker, else display
    fallbacks=lambda quote: ("story",) if quote else ("display",),
    summary="A still of the ad's creator with her own words as the caption.",
))
