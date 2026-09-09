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
"""
import re

STOPWORDS = {
    "a", "an", "the", "is", "are", "was", "were", "be", "been", "being", "to", "of", "in",
    "on", "at", "for", "with", "and", "or", "but", "that", "this", "it", "its", "as", "by",
    "from", "has", "have", "had", "not", "no", "so", "than", "then", "too", "very", "can",
    "will", "would", "should", "could", "just", "about", "into", "over", "under", "up",
    "down", "out", "if", "we", "you", "your", "our", "their", "they", "he", "she", "i",
}

TRIGGER_WORDS = ("medical", "clinical", "study", "proven", "emf", "rated", "reviews")

# Fix 4 (EMF must never appear) + Fix 6 (never name a competitor trademark or a
# discontinued Peak model in generated copy). Checked case-insensitively as a
# plain substring anywhere in page.json text.
ALWAYS_FORBIDDEN_TERMS = ("emf", "sunlighten", "crown", "olympus", "aspen")

# Fix 6: never name a specific financing lender unless one has been approved
# in claims/config.json (financing_lender non-null).
FORBIDDEN_LENDER_NAMES = ("bread pay", "affirm", "shop pay", "klarna", "afterpay", "sezzle")


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

def _contains_trigger(text):
    if re.search(r"\d", text) or "%" in text or "$" in text:
        return True
    lower = text.lower()
    return any(w in lower for w in TRIGGER_WORDS)


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
                if _contains_trigger(text) and not has_ref:
                    problems.append(
                        {"path": path, "issue": "text needs at least one claim_id", "text": text}
                    )
            for k, v in node.items():
                walk(v, f"{path}.{k}")
        elif isinstance(node, list):
            for i, v in enumerate(node):
                walk(v, f"{path}[{i}]")

    walk(page_json, "$")
    return problems


def find_forbidden_terms(page_json, financing_lender=None):
    """Recursively scan every string in page_json for a forbidden term
    (case-insensitive substring). Lender names are only forbidden while no
    lender has been approved (claims/config.json financing_lender is null)."""
    forbidden = list(ALWAYS_FORBIDDEN_TERMS)
    if not financing_lender:
        forbidden += list(FORBIDDEN_LENDER_NAMES)
    hits = []

    def walk(node, path):
        if isinstance(node, str):
            lower = node.lower()
            for term in forbidden:
                if term in lower:
                    hits.append({"path": path, "term": term, "issue": f"forbidden term {term!r} found", "text": node})
        elif isinstance(node, dict):
            for k, v in node.items():
                walk(v, f"{path}.{k}")
        elif isinstance(node, list):
            for i, v in enumerate(node):
                walk(v, f"{path}[{i}]")

    walk(page_json, "$")
    return hits


def gate_page_json(page_json, facts_pack, cartridge_name, financing_lender=None):
    valid_ids = {c["id"] for c in facts_pack["verified_claims"]}
    problems = validate_page_claim_ids(page_json, valid_ids)
    problems += find_forbidden_terms(page_json, financing_lender=financing_lender)
    if problems:
        raise ClaimsGateFailure(f"page_json:{cartridge_name}", problems)
