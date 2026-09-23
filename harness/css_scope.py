"""Cycle 59: make an exported page body survive inside a storefront theme.

The export (harness/page_body.py) is injected into the theme's page template,
inside the theme's own container and `.rte` rich-text wrapper. Measured on
the live storefront (2026-09-22) the theme broke the page four ways:

1. the theme sets `html{font-size:10px}`, so every `rem` in our CSS rendered
   at 10/16 of its size;
2. the theme's element rules (`h1{font-family:...}`, `.rte ul{display:flex}`,
   `.rte img{...}`) beat or filled in for our rules, because our head
   stylesheets (harness/structure.css, the tenant's brand/base.css) never
   reached the storefront and a template's single-class rules lose to
   `.rte h1`-style selectors;
3. the theme printed its own page title above our headline;
4. the theme's container trapped the page at 960px (or left it
   left-aligned at the template's own max-width).

This module holds the deterministic CSS transforms that fix those, all
export-only (the review html is never touched):

- `rem_to_px` / `rem_to_px_in_style_attrs`: every `rem` length becomes px at
  a 16px base, so the theme's root font size no longer matters.
- `scope_css`: every selector gets `.adv-wrap.adv-wrap` in front (or merged
  into its first compound when that compound already targets the export's
  root element). Every rule gains exactly two classes of specificity, so the
  order of our own rules against each other is unchanged, and even our
  lowest rule (a bare `img{}`, now 0,2,1) ties or beats the theme's
  `.rte img` (0,1,1) / `.rte h1:first-child` (0,2,1) and wins on source
  order, since the body `<style>` comes after the theme's head sheets.
- `ISOLATION_RESET_CSS`: browser-default values for the elements a theme
  restyles, at `.adv-wrap.adv-wrap <type>` (0,2,1). Placed BEFORE the scoped
  page CSS, so any rule of ours (always at least 0,2,1, and later) wins.
- `STOREFRONT_FIT_CSS`: hides the theme's duplicate page title and makes the
  page full-bleed, page-scoped through `:has(.adv-wrap)` (the rules ship in
  the body `<style>`, so they only exist on a page that carries our wrapper).

Standard library only: the input is always this harness's own CSS (no
nesting, no `@import`), so a small tokenizer that respects strings,
comments and bracket depth is enough and no CSS parser dependency is added.
"""
import re

PREFIX = ".adv-wrap.adv-wrap"
ROOT_CLASS = "adv-wrap"

# ---------------------------------------------------------------------------
# rem -> px
# ---------------------------------------------------------------------------

# Comments, strings and url(...) are copied unchanged (a string ends at an
# unescaped newline, as in a browser, so an apostrophe in prose cannot
# swallow the rems after it); a number directly followed by
# `rem` (and not part of an identifier such as `.m-2rem`) is converted.
_REM_OR_SKIP_RE = re.compile(
    r"""(?P<skip>/\*.*?(?:\*/|\Z)|"(?:\\.|[^"\\\n])*"|'(?:\\.|[^'\\\n])*'|\burl\(\s*[^)]*\))"""
    r"""|(?<![\w.#-])(?P<sign>[+-]?)(?P<num>\d+(?:\.\d*)?(?:[eE][+-]?\d+)?|\.\d+(?:[eE][+-]?\d+)?)(?i:rem)(?![\w-])""",
    re.DOTALL,
)
REM_BASE_PX = 16


def _format_px(value):
    text = f"{value:.4f}".rstrip("0").rstrip(".")
    return "0" if text in ("-0", "") else text


def rem_to_px(css):
    """Every `rem` length in `css` -> px at a 16px root. `em`, `%`, `vw` and
    every other unit are untouched, as is anything inside a string or a
    `url(...)`. Works inside calc()/clamp()/min()/max()/var() fallbacks,
    since it converts the number token itself."""

    def _sub(match):
        if match.group("skip") is not None:
            return match.group("skip")
        value = float(match.group("num")) * REM_BASE_PX
        if match.group("sign") == "-":
            value = -value
        return _format_px(value) + "px"

    return _REM_OR_SKIP_RE.sub(_sub, css)


