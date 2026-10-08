"""Cycle 91: the BLOCK CATALOG -- reusable page sections the harness owns
end to end, which a look composes and the writer fills.

The cycle 34 registry next door (harness/blocks/registry.json) holds LAYOUT
variants of one slot: no copy, no data of their own. A catalog block is a
whole section: it knows which facts it reads, computes every renderer-owned
value itself (prices, specs, ratings, photos, labels -- never from the
model where a fact exists), states the few fields the writer must fill when
a look uses it, gates those fields, and ships its own markup and scoped CSS.

One block = one module here plus one partial:

    harness/blocks/catalog/<id>.py    register(Block(...))
    harness/blocks/catalog/<id>.html  the Jinja partial, included by a look
                                      as {% include "blocks/<id>.html" %};
                                      its rendered context is `blocks.<id>`.
                                      Tokens only, classes `bk-<id>-*`, its
                                      CSS in the same partial, and
                                      data-look-pattern="<id>" on its root
                                      element (fidelity scoring reads it).

A look declares the blocks it uses in a `look.json` next to its template:

    {"blocks": ["stat_strip", "photo_essay"],
     "options": {"stat_strip": {"layout": "inline"}}}

Only declared blocks are rendered, only their writer fields reach the
prompt, and only their gates run. A look with no look.json declares nothing
and renders exactly as before cycle 91. See docs/BLOCKS.md.
"""
import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

CATALOG_DIR = Path(__file__).resolve().parent
REPO_ROOT = CATALOG_DIR.parents[2]
CARTRIDGES_DIR = REPO_ROOT / "cartridges"
LOOK_SPEC_FILE = "look.json"

# A block id: snake_case, so it can never be confused with a cycle 34
# registry name (kebab-case) and can never hold a path separator.
ID_RE = re.compile(r"^[a-z][a-z0-9]*(?:_[a-z0-9]+)*$")
PROBLEM_PREFIX = "blocks"


@dataclass(frozen=True)
class Block:
    """One catalog entry.

    id            snake_case id; the partial is <id>.html beside this package.
    title         what the Library and the docs call it.
    needs         the page / facts_pack / tenant data it reads (documentation
                  and the Library listing; not enforced).
    render        render(ctx) -> dict, the partial's `blocks.<id>`. Gets a
                  BlockContext. Returns {} (or a dict with nothing to show)
                  when the facts do not support the block: the partial then
                  renders nothing.
    writer        optional schema fragment for page.json `blocks.<id>`: the
                  fields the writer fills when a look uses the block.
    writer_lines  optional (facts_pack, tenant) -> [str]: the prompt lines
                  for those fields, stated from the constants the gate uses.
    gate          optional (fields, facts_pack, tenant) -> [problem]: checks
                  on `blocks.<id>`; problems use path `$.blocks.<id>.<field>`
                  and key `blocks:<id>:<rule>`.
    required      (facts_pack, tenant) -> bool: whether a look that declares
                  the block needs the writer's fields on this run.
    options       the block's option defaults; a look overrides them in
                  look.json "options".
    covers        (root, key) pairs of the look contract the block renders,
                  so a look that includes it need not print them itself
                  (harness/look_import.py's required blocks).
    page_gate     optional (page, facts_pack, tenant) -> [problem]: a rule
                  the block puts on the rest of the page (model_grid: a CTA
                  phrase that names no model), run whenever the look
                  declares the block.
    """
    id: str
    title: str
    needs: tuple
    render: Callable
    writer: dict = None
    writer_lines: Callable = None
    gate: Callable = None
    required: Callable = None
    options: dict = field(default_factory=dict)
    covers: tuple = ()
    page_gate: Callable = None

    @property
    def partial(self):
        return f"blocks/{self.id}.html"

    @property
    def writer_backed(self):
        return self.writer is not None

    def needs_writer(self, facts_pack, tenant):
        if not self.writer_backed:
            return False
        return True if self.required is None else bool(self.required(facts_pack, tenant))


@dataclass
class BlockContext:
    """What a block's render() gets. `assets` is render_page's assets_by_id:
    a block that shows an image the page.json does not name (a cut-out, a
    library photo) adds the asset here and returns its id in `asset_ids`, so
    render_page downloads it with the rest. Images come from cycle 93's slot
    resolver (harness/images.py fill_slots), never a block's own matcher."""
    page: dict
    facts_pack: dict
    tenant: object
    cartridge: str
    look: str
    options: dict
    assets: dict
    frame_asset: dict = None
    asset_alt: Callable = None
    # cycle 94/93: the run's ad-frame record (ad_frames.load), for a block that
    # asks images.fill_slots for an ad still
    frame_record: dict = None


_REGISTRY = {}


def register(block):
    if not ID_RE.match(block.id):
        raise ValueError(f"block id {block.id!r} must be snake_case")
    if block.id in _REGISTRY:
        raise ValueError(f"block {block.id!r} is registered twice")
    _REGISTRY[block.id] = block
    return block


def get(block_id):
    return _REGISTRY.get(block_id)


