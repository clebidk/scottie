"""Deterministic claims gate. No model calls.

(a) Every ad_brief.claims_made string must token-overlap >= 0.6 with some
    verified claim's normalized text, AND every numeric token in the ad claim
    must also appear in that verified claim's text, or the run STOPs. Fix
    cycle 10: a claim on a locked topic (warranty/reviews/financing/price --
    classify_locked_topic) skips word overlap entirely and is checked
    against its own locked fact instead; a failure is an AD OVERCLAIM, not a
    plain unmatched item, and claims/config.json's ad_overclaim_policy
    ("stop", default, or "warn") controls whether that alone stops the run.
(b) Every page.json node with a "claim_ids" list must reference existing ids;
    every node with a "text" field containing a digit, %, $, or one of the
    trigger words must carry a non-empty claim_ids list, or the run STOPs.
(c) No page.json string anywhere may contain one of the tenant's absolutely
    banned terms (vocab.yaml: banned topics, competitor names, discontinued
    model names, hype words) or -- when claims/config.json's financing_lender
    is null -- a known financing-lender name, or the run STOPs.
(d) Fix cycle 2 item 11: if ad_brief.speaker_pov is "first_person", no page.json
    prose string outside a quoted-testimonial container may put the ad
    speaker's story in the author's own first person ("I ran...", "I don't...").
(e) Fix cycle 2 item 9: after rendering, the page's visible text (tags,
    scripts, and styles stripped, entities unescaped) may not contain one of
    the tenant's visible_text_forbidden_terms -- href/src attribute values are
    exempt because tag-stripping removes them along with the tag.
(f) Fix cycle 3 item 3: each cartridge's persuasive section (proof_bullets /
    how_it_works / turn_section.criteria) must carry a minimum count of
    product-benefit claim_ids (spec/benefit/trust category, excluding price/
    shipping/warranty/returns by id) -- see find_benefit_claim_shortfall.
(g) Fix cycle 16 item 3: longform's optional hero.proof_stats row -- each
    stat must carry a claim_id -- see find_proof_stats_violations.
(h) Fix cycle 16 item 5: never more than one CTA/offer card per page -- a
    "cta_url" key anywhere but the page root is a second offer card -- see
    find_second_cta_violation.
"""
import html
import re

from . import tenant as tenant_mod
from . import exits
from . import vocab
from .textutil import DOLLAR_AMOUNT_RE, NON_PROSE_KEYS

# Every word list and fixed sentence below comes from the active tenant's
# vocab.yaml, read through the `vocab` module on each access -- importing the
# names directly would bind one tenant's values at import time.

STOPWORDS = {
    "a", "an", "the", "is", "are", "was", "were", "be", "been", "being", "to", "of", "in",
    "on", "at", "for", "with", "and", "or", "but", "that", "this", "it", "its", "as", "by",
    "from", "has", "have", "had", "not", "no", "so", "than", "then", "too", "very", "can",
    "will", "would", "should", "could", "just", "about", "into", "over", "under", "up",
    "down", "out", "if", "we", "you", "your", "our", "their", "they", "he", "she", "i",
}

# A quoted testimonial "span" -- a customer's own attributed words inline in
# an ordinary paragraph (e.g. 'she said, "It's 2026..."'). A structural
# "quotes" list (cartridges/longform's social_proof.quotes) is a list of
# plain strings, which validate_page_claim_ids never descends into as prose
# on its own -- this extends the same "a verbatim customer quote isn't the
# author's own claim" treatment to a quote sitting inline inside a "text"
# field. Matches straight and curly double quotes.
_QUOTED_SPAN_RE = re.compile(r'"[^"]*"|“[^”]*”')


class ClaimsGateFailure(Exception):
    exit_code = exits.GATE_STOP

    def __init__(self, stage, items):
        super().__init__(f"claims gate STOP at stage {stage!r}: {len(items)} unmatched item(s)")
        self.stage = stage
        self.items = items


# ---------------------------------------------------------------------------
# (a) ad claims vs verified claims
# ---------------------------------------------------------------------------

# Fix cycle 9 item 4: a small, hand-picked set of synonym pairs so
# semantically equivalent phrasing overlaps cleanly instead of STOPping on a
# wording mismatch -- e.g. the ad's "crate-protected delivery" against
# a shipping claim's own text (e.g. "... a branded custom
# protective wooden crate ... Free shipping ..."): "protected"/"protective"
# and "delivery"/"shipping" mean the same thing here but share no token.
# Applied inside normalize() so both sides of a comparison see the same
# canonical token -- not a general thesaurus, just the one pair this gate
# has actually needed.
_TOKEN_SYNONYMS = {"protected": "protective", "delivery": "shipping"}


def normalize(text):
    """lowercase, collapse number formatting so "$8,250" and "$8250.00" become
    the same token, strip remaining punctuation (keep $ and % since they carry
    meaning), drop stopwords, fold known synonyms to a canonical token ->
    list of tokens."""
    text = text.lower()
    text = re.sub(r"(?<=\d),(?=\d)", "", text)  # thousands separator: 8,250 -> 8250
    text = re.sub(r"\.00\b", "", text)  # cents suffix: 8250.00 -> 8250
    text = re.sub(r"[^a-z0-9%$\s]", " ", text)
    tokens = [t for t in text.split() if t and t not in STOPWORDS]
    return [_TOKEN_SYNONYMS.get(t, t) for t in tokens]


def overlap_ratio(ad_tokens, verified_tokens):
    if not ad_tokens:
        return 0.0
    return len(set(ad_tokens) & set(verified_tokens)) / len(set(ad_tokens))


# Fix cycle 12 item 4: a marketing combination-count idiom ("4-in-1: near,
# mid, far infrared + red light") isn't a single checkable fact the way a
# price or a review count is -- it's shorthand for "N things combined",
# verifiable (if at all) only by combining several verified claims (here:
# the full-spectrum claim covers 3 of the 4, the red-light claim covers the
# 4th), never by one claim's own text containing the literal digit "4". The
# numeric-token guard would otherwise reject a true, semantically-correct
# mapping to either claim just because neither one's text happens to
# contain "4" or "1" -- same rationale as claims.py's own pre-existing
# capacity-token exemption (a product's own "2-Person" doesn't count as an
# asserted number either, see _CAPACITY_TOKEN_RE elsewhere in this file).
_COMBO_COUNT_IDIOM_RE = re.compile(r"\b(\d+)-in-1\b", re.IGNORECASE)


def _combo_idiom_numbers(text):
    """Numbers (as numeric_tokens-shaped strings) that appear only as part
    of an "N-in-1" combination-count idiom in the raw (pre-normalize) claim
    text -- both the leading count and the idiom's own trailing "1"."""
    numbers = set()
    for m in _COMBO_COUNT_IDIOM_RE.finditer(text):
        numbers.add(m.group(1))
        numbers.add("1")
    return numbers


def numeric_tokens(tokens):
    """From an already-normalize()d token list, the subset of tokens that
    carry a digit, with any $/% stripped (commas and the .00 cents suffix are
    already gone -- normalize() strips those). Used so "$8,250" (ad phrasing)
    and "$8250.00" (verified.json phrasing) compare equal as the number 8250."""
    return {re.sub(r"[\$%]", "", t) for t in tokens if any(ch.isdigit() for ch in t)}


# Fix cycle 9 item 4: the general threshold (0.6) stays put, but a genuinely
# short ad claim (<=3 content tokens) is held to a lower bar (0.5) -- with so
# few tokens, a single word-choice mismatch (verified text says "speakers",
# the ad says "capabilities") is enough to sink an otherwise-real match, and
# there's no room left for the rest of the sentence to make up the
# difference the way a longer claim's surrounding words can. The numeric-
# token rule below is unaffected either way.
_SHORT_CLAIM_MAX_TOKENS = 3
_SHORT_CLAIM_THRESHOLD = 0.5
_DEFAULT_THRESHOLD = 0.6


def alias_match(ad_claim_text, verified_claims):
    """(matched_verified_claim_or_None, overlap_ratio). Fix cycle 16 item 11
    (Thursday queue item 1): a deterministic, no-model-call check of a
    verified claim's own `aliases` list (known equivalent ad phrasings, e.g.
    "4-in-1" for the full-spectrum claim) -- run BEFORE any semantic
    (model) call, so a known phrasing matches every time instead of only
    when that run's semantic_match call happens to agree. Same overlap
    threshold and numeric-token guard as ordinary word-overlap matching,
    just measured against each alias's own text instead of the claim's
    main text."""
    ad_tokens = normalize(ad_claim_text)
    ad_numbers = numeric_tokens(ad_tokens) - _combo_idiom_numbers(ad_claim_text)
    threshold = _SHORT_CLAIM_THRESHOLD if len(ad_tokens) <= _SHORT_CLAIM_MAX_TOKENS else _DEFAULT_THRESHOLD
    best, best_ratio = None, 0.0
    for vc in verified_claims:
        for alias in vc.get("aliases") or ():
            alias_tokens = normalize(alias)
            if not ad_numbers <= numeric_tokens(alias_tokens):
                continue
            ratio = overlap_ratio(ad_tokens, alias_tokens)
            if ratio > best_ratio:
                best, best_ratio = vc, ratio
    if best_ratio >= threshold:
        return best, best_ratio
    return None, best_ratio


