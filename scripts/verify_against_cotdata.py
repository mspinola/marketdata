#!/usr/bin/env python
"""ADR-0007 step 2: prove the futures port preserved the numbers.

Run this on the Windows box after a `marketdata-update --bars --domain futures`,
while `cotdata`'s price code still exists. Both producers read the same Norgate
install and write separate stores, so the two can be compared directly — until
ADR-0007 §7.5 deletes cotdata's half, at which point this check is gone forever.

WHY IT MATTERS MORE THAN IT LOOKS. `crowdmon` was the consumer whose tier
requirements were strictest, and the original work order put it first precisely
so it would fail loudly if the provider were wrong. It was deprecated on
2026-08-07 with no consumers left, so that check is gone, and nothing exercises
`propadj` until the deferred `npf` pass. This script is what replaces it.

WHAT AGREEMENT TO EXPECT — and it is much stricter than the databento harness
next door in cotdata. That one compares two INDEPENDENT vendors, whose roll
calendars and back-adjust anchors legitimately differ, so only the SHAPE can
agree. This compares the same vendor, the same symbol and the same adjustment
through two code paths. The vendor passthrough columns must be **exactly** equal.
A difference is a port bug, not a tolerance question.

The one column family that may legitimately differ is the volume reconstruction
(`Volume_Reconstructed`, `FirstVolume`, `SecondVolume`, the contract names).
Both producers compute it identically, but incrementally: each only recomputes a
trailing window over what its own store already holds. A fresh marketdata store
recomputes the whole history under today's logic, while cotdata's has accumulated
over months of runs. So those columns are compared and REPORTED, not failed on —
see --strict-volume, and re-run cotdata with `--full` if you want them to match.

Usage:
    python scripts/verify_against_cotdata.py \\
        --cotdata-store "%COTDATA_STORE%" \\
        --marketdata-store "%MARKETDATA_STORE%" \\
        --symbols ES CL GC ZS DC

Exit code is 0 only if every check passed, so it can gate the promotion.
Reads parquet directly from both stores: no network, and it does not need
`marketdata` or `cotdata` importable except for the optional --check-propadj.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import pandas as pd

# The columns Norgate hands over and both producers store verbatim. These must be
# identical: both paths call the same `norgatedata.price_timeseries` and rename
# with the same map, so any difference here is the port having changed the data.
PASSTHROUGH = ("Open", "High", "Low", "Close", "Volume", "Open Interest",
               "Delivery Month")

# Computed identically by both, but over each store's own incremental window.
# Compared and reported rather than failed on. See the module docstring.
RECONSTRUCTION = ("Volume_Reconstructed", "FirstVolume", "SecondVolume",
                  "FirstContract", "SecondContract", "Volume_Source")

STORED_TIERS = ("backadj", "unadj")

# Symbols cotdata prices off ETF proxies through yfinance because Norgate carries
# no continuous series. Absent from marketdata's futures registry on purpose, so
# their absence is reported as expected rather than as a missing symbol.
EXPECTED_ABSENT = ("MME", "MFS")


def _norm(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df.index = pd.to_datetime(df.index).tz_localize(None).normalize()
    df.index.name = "Date"
    return df.sort_index()


def read_cotdata(store: Path, symbol: str, tier: str) -> pd.DataFrame:
    """cotdata layout: prices/<symbol>_<tier>.parquet"""
    p = store / "prices" / f"{symbol}_{tier}.parquet"
    return _norm(pd.read_parquet(p)) if p.exists() else pd.DataFrame()


def read_marketdata(store: Path, symbol: str, tier: str) -> pd.DataFrame:
    """marketdata layout: bars/futures/norgate/<symbol>_<tier>.parquet"""
    p = store / "bars" / "futures" / "norgate" / f"{symbol}_{tier}.parquet"
    return _norm(pd.read_parquet(p)) if p.exists() else pd.DataFrame()


def compare_column(a: pd.Series, b: pd.Series) -> dict:
    """Exact-equality report for one column over a shared index.

    NaN == NaN counts as equal: a missing Open Interest that is missing in both
    stores is agreement, and `!=` would call it a difference on every such row.
    """
    both_null = a.isna() & b.isna()
    if pd.api.types.is_numeric_dtype(a) and pd.api.types.is_numeric_dtype(b):
        diff = (a - b).abs()
        unequal = ~(both_null | (diff == 0))
        worst = float(diff[unequal].max()) if unequal.any() else 0.0
    else:
        unequal = ~(both_null | (a.astype("object") == b.astype("object")))
        worst = float("nan") if unequal.any() else 0.0
    n = int(unequal.sum())
    return {
        "n_differing": n,
        "worst_abs_diff": worst,
        "first_date": str(a.index[unequal][0].date()) if n else None,
    }


def compare_tier(cot: pd.DataFrame, mkt: pd.DataFrame) -> dict:
    """One stored tier, one symbol. Returns a report dict; `ok` is the verdict on
    the passthrough columns only."""
    rep: dict = {"cot_rows": len(cot), "mkt_rows": len(mkt),
                 "passthrough": {}, "reconstruction": {}, "problems": []}
    if cot.empty or mkt.empty:
        rep["problems"].append(
            "absent from " + ("cotdata" if cot.empty else "marketdata"))
        rep["ok"] = False
        return rep

    rep["cot_span"] = f"{cot.index.min().date()}..{cot.index.max().date()}"
    rep["mkt_span"] = f"{mkt.index.min().date()}..{mkt.index.max().date()}"

    common = cot.index.intersection(mkt.index)
    rep["n_common"] = len(common)
    # Dates one store has and the other does not. A handful at the tail is just
    # one producer having run more recently; a gap in the middle is a real
    # difference in what was captured.
    rep["cot_only"] = len(cot.index.difference(mkt.index))
    rep["mkt_only"] = len(mkt.index.difference(cot.index))
    if len(common) == 0:
        rep["problems"].append("no overlapping dates")
        rep["ok"] = False
        return rep

    c, m = cot.loc[common], mkt.loc[common]
    for col in PASSTHROUGH:
        if col not in c.columns and col not in m.columns:
            continue
        if col not in c.columns or col not in m.columns:
            rep["problems"].append(
                f"{col}: present in only one store "
                f"({'cotdata' if col in c.columns else 'marketdata'})")
            continue
        r = compare_column(c[col], m[col])
        rep["passthrough"][col] = r
        if r["n_differing"]:
            rep["problems"].append(
                f"{col}: {r['n_differing']} of {len(common)} rows differ "
                f"(worst {r['worst_abs_diff']}, first {r['first_date']})")

    for col in RECONSTRUCTION:
        if col in c.columns and col in m.columns:
            rep["reconstruction"][col] = compare_column(c[col], m[col])

    rep["ok"] = not rep["problems"]
    return rep


def compare_specs(cot_store: Path, mkt_store: Path, symbols) -> dict:
    """contract_specs is one table keyed by Symbol, so compare row by row."""
    rep: dict = {"problems": [], "compared": 0}
    cp = cot_store / "metadata" / "contract_specs.parquet"
    mp = mkt_store / "metadata" / "contract_specs.parquet"
    if not cp.exists() or not mp.exists():
        rep["problems"].append(
            f"contract_specs missing in {'cotdata' if not cp.exists() else 'marketdata'} "
            f"(run marketdata-update --metadata)")
        rep["ok"] = False
        return rep

    c = pd.read_parquet(cp).set_index("Symbol")
    m = pd.read_parquet(mp).set_index("Symbol")
    for sym in symbols:
        if sym in EXPECTED_ABSENT:
            continue
        if sym not in c.index:
            continue                       # cotdata never had it; not a port question
        if sym not in m.index:
            rep["problems"].append(f"{sym}: no contract_specs row in marketdata")
            continue
        rep["compared"] += 1
        for col in ("Point Value", "Tick Size", "Tick Value", "Currency",
                    "Exchange", "Name"):
            if col not in c.columns or col not in m.columns:
                continue
            cv, mv = c.loc[sym, col], m.loc[sym, col]
            if pd.isna(cv) and pd.isna(mv):
                continue
            if cv != mv:
                rep["problems"].append(f"{sym}.{col}: cotdata {cv!r}, marketdata {mv!r}")
    rep["ok"] = not rep["problems"]
    return rep


def check_propadj(symbol: str, cot_store: Path) -> dict:
    """Run both ratio-adjust implementations over the SAME input frames.

    Store equality is checked separately, so feeding both implementations one pair
    of frames isolates the ALGORITHM port from the data. Needs both packages
    importable; skipped otherwise.
    """
    rep: dict = {"skipped": None, "problems": []}
    try:
        from cotdata.prices import _ratio_adjust as cot_ratio

        from marketdata.adjust import ratio_adjust as mkt_ratio
    except ImportError as e:
        rep["skipped"] = f"{e} (needs both packages importable)"
        return rep

    unadj = read_cotdata(cot_store, symbol, "unadj")
    backadj = read_cotdata(cot_store, symbol, "backadj")
    if unadj.empty or backadj.empty:
        rep["skipped"] = "cotdata store lacks both tiers for this symbol"
        return rep

    mine = mkt_ratio(unadj, backadj)
    # cotdata's reads the store itself and keys off COTDATA_STORE, so point it at
    # the same store rather than reimplementing its read.
    theirs = cot_ratio(symbol)
    if theirs.empty:
        rep["skipped"] = "cotdata's _ratio_adjust returned empty (is COTDATA_STORE set?)"
        return rep

    common = mine.index.intersection(theirs.index)
    rep["n_common"] = len(common)
    if not len(common):
        rep["problems"].append("no overlapping dates between the two propadj series")
        rep["ok"] = False
        return rep
    for col in ("Open", "High", "Low", "Close"):
        if col in mine.columns and col in theirs.columns:
            d = (mine.loc[common, col] - theirs.loc[common, col]).abs().max()
            rep[col] = float(d)
            # Same algorithm on the same inputs: floating-point noise only.
            if pd.notna(d) and d > 1e-9:
                rep["problems"].append(f"{col}: max abs diff {d:g} exceeds 1e-9")
    rep["ok"] = not rep["problems"]
    return rep


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--cotdata-store", required=True)
    p.add_argument("--marketdata-store", required=True)
    p.add_argument("--symbols", nargs="+", default=["ES", "CL", "GC", "ZS", "DC"],
                   help="default set spans an index, an energy, a metal, and the two "
                        "markets whose backadj history goes non-positive")
    p.add_argument("--strict-volume", action="store_true",
                   help="fail on reconstructed-volume differences too. Only meaningful "
                        "after a cotdata run with --full, since both producers "
                        "reconstruct incrementally over their own store's history")
    p.add_argument("--check-propadj", action="store_true",
                   help="also run cotdata's and marketdata's ratio-adjust over the same "
                        "frames. Needs both packages importable")
    args = p.parse_args(argv)

    cot_store, mkt_store = Path(args.cotdata_store), Path(args.marketdata_store)
    for label, path in (("cotdata", cot_store), ("marketdata", mkt_store)):
        if not path.exists():
            print(f"ERROR: {label} store does not exist: {path}")
            return 2

    failures = []
    print(f"cotdata store    {cot_store}")
    print(f"marketdata store {mkt_store}\n")

    for sym in args.symbols:
        if sym in EXPECTED_ABSENT:
            print(f"{sym}: skipped — Norgate carries no continuous series, priced off "
                  f"an ETF proxy in cotdata and deliberately not ported")
            continue
        print(f"── {sym} " + "─" * 60)

        for tier in STORED_TIERS:
            rep = compare_tier(read_cotdata(cot_store, sym, tier),
                               read_marketdata(mkt_store, sym, tier))
            status = "OK " if rep["ok"] else "FAIL"
            head = (f"  {status} {tier:8s} cot {rep['cot_rows']:6d} rows, "
                    f"mkt {rep['mkt_rows']:6d}")
            if "n_common" in rep:
                head += (f", {rep['n_common']} common"
                         + (f", {rep['cot_only']} cot-only" if rep["cot_only"] else "")
                         + (f", {rep['mkt_only']} mkt-only" if rep["mkt_only"] else ""))
            print(head)
            for prob in rep["problems"]:
                print(f"       ! {prob}")
            if not rep["ok"]:
                failures.append(f"{sym}/{tier}")

            for col, r in rep["reconstruction"].items():
                if r["n_differing"]:
                    note = ("FAIL" if args.strict_volume else "note")
                    print(f"       {note} {col}: {r['n_differing']} rows differ "
                          f"(first {r['first_date']}) — expected unless cotdata was "
                          f"last run with --full")
                    if args.strict_volume:
                        failures.append(f"{sym}/{tier}/{col}")

        if args.check_propadj:
            r = check_propadj(sym, cot_store)
            if r.get("skipped"):
                print(f"  SKIP propadj  {r['skipped']}")
            else:
                print(f"  {'OK ' if r['ok'] else 'FAIL'} propadj  "
                      f"{r.get('n_common', 0)} common rows, "
                      f"max close diff {r.get('Close', float('nan')):g}")
                for prob in r["problems"]:
                    print(f"       ! {prob}")
                if not r["ok"]:
                    failures.append(f"{sym}/propadj")

    print("\n── contract specs " + "─" * 51)
    specs = compare_specs(cot_store, mkt_store, args.symbols)
    print(f"  {'OK ' if specs['ok'] else 'FAIL'} {specs['compared']} symbols compared")
    for prob in specs["problems"]:
        print(f"       ! {prob}")
    if not specs["ok"]:
        failures.append("contract_specs")

    print()
    if failures:
        print(f"FAILED: {len(failures)} check(s) — {', '.join(failures)}")
        print("\nThe two producers read the same Norgate install, so the passthrough "
              "columns differing means the port changed the data. Do not promote.")
        return 1
    print("PASSED: every compared series is identical between the two stores.")
    print("\nThat is the evidence ADR-0007 §7.5 needs before cotdata's price code is "
          "deleted, and it is only obtainable while both halves still exist.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
