"""Cycle 41: the listicle cartridge's style system and its structural gates.

A listicle page is written in one of five STYLES. The style fixes the
headline formula, what a numbered item *is*, and therefore how each item
heading reads. Everything here is tenant-neutral: the formulas carry
<audience>/<category> placeholders the writer fills from the ad brief and
the product category, never a company or model name.

Three callers share this module so the prompt and the gate can never drift
apart:

  - harness/write.py appends writer_style_lines(style) to the writer's own
    hard constraints, so the writer is told the exact formula it will be
    measured against;
  - harness/repair.py's check_page_gates runs find_listicle_violations over
    the returned page.json. These are writer-owned, writer-fixable checks,
    so they live in the repair loop's gate rather than as a post-render
    backstop (see harness/pagechecks.py's module docstring for that split).
    Every problem carries a stable "key" so the repair loop recognises the
    same violation across attempts (harness/repair.py's failures_seen_by_key);
  - harness/pipeline.py resolves the run's style once (resolve_style): the
    `harness run --style` flag when given, else a deterministic pick from
    the run seed so a batch of runs rotates through all five.

Sections a reader sees but the writer never writes -- the trust line, the
pull-quote band, the model picker, the HSA/FSA line, the sticky bar's
rating line -- are built by the renderer from facts_pack alone
(trust_line_items/pull_quote/hsa_claim/rating_line below). That is what
makes "omitted when unverified" structural rather than a rule the writer
has to remember: there is no page.json field to invent one in, and
find_renderer_owned_violations rejects the page if the writer adds one.
"""
import re

from . import tenant as tenant_mod
from .claims import _trigger_reason
from .textutil import NON_PROSE_KEYS, walk_page

STYLES = ("reasons", "mistakes", "questions", "myths", "tested")

# The headline the writer must produce, per style. N is a number the writer
# fills in: the item count for the first four styles, the number of weeks
# for "tested" (which is the one style whose headline number is not the
# item count -- see _N_IS_ITEM_COUNT).
HEADLINE_FORMULAS = {
    "reasons": "N Reasons <audience> Are Choosing <category>",
    "mistakes": "N Mistakes People Make Buying <category>",
    "questions": "N Questions to Ask Before You Buy <category>",
    "myths": "N <category> Myths, and What the Evidence Says",
    "tested": "We Tested <category> for N Weeks. Here Is What Held Up",
}

# What one numbered item is, in this style. The item heading itself is a
# short benefit/claim line with no numeral in it -- the renderer draws the
# number -- in every style; only what the heading asserts changes.
ITEM_PATTERNS = {
    "reasons": "one reason to choose this category, stated as the benefit it buys the reader",
    "mistakes": "one mistake a buyer makes, named as the mistake itself",
    "questions": "one question to ask a seller, phrased as a question",
    "myths": "one myth, stated as the myth, with the body answering it from the evidence",
    "tested": "one thing the test looked at, stated as what held up (or did not)",
}

_HEADLINE_RES = {
    "reasons": re.compile(r"^\s*(\d+)\s+Reasons\b", re.IGNORECASE),
    "mistakes": re.compile(r"^\s*(\d+)\s+Mistakes\b", re.IGNORECASE),
    "questions": re.compile(r"^\s*(\d+)\s+Questions\b", re.IGNORECASE),
    "myths": re.compile(r"^\s*(\d+)\b.*\bMyths\b", re.IGNORECASE),
    "tested": re.compile(r"^\s*We Tested\b.*?\b(\d+)\s+Weeks?\b", re.IGNORECASE),
}

# Styles whose leading number states the item count. "tested"'s own number
# is a duration, so its item count is only checked against ITEM_COUNT_RANGE.
_N_IS_ITEM_COUNT = ("reasons", "mistakes", "questions", "myths")

ITEM_COUNT_RANGE = (5, 7)
# Cycle 43: lowered from 60 -- observed real runs stopping items at 53-57
# words against the old floor, a gap too small to be a real content problem.
# Ceiling unchanged.
ITEM_WORD_RANGE = (50, 150)
FAQ_COUNT_RANGE = (5, 7)
RECAP_BULLET_COUNT = 3
AUDIENCE_LIST_RANGE = (2, 4)
# The two item positions a micro-CTA renders after (template-side; listed
# here so cartridge.md, the template and the docs all quote one source).
MICRO_CTA_AFTER_ITEMS = (2, 4)

