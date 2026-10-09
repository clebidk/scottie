"""Cycle 73: the listicle winner-skeleton library (merged from
cursor/listicle-skeletons-fdad, reworked to fit cycles 41-72).

A skeleton (cartridges/listicle/skeletons/NN-<id>.json) is the ITEM MAP of a
proven listicle -- the role of each numbered item, its heading angle and image
role, plus section hints -- that the writer adapts the same way it adapts the
tenant's winner exemplar. It sits on top of the three existing dimensions and
replaces none of them:

  - the STYLE (harness/listicle.py) still fixes the copy; each skeleton lists
    the styles it fits, and only those may use it;
  - the LOOK still fixes the layout; each skeleton names the closest of the
    five looks, used only when the operator pins the skeleton (--skeleton)
    and gives no --look;
  - the HEADLINE TEMPLATE (harness/headlines.py) still fixes the title; each
    skeleton lists the templates that pair with it, preferred only when the
    operator pins the skeleton.

A skeleton never reaches the gates: check_page_gates sees the page exactly as
it would without one, so claims, proof lines, quote fidelity and the banned
words apply unchanged. Its hints are angles, never facts. One exception
(cycle 100, owner decision 2026-10-09): the item count. A skeleton may set
its own "item_range" (named-cause: 3-5); the listicle item gate reads it
through item_range() below, and every other skeleton keeps the cartridge's
5-7.

Tenant-neutral: tenant fill hints (extra auto-pick tags, item hints) live in
tenants/<tenant>/listicle-library/skeletons.yaml.
"""
import functools
import json
import re

import yaml

from . import listicle
from . import tenant as tenant_mod
from .errors import HarnessError

SKELETONS_DIR = tenant_mod.REPO_ROOT / "cartridges" / "listicle" / "skeletons"
INDEX_PATH = SKELETONS_DIR / "index.json"
SCHEMA_PATH = SKELETONS_DIR / "skeleton.schema.json"
TENANT_HINTS_PATH = ("listicle-library", "skeletons.yaml")


class SkeletonError(HarnessError, ValueError):
    """An unknown skeleton, or one that does not fit the run's style."""


# ---------------------------------------------------------------------------
# library
# ---------------------------------------------------------------------------

@functools.lru_cache(maxsize=1)
def _index():
    return json.loads(INDEX_PATH.read_text())


@functools.lru_cache(maxsize=1)
def schema():
    return json.loads(SCHEMA_PATH.read_text())


def skeleton_ids():
    return [row["id"] for row in _index()["skeletons"]]


def default_for_style(style):
    return _index()["default_by_style"][style]


@functools.lru_cache(maxsize=32)
def _load(skeleton_id):
    for row in _index()["skeletons"]:
        if row["id"] == skeleton_id:
            data = json.loads((SKELETONS_DIR / row["file"]).read_text())
            if data.get("id") != skeleton_id:
                raise SkeletonError(f"{row['file']}: id {data.get('id')!r} != index id {skeleton_id!r}")
            return data
    raise SkeletonError(f"unknown skeleton {skeleton_id!r}; choose one of {skeleton_ids()}")


def load_skeleton(skeleton_id):
    return json.loads(json.dumps(_load(skeleton_id)))


def load_all():
    return [load_skeleton(sid) for sid in skeleton_ids()]


_TYPES = {"object": dict, "array": list, "string": str, "integer": int, "boolean": bool}


