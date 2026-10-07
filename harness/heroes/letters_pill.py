"""letters_pill (cycle 94; reference: the owner's reference page 09 (rivian)): giant
display letters -- the first two and the last two letters of the tenant's
display name ("AC" / "ME" for "ACME"), or tenant.yaml `hero.letters` -- flanking a
pill-shaped photo window (border-radius on the image box), then the
headline, the price line or dek, and two actions: the pill and the text link
as an outline pill. Image: an exterior/lifestyle library photo (outdoor
preferred), else the product's cut-out, contained in the pill."""
from . import Hero, register
from ._common import fields, letters


def context(page, ad_brief, facts_pack, tenant, image, *, cartridge, base, look):
    ctx = fields(page, ad_brief, facts_pack, tenant, cartridge=cartridge, base=base, look=look)
    ctx["letters"] = letters(tenant)
    ctx["anchor"] = ".adv-landing .hr-root"
    return ctx


register(Hero(
    id="letters_pill", title="Letters and pill", supports=("listicle", "landing"), context=context,
    needs={"image": {"sources": ("library", "cutout"), "shots": ("exterior", "lifestyle"),
                     "settings": ("outdoor",), "label": "a 4:5-ish photo or the product cut-out"},
           "required": True},
    fallbacks=("dark_statement",),
    summary="Giant display letters flanking a pill-shaped photo window, then the headline and two actions.",
))
