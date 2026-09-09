"""Fix cycle 4 item 4: single source of truth for forbidden vocabulary. Both
the writer prompt (write.py's GLOBAL_VOICE_BLOCK) and the deterministic gate
(claims.py) import these lists so they can never drift apart -- a word added
here is enforced by the gate and told to the writer in the same edit.
"""

# Absolute bans -- never allowed anywhere in writer-composed prose, no matter
# what claim_id backs the sentence. Split into named groups so write.py's
# prompt can quote each group in a natural sentence; claims.py's gate just
# flattens them all into ALWAYS_FORBIDDEN_TERMS below.
EMF_TERMS = ("emf", "electromagnetic")
BANNED_NAMES = ("sunlighten", "crown", "olympus", "aspen")
HYPE_WORDS = ("game-changer", "unlock", "elevate", "journey", "revolutionary", "unleash")
EXCLAMATION_MARK = "!"

ALWAYS_FORBIDDEN_TERMS = EMF_TERMS + BANNED_NAMES + HYPE_WORDS + (EXCLAMATION_MARK,)

# Financing-lender names -- forbidden only while claims/config.json's
# financing_lender is unset (null).
FORBIDDEN_LENDER_NAMES = ("bread pay", "affirm", "shop pay", "klarna", "afterpay", "sezzle")

# Words that may appear ONLY when the sentence carries a claim_id (fix cycle
# 4 item 4: "reviews" as a bare word without a claim id) -- enforced by
# claims.py's trigger-word check, not the absolute-ban list above.
TRIGGER_WORDS = ("medical", "clinical", "study", "proven", "emf", "rated", "reviews")

# Fix cycle 6 item 4: while no lender is configured (claims/config.json's
# financing_lender is null), this is the ONLY sentence the writer may use to
# mention financing anywhere on the page -- the model kept inventing its own
# financing phrasing with numbers ("as low as $X/mo") that had no claim_id.
# Single source of truth for both the writer prompt (write.py) and the gate
# (claims.py), like every other list in this file.
ALLOWED_FINANCING_SENTENCE_NO_LENDER = "Financing is available at checkout."

# Fix cycle 7 item 1: warranty wording, same pattern as the financing sentence
# above -- claims/verified.json's warranty-terms/gbrain-warranty-component-
# coverage claims show per-component coverage (heating/cabinetry 7yr,
# control system/RLT panel 3yr, chromotherapy/audio/WiFi/accessories 1yr),
# NOT a blanket lifetime on every component. A product-page run wrote
# "Limited lifetime warranty on the cabin, heating elements, and
# electronics" -- false for electronics (3yr or 1yr, not lifetime). The only
# two allowed forms anywhere text mentions "warrant": this exact sentence,
# or (in a spec-table-shaped row) the label/value pair below -- or a verbatim
# quote of a verified warranty claim's own text.
ALLOWED_WARRANTY_SENTENCE = "Limited lifetime warranty; full terms by component are published on the warranty page."
ALLOWED_WARRANTY_SPEC_LABEL = "Warranty"
ALLOWED_WARRANTY_SPEC_VALUE = "Limited lifetime warranty (terms by component)"

# Fix cycle 7 item 2: implied claims -- inferring a second, unverified fact
# from a verified one (e.g. "US-owned" -> "the person you'd reach is
# domestic, not a call center"). Forbidden unless a verified claim's own
# text actually contains the phrase (find_forbidden_terms checks
# facts_pack.verified_claims before adding these to the forbidden list) --
# same conditional pattern as FORBIDDEN_LENDER_NAMES above, gated on content
# instead of a config flag.
IMPLIED_CLAIM_FORBIDDEN_TERMS = (
    "call center", "domestic support", "us-based support", "american-made", "made in the usa",
)


def forbidden_words_block(heading="Forbidden words -- never use any of these, in any form, anywhere on the page:"):
    """Fix cycle 6 item 3: the forbidden-word list, verbatim, one per line --
    used both at the top of the writer's system prompt (write.py) and inside
    every REVISION REQUIRED block (cli.py), so a repair attempt can't claim
    it forgot the list."""
    return heading + "\n" + "\n".join(ALWAYS_FORBIDDEN_TERMS)
