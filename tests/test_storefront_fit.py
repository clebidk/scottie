"""Cycle 59: the storefront export must survive the theme it is injected
into. Measured on the live storefront (peaksaunas.com/pages/listicle-test-12,
2026-09-22): the theme's `html{font-size:10px}` shrank every rem, its heading
rules replaced our fonts, it printed its own page title above our headline,
and its 960px container trapped the page. These tests pin the export-only
fixes in harness/css_scope.py and harness/page_body.py."""
import re
from pathlib import Path

import pytest

from harness import css_scope
from harness.css_scope import (
    ISOLATION_RESET_CSS,
    STOREFRONT_FIT_CSS,
    rem_to_px,
    rem_to_px_in_style_attrs,
    root_classes_of,
    scope_css,
    scope_selector,
)
from harness.page_body import build_shopify_body, font_face_css

REPO_ROOT = Path(__file__).resolve().parent.parent

# A rem unit in CSS text, outside strings (the same shape rem_to_px converts).
_CSS_REM_RE = re.compile(r"(?<![\w.#-])[+-]?(?:\d+(?:\.\d*)?|\.\d+)rem(?![\w-])", re.IGNORECASE)
_STRING_RE = re.compile(r""""(?:\\.|[^"\\])*"|'(?:\\.|[^'\\])*'""")


def _css_rems(css):
    return _CSS_REM_RE.findall(_STRING_RE.sub("", css))


# ---------------------------------------------------------------------------
# rem -> px
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "css, expected",
    [
        (".a{font-size:1.125rem}", ".a{font-size:18px}"),
        (".a{margin:-.5rem 0 -1rem}", ".a{margin:-8px 0 -16px}"),
        (".a{padding:0rem}", ".a{padding:0px}"),
        (".a{--x:var(--y,.75rem)}", ".a{--x:var(--y,12px)}"),
        (".a{width:calc(100% - 2.5rem)}", ".a{width:calc(100% - 40px)}"),
        (".a{font-size:clamp(1.75rem,4vw + 1rem,2.75rem)}", ".a{font-size:clamp(28px,4vw + 16px,44px)}"),
        (".a{font-size:0.8125rem;gap:0.3rem}", ".a{font-size:13px;gap:4.8px}"),
        (".a{top:+1rem}", ".a{top:16px}"),
        (".a{font-size:1REM}", ".a{font-size:16px}"),
    ],
)
def test_rem_to_px_converts_lengths(css, expected):
    assert rem_to_px(css) == expected


def test_rem_to_px_keeps_other_units_strings_urls_and_identifiers():
    css = (
        '.m-2rem{letter-spacing:-.01em;width:50%;height:10vh;font-size:1.2em}'
        '.a::before{content:"1rem"}.b{background:url(img/2rem.png)}'
    )
    assert rem_to_px(css) == css


def test_rem_to_px_in_style_attrs_converts_attributes_but_not_text_or_script():
    html = (
        '<div style="margin-top:1.5rem;padding:.25rem"><p>Leave 2rem of space.</p></div>'
        "<span style='gap:calc(1rem + 2px)'>x</span>"
        '<script>el.innerHTML = \'<i style="width:3rem"></i>\';</script>'
    )
    out = rem_to_px_in_style_attrs(html)
    assert 'style="margin-top:24px;padding:4px"' in out
    assert "style='gap:calc(16px + 2px)'" in out
    assert "<p>Leave 2rem of space.</p>" in out
    assert '<i style="width:3rem"></i>' in out  # script text is not CSS we own


# ---------------------------------------------------------------------------
# selector scoping
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "selector, expected",
    [
        (".qz-h1", ".adv-wrap.adv-wrap .qz-h1"),
        (".adv-quiz h2", ".adv-wrap.adv-wrap .adv-quiz h2"),
        ("img", ".adv-wrap.adv-wrap img"),
        ("*", ".adv-wrap.adv-wrap *"),
        (".a > .b + .c ~ .d", ".adv-wrap.adv-wrap .a > .b + .c ~ .d"),
        (".x::before", ".adv-wrap.adv-wrap .x::before"),
        (".adv-wrap:has(.cmp)", ".adv-wrap.adv-wrap.adv-wrap:has(.cmp)"),
        (".adv-wrap:has(.cmp) .adv-footer", ".adv-wrap.adv-wrap.adv-wrap:has(.cmp) .adv-footer"),
        (".adv-case-upper .qz-h1", ".adv-wrap.adv-wrap.adv-case-upper .qz-h1"),
        ("main.adv-wrap", "main.adv-wrap.adv-wrap.adv-wrap"),
        ("body", ".adv-wrap.adv-wrap"),
        ("html body .x", ".adv-wrap.adv-wrap .x"),
        ("html", ".adv-wrap.adv-wrap"),
        (":root", ":root"),
        (".q:not(.adv-wrap) a", ".adv-wrap.adv-wrap .q:not(.adv-wrap) a"),
        ('[data-x="a b"] .y', '.adv-wrap.adv-wrap [data-x="a b"] .y'),
    ],
)
def test_scope_selector(selector, expected):
    assert scope_selector(selector, ("adv-wrap", "adv-case-upper")) == expected


