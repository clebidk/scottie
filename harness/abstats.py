"""Cycle 67: Beta-posterior statistics for A/B/C tests (harness/abtest.py).

Each variant's CTA click-through rate has a Beta(clicks + 1, views - clicks + 1)
posterior (a uniform prior). P(best) is the share of joint posterior draws in
which a variant has the highest rate -- a seeded Monte Carlo in plain Python
(numpy is not a dependency of this harness, and 3 variants x 20k draws takes a
fraction of a second).
"""
import random

DEFAULT_DRAWS = 20000


def beta_params(clicks, views):
    """(alpha, beta) of the posterior. Clicks above views (a lost view beacon)
    are clamped to views, so beta never goes below 1."""
    views = max(int(views), 0)
    clicks = min(max(int(clicks), 0), views)
    return clicks + 1, views - clicks + 1


def ctr(clicks, views):
    """Observed click-through rate, clamped to [0, 1]; 0 with no views."""
    if views <= 0:
        return 0.0
    return min(max(clicks, 0), views) / views


def p_best(data, *, draws=DEFAULT_DRAWS, seed=0):
    """{key: P(this key has the highest CTR)} for data {key: (clicks, views)}.
    Deterministic for a given seed. The values sum to 1."""
    keys = list(data)
    if not keys:
        return {}
    params = [beta_params(*data[k]) for k in keys]
    rng = random.Random(seed)
    wins = [0] * len(keys)
    for _ in range(draws):
        best_i, best_v = 0, -1.0
        for i, (a, b) in enumerate(params):
            v = rng.betavariate(a, b)
            if v > best_v:
                best_i, best_v = i, v
        wins[best_i] += 1
    return {k: wins[i] / draws for i, k in enumerate(keys)}