def match_claim(ad_claim_text, verified_claims, semantic_mapping=None):
    """(matched_verified_claim_or_None, overlap_ratio). Fix cycle 12 item 4:
    if `semantic_mapping` (claims.semantic_match's {ad_claim_text:
    verified_id_or_None} return shape) proposes a verified id for this exact
    claim text, that mapping is used IF AND ONLY IF the same numeric-token
    guard below (every numeric token in the ad claim must also appear in the
    verified claim's own text) also passes -- the model's semantic judgment
    can recognize equivalent meaning ("4-in-1: near, mid, far infrared + red
    light" -> the full-spectrum/red-light allowlist claims), but it never
    gets to override the numeric guard on its own. A rejected or missing
    mapping falls through to ordinary word-overlap matching below, exactly
    as before this fix.

    Fix cycle 16 item 11: an alias match (alias_match, above) is tried
    FIRST, before semantic_mapping -- deterministic and free, so a known
    phrasing never depends on that run's semantic-match call agreeing."""
    alias_best, alias_ratio = alias_match(ad_claim_text, verified_claims)
    if alias_best:
        return alias_best, alias_ratio

    ad_tokens = normalize(ad_claim_text)
    ad_numbers = numeric_tokens(ad_tokens) - _combo_idiom_numbers(ad_claim_text)

    if semantic_mapping:
        proposed_id = semantic_mapping.get(ad_claim_text)
        if proposed_id:
            by_id = {vc["id"]: vc for vc in verified_claims}
            proposed = by_id.get(proposed_id)
            if proposed and ad_numbers <= numeric_tokens(normalize(proposed["text"])):
                return proposed, 1.0

    threshold = _SHORT_CLAIM_THRESHOLD if len(ad_tokens) <= _SHORT_CLAIM_MAX_TOKENS else _DEFAULT_THRESHOLD
    best, best_ratio = None, 0.0
    for vc in verified_claims:
        vc_tokens = normalize(vc["text"])
        # Every numeric token in the ad claim must also appear in the
        # verified claim's text -- word overlap alone can't tell "$8,250"
        # apart from "$9,750" if the surrounding words are similar.
        if not ad_numbers <= numeric_tokens(vc_tokens):
            continue
        ratio = overlap_ratio(ad_tokens, vc_tokens)
        if ratio > best_ratio:
            best, best_ratio = vc, ratio
    if best_ratio >= threshold:
        return best, best_ratio
    return None, best_ratio


def gate_ad_claims(claims_made, verified_claims):
    matched, unmatched = [], []
    for claim in claims_made:
        best, ratio = match_claim(claim, verified_claims)
        if best:
            matched.append({"claim": claim, "matched_claim_id": best["id"], "overlap": round(ratio, 3)})
        else:
            unmatched.append({"claim": claim, "best_overlap": round(ratio, 3)})
    return matched, unmatched


# ---------------------------------------------------------------------------
# Fix cycle 10 items 2-4: locked-topic ad claims. Fix cycle 9 left a real gap
# -- a false ad claim on one of these topics could clear the ordinary
# word-overlap bar against a superficially similar verified claim and get
# reported as MATCHED. Example that motivated this: "free lifetime warranty
# if it doesn't work" overlaps gbrain-allowlist-lifetime-warranty's "Limited
# Lifetime warranty." at 0.667 -- comfortably above the 0.6 bar -- even
# though the real warranty is per-component (3yr/1yr on most parts) and
# carries no "if it doesn't work" no-questions-asked guarantee anywhere.
# Warranty, review-stats, financing, and price claims never go through
# match_claim's word overlap at all now -- each is checked against its own
# locked fact instead, and a failure is reported as an AD OVERCLAIM (or, for
# price, the specific "matches no current product price" message) rather
# than silently folded into "matched".
# ---------------------------------------------------------------------------

_LOCKED_WARRANTY_RE = re.compile(r"\b(?:warrant\w*|guarantee\w*)\b", re.IGNORECASE)
_RATING_RE = re.compile(r"\b\d(?:\.\d+)?\s*(?:/|out of)\s*5\b|\b\d(?:\.\d+)?\s*stars?\b", re.IGNORECASE)
_REVIEW_COUNT_RE = re.compile(r"\b[\d,]+\+?\s*reviews?\b", re.IGNORECASE)
_MONTHLY_FIGURE_RE = re.compile(r"\$\s?[\d,]+(?:\.\d+)?\s*(?:/|a\s+|per\s+)\s*(?:mo\b|month\b)", re.IGNORECASE)


# Fix cycle 12 item 3 (second half): the red-X column of a comparative
# still/ad ("the other option leaves you drained", "the competing model
# takes 3 hours") is never a claim about the tenant's own product -- it's never
# checkable against claims/verified.json (there's nothing in this codebase
# that could verify or refute a statement about a competitor), and the
# writer's own guardrails already forbid stating a competitor "fact" that
# isn't the ad speaker's own experience (write.GLOBAL_VOICE_BLOCK: "Competitor
# statements are only ever the speaker's own experience, never a sourced
# fact about a competitor"). Classified by the claim's own grammatical
# subject -- one of "comparison"/"competing"/"competitor('s)" paired with one
# of "option"/"product"/"model"/"sauna", covering every real phrasing seen
# so far: "Comparison option ..." and "Competing product(s)/sauna(s) ..." and
# "Competitor(') product(s)/sauna(s)/model(s) ..." (docs/SWEEP-2026-09-10.md
# fixtures 5-7), plus two live phrasings that fell through this classifier's
# earlier, narrower version and word-overlap false-matched the tenant's own
# warranty-terms claim -- exactly the false-MATCHED-overclaim risk fix cycle
# 10 problem B was about, just for a competitor claim instead of a warranty
# one (both caught during Cycle 12's own Sweep 3, see docs/FIXLOG.md Cycle
# 12): "Competing saunas lack red light therapy" (0.667 overlap -- "sauna"
# wasn't in the noun list yet) and "Comparison product has no red light
# therapy" (0.6 overlap -- "comparison" was only paired with "option", never
# "product"). Deliberately still narrower than "any sentence mentioning a
# competitor": a specific factual assertion about a named rival with no red-X
# "the other option" framing at all (e.g. "a review site rated this brand below
# every major competitor" -- no claim here even uses "competitor"/"competing"/
# "comparison" as its own grammatical subject) is left to the ordinary
# unmatched-claim path -- it still never matches, and still never stops the
# run under "warn", just reported as a not-repeated claim instead of an
# "about: alternative" one.
_ALTERNATIVE_SUBJECT_RE = re.compile(
    r"\b(?:comparison|competing|competitor(?:'s)?) (?:option|product|model|sauna)s?\b"
    r"|\bthe other (?:option|brand)\b|\bother brands\b",
    re.IGNORECASE,
)


def classify_ad_claim_about(ad_claim_text):
    """None, or "alternative" -- a claim whose grammatical subject is the
    competing/comparison option, not the tenant's own product. An "alternative"
    claim is never run through any matching check (locked-topic or word
    overlap), never required to match a verified claim, and never stops the
    run under either ad_overclaim_policy -- see gate_ad_brief_claims."""
    if _ALTERNATIVE_SUBJECT_RE.search(ad_claim_text):
        return "alternative"
    return None


def classify_locked_topic(ad_claim_text, financing_lender=None):
    """None, or one of "warranty"/"reviews"/"financing"/"price" -- checked in
    that order (a claim naming a lender and a dollar figure is financing,
    not price; a claim with a number and "5" after "out of" is reviews, not
    a bare digit). None means the ordinary word-overlap gate (match_claim)
    still applies -- this only locks the four topics that have their own
    single source of truth to check against.

    Fix cycle 21: `financing_lender`, when given, also routes a claim to
    "financing" if it names that exact (configured) lender, even with no
    dollar/monthly figure -- e.g. "Financing available through Bread Pay".
    Before this, a claim naming the configured lender alone (no figure)
    fell through to the ordinary word-overlap path, since
    vocab.LENDER_NAME_RE only matches a *forbidden* lender name and the
    configured lender is deliberately not on that list (fix cycle 18)."""
    if _LOCKED_WARRANTY_RE.search(ad_claim_text):
        return "warranty"
    if _RATING_RE.search(ad_claim_text) or _REVIEW_COUNT_RE.search(ad_claim_text):
        return "reviews"
    if (
        _MONTHLY_FIGURE_RE.search(ad_claim_text)
        or vocab.LENDER_NAME_RE.search(ad_claim_text)
        or (financing_lender and financing_lender.lower() in ad_claim_text.lower())
    ):
        return "financing"
    if DOLLAR_AMOUNT_RE.search(ad_claim_text):
        return "price"
    return None


