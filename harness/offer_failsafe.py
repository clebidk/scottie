"""Cycle 111: the page's own fail-safe for a dated offer.

A published page is static HTML; the expiry sweep (harness/sale_sync.py)
switches it after the sale, but until that job has run the page must not
sell a sale that is over. The renderer marks, on an offer-led page only:

  - the offer lines (the offer bar that starts with the sale's name, the
    amount and code line, the deadline sentence, the CTA note, the sticky
    bar's code, a line "with code X", the offer's checked claim):
    data-offer-expires -> hidden after the instant;
  - every price line with a struck price: data-offer-expires,
    data-offer-net / data-offer-list / data-offer-strip -> the struck price
    is hidden, the sale price becomes the list price, "with code X" goes;
  - every sale CTA (the schema's sale group):
    data-offer-expires, data-offer-cta -> the tenant's default CTA text;

and adds one inline script (no network, no dependency) that does this when
Date.now() is past the element's own instant. Before the instant the script
changes nothing, so the first paint and the layout are the same.

The h1/h2 headings are not hidden. Owner decision 2026-10-16: when the run
has its non-sale twin, add_nosale puts the twin's h1 on the page's h1
(data-nosale-headline) and the twin's CTA on the sale CTAs
(data-nosale-cta), and the script swaps those texts too. Without a twin
(runs from before cycle 111) the headline stays until the sweep hides the
page.
"""
import html as html_mod
import re
from html.parser import HTMLParser

ATTR = "data-offer-expires"
_VOID = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "source", "track", "wbr"}
# the page's headline and section headlines stay; a small heading that is the offer (h3+) goes
_HEADINGS = {"h1", "h2"}

SCRIPT = """<script>(function(){var n=Date.now();
document.querySelectorAll("[data-offer-expires]").forEach(function(el){var d=el.dataset;
if(!(n>Date.parse(d.offerExpires)))return;
if(d.nosaleHeadline){el.textContent=d.nosaleHeadline;return;}
if(d.offerCta){el.textContent=d.nosaleCta||d.offerCta;return;}
if(d.offerList){var w=document.createTreeWalker(el,4),t,s=(d.offerStrip||"").split("|");
while((t=w.nextNode())){var v=t.nodeValue;if(d.offerNet)v=v.split(d.offerNet).join(d.offerList);
s.forEach(function(x){if(x)v=v.split(x).join("");});t.nodeValue=v;}
el.querySelectorAll("s,del").forEach(function(x){x.style.display="none";});return;}
el.style.display="none";});})();</script>"""


def _norm(text):
    return " ".join(html_mod.unescape(str(text or "")).split())


class _Tree(HTMLParser):
    """Element spans (offsets into the source) with their text."""

    def __init__(self, source):
        super().__init__(convert_charrefs=True)
        self.source = source
        # HTMLParser counts lines by "\n" only
        self.starts = [0] + [m.end() for m in re.finditer("\n", source)]
        self.nodes, self.stack, self.in_body = [], [], False

    def _offset(self):
        line, col = self.getpos()
        return self.starts[line - 1] + col

    def handle_starttag(self, tag, attrs):
        if tag == "body":
            self.in_body = True
            return
        start = self._offset()
        node = {"tag": tag, "start": start, "tag_end": start + len(self.get_starttag_text() or ""),
                "text": [], "has_a": tag == "a", "parent": self.stack[-1] if self.stack else None,
                "children": [], "body": self.in_body, "raw": tag in ("script", "style")}
        if node["parent"] is not None:
            node["parent"]["children"].append(node)
        if tag == "a":
            for up in self.stack:
                up["has_a"] = True
        self.nodes.append(node)
        if tag not in _VOID:
            self.stack.append(node)

    def handle_endtag(self, tag):
        for i in range(len(self.stack) - 1, -1, -1):
            if self.stack[i]["tag"] == tag:
                del self.stack[i:]
                return

    def handle_data(self, data):
        if any(n["raw"] for n in self.stack):
            return
        for n in self.stack:
            n["text"].append(data)


def _text(node):
    return _norm("".join(node["text"]))


def _price_line(node):
    """(net, list) for an element with a struck price child, else None."""
    struck = [c for c in node["children"] if c["tag"] in ("s", "del")]
    if not struck:
        return None
    listed = _text(struck[0])
    bold = next((c for c in node["children"] if c["tag"] in ("b", "strong") and _text(c).startswith("$")), None)
    net = _text(bold) if bold else _norm(_text(node).split(listed)[0]).split(" ")[0]
    return (net if net.startswith("$") and net != listed else ""), listed


