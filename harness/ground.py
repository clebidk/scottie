"""Grounding: turn an ad_brief + the local claims store into a small facts_pack
the writer can cite from. `FactsSource` is a protocol so `write`/`cli` don't
care whether facts came from the local JSON files or (later) g Brain.
"""
import json
import re
from collections import Counter
from pathlib import Path
from typing import Protocol

from . import asset_review
from . import tenant as tenant_mod
from .errors import UnknownProduct
from .prices import format_price
from .textutil import DOLLAR_AMOUNT_RE, product_name_slug, walk_page
from .tenant import DEFAULT_CLAIMS_CONFIG as DEFAULT_CONFIG

# Fix 8: for the chosen product's Drive assets, prefer lifestyle/interior,
# then render, then installation shots. Never video/logo/ugc in V1.
DRIVE_ASSET_TIERS = (("lifestyle", "interior"), ("render",), ("installation",))
DRIVE_ASSET_NEVER_KINDS = {"video", "logo", "ugc"}
DRIVE_ASSET_MAX = 6

def listicle_pack_models(tenant=None):
    """The models the tenant's own listicle asset pack actually covers
    (tenant.yaml's listicle_pack_models). Wiring the pack in for a model it has
    no assets for would just add empty lookups."""
    tenant = tenant or tenant_mod.active()
    return {m.lower() for m in (tenant.get("listicle_pack_models") or ())}
# Real photos first (photo_product/photo_install, plus still_video -- brand
# generally, not model-specific); an ai_render only ever becomes eligible via
# select_listicle_pack_assets's own allow_ai_renders gate.
LISTICLE_PACK_TIERS = (("photo_product", "photo_install", "still_video"), ("ai_render",))
LISTICLE_PACK_ASSET_MAX = 6

# Cycle 31: an explicit per-cartridge slot plan (docs/IMAGES-AUDIT-2026-09-14.md
# problem 1) -- built alongside the flat `assets` list facts_for() already
# returns, never replacing it (the writer keeps picking asset_ids from
# `assets` same as before; the plan is a recommendation plus a render-time
# enforcement backstop, see render.enforce_slot_plan below and
# render.render_page's call into it).
#
# Kind values span two asset shapes: brand/assets.json's Drive kinds
# (lifestyle/interior/render/installation/image) and
# brand/assets-listicle-pack.json's own kinds (photo_product/photo_install/
# still_video/ai_render). A "real photo, not a plain product-on-white shot"
# hero candidate is any of these; select_drive_assets/select_listicle_pack_
# assets already keep video/logo/ugc/excluded rows out of the pool entirely,
# so this set only needs to name what counts as hero-worthy, not what to
# reject again.
HERO_CANDIDATE_KINDS = {"lifestyle", "interior", "installation", "photo_install", "still_video"}
# Defensive -- none of this tenant's pools currently produce a logo/favicon
# row this deep into the pipeline (DRIVE_ASSET_NEVER_KINDS already excludes
# logo/video/ugc), but the hero picker checks explicitly rather than relying
# on that upstream filter alone.
HERO_NEVER_KINDS = {"logo", "video", "ugc", "favicon"}
# Round-robin kind groups for section images ("alternating detail/interior/
# lifestyle" per the cycle 31 brief), spanning both asset-pool kind spaces.
SECTION_KIND_GROUPS = (
    {"image", "render", "photo_product"},
    {"interior", "installation", "photo_install"},
    {"lifestyle", "still_video"},
)
DEFAULT_SECTION_SLOT_COUNT = 6

# Cycle 41 (listicle v0.2): how many models the listicle's model picker
# offers, and which spec labels make up a model's one-line "fit". Both are
# generic: a spec row only reaches the picker when it carries its own
# claim_id, so every line in the picker is backed by a verified claim.
MODEL_OPTION_MAX = 3
MODEL_FIT_SPEC_LABELS = ("capacity", "placement")

# Cycle 54 (product-page `pdp` look): the model compare table's spec rows.
# A row is kept only when at least MODEL_COMPARE_MIN_MODELS of the compared
# models carry that spec label WITH its own verified claim_id, so the table
# never lines one model's claim up against another model's blank.
MODEL_COMPARE_MAX_SPEC_ROWS = 5
MODEL_COMPARE_MIN_MODELS = 2


def pick_hero(assets, *, allow_ai_renders, exclude_ids=frozenset()):
    """The best hero candidate from `assets` (facts_for()'s combined list):
    a lifestyle/interior/installation real photo first, else the product's
    first Shopify product image, else any other remaining non-ai_render
    asset, else (only when `allow_ai_renders` and nothing else is left at
    all) an ai_render. Never a logo/video/ugc/favicon-kind asset. `exclude_ids`
    (ids already used elsewhere in this run/page) is honored when a
    compliant alternative exists; if excluding them would leave nothing,
    the exclusion is dropped rather than shipping no hero at all. Returns
    the asset dict, or None if `assets` is empty."""
    pool = [a for a in assets if a.get("kind") not in HERO_NEVER_KINDS]
    if not pool:
        return None
    candidates = [a for a in pool if a["id"] not in exclude_ids] or pool
    for a in candidates:
        if a.get("kind") in HERO_CANDIDATE_KINDS and not a.get("ai_generated"):
            return a
    shopify = [a for a in candidates if a.get("kind") == "image" and not a.get("ai_generated")]
    if shopify:
        return shopify[0]
    non_ai = [a for a in candidates if not a.get("ai_generated")]
    if non_ai:
        return non_ai[0]
    if allow_ai_renders:
        return candidates[0]
    return None


def pick_section_images(assets, count, *, exclude_ids=frozenset(), allow_ai_renders):
    """Up to `count` non-hero section images, cycling through
    SECTION_KIND_GROUPS in order so consecutive picks alternate kind rather
    than clustering (e.g. four product-detail shots in a row). Never repeats
    an id, never picks one in `exclude_ids`, never an ai_render unless
    `allow_ai_renders`. Returns fewer than `count` if the eligible pool runs
    out first."""
    pool = [
        a for a in assets
        if a["id"] not in exclude_ids
        and a.get("kind") not in HERO_NEVER_KINDS
        and (allow_ai_renders or not a.get("ai_generated"))
    ]
    selected = []
    remaining = list(pool)
    group_idx = 0
    while remaining and len(selected) < count:
        group = SECTION_KIND_GROUPS[group_idx % len(SECTION_KIND_GROUPS)]
        pick = next((a for a in remaining if a.get("kind") in group), None)
        if pick is None:
            pick = remaining[0]  # this tier's exhausted -- take whatever's left rather than stall
        selected.append(pick)
        remaining.remove(pick)
        group_idx += 1
    return selected


def build_slot_plan(assets, *, allow_ai_renders, section_count=DEFAULT_SECTION_SLOT_COUNT, exclude_ids=frozenset()):
    """{"hero": asset|None, "sections": [asset, ...], "used_ids": [...]} --
    the hero is picked first (see pick_hero) so section picks never steal
    it, then up to `section_count` section images fill from what's left.
    `exclude_ids` (ids a prior cartridge in this run already used) is
    honored the same way pick_hero honors it: a hard preference, not a hard
    requirement, when the pool is too small to satisfy it."""
    hero = pick_hero(assets, allow_ai_renders=allow_ai_renders, exclude_ids=exclude_ids)
    used = set(exclude_ids)
    if hero:
        used.add(hero["id"])
    sections = pick_section_images(assets, section_count, exclude_ids=used, allow_ai_renders=allow_ai_renders)
    used.update(a["id"] for a in sections)
    return {
        "hero": hero,
        "sections": sections,
        "used_ids": sorted(({hero["id"]} if hero else set()) | {a["id"] for a in sections}),
    }


