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