def _allowed_warranty_forms():
    return (
        vocab.ALLOWED_WARRANTY_SENTENCE.lower(),
        vocab.ALLOWED_WARRANTY_SPEC_VALUE.lower(),
        vocab.ALLOWED_WARRANTY_SPEC_LABEL.lower(),
    )


def warranty_claim_id():
    """The tenant's own warranty claim id (tenant.yaml's warranty_claim_id)."""
    return tenant_mod.active().get("warranty_claim_id") or "warranty-terms"


def _warranty_fact(verified_claims):
    by_id = {c["id"]: c for c in (verified_claims or [])}
    wid = warranty_claim_id()
    if wid in by_id:
        return by_id[wid]["text"]
    for c in verified_claims or ():
        if "warrant" in c.get("id", "").lower():
            return c["text"]
    return None


def evaluate_warranty_claim(ad_claim_text, verified_claims):
    """(ok, verified_fact_text). ok only if the ad claim IS one of the fixed
    allowed forms, or verbatim contains a verified warranty claim's own text
    -- the same rule find_warranty_violations already enforces on page copy
    (fix cycle 7 item 1), applied here to the ad's own spoken claim instead
    of the writer's prose."""
    stripped = ad_claim_text.strip().lower()
    fact = _warranty_fact(verified_claims)
    if stripped in _allowed_warranty_forms():
        return True, fact
    for c in verified_claims or ():
        if "warrant" in c.get("id", "").lower() and c.get("text") and c["text"].lower() in ad_claim_text.lower():
            return True, c["text"]
    return False, fact


_REVIEWS_LIVE_RE = re.compile(r"Rated\s+([\d.]+)\s+out of 5 across\s+([\d,]+)\s+reviews", re.IGNORECASE)


def _ad_rating(text):
    m = re.search(r"(\d(?:\.\d+)?)\s*(?:/|out of)\s*5\b", text, re.IGNORECASE)
    if m:
        return float(m.group(1))
    m = re.search(r"(\d(?:\.\d+)?)\s*stars?\b", text, re.IGNORECASE)
    return float(m.group(1)) if m else None


def _ad_review_count(text):
    m = _REVIEW_COUNT_RE.search(text)
    if not m:
        return None
    digits = re.sub(r"[^\d]", "", m.group(0))
    return int(digits) if digits else None


def evaluate_reviews_claim(ad_claim_text, reviews_claim):
    """(ok, verified_fact_text). Needs a real live reviews_claim (this run's
    adv.reviews.fetch_reviews_claim result) to compare against -- there is
    no static fallback, same as the rest of the reviews feature. ok only if
    every number the ad claim states (rating and/or review count) equals
    the live figure within rounding: rating equal at one decimal place;
    review count within 1% of the live count (so "about 9,000" still
    matches a live count of 8,978)."""
    if reviews_claim is None:
        return False, None
    m = _REVIEWS_LIVE_RE.search(reviews_claim.get("text", ""))
    if not m:
        return False, reviews_claim.get("text")
    live_rating, live_count = float(m.group(1)), int(m.group(2).replace(",", ""))
    ad_rating, ad_count = _ad_rating(ad_claim_text), _ad_review_count(ad_claim_text)
    if ad_rating is None and ad_count is None:
        return False, reviews_claim["text"]
    if ad_rating is not None and round(ad_rating, 1) != round(live_rating, 1):
        return False, reviews_claim["text"]
    if ad_count is not None and abs(ad_count - live_count) > max(1, round(live_count * 0.01)):
        return False, reviews_claim["text"]
    return True, reviews_claim["text"]


# Fix cycle 21: any figure a lender-quote check has no source for -- a dollar
# amount, a "/mo" or "per month" monthly payment (with or without a leading
# $, unlike _MONTHLY_FIGURE_RE which requires one), or an APR. No real lender
# quote exists anywhere in this codebase to verify a specific number against
# (same gap cycle 10 documented), so any of these keeps a financing ad claim
# an overclaim even once a lender is configured.
_FINANCING_FIGURE_RE = re.compile(r"\$\s?[\d,]+(?:\.\d+)?|/\s?mo\b|\bper\s+month\b|\bapr\b", re.IGNORECASE)


def evaluate_financing_claim(ad_claim_text, financing_lender):
    """(ok, verified_fact_text). Fix cycle 21: once claims/config.json's
    financing_lender is configured, the one true financing fact is the
    formatted with-lender sentence (vocab.allowed_financing_sentence(lender))
    instead of the no-lender one -- an ad claim that names the configured
    lender and states no dollar amount/monthly figure/APR (e.g. "Financing
    available through Bread Pay") asserts nothing beyond what that sentence
    already says, so it matches. Any claim carrying a $ amount, "/mo", "per
    month", or an APR is still an overclaim -- no real lender quote exists
    anywhere in this codebase to check a specific figure against (same gap
    fix cycle 10 documented) -- reported against the with-lender sentence.
    With no lender configured, behavior is unchanged from fix cycle 10:
    always an overclaim against the no-lender sentence (see
    docs/FIXLOG.md Cycle 10)."""
    fact = vocab.allowed_financing_sentence(financing_lender)
    if not financing_lender:
        return False, fact
    if _FINANCING_FIGURE_RE.search(ad_claim_text):
        return False, fact
    if financing_lender.lower() in ad_claim_text.lower():
        return True, fact
    return False, fact


def evaluate_price_claim(ad_claim_text, product_price):
    """(ok, message_or_None). ok if any dollar amount in the ad claim is
    within $1 of product_price -- fix cycle 10 item 2's numeric anchor,
    replacing the word-overlap match a price claim used to go through, so
    "on sale right now for $5,450" matches purely on the number instead of
    STOPping because "sale" appears nowhere in the verified price claim's
    own wording. product_price is this run's already-picked product's
    current price (fix cycle 10 item 1 -- product-picking now runs before
    this gate)."""
    amounts = [float(m.group(1).replace(",", "")) for m in DOLLAR_AMOUNT_RE.finditer(ad_claim_text)]
    if product_price is not None:
        for amt in amounts:
            if abs(amt - float(product_price)) <= 1.0:
                return True, None
    from .prices import format_price

    shown = format_price(amounts[0]) if amounts else "amount"
    return False, f"quoted price {shown} matches no current product price"


def _overclaim_item(claim, topic, verified_fact):
    if verified_fact:
        message = f'AD OVERCLAIM: "{claim}" — verified fact: "{verified_fact}"'
    else:
        message = f'AD OVERCLAIM: "{claim}" — no verified {topic} fact available'
    return {"claim": claim, "topic": topic, "verified_fact": verified_fact, "message": message}


def _not_repeated_item(item):
    """Normalizes a plain-unmatched item (gate_ad_claims/match_claim shape:
    claim + best_overlap) or a locked-topic overclaim item (_overclaim_item
    shape: claim + topic + verified_fact + message, already normalized) into
    the common shape used for REVIEW.md's "AD CLAIMS NOT REPEATED ON PAGE"
    section and the writer's DO NOT REPEAT block under policy "warn"
    (fix cycle 12 item 3). An overclaim item already has "message"; a plain-
    unmatched item doesn't, so build one from its best_overlap score."""
    if "message" in item:
        return item
    reason = f"no verified claim matched (best word overlap {item['best_overlap']})"
    return {
        "claim": item["claim"],
        "verified_fact": None,
        "message": f'AD CLAIM NOT REPEATED: "{item["claim"]}" — {reason}',
    }