def _lean_slot_plan(plan):
    """build_slot_plan's dict, trimmed to the one id that matters most to
    the writer (the hero recommendation -- docs/IMAGES-AUDIT-2026-09-14.md
    problem 1). test_facts_pack_stays_small caps facts_pack at ~4k tokens
    with very little headroom already, so this deliberately omits
    section_ids (a nice-to-have the writer can still satisfy freely by
    alternating kinds itself from "assets") and skips duplicating any asset
    record -- the full one is already in facts_pack.assets."""
    return {"hero_id": plan["hero"]["id"] if plan["hero"] else None}


# --- Run-level cross-cartridge dedupe state (docs/IMAGES-AUDIT-2026-09-14.md
# problem 2/3: the same hero, or a whole page's worth of images, repeating
# across an ad's three cartridges). facts_for() builds one shared facts_pack
# for the whole run, so per-cartridge dedupe has to be tracked as the
# cartridges render one at a time -- see render.render_page's call into
# record_used_asset_ids after each cartridge, and its call into
# all_used_asset_ids (via enforce_slot_plan) before the next.

_SELECTION_STATE_FILENAME = ".image-selection.json"


def load_used_asset_ids(run_dir):
    """{cartridge_name: [asset_id, ...]} already recorded for this run, or
    {} if no cartridge has rendered yet (or the run predates cycle 31)."""
    path = Path(run_dir) / _SELECTION_STATE_FILENAME
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text())
    except (json.JSONDecodeError, OSError):
        return {}


def record_used_asset_ids(run_dir, cartridge_name, asset_ids):
    """Records the asset ids `cartridge_name` actually ended up using in
    <run_dir>/.image-selection.json, for the next cartridge in this run to
    read back via all_used_asset_ids. Overwrites only this cartridge's own
    entry -- a re-render of one cartridge doesn't lose another's record."""
    path = Path(run_dir) / _SELECTION_STATE_FILENAME
    data = load_used_asset_ids(run_dir)
    data[cartridge_name] = sorted(set(asset_ids))
    path.write_text(json.dumps(data, indent=2) + "\n")
    return path


def all_used_asset_ids(run_dir, *, exclude_cartridge=None):
    """The union of every asset id recorded by any other cartridge in this
    run so far (excluding `exclude_cartridge`'s own prior record, so a
    re-render of one cartridge isn't blocked by its own earlier attempt)."""
    data = load_used_asset_ids(run_dir)
    used = set()
    for cartridge_name, ids in data.items():
        if cartridge_name == exclude_cartridge:
            continue
        used.update(ids)
    return used


# Cartridges with an explicit page.json hero field (page.hero.hero_image).
# article/listicle have no such field in their schema (owned by cycle30 for
# article; out of scope to add for either here) -- for them, the first
# image in the cartridge's own existing image list is treated as the de
# facto hero for selection-policy purposes only (never a new page.json
# field, purely which existing asset_id gets checked/possibly swapped).
_HERO_FIELD_PATH = {
    "longform": ("hero", "hero_image"),
    "product-page": ("hero", "hero_image"),
    # Cycle 41: listicle v0.2 has a real header hero slot of its own
    # (page.hero.asset_id). v0.1 had none, so the first item's image was
    # treated as the de facto hero -- see the listicle branch below, kept
    # only for a page written against the older schema.
    "listicle": ("hero",),
}


def hero_container(page, cartridge_name):
    """The page.json dict node whose "asset_id" key is this cartridge's
    hero slot, or None if the page has no eligible hero slot at all (e.g.
    an article page with an empty page.images list)."""
    path = _HERO_FIELD_PATH.get(cartridge_name)
    if path:
        node = page
        for key in path:
            if not isinstance(node, dict):
                return None
            node = node.get(key)
        return node if isinstance(node, dict) else None
    if cartridge_name == "article":
        images = page.get("images") or []
        return images[0] if images and isinstance(images[0], dict) else None
    if cartridge_name == "listicle":
        # v0.1 fallback: no page.hero at all, so the first item's image is
        # the de facto hero for selection-policy purposes.
        reasons = page.get("reasons") or []
        if reasons and isinstance(reasons[0], dict):
            image = reasons[0].get("image")
            return image if isinstance(image, dict) else None
    return None


def enforce_slot_plan(page, all_assets, cartridge_name, *, allow_ai_renders, exclude_ids=frozenset()):
    """Render-time backstop over the writer's own asset_id picks in `page`
    -- never touches copy, only "asset_id" fields. Fixes exactly the
    selection-policy gaps docs/IMAGES-AUDIT-2026-09-14.md found: (1) this
    cartridge's hero slot (hero_container) holding a never-eligible kind,
    a disallowed ai_render, or an id already used by an earlier cartridge
    this run (`exclude_ids`); (2) the same asset_id repeated on one page;
    (3) a disallowed ai_render used in a non-hero slot. Mutates `page` in
    place and returns the set of asset ids the page ends up using (for the
    caller to pass to record_used_asset_ids)."""
    by_id = {a["id"]: a for a in all_assets}
    used = set()

    def is_bad(asset_id, *, extra_exclude=frozenset()):
        asset = by_id.get(asset_id)
        return (
            asset is None
            or asset.get("kind") in HERO_NEVER_KINDS
            or (asset.get("ai_generated") and not allow_ai_renders)
            or asset_id in exclude_ids
            or asset_id in extra_exclude
        )

    hero_node = hero_container(page, cartridge_name)
    hero_id = None
    if hero_node is not None:
        current = hero_node.get("asset_id")
        if is_bad(current):
            replacement = pick_hero(all_assets, allow_ai_renders=allow_ai_renders, exclude_ids=exclude_ids)
            if replacement is not None:
                hero_node["asset_id"] = replacement["id"]
                current = replacement["id"]
        hero_id = current
        if hero_id:
            used.add(hero_id)

    # longform's own "images" (cartridges/longform/schema.json) is
    # documented as "a convenience index of everything used [elsewhere]",
    # not a distinct slot -- it is meant to repeat hero_image's and the
    # how_it_works steps' own ids, so it is excluded here the same way
    # pagechecks.find_duplicate_asset_violations excludes it from the
    # duplicate scan.
    skip_keys = {"images"} if cartridge_name == "longform" else ()
    for _path, node in walk_page(page, skip_keys=skip_keys):
        if not isinstance(node, dict) or "asset_id" not in node or node is hero_node:
            continue
        asset_id = node.get("asset_id")
        if asset_id in used or is_bad(asset_id, extra_exclude={hero_id} if hero_id else frozenset()):
            pool = [
                a for a in all_assets
                if a["id"] not in used
                and a["id"] not in exclude_ids
                and a.get("kind") not in HERO_NEVER_KINDS
                and (allow_ai_renders or not a.get("ai_generated"))
            ]
            if pool:
                asset_id = pool[0]["id"]
                node["asset_id"] = asset_id
            elif asset_id is None or by_id.get(asset_id) is None:
                continue  # nothing eligible left; leave as-is for pagechecks to flag
        if asset_id:
            used.add(asset_id)
    return used


