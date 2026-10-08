"""statement_band: a dark basalt band with one display line from the writer
and the page's CTA pill.

Writer: `blocks.statement_band.line` -- at most LINE_MAX_WORDS words, no
number, price or trigger word (it carries no claim ids, like the landing
hero and closing lines). The pill is the page's one cta_url / cta_text.
"""
from . import Block, _problem, number_reason, register, words

LINE_MAX_WORDS = 12


def render(ctx):
    fields = (ctx.page.get("blocks") or {}).get("statement_band") if isinstance(ctx.page.get("blocks"), dict) else None
    line = str((fields or {}).get("line") or "").strip() if isinstance(fields, dict) else ""
    if not line or number_reason(line, ctx.facts_pack) or words(line) > LINE_MAX_WORDS:
        return {"line": ""}   # the gate fails it; the band never prints an unchecked line
    return {"line": line}


def writer_lines(facts_pack, tenant):
    return [f"blocks.statement_band.line: one display line of at most {LINE_MAX_WORDS} words for a dark band "
            "with the CTA under it -- the page's promise in plain words, with no number, price, spec or trigger "
            "word (it carries no claim_ids) and not a repeat of the headline or closing.headline."]


def gate(fields, facts_pack, tenant):
    line = str(fields.get("line") or "").strip()
    if not line:
        return [_problem("statement_band", ".line", "line", "blocks.statement_band.line is required")]
    problems = []
    if words(line) > LINE_MAX_WORDS:
        problems.append(_problem("statement_band", ".line", "line_words",
                                 f"blocks.statement_band.line is {words(line)} words; at most {LINE_MAX_WORDS}",
                                 text=line))
    reason = number_reason(line, facts_pack)
    if reason:
        problems.append(_problem("statement_band", ".line", "line_number",
                                 f"blocks.statement_band.line has no claim_ids, so it cannot state {reason} -- "
                                 "rewrite it without the number", text=line))
    return problems


BLOCK = register(Block(
    id="statement_band",
    title="Statement band",
    needs=("page.blocks.statement_band", "page.cta_url", "page.cta_text"),
    render=render,
    writer={"type": "object", "properties": {
        "line": {"type": "string", "description": f"At most {LINE_MAX_WORDS} words, no number."}}},
    writer_lines=writer_lines,
    gate=gate,
))
