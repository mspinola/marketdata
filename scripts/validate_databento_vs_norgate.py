#!/usr/bin/env python
"""ADR-0006 item 6: validate databento back-adjustment against Norgate.

The databento and Norgate `backadj` series are built independently (single provider
per symbol, no stitching), so their absolute LEVELS may differ: different roll
calendars pick different roll dates, and the additive back-adjust anchor floats to
each provider's most-recent price. What must agree is the SHAPE.

Additive (Panama) back-adjustment preserves absolute daily price CHANGES, not percent
returns — a floating anchor changes the price level and hence the return denominator,
but `Close.diff()` on any segment equals the true settlement change either way. So the
core check is: daily changes match between the two series, except near roll dates the
two providers place differently. This harness quantifies that.

It reads `backadj` for a few liquid symbols from two stores — one built by Norgate,
one built by databento (`marketdata-update --build-databento`) — and reports, per symbol:
  * overlap (date spans, common days),
  * change correlation, scale ratio, and normalized worst-day change diff (the shape
    check, unitless so one tolerance works across symbols),
  * level difference stats (expected non-zero; informational),
  * roll-date agreement (from Delivery Month changes).

It exits non-zero if any symbol falls outside tolerance, so it can gate a promotion.
It needs real data, so it is NOT run in CI — the comparison logic is unit tested in
tests/test_validate_databento.py against synthetic frames.

**One store, two vendors, since databento was ported here.** ADR-0007 makes the vendor
a PATH component (`bars/futures/norgate/` beside `bars/futures/databento/`), so both
series normally sit in the same `$MARKETDATA_STORE` and the store paths default to it —
this is the comparison the layout was designed to make possible. The two `--*-store`
flags remain for the case they were written for: two stores produced on different
machines and compared without syncing them together.

Usage:
    python scripts/validate_databento_vs_norgate.py --symbols ES CL GC
    # or, across two separately-produced stores:
    python scripts/validate_databento_vs_norgate.py \
        --norgate-store /path/to/store_a --databento-store /path/to/store_b
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

import pandas as pd

_DEFAULT_SYMBOLS = ["ES", "CL", "GC"]
# Defaults are deliberately lenient — tighten once you have seen a clean run. A few
# days a year land on a mismatched roll and legitimately differ, so judge on the
# correlation and the bulk of days, not a single worst day.
_DEFAULT_CHANGE_CORR_MIN = 0.999
_DEFAULT_SCALE_BAND = 0.02          # |scale_ratio - 1| allowed (catches unit/settlement scale bugs)
_DEFAULT_REL_TOL = 0.10             # worst |Δchange| as a fraction of a typical daily change
_DEFAULT_MAX_OUTLIER_DAYS = 8       # days/yr allowed to exceed REL_TOL (roll-date mismatches)


def read_backadj(store_path: str, symbol: str, source: str = "norgate") -> pd.DataFrame:
    """Read one symbol's backadj OHLC frame straight from a store's parquet,
    normalized to a tz-naive daily Date index. Empty if absent.

    Tries this package's layout first, then cotdata's pre-ADR-0007 flat `prices/`, so
    a store synced before the move still reads. The fallback is layout-only and carries
    no vendor, which is exactly why it is a FALLBACK: in the old layout a Norgate and a
    databento `ES_backadj.parquet` were the same path, and telling them apart is the
    reason the vendor became a directory.
    """
    for layout in (f"bars/futures/{source}", "prices"):
        p = Path(store_path) / layout / f"{symbol}_backadj.parquet"
        if p.exists():
            df = pd.read_parquet(p)
            df.index = pd.to_datetime(df.index).tz_localize(None).normalize()
            df.index.name = "Date"
            return df.sort_index()
    return pd.DataFrame()


def _roll_dates(df: pd.DataFrame) -> pd.DatetimeIndex:
    """Roll dates = days the Delivery Month changes. Empty if the column is absent.
    Values differ between providers (Norgate '202606' vs databento 'ESM6'), but the
    DATES they change on are comparable."""
    if "Delivery Month" not in df.columns:
        return pd.DatetimeIndex([])
    dm = df["Delivery Month"]
    changed = dm.ne(dm.shift()) & dm.shift().notna()
    return df.index[changed.fillna(False)]


def compare(norgate: pd.DataFrame, databento: pd.DataFrame, symbol: str = "?") -> dict:
    """Compare two backadj frames over their date overlap. Returns a metrics dict."""
    idx = norgate.index.intersection(databento.index)
    m: dict = {"symbol": symbol, "n_common": int(len(idx))}
    if len(idx) < 3:
        m["error"] = "insufficient overlap"
        return m

    n = norgate.loc[idx, "Close"].astype(float)
    d = databento.loc[idx, "Close"].astype(float)

    # Shape check on daily CHANGES (additive back-adjust preserves changes, not returns).
    nc = n.diff().dropna()
    dc = d.diff().reindex(nc.index)
    scale = float(nc.abs().median()) or 1.0        # a typical daily change, for unitless norm
    rel = (nc - dc).abs() / scale
    m["change_corr"] = float(nc.corr(dc))
    m["scale_ratio"] = float(dc.std() / nc.std()) if float(nc.std()) else float("nan")
    m["change_max_rel_diff"] = float(rel.max())
    m["change_rmse_points"] = float(((nc - dc) ** 2).mean() ** 0.5)

    # Level difference: informational (a piecewise-constant offset is expected).
    lvl = (n - d).abs()
    m["level_mean_abs"] = float(lvl.mean())
    m["level_max_abs"] = float(lvl.max())

    # Coverage + roll agreement.
    m["norgate_span"] = f"{n.index.min().date()}..{n.index.max().date()}"
    m["databento_span"] = f"{d.index.min().date()}..{d.index.max().date()}"
    nr_rolls = _roll_dates(norgate).intersection(idx)
    db_rolls = _roll_dates(databento).intersection(idx)
    m["rolls_norgate"] = int(len(nr_rolls))
    m["rolls_databento"] = int(len(db_rolls))
    m["rolls_common"] = int(len(nr_rolls.intersection(db_rolls)))

    years = max((idx.max() - idx.min()).days / 365.25, 1e-9)
    m["outlier_days"] = int((rel > _DEFAULT_REL_TOL).sum())
    m["outlier_days_per_yr"] = m["outlier_days"] / years
    return m


def evaluate(m: dict, corr_min: float, scale_band: float, rel_tol: float,
             max_outliers_per_yr: float) -> list:
    """Return a list of failure reasons for one symbol's metrics (empty = pass)."""
    if "error" in m:
        return [m["error"]]
    fails = []
    if m["change_corr"] < corr_min:
        fails.append(f"change_corr {m['change_corr']:.5f} < {corr_min}")
    if abs(m["scale_ratio"] - 1.0) > scale_band:
        fails.append(f"scale_ratio {m['scale_ratio']:.4f} off 1.0 by > {scale_band} "
                     f"(unit/settlement scale mismatch?)")
    # A big worst-day diff is only a failure if it happens on too many days (roll noise
    # on a handful of days a year is expected and fine).
    n_over = round(m["outlier_days_per_yr"], 1)
    if m["change_max_rel_diff"] > rel_tol and m["outlier_days_per_yr"] > max_outliers_per_yr:
        fails.append(f"{n_over} days/yr exceed rel tol {rel_tol} "
                     f"(worst {m['change_max_rel_diff']:.2f}x a typical day)")
    return fails