# ---------------------------------------------------------------------------
# Cycle 42: paragraph-to-image matching (deterministic, no model call). Runs
# right after enforce_slot_plan (see render.render_page): where that function
# only fixes selection-POLICY violations (a never-eligible kind, a repeat, a
# disallowed ai_render), this one asks a content question -- does the image
# next to a paragraph actually show what the paragraph talks about? -- by
# scoring token overlap between the slot's own heading+body text and each
# candidate asset's reviewed alt/tags (tenants/<t>/brand/asset-review.json,
# harness/asset_review.py / harness/asset_describe.py) plus its kind/title.
#
# A no-op for a tenant with no reviewed alt text at all: with nothing to
# score against, guessing from kind/title alone would just as often make the
# writer's own pick worse, not better, so behaviour for an unreviewed tenant
# stays byte-for-byte unchanged.
# ---------------------------------------------------------------------------

_MATCH_STOPWORDS = frozenset({
    "a", "an", "the", "and", "or", "but", "of", "in", "on", "at", "to", "for",
    "with", "is", "are", "was", "were", "be", "been", "being", "this", "that",
    "these", "those", "it", "its", "as", "by", "from", "your", "you", "our",
    "we", "their", "they", "into", "over", "up", "out",
})

_MATCH_TOKEN_RE = re.compile(r"[a-z0-9]+")

# Score an asset needs to beat the writer's own pick by (never just tie it)
# before match_images_to_text will swap a slot's asset_id.
MATCH_REPLACE_THRESHOLD = 2

_TAGS_NOTE_RE = re.compile(r"tags:\s*(.+)$", re.IGNORECASE)


def _match_tokenize(text):
    """Lower-cased word tokens from `text`, stopwords dropped, a trailing
    plural "s" stripped from any token longer than 3 characters (so "panels"
    and "panel" collapse to one token) -- a token-overlap heuristic, not real
    stemming, so "ss"-ending words (glass, etc.) are left alone rather than
    mangled."""
    tokens = []
    for word in _MATCH_TOKEN_RE.findall((text or "").lower()):
        if word in _MATCH_STOPWORDS:
            continue
        if len(word) > 3 and word.endswith("s") and not word.endswith("ss"):
            word = word[:-1]
        tokens.append(word)
    return tokens


def _match_tags_from_note(note):
    """The tag list out of an asset-review.json override's own `note` field
    (harness/asset_describe.py writes `"...; tags: exterior, close-up, ..."`
    there -- see docs/IMAGES.md). [] when `note` carries no "tags:" suffix at
    all (a human reviewer's free-text note, or no note)."""
    if not note:
        return []
    m = _TAGS_NOTE_RE.search(note)
    if not m:
        return []
    return [t.strip() for t in m.group(1).split(",") if t.strip()]


def _match_asset_token_weights(asset, override):
    """token -> weight for one candidate asset's searchable text: the
    REVIEWED alt and its tags (weight 2 each -- the specific, human/vision-
    written signal) plus the asset's own kind/title (weight 1 each -- a much
    weaker, generic fallback). Deliberately never falls back to the asset's
    default/renderer-derived alt (e.g. "<product> – lifestyle photo"): that text
    is the same boilerplate on every asset of a kind, so scoring it would
    just reward matching the kind twice rather than adding real signal."""
    weights = Counter()
    override = override or {}
    for tok in _match_tokenize(override.get("alt") or ""):
        weights[tok] += 2
    for tag in _match_tags_from_note(override.get("note")):
        for tok in _match_tokenize(tag.replace("-", " ")):
            weights[tok] += 2
    for tok in _match_tokenize(asset.get("kind") or ""):
        weights[tok] += 1
    for tok in _match_tokenize(asset.get("title") or ""):
        weights[tok] += 1
    return weights


def _match_score(asset, override, context_tokens):
    """(score, matched_tokens) for one candidate against one slot's context.
    +1 more when the asset's own `model` field (not carried by facts_for()'s
    capped assets today -- see docs/IMAGES.md's "Paragraph-to-image
    matching" section -- but checked defensively so this activates on its
    own if that ever changes) is itself named in the context text."""
    weights = _match_asset_token_weights(asset, override)
    matched = {tok for tok in weights if tok in context_tokens}
    score = sum(weights[tok] for tok in matched)
    model = (asset.get("model") or "").strip().lower()
    if model and model in context_tokens:
        score += 1
        matched.add(model)
    return score, matched


def _match_section_context(section):
    """heading/title + body text of a page.json section-shaped dict (a
    listicle reason, a longform step, an article body_section) -- "" for
    anything else, so a missing/malformed section never crashes the walk."""
    if not isinstance(section, dict):
        return ""
    heading = section.get("heading") or section.get("title") or ""
    parts = [heading]
    if section.get("text"):
        parts.append(section["text"])
    for p in section.get("paragraphs") or []:
        if isinstance(p, dict) and p.get("text"):
            parts.append(p["text"])
    return " ".join(parts).strip()


def _match_slots(page, cartridge_name):
    """(slot path, page.json node holding "asset_id", context text) for
    every image slot this feature covers -- the same four the brief scopes
    it to (docs/IMAGES.md): article's `images[]` (paired with the
    body_section at the same index, since images render immediately after
    body_sections in cartridges/article/template.html and article has no
    per-image structural link to a specific paragraph -- the closest
    non-guessing reading of "the paragraph nearest the image" the schema
    allows), longform's `hero.hero_image` + `how_it_works.steps[].image`,
    product-page's `hero.hero_image`, and listicle's own header `hero`
    slot (cycle 41's page.hero.asset_id, context = headline + dek) plus
    `reasons[].image`.
    Mirrors ground.hero_container's own per-cartridge knowledge rather than
    a blind page.json walk, since which text belongs to which slot is a
    per-schema judgment call."""
    slots = []
    if cartridge_name == "listicle":
        hero = page.get("hero")
        if isinstance(hero, dict) and "asset_id" in hero:
            context = f"{page.get('headline', '')} {page.get('dek', '')}".strip()
            slots.append(("hero", hero, context))
        for i, reason in enumerate(page.get("reasons") or []):
            if not isinstance(reason, dict):
                continue
            image = reason.get("image")
            if isinstance(image, dict):
                slots.append((f"reasons[{i}].image", image, _match_section_context(reason)))
    elif cartridge_name == "longform":
        hero = page.get("hero") or {}
        hero_image = hero.get("hero_image")
        if isinstance(hero_image, dict):
            context = f"{hero.get('headline', '')} {hero.get('subhead', '')}".strip()
            slots.append(("hero.hero_image", hero_image, context))
        for i, step in enumerate((page.get("how_it_works") or {}).get("steps") or []):
            if not isinstance(step, dict):
                continue
            image = step.get("image")
            if isinstance(image, dict):
                slots.append((f"how_it_works.steps[{i}].image", image, _match_section_context(step)))
    elif cartridge_name == "product-page":
        hero = page.get("hero") or {}
        hero_image = hero.get("hero_image")
        if isinstance(hero_image, dict):
            # product-page's hero has no "headline" field of its own
            # (cartridges/product-page/schema.json) -- product_name +
            # promise are the two fields that actually carry the page's
            # own descriptive text.
            context = f"{hero.get('product_name', '')} {hero.get('promise', '')}".strip()
            slots.append(("hero.hero_image", hero_image, context))
    elif cartridge_name == "article":
        sections = page.get("body_sections") or []
        for i, img in enumerate(page.get("images") or []):
            if not isinstance(img, dict):
                continue
            section = sections[min(i, len(sections) - 1)] if sections else None
            slots.append((f"images[{i}]", img, _match_section_context(section)))
    return slots


