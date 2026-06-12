"""Tests for quant/ (Phase 1 tasks 1.1 + 1.2).

Run: .venv/bin/python -m pytest tests/test_quant.py -q
The Monte Carlo check is seeded and deterministic.
"""

import math
import os
import sys
from datetime import datetime

import numpy as np
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from quant.conventions import (  # noqa: E402
    GRID_ANCHORS, YEAR_SECONDS, time_to_settle,
)
from quant.distance import (  # noqa: E402
    normalized_distance, place_grid, snap, strike_for_distance,
)
from quant.pmkt import no_touch_prob  # noqa: E402

S, SIGMA = 7600.0, 0.12
T_3H = 3 * 3600 / YEAR_SECONDS


# --- task 1.1: distance & grid ---------------------------------------------

def test_distance_round_trip():
    for side in ("p", "c"):
        for D in (0.1, 0.8, 1.3, 2.1, 3.0):
            for T in (5 * 60 / YEAR_SECONDS, T_3H, 6.5 * 3600 / YEAR_SECONDS):
                K = strike_for_distance(D, S, SIGMA, T, side)
                assert normalized_distance(K, S, SIGMA, T, side) == pytest.approx(D, abs=1e-9)


def test_distance_sign_conventions():
    # OTM put strike below spot -> positive D; above spot -> negative
    assert normalized_distance(7500, S, SIGMA, T_3H, "p") > 0
    assert normalized_distance(7700, S, SIGMA, T_3H, "p") < 0
    assert normalized_distance(7700, S, SIGMA, T_3H, "c") > 0
    assert normalized_distance(7500, S, SIGMA, T_3H, "c") < 0
    # put strikes sit below spot, call strikes above, same D
    assert strike_for_distance(1.3, S, SIGMA, T_3H, "p") < S
    assert strike_for_distance(1.3, S, SIGMA, T_3H, "c") > S


def test_distance_input_validation():
    for bad in (dict(side="x"), dict(sigma=0), dict(T=-1), dict(S=0)):
        kwargs = dict(K=7500, S=S, sigma=SIGMA, T=T_3H, side="p")
        kwargs.update(bad)
        with pytest.raises(ValueError):
            normalized_distance(**kwargs)


def test_snap():
    assert snap(7602.4) == 7600
    assert snap(7602.6) == 7605
    assert snap(7600.0) == 7600


def test_place_grid_structure():
    grid = place_grid(S, SIGMA, T_3H, "p")
    assert len(grid) == len(GRID_ANCHORS["p"])
    for anchor, K, actual_d in grid:
        assert K % 5 == 0
        assert K < S
        # actual distance is the snapped strike's distance, not the anchor
        assert actual_d == pytest.approx(
            normalized_distance(K, S, SIGMA, T_3H, "p"), abs=1e-12)
        assert abs(actual_d - anchor) < 0.5  # snapped near the target
    strikes = [k for _, k, _ in grid]
    assert strikes == sorted(strikes, reverse=True)  # farther anchors, lower puts
    # call grid mirrors above spot with its own anchors
    cgrid = place_grid(S, SIGMA, T_3H, "c")
    assert len(cgrid) == len(GRID_ANCHORS["c"])
    assert all(k > S for _, k, _ in cgrid)


def test_delta_bucket():
    from quant.conventions import BAND_OF_RECORD, delta_bucket
    # put edges: 3.363 / 2.306 / 1.729 / 1.051
    assert delta_bucket(4.0, "p") == "lt05"
    assert delta_bucket(2.5, "p") == "05-10"
    assert delta_bucket(2.0, "p") == BAND_OF_RECORD
    assert delta_bucket(1.2, "p") == "15-25"
    assert delta_bucket(0.5, "p") == "gt25"
    # side asymmetry: same D, different bucket
    assert delta_bucket(2.0, "c") == "05-10"