def gate_ad_brief_claims(ad_brief, verified_claims, *, product=None, reviews_claim=None,
                          financing_lender=None, policy="stop", log=None, semantic_mapping=None):
    """Runs the ad_brief.claims_made list through the gate against the FULL
    claims/verified.json universe (not the per-product facts_pack subset --
    an ad claim can reference anything approved in verified.json, regardless
    of which product ends up being written about). speaker_experience is
    never passed through this gate.

    Fix cycle 12 item 3 (first half): a claim about the alternative/
    comparison option (classify_ad_claim_about) is never matched against
    anything and never stops the run under either policy -- see
    `alternative_claims` below.

    Fix cycle 10: a claim on a locked topic (warranty/reviews/financing/
    price -- classify_locked_topic) is never matched by word overlap; each
    is checked against its own locked fact (product's current price for
    price, reviews_claim for reviews, financing_lender for financing, the
    fixed warranty forms for warranty). `product` is this run's already-
    picked product (fix cycle 10 item 1 -- product-picking now runs before
    this gate), used for the price check's "current price".

    Fix cycle 12 item 4: `semantic_mapping` (claims.semantic_match's return
    shape: {ad_claim_text: verified_id_or_None}), if given, is consulted by
    match_claim BEFORE word-overlap for a plain (non-locked, non-alternative)
    claim -- never for a locked-topic claim, which always stays with its own
    evaluator above.

    Fix cycle 12 item 3 (second half): policy == "warn" no longer only
    exempts a locked-topic overclaim -- EVERY unmatched or overclaimed claim
    (plain or locked-topic) is dropped from what the writer may use instead
    of stopping the run: returned in `not_repeated`, never raised. Under
    policy == "stop" (default), behavior is unchanged from fix cycle 10: any
    unmatched or overclaimed claim (plain or locked-topic) stops the run.

    Returns (matched, not_repeated, alternative_claims). `not_repeated` is
    every claim that failed its check (plain-unmatched or locked-topic
    overclaim), normalized via `_not_repeated_item` -- non-empty only under
    policy == "warn" (under "stop" these are folded into the raised failure
    instead, same as before). `alternative_claims` is every claim classified
    "about: alternative" -- always returned, regardless of policy, since
    those never stop the run either way. Raises ClaimsGateFailure under
    policy == "stop" if any plain or locked-topic claim failed its check."""
    matched, plain_unmatched, overclaims, alternative_claims = [], [], [], []
    product_price = (product or {}).get("price")

    for claim in ad_brief.get("claims_made", []):
        about = classify_ad_claim_about(claim)
        if about == "alternative":
            alternative_claims.append({"claim": claim, "about": about})
            continue

        topic = classify_locked_topic(claim, financing_lender)

        if topic == "warranty":
            ok, fact = evaluate_warranty_claim(claim, verified_claims)
            if ok:
                matched.append({"claim": claim, "matched_claim_id": warranty_claim_id(), "overlap": 1.0})
            else:
                overclaims.append(_overclaim_item(claim, topic, fact))
            continue

        if topic == "reviews":
            ok, fact = evaluate_reviews_claim(claim, reviews_claim)
            if ok:
                matched.append({"claim": claim, "matched_claim_id": reviews_claim["id"], "overlap": 1.0})
            else:
                overclaims.append(_overclaim_item(claim, topic, fact))
            continue

        if topic == "financing":
            # Fix cycle 21: with no lender configured, evaluate_financing_claim
            # never returns ok=True (unchanged fix cycle 10 behavior) -- this
            # branch only started needing the ok check once a configured
            # lender made a real match possible.
            ok, fact = evaluate_financing_claim(claim, financing_lender)
            if ok:
                # Synthetic id, same shape as the price branch's "price-<slug>"
                # below -- there is no claims/verified.json entry for the
                # fixed financing sentence itself (claims/verified.json's own
                # "gbrain-financing-terms" is unrelated, stale g Brain figures
                # naming a different lender -- see docs/FIXLOG.md Cycle 18's
                # policy-financing-doc-stale note -- so it must not be reused
                # here).
                lender_id = re.sub(r"[^a-z0-9]+", "-", financing_lender.lower()).strip("-")
                matched.append({"claim": claim, "matched_claim_id": f"financing-{lender_id}", "overlap": 1.0})
            else:
                overclaims.append(_overclaim_item(claim, topic, fact))
            continue

        if topic == "price":
            ok, message = evaluate_price_claim(claim, product_price)
            if ok:
                price_id = f"price-{product['slug']}" if product else "price"
                matched.append({"claim": claim, "matched_claim_id": price_id, "overlap": 1.0})
            else:
                overclaims.append({"claim": claim, "topic": topic, "verified_fact": None, "message": message})
            continue

        best, ratio = match_claim(claim, verified_claims, semantic_mapping=semantic_mapping)
        if best:
            matched.append({"claim": claim, "matched_claim_id": best["id"], "overlap": round(ratio, 3)})
        else:
            plain_unmatched.append({"claim": claim, "best_overlap": round(ratio, 3)})

    if log:
        for item in overclaims:
            log.event("ad_claims", item["message"])
        for item in alternative_claims:
            log.event("ad_claims", f'ad statement about alternative (not repeated): "{item["claim"]}"')

    if policy == "warn":
        not_repeated = [_not_repeated_item(i) for i in plain_unmatched] + [_not_repeated_item(i) for i in overclaims]
        if log:
            for item in plain_unmatched:
                log.event("ad_claims", _not_repeated_item(item)["message"])
        return matched, not_repeated, alternative_claims

    stop_items = list(plain_unmatched) + list(overclaims)
    if stop_items:
        raise ClaimsGateFailure("ad_claims", stop_items)

    return matched, [], alternative_claims


# ---------------------------------------------------------------------------
# (b) page.json claim_ids validation
# ---------------------------------------------------------------------------

# Word-boundary, not substring: a plain substring check flags ordinary
# words that happen to contain a trigger word ("frustrated" contains
# "rated", "previews"/"interviews" contain "reviews") -- caught live on the
# hidden-costs-v2 fixture during fix-cycle-2 verification.


# Fix cycle 6 item 2: a digit inside the product's own short_name/title/model
# name (e.g. the "2" in a short_name like "Acme 2-Person Cabin") or a generic
# capacity token ("2-Person") is not a number the writer is asserting --
# stripping these before the digit check means writing the product's own
# name no longer forces a claim_id onto a sentence that has nothing else to
# cite (observed cycling with the leaked-claim-id fix in the hidden-costs-v2
# verification run: attempt 2's fix for "unlock" re-triggered this instead).
_CAPACITY_TOKEN_RE = re.compile(r"\b[1-6]-Person\b", re.IGNORECASE)


def _strip_digit_exempt_tokens(text, digit_exempt_terms):
    for term in digit_exempt_terms or ():
        if term:
            text = re.sub(re.escape(term), " ", text, flags=re.IGNORECASE)
    return _CAPACITY_TOKEN_RE.sub(" ", text)


# Fix cycle 11 problem A: an item the writer marks attributed_to_customer is
# exempt from the ordinary digit/$/% claim_id rule when -- and only when --
# every number in its own text also appears in the ad speaker's own words
# (ad_brief.speaker_experience, or the raw transcript). Numbers are compared
# as normalized tokens (comma grouping stripped, $ and % kept) so "$2,400"
# in the writer's sentence matches "$2400" or "2,400" in the transcript.
_NUMBER_TOKEN_RE = re.compile(r"\$?\d[\d,]*(?:\.\d+)?%?")


def _extract_numbers(text):
    return {m.group(0).replace(",", "") for m in _NUMBER_TOKEN_RE.finditer(text)}


def speaker_numbers(ad_brief):
    """Every number the ad speaker herself said, from ad_brief.speaker_experience
    (her own hedged estimates, fix cycle 9 item 3) plus the raw transcript --
    the set an attributed_to_customer sentence's own numbers must be a
    subset of to earn the digit exemption below."""
    if not ad_brief:
        return set()
    parts = [p for p in (ad_brief.get("speaker_experience") or []) if isinstance(p, str)]
    transcript = ad_brief.get("transcript_or_text")
    if isinstance(transcript, str):
        parts.append(transcript)
    return _extract_numbers(" ".join(parts))


# Fix cycle 11 problem A item 2 (last sentence): attributed_to_customer is
# only ever honored on a plain narrative paragraph -- the ad speaker's own
# story -- never a heading, a proof/benefit bullet, a spec-table row, or an
# FAQ item. Enumerated by exact page.json path shape, one entry per
# cartridge schema, so a flag anywhere else (proof_bullets, specs_table,
# faq, trust_strip, hero price/financing lines, turn_section.criteria,
# how_it_works.steps -- all proof- or locked-topic shaped) is rejected by
# validate_page_claim_ids below regardless of how the sentence is phrased.
_ATTRIBUTABLE_PATH_RE = re.compile(
    r"^\$\.(?:"
    r"angle_section\.paragraphs\[\d+\]"              # product-page
    r"|open\[\d+\]"                                  # article
    r"|body_sections\[\d+\]\.paragraphs\[\d+\]"      # article
    r"|alternatives_section\.paragraphs\[\d+\]"      # article, fix cycle 16 item 8
    r"|how_it_works_section\.paragraphs\[\d+\]"      # article, fix cycle 16 item 8
    r"|close\.paragraphs\[\d+\]"                     # article
    r"|problem\.paragraphs\[\d+\]"                   # longform
    r"|reasons\[\d+\]"                               # listicle, fix cycle 16 item 6
    r")$"
)