def _all_page_asset_ids(page):
    """Every asset_id referenced anywhere in page.json -- mirrors
    render.collect_asset_ids exactly (both walk_page(page) with no
    skip_keys), duplicated here rather than imported since render.py
    imports ground as ground_mod (importing back would be circular)."""
    ids = set()
    for _path, node in walk_page(page):
        if isinstance(node, dict) and node.get("asset_id"):
            ids.add(node["asset_id"])
    return ids


def match_images_to_text(page, assets, *, cartridge_name, exclude_ids=frozenset(), allow_ai_renders):
    """Render-time content backstop, run right after enforce_slot_plan (see
    render.render_page): swaps a slot's asset_id for a better-matching
    candidate when one clearly beats the writer's own pick on token overlap
    with the slot's own heading+body text, against each candidate's REVIEWED
    alt/tags (never the generic default alt -- see
    _match_asset_token_weights) plus kind/title. Mutates `page` in place
    (asset_id fields only, same contract as enforce_slot_plan) and returns
    `{"matches": [{"path", "old_id", "new_id", "score", "matched_tokens"},
    ...]}` for the caller to log and persist (render.render_page writes
    these into <run_dir>/.image-selection.json via record_image_matches).

    A no-op (`{"matches": []}`) whenever the tenant has no reviewed alt text
    anywhere -- see the module docstring above this section. Never
    introduces a duplicate asset_id within this page or against
    `exclude_ids` (ids another cartridge in this run already used), and
    never selects a never-eligible kind or a disallowed ai_render -- the
    same eligibility rules enforce_slot_plan applies. A candidate must score
    at least MATCH_REPLACE_THRESHOLD AND strictly beat the current pick's
    own score to replace it; a tie keeps the writer's original choice."""
    tenant = tenant_mod.active()
    review = asset_review.load_asset_review(tenant.brand_dir)
    overrides = (review or {}).get("assets") or {}
    if not any((o.get("alt") or "").strip() for o in overrides.values()):
        return {"matches": []}

    slots = _match_slots(page, cartridge_name)
    if not slots:
        return {"matches": []}

    by_id = {a["id"]: a for a in assets}
    # Every asset id anywhere on the page (not just the slots this pass
    # edits) counts as occupied -- otherwise a swap could duplicate an id
    # sitting in a slot _match_slots doesn't iterate (e.g. the listicle
    # hero before it was added above, or any future slot this feature
    # hasn't been taught about yet).
    used_ids = set(exclude_ids) | _all_page_asset_ids(page)

    matches = []
    for path, node, context_text in slots:
        context_tokens = set(_match_tokenize(context_text))
        if not context_tokens:
            continue
        current_id = node.get("asset_id")
        current_asset = by_id.get(current_id)
        if current_asset is None:
            continue  # enforce_slot_plan already backstops a missing/bad id

        best_score, _ = _match_score(current_asset, overrides.get(current_id), context_tokens)
        best_asset, best_tokens = None, set()
        for asset in assets:
            asset_id = asset["id"]
            if asset_id == current_id or asset_id in used_ids:
                continue
            if asset.get("kind") in HERO_NEVER_KINDS:
                continue
            if asset.get("ai_generated") and not allow_ai_renders:
                continue
            score, matched_tokens = _match_score(asset, overrides.get(asset_id), context_tokens)
            if score > best_score:
                best_score, best_asset, best_tokens = score, asset, matched_tokens

        if best_asset is not None and best_score >= MATCH_REPLACE_THRESHOLD:
            node["asset_id"] = best_asset["id"]
            used_ids.discard(current_id)
            used_ids.add(best_asset["id"])
            matches.append({
                "path": path, "old_id": current_id, "new_id": best_asset["id"],
                "score": best_score, "matched_tokens": sorted(best_tokens),
            })
    return {"matches": matches}


def record_image_matches(run_dir, cartridge_name, matches):
    """Companion to record_used_asset_ids: writes match_images_to_text's own
    decisions into the same <run_dir>/.image-selection.json, under a
    "matches" key keyed by cartridge_name (same overwrite-only-this-
    cartridge's-own-entry contract as record_used_asset_ids) -- so a
    reviewer can see why a slot's asset_id changed, not just what it ended
    up as."""
    path = Path(run_dir) / _SELECTION_STATE_FILENAME
    data = load_used_asset_ids(run_dir)
    all_matches = dict(data.get("matches") or {})
    all_matches[cartridge_name] = matches
    data["matches"] = all_matches
    path.write_text(json.dumps(data, indent=2) + "\n")
    return path


def benefit_allowlist_ids(tenant=None):
    """Cleared, product-wide (not per-model) claims every product's facts_pack
    always carries -- what the product actually does or is built with, as
    opposed to price/shipping/warranty/returns. From tenant.yaml's
    benefit_allowlist_ids; without them the writer has nothing to cite when
    describing the product itself."""
    tenant = tenant or tenant_mod.active()
    return set(tenant.get("benefit_allowlist_ids") or ())


class FactsSource(Protocol):
    def facts_for(self, product_slug, ad_brief) -> dict: ...


def load_claims_config(claims_dir):
    """The tenant's claims/config.json, with defaults filled in for any missing
    key. Kept as a directory-taking function so a caller with only a claims dir
    (a test, a one-off script) still works; a full run goes through
    tenant.claims_config, which layers tenant.yaml's defaults underneath."""
    config_path = Path(claims_dir) / "config.json"
    config = dict(DEFAULT_CONFIG)
    if config_path.exists():
        config.update(json.loads(config_path.read_text()))
    return config


# Fix cycle 9 item 2: product inference by price -- only claims_made and the
# ad's own hook/promise/angle count as "the ad brief contains a dollar
# amount"; speaker_experience is deliberately excluded (a hedge like "around
# $200 a month" is the speaker's own estimate, not a quoted price, and
# ingest.py fix cycle 9 item 3 already keeps it out of claims_made).


def _quoted_dollar_amounts(ad_brief):
    fields = [ad_brief.get("hook", ""), ad_brief.get("promise", ""), ad_brief.get("angle", "")]
    fields += [c for c in ad_brief.get("claims_made", []) if isinstance(c, str)]
    text = " ".join(f for f in fields if isinstance(f, str))
    amounts = []
    for m in DOLLAR_AMOUNT_RE.finditer(text):
        try:
            amounts.append(float(m.group(1).replace(",", "")))
        except ValueError:
            continue
    return amounts