def validate(data, spec=None, path="$"):
    """Problems (strings) found checking `data` against skeleton.schema.json.
    Covers the keywords that schema uses -- type, required, properties,
    additionalProperties (false, or a schema for every other key), enum,
    pattern, minLength, minItems, maxItems, minimum, maximum, items, and a
    "#/..." $ref into the same file -- so the schema file is the one
    definition (no jsonschema dependency for one file). At the top level
    also the item-count rules no keyword can state (count_problems)."""
    if spec is None:
        return validate(data, schema(), path) + (count_problems(data) if isinstance(data, dict) else [])
    if "$ref" in spec:
        target = schema()
        for part in spec["$ref"].removeprefix("#/").split("/"):
            target = target[part]
        spec = target
    problems = []
    typ = spec.get("type")
    if typ:
        ok = isinstance(data, _TYPES[typ]) and not (typ == "integer" and isinstance(data, bool))
        if not ok:
            return [f"{path}: expected {typ}, got {type(data).__name__}"]
    if "enum" in spec and data not in spec["enum"]:
        problems.append(f"{path}: {data!r} is not one of {spec['enum']}")
    if isinstance(data, str):
        if len(data) < spec.get("minLength", 0):
            problems.append(f"{path}: shorter than {spec['minLength']}")
        if "pattern" in spec and not re.search(spec["pattern"], data):
            problems.append(f"{path}: {data!r} does not match {spec['pattern']}")
    if isinstance(data, int) and not isinstance(data, bool):
        if "minimum" in spec and data < spec["minimum"]:
            problems.append(f"{path}: {data} < {spec['minimum']}")
        if "maximum" in spec and data > spec["maximum"]:
            problems.append(f"{path}: {data} > {spec['maximum']}")
    if isinstance(data, list):
        if len(data) < spec.get("minItems", 0):
            problems.append(f"{path}: fewer than {spec['minItems']} entries")
        if "maxItems" in spec and len(data) > spec["maxItems"]:
            problems.append(f"{path}: more than {spec['maxItems']} entries")
        for i, entry in enumerate(data):
            problems += validate(entry, spec.get("items") or {}, f"{path}[{i}]")
    if isinstance(data, dict):
        props = spec.get("properties") or {}
        for key in spec.get("required") or []:
            if key not in data:
                problems.append(f"{path}: missing {key!r}")
        extra = spec.get("additionalProperties")
        for key, value in data.items():
            if key in props:
                problems += validate(value, props[key], f"{path}.{key}")
            elif extra is False:
                problems.append(f"{path}: unknown key {key!r}")
            elif isinstance(extra, dict):
                problems += validate(value, extra, f"{path}.{key}")
    return problems


def count_problems(skeleton):
    """Cycle 100: the item-count rules across fields -- item_range is
    [lo, hi] with lo <= hi, item_count and every item_plans key sit inside
    it, items has item_count entries, and each plan has as many entries as
    its key says."""
    problems = []
    lo, hi = item_range(skeleton)
    if lo > hi:
        problems.append(f"$.item_range: {lo} > {hi}")
    count = skeleton.get("item_count")
    if isinstance(count, int) and not lo <= count <= hi:
        problems.append(f"$.item_count: {count} is outside item_range {lo}-{hi}")
    if isinstance(count, int) and isinstance(skeleton.get("items"), list) and len(skeleton["items"]) != count:
        problems.append(f"$.items: {len(skeleton['items'])} entries, item_count is {count}")
    for key, plan in (skeleton.get("item_plans") or {}).items():
        if not key.isdigit():
            problems.append(f"$.item_plans.{key}: the key must be the item count")
            continue
        if not lo <= int(key) <= hi:
            problems.append(f"$.item_plans.{key}: {key} is outside item_range {lo}-{hi}")
        if int(key) == count:
            problems.append(f"$.item_plans.{key}: item_count's map is items, not a plan")
        if isinstance(plan, list) and len(plan) != int(key):
            problems.append(f"$.item_plans.{key}: {len(plan)} entries, not {key}")
    return problems


# ---------------------------------------------------------------------------
# item count (cycle 100)
# ---------------------------------------------------------------------------

def item_range(skeleton):
    """(lo, hi): the item counts a page on this skeleton may have -- its own
    "item_range" when it sets one (named-cause: 3-5), else the cartridge's
    listicle.ITEM_COUNT_RANGE (5-7). Takes a skeleton, its writer payload
    (for_writer keeps item_range), or None (no skeleton: the cartridge's)."""
    own = (skeleton or {}).get("item_range")
    return (own[0], own[1]) if own else listicle.ITEM_COUNT_RANGE