def _trigger_reason(text, digit_exempt_terms=None, attributed_to_customer=False, speaker_number_set=None):
    """None if `text` carries nothing that requires a claim_id; otherwise a
    short human-readable reason (fix cycle 4: named in the gate failure so a
    repair attempt knows exactly what to remove or cite, instead of
    re-reading the whole paragraph to guess). A verbatim customer quote
    (e.g. "It's 2026," she said) isn't the author's own factual assertion --
    don't require a claim_id just because the ad speaker's own words
    happened to include a number. digit_exempt_terms (fix cycle 6 item 2) are
    stripped before the digit check only -- a product name/title/capacity
    token doesn't count as an asserted number, but a real dollar amount or
    percentage still needs a claim_id even inside the product name's
    sentence.

    Fix cycle 11 problem A item 2: when `attributed_to_customer` is true and
    `speaker_number_set` is given, the digit/$/% checks are replaced by a
    number-by-number comparison against the ad speaker's own words -- a
    number she never said still needs a claim_id like anywhere else, but one
    she did say (her own estimate, e.g. "around $200 a month") no longer
    does. The trigger-word check (medical/clinical/proven/rated/reviews/
    study/emf) is unaffected either way -- attribution never excuses those."""
    unquoted = _QUOTED_SPAN_RE.sub(" ", text)

    if attributed_to_customer and speaker_number_set is not None:
        stripped = _strip_digit_exempt_tokens(unquoted, digit_exempt_terms)
        unsupported = _extract_numbers(stripped) - speaker_number_set
        if unsupported:
            return f"contains a number not in the ad speaker's own words: {', '.join(sorted(unsupported))}"
    else:
        if "$" in unquoted:
            return "contains a dollar amount"
        if "%" in unquoted:
            return "contains a percentage"
        digit_check_text = _strip_digit_exempt_tokens(unquoted, digit_exempt_terms)
        if re.search(r"\d", digit_check_text):
            return "contains a number"

    m = vocab.TRIGGER_WORD_RE.search(unquoted.lower())
    if m:
        return f'uses the word "{m.group(0)}"'
    return None


def _contains_trigger(text, digit_exempt_terms=None):
    return _trigger_reason(text, digit_exempt_terms) is not None


def collect_claim_ids(node):
    """Recursively collect every claim id referenced anywhere in page.json
    (both the plural "claim_ids" list field and the singular "claim_id"
    string field used on facts_pack.specs-derived rows)."""
    ids = set()

    def walk(n):
        if isinstance(n, dict):
            if isinstance(n.get("claim_ids"), list):
                ids.update(n["claim_ids"])
            if n.get("claim_id"):  # non-empty string only
                ids.add(n["claim_id"])
            for v in n.values():
                walk(v)
        elif isinstance(n, list):
            for v in n:
                walk(v)

    walk(node)
    return ids


def validate_page_claim_ids(page_json, valid_claim_ids, digit_exempt_terms=None, speaker_number_set=None):
    problems = []

    def walk(node, path):
        if isinstance(node, dict):
            claim_ids = node.get("claim_ids")
            if isinstance(claim_ids, list):
                for cid in claim_ids:
                    if cid not in valid_claim_ids:
                        problems.append({"path": path, "issue": f"claim_id {cid!r} does not exist"})
            claim_id = node.get("claim_id") or None  # treat "" as not provided
            if claim_id is not None and claim_id not in valid_claim_ids:
                problems.append({"path": path, "issue": f"claim_id {claim_id!r} does not exist"})

            # Fix cycle 11 problem A item 2 (last sentence): the flag itself
            # is only ever valid on a plain narrative paragraph -- reject it
            # outright anywhere else (a heading, proof bullet, spec row, or
            # FAQ item), regardless of how the sentence is phrased, rather
            # than silently granting a digit exemption a locked/structured
            # field should never get.
            attributed = node.get("attributed_to_customer") is True
            if attributed and not _ATTRIBUTABLE_PATH_RE.match(path):
                problems.append(
                    {
                        "path": path,
                        "issue": "attributed_to_customer is only allowed on a narrative paragraph -- "
                                 "never a heading, proof bullet, spec-table row, or FAQ item",
                    }
                )
                attributed = False

            if isinstance(node.get("text"), str):
                text = node["text"]
                has_ref = bool(claim_ids) or bool(claim_id)
                reason = _trigger_reason(
                    text, digit_exempt_terms,
                    attributed_to_customer=attributed, speaker_number_set=speaker_number_set,
                )
                if reason and not has_ref:
                    problems.append(
                        {
                            "path": path,
                            "issue": f"text needs at least one claim_id ({reason}) -- cite a "
                                     "verified claim_id, or rewrite the sentence without it",
                            "text": text,
                        }
                    )
            for k, v in node.items():
                walk(v, f"{path}.{k}")
        elif isinstance(node, list):
            for i, v in enumerate(node):
                walk(v, f"{path}[{i}]")

    walk(page_json, "$")
    return problems


_ATTRIBUTION_CUSTOMER_RE = re.compile(r"\bcustomer\b", re.IGNORECASE)
_ATTRIBUTION_PRONOUN_RE = re.compile(r"\b(?:she|he|they)\b", re.IGNORECASE)
_ATTRIBUTION_VERB_RE = re.compile(r"\btold us\b|\bestimated\b|\bsaid\b", re.IGNORECASE)


def _has_customer_attribution(text):
    if _ATTRIBUTION_CUSTOMER_RE.search(text):
        return True
    return bool(_ATTRIBUTION_PRONOUN_RE.search(text) and _ATTRIBUTION_VERB_RE.search(text))


def find_missing_attribution(page_json):
    """Fix cycle 11 problem A item 3: an item marked attributed_to_customer
    must visibly read, to a reader, as the ad speaker's own words -- not
    just carry the flag internally. Requires "customer", or one of
    "she"/"he"/"they" together with "told us"/"estimated"/"said", in the
    sentence's own text. Called pre-render (gate_page_json, feeds the
    writer repair loop) and post-render (render.render_page, a backstop) --
    the same defense-in-depth pattern as the EMF/leaked-claim-id checks."""
    hits = []

    def walk(node, path):
        if isinstance(node, dict):
            if node.get("attributed_to_customer") is True and isinstance(node.get("text"), str):
                text = node["text"]
                if not _has_customer_attribution(text):
                    hits.append(
                        {
                            "path": path,
                            "issue": 'attributed_to_customer item has no visible attribution -- the '
                                     'sentence must contain "customer", or "she"/"he"/"they" plus '
                                     '"told us"/"estimated"/"said"',
                            "text": text,
                        }
                    )
            for k, v in node.items():
                walk(v, f"{path}.{k}")
        elif isinstance(node, list):
            for i, v in enumerate(node):
                walk(v, f"{path}[{i}]")

    walk(page_json, "$")
    return hits


# Structural/reference fields, not prose the writer composed -- a product
# URL or asset id can legitimately contain "emf" (the Shopify handle does)
# without it ever reaching rendered copy. Fix 4: "The product URL may still
# contain the word; that is fine."
_URL_RE = re.compile(r"https?://\S+")


def find_forbidden_terms(page_json, financing_lender=None, verified_claims=None):
    """Recursively scan every prose string in page_json for a forbidden term
    (case-insensitive substring), skipping structural/reference fields (url,
    asset_id, etc.) that aren't writer-composed copy. Lender names are only
    forbidden while no lender has been approved (claims/config.json
    financing_lender is null). Fix cycle 7 item 2: IMPLIED_CLAIM_FORBIDDEN_TERMS
    (a second, unverified fact inferred from a verified one, e.g. "US-owned"
    implying "domestic support") are forbidden unless a verified claim's own
    text actually carries the phrase -- checked against `verified_claims`
    (facts_pack["verified_claims"]) so a future genuinely-verified claim
    about support location isn't blocked forever."""
    forbidden = list(vocab.ALWAYS_FORBIDDEN_TERMS)
    if not financing_lender:
        forbidden += list(vocab.FORBIDDEN_LENDER_NAMES)
    verified_text_blob = " ".join(c.get("text", "").lower() for c in (verified_claims or []))
    forbidden += [t for t in vocab.IMPLIED_CLAIM_FORBIDDEN_TERMS if t not in verified_text_blob]
    hits = []

    def walk(node, path):
        if isinstance(node, str):
            # A citation URL inline in prose (e.g. "(the brand, 2026,
            # https://.../near-zero-emf-...)") may legitimately contain a
            # forbidden term as part of the product's own URL/handle -- fix 4:
            # "The product URL may still contain the word; that is fine."
            # Strip any URL substring before scanning the surrounding prose.
            without_urls = _URL_RE.sub(" ", node)
            lower = without_urls.lower()
            for term in forbidden:
                if term in lower:
                    hits.append({"path": path, "term": term, "issue": f"forbidden term {term!r} found", "text": node})
        elif isinstance(node, dict):
            for k, v in node.items():
                if k in NON_PROSE_KEYS:
                    continue
                walk(v, f"{path}.{k}")
        elif isinstance(node, list):
            for i, v in enumerate(node):
                walk(v, f"{path}[{i}]")

    walk(page_json, "$")
    return hits