def test_scope_css_handles_lists_media_keyframes_font_face_and_comments():
    css = """
/* a comment, kept */
.a,.b:hover , h1{color:red}
@media (max-width:600px){.a{color:blue}.c,.d::after{content:"}"}}
@keyframes spin{from{transform:rotate(0)}to{transform:rotate(360deg)}}
@font-face{font-family:"X";src:url("x.woff2")}
:root{--k:1px}
.adv-wrap:has(.cmp){max-width:1120px}
"""
    out = scope_css(css)
    assert "/* a comment, kept */" in out
    assert ".adv-wrap.adv-wrap .a,.adv-wrap.adv-wrap .b:hover,.adv-wrap.adv-wrap h1{color:red}" in out
    assert "@media (max-width:600px){" in out
    assert ".adv-wrap.adv-wrap .a{color:blue}" in out
    assert '.adv-wrap.adv-wrap .c,.adv-wrap.adv-wrap .d::after{content:"}"}' in out
    assert "@keyframes spin{from{transform:rotate(0)}to{transform:rotate(360deg)}}" in out
    assert '@font-face{font-family:"X";src:url("x.woff2")}' in out
    assert ":root{--k:1px}" in out
    assert ".adv-wrap.adv-wrap.adv-wrap:has(.cmp){max-width:1120px}" in out


def test_scope_css_drops_a_rule_a_browser_would_drop():
    """harness/structure.css's first comment contains `cartridge/*/template`,
    whose `*/` ends the comment early; a browser then drops the garbage rule
    up to the next `{`. The scoper drops it too."""
    out = scope_css("/* see a/*/b for more, and (c). */ :root{--k:1px}\n.a{color:red}")
    assert "--k" not in out
    assert ".adv-wrap.adv-wrap .a{color:red}" in out


def test_an_apostrophe_pushed_out_of_a_comment_does_not_swallow_later_rules():
    """cartridges/listicle/looks/cards/template.html: a comment mentioning
    `.lst-*/.pk-*` closes early, leaving "this look's" outside it. A browser
    ends that bad string at the newline; so must the tokenizer. (The rule
    right after the garbage joins its selector, and the browser drops it on
    the review page as well -- the export drops it the same way.)"""
    css = "/* rules because .lst-*/.pk-* are this look's alone */\n.a{color:red}\n/* the image's ratio */\n.b{color:blue}"
    out = scope_css(css)
    assert "color:red" not in out
    assert ".adv-wrap.adv-wrap .b{color:blue}" in out


def test_scope_css_drop_leaves_out_root_and_font_face():
    out = scope_css(':root{--k:1px}@font-face{font-family:"X"}.a{color:red}', drop=(":root", "@font-face"))
    assert ":root" not in out and "@font-face" not in out
    assert ".adv-wrap.adv-wrap .a{color:red}" in out


def test_root_classes_of_reads_the_wrapper_element():
    body = '<style>.x{}</style><main class="adv-wrap adv-case-upper"><div class="adv-quiz">'
    assert root_classes_of(body) == ("adv-wrap", "adv-case-upper")
    assert root_classes_of("<div>no wrapper</div>") == ("adv-wrap",)


def _specificity(selector):
    """(ids, classes+attrs+pseudo-classes, types+pseudo-elements) for the
    simple selectors this harness writes (no :is/:where lists)."""
    sel = re.sub(r"\([^()]*\)", "", selector)
    ids = len(re.findall(r"#[\w-]+", sel))
    pseudo_el = len(re.findall(r"::[\w-]+", sel))
    sel = re.sub(r"::[\w-]+", "", sel)
    classes = len(re.findall(r"\.[\w-]+|\[[^\]]*\]|:[\w-]+", sel))
    types = len(re.findall(r"(?:^|[\s>+~])([a-zA-Z][\w-]*)", sel))
    return (ids, classes, types + pseudo_el)


