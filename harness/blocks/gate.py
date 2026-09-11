"""The block gate (docs/KIMI-LONG-RUN.md phase 3, correction 4):
deterministic checks every registered block must pass. No model judge --
a model judge is allowed only once human scores exist to calibrate it
against, and none do.

Per the plan, every block is checked for: tenant-neutral words, CSS
variables only (no hard-coded colors), no fixed widths over 390 px, motion
via CSS and the existing scroll observer (so: no scripts, no inline event
handlers), every image sized, license field present, screenshot present.
Plus the two structural rules that make blocks composable at all: the
registry entry is complete and its files exist, and block.html carries no
literal copy (blocks are layout, never copy -- correction 2).

`validate_registry(tenant_words=...)` returns a list of problem strings;
empty means every block passes. The suite runs it over the real registry
with the real tenant word lists (tests/test_blocks.py) and over planted-bad
registries under tmp_path.
"""
import re
from pathlib import Path

from . import BLOCKS_DIR, CATEGORIES, load_registry

_KEBAB_RE = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")
_SCRIPT_RE = re.compile(r"<\s*script", re.IGNORECASE)
_HANDLER_RE = re.compile(r"\son[a-z]+\s*=", re.IGNORECASE)
_IMG_RE = re.compile(r"<img\b[^>]*>", re.IGNORECASE)
_HTML_COMMENT_RE = re.compile(r"<!--.*?-->", re.DOTALL)
_JINJA_RE = re.compile(r"\{#.*?#\}|\{\{.*?\}\}|\{%.*?%\}", re.DOTALL)
_TAG_RE = re.compile(r"<[^>]+>")
_ENTITY_RE = re.compile(r"&[a-zA-Z#0-9]+;")
_WORD_RE = re.compile(r"[A-Za-z]{3,}")
_CSS_COMMENT_RE = re.compile(r"/\*.*?\*/", re.DOTALL)
_HEX_COLOR_RE = re.compile(r"#[0-9a-fA-F]{3,8}\b")
_CSS_COLOR_FN_RE = re.compile(r"\b(?:rgba?|hsla?)\s*\(", re.IGNORECASE)
_CSS_COLOR_WORD_RE = re.compile(
    r"\b(?:black|white|red|blue|green|gray|grey|orange|purple|pink|yellow|brown)\b", re.IGNORECASE
)
# A width-ish declaration with a px value, e.g. `max-width: 720px`. The 390px
# ceiling is the plan's mobile rule: no block may assume a viewport wider
# than a phone.
_CSS_PX_WIDTH_RE = re.compile(
    r"\b(?:min-width|max-width|width|flex-basis)\s*:\s*[^;]*?(\d+(?:\.\d+)?)px", re.IGNORECASE
)
# `@media (max-width: 480px)` preludes are breakpoint conditions, not width
# declarations -- stripped before declarations are scanned.
_CSS_MEDIA_PRELUDE_RE = re.compile(r"@media[^{}]*", re.IGNORECASE)
_CSS_DECLARATION_RE = re.compile(r"([\w-]+)\s*:\s*([^;{}]+)")
MAX_FIXED_WIDTH_PX = 390

_META_REQUIRED = ("license", "tenant_neutral", "mobile_rules", "motion", "screenshot", "sections", "bindings")


def _literal_copy_words(html):
    """Text remaining after stripping comments, Jinja, tags and entities --
    a block may bind content through placeholders, but may not contain copy.
    Returns the offending words."""
    text = _HTML_COMMENT_RE.sub("", html)
    text = _JINJA_RE.sub("", text)
    text = _TAG_RE.sub(" ", text)
    text = _ENTITY_RE.sub(" ", text)
    return _WORD_RE.findall(text)