# Fix cycle 2 item 2 / item 11: the page author must never speak in
# the ad speaker's first person. A simple heuristic: "I" directly followed by
# one of these common first-person-anecdote verbs. Kept deliberately simple
# per the fix note -- not a full grammar check.
_FIRST_PERSON_VERBS = (
    "ran", "was", "had", "went", "tried", "wanted", "hated", "found", "came",
    "didn't", "don't",
)
_FIRST_PERSON_RE = re.compile(
    r"\bI(?:'m|'ve|'d)?\s+(?:" + "|".join(_FIRST_PERSON_VERBS) + r")\b", re.IGNORECASE
)

# Containers that hold an actual, credited quote/testimonial (not the author
# speaking) -- e.g. cartridges/longform's social_proof.quotes. First-person
# text inside these is fine; it's someone else's words, not the author's.
_QUOTED_CONTAINER_KEYS = {"quotes", "quote", "testimonial", "testimonials", "blockquote"}

# _QUOTED_SPAN_RE (defined near TRIGGER_WORDS above) covers the other case: a
# quotation-mark span inline inside an ordinary paragraph -- e.g. open[1]'s
# 'She said, "I don\'t want to talk to anyone."' is a customer's attributed
# quote, not the author speaking, and must not be flagged here either.


def find_first_person_violations(page_json, speaker_pov):
    """Fix 11: when the ad speaker talks in first person, that story must be
    attributed to "a customer" (or facts_pack.speaker_name), never written as
    the page author's own first-person experience. Flags any prose string
    containing an "I <verb>" construction outside a quoted-testimonial
    container or an inline quotation-mark span. No-op when speaker_pov isn't
    "first_person"."""
    if speaker_pov != "first_person":
        return []
    hits = []

    def walk(node, path, in_quote):
        if isinstance(node, str):
            if not in_quote:
                unquoted = _QUOTED_SPAN_RE.sub(" ", node)
                if _FIRST_PERSON_RE.search(unquoted):
                    hits.append(
                        {
                            "path": path,
                            "issue": "first-person statement outside a quoted testimonial",
                            "text": node,
                        }
                    )
        elif isinstance(node, dict):
            for k, v in node.items():
                walk(v, f"{path}.{k}", in_quote or k in _QUOTED_CONTAINER_KEYS)
        elif isinstance(node, list):
            for i, v in enumerate(node):
                walk(v, f"{path}[{i}]", in_quote)

    walk(page_json, "$", False)
    return hits


# Fix cycle 6 item 4 (broadened fix cycle 21): the schema's dedicated
# financing field (hero.financing_line / final_cta.financing_line in
# product-page and longform -- the field GLOBAL_VOICE_BLOCK's financing
# paragraph and every cartridge.md's own financing-line rule are actually
# about) must state exactly the one allowed financing sentence for this run
# -- ALLOWED_FINANCING_SENTENCE_NO_LENDER while no lender is configured, or
# the with-lender sentence (vocab.allowed_financing_sentence(financing_lender),
# fix cycle 21) once one is -- verbatim, either way. Scoped to this one
# field, not scanned across every prose string, after repeated
# live-verification failures (docs/FIXLOG.md Cycle 6) where a
# text-content-based version of this check kept flagging ordinary
# buyer-education prose that merely discussed financing as a topic, or
# that happened to also state an unrelated price/product-name digit in the
# same sentence -- a case a proximity heuristic couldn't reliably tell
# apart from an actually-invented financing figure. A lender name (or a
# figure) invented anywhere else on the page is still caught: any other
# lender name by find_forbidden_terms, any other invented number by the
# digit/claim_id rule (item 2).
#
# Fix cycle 21 note: cycle 18's real-run verification found this check
# no-op'd entirely once a lender was configured (financing_lender truthy
# skipped it outright), so a page could -- and did -- keep rendering the
# no-lender sentence even with Bread Pay configured; nothing ever caught
# it. Deliberately still scoped to only this one field, not reopened to a
# broader "any text containing 'financ'" scan -- that shape was tried and
# reverted during Cycle 6 (see its run log above) after repeatedly
# false-positiving on ordinary buyer-education prose; nothing about the
# with-lender case changes that risk.
def find_financing_violations(page_json, financing_lender=None):
    allowed = vocab.allowed_financing_sentence(financing_lender)
    hits = []

    def walk(node, path):
        if isinstance(node, dict):
            financing_line = node.get("financing_line")
            if financing_line is not None:
                text = financing_line.get("text") if isinstance(financing_line, dict) else financing_line
                text_path = f"{path}.financing_line.text" if isinstance(financing_line, dict) else f"{path}.financing_line"
                if isinstance(text, str) and text.strip() != allowed:
                    configured = f"configured lender is {financing_lender!r}" if financing_lender else "no lender is configured"
                    hits.append(
                        {
                            "path": text_path,
                            "issue": f"financing_line must be exactly {allowed!r} ({configured})",
                            "text": text,
                        }
                    )
            for k, v in node.items():
                walk(v, f"{path}.{k}")
        elif isinstance(node, list):
            for i, v in enumerate(node):
                walk(v, f"{path}[{i}]")

    walk(page_json, "$")
    return hits


# Fix cycle 7 item 1: warranty wording, same rationale/shape as
# find_financing_violations above but scoped to any text field ("any text
# containing 'warrant'"), not one dedicated schema field -- warranty copy
# can legitimately appear in trust_strip.warranty.text, a proof bullet, a
# specs_table row, or longform's final_cta warranty line, and the false
# claim that triggered this fix ("Limited lifetime warranty on the cabin,
# heating elements, and electronics") was writer-composed prose, not a
# dedicated field. The only allowed forms: the fixed sentence
# (ALLOWED_WARRANTY_SENTENCE), the fixed spec-table value
# (ALLOWED_WARRANTY_SPEC_VALUE), the bare spec-table label
# (ALLOWED_WARRANTY_SPEC_LABEL, e.g. a {"label": "Warranty", "value": ...}
# row's label on its own), or a verbatim quote of one of claims/verified.json's
# own warranty-* / gbrain-warranty-* claim texts (substring match, so a
# longer sentence that quotes the claim with attribution still passes).
#
# Cycle 7 real-run verification (out/20260909-2233-hidden-costs-v2, article
# cartridge STOPped) surfaced the same two false-positive shapes the
# financing gate already hit in cycle 6: (1) the allowed sentence combined
# with unrelated surrounding content in the same field ("Clear policies on
# shipping, warranty, and returns ... Limited lifetime warranty; full terms
# by component are published on the warranty page.") -- same "combined
# field" fix as cycle 6 item 5, strip the allowed sentence out before
# judging what's left; (2) ordinary buyer-education prose that merely
# discusses warranty as a policy topic with no specific coverage claim
# ("the return or warranty terms", "what the warranty actually covers
# component by component") -- asserts nothing false and needs no fixed
# wording. The operator-reported bug's actual shape was always a blanket
# *lifetime* coverage claim ("Limited lifetime warranty on the cabin,
# heating elements, and electronics" -- false because electronics are
# 3yr/1yr, not lifetime), so after removing any allowed-sentence substring,
# a violation requires "lifetime" to still be present alongside "warrant" --
# a topical mention with no lifetime/blanket-coverage assertion passes.
# Two real-run STOPs on the same fixture (out/20260909-2241-hidden-costs-v2)
# surfaced a third false-positive shape: the writer reproduces the allowed
# sentence's exact MEANING but drifts on a connector word or the closing
# punctuation while weaving it into a bigger sentence -- "a limited lifetime
# warranty, with full terms by component published on the warranty page"
# (drops "are", "with" instead of ";"), "...are published on the warranty
# page, so you can check..." (comma instead of a period, continuing the
# sentence). A strict case-insensitive `str`/regex match of the sentence
# verbatim doesn't recognize either as "the allowed sentence" even though
# neither states anything false. _WARRANTY_SENTENCE_CORE_RE recognizes the
# semantic core (the specific phrase "full terms by component ... published
# on the warranty page" is what actually matters -- it's what makes the
# statement true regardless of per-component duration) while tolerating an
# optional "are" and the connector/punctuation before it. It still requires
# that exact phrase, so it does NOT match the original false claim ("Limited
# lifetime warranty on the cabin, heating elements, and electronics" has no
# "full terms by component ... published" at all) or a claim that states
# specific coverage without that disclaimer.
_WARRANTY_SENTENCE_CORE_RE = re.compile(
    r"limited\s+lifetime\s+warranty\W+(?:with\s+)?full\s+terms\s+by\s+component\s+"
    r"(?:are\s+)?published\s+on\s+the\s+warranty\s+page\.?",
    re.IGNORECASE,
)