_SCRIPT_OR_STYLE_RE = re.compile(r"<(script|style)\b[^>]*>.*?</\1\s*>", re.IGNORECASE | re.DOTALL)
_STYLE_ATTR_RE = re.compile(r"""(\sstyle\s*=\s*)("[^"]*"|'[^']*')""", re.IGNORECASE)


def rem_to_px_in_style_attrs(html):
    """`rem_to_px` applied to every inline `style="..."` attribute value in
    `html`. Text content, `<script>` blocks and `<style>` blocks are copied
    unchanged (a `<style>` block is converted separately, as a whole)."""
    out, pos = [], 0
    for block in _SCRIPT_OR_STYLE_RE.finditer(html):
        out.append(_convert_attrs(html[pos : block.start()]))
        out.append(block.group(0))
        pos = block.end()
    out.append(_convert_attrs(html[pos:]))
    return "".join(out)


def _convert_attrs(fragment):
    # the value WITHOUT its quotes: rem_to_px leaves a quoted string alone
    return _STYLE_ATTR_RE.sub(
        lambda m: m.group(1) + m.group(2)[0] + rem_to_px(m.group(2)[1:-1]) + m.group(2)[-1], fragment
    )


# ---------------------------------------------------------------------------
# A small CSS tokenizer: top-level items of a stylesheet
# ---------------------------------------------------------------------------


def _skip_string(css, i):
    """Index just past the string starting at `css[i]`. As in a browser, an
    unescaped newline ends a string too (a "bad string"): an apostrophe in
    comment prose that a stray `*/` has pushed out of its comment must not
    swallow the stylesheet up to the next apostrophe."""
    quote = css[i]
    i += 1
    while i < len(css):
        if css[i] == "\\":
            i += 2
            continue
        if css[i] == quote:
            return i + 1
        if css[i] == "\n":
            return i
        i += 1
    return i


def _skip_comment(css, i):
    end = css.find("*/", i + 2)
    return len(css) if end == -1 else end + 2


def _find_block_end(css, i):
    """`css[i]` is `{`; index just past its matching `}`."""
    depth = 0
    while i < len(css):
        ch = css[i]
        if ch in "\"'":
            i = _skip_string(css, i)
            continue
        if css.startswith("/*", i):
            i = _skip_comment(css, i)
            continue
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return i + 1
        i += 1
    return len(css)


def parse_stylesheet(css):
    """Top-level items of `css`, in order, as tuples:
    ("comment", text) | ("statement", text) | ("block", prelude, body).
    A block's prelude is its selector list or at-rule prelude with comments
    removed; its body is the raw text between the braces."""
    items, i, n = [], 0, len(css)
    while i < n:
        if css[i].isspace():
            i += 1
            continue
        if css.startswith("/*", i):
            end = _skip_comment(css, i)
            items.append(("comment", css[i:end]))
            i = end
            continue
        if css.startswith("<!--", i) or css.startswith("-->", i):
            i += 4 if css.startswith("<!--", i) else 3
            continue
        start, prelude = i, []
        while i < n:
            ch = css[i]
            if ch in "\"'":
                end = _skip_string(css, i)
                prelude.append(css[i:end])
                i = end
                continue
            if css.startswith("/*", i):
                i = _skip_comment(css, i)
                prelude.append(" ")
                continue
            if ch in "{;":
                break
            if ch == "}":  # stray closer: drop it
                break
            prelude.append(ch)
            i += 1
        text = " ".join("".join(prelude).split())
        if i >= n:
            if text:
                items.append(("statement", css[start:]))
            break
        if css[i] == ";":
            items.append(("statement", text + ";"))
            i += 1
            continue
        if css[i] == "}":
            i += 1
            continue
        end = _find_block_end(css, i)
        items.append(("block", text, css[i + 1 : end - 1]))
        i = end
    return items


def _split_top_level(text, sep=","):
    parts, depth, buf, i = [], 0, [], 0
    while i < len(text):
        ch = text[i]
        if ch in "\"'":
            end = _skip_string(text, i)
            buf.append(text[i:end])
            i = end
            continue
        if ch == "\\" and i + 1 < len(text):
            buf.append(text[i : i + 2])
            i += 2
            continue
        if ch in "([":
            depth += 1
        elif ch in ")]":
            depth -= 1
        if ch == sep and depth == 0:
            parts.append("".join(buf))
            buf = []
        else:
            buf.append(ch)
        i += 1
    parts.append("".join(buf))
    return parts


