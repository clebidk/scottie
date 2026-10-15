"""peak_first (cycle 110): the pre-sell landing look's first screen, built for
the phone window a visitor actually sees on load (375 x 812 inside the
live theme: promo bar + header take y 0-116, the chat bar and the sales popup
cover the bottom 212px, so about y 116-600 is visible). In that window, in
order: the offer bar (a verified sale only), the eyebrow, the headline (the
ad's hook), a two-line subhead, the proof row (store rating, units
delivered), the price line (net sale price with the code and the struck list
price, or the price and the monthly figure) and the CTA pill. The photo comes
right after the pill, cropped 4:5 and at most 420px tall, so its top edge
shows at load: the ad's still for a creator ad (a first-person speaker), else
the product cut-out (owner rule), else a real-room library photo.

Every value is the landing renderer's (landing.render_context `sell`), so
nothing here is copy."""
from . import Hero, register
from ._common import fields


def context(page, ad_brief, facts_pack, tenant, image, *, cartridge, base, look):
    ctx = fields(page, ad_brief, facts_pack, tenant, cartridge=cartridge, base=base, look=look)
    ctx["anchor"] = ".adv-landing .hr-root"
    ctx["sell"] = (base or {}).get("sell") or {}
    return ctx


register(Hero(
    id="peak_first", title="Pre-sell first screen", supports=("landing",), context=context,
    needs={"image": {"sources": ("ad_still", "cutout", "library"),
                     "shots": ("room", "exterior", "hero-wide"),
                     "label": "the ad still, the product cut-out or a room photo"},
           "required": False},
    summary="Offer bar, hook headline, proof row, price line and the pill inside the first phone window; "
            "the photo right after.",
))
