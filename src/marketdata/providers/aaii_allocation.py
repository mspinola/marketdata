"""AAII Asset Allocation Survey: monthly, beside the weekly sentiment survey.

WHAT IT IS. AAII asks its members each month how their portfolios are split between
stocks, bonds and cash, and publishes the averages early the following month. History
runs from November 1987. Where the sentiment survey asks what members expect, this one
asks what they hold.

ONE STAGE, AND A PERSON RUNS IT, exactly as for `aaii` (sentiment.xls): the history is
a member spreadsheet, ``asset.xls``, behind an AAII login; nothing here fetches it. A
person downloads it and runs ``marketdata-update --build-aaii-allocation PATH``. The
file is AAII member data: never committed to any repo (this one is public), it lives
only as the built series in the store. A newer download appends the months the store
lacks.

WHAT IS STORED. The sheet has five detailed shares (stock funds, individual stocks,
bond funds, individual bonds, cash) to six places, then the three totals AAII headlines
(stocks, bonds, cash) to three. "Stocks" heads two columns, so columns are keyed here
by position, and the registry names a key, never a header. Stored as served: the
headline three as AAII rounds them, the four detailed fund/direct shares as given.
Detailed cash is the headline cash unrounded and is not stored twice.

DATES. One row per survey month, indexed on that month's last day. AAII's own date
cells vary in their day (the 30th, the 1st, the 24th...), and the calendar month is the
survey's: 2012-12-25 is followed by 2013-01-01, then 2013-02-01. The results are
published in the first days of the NEXT month (the download that carried October 2026
was saved 2026-11-02), so a reading is NOT known on its index date: a point-in-time
consumer lags it into the following month.

ONE REPAIR, printed every time it applies. AAII's file labels the February to December
rows of 2006 and of 2007 as 2005 (22 rows): each follows a correctly dated January,
their values differ from the real 2005 rows, and with them read as 2006 and 2007 the
file's 468 rows are exactly its 468 calendar months, one each. So a row whose MONTH is
the next one expected but whose YEAR is not is re-dated to the expected year. Anything
else out of sequence (a skipped month, a repeated month, a month out of order) is
refused.

WHAT THE BUILD REFUSES, writing nothing for that symbol when it does:

  * A header row that does not read Stock Funds / Stocks / Bond Funds / Bonds / Cash /
    Total, a blank, then Stocks / Bonds / Cash. If AAII moves a column, a person
    should look before the parser guesses.
  * Months out of sequence beyond the one repair above.
  * A row with some shares blank, a share outside 0-1, five detailed shares or three
    headline shares not summing to 1 within ``SUM_TOLERANCE``, or a headline total
    more than ``HEADLINE_TOLERANCE`` from its detailed parts.
  * A month the store holds at a different value, unless named in
    ``accept_restatement``; and a registry anchor that does not match.
"""
from __future__ import annotations

from pathlib import Path
from typing import Iterable, Optional

import numpy as np
import pandas as pd

from .. import store
from ..registry import Symbol, all_symbols, default_price_source, resolve_source
from .tradingview import OHLC, RawError, check_anchors

NAME = "aaii_allocation"
DOMAIN = "series"
#: Column position -> (registry key, header text) in AAII's sheet.
COLUMNS = {
    1: ("stock_funds", "Stock Funds"),
    2: ("stocks_direct", "Stocks"),
    3: ("bond_funds", "Bond Funds"),
    4: ("bonds_direct", "Bonds"),
    5: ("cash_detail", "Cash"),
    6: ("total_detail", "Total"),
    8: ("stocks", "Stocks"),
    9: ("bonds", "Bonds"),
    10: ("cash", "Cash"),
}
DETAIL = ("stock_funds", "stocks_direct", "bond_funds", "bonds_direct", "cash_detail")
HEADLINE = ("stocks", "bonds", "cash")
#: The keys a registry symbol may name (the totals, and the fund/direct split).
STORABLE = ("stocks", "bonds", "cash", "stock_funds", "stocks_direct", "bond_funds",
            "bonds_direct")
# The detailed shares' own Total column reads 0.9977 to 1.0007 in the file (rounding
# of averages over a few hundred members); the three headline totals, rounded to three
# places, sum to 0.998 to 1.001. Measured on the 2026-11-02 download, all 468 months.
SUM_TOLERANCE = 0.005
# A headline total is its detailed parts rounded to three places: measured at most
# 0.001 apart over all 468 months.
HEADLINE_TOLERANCE = 0.002