def _pick_by_quoted_price(active_products, ad_brief):
    """(amount, product) for the one active product whose current price is
    within $1 of a dollar amount quoted in the ad brief, or None if no
    amount is quoted, none lines up with any active product's price, or more
    than one distinct product would match (ambiguous -- not a signal)."""
    matches = []
    for amount in _quoted_dollar_amounts(ad_brief):
        for p in active_products:
            price = p.get("price")
            if price is None:
                continue
            try:
                price = float(price)
            except (TypeError, ValueError):
                continue
            if abs(amount - price) <= 1.0:
                matches.append((amount, p))
    distinct_slugs = {p["slug"] for _, p in matches}
    if len(distinct_slugs) == 1:
        return matches[0]
    return None


def select_drive_assets(assets_index, model_slug, limit=DRIVE_ASSET_MAX, *, exclude_ids=frozenset()):
    """Up to `limit` Drive assets for model_slug, in tier order
    (lifestyle/interior, then render, then installation), never
    video/logo/ugc, skipping any asset marked excluded. `exclude_ids` (full
    asset ids, e.g. asset_review.excluded_ids()'s result) is filtered out of
    the candidate pool BEFORE tiering/capping -- cycle 36 fix -- so a
    reviewer-excluded top-tier asset makes room for the next eligible one
    instead of just shrinking the capped result by one."""
    candidates = [
        a
        for a in assets_index.get("assets", [])
        if a.get("model") == model_slug
        and a.get("kind") not in DRIVE_ASSET_NEVER_KINDS
        and not a.get("excluded")
        and f"asset-drive-{a['id']}" not in exclude_ids
    ]
    download_pattern = assets_index.get("download_url_pattern", "https://drive.google.com/uc?export=download&id={id}")
    selected, seen = [], set()
    for tier in DRIVE_ASSET_TIERS:
        if len(selected) >= limit:
            break
        for a in candidates:
            if len(selected) >= limit:
                break
            if a["id"] in seen or a.get("kind") not in tier:
                continue
            seen.add(a["id"])
            selected.append(
                {
                    "id": f"asset-drive-{a['id']}",
                    "drive_id": a["id"],
                    "url": download_pattern.format(id=a["id"]),
                    "kind": a.get("kind"),
                    "alt": a.get("title") or f"{model_slug} sauna",
                }
            )
    return selected


def select_listicle_pack_assets(
    pack_index, model_slug, allow_ai_renders, limit=LISTICLE_PACK_ASSET_MAX, *, exclude_ids=frozenset()
):
    """Up to `limit` assets from brand/assets-listicle-pack.json for
    model_slug (whichever models the pack covers), real photos (photo_product,
    photo_install) and brand stills (still_video) first, an ai_render only if
    `allow_ai_renders` is true (claims/config.json's allow_ai_renders, default
    False) -- never selected otherwise, per docs/DRIVE-AUDIT-LISTICLE.md's
    policy-decision-needed flag. An excluded asset is skipped either way.
    `exclude_ids` (see select_drive_assets) is filtered out of the candidate
    pool before tiering/capping -- cycle 36 fix."""
    candidates = [
        a
        for a in pack_index.get("assets", [])
        if (a.get("model") == model_slug or a.get("kind") == "still_video")
        and not a.get("excluded")
        and f"asset-listicle-{a['id']}" not in exclude_ids
    ]
    download_pattern = pack_index.get("download_url_pattern", "https://drive.google.com/uc?export=download&id={id}")
    selected, seen = [], set()
    for tier in LISTICLE_PACK_TIERS:
        if len(selected) >= limit:
            break
        for a in candidates:
            if len(selected) >= limit:
                break
            if a["id"] in seen or a.get("kind") not in tier:
                continue
            if a.get("ai_generated") and not allow_ai_renders:
                continue
            seen.add(a["id"])
            selected.append(
                {
                    "id": f"asset-listicle-{a['id']}",
                    "drive_id": a["id"],
                    "url": download_pattern.format(id=a["id"]),
                    "kind": a.get("kind"),
                    "alt": a.get("title") or f"{model_slug} sauna",
                    "ai_generated": bool(a.get("ai_generated")),
                }
            )
    return selected


def full_asset_pool(store, product, config):
    """Cycle 36: the UNCAPPED pool of every asset a human reviewer could
    ever act on for product -- every Shopify image, every Drive asset for
    the model that facts_for() would ever be willing to select (not in
    DRIVE_ASSET_NEVER_KINDS, and not excluded in the manifest), and every
    listicle-pack asset for the model, including ai_renders (each flagged
    ai_generated so the review page can badge it -- unlike
    select_listicle_pack_assets, this never gates them on allow_ai_renders).
    Unlike facts_for()'s own assets list, this ignores DRIVE_ASSET_MAX/
    LISTICLE_PACK_ASSET_MAX and does NOT apply asset-review.json -- the
    review page needs to show an already-excluded asset too, greyed out, so
    it can be un-excluded. config is accepted for symmetry with facts_for;
    the full pool does not currently read any claims-config value.
    Each entry: id, url, drive_id (drive/listicle only), kind, source
    ("shopify"|"drive"|"listicle"), model, title, alt (current default),
    ai_generated."""
    name_slug = product_name_slug(product["name"])
    pool = [
        {
            "id": f"asset-{product['slug']}-{i + 1}",
            "url": url,
            "kind": "image",
            "source": "shopify",
            "model": name_slug,
            "title": None,
            "alt": f"{product['name']} sauna",
            "ai_generated": False,
        }
        for i, url in enumerate(product.get("image_urls", []))
    ]

    assets_index = store._load_assets_index()
    drive_pattern = assets_index.get("download_url_pattern", "https://drive.google.com/uc?export=download&id={id}")
    for a in assets_index.get("assets", []):
        if a.get("model") != name_slug or a.get("kind") in DRIVE_ASSET_NEVER_KINDS or a.get("excluded"):
            continue
        pool.append(
            {
                "id": f"asset-drive-{a['id']}",
                "drive_id": a["id"],
                "url": drive_pattern.format(id=a["id"]),
                "kind": a.get("kind"),
                "source": "drive",
                "model": name_slug,
                "title": a.get("title"),
                "alt": a.get("title") or f"{name_slug} sauna",
                "ai_generated": False,
            }
        )

    if name_slug in listicle_pack_models():
        pack_index = store._load_listicle_pack_index()
        pack_pattern = pack_index.get("download_url_pattern", "https://drive.google.com/uc?export=download&id={id}")
        for a in pack_index.get("assets", []):
            if (a.get("model") != name_slug and a.get("kind") != "still_video") or a.get("excluded"):
                continue
            pool.append(
                {
                    "id": f"asset-listicle-{a['id']}",
                    "drive_id": a["id"],
                    "url": pack_pattern.format(id=a["id"]),
                    "kind": a.get("kind"),
                    "source": "listicle",
                    "model": name_slug,
                    "title": a.get("title"),
                    "alt": a.get("title") or f"{name_slug} sauna",
                    "ai_generated": bool(a.get("ai_generated")),
                }
            )
    return pool


