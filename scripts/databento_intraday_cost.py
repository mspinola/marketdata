"""Price an intraday (ohlcv-1m) databento pull BEFORE spending anything.

Motivated by a slow previous pull. Two things are worth knowing before repeating it:

1. **You do not need to download a sample to learn the cost.** Databento's metadata API
   prices a request exactly, for free: `get_cost` returns dollars and `get_record_count`
   returns rows, both without transferring the data. This script uses those, so the
   "grab a bit and extrapolate" step is unnecessary.

2. **The previous pull was slow because of HOW it fetched, not how much.**
   `providers/databento.py::_fetch` uses `timeseries.get_range`, which streams one symbol
   at a time over HTTP and is the wrong tool for a large historical backfill. Databento's
   batch API (`batch.submit_job` then `batch.download`) prepares files server-side and is
   the supported path for bulk. Cost is the same; wall-clock is not.

Billing note: databento bills on the data it serves for a request, so an `ohlcv-1m` pull is
vastly cheaper than `trades` or `mbo` over the same window. That is what makes this
affordable, and it is why the schema choice matters more than the symbol count.

This script SPENDS NOTHING. It only calls metadata endpoints.

Usage:
    DATABENTO_API_KEY=... python scripts/databento_intraday_cost.py \
        [--start 2022-01-01] [--end 2026-09-01] [--schema ohlcv-1m] [--symbols ES NQ ...]
"""

from __future__ import annotations

import argparse
import os
import sys

DATASET = "GLBX.MDP3"

# The liquid CME subset that carries the labelled discretionary trades. ICE markets
# (cotton, sugar, coffee, cocoa, OJ) are NOT on GLBX and cannot be served by this dataset
# at any price, so they are absent by necessity rather than by choice.
CME_SUBSET = [
    "ES", "NQ", "YM", "RTY",                    # equities
    "6A", "6B", "6C", "6E", "6J", "6S",         # currencies
    "GC", "SI", "HG", "PL", "PA",               # metals
    "CL", "NG", "HO", "RB",                     # energies
    "ZC", "ZW", "ZS", "ZM", "ZL",               # grains
    "ZB", "ZN", "ZF", "ZT",                     # fixed income
    "LE", "HE",                                 # livestock
]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", default="2022-01-01")
    ap.add_argument("--end", default="2026-09-01")
    ap.add_argument("--schema", default="ohlcv-1m")
    ap.add_argument("--symbols", nargs="*", default=None)
    ap.add_argument("--per-symbol", action="store_true",
                    help="also price each symbol alone (more API calls, same zero cost)")
    args = ap.parse_args()

    if not os.environ.get("DATABENTO_API_KEY"):
        print("DATABENTO_API_KEY not set", file=sys.stderr)
        return 2

    import databento as db

    client = db.Historical()
    syms = args.symbols or CME_SUBSET
    # Continuous front-month, the same symbology the provider already stores.
    dbsyms = [f"{s}.n.0" for s in syms]

    print(f"dataset {DATASET}   schema {args.schema}")
    print(f"window  {args.start} to {args.end}")
    print(f"symbols {len(syms)} continuous front-month ({', '.join(syms[:8])}...)\n")

    try:
        rng = client.metadata.get_dataset_range(dataset=DATASET)
        print(f"dataset available range: {rng}\n")
    except Exception as e:  # noqa: BLE001
        print(f"(range lookup failed: {e})\n")

    def price(symbols, label):
        cost = client.metadata.get_cost(
            dataset=DATASET, symbols=symbols, schema=args.schema,
            stype_in="continuous", start=args.start, end=args.end)
        n = client.metadata.get_record_count(
            dataset=DATASET, symbols=symbols, schema=args.schema,
            stype_in="continuous", start=args.start, end=args.end)
        print(f"  {label:<34} ${cost:>10,.2f}   {n:>12,} records")
        return cost, n

    print("COST PREVIEW (no data transferred, nothing billed)")
    total, rows = price(dbsyms, f"whole subset, {args.schema}")

    print("\nSAME WINDOW, OTHER SCHEMAS (why schema choice dominates)")
    for sch in ("ohlcv-1h", "ohlcv-1s", "trades"):
        if sch == args.schema:
            continue
        try:
            c = client.metadata.get_cost(
                dataset=DATASET, symbols=dbsyms, schema=sch,
                stype_in="continuous", start=args.start, end=args.end)
            print(f"  {sch:<34} ${c:>10,.2f}")
        except Exception as e:  # noqa: BLE001
            print(f"  {sch:<34} (failed: {e})")

    if args.per_symbol:
        print("\nPER SYMBOL")
        for s, d in zip(syms, dbsyms):
            try:
                price([d], s)
            except Exception as e:  # noqa: BLE001
                print(f"  {s:<34} (failed: {e})")

    print(f"\nTOTAL for the requested pull: ${total:,.2f} over {rows:,} records")
    print("Nothing has been spent. To proceed, use the batch API rather than "
          "timeseries.get_range, which is what made the previous pull take days.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