# ---------------------------------------------------------------------------
# Selector scoping
# ---------------------------------------------------------------------------

_COMBINATORS = " >+~"
_TYPE_RE = re.compile(r"^(?:\*|[a-zA-Z][\w-]*|[\w*-]*\|[\w*-]+)")
_CLASS_RE = re.compile(r"\.(-?[_a-zA-Z][\w-]*)")


def _split_first_compound(selector):
    """(first_compound, rest): rest starts with the combinator (and any
    whitespace) that ended the first compound, or is ""."""
    depth, i = 0, 0
    while i < len(selector):
        ch = selector[i]
        if ch in "\"'":
            i = _skip_string(selector, i)
            continue
        if ch == "\\":
            i += 2
            continue
        if ch in "([":
            depth += 1
        elif ch in ")]":
            depth -= 1
        elif depth == 0 and ch in _COMBINATORS:
            return selector[:i], selector[i:]
        i += 1
    return selector, ""


def _top_level_classes(compound):
    """Class names in `compound` itself, not inside :has()/:not()/... args."""
    depth, flat = 0, []
    for ch in compound:
        if ch in "([":
            depth += 1
        elif ch in ")]":
            depth -= 1
        elif depth == 0:
            flat.append(ch)
            continue
        flat.append(" ")
    return set(_CLASS_RE.findall("".join(flat)))


def _type_of(compound):
    m = _TYPE_RE.match(compound)
    return m.group(0) if m else ""


def _merge_into_compound(compound):
    """PREFIX merged into `compound` (after its type selector, if any)."""
    type_sel = _type_of(compound)
    return type_sel + PREFIX + compound[len(type_sel) :]


def scope_selector(selector, root_classes=(ROOT_CLASS,)):
    """One complex selector -> the same selector with two extra classes of
    specificity, anchored on the export's root element (`.adv-wrap`).

    - `:root ...` is left alone (a custom-property block, not a component);
    - a leading `html`/`body` compound is the document the review page had;
      on a storefront that role is played by the root element, so it becomes
      PREFIX (keeping any class/pseudo it carried);
    - a first compound that names one of the root element's own classes
      (`.adv-wrap:has(.cmp)`, `.adv-case-upper .qz-h1`) targets the root
      itself: PREFIX is merged into that compound;
    - anything else is a descendant: `PREFIX <selector>`."""
    selector = selector.strip()
    if not selector or selector.startswith(":root"):
        return selector
    first, rest = _split_first_compound(selector)
    type_sel = _type_of(first).lower()
    # `html body .x` / `html > body .x`: drop the html compound first.
    if type_sel == "html" and first.lower() == "html":
        second, after = _split_first_compound(rest.lstrip(" >"))
        if _type_of(second).lower() == "body":
            first, rest, type_sel = second, after, "body"
    if type_sel in ("html", "body"):
        return PREFIX + first[len(type_sel) :] + rest
    if _top_level_classes(first) & set(root_classes):
        return _merge_into_compound(first) + rest
    return PREFIX + " " + selector


def _scope_prelude(prelude, root_classes):
    return ",".join(scope_selector(s, root_classes) for s in _split_top_level(prelude))


def _has_stray_slash(prelude):
    """True for a prelude no browser accepts as a selector: a `/` or a quote
    outside any bracket (a string is valid only inside `[attr="..."]` or a
    pseudo-class argument)."""
    depth, i = 0, 0
    while i < len(prelude):
        ch = prelude[i]
        if depth and ch in "\"'":
            i = _skip_string(prelude, i)
            continue
        if ch in "([":
            depth += 1
        elif ch in ")]":
            depth -= 1
        elif depth == 0 and ch in "/\"'":
            return True
        i += 1
    return False


# At-rules whose body is a list of style rules: recurse into them.
_RECURSE_AT_RULES = ("@media", "@supports", "@container", "@layer", "@document", "@-moz-document")