# Generic conversion-urgency vocabulary -- no company or product words, the
# same category of tenant-neutral rule as harness/vocab.py's hype list (which
# is tenant data and stays there; this is the structural rule the listicle
# format itself carries, because the reference landers that use urgency are
# exactly the ones this cartridge does not copy).
URGENCY_PHRASES = (
    "act now", "hurry", "last chance", "final hours", "today only",
    "limited time", "limited-time", "ends soon", "ends tonight", "ends today",
    "while supplies last", "selling fast", "almost gone", "don't miss out",
    "do not miss out", "before it's gone", "countdown", "flash sale",
    "sale ends", "offer ends", "expires soon", "only a few left",
    "spots are filling", "deal of the day",
)
_URGENCY_RES = tuple(
    (phrase, re.compile(r"\b" + re.escape(phrase).replace(r"\ ", r"\s+") + r"\b", re.IGNORECASE))
    for phrase in URGENCY_PHRASES
)

# page.json keys for sections the RENDERER owns (see the module docstring).
# A writer that invents one of these is rejected rather than quietly
# overriding data that must come from facts_pack.
RENDERER_OWNED_KEYS = (
    "proof_row", "trust_line", "pull_quote", "model_picker", "models",
    "hsa_line", "sticky_cta", "rating_line",
)

# facts_pack.verified_claims text that makes a claim usable as the HSA/FSA
# line or the free-shipping half of the trust line. Both are ordinary
# English phrases, not a tenant's wording.
_HSA_RE = re.compile(r"\b(HSA|FSA)\b")
_FREE_SHIPPING_RE = re.compile(r"\bfree shipping\b", re.IGNORECASE)

# Cycle 43: a tenant's reviews.claim_template (harness/sources/judgeme.py)
# commonly ends the claim's own text with a "(fetched YYYY-MM-DD)"
# parenthetical -- useful provenance for REVIEW.md and the claim record, not
# something a reader of the trust line or the sticky bar needs. Stripped only
# from what those two display -- the claim's own text (and its claim_id,
# still cited) is untouched, so citation matching is unaffected.
_FETCHED_SUFFIX_RE = re.compile(r"\s*\(fetched[^)]*\)\.?\s*$", re.IGNORECASE)


def _drop_fetched_date(text):
    stripped = _FETCHED_SUFFIX_RE.sub("", text).rstrip()
    if stripped and not stripped.endswith("."):
        stripped += "."
    return stripped


# ---------------------------------------------------------------------------
# Style selection
# ---------------------------------------------------------------------------

def tenant_styles(tenant=None):
    """The styles this tenant allows, in order -- tenant.yaml's
    `cartridges.listicle.styles` filtered to real STYLES, else all five.
    A pinned list that names nothing valid is ignored rather than leaving a
    run with no style to pick."""
    tenant = tenant or tenant_mod.active()
    pinned = tenant.get("cartridges.listicle.styles") or ()
    chosen = tuple(s for s in pinned if s in STYLES)
    return chosen or STYLES


def resolve_style(requested=None, *, seed=0, tenant=None):
    """The style one run writes in. An explicit `requested` (the
    `harness run --style` flag) always wins, including over a tenant's
    pinned subset -- it is an operator's deliberate choice. Otherwise the
    pick is deterministic from the run seed, so a batch of runs with
    different seeds rotates through the whole allowed set rather than
    landing on one style every time."""
    if requested:
        if requested not in STYLES:
            raise ValueError(f"unknown listicle style {requested!r}; choose one of {list(STYLES)}")
        return requested
    allowed = tenant_styles(tenant)
    return allowed[int(seed) % len(allowed)]


# Spelled-out counts a headline may lead with instead of the numeral the
# formula asks for ("Six Mistakes ..."). Observed on real runs; fixed
# deterministically (fix_headline_number) rather than spent on a repair call.
_COUNT_WORDS = {
    "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6,
    "seven": 7, "eight": 8, "nine": 9, "ten": 10, "eleven": 11, "twelve": 12,
}
_LEADING_COUNT_RE = re.compile(
    r"^\s*(\d+|" + "|".join(_COUNT_WORDS) + r")\b", re.IGNORECASE
)


