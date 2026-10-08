"""split (cycle 94; reference: the owner's reference page 03 (bobbie)): a photo on one
side, the headline + dek, price + financing (landing), the pill, the text
link and the trust row on the other; on a phone the photo comes first, full
width. Image: the ad's still, else a portrait/room library photo
(cycle 93 shots room, lifestyle-person, exterior; portrait preferred, then
relaxed), else the product's cut-out."""
from . import Hero, register
from ._common import fields


def context(page, ad_brief, facts_pack, tenant, image, *, cartridge, base, look):
    ctx = fields(page, ad_brief, facts_pack, tenant, cartridge=cartridge, base=base, look=look)
    ctx["anchor"] = ".adv-landing .hr-root"
    return ctx


register(Hero(
    id="split", title="Split", supports=("listicle", "landing"), context=context,
    needs={"image": {"sources": ("ad_still", "library", "cutout"),
                     "shots": ("room", "lifestyle-person", "exterior", "hero-wide"),
                     "orientation": ["portrait", "square"], "label": "a portrait or room photo"},
           "required": True},
    summary="A photo on one side; the headline, the pill and the trust row on the other.",
))
