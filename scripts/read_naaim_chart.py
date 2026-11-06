"""Read NAAIM Exposure readings off a StockCharts weekly chart image, by bar height.

The reproducer for NAAIM_EXPOSURE_PROVISIONAL's first backfill (2026-11-06): the
weeks between the official series' end (2026-08-05) and the chart's last bar.
The image is the operator's own screenshot of a StockCharts chart with an
`!NAAIM` bar panel under $SPX; it is not committed (NAAIM's data), so neither
is any output.

How it reads. Bars are filled blue columns with dark outlines, and adjacent bars
touch, so a bar is found by its fill-coloured interior along one row near the
panel floor (``--row``); one-pixel interiors are antialiasing slivers and are
dropped. A bar's height is the median topmost bar pixel (fill or outline) over
its interior columns, mapped to a value by a straight line through the panel's
horizontal grid rows (``--grid``, value:pixel-row pairs read off the image).
Bars are dated one week apart back from ``--last-week``, which assumes the chart
has no missing week in its span; ``--check`` tests that against the store.

What it measured on the 2026-11-06 image (2000 x 1022, grid
110:745.5,90:791.5,80:814.5,70:837, row 972): 262 bars, 249 of them on weeks the
official series holds. Against those: median absolute error 0.19 points, 90th
percentile 0.51, last bar 78.38 against the chart's own label 78.62. Three weeks
miss by more, none of them a reading error: 2021-11-10 sits under the chart's
legend text, and on 2022-05-04 (about 57 against 31.76) and 2024-11-27 (the
previous week's value against 98.93) StockCharts draws a different number from
NAAIM's. So a provisional week carries the source's error as well as the
reading's, and the chart's own label beats a bar read wherever there is one.

Usage (from the repo root; --check reads MARKETDATA_STORE):

    python scripts/read_naaim_chart.py IMAGE --last-week 2026-11-04 \\
        --grid 110:745.5,90:791.5,80:814.5,70:837 --row 972 --check

Prints the readings for the weeks the official series lacks as provisional.csv
rows (week,value,source), ready to paste. Needs Pillow.
"""
from __future__ import annotations

import argparse
import sys

import numpy as np
import pandas as pd

TOP, FLOOR = 742, 980          # the NAAIM panel's rows on the 2026-11-06 image


def read_bars(path, grid, row, top=TOP, floor=FLOOR, right=1925):
    """Bars left to right as a frame of x0, x1, top pixel and value."""
    from PIL import Image
    a = np.array(Image.open(path).convert("RGB")).astype(int)
    r, b = a[..., 0], a[..., 2]
    bar = (b - r > 60) & (b > 80)                 # fill or outline
    fill = bar & (r >= 55) & (r <= 110)           # fill only, about (86, 76, 176)
    runs, start = [], None
    for x, on in enumerate(fill[row, :right]):
        if on and start is None:
            start = x
        if not on and start is not None:
            runs.append((start, x - 1))
            start = None
    slope, icpt = np.polyfit([p for _, p in grid], [v for v, _ in grid], 1)
    rows = []
    for x0, x1 in runs:
        if x1 == x0:
            continue                               # antialiasing sliver, not a bar
        tops = [top + int(np.argmax(bar[top:floor, c])) for c in range(x0, x1 + 1)]
        px = float(np.median(tops))
        rows.append((x0, x1, px, icpt + slope * px))
    return pd.DataFrame(rows, columns=["x0", "x1", "top", "value"])


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument("image")
    p.add_argument("--last-week", required=True, help="date of the chart's last bar")
    p.add_argument("--grid", required=True, help="value:row pairs, comma separated")
    p.add_argument("--row", type=int, required=True, help="pixel row to find bars on")
    p.add_argument("--check", action="store_true",
                   help="compare with NAAIM_EXPOSURE on shared weeks")
    args = p.parse_args(argv)
    grid = [tuple(float(x) for x in pair.split(":")) for pair in args.grid.split(",")]
    df = read_bars(args.image, grid, args.row)
    end = pd.Timestamp(args.last_week)
    df.index = pd.DatetimeIndex([end - pd.Timedelta(weeks=len(df) - 1 - i)
                                 for i in range(len(df))])
    print(f"{len(df)} bars, {df.index[0].date()} to {df.index[-1].date()}", file=sys.stderr)
    new = df["value"]
    if args.check:
        import marketdata
        off = marketdata.get_bars("NAAIM_EXPOSURE")["Close"]
        off.index = pd.DatetimeIndex(off.index)
        shared = df["value"].reindex(off.index).dropna()
        d = shared - off.reindex(shared.index)
        print(f"shared weeks {len(d)}: median abs {d.abs().median():.2f}, 90th pct "
              f"{d.abs().quantile(0.9):.2f}, max abs {d.abs().max():.2f} "
              f"({d.abs().idxmax().date()})", file=sys.stderr)
        new = df["value"][df.index > off.index.max()]
    print("week,value,source")
    for week, value in new.items():
        print(f"{week.date()},{value:.2f},stockcharts chart bar read")


if __name__ == "__main__":
    main()