def test_scoped_rules_outrank_theme_rules_and_the_reset():
    theme = [".rte h1", ".rte ul", ".rte a", ".rte img", ".rte--page p", "h1"]
    theme_max = max(_specificity(s) for s in theme)
    for element in ("h1", "ul", "a", "img", "p"):
        reset = _specificity(".adv-wrap.adv-wrap " + element)
        assert re.search(r"\.adv-wrap\.adv-wrap " + element + r"\b", ISOLATION_RESET_CSS)
        assert reset >= theme_max  # the reset ties or beats the theme ...
        for ours in (element, ".x " + element, ".qz-" + element):
            # ... and every scoped rule of ours ties or beats the reset for
            # the same element (and comes after it, so it wins a tie).
            assert _specificity(scope_selector(ours)) >= reset, ours
    assert _specificity(scope_selector("li::before")) >= _specificity(".adv-wrap.adv-wrap li::before")


def test_isolation_reset_covers_the_elements_a_theme_restyles():
    for element in ("h1", "h2", "h3", "h4", "h5", "h6", "p", "ul", "ol", "li", "a", "table", "th", "td",
                    "img", "button", "input", "blockquote"):
        assert re.search(r"\.adv-wrap\.adv-wrap " + element + r"\b", ISOLATION_RESET_CSS), element
    heading_rule = re.search(r"\.adv-wrap\.adv-wrap h1,[^{]*\{([^}]*)\}", ISOLATION_RESET_CSS).group(1)
    for prop in ("font-family:inherit", "letter-spacing:inherit", "text-transform:inherit", "color:inherit"):
        assert prop in heading_rule
    assert "list-style-type:disc" in ISOLATION_RESET_CSS
    # the live theme's `.rte ul{display:flex;flex-flow:column}`: a template
    # that sets display:flex but no direction must still get a row
    assert "flex-flow:row nowrap" in ISOLATION_RESET_CSS
    assert "rem" not in ISOLATION_RESET_CSS


def test_fit_css_hides_the_theme_title_and_goes_full_bleed():
    assert re.search(r"\.page__title[^{]*\{display:none!important\}", STOREFRONT_FIT_CSS)
    for title in (".main-page-title", ".page-title"):
        assert title in STOREFRONT_FIT_CSS
    ancestors = re.search(r"body :has\(\.adv-wrap\)\{([^}]*)\}", STOREFRONT_FIT_CSS).group(1)
    for prop in ("max-width:none!important", "padding-left:0!important", "overflow:visible!important",
                 "transform:none!important"):
        assert prop in ancestors
    assert "max-width:none!important" in STOREFRONT_FIT_CSS.split(".adv-wrap.adv-wrap{", 1)[1]
    assert "overflow-x:clip" in STOREFRONT_FIT_CSS
    # the 100vw breakout is the no-:has() fallback only
    fallback = STOREFRONT_FIT_CSS.split("@supports not selector(:has(*)){", 1)[1]
    assert "width:100vw" in fallback and "calc(50% - 50vw)" in fallback


# ---------------------------------------------------------------------------
# the export, end to end
# ---------------------------------------------------------------------------
_REVIEW_DOC = """<!doctype html><html><head><title>t</title>
<style>:root{--adv-fg:#111}
* { box-sizing: border-box; }
body{margin:0;line-height:1.55}
h1 { font-size: 2rem; margin: 0 0 8px; }
.adv-wrap{max-width:760px;margin:0 auto}</style>
<style>@font-face{font-family:"Epika";src:url("brand/fonts/Epika-Regular.woff2")}
:root{--ps-accent:#f27046}
h1,h2{font-family:var(--ps-sans)}</style>
</head><body><main class="adv-wrap adv-case-upper">
<style>.qz-h1{font-size:2.25rem;margin:0 0 .875rem}
@media (min-width:900px){.qz-h1{font-size:2.75rem}}</style>
<div class="adv-quiz"><h1 class="qz-h1" style="margin-bottom:.5rem">Headline with 2rem in text</h1>
<p>Body</p></div></main>
<script>var s = "3rem";</script>
</body></html>"""


def _export(tmp_path, doc=_REVIEW_DOC):
    cartridge_dir = tmp_path / "quiz"
    cartridge_dir.mkdir()
    (cartridge_dir / "index.html").write_text(doc)
    html, _ = build_shopify_body(cartridge_dir)
    return html


def _style_block(html):
    return re.search(r"<style>(.*?)</style>", html, re.S).group(1)