def ids():
    return tuple(_REGISTRY)


def all_blocks():
    return tuple(_REGISTRY.values())


# ---------------------------------------------------------------------------
# Which blocks a look uses (look.json)
# ---------------------------------------------------------------------------

def look_spec_path(cartridge, look, tenant=None, looks_dir=None):
    """The look.json beside this look's template, or None. A built-in look
    first (cartridges/<c>/looks/<look>/), else this tenant's imported look
    (tenants/<t>/looks/<c>/<slug>/) -- the same order harness/looks.py and
    render_page resolve the template in."""
    from ... import looks as looks_mod

    look = looks_mod.canonical(cartridge, look)
    if not look:
        return None
    if look in looks_mod.cartridge_looks(cartridge):
        path = CARTRIDGES_DIR / cartridge / "looks" / look / LOOK_SPEC_FILE
        return path if path.is_file() else None
    if looks_mod.LOOK_SLUG_RE.match(look):
        folder = looks_mod.imported_looks_dir(cartridge, tenant, looks_dir)
        path = folder / look / LOOK_SPEC_FILE if folder is not None else None
        return path if path is not None and path.is_file() else None
    return None


def parse_look_spec(data):
    """{"blocks": (ids...), "options": {id: {...}}} from look.json data.
    Raises ValueError on a malformed spec or an unknown block id."""
    if not isinstance(data, dict):
        raise ValueError("look.json must be an object")
    blocks = data.get("blocks", [])
    if not isinstance(blocks, list) or not all(isinstance(b, str) for b in blocks):
        raise ValueError('look.json "blocks" must be a list of block ids')
    unknown = [b for b in blocks if b not in _REGISTRY]
    if unknown:
        raise ValueError(f"look.json names unknown blocks {unknown}; the catalog has {sorted(_REGISTRY)}")
    if len(set(blocks)) != len(blocks):
        raise ValueError('look.json "blocks" lists a block twice')
    options = data.get("options", {})
    if not isinstance(options, dict) or not all(isinstance(v, dict) for v in options.values()):
        raise ValueError('look.json "options" must map a block id to an object')
    stray = [k for k in options if k not in blocks]
    if stray:
        raise ValueError(f"look.json has options for blocks it does not use: {stray}")
    merged = {b: {**_REGISTRY[b].options, **options.get(b, {})} for b in blocks}
    return {"blocks": tuple(blocks), "options": merged}


EMPTY_SPEC = {"blocks": (), "options": {}}


def look_spec(cartridge, look, tenant=None, looks_dir=None):
    """The parsed look.json of this look, or EMPTY_SPEC (no file: the look
    declares no blocks). Raises ValueError for a malformed file -- a look
    that names a block the catalog does not have must not render half."""
    path = look_spec_path(cartridge, look, tenant, looks_dir)
    if path is None:
        return EMPTY_SPEC
    try:
        data = json.loads(path.read_text())
    except (OSError, ValueError) as e:
        raise ValueError(f"{path}: {e}") from e
    try:
        return parse_look_spec(data)
    except ValueError as e:
        raise ValueError(f"{path}: {e}") from e


def look_blocks(cartridge, look, tenant=None, looks_dir=None):
    """The block ids this look declares, in order (() for none)."""
    return look_spec(cartridge, look, tenant, looks_dir)["blocks"]


# ---------------------------------------------------------------------------
# Render
# ---------------------------------------------------------------------------

def render_blocks(spec, ctx_kwargs):
    """{"blocks": {id: rendered dict}, "asset_ids": set, "claim_ids": set}
    for every block `spec` declares. `ctx_kwargs` are BlockContext fields
    except `options`."""
    out, asset_ids, claim_ids = {}, set(), set()
    for block_id in spec["blocks"]:
        block = _REGISTRY[block_id]
        ctx = BlockContext(options=dict(spec["options"].get(block_id) or {}), **ctx_kwargs)
        data = block.render(ctx) or {}
        asset_ids |= set(data.get("asset_ids") or ())
        claim_ids |= set(data.get("claim_ids") or ())
        out[block_id] = data
    return {"blocks": out, "asset_ids": asset_ids, "claim_ids": claim_ids}


# ---------------------------------------------------------------------------
# Writer prompt and gate
# ---------------------------------------------------------------------------

def page_fields(page, block_id):
    """page.json `blocks.<id>` when it is an object, else None. (A string
    value is a cycle 34 registry slot pick, never catalog fields.)"""
    blocks = page.get("blocks") if isinstance(page, dict) else None
    fields = blocks.get(block_id) if isinstance(blocks, dict) else None
    return fields if isinstance(fields, dict) else None


