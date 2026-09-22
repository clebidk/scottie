"""Cycle 32: the simplicity gate (docs/RESEARCH-HORMOZI-LANDING.md section 3,
"Proposed simplicity gate: measurable checks").

Four page.json-level checks:

  1. find_above_fold_link_violations -- at most 1 distinct link in the
     page's above-the-fold region, defined per cartridge below.
  2. find_headline_band_violation -- the cartridge's own headline field is
     inside its schema.json's `headline_word_band` [lo, hi].
  3. find_offer_element_violations -- a second, differently-worded CTA text,
     or a second financing sentence, anywhere on the page (a second cta_url
     -- claims.find_second_cta_violation -- is already an unconditional hard
     gate via gate_page_json; see simplicity_review_lines below for where
     that's surfaced in the Simplicity report alongside these two).
  4. find_value_equation_warnings -- product-page's proof_bullets carry 3
     distinct, non-transactional claim_ids. Soft in every mode -- "does this
     bullet read as outcome/proof/effort" isn't a bright-line count the way
     1-3 are.

1-3 are combined by find_simplicity_violations, which harness/repair.py's
check_page_gates wires in as a hard gate ONLY when this tenant's
simplicity_mode is "enforce" (tenant.yaml; default "warn" -- see
tenants/_template/tenant.yaml). Under "warn" (or with no config at all) they
are advisory only: simplicity_review_lines reports every check's result,
regardless of mode, for REVIEW.md's own "Simplicity" section. Item 4 never
gates, in either mode.

Above-the-fold region per cartridge, per the research doc's own mapping:
  - product-page / longform: the `hero` block, plus the page's top-level
    `cta_url` (schema.json: "reused everywhere cta_text is", including the
    hero -- it is a single top-level field in page.json, not nested under
    `hero`, but it renders inside the fold).
  - article: `headline` + `dek` + `open[0]` (the first paragraph).
  - listicle: `headline` + `dek` + `hero`, plus the page's top-level
    `cta_url`. What is counted is the set of DISTINCT hrefs, never the number
    of anchors: cycle 51's `lander` look puts a primary button and a
    secondary ghost link side by side above the fold, both pointing at the
    same single `cta_url` -- one destination offered twice, not a second
    offer. A look that put a genuinely different destination above the fold
    would still fail here. v0.2's header is a landing-page stack (hero image,
    primary CTA, trust line), so the hero CTA IS the one link allowed above the
    fold. The sticky bottom bar is exempt by construction, not by a special
    case: it renders that same single `cta_url`, so it adds no second href
    for this check to count, and it is renderer chrome that only appears
    once the hero has scrolled past -- it is never above the fold. The
    trust line is renderer-built from facts_pack and never appears in
    page.json at all (harness/listicle.py), so it cannot carry a link.
  - comparison (cycle 56): the same header stack as listicle -- `headline`
    + `dek` + `hero` plus the top-level `cta_url`, so the header CTA is the
    one link above the fold. The model table's per-model links sit below
    the fold and are renderer-built (harness/comparison.py).
  - quiz (cycle 57): `headline` + `dek` + `hero`. The one link above the fold
    is the renderer's "Start the quiz" anchor (#qz-quiz), which is not in
    page.json; answer options are <button> elements, not links; the
    page's `cta_url` renders only on the featured model's result card, below
    the quiz -- so it is not counted as above the fold.

The disclosure paragraph and the byline's "Full bio" link are exempt from
every check here by construction, not by special-case code: both are
rendered by harness/render.py straight from tenant.yaml/authors.yaml and
never appear in page.json at all (confirmed by grep -- neither `render.py`
nor any cartridge's schema.json writes a "disclosure" or "byline" key into
the writer's page.json shape; harness/claims.py's own cycle-30 module
docstring notes the same fact about the same two blocks for the warm-up
gate). Every check in this module reads only page.json, so there is nothing
for them to ever see.
"""
import json

from . import config
from . import tenant as tenant_mod
from .claims import find_second_cta_violation
from .textutil import walk_page

# Same convention as harness/pagechecks.py's own _URL_KEYS: every cartridge's
# CTA is one of these two keys (article nests it at cta.url; the rest use a
# flat top-level cta_url); nothing else in page.json legitimately carries a
# link target.
_URL_KEYS = ("url", "cta_url")