def item_counts(skeleton):
    """The counts this skeleton has an item map for: item_count (its items)
    and each item_plans key, ascending."""
    return sorted({skeleton["item_count"], *(int(k) for k in skeleton.get("item_plans") or {})})


def items_for(skeleton, n=None):
    """The ordered item map for an n-item page (n None: item_count, the
    skeleton's own items). Raises SkeletonError for a count outside the
    skeleton's item_range, or one inside it with no item map (only
    named-cause has item_plans; the others have their one item_count)."""
    if n is None or n == skeleton["item_count"]:
        return skeleton["items"]
    lo, hi = item_range(skeleton)
    if not lo <= n <= hi:
        raise SkeletonError(
            f"skeleton {skeleton['id']!r} allows {lo} to {hi} items, not {n}"
        )
    plan = (skeleton.get("item_plans") or {}).get(str(n))
    if plan is None:
        raise SkeletonError(
            f"skeleton {skeleton['id']!r} has an item map for {item_counts(skeleton)} items, not {n}"
        )
    return plan


# ---------------------------------------------------------------------------
# tenant fill hints
# ---------------------------------------------------------------------------

def tenant_hints(tenant=None, skeleton_id=None):
    """The tenant's own fill hints: {skeleton_id: {"angle_fit": [...],
    "item_hints": [...]}} from tenants/<tenant>/listicle-library/
    skeletons.yaml, or {} when the tenant has none. With `skeleton_id`, that
    one entry ({} when absent)."""
    tenant = tenant or tenant_mod.active()
    path = tenant.root.joinpath(*TENANT_HINTS_PATH)
    data = {}
    if path.exists():
        data = yaml.safe_load(path.read_text()) or {}
    if skeleton_id is None:
        return data
    return data.get(skeleton_id) or {}


# ---------------------------------------------------------------------------
# selection
# ---------------------------------------------------------------------------

def style_for(skeleton_id, requested_style=None):
    """The style a run that pins `skeleton_id` writes in: `requested_style`
    (the --style flag) when the skeleton fits it, else the skeleton's first
    style. Raises SkeletonError when the two disagree."""
    styles = load_skeleton(skeleton_id)["styles"]
    if requested_style:
        if requested_style not in styles:
            raise SkeletonError(
                f"skeleton {skeleton_id!r} fits the {styles} style(s), not {requested_style!r}"
            )
        return requested_style
    return styles[0]


def _haystack(ad_brief):
    ad_brief = ad_brief or {}
    parts = [str(ad_brief.get(k) or "") for k in ("angle", "hook", "promise", "tone")]
    for k in ("features_shown", "objections_raised"):
        parts += [str(x) for x in ad_brief.get(k) or []]
    return " ".join(parts).lower()


def _score(skeleton, text, hints):
    score = 0
    for tag in skeleton["tags"]:
        if re.search(r"\b" + re.escape(tag.lower()) + r"\b", text):
            score += 1
    for tag in hints.get("angle_fit") or []:
        if re.search(r"\b" + re.escape(str(tag).lower()) + r"\b", text):
            score += 2
    return score


def select(style, ad_brief=None, *, requested=None, tenant=None):
    """The skeleton one listicle run adapts. `requested` (--skeleton) wins and
    must fit `style`. Otherwise the skeletons that fit the style are scored
    against the ad (their tags, +2 per tenant angle_fit tag); the best score
    wins, ties go to library order, and no match at all falls back to the
    style's default (index.json default_by_style). Deterministic: the same ad
    and style always get the same skeleton."""
    if requested:
        style_for(requested, style)
        return load_skeleton(requested)
    if style not in listicle.STYLES:
        raise SkeletonError(f"unknown listicle style {style!r}")
    text = _haystack(ad_brief)
    hints = tenant_hints(tenant)
    best, best_score = None, 0
    for sk in load_all():
        if style not in sk["styles"]:
            continue
        score = _score(sk, text, hints.get(sk["id"]) or {})
        if score > best_score:
            best, best_score = sk, score
    return best or load_skeleton(default_for_style(style))