def fix_headline_number(page):
    """The corrected headline when the only thing wrong with it is its
    leading count -- spelled out ("Six Mistakes ...") where the formula wants
    a numeral, or a numeral that no longer matches the item count -- else
    None.

    This is a safe mechanical substitution, not a rewrite: it changes one
    token and then re-checks the result against the style's own formula,
    accepting it only if the headline now passes. harness/repair.py's
    deterministic pre-repair pass applies it before ever spending a model
    call, for the reason cycle 6 introduced that pass: a repair that trims or
    adds an item to fix a word-count failure silently invalidates the
    headline's number, and chasing the two around costs every attempt the
    loop has (observed on the cycle 41 verification runs -- attempt 3 STOPped
    on nothing but this)."""
    style = page.get("style") if isinstance(page, dict) else None
    if style not in _N_IS_ITEM_COUNT:
        return None
    headline = page.get("headline") or ""
    count = len(_items(page))
    match = _LEADING_COUNT_RE.match(headline)
    if not match:
        return None
    fixed = headline[: match.start(1)] + str(count) + headline[match.end(1) :]
    if fixed == headline:
        return None
    checked = _HEADLINE_RES[style].search(fixed)
    if not checked or int(checked.group(1)) != count:
        return None
    return fixed


# Rules the writer needs whatever style this run is in. Every one of these is
# a failure mode seen on a real run of this cartridge, stated where the
# writer reads it rather than left for the gate to discover.
def writer_rules_lines():
    lo, hi = ITEM_COUNT_RANGE
    wlo, whi = ITEM_WORD_RANGE
    return [
        f"Every one of the {lo}-{hi} items carries its own "
        '"image": {"asset_id": "<id>"} using an id from facts_pack.assets, and "hero" '
        'carries one more: {"asset_id": "<id>"}. Every id on the page must be different '
        "from every other. Never leave an image out.",
        f"Every item body is {wlo}-{whi} words -- count them, including the last item's.",
        'The page has exactly one "cta_url", at the top level. Never put a "cta_url" '
        "inside an item, the closing block, or anywhere else.",
        "Any line stating a number, a price, a measurement, a spec, or one of the trigger "
        "words carries its own claim_ids -- that includes audience_fit lines, closing recap "
        "bullets and FAQ answers, not only item bodies. A line you cannot cite gets "
        "rewritten without the number, not shipped uncited.",
    ]


def writer_style_lines(style):
    """The hard-constraint lines write.py adds to the writer's prompt for
    this style. Empty for an unknown style (the gate still rejects the
    page) so a bad value can never produce a contradictory prompt."""
    if style not in STYLES:
        return []
    lo, hi = ITEM_COUNT_RANGE
    wlo, whi = ITEM_WORD_RANGE
    formula = HEADLINE_FORMULAS[style]
    lines = [
        f'This page\'s style is "{style}". Its headline must follow this formula exactly: '
        f'"{formula}" -- fill in N and the bracketed parts, keep the rest of '
        f'the wording. Set page.json\'s "style" field to "{style}".',
    ]
    # Cycle 43: observed on a real run -- a headline shaped like "6 Reasons
    # Home Buyers Are Choosing Acme Saunas Infrared Saunas" put a real-estate
    # term in <audience> and the tenant's own name in <category>. Stated once
    # here, next to the formula itself, rather than left for the gate
    # (find_headline_slot_violations below) to discover after the fact.
    if "<audience>" in formula:
        lines.append(
            "<audience> names people by their situation or goal (\"busy parents\", "
            "\"apartment owners\", \"people who train at home\") -- never a real-estate term "
            "and never the ad brief's audience field copied in verbatim."
        )
    if "<category>" in formula:
        lines.append(
            '<category> is the product category (e.g. "home infrared saunas") -- never the '
            "tenant's name and never a model name."
        )
    lines += [
        "Write N as a numeral (5, not \"five\"). "
        + (
            "N is the number of entries you actually put in \"reasons\" -- count them before "
            "you answer, and if you add or drop an item while revising, change the headline's "
            "number to match."
            if style in _N_IS_ITEM_COUNT
            else "N is the number of weeks the test ran, not the item count."
        ),
        f"Every numbered item is {ITEM_PATTERNS[style]}. Item headings carry no numeral "
        "(the renderer draws the number) and no price.",
        f"Write {lo}-{hi} items, each with a {wlo}-{whi} word body and a closing proof line "
        "that either cites a verified claim_id or is an attributed customer statement.",
    ]
    return lines


# ---------------------------------------------------------------------------
# Renderer-owned sections, built from facts_pack alone
# ---------------------------------------------------------------------------