# claims/verified.json's own category taxonomy (tenants/*/claims/verified.json)
# has no dedicated "warranty"/"shipping" category -- those claims (e.g.
# "warranty-terms", "shipping-policy", "gbrain-shipping-timing") live under
# "trust"/"policy" by category, distinguished only by their own id. "price"
# is a real category value, so that one check is a category match; the other
# two are an id-substring match against the claims file's own naming.
_TRANSACTIONAL_CATEGORY = "price"
_TRANSACTIONAL_ID_MARKERS = ("warranty", "shipping", "returns")


# ---------------------------------------------------------------------------
# 2. headline word band -- cartridges/*/schema.json's own headline_word_band
# ---------------------------------------------------------------------------

# Which page.json field is "the headline" per cartridge. product-page has no
# literal `headline` field (see cartridges/product-page/schema.json) -- its
# hero.promise is the one-line hook the ad angle produces, functionally the
# same "read this in one glance" role the other three cartridges' headline
# field plays, so that's what this check measures for product-page.
_HEADLINE_FIELDS = {
    "article": lambda page: page.get("headline"),
    "listicle": lambda page: page.get("headline"),
    "comparison": lambda page: page.get("headline"),
    "quiz": lambda page: page.get("headline"),
    "longform": lambda page: (page.get("hero") or {}).get("headline"),
    "product-page": lambda page: (page.get("hero") or {}).get("promise"),
}

_HEADLINE_PATHS = {
    "article": "$.headline",
    "listicle": "$.headline",
    "comparison": "$.headline",
    "quiz": "$.headline",
    "longform": "$.hero.headline",
    "product-page": "$.hero.promise",
}


def headline_word_band(cartridge_name):
    """This cartridge's own schema.json `headline_word_band` as (lo, hi), or
    None if its schema declares none (e.g. `comparison`, which this cycle
    does not touch)."""
    path = config.REPO_ROOT / "cartridges" / cartridge_name / "schema.json"
    try:
        schema = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return None
    band = schema.get("headline_word_band")
    if not band or len(band) != 2:
        return None
    return tuple(band)


def find_headline_band_violation(page, cartridge_name, band):
    """[] if `band` is None (cartridge has no declared band) or the
    cartridge's headline field's word count falls inside it; otherwise one
    problem dict, same shape this engine's other find_*_violations return."""
    get_headline = _HEADLINE_FIELDS.get(cartridge_name)
    if not band or not get_headline or not isinstance(page, dict):
        return []
    headline = get_headline(page) or ""
    wc = len(headline.split())
    lo, hi = band
    if lo <= wc <= hi:
        return []
    return [{
        "path": _HEADLINE_PATHS.get(cartridge_name, "$.headline"),
        "key": "simplicity:headline_band",
        "issue": (
            f"headline is {wc} word(s) ({headline!r}); {cartridge_name}'s headline_word_band "
            f"requires {lo}-{hi}"
        ),
    }]


# ---------------------------------------------------------------------------
# 1. links above the fold
# ---------------------------------------------------------------------------

def _above_fold_subtrees(page, cartridge_name):
    """(direct_cta_url_or_None, [subtree, ...]) -- the nodes this cartridge's
    above-the-fold region covers. None (not []) means "no above-the-fold
    definition for this cartridge", distinct from an empty region. Cycle
    56: comparison's header is the same stack as listicle's (headline, dek,
    hero, one CTA to the page's single cta_url)."""
    if cartridge_name in ("product-page", "longform"):
        return page.get("cta_url"), [page.get("hero")]
    if cartridge_name == "article":
        open_list = page.get("open") or []
        return None, [open_list[0] if open_list else None]
    if cartridge_name in ("listicle", "comparison"):
        return page.get("cta_url"), [page.get("hero")]
    if cartridge_name == "quiz":
        # Cycle 57: the fold holds the renderer's one start link (a same-page
        # anchor to the quiz, never a page.json url); the option controls are
        # <button>s, not links; the page's cta_url renders only on the result
        # card, below the quiz. So only the hero subtree is in the fold.
        return None, [page.get("hero")]
    return None, None


