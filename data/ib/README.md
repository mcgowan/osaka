# `data/ib/` — Interactive Brokers raw 1-min and daily index history

Files in this directory are produced by `data/ib_download.py`. Format is
`YYYY-MM-DD HH:MM:SS,o,h,l,c`, timestamps in US/Eastern. Rows within a chunk
are chronological; chunks are appended in newest-first order, so the file as
a whole is not globally sorted until the Phase 0 loader processes it.

The Phase 0 loader is the one and only consumer of these files. It sorts,
dedupes, filters to RTH 09:30–16:00 ET, and writes the canonical parquet.
Nothing else in the pipeline should read these CSVs directly.

## Known IB-side data holes

These are confirmed empty (or partial) on IB's HMDS — not bugs in the
downloader. The loader must tolerate them; treat the dates as missing data,
not as zero values or interpolation targets.

| Symbol | Range | What IB has | Why it matters |
| --- | --- | --- | --- |
| VIX | 2006-05-01 → 2006-05-19 | zero bars for 15 consecutive weekdays | Walking-back queries return three consecutive `error 162` ("HMDS query returned no data"). Adjacent days (Apr 28, May 22) are clean full RTH days. |
| VIX | 2011-05-27 | only 29 bars, 15:31–15:59 ET (no morning session) | Refetched independently and IB returns the same 29 bars. Listed in `KNOWN_IB_HOLES` in `data/ib_download.py` so `--fill-gaps` doesn't loop forever trying to refill it. |
| SPX | 2011-05-27 | only 28 bars, afternoon only | Same IB-side hole as VIX that day. In `KNOWN_IB_HOLES`. |
| SPX | 2004-04-09 | 1 junk bar on Good Friday (market closed) | In `KNOWN_IB_HOLES` (trips the <200-bar gap heuristic). Loader drops via holiday calendar. |
| SPX | 2004-05-31 | 373 stale flat prints (1120.29 all day) on Memorial Day (market closed) | Does **not** trip the gap heuristic (>200 bars). Loader must drop via holiday calendar — a "flat all day" sanity check is a good belt-and-braces. |
| SPX | 2020-12-07 | 374 bars (16 minutes missing) | Isolated; accepted as-is. |

Additional conventions confirmed by the Gate 0 audit (`data/qa_ib.py`):

- **Vol indices print from 09:31**, not 09:30 — 389 bars is a *normal* full day
  for VIX/VIX1D (and 209 a normal half day). Not gaps.
- **Half days carry a 13:00–13:15 tail** in some eras (225-bar SPX days,
  224-bar VIX days). The loader truncates half days at 13:00 ET.
- **2004–2007 SPX has ~40 days with a handful of missing minutes**
  (377–389 bars). Accepted: this era is outside the v1 training universe and
  only feeds long-history regime features, which tolerate missing minutes.
- **VIX has ~20 deeply partial days** (238–378 bars, mostly 2005–2011 plus
  COVID March 2020). The canonical enumerated list lives in `KNOWN_HOLES`
  in `data/qa_ib.py`; treat that as the source of truth rather than this
  table for per-day detail.
- The v1 training era (2023-04 →) is clean: zero anomalies in all three
  symbols, zero duplicate timestamps, zero OHLC violations.
- IB 2026-06-01 SPX bars cross-checked against the trader's own recording:
  390/390 bars aligned (ET = PT+3h), 381 exact, max close diff 1.63 pts
  (live-snapshot vs consolidated-history revisions; benign).

The downloader's per-day-bar-count heuristic (`GAP_THRESHOLD_BARS = 200`)
also won't be tripped by **zero-bar** holiday/closed days — only by *partial*
days (1..199 bars). Holidays and the 2006-05 gap are zero-bar and pass through
the gap detector untouched, as intended.

## Note on VIX extended-hours bars

For the VIX index (not VIX1D), recent dates (~2018+) include bars from
03:15–09:30 ET and 16:00–16:59 ET in addition to the 09:31–15:59 RTH session.
This is CBOE's Global Trading Hours session for VIX. The downloader requests
`useRTH=True`; for VIX, IB nonetheless returns the extended session. These
are **not gaps** — they are extra bars that the loader will drop when
filtering to the project's 09:30–16:00 ET RTH window.

## Operational notes

- **Resume.** Re-running `--symbol X` walks backward from the file's
  earliest timestamp. It does **not** fill holes in the middle of the file.
  Use `--symbol X --fill-gaps` for that.
- **SIGINT during a download is safe.** The downloader installs a deferred-
  stop handler: the first Ctrl-C lets the in-flight chunk's write complete,
  then exits cleanly. A second Ctrl-C force-quits. This was added after a
  partial 2011-05-27 chunk turned into a permanent hole that the resume
  logic could not detect.
- **Adding to `KNOWN_IB_HOLES`.** If a future `--fill-gaps` run reports a
  day that IB confirms is empty/partial, add it to the dict in
  `data/ib_download.py` and document the evidence in the table above.
