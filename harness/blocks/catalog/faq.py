"""faq: the page's FAQ (page.faq.questions) as plain Q/A on hairlines.

No writer field of its own: the cartridge's FAQ fields and gates (landing:
3-5 entries, word limits, claim ids on factual answers) already cover it.
The partial also holds the FAQ markup the landing `open` and `proof` looks
used to repeat inline: a look that includes it WITHOUT declaring the block
gets that markup byte for byte; a look that declares it gets the catalog
markup (`bk-faq-*`, data-look-pattern="faq").
"""
from . import Block, register


def render(ctx):
    faq = ctx.page.get("faq") if isinstance(ctx.page.get("faq"), dict) else {}
    questions = [q for q in (faq.get("questions") or []) if isinstance(q, dict)
                 and str(q.get("question") or "").strip() and str(q.get("answer") or "").strip()]
    return {"questions": questions}


BLOCK = register(Block(
    id="faq",
    title="FAQ",
    needs=("page.faq.questions",),
    render=render,
    covers=(("page", "faq"),),
))
