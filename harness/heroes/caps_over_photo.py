"""caps_over_photo (cycle 94; reference: the owner's reference page 01 (ridge)): an
all-caps display headline over a dark full-bleed photo with a flat basalt
overlay (a solid token fill at fixed opacity, never a gradient), the
eyebrow, the dek or subhead, price + financing on the landing page, the
pill (white on the dark ground) and the text link, the trust row.

Image: the ad's still first (the ad's own face, message match), else the
best wide library photo (exterior, lifestyle or people in use) that reads
under a dark overlay. With neither it falls back to dark_statement."""
from . import Hero, register
from ._common import fields


def context(page, ad_brief, facts_pack, tenant, image, *, cartridge, base, look):
    ctx = fields(page, ad_brief, facts_pack, tenant, cartridge=cartridge, base=base, look=look)
    ctx["anchor"] = ".adv-landing .hr-root"
    return ctx


register(Hero(
    id="caps_over_photo", title="Caps over photo", supports=("listicle", "landing"), context=context,
    needs={"image": {"sources": ("ad_still", "library"), "shots": ("people-in-use", "lifestyle", "exterior"),
                     "orient": "wide", "dark_ok": True, "label": "a wide photo that reads under a dark overlay"},
           "required": True},
    fallbacks=("dark_statement",),
    summary="An all-caps display headline over a dark full-bleed photo with a flat basalt overlay.",
))