def read_sheet(path) -> pd.DataFrame:
    """The first sheet, headerless and untouched. Needs `xlrd` for a .xls."""
    try:
        return pd.read_excel(path, sheet_name=0, header=None)
    except ImportError as e:
        raise ImportError("reading AAII's asset.xls needs xlrd: "
                          "pip install 'marketdata[aaii]'") from e


def _header_row(raw: pd.DataFrame, what: str) -> int:
    want = {c: h for c, (_, h) in COLUMNS.items()}
    for i in range(min(len(raw), 10)):
        got = {c: str(raw.iat[i, c]).strip() for c in want if c < raw.shape[1]}
        if got == want:
            return i
    raise RawError(f"{what}: no header row reading {list(want.values())} in columns "
                   f"{list(want)} of the first 10 rows. The layout moved; look at the "
                   f"file before changing the parser.")


def _month_ends(dates: list, what: str) -> tuple:
    """(index of month ends, [(row date, repaired month end), ...]). See ONE REPAIR."""
    out, repaired = [], []
    for d in dates:
        here = pd.Timestamp(d).to_period("M")
        if out:
            expected = out[-1] + 1
            if here == expected:
                pass
            elif here.month == expected.month and here.year != expected.year:
                repaired.append((pd.Timestamp(d).date(), expected.to_timestamp("M").date()))
                here = expected
            else:
                raise RawError(f"{what}: {pd.Timestamp(d).date()} follows "
                               f"{out[-1].to_timestamp('M').date()}; expected a "
                               f"{expected.strftime('%B %Y')} survey. Months must run one "
                               f"after another with none skipped or repeated.")
        out.append(here)
    idx = pd.DatetimeIndex([p.to_timestamp("M") for p in out], name="Date")
    return idx, repaired


def parse_sheet(raw: pd.DataFrame, what: str = "asset.xls") -> tuple:
    """(months, repairs): one row per survey month, a column per COLUMNS key as a
    0-1 share, indexed on the month's last day; and the year labels repaired. Raises
    `RawError` on anything that does not look like AAII's sheet."""
    header = _header_row(raw, what)
    body = raw.iloc[header + 1:]
    dates = pd.to_datetime(body.iloc[:, 0], errors="coerce")
    # Below the monthly rows the sheet carries summary rows (Average, Maximum...)
    # whose first cell is a label, not a date.
    body = body.loc[dates.notna()]
    dates = dates.loc[dates.notna()]
    if body.empty:
        raise RawError(f"{what}: no dated rows under the header")
    vals = pd.DataFrame({key: pd.to_numeric(body.iloc[:, c], errors="coerce")
                         for c, (key, _) in COLUMNS.items()})
    idx, repaired = _month_ends(list(dates), what)
    vals.index = idx

    blank = vals.isna()
    if blank.any().any():
        row = vals.index[blank.any(axis=1)][0].date()
        raise RawError(f"{what}: the {row:%B %Y} survey has a blank share")
    shares = vals.drop(columns=["total_detail"])
    v = shares.to_numpy(dtype=float)
    if (v < 0).any() or (v > 1).any():
        raise RawError(f"{what}: a share is outside 0-1 (min {v.min():g}, max "
                       f"{v.max():g}); AAII publishes decimals")
    for keys, label in ((DETAIL, "detailed"), (HEADLINE, "headline")):
        total = vals[list(keys)].sum(axis=1)
        off = (total - 1).abs() > SUM_TOLERANCE
        if off.any():
            raise RawError(f"{what}: the {off.idxmax():%B %Y} {label} shares sum to "
                           f"{total[off].iloc[0]:g}, not 1")
    parts = {"stocks": ("stock_funds", "stocks_direct"),
             "bonds": ("bond_funds", "bonds_direct"), "cash": ("cash_detail",)}
    for head, legs in parts.items():
        gap = (vals[head] - vals[list(legs)].sum(axis=1)).abs()
        if (gap > HEADLINE_TOLERANCE).any():
            raise RawError(f"{what}: the {gap.idxmax():%B %Y} {head} total is "
                           f"{gap.max():.4f} from its detailed parts")
    return vals, repaired


def _targets(symbols: Optional[Iterable[str]] = None) -> list:
    default = default_price_source()
    wanted = set(symbols) if symbols is not None else None
    return [s for s in all_symbols()
            if s.domain == DOMAIN and s.aaii_allocation
            and resolve_source(s, default) == NAME
            and (wanted is None or s.internal in wanted)]