def test_place_grid_late_day_collisions_kept_itm_dropped():
    # 5 minutes to settle: implied move tiny; anchors collide onto few
    # strikes (kept) and near anchors can snap onto/across spot (dropped)
    T = 5 * 60 / YEAR_SECONDS
    grid = place_grid(S, SIGMA, T, "p")
    assert 0 < len(grid) <= len(GRID_ANCHORS["p"])
    assert all(d > 0 for _, _, d in grid)  # never an at/through-spot row
    # verifier's minimal reproduction: put anchor snapping above spot
    repro = place_grid(6004, 0.12, 3 * 60 / YEAR_SECONDS, "p")
    assert all(k < 6004 and d > 0 for _, k, d in repro)


def test_time_to_settle():
    t = datetime(2026, 6, 1, 13, 0)  # 1 PM ET on a full day -> 3h
    assert time_to_settle(t) == pytest.approx(T_3H)
    half = time_to_settle(datetime(2026, 7, 3, 12, 0), is_half_day=True)
    assert half == pytest.approx(3600 / YEAR_SECONDS)
    with pytest.raises(ValueError):
        time_to_settle(datetime(2026, 6, 1, 16, 0))


# --- task 1.2: p_mkt --------------------------------------------------------

def test_pmkt_limits_and_bounds():
    # far OTM -> ~1; at the money -> 0 (barrier at spot)
    assert no_touch_prob(6000, S, SIGMA, T_3H, "p") > 0.9999
    assert no_touch_prob(S, S, SIGMA, T_3H, "p") == 0.0
    assert no_touch_prob(S, S, SIGMA, T_3H, "c") == 0.0
    # ITM-side barrier -> 0
    assert no_touch_prob(7700, S, SIGMA, T_3H, "p") == 0.0
    assert no_touch_prob(7500, S, SIGMA, T_3H, "c") == 0.0
    for K in (7400, 7550, 7590):
        assert 0.0 <= no_touch_prob(K, S, SIGMA, T_3H, "p") <= 1.0


def test_pmkt_monotone_in_distance_and_time():
    # farther strike -> higher survival (strict within the non-saturated
    # range; far strikes saturate to exactly 1.0 in float, hence the bound)
    ps = [no_touch_prob(K, S, SIGMA, T_3H, "p") for K in range(7595, 7535, -5)]
    assert all(b > a for a, b in zip(ps, ps[1:]))
    assert all(b >= a for a, b in zip(ps, ps[1:]))  # and never decreasing
    # less time remaining -> higher survival, same strike
    ts = [no_touch_prob(7550, S, SIGMA, h * 3600 / YEAR_SECONDS, "p")
          for h in (6.5, 5, 3, 1, 0.25)]
    assert all(b > a for a, b in zip(ts, ts[1:]))


def test_pmkt_put_call_near_symmetry():
    # log-symmetric barriers are NEARLY symmetric; the -sigma^2/2 drift makes
    # the lower barrier slightly easier to touch, so put survival < call
    # survival by O(nu*T / sigma*sqrt(T)) - tiny at 0DTE horizons.
    for d_log in (0.002, 0.005, 0.01):
        pp = no_touch_prob(S * math.exp(-d_log), S, SIGMA, T_3H, "p")
        pc = no_touch_prob(S * math.exp(d_log), S, SIGMA, T_3H, "c")
        assert pp == pytest.approx(pc, abs=5e-3)
        if 1e-9 < pp < 1 - 1e-9:  # away from saturation the ordering is strict
            assert pp < pc


def test_f_correction_interpolation():
    from quant.conventions import F_T_KNOTS, f_correction
    for side in ("p", "c"):
        knots = F_T_KNOTS[side]
        # exact at knots, flat beyond ends
        for m, v in knots:
            assert f_correction(side, m) == pytest.approx(v)
        assert f_correction(side, 0) == knots[0][1]
        assert f_correction(side, 389) == knots[-1][1]
        # between first two knots: strictly between their values
        mid = f_correction(side, (knots[0][0] + knots[1][0]) / 2)
        lo, hi = sorted((knots[0][1], knots[1][1]))
        assert lo < mid < hi
    # puts always carry the bigger correction (skew)
    for m in (5, 100, 250, 330):
        assert f_correction("p", m) > f_correction("c", m)