def scope_css(css, root_classes=(ROOT_CLASS,), drop=()):
    """Every style rule in `css` scoped by `scope_selector`. @media /
    @supports / @container / @layer blocks are recursed into; @font-face,
    @keyframes, @page, @property and other at-rules are copied verbatim, as
    are comments and statements. `drop` names top-level preludes to leave out
    entirely (e.g. ":root", "@font-face" -- carried separately)."""
    out = []
    for item in parse_stylesheet(css):
        kind = item[0]
        if kind == "comment":
            out.append(item[1])
            continue
        if kind == "statement":
            out.append(item[1])
            continue
        prelude, body = item[1], item[2]
        lowered = prelude.lower()
        if any(lowered == d or lowered.startswith(d + " ") or lowered.startswith(d + "{") for d in drop):
            continue
        if lowered.startswith("@"):
            if lowered.split(None, 1)[0].split("(")[0] in _RECURSE_AT_RULES:
                out.append(prelude + "{\n" + scope_css(body, root_classes) + "\n}")
            else:
                out.append(prelude + "{" + body + "}")
            continue
        if _has_stray_slash(prelude):
            # Not a selector a browser accepts (a `/` or quote outside a
            # bracket -- in practice the tail of a comment that a `*/` inside
            # it closed early): the browser drops this rule on the review
            # page, so the export drops it too instead of scoping garbage.
            continue
        out.append(_scope_prelude(prelude, root_classes) + "{" + body.strip() + "}")
    return "\n".join(out)


_ROOT_TAG_RE = re.compile(
    r"""<[a-zA-Z][\w-]*\b[^>]*?\bclass\s*=\s*(?:"([^"]*)"|'([^']*)')""", re.IGNORECASE
)


def root_classes_of(body_html):
    """The class list of the export's root element -- the first element whose
    class attribute carries `adv-wrap` (base.html's `<main class="adv-wrap
    ...">`). Always includes `adv-wrap` itself."""
    for m in _ROOT_TAG_RE.finditer(body_html):
        classes = (m.group(1) if m.group(1) is not None else m.group(2)).split()
        if ROOT_CLASS in classes:
            return tuple(dict.fromkeys([ROOT_CLASS] + classes))
    return (ROOT_CLASS,)


# ---------------------------------------------------------------------------
# The fixed layers
# ---------------------------------------------------------------------------

# Browser-default values for the elements a storefront theme restyles, so an
# element none of our rules sizes looks the way it does on the review page
# (browser defaults + our head sheets) instead of taking the theme's look.
# Every selector is `.adv-wrap.adv-wrap <type>` = (0,2,1): it beats the
# theme's `.rte h1` (0,1,1) and ties `.rte h1:first-child` (0,2,1), winning
# on source order; our own scoped rules (>= 0,2,1, and later) beat it.
ISOLATION_RESET_CSS = """\
/* storefront isolation reset (harness/css_scope.py): browser defaults for
   what a theme restyles; every rule of ours comes after and outranks it */
.adv-wrap.adv-wrap{box-sizing:border-box;font-size:16px;line-height:normal;font-style:normal;font-weight:400;letter-spacing:normal;word-spacing:normal;text-transform:none;text-align:left;text-indent:0;margin-top:0;margin-bottom:0}
.adv-wrap.adv-wrap h1,.adv-wrap.adv-wrap h2,.adv-wrap.adv-wrap h3,.adv-wrap.adv-wrap h4,.adv-wrap.adv-wrap h5,.adv-wrap.adv-wrap h6{font-family:inherit;font-weight:bold;font-style:normal;color:inherit;line-height:inherit;letter-spacing:inherit;text-transform:inherit;text-align:inherit;padding:0;border:0;background:none}
.adv-wrap.adv-wrap h1{font-size:2em;margin:.67em 0}
.adv-wrap.adv-wrap h2{font-size:1.5em;margin:.83em 0}
.adv-wrap.adv-wrap h3{font-size:1.17em;margin:1em 0}
.adv-wrap.adv-wrap h4{font-size:1em;margin:1.33em 0}
.adv-wrap.adv-wrap h5{font-size:.83em;margin:1.67em 0}
.adv-wrap.adv-wrap h6{font-size:.67em;margin:2.33em 0}
.adv-wrap.adv-wrap p{font-family:inherit;font-size:inherit;line-height:inherit;color:inherit;letter-spacing:inherit;text-transform:inherit;margin:1em 0;padding:0}
.adv-wrap.adv-wrap ul,.adv-wrap.adv-wrap ol{display:block;margin:1em 0;padding:0 0 0 40px;gap:normal;flex-flow:row nowrap;align-items:normal;justify-content:normal;list-style-position:outside;font-size:inherit;line-height:inherit;color:inherit}
.adv-wrap.adv-wrap ul{list-style-type:disc}
.adv-wrap.adv-wrap ol{list-style-type:decimal}
.adv-wrap.adv-wrap li{display:list-item;margin:0;padding:0;font-size:inherit;line-height:inherit;color:inherit;letter-spacing:inherit;text-transform:inherit}
.adv-wrap.adv-wrap li::before,.adv-wrap.adv-wrap li::after{content:none}
.adv-wrap.adv-wrap a{color:inherit;font-family:inherit;font-size:inherit;letter-spacing:inherit;text-transform:inherit;text-decoration:underline;text-underline-offset:auto;text-decoration-thickness:auto;background-image:none;border-bottom:0;box-shadow:none}
.adv-wrap.adv-wrap table{margin:0;width:auto;max-width:none;border:0;font-family:inherit;font-size:inherit;line-height:inherit;color:inherit;table-layout:auto}
.adv-wrap.adv-wrap th,.adv-wrap.adv-wrap td{padding:1px;border:0;background:transparent;font-family:inherit;font-size:inherit;line-height:inherit;color:inherit;letter-spacing:inherit;text-transform:inherit;text-align:inherit}
.adv-wrap.adv-wrap th{font-weight:bold;text-align:center}
.adv-wrap.adv-wrap img{margin:0;max-width:none;border:0;border-radius:0;box-shadow:none;float:none}
.adv-wrap.adv-wrap button,.adv-wrap.adv-wrap input,.adv-wrap.adv-wrap select,.adv-wrap.adv-wrap textarea{font-family:inherit;font-size:inherit;letter-spacing:inherit;text-transform:none;margin:0}
.adv-wrap.adv-wrap blockquote{margin:1em 40px;padding:0;border:0;background:none;font-family:inherit;font-size:inherit;font-style:normal;color:inherit;quotes:auto}
.adv-wrap.adv-wrap figure{margin:1em 40px;padding:0}"""