def build_symbol(sym: Symbol, months: pd.DataFrame, *, accept: bool = False) -> int:
    """Append the months the store lacks for one share. Returns rows written. Raises
    `RawError` and writes nothing on any refusal. The same rules as the sentiment
    build's `aaii.build_symbol`, kept apart so a change to one survey's build never
    moves the other's."""
    if sym.aaii_allocation not in STORABLE:
        raise RawError(f"{sym.internal}: aaii_allocation {sym.aaii_allocation!r} is not "
                       f"one of {STORABLE}")
    c = months[sym.aaii_allocation].astype(float)
    merged = pd.DataFrame({k: c for k in OHLC})
    merged.index.name = "Date"
    restated = []
    stored = store.read_bars(sym.internal, DOMAIN, NAME)
    if not stored.empty:
        stored = stored.copy()
        stored.index = pd.to_datetime(stored.index).tz_localize(None).normalize()
        stored = stored.sort_index()
        common = stored.index.intersection(merged.index)
        a = stored.loc[common, "Close"].to_numpy(dtype=float)
        b = merged.loc[common, "Close"].to_numpy(dtype=float)
        bad = ~np.isclose(a, b, rtol=0, atol=1e-9)
        if bad.any() and not accept:
            raise RawError(
                f"{sym.internal}: {common[bad][0]:%B %Y} is {b[bad][0]:g} in the file "
                f"and {a[bad][0]:g} in the store ({int(bad.sum())} month(s) differ). "
                f"The store is never rewritten by a build. Once you have decided AAII "
                f"revised it, --accept-restatement {sym.internal} takes the file's value.")
        new = merged.loc[merged.index.difference(stored.index)]
        out = pd.concat([stored[list(OHLC)], new]).sort_index()
        if bad.any():
            out.loc[common[bad], list(OHLC)] = merged.loc[common[bad], list(OHLC)].to_numpy()
            restated = list(zip(common[bad], a[bad], b[bad]))
    else:
        new = merged
        out = merged
    out.index.name = "Date"
    check_anchors(out, sym, sym.internal)
    if new.empty and not restated:
        return 0
    for day, old, now in restated:
        print(f"  ACCEPTED {sym.internal} {day:%B %Y}: store {old:g} -> {now:g}")
    store.write_bars(sym.internal, out.astype(float), domain=DOMAIN, source=NAME)
    return int(len(new))


def build(path, symbols: Optional[Iterable[str]] = None, *,
          accept_restatement: Optional[Iterable[str]] = None) -> dict:
    """Build every registry symbol that resolves to the allocation survey from one
    downloaded asset.xls (scope with `symbols`). Local file only. Returns the producer
    result dict the CLI prints: ``{kind, ok, partial, wrote, failed, errors}``."""
    kind = "series_aaii_allocation"
    targets = _targets(symbols)
    if not targets:
        return {"kind": kind, "ok": True, "partial": False, "wrote": 0,
                "failed": 0, "errors": []}
    name = Path(path).name
    try:
        months, repaired = parse_sheet(read_sheet(path), name)
    except RawError as e:
        print(f"{name}: REFUSED -- {e}")
        return {"kind": kind, "ok": False, "partial": False, "wrote": 0,
                "failed": len(targets), "errors": [(name, str(e))]}
    print(f"{name}: {len(months)} months, {months.index.min():%B %Y} to "
          f"{months.index.max():%B %Y}")
    if repaired:
        print(f"{name}: re-dated {len(repaired)} row(s) whose year label was wrong "
              f"(their month is the next one in sequence):")
        for was, now in repaired:
            print(f"  {was} -> {now:%B %Y}")
    accept = set(accept_restatement or ())
    wrote = failed = 0
    errors = []
    for s in targets:
        try:
            n = build_symbol(s, months, accept=s.internal in accept)
        except RawError as e:
            print(f"{s.internal}: REFUSED -- {e}")
            failed += 1
            errors.append((s.internal, str(e)))
            continue
        if n:
            wrote += 1
            print(f"{s.internal}: +{n} month(s) -> store")
        else:
            print(f"{s.internal}: already current")
    return {"kind": kind, "ok": failed == 0, "partial": 0 < failed < len(targets),
            "wrote": wrote, "failed": failed, "errors": errors}
