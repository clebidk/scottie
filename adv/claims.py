"""Deterministic claims gate. No model calls.

(a) Every ad_brief.claims_made string must token-overlap >= 0.6 with some
    verified claim's normalized text, AND every numeric token in the ad claim
    must also appear in that verified claim's text, or the run STOPs.
(b) Every page.json node with a "claim_ids" list must reference existing ids;
    every node with a "text" field containing a digit, %, $, or one of the
    trigger words must carry a non-empty claim_ids list, or the run STOPs.
(c) No page.json string anywhere may contain "EMF" (any case), a discontinued
    model name, "Sunlighten", or -- when claims/config.json's financing_lender
    is null -- any known financing-lender name, or the run STOPs.
(d) Fix cycle 2 item 11: if ad_brief.speaker_pov is "first_person", no page.json
    prose string outside a quoted-testimonial container may put the ad
    speaker's story in the author's own first person ("I ran...", "I don't...").
(e) Fix cycle 2 item 9: after rendering, the page's visible text (tags,
    scripts, and styles stripped, entities unescaped) may not contain "emf" or
    "electromagnetic" -- href/src attribute values are exempt because tag-
    stripping removes them along with the tag.
(f) Fix cycle 3 item 3: each cartridge's persuasive section (proof_bullets /
    how_it_works / turn_section.criteria) must carry a minimum count of
    product-benefit claim_ids (spec/benefit/trust category, excluding price/
    shipping/warranty/returns by id) -- see find_benefit_claim_shortfall.
"""
import html
import re

from .vocab import ALWAYS_FORBIDDEN_TERMS, FORBIDDEN_LENDER_NAMES, TRIGGER_WORDS

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
    def __init__(self, stage, items):
        super().__init__(f"claims gate STOP at stage {stage!r}: {len(items)} unmatched item(s)")
        self.stage = stage
        self.items = items


# ---------------------------------------------------------------------------
# (a) ad claims vs verified claims
# ---------------------------------------------------------------------------

def normalize(text):
    """lowercase, collapse number formatting so "$8,250" and "$8250.00" become
    the same token, strip remaining punctuation (keep $ and % since they carry
    meaning), drop stopwords -> list of tokens."""
    text = text.lower()
    text = re.sub(r"(?<=\d),(?=\d)", "", text)  # thousands separator: 8,250 -> 8250
    text = re.sub(r"\.00\b", "", text)  # cents suffix: 8250.00 -> 8250
    text = re.sub(r"[^a-z0-9%$\s]", " ", text)
    return [t for t in text.split() if t and t not in STOPWORDS]


def overlap_ratio(ad_tokens, verified_tokens):
    if not ad_tokens:
        return 0.0
    return len(set(ad_tokens) & set(verified_tokens)) / len(set(ad_tokens))


def numeric_tokens(tokens):
    """From an already-normalize()d token list, the subset of tokens that
    carry a digit, with any $/% stripped (commas and the .00 cents suffix are
    already gone -- normalize() strips those). Used so "$8,250" (ad phrasing)
    and "$8250.00" (verified.json phrasing) compare equal as the number 8250."""
    return {re.sub(r"[\$%]", "", t) for t in tokens if any(ch.isdigit() for ch in t)}


def match_claim(ad_claim_text, verified_claims):
    ad_tokens = normalize(ad_claim_text)
    ad_numbers = numeric_tokens(ad_tokens)
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
    if best_ratio >= 0.6:
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


def gate_ad_brief_claims(ad_brief, verified_claims):
    """Runs the ad_brief.claims_made list through the gate against the FULL
    claims/verified.json universe (not the per-product facts_pack subset --
    an ad claim can reference anything approved in verified.json, regardless
    of which product ends up being written about). Raises ClaimsGateFailure
    on any unmatched claim. speaker_experience is never passed through this
    gate."""
    matched, unmatched = gate_ad_claims(ad_brief.get("claims_made", []), verified_claims)
    if unmatched:
        raise ClaimsGateFailure("ad_claims", unmatched)
    return matched


# ---------------------------------------------------------------------------
# (b) page.json claim_ids validation
# ---------------------------------------------------------------------------

# Word-boundary, not substring: a plain substring check flags ordinary
# words that happen to contain a trigger word ("frustrated" contains
# "rated", "previews"/"interviews" contain "reviews") -- caught live on the
# hidden-costs-v2 fixture during fix-cycle-2 verification.
_TRIGGER_WORD_RE = re.compile(r"\b(?:" + "|".join(TRIGGER_WORDS) + r")\b")


