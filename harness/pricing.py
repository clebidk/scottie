"""Per-model token pricing for cost-estimate logging (harness/log.py).

Fix cycle 17: replaces the single hard-coded $3/$15-per-million blended rate
(harness/config.py's old INPUT_COST_PER_M/OUTPUT_COST_PER_M -- a rate this
harness never actually paid) with real per-model rates, plus the prompt-cache
and batch multipliers now genuinely in play (write.py's cache_control blocks,
cli.py's --batch flag). Every dollar figure below is $ per million tokens.
"""

PRICES_PER_MILLION = {
    "claude-sonnet-5": {"input": 2.00, "output": 10.00},
    "claude-haiku-4-5": {"input": 1.00, "output": 5.00},
}

# A cache write (cache_creation_input_tokens) costs MORE than an ordinary
# input token (you pay to populate the cache); a cache read
# (cache_read_input_tokens) costs a fraction of one (that's the saving).
CACHE_READ_MULTIPLIER = 0.10
CACHE_WRITE_MULTIPLIER = 1.25

# The Message Batches API is a flat 50% off every token type in the request.
BATCH_MULTIPLIER = 0.5


class UnknownModel(Exception):
    """No price entry for a model id -- fail loudly rather than silently cost
    it at $0 (or at some other model's rate)."""


def price_for(model):
    """{"input": ..., "output": ...} $/M tokens for `model`."""
    try:
        return PRICES_PER_MILLION[model]
    except KeyError:
        raise UnknownModel(f"no price entry for model {model!r}; add it to harness/pricing.py") from None


def calculate_cost(model, input_tokens=0, output_tokens=0, *,
                    cache_creation_input_tokens=0, cache_read_input_tokens=0, batch=False):
    """Estimated USD cost of one Claude call (or one batch result).

    `input_tokens` is the uncached portion (usage.input_tokens);
    cache_creation_input_tokens and cache_read_input_tokens are the Anthropic
    SDK Usage object's own separate fields, priced at CACHE_WRITE_MULTIPLIER
    and CACHE_READ_MULTIPLIER of the model's input rate respectively.
    batch=True applies BATCH_MULTIPLIER to the whole total, matching the
    Batches API's flat 50% discount across every token type in the request.
    """
    price = price_for(model)
    cost = (
        input_tokens * price["input"]
        + cache_creation_input_tokens * price["input"] * CACHE_WRITE_MULTIPLIER
        + cache_read_input_tokens * price["input"] * CACHE_READ_MULTIPLIER
        + output_tokens * price["output"]
    ) / 1_000_000
    if batch:
        cost *= BATCH_MULTIPLIER
    return cost