def mark(page_html, view, expires, *, default_cta, sale_ctas=()):
    """`page_html` with the fail-safe attributes and script, or unchanged
    when there is no offer view or no instant."""
    if not view or not expires or ATTR in page_html:
        return page_html
    tree = _Tree(page_html)
    tree.feed(page_html)
    tree.close()
    exp = html_mod.escape(str(expires), quote=True)
    marks, hidden, price_lines = {}, set(), set()   # marks: start-tag end offset -> attribute text
    codes = [c for c in (view.get("codes") or []) if c]
    strip = "|".join([f" with code {c}" for c in codes] + [f"with code {c}" for c in codes])
    sale_ctas = {_norm(t) for t in sale_ctas if _norm(t) and _norm(t) != _norm(default_cta)}
    body = [n for n in tree.nodes if n["body"] and not n["raw"]]
    for node in body:
        if node["tag"] == "a" and _text(node) in sale_ctas:
            marks[node["tag_end"]] = f' {ATTR}="{exp}" data-offer-cta="{html_mod.escape(default_cta, quote=True)}"'
            continue
        found = _price_line(node)
        if found:
            net, listed = found
            price_lines.add(id(node))
            marks[node["tag_end"]] = (f' {ATTR}="{exp}" data-offer-list="{html_mod.escape(listed, quote=True)}"'
                                      + (f' data-offer-net="{html_mod.escape(net, quote=True)}"' if net else "")
                                      + (f' data-offer-strip="{html_mod.escape(strip, quote=True)}"' if strip else ""))
    fact = _norm((view.get("check") or {}).get("fact")).lower()
    contains = [c for c in [_norm(view.get(k)).rstrip(".").lower() for k in ("line", "deadline", "cta_note", "bar")]
                + [f"with code {c.lower()}" for c in codes] + [fact] if c]
    name = _norm(view.get("name")).lower()

    def holds(node):   # the offer lines it holds; "name": a short line that starts with the sale's name (the bar)
        text = _text(node).lower()
        found = {c for c in contains if c in text}
        return found | ({"name"} if name and text.startswith(name) and len(text) <= 160 else set())

    def blocked(node):
        return node["has_a"] or node["tag"] in _HEADINGS or id(node) in price_lines

    for node in body:
        own = holds(node) if not blocked(node) else set()
        for child in node["children"]:
            own -= holds(child)
        if not own:   # the innermost element for at least one offer line
            continue
        up = node   # the outermost wrapper with the same text
        while up["parent"] is not None and not blocked(up["parent"]) and _text(up["parent"]) == _text(node):
            up = up["parent"]
        if fact and fact in _text(node).lower():   # the offer's checked claim: its list item
            li = up
            for _ in range(3):
                if li["tag"] == "li" or li["parent"] is None or blocked(li["parent"]):
                    break
                li = li["parent"]
            up = li if li["tag"] == "li" else up
        anc = up["parent"]
        while anc is not None and id(anc) not in hidden:
            anc = anc["parent"]
        if anc is None:
            hidden.add(id(up))
            marks.setdefault(up["tag_end"], f' {ATTR}="{exp}"')
    if not marks:
        return page_html
    out, last = [], 0
    for at in sorted(marks):
        cut = at - 2 if page_html[at - 2:at] == "/>" else at - 1
        out += [page_html[last:cut], marks[at], page_html[cut:at]]
        last = at
    out.append(page_html[last:])
    result = "".join(out)
    closing = result.lower().rfind("</body>")
    return (result[:closing] + SCRIPT + "\n" + result[closing:]) if closing != -1 else result + SCRIPT


def nosale_texts(twin_dir):
    """(the twin page's h1 text, its CTA text), each None when absent."""
    from pathlib import Path

    twin_dir = Path(twin_dir)
    index = twin_dir / "index.html"
    if not index.is_file():
        return None, None
    m = re.search(r"<h1\b[^>]*>(.*?)</h1>", index.read_text(), re.IGNORECASE | re.DOTALL)
    headline = _norm(re.sub(r"<[^>]+>", " ", m.group(1))) if m else None
    try:
        import json

        cta = _norm(json.loads((twin_dir / "page.json").read_text()).get("cta_text")) or None
    except (OSError, ValueError):
        cta = None
    return headline or None, cta


def add_nosale(page_html, headline, cta):
    """The marked page with the twin's texts: data-nosale-headline (and the
    instant) on the first h1, data-nosale-cta on every sale CTA. Unchanged
    without the fail-safe or without twin texts; never added twice."""
    m = re.search(ATTR + r'="([^"]+)"', page_html)
    if not m or not (headline or cta):
        return page_html
    expires = m.group(1)
    if headline:
        def h1(match):
            if "data-nosale-headline" in match.group(0):
                return match.group(0)
            extra = (f' {ATTR}="{expires}"' if ATTR not in match.group(0) else "") + \
                f' data-nosale-headline="{html_mod.escape(headline, quote=True)}"'
            return match.group(0)[:-1] + extra + ">"
        page_html = re.sub(r"<h1\b[^>]*>", h1, page_html, count=1, flags=re.IGNORECASE)
    if cta:
        def link(match):
            if "data-nosale-cta" in match.group(0):
                return match.group(0)
            return match.group(0)[:-1] + f' data-nosale-cta="{html_mod.escape(cta, quote=True)}">'
        page_html = re.sub(r'<a\b[^>]*data-offer-cta="[^"]*"[^>]*>', link, page_html)
    return page_html


def default_and_sale_ctas(schema, facts_pack, view):
    """(the default CTA text, the sale CTA texts) from the cartridge's
    schema.json allowed_cta_texts map."""
    from . import persuasion

    templates = (schema or {}).get("allowed_cta_texts") or []
    name = ((facts_pack or {}).get("product") or {}).get("name") or ""
    default = persuasion.cta_texts_for_angle(templates, "default", short_name=name)
    sale = persuasion.cta_texts_for_angle(templates, "sale", short_name=name, offer_amount=(view or {}).get("amount"))
    return (default[0] if default else ""), [t for t in sale if t not in default]