# Fourth real-run STOP on the same fixture (out/20260909-2248-hidden-costs-v2):
# a bare "lifetime" check flagged "A warranty document you can actually read
# component by component, not just a one-line lifetime promise." -- that's
# buyer-education commentary CONTRASTING a vague "lifetime promise" against
# reading real per-component terms, not a claim that the tenant's own warranty is
# blanket lifetime coverage. The word "warranty" and the word "lifetime" both
# appear, but never adjacent -- the actual assertion this gate needs to catch
# is specifically the phrase "lifetime warranty" (the two words together,
# describing what IS covered), not either word alone anywhere in the
# sentence.
_LIFETIME_WARRANTY_BIGRAM_RE = re.compile(r"lifetime\s+warranty", re.IGNORECASE)

# Thursday queue item 2 / fix cycle 16 item 12: the warranty heuristic used to
# trigger on any text containing "warrant" at all, then judge whether the
# wording was one of the allowed forms -- which meant honest, descriptive
# prose that merely brings up warranty as a topic ("read the warranty terms
# before you buy", "ask what the warranty actually covers") had to pass
# is_allowed's checks too, even though it never asserts any specific coverage.
# Narrowed here: the check only ever fires on a sentence that actually
# ASSERTS coverage -- one of these words alongside "warrant" -- so descriptive/
# buyer-education mentions are left alone before is_allowed is even consulted.
_WARRANTY_COVERAGE_ASSERTION_RE = re.compile(
    r"\b(?:cover(?:s|ed|age)?|backed|guarantee[sd]?|years?|lifetime)\b", re.IGNORECASE
)


def find_warranty_violations(page_json, verified_claims):
    verified_warranty_texts = {
        c["text"].strip() for c in (verified_claims or []) if "warranty" in c.get("id", "").lower() and c.get("text")
    }
    allowed_label_lower = vocab.ALLOWED_WARRANTY_SPEC_LABEL.lower()

    def is_allowed(text):
        stripped = text.strip()
        if stripped.lower() in (vocab.ALLOWED_WARRANTY_SENTENCE.lower(), vocab.ALLOWED_WARRANTY_SPEC_VALUE.lower()):
            return True
        if stripped.lower() == allowed_label_lower:
            return True
        if any(vt in text for vt in verified_warranty_texts):
            return True
        remainder = _WARRANTY_SENTENCE_CORE_RE.sub("", text)
        return not _LIFETIME_WARRANTY_BIGRAM_RE.search(remainder)

    hits = []

    def walk(node, path):
        if isinstance(node, str):
            lowered = node.lower()
            if (
                "warrant" in lowered
                and _WARRANTY_COVERAGE_ASSERTION_RE.search(lowered)
                and not is_allowed(node)
            ):
                hits.append(
                    {
                        "path": path,
                        "issue": (
                            f"warranty wording must be exactly {vocab.ALLOWED_WARRANTY_SENTENCE!r}, "
                            f"the spec-table pair ({vocab.ALLOWED_WARRANTY_SPEC_LABEL!r}: "
                            f"{vocab.ALLOWED_WARRANTY_SPEC_VALUE!r}), or a verbatim quote of a verified "
                            "warranty claim's own text"
                        ),
                        "text": node,
                    }
                )
        elif isinstance(node, dict):
            for k, v in node.items():
                if k in NON_PROSE_KEYS:
                    continue
                walk(v, f"{path}.{k}")
        elif isinstance(node, list):
            for i, v in enumerate(node):
                walk(v, f"{path}[{i}]")

    walk(page_json, "$")
    return hits


# ---------------------------------------------------------------------------
# Fix cycle 16 item 3 (design note 7, "Proposed Changes"): longform's optional
# hero.proof_stats row -- 2-3 stats directly under the hero subhead -- is
# verified-claims-only. No-op when proof_stats is absent (it's optional; the
# cartridge should omit it rather than pad it out with nothing to cite).
# ---------------------------------------------------------------------------


def find_proof_stats_violations(page_json):
    stats = (page_json.get("hero") or {}).get("proof_stats") if isinstance(page_json, dict) else None
    if not stats:
        return []
    hits = []
    for i, stat in enumerate(stats):
        if not isinstance(stat, dict) or not stat.get("claim_ids"):
            hits.append(
                {
                    "path": f"$.hero.proof_stats[{i}]",
                    "issue": "proof_stats item must carry at least one claim_id -- "
                             "every stat comes from facts_pack.verified_claims only",
                }
            )
    return hits


# ---------------------------------------------------------------------------
# Fix cycle 16 item 5 (design note 9, "Roman hub" anti-pattern): never render
# more than one CTA/offer card on a page. The page's own top-level cta_url
# (longform/product-page/listicle) is the one allowed CTA destination; any
# OTHER node anywhere in page_json that carries its own "cta_url" key is a
# second offer card the writer added on its own. No-op for article, whose
# schema has no "cta_url" key at all (its single CTA lives at cta.text/url,
# under the non-prose-exempt "cta" object, which this check doesn't touch).
# ---------------------------------------------------------------------------


def find_second_cta_violation(page_json):
    hits = []

    def walk(node, path, is_root):
        if isinstance(node, dict):
            if not is_root and "cta_url" in node:
                hits.append(
                    {
                        "path": f"{path}.cta_url",
                        "issue": (
                            f"second CTA url found on the page ({node.get('cta_url')!r}) -- "
                            "only one CTA/offer card is allowed per page"
                        ),
                    }
                )
            for k, v in node.items():
                walk(v, f"{path}.{k}", False)
        elif isinstance(node, list):
            for i, v in enumerate(node):
                walk(v, f"{path}[{i}]", False)

    walk(page_json, "$", True)
    return hits


def gate_page_json(page_json, facts_pack, cartridge_name, financing_lender=None, speaker_pov=None, ad_brief=None):
    valid_ids = {c["id"] for c in facts_pack["verified_claims"]}
    digit_exempt_terms = facts_pack.get("digit_exempt_terms")
    # Fix cycle 11 problem A: only present when the caller has an ad_brief
    # to check attributed_to_customer numbers against (cli.write_and_gate_page
    # always does, in the real pipeline); None elsewhere, which
    # validate_page_claim_ids/_trigger_reason treat as "no exemption" --
    # same as before this fix for every existing caller/test.
    speaker_number_set = speaker_numbers(ad_brief) if ad_brief is not None else None
    problems = validate_page_claim_ids(page_json, valid_ids, digit_exempt_terms, speaker_number_set=speaker_number_set)
    problems += find_forbidden_terms(
        page_json, financing_lender=financing_lender, verified_claims=facts_pack.get("verified_claims")
    )
    problems += find_first_person_violations(page_json, speaker_pov)
    problems += find_benefit_claim_shortfall(page_json, facts_pack, cartridge_name)
    problems += find_leaked_claim_ids(page_json, valid_ids)
    problems += find_financing_violations(page_json, financing_lender=financing_lender)
    problems += find_warranty_violations(page_json, facts_pack.get("verified_claims"))
    problems += find_missing_attribution(page_json)
    problems += find_proof_stats_violations(page_json)
    problems += find_second_cta_violation(page_json)
    if problems:
        raise ClaimsGateFailure(f"page_json:{cartridge_name}", problems)


# ---------------------------------------------------------------------------
# Fix cycle 5: a claim id belongs only in a node's own "claim_ids"/"claim_id"
# field -- never inside prose the writer composed. Observed twice in run
# 20260909-2021-hidden-costs-v2: "the full product short_name
# (spec-<model>-capacity), which is priced at $8,250". A reader has no idea
# what "spec-<model>-capacity" means; it's an internal id, not a citation.
# ---------------------------------------------------------------------------