def format_report(m: dict, fails: list) -> str:
    if "error" in m:
        return f"  {m['symbol']:<5} SKIP  ({m['error']}, n_common={m['n_common']})"
    status = "PASS" if not fails else "FAIL"
    lines = [
        f"  {m['symbol']:<5} {status}  common={m['n_common']}  "
        f"norgate={m['norgate_span']}  databento={m['databento_span']}",
        f"        change_corr={m['change_corr']:.5f}  scale_ratio={m['scale_ratio']:.4f}  "
        f"worst_rel={m['change_max_rel_diff']:.2f}  rmse={m['change_rmse_points']:.4f}pts  "
        f"outliers={m['outlier_days']} ({m['outlier_days_per_yr']:.1f}/yr)",
        f"        level|Δ| mean={m['level_mean_abs']:.4f} max={m['level_max_abs']:.4f}  "
        f"rolls n/db/common={m['rolls_norgate']}/{m['rolls_databento']}/{m['rolls_common']}",
    ]
    if fails:
        lines.append("        FAIL: " + "; ".join(fails))
    return "\n".join(lines)


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description="Validate databento backadj against Norgate.")
    p.add_argument("--norgate-store", default=None,
                   help="Store holding the Norgate series. Default: $MARKETDATA_STORE, "
                        "where both vendors normally live side by side.")
    p.add_argument("--databento-store", default=None,
                   help="Store holding the databento series. Default: $MARKETDATA_STORE.")
    p.add_argument("--symbols", nargs="+", default=_DEFAULT_SYMBOLS)
    p.add_argument("--change-corr-min", type=float, default=_DEFAULT_CHANGE_CORR_MIN)
    p.add_argument("--scale-band", type=float, default=_DEFAULT_SCALE_BAND)
    p.add_argument("--rel-tol", type=float, default=_DEFAULT_REL_TOL)
    p.add_argument("--max-outlier-days-per-yr", type=float, default=_DEFAULT_MAX_OUTLIER_DAYS)
    args = p.parse_args(argv)

    default_store = os.environ.get("MARKETDATA_STORE", "").strip()
    ng_store = args.norgate_store or default_store
    db_store = args.databento_store or default_store
    if not ng_store or not db_store:
        p.error("no store to read. Set $MARKETDATA_STORE, or pass --norgate-store and "
                "--databento-store explicitly.")

    print(f"Validating databento vs Norgate backadj on: {', '.join(args.symbols)}")
    if ng_store == db_store:
        print(f"  one store, two vendors: {ng_store}")
    any_fail, any_data = False, False
    for sym in args.symbols:
        ng = read_backadj(ng_store, sym, "norgate")
        db = read_backadj(db_store, sym, "databento")
        if ng.empty or db.empty:
            missing = ", ".join(s for s, df in (("norgate", ng), ("databento", db)) if df.empty)
            print(f"  {sym:<5} SKIP  (no backadj in: {missing})")
            continue
        any_data = True
        m = compare(ng, db, sym)
        fails = evaluate(m, args.change_corr_min, args.scale_band, args.rel_tol,
                         args.max_outlier_days_per_yr)
        any_fail = any_fail or bool(fails)
        print(format_report(m, fails))

    if not any_data:
        print("No comparable symbols found in both stores.")
        return 2
    return 1 if any_fail else 0


if __name__ == "__main__":
    sys.exit(main())