# Page-scoped theme fixes. The body <style> exists only on a page that
# carries our export, so these rules never reach any other storefront page.
#
# Full-bleed: where :has() works (Chrome/Edge 105+, Safari 15.4+, Firefox
# 121+), every ANCESTOR of .adv-wrap drops its width cap, side padding, side
# margin, clipping and transform, so .adv-wrap simply fills the page width
# at `width:auto` -- no 100vw, so no scrollbar-width overflow and no
# horizontal scrollbar, and the page is centred however the theme placed its
# container (the live theme centres a 960px box; the draft theme left-aligns
# a wider one). Dropping overflow/transform on the ancestors also keeps our
# sticky/fixed bars working (a transformed or clipping ancestor breaks them).
# Only where :has() is missing does .adv-wrap fall back to the classic
# `width:100vw; margin-inline:calc(50% - 50vw)` breakout. overflow-x:clip on
# .adv-wrap clips anything inside that runs wider than the page without
# creating a scroll container (so position:sticky still works).
STOREFRONT_FIT_CSS = """\
/* storefront fit (harness/css_scope.py): hide the theme's own page title
   and let the page run full-bleed -- page-scoped, see :has(.adv-wrap) */
body:has(.adv-wrap) :is(.page__title,.main-page-title,.page-title,.page-header__title,.page__header):not(.adv-wrap *){display:none!important}
h1:has(~ * > .adv-wrap):not(.adv-wrap *){display:none!important}
@supports selector(:has(*)){
body :has(.adv-wrap){max-width:none!important;width:auto!important;min-width:0!important;margin-left:0!important;margin-right:0!important;padding-left:0!important;padding-right:0!important;overflow:visible!important;transform:none!important;filter:none!important;contain:none!important;animation:none!important;opacity:1!important;grid-column:1/-1!important;align-self:stretch!important;justify-self:stretch!important}
}
.adv-wrap.adv-wrap{width:auto!important;max-width:none!important;margin-left:0!important;margin-right:0!important;overflow-x:clip}
@supports not selector(:has(*)){
.adv-wrap.adv-wrap{width:100vw!important;margin-left:calc(50% - 50vw)!important;margin-right:calc(50% - 50vw)!important}
}"""
