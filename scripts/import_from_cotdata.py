#!/usr/bin/env python
"""ADR-0007 step 4 follow-on: move the futures bars a repointed consumer expects.

WHY THIS EXISTS. ADR-0007 is shipping as separate steps and two of them are out of
step with each other. Step 4 repointed the consumers: `cotmetrics` reads
`marketdata.get_bars(symbol, "backadj")` and no longer calls `cotdata.get_prices`
anywhere. Step 2, which moves the producer code and the bars themselves, is on
ice. So the read points at `$MARKETDATA_STORE/bars/futures/` while the bars are
still in `$COTDATA_STORE/prices/`, where cotdata's Windows producer keeps writing
them.

**That gap fails silently, which is why it needs a tool rather than a note.** A
futures read against a store holding no `bars/futures/` does not raise. It returns
an empty frame, so an app boots, renders every COT page off the other store, and
shows blank price overlays that read as a UI regression.

WHAT THIS IS NOT. It is not a producer, and it is not the port. It copies bytes
between two stores; it never touches Norgate or the network, so it runs anywhere,
which is the point (the real producer is Windows-only). `scripts/verify_against_cotdata.py`
remains the thing that proves the PORT preserved the numbers, and that comparison
needs the Windows box and cotdata's price code still alive. This script assumes
that verification and moves what it verified.

WHAT IT COPIES, AND WHY VERBATIM. Both stores hold the same frame: cotdata writes
the seven Norgate passthrough columns plus the six volume-reconstruction columns,
and marketdata's norgate provider writes exactly those. So a row is carried across
unmodified rather than reconstructed, and `--verify` re-reads both sides and
compares every cell. Recomputing the reconstruction here would be worse than
useless: it is the one column family the two producers legitimately disagree on,
because each recomputes over a trailing window of its own store's history.

SAFETY. Refuses to overwrite an existing series unless `--force`, because a store
that already holds futures may hold a fresher or differently-reconstructed copy,
and this script has no way to tell which is better. Writes through
`store.write_bars`, so the manifest entry is derived by the same code the producer
uses rather than hand-built. Entries for other domains are untouched: a pinned
snapshot over the equities half (npf's `month_end_snapshot.json`) stays green, and
`--verify-pin` is the way to confirm that rather than trust it.

Usage:
    python scripts/import_from_cotdata.py --dry-run
    python scripts/import_from_cotdata.py --verify
    python scripts/import_from_cotdata.py --symbols ES GC --force

Exit code is 0 only if every requested series landed and verified.
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

import pandas as pd

# Set by cotdata's producer and by marketdata's, and identical by construction:
# both rename Norgate's frame with the same map, which is what
# verify_against_cotdata.py exists to prove. Listed here so an unexpected column
# is reported rather than carried across unnoticed.
PASSTHROUGH = ("Open", "High", "Low", "Close", "Volume", "Open Interest",
               "Delivery Month")
RECONSTRUCTION = ("Volume_Reconstructed", "FirstVolume", "SecondVolume",
                  "FirstContract", "SecondContract", "Volume_Source")

DOMAIN = "futures"
SOURCE = "norgate"


def _cotdata_prices_dir(explicit: str | None) -> Path:
    root = explicit or os.environ.get("COTDATA_STORE", "").strip()
    if not root:
        raise SystemExit(
            "COTDATA_STORE is not set and --cotdata-store was not passed. "
            "Point it at the store holding prices/.")
    d = Path(root).expanduser() / "prices"
    if not d.is_dir():
        raise SystemExit(f"no prices/ directory under {root}")
    return d


def _available(prices: Path) -> dict:
    """``{symbol: {tier: path}}`` for every stored tier present.

    Globs the two tier suffixes rather than listing the directory, so a leftover
    ``*.tmp`` from an interrupted atomic write is skipped instead of being read as
    a symbol named after the temp file.
    """
    found: dict = {}
    for tier in ("backadj", "unadj"):
        for p in sorted(prices.glob(f"*_{tier}.parquet")):
            found.setdefault(p.name[: -len(f"_{tier}.parquet")], {})[tier] = p
    return found


def _load(path: Path) -> pd.DataFrame:
    df = pd.read_parquet(path)
    df.index = pd.to_datetime(df.index).tz_localize(None).normalize()
    df.index.name = "Date"
    return df.sort_index()


def _frames_equal(a: pd.DataFrame, b: pd.DataFrame) -> str | None:
    """None when identical, else the first difference in words."""
    if len(a) != len(b):
        return f"row count {len(a)} != {len(b)}"
    if list(a.columns) != list(b.columns):
        return f"columns {list(a.columns)} != {list(b.columns)}"
    if not a.index.equals(b.index):
        return "index differs"
    for col in a.columns:
        # NaN == NaN is False, so compare through a null-aware mask rather than
        # ==; the reconstruction columns are full of legitimate NaN.
        left, right = a[col], b[col]
        both_null = left.isna() & right.isna()
        if not bool(((left == right) | both_null).all()):
            n = int((~((left == right) | both_null)).sum())
            return f"column {col!r} differs on {n} row(s)"
    return None


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="import_from_cotdata", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--cotdata-store", metavar="PATH",
                    help="source store. Default: $COTDATA_STORE")
    ap.add_argument("--symbols", nargs="+", metavar="SYM",
                    help="import only these. Default: every symbol present in "
                         "BOTH the source store and marketdata's futures registry")
    ap.add_argument("--dry-run", action="store_true",
                    help="report what would be written, write nothing")
    ap.add_argument("--force", action="store_true",
                    help="overwrite a series the marketdata store already holds")
    ap.add_argument("--verify", action="store_true",
                    help="after writing, re-read both stores and compare every cell")
    args = ap.parse_args(argv)

    # Imported here, not at module scope, so --help works without MARKETDATA_STORE.
    from marketdata import config, registry, store

    prices = _cotdata_prices_dir(args.cotdata_store)
    dest = config.store_root()
    print(f"source: {prices}")
    print(f"dest:   {dest}")

    available = _available(prices)
    known = {s.internal for s in registry.all_symbols()
             if registry.domain_for(s.internal) == DOMAIN}

    if args.symbols:
        wanted = list(dict.fromkeys(args.symbols))
        missing = [s for s in wanted if s not in available]
        if missing:
            print(f"\nnot in the source store: {' '.join(missing)}", file=sys.stderr)
            return 1
    else:
        wanted = sorted(available)

    # A symbol the registry does not carry is reported, never guessed at. The
    # store is registry-free and would happily hold it, but the registry is what
    # the producer will later refresh, so an unlisted symbol would be imported
    # once and then never updated again.
    unlisted = [s for s in wanted if s not in known]
    if unlisted:
        print(f"\nskipping {len(unlisted)} symbol(s) absent from marketdata's "
              f"futures registry: {' '.join(unlisted)}")
        wanted = [s for s in wanted if s in known]

    tiers = tuple(t for t in ("backadj", "unadj"))
    written, skipped, failed, verified = [], [], [], []

    for sym in wanted:
        have = available[sym]
        # Both tiers or neither. `propadj` is derived from the pair, so a
        # half-imported symbol reads fine on `backadj` and raises, or worse
        # silently mis-scales, the moment anything asks for a return series.
        absent = [t for t in tiers if t not in have]
        if absent:
            print(f"  {sym}: source has no {'/'.join(absent)} tier, skipping symbol")
            skipped.append(sym)
            continue
        exists = [t for t in tiers if store.has_bars(sym, DOMAIN, SOURCE, t)]
        if exists and not args.force:
            print(f"  {sym}: already in the marketdata store "
                  f"({'/'.join(exists)}); pass --force to overwrite")
            skipped.append(sym)
            continue

        for tier in tiers:
            df = _load(have[tier])
            extra = [c for c in df.columns
                     if c not in PASSTHROUGH + RECONSTRUCTION]
            if extra:
                print(f"  {sym}_{tier}: unexpected column(s) {extra}, carried across")
            if args.dry_run:
                print(f"  would write {sym}_{tier}: {len(df)} rows, "
                      f"{df.index.min().date()} to {df.index.max().date()}")
                continue
            store.write_bars(sym, df, DOMAIN, SOURCE, tier)
            print(f"  wrote {sym}_{tier}: {len(df)} rows, "
                  f"{df.index.min().date()} to {df.index.max().date()}")

            if args.verify:
                back = store.read_bars(sym, DOMAIN, SOURCE, tier)
                why = _frames_equal(_load(have[tier]), _load_frame(back))
                if why:
                    print(f"  VERIFY FAILED {sym}_{tier}: {why}", file=sys.stderr)
                    failed.append(f"{sym}_{tier}")
                else:
                    verified.append(f"{sym}_{tier}")
        written.append(sym)

    print(f"\n{'would import' if args.dry_run else 'imported'}: {len(written)} symbol(s)"
          f"{f', {len(wanted)} requested' if len(written) != len(wanted) else ''}")
    if skipped:
        print(f"skipped: {len(skipped)} ({' '.join(skipped)})")
    if args.verify and not args.dry_run:
        print(f"verified identical: {len(verified)} series")
    if failed:
        print(f"VERIFY FAILED: {len(failed)} series ({' '.join(failed)})", file=sys.stderr)
        return 1
    return 0


def _load_frame(df: pd.DataFrame) -> pd.DataFrame:
    """`_load` for a frame already in memory, so both sides of a verify go
    through the same index normalisation."""
    df = df.copy()
    df.index = pd.to_datetime(df.index).tz_localize(None).normalize()
    df.index.name = "Date"
    return df.sort_index()


if __name__ == "__main__":
    raise SystemExit(main())
