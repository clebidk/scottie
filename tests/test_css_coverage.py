"""Fix cycle 23: harness/structure.css (renamed from fallback.css) now
always loads before a tenant's own base.css, as the layer every cartridge
template's structural classes are defined against. This asserts that
promise directly: every class any cartridge/*/template.html uses -- other
than a class the template's own inline <style> block already defines
itself (listicle ships a fully self-contained design system, deliberately;
see its own header comment) -- has at least one matching selector in
structure.css.
"""
import re

from harness.tenant import REPO_ROOT

STRUCTURE_CSS = (REPO_ROOT / "harness" / "structure.css").read_text()
CARTRIDGES_DIR = REPO_ROOT / "cartridges"

# Matched as two alternatives (not one char class accepting either quote)
# so a double-quoted attribute whose value happens to contain a single quote
# -- e.g. listicle's class="pk-item {{ 'pk-item--left' if ... }}" -- doesn't
# get truncated at that inner quote.
_CLASS_ATTR_RE = re.compile(r'class="([^"]*)"|class=\'([^\']*)\'')
_JINJA_EXPR_RE = re.compile(r"\{\{.*?\}\}", re.DOTALL)
_INLINE_STYLE_RE = re.compile(r"<style>(.*?)</style>", re.DOTALL)
_CSS_CLASS_SELECTOR_RE = re.compile(r"\.([a-zA-Z0-9_-]+)")


def _classes_used_in_template(text):
    """Static (non-Jinja-computed) class names a template's markup uses."""
    classes = set()
    for double, single in _CLASS_ATTR_RE.findall(text):
        attr_value = double or single
        literal = _JINJA_EXPR_RE.sub(" ", attr_value)
        classes.update(tok for tok in literal.split() if tok)
    return classes


def _classes_self_defined(text):
    """Class selectors a template's own inline <style> block (if any)
    defines for itself -- e.g. listicle's scoped .pk-* design system."""
    classes = set()
    for style_block in _INLINE_STYLE_RE.findall(text):
        classes.update(_CSS_CLASS_SELECTOR_RE.findall(style_block))
    return classes


def _has_structure_rule(class_name):
    # The dot is the anchor: a literal "." immediately followed by the class
    # name (and not by more name/dash characters, so "adv-cta" doesn't match
    # inside "adv-ctas") is a real selector for it -- whatever precedes the
    # dot (nothing, a combinator, or a type selector like "table.adv-specs")
    # doesn't matter.
    pattern = re.compile(r"\." + re.escape(class_name) + r"(?![\w-])")
    return bool(pattern.search(STRUCTURE_CSS))


def _cartridge_templates():
    """Every cartridge template, including the listicle cartridge's five
    per-look templates (cycle 51), which carry the same promise: a class a
    template's markup uses is either defined in that template's own inline
    <style> or has a rule in structure.css."""
    return sorted(CARTRIDGES_DIR.glob("*/template.html")) + sorted(
        CARTRIDGES_DIR.glob("*/looks/*/template.html")
    )


def test_every_cartridge_has_a_template():
    templates = _cartridge_templates()
    names = {p.parent.name for p in templates}
    assert {"article", "listicle", "longform", "product-page", "quiz"} <= names
    # cycle 51: the listicle cartridge's five looks are templates too
    assert {"editorial", "cards", "pillars", "scorecard", "lander"} <= names


def test_every_template_class_not_self_styled_has_a_structure_css_rule():
    missing = []
    for template_path in _cartridge_templates():
        text = template_path.read_text()
        used = _classes_used_in_template(text)
        self_defined = _classes_self_defined(text)
        needs_coverage = used - self_defined
        for cls in sorted(needs_coverage):
            if not _has_structure_rule(cls):
                missing.append(f"{template_path.relative_to(REPO_ROOT)}: .{cls}")
    assert missing == [], "class(es) with no structure.css rule:\n" + "\n".join(missing)


def test_generic_img_max_width_rule_present():
    assert re.search(r"\bimg\s*\{[^}]*max-width:\s*100%", STRUCTURE_CSS)
    assert re.search(r"\bimg\s*\{[^}]*height:\s*auto", STRUCTURE_CSS)


def test_sticky_cta_has_a_base_and_a_mobile_rule():
    assert _has_structure_rule("adv-sticky-cta")
    mobile_block = STRUCTURE_CSS.split("max-width: 480px")[-1]
    assert ".adv-sticky-cta" in mobile_block


def test_mobile_block_covers_hero_tables_and_proof_rows():
    mobile_block = STRUCTURE_CSS.split("max-width: 480px")[-1]
    assert ".adv-hero" in mobile_block
    assert "table.adv-specs" in mobile_block
    assert "overflow-x: auto" in mobile_block
    assert ".adv-proof-stats" in mobile_block or ".adv-trust-strip" in mobile_block
