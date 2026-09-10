"""Fix cycle 12 item 4: ONE real Claude call, made once per run before
token-overlap matching, proposing a semantic (equivalent-meaning) mapping
from each ad claim to a verified claim id or null.

This exists because word-overlap alone (claims.match_claim) misses claims
that are true but phrased nothing like the verified claim's own text --
"4-in-1: near, mid, far infrared + red light" should match the full-spectrum
and red-light allowlist claims, and "medical-grade panel" should match the
medical-grade red light claim, but neither shares enough tokens with either
verified claim's wording to clear the 0.6 overlap bar.

A mapping this call proposes is only ever a SUGGESTION -- claims.match_claim
still enforces the numeric-token guard in code before accepting it (see
match_claim's own docstring). If this call fails for any reason (bad JSON,
an API error, an empty response), semantic_match_claims returns {} and the
caller falls through to pure word-overlap matching for every claim, exactly
as before this fix -- this is an assist, never a hard dependency.
"""
import json

from .jsonutil import extract_json

# Fix cycle 12 item 4: "≤120 items, trimmed" -- id + text only (not
# category/source), and capped so this one call's prompt never grows
# unbounded as claims/verified.json grows.
MAX_VERIFIED_CLAIMS_FOR_PROMPT = 120

SEMANTIC_MATCH_SYSTEM = """You map each ad claim to the one verified claim it means the same thing as, or null.

You will be given "ad_claims" (a list of strings) and "verified_claims" (a list of {"id": ..., "text": ...} objects).

Output ONLY a single JSON object whose keys are EXACTLY the ad claim strings you were given (verbatim, unchanged) and whose values are each either a verified claim's "id" string, or null. No markdown fences, no commentary before or after.

Rules:
- Map a claim to a verified claim's id only when they mean the same thing, even if the wording is completely different (e.g. "4-in-1: near, mid, far infrared + red light" means the same thing as a verified claim stating full-spectrum infrared plus red light therapy; "medical-grade panel" means the same thing as a verified claim about medical-grade red light therapy).
- Any number stated in the ad claim (a price, a count, a percentage, a duration, a dimension) MUST also appear in the verified claim's own text for that mapping to be valid -- if the numbers don't match, or the verified claim doesn't state a number the ad claim does, map to null instead, even if the rest of the wording is close.
- A comparative or superlative word in the ad claim -- "best", "only", "#1", "greatest", "smartest", "leading" -- always makes that claim null. A verified claim can never verify a superlative.
- NEVER map a warranty claim, a review-count/rating claim, a financing claim, or a price claim to anything -- always null for these, regardless of wording. Those are checked separately against their own locked source of truth, not against this list.
- If no verified claim means the same thing, map to null. Do not guess or force a loose match.
- Output valid JSON only."""


def _trim_verified_claims_for_prompt(verified_claims, limit=MAX_VERIFIED_CLAIMS_FOR_PROMPT):
    """id + text only (never category/source -- the model doesn't need them
    to judge equivalent meaning), capped to `limit` items."""
    return [{"id": c["id"], "text": c["text"]} for c in list(verified_claims)[:limit]]


def semantic_match_claims(claims_made, verified_claims, *, client, model, budget=None, log=None):
    """{ad_claim_text: verified_id_or_None}. Never raises -- any failure (an
    empty/invalid JSON response, an API error) is logged as an event and
    {} is returned so the caller falls back to pure word-overlap matching
    for every claim. Logs each mapping (fix cycle 12 item 4: "Log each
    mapping.") once the call succeeds."""
    if not claims_made:
        return {}

    trimmed = _trim_verified_claims_for_prompt(verified_claims)
    user_content = json.dumps({"ad_claims": list(claims_made), "verified_claims": trimmed})

    try:
        if budget:
            budget.check()
        response = client.messages.create(
            model=model,
            max_tokens=1500,
            thinking={"type": "disabled"},
            system=SEMANTIC_MATCH_SYSTEM,
            messages=[{"role": "user", "content": user_content}],
        )
        usage = response.usage
        if budget:
            budget.record_call(usage.input_tokens, usage.output_tokens)
        if log:
            log.call("ad_claims.semantic_match", model, usage.input_tokens, usage.output_tokens)

        text = "".join(b.text for b in response.content if getattr(b, "type", None) == "text")
        mapping = extract_json(text)
        if not isinstance(mapping, dict):
            raise ValueError("semantic match response is not a JSON object")
    except Exception as e:
        if log:
            log.event("ad_claims.semantic_match", f"semantic match call failed, falling back to overlap: {e}")
        return {}

    if log:
        for claim in claims_made:
            log.event("ad_claims.semantic_match", f"{claim!r} -> {mapping.get(claim)!r}")

    return mapping