def find_above_fold_link_violations(page, cartridge_name):
    """[] if the above-the-fold region (see module docstring) carries at
    most 1 distinct link target; otherwise one problem dict listing every
    href found. No-op for a cartridge with no above-the-fold definition."""
    if not isinstance(page, dict):
        return []
    direct_cta_url, subtrees = _above_fold_subtrees(page, cartridge_name)
    if subtrees is None:
        return []
    hrefs = set()
    if isinstance(direct_cta_url, str) and direct_cta_url:
        hrefs.add(direct_cta_url)
    for subtree in subtrees:
        if subtree is None:
            continue
        for _path, node in walk_page(subtree):
            if isinstance(node, dict):
                for key in _URL_KEYS:
                    value = node.get(key)
                    if isinstance(value, str) and value:
                        hrefs.add(value)
    if len(hrefs) > 1:
        return [{
            "path": "$",
            "key": "simplicity:above_fold_links",
            "issue": (
                f"{len(hrefs)} distinct links above the fold {sorted(hrefs)}; at most 1 is allowed "
                "(the single CTA). The disclosure paragraph and byline 'Full bio' link are "
                "renderer-injected and never appear in page.json, so they never count here."
            ),
        }]
    return []


# ---------------------------------------------------------------------------
# 3. one offer element per page
# ---------------------------------------------------------------------------

def _cta_texts(page, cartridge_name):
    texts = set()
    for _path, node in walk_page(page):
        if isinstance(node, dict):
            value = node.get("cta_text")
            if isinstance(value, str) and value:
                texts.add(value)
    if cartridge_name == "article":
        value = (page.get("cta") or {}).get("text")
        if isinstance(value, str) and value:
            texts.add(value)
    return texts


def _financing_texts(page):
    texts = []
    for _path, node in walk_page(page):
        if isinstance(node, dict) and "financing_line" in node:
            financing_line = node["financing_line"]
            text = financing_line.get("text") if isinstance(financing_line, dict) else financing_line
            if isinstance(text, str) and text.strip():
                texts.append(text.strip())
    return texts


def find_offer_element_violations(page, cartridge_name):
    """The new half of item 3: a second, differently-worded CTA text
    anywhere on the page (the same text may repeat verbatim), or a second,
    differently-worded financing sentence. Like the CTA text rule, the SAME
    financing sentence repeating verbatim in more than one render slot is
    fine and expected -- longform's own cartridge.md documents exactly this
    ("wherever financing_line appears (hero or final_cta), its text must be
    exactly the allowed financing sentence"); claims.find_financing_violations
    already forces every occurrence to match that one sentence exactly, so
    this only ever fires on a wording mismatch that gate somehow missed, or
    once a tenant's allowed sentence changes mid-run (financing_lender
    variance) -- never on a plain repeat. A second `cta_url`/offer card is
    already an unconditional hard gate (claims.find_second_cta_violation,
    run inside gate_page_json regardless of simplicity_mode) -- not
    repeated here, see simplicity_review_lines for where it's surfaced in
    the Simplicity report alongside these two."""
    if not isinstance(page, dict):
        return []
    problems = []
    cta_texts = _cta_texts(page, cartridge_name)
    if len(cta_texts) > 1:
        problems.append({
            "path": "$",
            "key": "simplicity:offer_cta_text",
            "issue": (
                f"{len(cta_texts)} distinct CTA texts found on the page {sorted(cta_texts)}; only one "
                "CTA phrase is allowed per page (it may repeat verbatim)"
            ),
        })
    distinct_financing_texts = set(_financing_texts(page))
    if len(distinct_financing_texts) > 1:
        problems.append({
            "path": "$",
            "key": "simplicity:offer_financing",
            "issue": (
                f"{len(distinct_financing_texts)} distinct financing sentences found on the page "
                f"{sorted(distinct_financing_texts)}; only one financing sentence is allowed per page "
                "(it may repeat verbatim)"
            ),
        })
    return problems


# ---------------------------------------------------------------------------
# 4. value-equation checklist (product-page proof_bullets) -- soft only,
# every mode. The writer-facing instruction (tenant-neutral: outcome, proof
# it's likely, time to first benefit/install effort, in that order, verified
# claim ids only) lives in cartridges/product-page/cartridge.md's proof
# bullets rule, next to the existing product-benefit-claims minimum -- this
# is only the after-the-fact soft check.
# ---------------------------------------------------------------------------

