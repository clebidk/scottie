"""Small helpers shared by more than one engine module.

Everything here existed already, two to four times over, in modules that had no
reason to agree with each other by accident:

    NON_PROSE_KEYS      harness/cli.py and harness/claims.py, identical copies
    DOLLAR_AMOUNT_RE    harness/claims.py and harness/ground.py, identical
    product_name_slug   harness/ground.py, harness/prices.py (twice), and
                        harness/pdp_claims.py, written out longhand each time

Nothing company-specific belongs in this module.
"""
import re

# page.json keys holding structural/reference data rather than prose the writer
# composed. A product URL or an asset id can legitimately contain a term that
# would be forbidden in copy, and a claim_ids list legitimately contains
# id-shaped tokens -- neither is writer-composed text, so every prose scan
# (forbidden terms, leaked claim ids, warranty wording) and the word count skip
# these keys.
NON_PROSE_KEYS = frozenset({"url", "cta_url", "asset_id", "claim_ids", "claim_id", "id", "sku"})

# "$8,250" / "$ 8250.00" -- the dollar figure itself is group 1, without the
# sign, so a caller can float() it after stripping commas.
DOLLAR_AMOUNT_RE = re.compile(r"\$\s?([\d,]+(?:\.\d+)?)")


def product_name_slug(name):
    """A product's `name` as the slug its claim ids are namespaced under, e.g.
    "Model Two" -> "model-two". This is the rule that ties `price-<slug>`,
    `spec-<slug>-*`, and `pdp-<slug>-*` together; it must stay identical
    everywhere or a claim silently stops resolving."""
    return (name or "").lower().replace(" ", "-")
