"""Analytic market-implied no-touch probability p_mkt (task 1.2, FR-5.1).

Model: zero-drift GBM for the underlying (rates ~ 0 at 0DTE horizon),
    d ln S_t = -sigma^2/2 dt + sigma dW_t
First-passage (reflection principle) survival probability for a barrier K:

    b  = ln(K / S)                 (b < 0 puts, b > 0 calls)
    nu = -sigma^2 / 2
    puts  (barrier below): P(min > K) = N((-b + nu*T)/(sigma*sqrt(T)))
                                      - exp(2*nu*b/sigma^2) * N(( b + nu*T)/(sigma*sqrt(T)))
    calls (barrier above): P(max < K) = N(( b - nu*T)/(sigma*sqrt(T)))
                                      - exp(2*nu*b/sigma^2) * N((-b - nu*T)/(sigma*sqrt(T)))

This is the evaluation BASELINE, not a feature of the world: it needs
documented, stable bias (validated vs recorded chains in task 1.4), not
perfection. Internal consistency with quant.distance (same sigma, same
sqrt(T) convention) outranks absolute accuracy (NFR-3.4).
"""

import math
from statistics import NormalDist

from .distance import _check

_N = NormalDist().cdf


def no_touch_prob(K, S, sigma, T, side):
    """P(price never touches K before settlement) under zero-drift GBM.

    Returns a probability in [0, 1]. A barrier already at or behind the spot
    (K >= S for puts, K <= S for calls) returns 0.0 - touched by definition.
    """
    _check(side, S, sigma, T)
    if K <= 0:
        raise ValueError(f"need K > 0, got {K}")
    if (side == "p" and K >= S) or (side == "c" and K <= S):
        return 0.0
    b = math.log(K / S)
    nu = -sigma * sigma / 2.0
    sq = sigma * math.sqrt(T)
    reflect = math.exp(2.0 * nu * b / (sigma * sigma))  # = exp(-b) = S/K
    if side == "p":
        p = _N((-b + nu * T) / sq) - reflect * _N((b + nu * T) / sq)
    else:
        p = _N((b - nu * T) / sq) - reflect * _N((-b - nu * T) / sq)
    return min(1.0, max(0.0, p))
