"""Fix cycle 17 item 1: per-model pricing, including prompt-cache and batch
multipliers, replacing the old hard-coded $3/$15-per-million blended rate."""
import pytest

from harness import pricing


def test_price_for_known_models():
    assert pricing.price_for("claude-sonnet-5") == {"input": 2.00, "output": 10.00}
    assert pricing.price_for("claude-haiku-4-5") == {"input": 1.00, "output": 5.00}


def test_price_for_unknown_model_raises():
    with pytest.raises(pricing.UnknownModel):
        pricing.price_for("claude-opus-5")


def test_calculate_cost_plain_input_and_output_sonnet():
    # 1M in + 1M out at $2/$10 per million.
    cost = pricing.calculate_cost("claude-sonnet-5", input_tokens=1_000_000, output_tokens=1_000_000)
    assert cost == pytest.approx(12.00)


def test_calculate_cost_plain_input_and_output_haiku():
    cost = pricing.calculate_cost("claude-haiku-4-5", input_tokens=1_000_000, output_tokens=1_000_000)
    assert cost == pytest.approx(6.00)


def test_calculate_cost_cache_read_is_one_tenth_input_price():
    cost = pricing.calculate_cost("claude-sonnet-5", cache_read_input_tokens=1_000_000)
    assert cost == pytest.approx(2.00 * pricing.CACHE_READ_MULTIPLIER)
    assert cost == pytest.approx(0.20)


def test_calculate_cost_cache_write_is_1_25x_input_price():
    cost = pricing.calculate_cost("claude-sonnet-5", cache_creation_input_tokens=1_000_000)
    assert cost == pytest.approx(2.00 * pricing.CACHE_WRITE_MULTIPLIER)
    assert cost == pytest.approx(2.50)


def test_calculate_cost_combines_every_token_type():
    cost = pricing.calculate_cost(
        "claude-sonnet-5",
        input_tokens=1000, output_tokens=500,
        cache_creation_input_tokens=2000, cache_read_input_tokens=4000,
    )
    expected = (
        1000 * 2.00
        + 2000 * 2.00 * pricing.CACHE_WRITE_MULTIPLIER
        + 4000 * 2.00 * pricing.CACHE_READ_MULTIPLIER
        + 500 * 10.00
    ) / 1_000_000
    assert cost == pytest.approx(expected)


def test_calculate_cost_batch_applies_50_percent_off_the_whole_total():
    non_batch = pricing.calculate_cost("claude-sonnet-5", input_tokens=1000, output_tokens=1000)
    batch = pricing.calculate_cost("claude-sonnet-5", input_tokens=1000, output_tokens=1000, batch=True)
    assert batch == pytest.approx(non_batch * pricing.BATCH_MULTIPLIER)
    assert batch == pytest.approx(non_batch * 0.5)


def test_calculate_cost_batch_also_discounts_cache_tokens():
    batch = pricing.calculate_cost(
        "claude-sonnet-5", cache_creation_input_tokens=1_000_000, cache_read_input_tokens=1_000_000, batch=True,
    )
    non_batch = pricing.calculate_cost(
        "claude-sonnet-5", cache_creation_input_tokens=1_000_000, cache_read_input_tokens=1_000_000,
    )
    assert batch == pytest.approx(non_batch * 0.5)


def test_calculate_cost_zero_tokens_is_zero_cost():
    assert pricing.calculate_cost("claude-sonnet-5") == 0.0
