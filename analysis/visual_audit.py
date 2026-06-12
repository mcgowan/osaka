#!/usr/bin/env python3
"""Task 2.5: visual audit plots for trader sign-off.

15 sampled days (seeded; stratified: 5 per VIX1D tercile). Each plot: the
day's 1-min price path, the grid strikes for three entry times (10:00,
12:00, 14:00 ET) drawn from entry to settlement - green if the label says
survived, red if touched, with a marker at the labeled touch minute.

Run:  .venv/bin/python analysis/visual_audit.py
Output: analysis/plots/audit-<day>.png x 15
"""

import os
import random
import sys

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from data.labels import load_labels  # noqa: E402
from data.loader import load_bars  # noqa: E402

OUT_DIR = os.path.join(os.path.dirname(__file__), "plots")
ENTRY_MINUTES = (30, 150, 270)  # 10:00, 12:00, 14:00 ET
SEED = 17


def main():
    os.makedirs(OUT_DIR, exist_ok=True)
    from quant.conventions import REGIME_TERCILES
    t1, t2 = REGIME_TERCILES
    labels = load_labels(stride=5)  # default load = pre-TEST_START only
    labels["vix"] = labels["sigma_anchor"] * 100
    days = labels.groupby("day")["vix"].first()
    rng = random.Random(SEED)
    picked = []
    for lo, hi in ((0, t1), (t1, t2), (t2, 999)):
        pool = sorted(days[(days >= lo) & (days < hi)].index)
        picked += rng.sample(pool, 5)

    spx = load_bars("SPX", start="2023-04-01")
    spx["day"] = spx["ts"].dt.strftime("%Y-%m-%d")
    by_day = dict(tuple(spx.groupby("day")))

    for day in sorted(picked):
        bars = by_day[day].reset_index(drop=True)
        sel = labels[(labels["day"] == day)
                     & (labels["minute"].isin(ENTRY_MINUTES))]
        fig, ax = plt.subplots(figsize=(14, 8))
        ax.plot(range(len(bars)), bars["close"], lw=0.8, color="black",
                label="SPX 1-min close")
        for r in sel.itertuples():
            color = "green" if r.survive else "red"
            ax.hlines(r.strike, r.minute, len(bars) - 1, colors=color,
                      lw=0.9, alpha=0.55,
                      linestyles="solid" if r.survive else "dashed")
            ax.plot([r.minute], [r.strike], marker="|", ms=8, color=color)
            if not r.survive and r.touch_min >= 0:
                ax.plot([r.touch_min], [r.strike], marker="x", ms=8,
                        color="red")
        ax.set_title(f"{day}  (VIX1D anchor {sel['vix'].iloc[0]:.1f})  -  "
                     f"grid strikes at 10:00/12:00/14:00 ET; "
                     f"green=survived, red dashed=touched (x at touch)")
        ax.set_xlabel("minutes since 09:30 ET")
        ax.set_ylabel("SPX")
        fig.tight_layout()
        path = os.path.join(OUT_DIR, f"audit-{day}.png")
        fig.savefig(path, dpi=110)
        plt.close(fig)
        print(f"  {path}")


if __name__ == "__main__":
    main()