def validate_block(entry, block_dir, *, tenant_words=(), root=None):
    """Problems for one registered block (empty = pass). `root` is the
    directory registry paths resolve against (the real BLOCKS_DIR unless a
    fixture registry under test says otherwise)."""
    root = Path(root) if root else BLOCKS_DIR
    problems = []
    name = entry.get("name")
    label = name or "<unnamed>"

    if not name or not _KEBAB_RE.match(name):
        problems.append(f"{label}: name must be kebab-case")
    if entry.get("type") != "registry:block":
        problems.append(f"{label}: type must be 'registry:block'")
    if entry.get("category") not in CATEGORIES:
        problems.append(f"{label}: category must be one of {CATEGORIES}")
    if not (entry.get("title") or "").strip():
        problems.append(f"{label}: title is required")
    if not (entry.get("description") or "").strip():
        problems.append(f"{label}: description is required")
    if not isinstance(entry.get("dependencies"), list):
        problems.append(f"{label}: dependencies must be a list")

    meta = entry.get("meta")
    if not isinstance(meta, dict):
        problems.append(f"{label}: meta is required")
        meta = {}
    for key in _META_REQUIRED:
        if key not in meta:
            problems.append(f"{label}: meta.{key} is required")
    if "license" in meta and not str(meta["license"]).strip():
        problems.append(f"{label}: meta.license must be non-empty")
    if meta.get("tenant_neutral") is not True:
        problems.append(f"{label}: meta.tenant_neutral must be true")
    if not isinstance(meta.get("sections"), list) or not meta.get("sections"):
        problems.append(f"{label}: meta.sections must be a non-empty list")
    if not isinstance(meta.get("bindings"), dict) or not meta.get("bindings"):
        problems.append(f"{label}: meta.bindings must be a non-empty object")

    files = entry.get("files")
    if not isinstance(files, list) or not files:
        problems.append(f"{label}: files must be a non-empty list")
        files = []
    for f in files:
        fpath = f.get("path") if isinstance(f, dict) else None
        if not fpath:
            problems.append(f"{label}: every files entry needs a path")
            continue
        if name and not fpath.startswith(f"{name}/"):
            problems.append(f"{label}: file {fpath!r} is not under the block's own directory")
        if not (root / fpath).is_file():
            problems.append(f"{label}: file {fpath!r} does not exist")

    styling = meta.get("styling", "self")
    if styling not in ("self", "structure"):
        problems.append(f"{label}: meta.styling must be 'self' or 'structure'")

    screenshot = meta.get("screenshot")
    if screenshot:
        shot = root / screenshot
        if shot.suffix != ".svg" or not shot.is_file() or shot.stat().st_size == 0:
            problems.append(f"{label}: screenshot {screenshot!r} must be a non-empty .svg on disk")

    block_html = block_dir / "block.html" if name else None
    if not name or block_html is None or not block_html.is_file():
        problems.append(f"{label}: block.html is missing")
        return problems
    html = block_html.read_text()

    if _SCRIPT_RE.search(html):
        problems.append(f"{label}: block.html must not contain <script> -- motion is CSS-only")
    if _HANDLER_RE.search(html):
        problems.append(f"{label}: block.html must not use inline event handlers")
    for img in _IMG_RE.findall(html):
        if "width=" not in img or "height=" not in img:
            problems.append(f"{label}: every <img> must be sized (width and height attributes): {img.strip()[:80]}")
    copy_words = _literal_copy_words(html)
    if copy_words:
        problems.append(f"{label}: block.html carries literal copy {sorted(set(copy_words))[:5]} -- blocks are layout, never copy")
    for word in tenant_words:
        if re.search(r"\b" + re.escape(word) + r"\b", html, re.IGNORECASE):
            problems.append(f"{label}: block.html names a tenant word {word!r}")

    css_path = block_dir / "block.css"
    if styling == "self" and not css_path.is_file():
        problems.append(f"{label}: meta.styling is 'self' but block.css is missing")
    if css_path.is_file():
        css = _CSS_COMMENT_RE.sub("", css_path.read_text())
        if _HEX_COLOR_RE.search(css) or _CSS_COLOR_FN_RE.search(css):
            problems.append(f"{label}: block.css must use CSS variables for colors (var(--...)), never literals")
        # Color keywords are only color literals when they appear in a
        # declaration's VALUE -- `white-space: nowrap` is not a color.
        for _prop, value in _CSS_DECLARATION_RE.findall(css):
            if _CSS_COLOR_WORD_RE.search(value):
                problems.append(f"{label}: block.css must use CSS variables for colors (var(--...)), never literals")
                break
        for m in _CSS_PX_WIDTH_RE.finditer(_CSS_MEDIA_PRELUDE_RE.sub("", css)):
            if float(m.group(1)) > MAX_FIXED_WIDTH_PX:
                problems.append(f"{label}: block.css fixes a width over {MAX_FIXED_WIDTH_PX}px: {m.group(0).strip()}")
        if styling == "self":
            for selector in re.findall(r"([^{}]+)\{", css):
                selector = selector.strip()
                if not selector or selector.startswith(("@", "from", "to", "%")):
                    continue
                if f".bk-{name}" not in selector:
                    problems.append(f"{label}: block.css selector escapes the block's scope: {selector[:60]}")
    return problems


def validate_registry(path=None, *, tenant_words=(), min_blocks=None, max_blocks=None):
    """Problems for the whole registry (empty = every block passes). The
    20-30 block count the plan calls for is enforced by the suite running
    this against the real registry with min_blocks/max_blocks set; planted
    fixture registries leave both unset."""
    problems = []
    try:
        data = load_registry(path)
    except (OSError, ValueError) as e:
        return [f"registry: {e}"]
    root = Path(path).parent if path else BLOCKS_DIR
    items = data["items"]
    if min_blocks is not None and len(items) < min_blocks:
        problems.append(f"registry: {len(items)} blocks registered; the plan calls for {min_blocks}-{max_blocks}")
    if max_blocks is not None and len(items) > max_blocks:
        problems.append(f"registry: {len(items)} blocks registered; the plan calls for {min_blocks}-{max_blocks}")
    seen = set()
    for entry in items:
        name = entry.get("name") if isinstance(entry, dict) else None
        if name in seen:
            problems.append(f"{name or '<unnamed>'}: duplicate registry entry")
        seen.add(name)
        if not isinstance(entry, dict):
            problems.append("registry: every item must be an object")
            continue
        block_dir = root / (name or "")
        problems += validate_block(entry, block_dir, tenant_words=tenant_words, root=root)
    return problems
