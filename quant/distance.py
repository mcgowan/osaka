"""Normalized distance and strike-grid placement (task 1.1, FR-4).

Definitions (docs/requirements.md, Section 3 + FR-4):
    D = (S - K) / (sigma * sqrt(T) * S)   for puts  (K below spot -> D > 0)
    D = (K - S) / (sigma * sqrt(T) * S)   for calls (K above spot -> D > 0)

D is "how many remaining implied moves away is the strike"; positive = OTM.
Grid strikes are placed at the GRID_ANCHORS targets and snapped to the
listed 5-pt increment; the SNAPPED strike's actual distance is the feature
(anchors are sampling targets, not data values).
"""

from .conventions import GRID_ANCHORS, STRIKE_INCREMENT

SIDES = ("p", "c")


def _check(side, S, sigma, T):
    if side not in SIDES:
        raise ValueError(f"side must be 'p' or 'c', got {side!r}")
    if S <= 0 or sigma <= 0 or T <= 0:
        raise ValueError(f"need S, sigma, T > 0; got S={S} sigma={sigma} T={T}")


def normalized_distance(K, S, sigma, T, side):
    """Signed OTM distance of strike K in remaining-implied-move units."""
    _check(side, S, sigma, T)
    if K <= 0:
        raise ValueError(f"need K > 0, got {K}")
    moved = sigma * (T ** 0.5) * S
    return (S - K) / moved if side == "p" else (K - S) / moved


def strike_for_distance(D, S, sigma, T, side):
    """Unsnapped strike at distance D (inverse of normalized_distance)."""
    _check(side, S, sigma, T)
    moved = sigma * (T ** 0.5) * S
    return S - D * moved if side == "p" else S + D * moved


def snap(K, increment=STRIKE_INCREMENT):
    """Nearest listed strike. Exact midpoints follow Python's half-to-even
    rounding (snap(7602.5) -> 7600, snap(7607.5) -> 7610) - deterministic,
    and immaterial since spot is effectively never exactly on a midpoint."""
    return round(K / increment) * increment


def place_grid(S, sigma, T, side, anchors=None):
    """Grid strikes for one (bar, side): [(anchor, strike, actual_D), ...].

    Strikes are snapped; actual_D is the snapped strike's true distance —
    that is what enters the feature row. Snapping can collide two anchors
    onto one strike when sigma*sqrt(T)*S is small (late day); collisions are
    kept (same strike, two anchors) and deduplicated downstream by the
    label/feature pipeline, which keys on the strike.

    Snapped strikes that land at or across spot (actual_D <= 0, possible
    when the remaining implied move is under half a strike increment) are
    DROPPED: the model's domain is OTM strikes, and an at/through-spot row
    is an instant-touch tautology, not a sample. (Math-verifier finding,
    2026-06-11.) Callers must tolerate len(result) < len(anchors) late in
    the session.
    """
    if anchors is None:
        anchors = GRID_ANCHORS[side]
    out = []
    for a in anchors:
        K = snap(strike_for_distance(a, S, sigma, T, side))
        if K <= 0:
            continue
        d = normalized_distance(K, S, sigma, T, side)
        if d <= 0:
            continue
        out.append((a, K, d))
    return out
