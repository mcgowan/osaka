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