def test_export_has_no_rem_in_css_but_keeps_text_and_script(tmp_path):
    html = _export(tmp_path)
    assert _css_rems(_style_block(html)) == []
    assert 'style="margin-bottom:8px"' in html
    assert "Headline with 2rem in text" in html
    assert 'var s = "3rem";' in html
    assert ".adv-wrap.adv-wrap .qz-h1{font-size:36px;margin:0 0 14px}" in html
    assert ".adv-wrap.adv-wrap .qz-h1{font-size:44px}" in html


def test_export_carries_the_review_head_stylesheets_scoped(tmp_path):
    """The review page is styled by structure.css + brand/base.css in its
    HEAD; a storefront keeps only the body, so those rules travel scoped to
    the wrapper (a theme's h1 font no longer wins over ours)."""
    css = _style_block(_export(tmp_path))
    assert ".adv-wrap.adv-wrap h1{font-size: 32px; margin: 0 0 8px;}" in css
    assert ".adv-wrap.adv-wrap h1,.adv-wrap.adv-wrap h2{font-family:var(--ps-sans)}" in css
    assert ".adv-wrap.adv-wrap.adv-wrap{max-width:760px;margin:0 auto}" in css
    # body -> the wrapper, and never a bare theme-wide rule
    assert re.search(r"(^|\n)\.adv-wrap\.adv-wrap\{margin:0;line-height:1.55\}", css)
    assert not re.search(r"(^|[\n}])\s*(body|h1|\*)\s*[{,]", css)
    # :root / @font-face are not duplicated from the head (token_css and
    # font_face_css carry them); the head's font url is not re-emitted
    assert css.count("@font-face") == font_face_css().count("@font-face")
    assert ":root" not in css


def test_export_layers_are_in_cascade_order(tmp_path):
    css = _style_block(_export(tmp_path))
    reset = css.index("storefront isolation reset")
    fit = css.index("storefront fit")
    head = css.index(".adv-wrap.adv-wrap h1,.adv-wrap.adv-wrap h2{font-family")
    page = css.index(".adv-wrap.adv-wrap .qz-h1{")
    assert fit < reset < head < page


def test_export_hides_the_theme_title_and_is_full_bleed(tmp_path):
    css = _style_block(_export(tmp_path))
    assert STOREFRONT_FIT_CSS in css
    assert "{display:none!important}" in css
    assert "body :has(.adv-wrap){" in css


def test_review_html_is_untouched(tmp_path):
    _export(tmp_path)
    assert (tmp_path / "quiz" / "index.html").read_text() == _REVIEW_DOC


def _split_top_level_commas(selector_list):
    """Split a selector list on commas outside parentheses, so
    `.x :is(h1,h2)` stays one selector."""
    parts, depth, start = [], 0, 0
    for i, ch in enumerate(selector_list):
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
        elif ch == "," and depth == 0:
            parts.append(selector_list[start:i])
            start = i + 1
    parts.append(selector_list[start:])
    return parts


# Every cartridge type, rendered from the canned fake-run pages the eval
# harness already builds, exports with no rem in its CSS.
_CARTRIDGE_TEMPLATES = sorted(
    p for p in (REPO_ROOT / "cartridges").glob("**/template.html")
)


@pytest.mark.parametrize("template", _CARTRIDGE_TEMPLATES, ids=lambda p: str(p.relative_to(REPO_ROOT / "cartridges")))
def test_every_cartridge_template_css_exports_without_rem(tmp_path, template):
    """Each template's own <style> CSS (plus every block.css a template can
    include), wrapped as a review document and exported: no rem survives,
    and every style rule is scoped to the wrapper."""
    source = template.read_text()
    styles = [re.sub(r"\{%.*?%\}|\{\{.*?\}\}", "", s, flags=re.S)
              for s in re.findall(r"<style\b[^>]*>(.*?)</style>", source, re.S | re.I)]
    styles += [p.read_text() for p in sorted((REPO_ROOT / "harness" / "blocks").glob("*/block.css"))]
    doc = (
        "<!doctype html><html><head><style>"
        + (REPO_ROOT / "harness" / "structure.css").read_text()
        + "</style></head><body><main class=\"adv-wrap\"><style>"
        + "\n".join(styles)
        + "</style><p>x</p></main></body></html>"
    )
    css = _style_block(_export(tmp_path, doc))
    assert _css_rems(css) == [], template
    page_css = css[css.index("/* storefront isolation reset"):]
    for item in css_scope.parse_stylesheet(page_css):
        if item[0] == "block" and not item[1].startswith("@"):
            for sel in _split_top_level_commas(item[1]):
                assert sel.startswith(".adv-wrap.adv-wrap") or re.match(r"[a-z]+\.adv-wrap", sel), sel