class LocalFactsSource:
    """Reads claims/products.json and claims/verified.json from disk."""

    def __init__(self, claims_dir):
        self.claims_dir = Path(claims_dir)
        self.brand_dir = self.claims_dir.parent / "brand"
        self._products = None
        self._verified = None
        self._assets_index = None
        self._listicle_pack_index = None

    def _load(self):
        if self._products is None:
            self._products = json.loads((self.claims_dir / "products.json").read_text())["products"]
        if self._verified is None:
            self._verified = json.loads((self.claims_dir / "verified.json").read_text())

    def _load_assets_index(self):
        if self._assets_index is None:
            assets_path = self.brand_dir / "assets.json"
            self._assets_index = json.loads(assets_path.read_text()) if assets_path.exists() else {"assets": []}
        return self._assets_index

    def _load_listicle_pack_index(self):
        if self._listicle_pack_index is None:
            pack_path = self.brand_dir / "assets-listicle-pack.json"
            self._listicle_pack_index = json.loads(pack_path.read_text()) if pack_path.exists() else {"assets": []}
        return self._listicle_pack_index

    def all_verified_claims(self, live_price_claims=None, extra_claims=None):
        """The full claims/verified.json universe, for gating ad_brief.claims_made
        (which can reference anything approved, not just the eventual product's
        curated facts_pack subset). live_price_claims (fix 2), if given, is a
        slug -> claim map of this run's freshly-fetched price claims, which
        take priority over the static price-* entries they replace.
        extra_claims (fix cycle 9 item 1), if given, is this run's freshly-
        seeded PDP claims (pdp_claims.seed_pdp_claims) -- appended as-is,
        never written to claims/verified.json."""
        self._load()
        claims = self._verified
        if live_price_claims:
            live_by_id = {c["id"]: c for c in live_price_claims.values()}
            claims = [c for c in claims if c["id"] not in live_by_id]
            claims = claims + list(live_by_id.values())
        if extra_claims:
            claims = list(claims) + list(extra_claims)
        return claims

    def active_products(self):
        """Cycle 36: every active (`active: true`) product, sorted by name --
        harness/serve.py's /images index iterates this to list a product per
        row; nothing else in the harness needed a plain "list the active
        products" call until now (pick_product_with_warning below builds the
        same filter inline for its own ad-matching logic)."""
        self._load()
        return sorted((p for p in self._products.values() if p.get("active", True)), key=lambda p: p["name"])

    def pick_product(self, product_slug, ad_brief):
        product, _warning = self.pick_product_with_warning(product_slug, ad_brief)
        return product

    def pick_product_with_warning(self, product_slug, ad_brief):
        """Cycle 8 problem 1b: an explicit --product still wins outright. Otherwise
        match each active model's real name (never an alias, a nickname, or a
        capacity phrase -- only the product's own `name` field is matched)
        against the ad's transcript/brief, case-insensitively and on a whole
        word/phrase boundary so a short model name does not match inside some
        unrelated longer word. If exactly one model is
        named, pick it. If several are named, pick whichever is mentioned first
        in the haystack. If none is named, fall back to the default product (or
        the first active one if none is marked default) and return a warning
        string for REVIEW.md; a `product_slug`/named match never carries a
        warning. Discontinued models (`active: false`) are never picked either
        way.
        """
        self._load()
        products = self._products

        if product_slug:
            for slug, p in products.items():
                if slug == product_slug or p["name"].lower() == product_slug.lower():
                    return p, None
            raise UnknownProduct(
                f"unknown --product: {product_slug!r}; available: "
                + ", ".join(sorted(products))
            )

        active_products = [p for p in products.values() if p.get("active", True)]

        haystack = " ".join(
            [
                ad_brief.get("transcript_or_text", ""),
                ad_brief.get("hook", ""),
                ad_brief.get("promise", ""),
                ad_brief.get("angle", ""),
            ]
        ).lower()

        named = []
        for p in active_products:
            pattern = r"\b" + re.escape(p["name"].lower()) + r"\b"
            m = re.search(pattern, haystack)
            if m:
                named.append((m.start(), p))
        if named:
            named.sort(key=lambda t: t[0])
            return named[0][1], None

        # Fix cycle 9 item 2: no model named -- if the ad quotes a dollar
        # amount that lines up (within $1) with exactly one active product's
        # current price, infer that product rather than falling through to
        # the default. price-comparison-v2.mov never names a model ("I think
        # I'm going to buy the sauna") but does say "$5,450", which is
        # exactly one active model's price and no other's.
        price_pick = _pick_by_quoted_price(active_products, ad_brief)
        if price_pick:
            amount, product = price_pick
            return product, f"product inferred from quoted price {format_price(amount)} = {product['name']}"

        for p in active_products:
            if p.get("default"):
                return p, f"product not named in ad; defaulted to {p['name']}"

        default_product = active_products[0] if active_products else next(iter(products.values()))
        return default_product, f"product not named in ad; defaulted to {default_product['name']}"

    def _comparison_targets(self, product):
        """Kimi long-run phase 6: the subjects a comparison cartridge may put
        in its spec table, and the claims that back their rows.

        Two sources:
        - the tenant's OWN other active products (from claims/products.json;
          discontinued models -- active: false -- are never targets), each
          row citing the model's own spec-/price- claim from
          claims/verified.json;
        - claims/competitors/*.json entries, each one a sourced comparison
          subject -- loaded ONLY once its file carries an approved_by (a
          pending file is skipped, so no run can name a competitor before
          an operator approves the competitor claims; see the directory's
          README).

        Returns (targets, backing_claims): targets is the writer-facing list;
        backing_claims is the claim entries (id/text/category/source) that
        must join facts_pack.verified_claims so the page gate can cite the
        rows."""
        self._load()
        tenant = tenant_mod.active()
        targets = []
        backing = []
        by_id = {c["id"]: c for c in self._verified}
        for p in self._products.values():
            if p["slug"] == product["slug"] or not p.get("active", True):
                continue
            rows = []
            price_claim = by_id.get(f"price-{product_name_slug(p['name'])}")
            if price_claim:
                rows.append({"label": "Price", "text": price_claim["text"], "claim_ids": [price_claim["id"]]})
                backing.append(price_claim)
            for s in p.get("specs", []):
                claim = by_id.get(s.get("claim_id") or "")
                if claim:
                    rows.append({"label": s["label"], "text": s["value"], "claim_ids": [claim["id"]]})
                    backing.append(claim)
            if rows:
                targets.append({"id": product_name_slug(p["name"]),
                                "name": tenant.display_product_name(p["name"]),
                                "short_name": tenant.display_product_name(p.get("short_name", p["name"])),
                                "kind": "own-product", "rows": rows})

        competitors_dir = self.claims_dir / "competitors"
        if competitors_dir.is_dir():
            for path in sorted(competitors_dir.glob("*.json")):
                entry = json.loads(path.read_text())
                if not entry.get("approved_by"):
                    continue  # pending operator approval -- never loaded
                rows = []
                for row in entry.get("rows", []):
                    claim = {"id": row["id"], "text": row["text"], "category": "comparison", "source": row["source"]}
                    rows.append({"label": row["label"], "text": row["text"], "claim_ids": [row["id"]]})
                    backing.append(claim)
                if rows:
                    targets.append({"id": entry["id"], "name": entry["name"],
                                    "kind": entry.get("kind", "competitor"), "rows": rows})
        # dedupe backing claims by id, preserving order
        seen, unique = set(), []
        for c in backing:
            if c["id"] not in seen:
                seen.add(c["id"])
                unique.append(c)
        return targets, unique

    def _model_options(self, product, live_price_claims=None):
        """Cycle 41: the listicle model picker's rows -- up to
        MODEL_OPTION_MAX of the tenant's OWN active products (the run's
        product first, then the closest in price), each with a one-line fit
        built from its claim-backed spec rows, its published price, and its
        own product URL.

        Returns (options, backing_claims) the same way _comparison_targets
        does: backing_claims are the price/spec claims that must join
        facts_pack.verified_claims so the rendered page's Sources list can
        cite every line the picker shows. A product with no price claim at
        all is skipped rather than shown with an unsourced number.
        `live_price_claims` (pipeline's live_price_claims_by_slug) is
        preferred over the static claim so the picker never shows a price
        this run already knows is stale."""
        self._load()
        tenant = tenant_mod.active()
        live_price_claims = live_price_claims or {}
        by_id = {c["id"]: c for c in self._verified}
        active = [p for p in self._products.values() if p.get("active", True)]
        run_price = float(product.get("price") or 0)
        others = sorted(
            (p for p in active if p["slug"] != product["slug"]),
            key=lambda p: (abs(float(p.get("price") or 0) - run_price), p["slug"]),
        )
        options, backing = [], []
        for p in ([product] if product.get("active", True) else []) + others:
            if len(options) >= MODEL_OPTION_MAX:
                break
            name_slug = product_name_slug(p["name"])
            price_claim = live_price_claims.get(p["slug"]) or by_id.get(f"price-{name_slug}")
            if not price_claim:
                continue
            claim_ids = [price_claim["id"]]
            backing.append(price_claim)
            fit_parts = []
            for spec in p.get("specs", []):
                if (spec.get("label") or "").strip().lower() not in MODEL_FIT_SPEC_LABELS:
                    continue
                claim = by_id.get(spec.get("claim_id") or "")
                if not claim:
                    continue
                fit_parts.append(spec["value"].rstrip("."))
                claim_ids.append(claim["id"])
                backing.append(claim)
            options.append({
                # Cycle 52: the picker's rows are read by a buyer, so the
                # storefront title prefix is stripped here (tenant.yaml's
                # product_display_strip_prefix). The URL beside it is not.
                "name": tenant.display_product_name(p.get("short_name") or p["name"]),
                "url": p["url"],
                "price_text": format_price(p["price"]),
                "fit": " \u00b7 ".join(fit_parts),
                "claim_ids": claim_ids,
            })
        seen, unique = set(), []
        for c in backing:
            if c["id"] not in seen:
                seen.add(c["id"])
                unique.append({"id": c["id"], "text": c["text"], "category": c["category"], "source": c["source"]})
        return options, unique

    def _model_compare(self, product, live_price_claims=None):
        """Cycle 54: the product-page `pdp` look's compare table -- the same
        models the listicle's model picker offers (_model_options: the run's
        own product first, then the tenant's other active products closest
        in price), laid out as columns, with a price row and up to
        MODEL_COMPARE_MAX_SPEC_ROWS spec rows. Every cell carries the claim
        id that backs it; a model with no verified value for a row shows an
        empty cell (None), never a guess. Only ever the tenant's OWN
        products -- no competitor data enters this table.

        Returns (compare_or_None, backing_claims) in _model_options' shape.
        None when fewer than two models can be compared."""
        self._load()
        options, backing = self._model_options(product, live_price_claims)
        if len(options) < 2:
            return None, backing
        by_id = {c["id"]: c for c in self._verified}
        by_url = {p["url"]: p for p in self._products.values()}
        records = [by_url.get(o["url"]) or {} for o in options]

        def spec_cell(record, label):
            for spec in record.get("specs", []):
                if (spec.get("label") or "").strip().lower() != label.lower():
                    continue
                claim = by_id.get(spec.get("claim_id") or "")
                if claim:
                    return {"text": spec["value"], "claim_ids": [claim["id"]]}, claim
            return None, None

        labels = []
        for record in records:
            for spec in record.get("specs", []):
                label = (spec.get("label") or "").strip()
                if label and label.lower() not in {x.lower() for x in labels}:
                    labels.append(label)

        rows = [{
            "label": "Price",
            "cells": [{"text": o["price_text"], "claim_ids": o["claim_ids"][:1]} for o in options],
        }]
        extra = []
        for label in labels:
            if len(rows) > MODEL_COMPARE_MAX_SPEC_ROWS:
                break
            cells, claims = zip(*(spec_cell(r, label) for r in records), strict=True)
            if sum(1 for c in cells if c) < MODEL_COMPARE_MIN_MODELS:
                continue
            rows.append({"label": label, "cells": list(cells)})
            extra.extend(c for c in claims if c)

        seen = {c["id"] for c in backing}
        for c in extra:
            if c["id"] not in seen:
                seen.add(c["id"])
                backing.append({"id": c["id"], "text": c["text"], "category": c["category"], "source": c["source"]})
        compare = {
            "models": [
                {"name": o["name"], "url": o["url"], "current": o["url"] == product["url"]}
                for o in options
            ],
            "rows": rows,
        }
        return compare, backing

    def facts_for(self, product_slug, ad_brief, *, config=None, live_price_claim=None, reviews_claim=None, pdp_claims=None, include_comparison=False, include_listicle=False, include_product_page=False, live_price_claims=None, log=None):
        self._load()
        product = self.pick_product(product_slug, ad_brief)
        config = config or load_claims_config(self.claims_dir)

        specs = [dict(s) for s in product.get("specs", [])]
        shopify_assets = [
            {"id": f"asset-{product['slug']}-{i + 1}", "url": url, "kind": "image", "alt": f"{product['name']} sauna"}
            for i, url in enumerate(product.get("image_urls", []))
        ]
        name_slug = product_name_slug(product["name"])
        # Cycle 36: human reviewer overrides (harness/serve.py's /images
        # site, tenants/<t>/brand/asset-review.json), loaded once. Cycle 36
        # fix: an excluded id is passed into select_drive_assets/
        # select_listicle_pack_assets as exclude_ids so it's skipped BEFORE
        # tiering/capping -- excluding one of DRIVE_ASSET_MAX's top picks
        # then makes room for the next eligible asset, rather than just
        # shrinking the capped result by one. apply_asset_review below still
        # runs on the final list: it's what actually swaps in reviewer alt
        # text, and it's the only thing that ever excludes a Shopify image
        # (never capped/tiered, so there's no backfill to lose there).
        review = asset_review.load_asset_review(self.brand_dir, log=log)
        exclude_ids = asset_review.excluded_ids(review, shopify_assets)
        drive_assets = select_drive_assets(self._load_assets_index(), name_slug, exclude_ids=exclude_ids)
        listicle_pack_assets = []
        if name_slug in listicle_pack_models():
            listicle_pack_assets = select_listicle_pack_assets(
                self._load_listicle_pack_index(), name_slug, bool(config.get("allow_ai_renders")),
                exclude_ids=exclude_ids,
            )
        assets = shopify_assets + drive_assets + listicle_pack_assets  # Shopify images stay first, as hero (fix 8)
        assets = asset_review.apply_asset_review(assets, review, log=log)

        price_id = f"price-{name_slug}"
        spec_ids = {s["claim_id"] for s in specs if s.get("claim_id")}
        # Fix cycle 8 problem 2: claims/products.json's own `specs` list only
        # ever carried the handful of fields Shopify already exposed (capacity,
        # cabin material, max temp, ...) -- per-model facts seeded from g Brain
        # (dimensions, electrical, red light, heater, wood: `spec-<model>-*`,
        # and the older `gbrain-<model>-*` set) were never wired
        # into facts_pack at all, so the writer had nothing to cite even once
        # the claim existed in claims/verified.json. Any verified claim whose
        # id is namespaced to this model is citable, not just the ones already
        # listed as an explicit spec-table row.
        spec_ids |= {
            c["id"]
            for c in self._verified
            if c["id"].startswith(f"spec-{name_slug}-") or c["id"].startswith(f"gbrain-{name_slug}-")
        }
        universal_ids = (
            set(tenant_mod.active().get("universal_claim_ids") or ()) | {price_id}
            | spec_ids
            | benefit_allowlist_ids()
        )
        verified_claims = [
            {"id": c["id"], "text": c["text"], "category": c["category"], "source": c["source"]}
            for c in self._verified
            if c["id"] in universal_ids
        ]

        # Fix cycle 9 item 1: this run's freshly-seeded PDP claims for this
        # product (pdp_claims.seed_pdp_claims) -- never in claims/verified.json,
        # so they're not covered by universal_ids/self._verified above; add
        # directly, scoped to this product's own id namespace.
        pdp_claims_for_product = [c for c in (pdp_claims or []) if c["id"].startswith(f"pdp-{name_slug}-")]
        verified_claims.extend(
            {"id": c["id"], "text": c["text"], "category": c["category"], "source": c["source"]}
            for c in pdp_claims_for_product
        )

        by_id = {c["id"]: c["text"] for c in self._verified}
        by_id.update({c["id"]: c["text"] for c in pdp_claims_for_product})

        # Fix 2: a live price claim (fetched this run, dated today, sourced
        # to the product URL) replaces the static claims/verified.json entry
        # when one was fetched; otherwise fall back to the static claim above.
        if live_price_claim is not None:
            verified_claims = [c for c in verified_claims if c["id"] != price_id]
            verified_claims.append(
                {
                    "id": live_price_claim["id"],
                    "text": live_price_claim["text"],
                    "category": live_price_claim["category"],
                    "source": live_price_claim["source"],
                }
            )

        reviews_summary = None
        if reviews_claim is not None:
            verified_claims.append(
                {
                    "id": reviews_claim["id"],
                    "text": reviews_claim["text"],
                    "category": reviews_claim["category"],
                    "source": reviews_claim["source"],
                }
            )
            reviews_summary = {"text": reviews_claim["text"], "claim_ids": [reviews_claim["id"]]}

        # Fix 1: financing never carries a lender/monthly figure unless
        # claims/config.json's financing_lender has been set by an operator, and a
        # compare-at price is never even offered to the writer unless
        # show_compare_at_price is true.
        financing = {"available": True, "lender": config.get("financing_lender"), "monthly": None}
        compare_at_price = product.get("compare_at_price") if config.get("show_compare_at_price") else None

        # Fix cycle 6 item 2: every product's short_name/title/model name
        # across the whole catalog (not just the one this run is about) --
        # claims.py strips these out of a text field before checking it for
        # a bare digit, so writing a short_name with a capacity digit never
        # by itself forces a claim_id onto a sentence that has nothing else
        # to cite. A generic "N-Person" capacity token is covered separately
        # by claims.py's own regex, not by this list.
        tenant = tenant_mod.active()
        digit_exempt_terms = sorted(
            {
                form
                for p in self._products.values()
                for t in (p.get("short_name"), p.get("title"), p.get("name"))
                if t
                # Cycle 52: both the raw catalog form and the display form
                # (product_display_strip_prefix removed). The writer only
                # ever sees the display form, but a page can still quote a
                # claim that carries the raw one, and this list is what
                # claims.py subtracts before looking for a bare digit.
                for form in {t, tenant.display_product_name(t)}
            }
        )

        pack = {
            "product": {
                # Cycle 52: the writer is handed the DISPLAY form of every
                # product name so it can never write the storefront's own
                # title prefix into a sentence. "slug" and "url" below are
                # identifiers and stay exactly as the catalog has them.
                "name": tenant.display_product_name(product["name"]),
                "short_name": tenant.display_product_name(product.get("short_name", product["name"])),
                "slug": product["slug"],
                "url": product["url"],
                "price": product["price"],
                "compare_at_price": compare_at_price,
                "financing": financing,
                "image_urls": product.get("image_urls", []),
            },
            "specs": specs,
            "warranty": by_id.get("warranty-terms"),
            "shipping": by_id.get("shipping-policy"),
            "returns": by_id.get("returns-policy"),
            "reviews_summary": reviews_summary,
            "verified_claims": verified_claims,
            "assets": assets,
            # Fix 2 (cycle 2): the ad speaker's first-person story is
            # attributed to "a customer" unless an operator has put a real,
            # consented name in claims/config.json.
            "speaker_name": config.get("speaker_name"),
            "digit_exempt_terms": digit_exempt_terms,
            # Cycle 31: additive -- the writer still picks freely from
            # "assets" above; this is a recommendation (by id, not a
            # duplicated copy of each asset -- test_facts_pack_stays_small
            # caps facts_pack's size) plus what render.enforce_slot_plan
            # checks a cartridge's actual picks against (render.py reads
            # allow_ai_renders itself, from tenant.claims_config, rather
            # than this pack carrying a second copy of it). Never removes
            # or reorders "assets" itself.
            "image_slots": _lean_slot_plan(build_slot_plan(assets, allow_ai_renders=bool(config.get("allow_ai_renders")))),
        }
        if include_listicle or include_product_page:
            # Cycle 41: only a run whose selected cartridges include listicle
            # gets the model picker's rows (and their backing claims joined
            # into the citable universe) -- every other run's facts_pack is
            # byte-identical to before. Cycle 54: the product-page `pdp`
            # look shows the same models, so it opts in the same way.
            options, backing_claims = self._model_options(product, live_price_claims)
            pack["model_options"] = options
            existing_ids = {c["id"] for c in pack["verified_claims"]}
            pack["verified_claims"] = pack["verified_claims"] + [
                c for c in backing_claims if c["id"] not in existing_ids
            ]
        if include_product_page:
            # Cycle 54: the product-page `pdp` look's model compare table --
            # the same models as above, with claim-backed spec rows.
            compare, backing_claims = self._model_compare(product, live_price_claims)
            if compare:
                pack["model_compare"] = compare
                existing_ids = {c["id"] for c in pack["verified_claims"]}
                pack["verified_claims"] = pack["verified_claims"] + [
                    c for c in backing_claims if c["id"] not in existing_ids
                ]
        if include_comparison:
            # Kimi long-run phase 6: only a run whose selected cartridges
            # include comparison gets the targets list (and the backing
            # claims joined into the citable universe) -- every other run's
            # facts_pack is byte-identical to before.
            targets, backing_claims = self._comparison_targets(product)
            pack["comparison_targets"] = targets
            existing_ids = {c["id"] for c in pack["verified_claims"]}
            pack["verified_claims"] = pack["verified_claims"] + [
                {"id": c["id"], "text": c["text"], "category": c["category"], "source": c["source"]}
                for c in backing_claims
                if c["id"] not in existing_ids
            ]
        return pack
