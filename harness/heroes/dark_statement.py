"""dark_statement (cycle 94; references: the owner's reference page 02 (hexclad) and
07-oura.html): a dark ground, ONE display line (the short display line when
the page has one, else the headline), the dek or subhead, price + financing
on the landing page, the pill in white, the trust row, and -- when the
library has one -- a dark detail/macro photo under the text (beside it on
a wide screen). Needs no image: it is the fallback of the photo
compositions."""
from . import Hero, register
from ._common import fields


def context(page, ad_brief, facts_pack, tenant, image, *, cartridge, base, look):
    ctx = fields(page, ad_brief, facts_pack, tenant, cartridge=cartridge, base=base, look=look)
    ctx["anchor"] = ".adv-landing .hr-root"
    return ctx


register(Hero(
    id="dark_statement", title="Dark statement", supports=("listicle", "landing"), context=context,
    needs={"image": {"sources": ("library",), "shots": ("detail-macro",), "dark_ok": True,
                     "label": "a dark detail (macro) photo"},
           "required": False},
    summary="A dark ground with one display line and one pill; a dark macro photo when the library has one.",
))