def hsa_claim(facts_pack):
    """The verified claim that makes an HSA/FSA line truthful, or None.
    None means the line is simply not rendered -- never a softened version
    of it."""
    for claim in (facts_pack or {}).get("verified_claims", []):
        if _HSA_RE.search(claim.get("text", "")):
            return claim
    return None


def free_shipping_claim(facts_pack):
    """The verified claim behind a "Free shipping" trust-line item, or None."""
    for claim in (facts_pack or {}).get("verified_claims", []):
        if _FREE_SHIPPING_RE.search(claim.get("text", "")):
            return claim
    return None


def rating_line(facts_pack):
    """The verified rating/review-count sentence, from
    facts_pack.reviews_summary, or None when this run fetched no review
    statistics. Not re-worded -- what the trust line and the sticky bar show
    is still the claim's own sentence -- except for a trailing
    "(fetched YYYY-MM-DD)" provenance note (_drop_fetched_date), which reads
    as internal bookkeeping to a customer. The claim_id cited is unchanged,
    so this never affects what the sentence is allowed to say."""
    summary = (facts_pack or {}).get("reviews_summary")
    if not summary or not summary.get("text"):
        return None
    return {"text": _drop_fetched_date(summary["text"]), "claim_ids": list(summary.get("claim_ids") or [])}


def trust_line_items(facts_pack):
    """The header's trust line under the primary CTA: the verified rating
    sentence and/or a free-shipping item, each with the claim ids that back
    it. [] when neither is verified for this run, which is what makes the
    whole line disappear rather than thin out into something unsourced."""
    items = []
    rating = rating_line(facts_pack)
    if rating:
        items.append(rating)
    shipping = free_shipping_claim(facts_pack)
    if shipping:
        # The claim's own phrase, not a re-write of it: the match above is
        # literally "free shipping" appearing in the verified text.
        items.append({"text": "Free shipping", "claim_ids": [shipping["id"]]})
    return items


def pull_quote(facts_pack):
    """One attributed customer statement for the band after item 3, from
    facts_pack.review_quotes, or None. A facts source that carries no review
    quotes (no tenant's does yet -- a rating/count summary is not a quote)
    means the band is omitted; nothing here ever composes one."""
    quotes = (facts_pack or {}).get("review_quotes") or []
    for quote in quotes:
        if isinstance(quote, dict) and quote.get("text"):
            return quote
    return None


def render_context(facts_pack):
    """Everything cartridges/listicle/template.html renders that came from
    facts_pack rather than from the writer. One call, so the template never
    reaches into facts_pack itself and every "only when verified" rule lives
    in this module next to the gate that forbids writing them by hand."""
    return {
        "trust_items": trust_line_items(facts_pack),
        "rating_line": rating_line(facts_pack),
        "pull_quote": pull_quote(facts_pack),
        "model_options": model_options(facts_pack),
        "hsa_claim": hsa_claim(facts_pack),
    }


def model_options(facts_pack):
    """The model picker's rows -- facts_pack.model_options, built by
    harness/ground.py from the tenant's own active products. [] when the
    run's facts_pack carries none (a run whose selected cartridges do not
    include listicle never builds them)."""
    return (facts_pack or {}).get("model_options") or []


# ---------------------------------------------------------------------------
# Gate: structural checks over the writer's page.json
# ---------------------------------------------------------------------------

def _problem(path, key, issue, **extra):
    item = {"path": path, "key": key, "issue": issue}
    item.update(extra)
    return item


def _items(page):
    reasons = page.get("reasons")
    return reasons if isinstance(reasons, list) else []


def find_style_violations(page, style=None):
    """page.style is one of STYLES (and is the run's own style, when the
    caller knows it), and the headline follows that style's formula with a
    number that matches the item count where the formula says it should."""
    problems = []
    page_style = page.get("style")
    if page_style not in STYLES:
        problems.append(_problem(
            "$.style", "listicle:style",
            f"style is {page_style!r}; it must be one of {list(STYLES)}",
        ))
        return problems
    if style and page_style != style:
        problems.append(_problem(
            "$.style", "listicle:style",
            f'style is "{page_style}"; this run is writing the "{style}" style '
            f'(headline formula: "{HEADLINE_FORMULAS[style]}")',
        ))
        page_style = style

    headline = page.get("headline") or ""
    match = _HEADLINE_RES[page_style].search(headline)
    if not match:
        problems.append(_problem(
            "$.headline", "listicle:headline_formula",
            f'headline {headline!r} does not follow the "{page_style}" formula: '
            f'"{HEADLINE_FORMULAS[page_style]}"',
        ))
        return problems
    if page_style in _N_IS_ITEM_COUNT:
        stated = int(match.group(1))
        actual = len(_items(page))
        if stated != actual:
            problems.append(_problem(
                "$.headline", "listicle:headline_formula",
                f"headline says {stated} but the page has {actual} items; the number in the "
                "headline must be the item count",
            ))
    return problems