def _is_transactional_claim(claim_id, verified_by_id):
    claim = verified_by_id.get(claim_id)
    if claim and claim.get("category") == _TRANSACTIONAL_CATEGORY:
        return True
    lowered = claim_id.lower()
    return any(marker in lowered for marker in _TRANSACTIONAL_ID_MARKERS)


def find_value_equation_warnings(page, cartridge_name, facts_pack):
    """[] unless this is product-page and its proof_bullets fall short of
    the value-equation checklist: 3 distinct claim_ids, none of them a
    price/warranty/shipping/returns claim. Advisory only -- never wired into
    find_simplicity_violations, in either simplicity_mode."""
    if cartridge_name != "product-page" or not isinstance(page, dict):
        return []
    bullets = page.get("proof_bullets") or []
    verified_by_id = {c["id"]: c for c in (facts_pack or {}).get("verified_claims", [])}
    all_ids = []
    for bullet in bullets:
        ids = (bullet.get("claim_ids") or []) if isinstance(bullet, dict) else []
        all_ids.extend(ids)
    distinct_ids = sorted(set(all_ids))
    warnings = []
    if len(distinct_ids) < 3:
        warnings.append(
            f"product-page: proof_bullets cite only {len(distinct_ids)} distinct claim_id(s) "
            f"{distinct_ids} -- the value-equation checklist wants 3 (outcome, proof it's likely, "
            "time to first benefit/install effort), each a different verified claim"
        )
    transactional = [cid for cid in distinct_ids if _is_transactional_claim(cid, verified_by_id)]
    if transactional:
        warnings.append(
            f"product-page: proof_bullets cite a price/warranty/shipping claim_id {transactional} -- "
            "the value-equation checklist wants outcome/proof/effort claims, not transactional ones"
        )
    return warnings


# ---------------------------------------------------------------------------
# combined hard-gate check (items 1-3) and REVIEW.md reporting (items 1-4)
# ---------------------------------------------------------------------------

def find_simplicity_violations(page, cartridge_name):
    """Items 1-3, combined -- what harness/repair.py's check_page_gates
    wires in as a hard gate when this tenant's simplicity_mode is
    "enforce". Item 4 is deliberately excluded; it never gates."""
    problems = []
    problems += find_above_fold_link_violations(page, cartridge_name)
    problems += find_headline_band_violation(page, cartridge_name, headline_word_band(cartridge_name))
    problems += find_offer_element_violations(page, cartridge_name)
    return problems


def _check_line(label, problems):
    if not problems:
        return f"- {label}: PASS"
    details = "; ".join(p["issue"] if isinstance(p, dict) else p for p in problems)
    return f"- {label}: WARN -- {details}"


def simplicity_review_lines(pages, tenant, facts_pack):
    """REVIEW.md's "Simplicity" section -- one PASS/WARN line per check per
    page, computed unconditionally (like the Warm-up window section)
    regardless of simplicity_mode; mode only changes whether
    find_simplicity_violations above is wired into check_page_gates as a
    hard gate."""
    tenant = tenant or tenant_mod.active()
    mode = tenant.get("simplicity_mode", "warn")
    lines = [
        f"Mode: {mode} (tenant.yaml's `simplicity_mode`; \"enforce\" makes items 1-3 hard gates "
        "through the repair loop -- the value-equation checklist always stays soft)."
    ]
    for cartridge_name, page in pages.items():
        band = headline_word_band(cartridge_name)
        band_label = f"Headline word band ({band[0]}-{band[1]})" if band else "Headline word band (n/a)"
        offer_problems = find_second_cta_violation(page) + find_offer_element_violations(page, cartridge_name)
        lines.append(f"### {cartridge_name}")
        lines.append(_check_line("Links above the fold", find_above_fold_link_violations(page, cartridge_name)))
        lines.append(_check_line(band_label, find_headline_band_violation(page, cartridge_name, band)))
        lines.append(_check_line("One offer element", offer_problems))
        if cartridge_name == "product-page":
            lines.append(_check_line("Value-equation checklist (soft)", find_value_equation_warnings(page, cartridge_name, facts_pack)))
    return lines