# An id-shaped token: lowercase letters, digits, and hyphens, at least one
# hyphen (a bare word like "sauna" never matches). Flags both a leaked known
# id and a hallucinated one in the same family (see known_claim_id_prefixes())
# that was never in claims/verified.json to begin with.
_ID_SHAPED_TOKEN_RE = re.compile(r"\b[a-z]+(?:-[a-z0-9]+){1,}\b")

# The id-family prefixes actually used in claims/verified.json. A token
# starting with one of these reads as an internal id even if it doesn't
# happen to be one of this run's own valid_claim_ids.
_FALLBACK_CLAIM_ID_PREFIXES = ("price-", "spec-", "reviews-", "benefit-", "trust-")


def known_claim_id_prefixes():
    """tenant.yaml's claim_id_prefixes -- the id families this tenant's own
    claims store actually uses."""
    prefixes = tenant_mod.active().get("claim_id_prefixes") or ()
    return tuple(prefixes) or _FALLBACK_CLAIM_ID_PREFIXES


def _looks_like_claim_id(token, valid_claim_ids):
    return token in valid_claim_ids or token.startswith(known_claim_id_prefixes())


def find_leaked_claim_ids(page_json, valid_claim_ids):
    """Scans every prose string in page_json (same structural-field exemptions
    as find_forbidden_terms -- a claim_ids list or an asset_id legitimately
    contains id-shaped tokens) for a token that is either an exact known id
    from this run's valid_claim_ids or starts with one of
    KNOWN_CLAIM_ID_PREFIXES. Returns one problem dict per hit, gate-shaped
    like find_forbidden_terms's, so write_and_gate_page's repair loop
    rewrites the offending sentence instead of citing the id inline."""
    hits = []

    def walk(node, path):
        if isinstance(node, str):
            for m in _ID_SHAPED_TOKEN_RE.finditer(node):
                token = m.group(0)
                if _looks_like_claim_id(token, valid_claim_ids):
                    hits.append(
                        {
                            "path": path,
                            "issue": f"claim id leaked into copy: {token} in {path}",
                            "text": node,
                        }
                    )
        elif isinstance(node, dict):
            for k, v in node.items():
                if k in NON_PROSE_KEYS:
                    continue
                walk(v, f"{path}.{k}")
        elif isinstance(node, list):
            for i, v in enumerate(node):
                walk(v, f"{path}[{i}]")

    walk(page_json, "$")
    return hits


def find_leaked_claim_ids_visible_text(rendered_html, valid_claim_ids):
    """Post-render backstop, same rationale as find_forbidden_visible_text:
    scans the page's visible text (tags/scripts/styles stripped) for a
    claim id that slipped past find_leaked_claim_ids above. Same detection
    rule -- exact known id, or an id-shaped token starting with a known id
    prefix."""
    text = strip_html_to_visible_text(rendered_html).lower()
    hits = []
    for m in _ID_SHAPED_TOKEN_RE.finditer(text):
        token = m.group(0)
        if _looks_like_claim_id(token, valid_claim_ids):
            hits.append({"term": token, "issue": f"claim id leaked into copy: {token} in visible text"})
    return hits


_LEAKED_CLAIM_ID_PAREN_RE = re.compile(r"\s?\(([a-z]+(?:-[a-z0-9]+){1,})\)")


def strip_leaked_claim_ids(html_text, valid_claim_ids):
    """Last line of defense: find_leaked_claim_ids (pre-render) and
    find_leaked_claim_ids_visible_text (post-render) above should already
    have caught this and sent it back for a rewrite -- this only fires if
    both missed it. Quietly removes a parenthesized known claim id from the
    rendered HTML (e.g. "(spec-<model>-capacity)" -> "") rather than failing an
    already-written run over it. Returns (cleaned_html, [removed_ids])."""
    removed = []

    def repl(m):
        token = m.group(1)
        if _looks_like_claim_id(token, valid_claim_ids):
            removed.append(token)
            return ""
        return m.group(0)

    cleaned = _LEAKED_CLAIM_ID_PAREN_RE.sub(repl, html_text)
    return cleaned, removed


# ---------------------------------------------------------------------------
# Fix cycle 3 item 3: product substance. Price/shipping/warranty/returns are
# operational facts, not a reason to buy -- a run can pass every other gate
# while saying almost nothing about what the sauna does. Each cartridge's
# persuasive section (proof_bullets / how_it_works / turn_section.criteria)
# must carry a minimum number of claim_ids whose facts_pack category is
# spec/benefit/trust, excluding the transactional ids by name.
# ---------------------------------------------------------------------------

MIN_BENEFIT_CLAIMS = {"product-page": 3, "longform": 3, "article": 1}

BENEFIT_CLAIM_CATEGORIES = {"spec", "benefit", "trust"}

# Excluded by name (fix cycle 3 item 3: "not price, shipping, warranty, or
# returns") -- these are trust/price category claims that don't say anything
# about what the product does, so they don't count toward the minimum even
# though their category would otherwise qualify.
_EXCLUDED_BENEFIT_ID_PREFIXES = ("price-",)


def _excluded_benefit_ids():
    return set(tenant_mod.active().get("excluded_benefit_ids") or ())

_BENEFIT_SECTION_GETTERS = {
    "product-page": lambda page: page.get("proof_bullets", []),
    "longform": lambda page: (page.get("how_it_works") or {}).get("steps", []),
    "article": lambda page: (page.get("turn_section") or {}).get("criteria", []),
}


def _is_benefit_claim_id(claim_id, verified_by_id):
    if claim_id in _excluded_benefit_ids() or claim_id.startswith(_EXCLUDED_BENEFIT_ID_PREFIXES):
        return False
    claim = verified_by_id.get(claim_id)
    return bool(claim) and claim.get("category") in BENEFIT_CLAIM_CATEGORIES


def find_benefit_claim_shortfall(page_json, facts_pack, cartridge_name):
    """[] if `cartridge_name`'s persuasive section already carries enough
    product-benefit claim_ids (MIN_BENEFIT_CLAIMS); otherwise one problem
    dict describing the shortfall. No-op for a cartridge not in
    MIN_BENEFIT_CLAIMS."""
    minimum = MIN_BENEFIT_CLAIMS.get(cartridge_name)
    get_section = _BENEFIT_SECTION_GETTERS.get(cartridge_name)
    if minimum is None or get_section is None:
        return []
    verified_by_id = {c["id"]: c for c in facts_pack.get("verified_claims", [])}
    section_ids = collect_claim_ids(get_section(page_json))
    benefit_ids = {cid for cid in section_ids if _is_benefit_claim_id(cid, verified_by_id)}
    if len(benefit_ids) < minimum:
        return [
            {
                "path": f"$.{cartridge_name}-benefit-claims",
                "issue": (
                    f"only {len(benefit_ids)} product-benefit claim_id(s) found "
                    f"({sorted(benefit_ids)}), need >= {minimum}"
                ),
            }
        ]
    return []


# ---------------------------------------------------------------------------
# (e) rendered-HTML visible-text EMF gate (fix cycle 2 item 9)
# ---------------------------------------------------------------------------

_SCRIPT_STYLE_RE = re.compile(r"<(script|style)\b[^>]*>.*?</\1>", re.IGNORECASE | re.DOTALL)
_COMMENT_RE = re.compile(r"<!--.*?-->", re.DOTALL)
_TAG_RE = re.compile(r"<[^>]+>")



def strip_html_to_visible_text(rendered_html):
    """Rendered HTML -> the text a reader (or a page-text extractor) actually
    sees: script/style contents and HTML comments dropped whole, every
    remaining tag (and therefore every attribute, including href/src) removed,
    then entities unescaped."""
    without_scripts = _SCRIPT_STYLE_RE.sub(" ", rendered_html)
    without_comments = _COMMENT_RE.sub(" ", without_scripts)
    without_tags = _TAG_RE.sub(" ", without_comments)
    return html.unescape(without_tags)


def find_forbidden_visible_text(rendered_html, terms=None):
    """A tenant's absolute bans (vocab.yaml's visible_text_forbidden_terms) are
    checked once more after rendering: scan the page as a reader would see it,
    not as page.json's structured fields. Catches a citation URL or a
    Sources-list link that got printed as visible link text even though it
    was exempt as a structural field pre-render. href/src attribute values
    are exempt -- they disappear along with their tag."""
    if terms is None:
        terms = vocab.VISIBLE_TEXT_FORBIDDEN_TERMS
    text = strip_html_to_visible_text(rendered_html).lower()
    hits = []
    for term in terms:
        if term in text:
            hits.append({"term": term, "issue": f"forbidden term {term!r} found in visible text"})
    return hits