def _trigger_reason(text):
    """None if `text` carries nothing that requires a claim_id; otherwise a
    short human-readable reason (fix cycle 4: named in the gate failure so a
    repair attempt knows exactly what to remove or cite, instead of
    re-reading the whole paragraph to guess). A verbatim customer quote
    (e.g. "It's 2026," she said) isn't the author's own factual assertion --
    don't require a claim_id just because the ad speaker's own words
    happened to include a number."""
    unquoted = _QUOTED_SPAN_RE.sub(" ", text)
    if "$" in unquoted:
        return "contains a dollar amount"
    if "%" in unquoted:
        return "contains a percentage"
    if re.search(r"\d", unquoted):
        return "contains a number"
    m = _TRIGGER_WORD_RE.search(unquoted.lower())
    if m:
        return f'uses the word "{m.group(0)}"'
    return None


def _contains_trigger(text):
    return _trigger_reason(text) is not None


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


def validate_page_claim_ids(page_json, valid_claim_ids):
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
            if isinstance(node.get("text"), str):
                text = node["text"]
                has_ref = bool(claim_ids) or bool(claim_id)
                reason = _trigger_reason(text)
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


# Structural/reference fields, not prose the writer composed -- a product
# URL or asset id can legitimately contain "emf" (the Shopify handle does)
# without it ever reaching rendered copy. Fix 4: "The product URL may still
# contain the word; that is fine."
_NON_PROSE_KEYS = {"url", "cta_url", "asset_id", "claim_ids", "claim_id", "id", "sku"}
_URL_RE = re.compile(r"https?://\S+")


def find_forbidden_terms(page_json, financing_lender=None):
    """Recursively scan every prose string in page_json for a forbidden term
    (case-insensitive substring), skipping structural/reference fields (url,
    asset_id, etc.) that aren't writer-composed copy. Lender names are only
    forbidden while no lender has been approved (claims/config.json
    financing_lender is null)."""
    forbidden = list(ALWAYS_FORBIDDEN_TERMS)
    if not financing_lender:
        forbidden += list(FORBIDDEN_LENDER_NAMES)
    hits = []

    def walk(node, path):
        if isinstance(node, str):
            # A citation URL inline in prose (e.g. "(Peak Saunas, 2026,
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
                if k in _NON_PROSE_KEYS:
                    continue
                walk(v, f"{path}.{k}")
        elif isinstance(node, list):
            for i, v in enumerate(node):
                walk(v, f"{path}[{i}]")

    walk(page_json, "$")
    return hits


# Fix cycle 2 item 2 / item 11: the page author (Austin) must never speak in
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


def gate_page_json(page_json, facts_pack, cartridge_name, financing_lender=None, speaker_pov=None):
    valid_ids = {c["id"] for c in facts_pack["verified_claims"]}
    problems = validate_page_claim_ids(page_json, valid_ids)
    problems += find_forbidden_terms(page_json, financing_lender=financing_lender)
    problems += find_first_person_violations(page_json, speaker_pov)
    problems += find_benefit_claim_shortfall(page_json, facts_pack, cartridge_name)
    if problems:
        raise ClaimsGateFailure(f"page_json:{cartridge_name}", problems)


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
_EXCLUDED_BENEFIT_IDS = {"warranty-terms", "shipping-policy", "returns-policy"}
_EXCLUDED_BENEFIT_ID_PREFIXES = ("price-",)

_BENEFIT_SECTION_GETTERS = {
    "product-page": lambda page: page.get("proof_bullets", []),
    "longform": lambda page: (page.get("how_it_works") or {}).get("steps", []),
    "article": lambda page: (page.get("turn_section") or {}).get("criteria", []),
}


def _is_benefit_claim_id(claim_id, verified_by_id):
    if claim_id in _EXCLUDED_BENEFIT_IDS or claim_id.startswith(_EXCLUDED_BENEFIT_ID_PREFIXES):
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

VISIBLE_TEXT_FORBIDDEN_TERMS = ("emf", "electromagnetic")


def strip_html_to_visible_text(rendered_html):
    """Rendered HTML -> the text a reader (or a page-text extractor) actually
    sees: script/style contents and HTML comments dropped whole, every
    remaining tag (and therefore every attribute, including href/src) removed,
    then entities unescaped."""
    without_scripts = _SCRIPT_STYLE_RE.sub(" ", rendered_html)
    without_comments = _COMMENT_RE.sub(" ", without_scripts)
    without_tags = _TAG_RE.sub(" ", without_comments)
    return html.unescape(without_tags)


def find_forbidden_visible_text(rendered_html, terms=VISIBLE_TEXT_FORBIDDEN_TERMS):
    """EMF is absolute (fix cycle 2 item 6/9): scan the page as a reader would
    see it, not as page.json's structured fields. Catches a citation URL or a
    Sources-list link that got printed as visible link text even though it
    was exempt as a structural field pre-render. href/src attribute values
    are exempt -- they disappear along with their tag."""
    text = strip_html_to_visible_text(rendered_html).lower()
    hits = []
    for term in terms:
        if term in text:
            hits.append({"term": term, "issue": f"forbidden term {term!r} found in visible text"})
    return hits