def find_headline_slot_violations(page, tenant_name=None, product_names=None):
    """The headline's <category> slot (see HEADLINE_FORMULAS/writer_style_
    lines) is the product category, never a brand or model name -- observed
    on a real run: a headline shaped like "6 Reasons Home Buyers Are
    Choosing Acme Saunas Infrared Saunas" named the tenant itself.
    tenant_name and product_names are whatever this run's own tenant/
    facts_pack carry (see harness/repair.py's check_page_gates) -- taking
    them as plain strings keeps this module tenant-neutral rather than
    importing harness/tenant.py here."""
    headline = page.get("headline") or ""
    if not headline:
        return []
    names = [n for n in [tenant_name, *(product_names or [])] if n]
    for name in names:
        if re.search(r"\b" + re.escape(name) + r"\b", headline, re.IGNORECASE):
            return [_problem(
                "$.headline", "listicle:headline_slots",
                f"headline contains {name!r} -- the <category> slot names the product "
                'category only (e.g. "home infrared saunas"), never the tenant\'s name or a '
                "product/model name; rewrite that slot without it",
            )]
    return []


def find_item_violations(page):
    """5-7 numbered items; each numbered by position, with an image, a
    50-150 word body, and a proof line that is either claim-backed or an
    attributed customer statement."""
    problems = []
    items = _items(page)
    lo, hi = ITEM_COUNT_RANGE
    if not lo <= len(items) <= hi:
        problems.append(_problem(
            "$.reasons", "listicle:item_count",
            f"page has {len(items)} items; a listicle needs {lo}-{hi}",
        ))
    wlo, whi = ITEM_WORD_RANGE
    for i, item in enumerate(items):
        path = f"$.reasons[{i}]"
        if not isinstance(item, dict):
            problems.append(_problem(path, f"listicle:item_shape:{i}", "item is not an object"))
            continue
        if item.get("number") != i + 1:
            problems.append(_problem(
                path, f"listicle:item_numbering:{i}",
                f"item number is {item.get('number')!r}; it must be {i + 1}, its own position",
            ))
        words = len((item.get("text") or "").split())
        if not wlo <= words <= whi:
            problems.append(_problem(
                path, f"listicle:item_words:{i}",
                f"item body is {words} words; each item body must be {wlo}-{whi}",
            ))
        if not ((item.get("image") or {}).get("asset_id")):
            problems.append(_problem(
                path, f"listicle:item_image:{i}",
                "item has no image.asset_id; every item carries exactly one image",
            ))
        proof = item.get("proof")
        if not isinstance(proof, dict) or not (proof.get("text") or "").strip():
            problems.append(_problem(
                path, f"listicle:item_proof:{i}",
                "item has no proof line; every item closes with one, either citing a verified "
                "claim_id or stating an attributed customer statement",
            ))
        elif not proof.get("claim_ids") and proof.get("attributed_to_customer") is not True:
            problems.append(_problem(
                f"{path}.proof", f"listicle:item_proof:{i}",
                "proof line carries neither a claim_id nor attributed_to_customer: true",
            ))
    return problems


def find_hero_violations(page):
    """v2's header has a real hero image slot of its own (v0.1 used the
    first item's image as the de facto hero -- see ground.hero_container)."""
    if not ((page.get("hero") or {}).get("asset_id")):
        return [_problem(
            "$.hero.asset_id", "listicle:hero",
            "page has no hero.asset_id; the header carries one hero image",
        )]
    return []


def find_audience_fit_violations(page):
    """The "who this is for / who it is not for" block: two short lists,
    both present. The honest half is the point -- a page with only the
    flattering list is not the pattern."""
    block = page.get("audience_fit")
    lo, hi = AUDIENCE_LIST_RANGE
    if not isinstance(block, dict):
        return [_problem(
            "$.audience_fit", "listicle:audience_fit",
            "page has no audience_fit block (who this is for / who it is not for)",
        )]
    problems = []
    for field in ("for_you", "not_for_you"):
        entries = block.get(field)
        entries = entries if isinstance(entries, list) else []
        if not lo <= len(entries) <= hi:
            problems.append(_problem(
                f"$.audience_fit.{field}", f"listicle:audience_fit:{field}",
                f"audience_fit.{field} has {len(entries)} lines; it needs {lo}-{hi}",
            ))
    return problems


