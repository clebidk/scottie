"""Cycle 74: patch repairs for the listicle -- the repair call edits only the
fields the gate flagged, instead of rewriting the whole page.

Why: a repair used to be a full page rewrite. On the 2026-10-05 listicle runs
the last full rewrite often fixed the flagged sentence and broke a sentence
that had passed before (a new uncited number, a new unfaithful quote): 5 of the
8 STOPs failed on a field that attempt 1 had right. A patch cannot touch a
field nobody flagged, and its output is a few hundred tokens instead of a
3,000-token page (cheaper, and far less room for invalid JSON).

The writer returns {"edits": [{"path": "$.reasons[2].proof", "value": ...}]}.
Each path must be one of the edit roots the repair loop allows (the flagged
field's own list item, e.g. "$.reasons[2]" for "$.reasons[2].proof.text",
plus the headline) or a path inside one. The value replaces the node at that
path. The caller validates the patched page against the schema and runs every
gate on it again, exactly as for a full rewrite.
"""
import copy
import re

_PATH_RE = re.compile(r"^\$((?:\.[A-Za-z_][A-Za-z0-9_]*|\[\d+\])*)$")
_SEGMENT_RE = re.compile(r"\.([A-Za-z_][A-Za-z0-9_]*)|\[(\d+)\]")
_FIRST_INDEX_RE = re.compile(r"^(\$[^\[]*\[\d+\])")

# A field the writer may always edit in a patch: the headline's N has to
# follow an item-count change, and the dek is the headline's own line.
ALWAYS_EDITABLE = ("$.headline", "$.dek")


class PatchError(ValueError):
    """An edit list that cannot be applied: bad shape, a path outside the
    allowed roots, or a path that does not resolve."""


def segments(path):
    """["reasons", 2, "proof"] for "$.reasons[2].proof"; PatchError when the
    path is not of that form."""
    if not isinstance(path, str) or not _PATH_RE.match(path):
        raise PatchError(f"bad path {path!r}")
    return [int(i) if i else k for k, i in _SEGMENT_RE.findall(path)]


def edit_root(path):
    """The node a patch for a failure at `path` may replace: everything up
    to the first list index ("$.reasons[2].image.asset_id" -> "$.reasons[2]",
    "$.faq.questions[1].answer" -> "$.faq.questions[1]"), else the path
    itself ("$.headline", "$.closing.warranty_line")."""
    m = _FIRST_INDEX_RE.match(path or "")
    return m.group(1) if m else path


def resolves(page, path):
    try:
        node = page
        for seg in segments(path):
            node = node[seg]
        return True
    except (PatchError, KeyError, IndexError, TypeError):
        return False


def _inside(path, root):
    return path == root or path.startswith(root + ".") or path.startswith(root + "[")


def apply_edits(page, edits, roots):
    """A deep copy of `page` with every edit applied. `roots` are the paths
    an edit may replace or reach inside of. Raises PatchError on any edit
    outside them, on a list index that does not exist (a patch never adds
    or drops an item -- that is a full rewrite), or on a malformed edit."""
    if not isinstance(edits, list) or not edits:
        raise PatchError('"edits" must be a non-empty list')
    allowed = tuple(dict.fromkeys([*roots, *ALWAYS_EDITABLE]))
    patched = copy.deepcopy(page)
    for edit in edits:
        if not isinstance(edit, dict) or "path" not in edit or "value" not in edit:
            raise PatchError('each edit is {"path": ..., "value": ...}')
        path = edit["path"]
        segs = segments(path)
        if not segs:
            raise PatchError("an edit may not replace the whole page")
        if not any(_inside(path, root) for root in allowed):
            raise PatchError(f"path {path} is outside the fields this repair may change: {list(allowed)}")
        node = patched
        try:
            for seg in segs[:-1]:
                node = node[seg]
        except (KeyError, IndexError, TypeError) as e:
            raise PatchError(f"path {path} does not resolve") from e
        last = segs[-1]
        if isinstance(last, int):
            if not isinstance(node, list) or not 0 <= last < len(node):
                raise PatchError(f"path {path} does not resolve")
        elif not isinstance(node, dict):
            raise PatchError(f"path {path} does not resolve")
        # A value keeps the type of the node it replaces: an object put on an
        # "answer" string (live smoke run 20261005-162640-hidden-costs-v2-3rtc)
        # passed the schema check and crashed the FAQ gate.
        if (isinstance(last, int) or last in node) and _kind(node[last]) != _kind(edit["value"]):
            raise PatchError(
                f"path {path} holds a {_kind(node[last])}; its new value must be a {_kind(node[last])} too "
                f"(got a {_kind(edit['value'])}) -- to change a line's claim_ids, edit the object that holds it"
            )
        node[last] = edit["value"]
    return patched


def _kind(value):
    if isinstance(value, str):
        return "string"
    if isinstance(value, dict):
        return "object"
    if isinstance(value, list):
        return "list"
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "boolean"
    return "number"


def is_edit_list(data):
    """True when a parsed writer response is a patch, not a full page."""
    return isinstance(data, dict) and set(data) == {"edits"}