def writer_lines(block_ids, facts_pack, tenant=None):
    """The prompt lines for the declared blocks that have any (writer-backed
    blocks, and renderer blocks that put a rule on the page), empty when
    there are none: a look with no such block adds nothing to the prompt."""
    from ... import tenant as tenant_mod

    tenant = tenant or tenant_mod.active()
    declared = [_REGISTRY[b] for b in block_ids if b in _REGISTRY]
    backed = [b for b in declared if b.writer_backed]
    wanted = [b for b in backed if b.needs_writer(facts_pack, tenant)]
    lines = []
    if wanted:
        lines.append(
            "This page's look also renders section BLOCKS. Write a top-level `blocks` object with exactly these "
            "keys: " + ", ".join(f"`{b.id}`" for b in wanted) + ". Each key's fields are below. Block words count "
            "toward the page's word range. Never put a price, spec value, rating, link or phone number in a "
            "block: the renderer shows those."
        )
    elif backed:
        lines.append("Omit `blocks` entirely: no block on this look needs words for this run.")
    for block in declared:
        if block in wanted:
            lines.append(f"blocks.{block.id} has this shape: " + json.dumps(block.writer, sort_keys=True))
        if block.writer_lines:   # a writer block not needed on this run says "omit" itself
            lines += list(block.writer_lines(facts_pack, tenant))
    return lines


def _problem(block_id, path, rule, issue, **extra):
    return {"path": f"$.blocks.{block_id}{path}", "key": f"{PROBLEM_PREFIX}:{block_id}:{rule}",
            "issue": issue, **extra}


def find_violations(page, facts_pack, block_ids, tenant=None):
    """The catalog's gate over page.json `blocks`: an unknown block, a block
    this look does not use, a declared writer block left out, and each
    writer block's own field checks. `block_ids` is what the look declares
    (None: gate whatever writer blocks the page carries, e.g. a revise of a
    page whose look is not known here)."""
    from ... import tenant as tenant_mod

    tenant = tenant or tenant_mod.active()
    if not isinstance(page, dict):
        return []
    blocks = page.get("blocks")
    if blocks is None:
        present = {}
    elif not isinstance(blocks, dict):
        return [{"path": "$.blocks", "key": f"{PROBLEM_PREFIX}:shape",
                 "issue": "blocks must be an object mapping a block id to its fields"}]
    else:
        present = {k: v for k, v in blocks.items() if isinstance(v, dict)}
    problems = []
    declared = tuple(block_ids) if block_ids is not None else tuple(k for k in present if k in _REGISTRY)
    for block_id in present:
        block = _REGISTRY.get(block_id)
        if block is None:
            problems.append({"path": f"$.blocks.{block_id}", "key": f"{PROBLEM_PREFIX}:unknown:{block_id}",
                             "issue": f"blocks.{block_id} is not a catalog block -- remove it"})
        elif block_id not in declared:
            problems.append(_problem(block_id, "", "not_in_look",
                                     f"this page's look does not use the {block_id} block -- remove blocks.{block_id}"))
        elif not block.writer_backed:
            problems.append(_problem(block_id, "", "renderer_owned",
                                     f"blocks.{block_id} is built by the renderer from facts_pack; never write it"))
    for block_id in declared:
        block = _REGISTRY.get(block_id)
        if block is None or not block.writer_backed:
            continue
        fields = present.get(block_id)
        if fields is None:
            if block.needs_writer(facts_pack, tenant):
                problems.append(_problem(block_id, "", "missing",
                                         f"this look uses the {block_id} block: write blocks.{block_id} "
                                         f"({', '.join(block.writer.get('properties', {}))})"))
            continue
        problems += block.gate(fields, facts_pack, tenant) if block.gate else []
    for block_id in declared:
        block = _REGISTRY.get(block_id)
        if block is not None and block.page_gate:
            problems += block.page_gate(page, facts_pack, tenant)
    return problems


# ---------------------------------------------------------------------------
# Shared field checks (the landing/listicle gate rules, for block fields)
# ---------------------------------------------------------------------------

def words(text):
    return len(str(text or "").split())


def verified(facts_pack):
    return {c["id"]: c for c in (facts_pack or {}).get("verified_claims", []) if c.get("id")}


def number_reason(text, facts_pack):
    """Why `text` needs a claim id (a digit, a price, a trigger word -- the
    shared claims gate's own test), or None."""
    from ...claims import _trigger_reason

    if not isinstance(text, str) or not text:
        return None
    return _trigger_reason(text, (facts_pack or {}).get("digit_exempt_terms"))


def check_claim_ids(block_id, path, rule, ids, facts_pack, *, required, why=""):
    """claim_ids present (when required) and every one verified."""
    have = verified(facts_pack)
    ids = [c for c in (ids or []) if c]
    if not ids:
        if required:
            return [_problem(block_id, path, f"{rule}_claims",
                             f"blocks.{block_id}{path.replace('.claim_ids', '')} needs claim_ids from "
                             f"facts_pack.verified_claims{why}")]
        return []
    bad = [c for c in ids if c not in have]
    if bad:
        return [_problem(block_id, path, f"{rule}_unverified", f"claim_ids {bad} are not verified claims")]
    return []


# The modules register themselves on import, in catalog order.
from . import stat_strip, photo_essay, hotspot_image, feature_grid, model_card, model_grid  # noqa: E402,F401
from . import statement_band, faq, trust_bar  # noqa: E402,F401
