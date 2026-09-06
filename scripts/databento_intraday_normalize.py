"""Normalize the batch-downloaded intraday CSVs into per-symbol parquet.

Stage 2 of the intraday pull, and it costs nothing: local files only, no API calls.
`scripts/databento_intraday_pull.py` leaves the batch job's monthly `.csv.zst` files under
`<raw_root>/intraday/_jobs/<job_id>/`; this splits them by symbol and writes tidy parquet
to `intraday_raw_path(symbol)`.

Two conversions the raw CSV needs:

- **Prices are fixed-point integers** at 1e-9. Databento stores `4771000000000` for 4771.0.
  Left as integers they silently produce nonsense a long way downstream, so they are scaled
  here, once, at the boundary.
- **`ts_event` is UTC nanoseconds.** Stored as a tz-aware UTC index. Consumers doing
  news-failure work will want US/Eastern, since the releases that matter are scheduled in
  ET, but that conversion belongs to the consumer rather than to the store: one canonical
  timezone in, whatever the caller needs out.

Usage:
    python scripts/databento_intraday_normalize.py [--job JOB_ID] [--schema ohlcv-1m]
"""

from __future__ import annotations

import argparse
import glob
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from marketdata.providers.databento import (  # noqa: E402
    INTRADAY_SCHEMA,
    intraday_raw_path,
    raw_root,
)

PRICE_SCALE = 1e-9
PRICE_COLS = ("open", "high", "low", "close")


def load_job(job_dir: Path, schema: str) -> pd.DataFrame:
    files = sorted(glob.glob(str(job_dir / f"**/*.{schema}.csv.zst"), recursive=True))
    if not files:
        files = sorted(glob.glob(str(job_dir / "**/*.csv.zst"), recursive=True))
    if not files:
        raise SystemExit(f"no .csv.zst under {job_dir}")
    print(f"reading {len(files)} monthly files from {job_dir}")
    parts = []
    for i, f in enumerate(files, 1):
        d = pd.read_csv(f, compression="zstd")
        parts.append(d)
        if i % 12 == 0 or i == len(files):
            print(f"  {i}/{len(files)}")
    return pd.concat(parts, ignore_index=True)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--job", default=None, help="job id; default = the only one present")
    ap.add_argument("--schema", default=INTRADAY_SCHEMA)
    args = ap.parse_args()

    jobs_root = raw_root() / "intraday" / "_jobs"
    if args.job:
        job_dir = jobs_root / args.job
    else:
        cands = [p for p in jobs_root.iterdir() if p.is_dir()] if jobs_root.exists() else []
        if len(cands) != 1:
            raise SystemExit(f"pass --job; found {len(cands)} job dirs under {jobs_root}")
        job_dir = cands[0]

    df = load_job(job_dir, args.schema)
    print(f"\nraw rows: {len(df):,}   columns: {list(df.columns)}")

    df["ts_event"] = pd.to_datetime(df["ts_event"], utc=True)
    for c in PRICE_COLS:
        df[c] = df[c].astype("float64") * PRICE_SCALE
    df = df.rename(columns={"ts_event": "ts", "symbol": "raw_symbol"})
    df["symbol"] = df["raw_symbol"].str.split(".").str[0]
    keep = ["ts", "open", "high", "low", "close", "volume", "symbol", "raw_symbol"]
    df = df[keep].sort_values(["symbol", "ts"]).reset_index(drop=True)

    print(f"\n{'symbol':<8}{'rows':>12}{'first':>22}{'last':>22}{'px range':>22}")
    for sym, d in df.groupby("symbol"):
        out = intraday_raw_path(f"{sym}.n.0".split(".")[0] + "", ".n.0", args.schema)
        out.parent.mkdir(parents=True, exist_ok=True)
        d.drop(columns=["symbol"]).to_parquet(out, index=False)
        print(f"{sym:<8}{len(d):>12,}{str(d.ts.min())[:19]:>22}{str(d.ts.max())[:19]:>22}"
              f"{f'{d.low.min():.1f}-{d.high.max():.1f}':>22}")

    print(f"\nwritten under {raw_root() / 'intraday' / args.schema}")
    print("Index is tz-aware UTC. Convert to US/Eastern in the consumer for "
          "release-time work; the store keeps one canonical timezone.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
