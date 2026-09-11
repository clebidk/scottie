"""Small helpers shared by more than one engine module.

Everything here existed already, two to four times over, in modules that had no
reason to agree with each other by accident:

    NON_PROSE_KEYS      harness/cli.py and harness/claims.py, identical copies
    DOLLAR_AMOUNT_RE    harness/claims.py and harness/ground.py, identical
    product_name_slug   harness/ground.py, harness/prices.py (twice), and
                        harness/pdp_claims.py, written out longhand each time

Nothing company-specific belongs in this module.
"""
import re

# page.json keys holding structural/reference data rather than prose the writer
# composed. A product URL or an asset id can legitimately contain a term that
# would be forbidden in copy, and a claim_ids list legitimately contains
# id-shaped tokens -- neither is writer-composed text, so every prose scan
# (forbidden terms, leaked claim ids, warranty wording) and the word count skip
# these keys.
NON_PROSE_KEYS = frozenset({"url", "cta_url", "asset_id", "claim_ids", "claim_id", "id", "sku"})

# "$8,250" / "$ 8250.00" -- the dollar figure itself is group 1, without the
# sign, so a caller can float() it after stripping commas.
DOLLAR_AMOUNT_RE = re.compile(r"\$\s?([\d,]+(?:\.\d+)?)")


def product_name_slug(name):
    """A product's `name` as the slug its claim ids are namespaced under, e.g.
    "Model Two" -> "model-two". This is the rule that ties `price-<slug>`,
    `spec-<slug>-*`, and `pdp-<slug>-*` together; it must stay identical
    everywhere or a claim silently stops resolving."""
    return (name or "").lower().replace(" ", "-")


# ---------------------------------------------------------------------------
# Review 2026-09-11 R11: one page.json walker for the whole engine. The
# recursive walk was hand-written ten times in claims.py alone (plus
# render.py, repair.py, pagechecks.py), each re-deriving its path formatting
# and skip rules. The copies that "differ" differ only in their skip_keys --
# which is a parameter here, not a reason to re-write the recursion.
#
# revise.py's _apply_cut is deliberately NOT unified into this: it is a
# transform (returns a new tree, drops emptied items), not a read-only visit.
# ---------------------------------------------------------------------------

def walk_page(node, path="$", *, skip_keys=()):
    """Yield (path, node) for every node in a page.json-shaped tree,
    pre-order, the root ("$") included. `skip_keys` is a collection of dict
    keys whose values are never descended into (e.g. NON_PROSE_KEYS for a
    prose-only scan). Paths look like "$.body_sections[0].paragraphs[1].text"
    -- the same formatting every hand-written copy used."""
    yield path, node
    if isinstance(node, dict):
        for k, v in node.items():
            if k in skip_keys:
                continue
            yield from walk_page(v, f"{path}.{k}", skip_keys=skip_keys)
    elif isinstance(node, list):
        for i, v in enumerate(node):
            yield from walk_page(v, f"{path}[{i}]", skip_keys=skip_keys)


_PATH_KEY_RE = re.compile(r"\.([^.\[\]]+)")


def path_keys(path):
    """The dict-key segments of a walk_page path ("$.a.b[0].c" ->
    ["a", "b", "c"]; list indices are not keys). For checks whose rule
    depends on an ancestor key, e.g. "inside a quotes container"."""
    return _PATH_KEY_RE.findall(path)


# ---------------------------------------------------------------------------
# Path safety (Cycle 22 finding R36)
#
# Two places build a filename out of a value this harness does not control: a
# Drive download uses the remote server's Content-Disposition header, and an
# asset file uses an id and an extension taken from a URL. Neither was checked,
# so "../../.." in either escaped the run directory.
# ---------------------------------------------------------------------------

_UNSAFE_FILENAME_CHARS = re.compile(r"[^A-Za-z0-9._-]+")


def safe_filename(name, *, fallback="file"):
    """`name` reduced to a single, harmless path component.

    Drops any directory part, collapses everything outside [A-Za-z0-9._-] to a
    hyphen, and refuses a name that is empty or made only of dots -- so
    "../../etc/passwd" becomes "passwd", "..", "." and "" become `fallback`,
    and an ordinary "IMG_3988.JPG" is returned unchanged."""
    # Both separators, whatever the platform: a header written on Windows can
    # carry a backslash even when this is running on Linux.
    base = str(name or "").replace("\\", "/").rsplit("/", 1)[-1]
    base = _UNSAFE_FILENAME_CHARS.sub("-", base).strip("-")
    if not base or set(base) <= {"."}:
        return fallback
    return base


# A tenant name becomes a directory under tenants/, so it has to be one path
# component and nothing else -- `--tenant ../../x` would otherwise read a
# tenant.yaml from outside the repo, and `harness tenant init ../evil` would
# copy the template outside it.
TENANT_NAME_RE = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9._-]*$")


def is_safe_tenant_name(name):
    return bool(name) and ".." not in name and bool(TENANT_NAME_RE.match(name))