def test_p_mkt_baseline_vs_uncorrected():
    from quant.pmkt import p_mkt
    # corrected vol > anchor vol => lower survival than the naive number
    naive = no_touch_prob(7550, S, SIGMA, T_3H, "p")
    corrected = p_mkt(7550, S, SIGMA, T_3H, "p", minutes_since_open=210)
    assert corrected < naive
    assert 0.0 <= corrected <= 1.0
    # equals no_touch_prob at the corrected sigma exactly
    from quant.conventions import f_correction
    sigma_c = f_correction("p", 210) * SIGMA
    assert corrected == pytest.approx(
        no_touch_prob(7550, S, sigma_c, T_3H, "p"), abs=1e-15)


def test_p_mkt_composite_monotonicity():
    """The model's monotone constraint will lean on p_mkt behaving sensibly
    THROUGH the f(t) correction, not just on the raw formula. Two composite
    properties across the session clock:
    1. fixed strike, advancing clock (T shrinks as minutes grow): survival
       non-decreasing - even where f(t) is locally rising (calls after 210').
    2. fixed clock: survival monotone in strike distance."""
    from quant.pmkt import p_mkt
    for side, K in (("p", 7550.0), ("c", 7650.0)):
        ps = []
        for m in range(5, 386, 5):
            T = (390 - m) * 60 / YEAR_SECONDS
            ps.append(p_mkt(K, S, SIGMA, T, side, m))
        assert all(b >= a for a, b in zip(ps, ps[1:])), side
        assert ps[-1] > ps[0]  # and it genuinely moves
    # distance monotonicity at several clock points, through f(t)
    for m in (5, 90, 210, 330):
        T = (390 - m) * 60 / YEAR_SECONDS
        for side, ks in (("p", range(7595, 7480, -5)), ("c", range(7605, 7720, 5))):
            ps = [p_mkt(k, S, SIGMA, T, side, m) for k in ks]
            assert all(b >= a for a, b in zip(ps, ps[1:])), (side, m)


def test_time_to_settle_cal_half_day_lookup():
    from quant.conventions import time_to_settle_cal
    # 2024-07-03 was a 13:00 ET close; the wrapper must find that itself
    assert time_to_settle_cal(datetime(2024, 7, 3, 12, 0)) == pytest.approx(
        time_to_settle(datetime(2024, 7, 3, 12, 0), is_half_day=True))
    # and a normal day settles at 16:00
    assert time_to_settle_cal(datetime(2026, 6, 1, 13, 0)) == pytest.approx(T_3H)


def test_pmkt_against_monte_carlo():
    """Verify the reflection formula against simulated GBM paths.

    200k paths, 1-second steps over 3 hours. Discrete-time MC slightly
    UNDER-detects touches vs the continuous formula, so tolerance is one-
    sided-ish: generous absolute band, tight enough to catch sign/reflection
    errors (which produce O(0.1) discrepancies).
    """
    rng = np.random.default_rng(7)
    n_paths, n_steps = 200_000, 3 * 3600
    dt = T_3H / n_steps
    drift = -0.5 * SIGMA * SIGMA * dt
    volstep = SIGMA * math.sqrt(dt)
    for K, side in ((7550.0, "p"), (7540.0, "p"), (7650.0, "c"), (7660.0, "c")):
        b = math.log(K / S)
        x = np.zeros(n_paths)
        alive = np.ones(n_paths, dtype=bool)
        for _ in range(n_steps):
            x[alive] += drift + volstep * rng.standard_normal(int(alive.sum()))
            alive &= (x > b) if side == "p" else (x < b)
        mc = alive.mean()
        analytic = no_touch_prob(K, S, SIGMA, T_3H, side)
        # MC under-detects touches -> mc >= analytic - mc_error
        assert mc == pytest.approx(analytic, abs=0.004), (K, side, mc, analytic)
        assert mc >= analytic - 0.003, (K, side, mc, analytic)
