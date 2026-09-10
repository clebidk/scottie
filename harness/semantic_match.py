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

from .anthropic_client import thinking_kwargs
from .jsonutil import extract_json

# Fix cycle 12 item 4: "≤120 items, trimmed" -- id + text only (not
# category/source), and capped so this one call's prompt never grows
# unbounded as claims/verified.json grows.
MAX_VERIFIED_CLAIMS_FOR_PROMPT = 120

SEMANTIC_MATCH_SYSTEM = """You map each ad claim to the one verified claim it means the same thing as, or null.

You will be given "ad_claims" (a list of strings) and "verified_claims" (a list of {"id": ..., "text": ...} objects, some also carrying an "aliases" list of known equivalent ad phrasings for that same claim -- treat an alias exactly like the claim's own "text" when judging whether an ad claim means the same thing).

Output ONLY a single JSON object whose keys are EXACTLY the ad claim strings you were given (verbatim, unchanged) and whose values are each either a verified claim's "id" string, or null. No markdown fences, no commentary before or after.

Rules:
- Map a claim to a verified claim's id only when they mean the same thing, even if the wording is completely different (e.g. "4-in-1: near, mid, far infrared + red light" means the same thing as a verified claim stating full-spectrum infrared plus red light therapy; "medical-grade panel" means the same thing as a verified claim about medical-grade red light therapy).
- Any number stated in the ad claim (a price, a count, a percentage, a duration, a dimension) MUST also appear in the verified claim's own text for that mapping to be valid -- if the numbers don't match, or the verified claim doesn't state a number the ad claim does, map to null instead, even if the rest of the wording is close.
- A comparative or superlative word in the ad claim -- "best", "only", "#1", "greatest", "smartest", "leading" -- always makes that claim null. A verified claim can never verify a superlative.
- NEVER map a warranty claim, a review-count/rating claim, a financing claim, or a price claim to anything -- always null for these, regardless of wording. Those are checked separately against their own locked source of truth, not against this list.
- If no verified claim means the same thing, map to null. Do not guess or force a loose match.
- Output valid JSON only."""


def _trim_verified_claims_for_prompt(verified_claims, limit=MAX_VERIFIED_CLAIMS_FOR_PROMPT):
    """id + text (+ aliases, when the claim has any -- fix cycle 16 item 11:
    a known ad phrasing like "4-in-1" is candidate text the model should see
    alongside the claim's own wording, not just a deterministic-matcher-only
    list) -- never category/source, the model doesn't need them to judge
    equivalent meaning -- capped to `limit` items."""
    trimmed = []
    for c in list(verified_claims)[:limit]:
        entry = {"id": c["id"], "text": c["text"]}
        if c.get("aliases"):
            entry["aliases"] = list(c["aliases"])
        trimmed.append(entry)
    return trimmed


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
            # Fix cycle 17 item 2: SEMANTIC_MATCH_SYSTEM never changes (no
            # tenant data, no run-specific value in it at all), so it's
            # cached like the writer's own stable prefix -- a real win across
            # repeated `harness run`s for the same tenant inside the 5-minute
            # ephemeral cache window, though a single run only ever makes
            # this one call, so there's no within-run repeat to hit. The
            # verified_claims candidate list in the user message is NOT
            # cached: it can carry a live/refreshed price claim's text, which
            # must never sit in a cached prefix.
            system=[{"type": "text", "text": SEMANTIC_MATCH_SYSTEM, "cache_control": {"type": "ephemeral"}}],
            messages=[{"role": "user", "content": user_content}],
            **thinking_kwargs(model),
            # Fix cycle 16 item 11: the brief asked for temperature 0 on this
            # call, for a deterministic mapping decision. Tried on the
            # server two ways -- a bare `temperature=0` kwarg (this client's
            # `messages.create` has no typed `temperature` parameter,
            # anthropic==1.4.0: raises TypeError) and `extra_body={"temperature":
            # 0}` (reaches the real API, which rejects it outright: "400
            # `temperature` is deprecated for this model") -- neither works
            # for claude-sonnet-5 on this deployment. Deliberately NOT sent:
            # the fallback-to-overlap-on-any-failure behavior below would
            # otherwise silently eat every semantic-match call, defeating
            # the model's ability to catch a phrasing an alias/word-overlap
            # can't. The real fix for the Thursday queue's specific variance
            # bug is alias_match (checked before this call ever runs, in
            # claims.match_claim) -- deterministic regardless of this call's
            # temperature, which is why it's what actually resolves item 11.
        )
        usage = response.usage
        if budget:
            budget.record_call(usage.input_tokens, usage.output_tokens)
        if log:
            log.call(
                "ad_claims.semantic_match", model, usage.input_tokens, usage.output_tokens,
                cache_creation_input_tokens=usage.cache_creation_input_tokens,
                cache_read_input_tokens=usage.cache_read_input_tokens,
            )

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