def ranked(style, ad_brief=None, *, tenant=None):
    """Cycle 76: every skeleton that fits `style`, in select()'s order of
    preference -- best ad score first, ties by library order, and the style's
    default first among those that score nothing. ranked(...)[0] is what
    select() picks with no --skeleton; harness/drafts.py gives a second draft
    the best one the first draft did not use."""
    text = _haystack(ad_brief)
    hints = tenant_hints(tenant)
    default = default_for_style(style)
    fits = [sk for sk in load_all() if style in sk["styles"]]
    scored = [(_score(sk, text, hints.get(sk["id"]) or {}), i, sk) for i, sk in enumerate(fits)]
    scored.sort(key=lambda t: (-t[0], t[0] == 0 and t[2]["id"] != default, t[1]))
    return [sk for _score_value, _i, sk in scored]


# ---------------------------------------------------------------------------
# writer
# ---------------------------------------------------------------------------

WRITER_KEYS = ("id", "name", "item_count", "item_range", "sections", "items", "voice", "compliance", "writer_brief")


def for_writer(skeleton, tenant=None, items=None):
    """The compact payload the writer's user message carries under
    "skeleton": the item map and hints, plus the tenant's item hints. No
    sources, tags, look or headline list -- those steer selection, not
    copy. Cycle 100: `items` (--items) picks the count and its item map
    (items_for; SkeletonError when the skeleton has none); item_count and
    items then say that count. item_range is kept only when the skeleton
    sets one, so the gate reads the same range the writer was given."""
    payload = {k: skeleton[k] for k in WRITER_KEYS if k in skeleton}
    if items is not None:
        payload["items"] = items_for(skeleton, items)
        payload["item_count"] = items
    hints = tenant_hints(tenant, skeleton["id"]).get("item_hints")
    if hints:
        payload["tenant_item_hints"] = list(hints)
    return payload


def writer_lines(skeleton):
    """Hard-constraint lines for a run with a skeleton (listicle only)."""
    if not skeleton:
        return []
    lo, hi = item_range(skeleton)
    lines = [
        f'This page adapts the winner skeleton "{skeleton["name"]}" -- the user message carries it '
        'under "skeleton". It is an item map, never a claims source: write the numbered items in '
        "its order, one per entry in skeleton.items, using each entry's role and heading_hint as "
        "that item's angle in this page's style and headline template. Keep its item count unless "
        f"an item has no verified fact to stand on; then drop that item and stay inside {lo}-{hi}.",
        "The skeleton's section hints fit inside this cartridge's Structure: they never add a "
        "section, a label, a byline, comments or a trust row. Its hints and tenant_item_hints are "
        "angles, not facts -- every number, spec or outcome still needs a verified claim_id, and "
        "every other rule in these hard constraints wins over the skeleton.",
    ]
    # Cycle 100: a skeleton with its own range names the count to write.
    if skeleton.get("item_range"):
        n = skeleton["item_count"]
        dlo, dhi = listicle.ITEM_COUNT_RANGE
        lines.insert(1, (
            f"Write exactly {n} numbered items (skeleton.item_count), and a count headline's N is {n}. "
            f"This skeleton allows {lo} to {hi} items, never more than {hi}: that range replaces the "
            f"{dlo}-{dhi} items stated elsewhere in these instructions. With fewer items each one keeps its "
            "whole role from skeleton.items (an item whose role names two parts carries both); never pad "
            "an item to make up the length."
        ))
    return lines + ([DRAFT_ANGLE_LINE] if skeleton.get("draft_angle") else [])


# Cycle 76 (harness/drafts.py): a second draft whose style allows no other
# headline template or skeleton gets this line, so it is a different attempt
# at the page rather than the same prompt sampled again.
DRAFT_ANGLE = "objection-first"
DRAFT_ANGLE_LINE = (
    "Draft angle (objection-first): this is a second, different attempt at this page. Open from the "
    "reader's main doubt, not the product's promise: fill the headline's free slots and write the dek "
    "from the ad brief's objections_raised (if it has none, from the problem in its hook), and order "
    "the items so the first one answers that doubt. Same facts, same structure, same rules."
)