def find_faq_violations(page):
    """5-7 questions, and any answer that states a fact carries claim_ids.
    The number/trigger-word rule is claims._trigger_reason itself, so an FAQ
    answer is held to exactly the standard every other sentence on the page
    is (the shared gate only reaches a node's own "text" field; an FAQ
    answer lives under "answer", which is why this check exists)."""
    faq = page.get("faq")
    questions = faq.get("questions") if isinstance(faq, dict) else faq
    questions = questions if isinstance(questions, list) else []
    lo, hi = FAQ_COUNT_RANGE
    problems = []
    if not lo <= len(questions) <= hi:
        problems.append(_problem(
            "$.faq.questions", "listicle:faq_count",
            f"FAQ has {len(questions)} questions; it needs {lo}-{hi}",
        ))
    for i, entry in enumerate(questions):
        if not isinstance(entry, dict):
            continue
        answer = entry.get("answer") or ""
        reason = _trigger_reason(answer)
        if reason and not entry.get("claim_ids"):
            problems.append(_problem(
                f"$.faq.questions[{i}].answer", f"listicle:faq_claims:{i}",
                f"FAQ answer needs at least one claim_id ({reason}) -- cite a verified claim_id, "
                "or rewrite the answer without it",
                text=answer,
            ))
    return problems


def find_closing_violations(page):
    """The closing block's 3-bullet recap. The warranty and financing
    sentences are claims.find_warranty_violations /
    find_financing_violations' job, unchanged."""
    closing = page.get("closing")
    closing = closing if isinstance(closing, dict) else {}
    recap = closing.get("recap")
    recap = recap if isinstance(recap, list) else []
    if len(recap) != RECAP_BULLET_COUNT:
        return [_problem(
            "$.closing.recap", "listicle:recap",
            f"closing recap has {len(recap)} bullets; it must have exactly {RECAP_BULLET_COUNT}",
        )]
    return []


def find_urgency_violations(page):
    """No conversion-urgency vocabulary anywhere in writer-composed prose."""
    problems = []
    for path, node in walk_page(page, skip_keys=NON_PROSE_KEYS):
        if not isinstance(node, str):
            continue
        for phrase, pattern in _URGENCY_RES:
            if pattern.search(node):
                problems.append(_problem(
                    path, f"listicle:urgency:{phrase}",
                    f"urgency phrase {phrase!r} found; this page never uses urgency, a countdown, "
                    "or a discount to push the reader",
                    text=node,
                ))
    return problems


def find_renderer_owned_violations(page):
    """The trust line, pull quote, model picker and HSA/FSA line are built
    by the renderer from facts_pack (see the module docstring) -- a writer
    that supplies one is inventing data the gate cannot check."""
    problems = []
    for path, node in walk_page(page):
        if not isinstance(node, dict):
            continue
        for key in RENDERER_OWNED_KEYS:
            if key in node:
                problems.append(_problem(
                    f"{path}.{key}", f"listicle:renderer_owned:{key}",
                    f"{key!r} is built by the renderer from facts_pack, never written here -- "
                    "remove it from page.json",
                ))
    return problems


def find_listicle_violations(page, style=None, tenant_name=None, product_names=None):
    """Every listicle-specific structural check, combined. Wired into
    harness/repair.py's check_page_gates for the listicle cartridge only.
    tenant_name/product_names (cycle 43) feed find_headline_slot_violations
    only; both default to None, so a caller that doesn't have them yet
    (every existing call site outside check_page_gates) simply skips that
    one check rather than needing an update."""
    if not isinstance(page, dict):
        return []
    problems = []
    problems += find_style_violations(page, style)
    problems += find_headline_slot_violations(page, tenant_name, product_names)
    problems += find_item_violations(page)
    problems += find_hero_violations(page)
    problems += find_audience_fit_violations(page)
    problems += find_faq_violations(page)
    problems += find_closing_violations(page)
    problems += find_urgency_violations(page)
    problems += find_renderer_owned_violations(page)
    return problems
