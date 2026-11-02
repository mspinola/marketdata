"""AAII Sentiment Survey: the `series` domain's second producer.

WHAT IT IS. The American Association of Individual Investors polls its members
weekly on whether stocks will be higher, unchanged or lower in six months, and
publishes the bullish, neutral and bearish shares every Thursday. History runs
from July 1987. It is the one crowd-sentiment survey with that much history, and
the CMR source names it as a read he uses (npf docs/npf/l3_sentiment_candidate.md).

ONE STAGE, AND A PERSON RUNS IT. The history is a member spreadsheet,
``sentiment.xls``, behind an AAII login and a bot wall; nothing here fetches it,
and nothing should try. A person downloads the file and runs
``marketdata-update --build-aaii PATH``. The file is AAII member data: it is never
committed to any repo (this one is public) and lives only as the built series in
the store. Re-running the build on a newer download appends the weeks the store
lacks.

WHAT THE BUILD REFUSES, and writes nothing for that symbol when it does:

  * A sheet whose header row does not carry Date / Bullish / Neutral / Bearish in
    the first four columns. AAII has kept that layout since 1997; if it moves, a
    person should look before the parser guesses.
  * Dates that repeat or run backwards.
  * A week with some legs filled and some blank, or a value outside 0-1, or three
    legs that do not sum to 1 within ``SUM_TOLERANCE``. Weeks with all three
    blank are AAII's own gaps (three of them) and are skipped, never stored.
  * A week the store already holds at a different value, unless the symbol is
    named in ``accept_restatement`` (same rule as the TradingView build: only a
    person who has compared the two can tell a revision from a bad file).
  * A registry anchor that does not match.

STORED AS SERVED. AAII publishes decimals (0.36), so the store holds decimals and
the registry kind is ``fraction``. Index is the "Reported Date", the Thursday of
publication, so a reading is known on its own index date and needs no lag
beyond the consumer's own.
"""
from __future__ import annotations

from pathlib import Path
from typing import Iterable, Optional

import numpy as np
import pandas as pd

from .. import store
from ..registry import Symbol, all_symbols, default_price_source, resolve_source
from .tradingview import OHLC, RawError, check_anchors

NAME = "aaii"
DOMAIN = "series"
SHEET = "SENTIMENT"
LEGS = ("Bullish", "Neutral", "Bearish")
# AAII's own Total column reads as low as 0.99998 from rounding to six places.
SUM_TOLERANCE = 0.005


def read_sheet(path) -> pd.DataFrame:
    """The SENTIMENT sheet, headerless and untouched. Needs `xlrd` for a .xls."""
    try:
        return pd.read_excel(path, sheet_name=SHEET, header=None)
    except ImportError as e:
        raise ImportError("reading AAII's sentiment.xls needs xlrd: "
                          "pip install 'marketdata[aaii]'") from e


def parse_sheet(raw: pd.DataFrame, what: str = "sentiment.xls") -> pd.DataFrame:
    """One row per published week, columns `LEGS`, indexed by Reported Date.
    Weeks with no reading at all are dropped. Raises `RawError` on anything else
    that does not look like the sheet AAII has published since 1997."""
    header = None
    for i in range(min(len(raw), 10)):
        if str(raw.iat[i, 0]).strip() == "Date":
            header = i
            break
    if header is None:
        raise RawError(f"{what}: no 'Date' header in the first 10 rows of {SHEET}")
    got = tuple(str(raw.iat[header, c]).strip() for c in range(1, 1 + len(LEGS)))
    if got != LEGS:
        raise RawError(f"{what}: header reads {got}, expected {LEGS}. The layout "
                       f"moved; look at the file before changing the parser.")

    body = raw.iloc[header + 1:, :1 + len(LEGS)]
    dates = pd.to_datetime(body.iloc[:, 0], errors="coerce")
    # Below the weekly rows the sheet carries summary blocks (averages, yearly max
    # and min) whose first column is a label, not a date.
    body = body.loc[dates.notna()]
    dates = dates.loc[dates.notna()]
    vals = body.iloc[:, 1:].apply(pd.to_numeric, errors="coerce")
    vals.columns = list(LEGS)
    vals.index = pd.DatetimeIndex(dates.dt.normalize(), name="Date")

    if vals.index.duplicated().any():
        d = vals.index[vals.index.duplicated()][0].date()
        raise RawError(f"{what}: week {d} appears twice")
    if not vals.index.is_monotonic_increasing:
        raise RawError(f"{what}: dates run backwards")

    blank = vals.isna()
    partial = blank.any(axis=1) & ~blank.all(axis=1)
    if partial.any():
        raise RawError(f"{what}: week {vals.index[partial][0].date()} has some legs "
                       f"blank and some filled")
    vals = vals.loc[~blank.all(axis=1)]
    v = vals.to_numpy(dtype=float)
    if (v < 0).any() or (v > 1).any():
        raise RawError(f"{what}: a share is outside 0-1 (min {v.min():g}, max "
                       f"{v.max():g}); AAII publishes decimals")
    off = np.abs(v.sum(axis=1) - 1) > SUM_TOLERANCE
    if off.any():
        raise RawError(f"{what}: week {vals.index[off][0].date()} legs sum to "
                       f"{v[off][0].sum():g}, not 1")
    return vals


def _targets(symbols: Optional[Iterable[str]] = None) -> list:
    default = default_price_source()
    wanted = set(symbols) if symbols is not None else None
    return [s for s in all_symbols()
            if s.domain == DOMAIN and s.aaii and resolve_source(s, default) == NAME
            and (wanted is None or s.internal in wanted)]


def build_symbol(sym: Symbol, weeks: pd.DataFrame, *, accept: bool = False) -> int:
    """Append the weeks the store lacks for one leg. Returns rows written.
    Raises `RawError` and writes nothing on any refusal."""
    c = weeks[sym.aaii].astype(float)
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
                f"{sym.internal}: week {common[bad][0].date()} is {b[bad][0]:g} in the "
                f"file and {a[bad][0]:g} in the store ({int(bad.sum())} week(s) differ). "
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
        print(f"  ACCEPTED {sym.internal} {day.date()}: store {old:g} -> {now:g}")
    store.write_bars(sym.internal, out.astype(float), domain=DOMAIN, source=NAME)
    return int(len(new))


def build(path, symbols: Optional[Iterable[str]] = None, *,
          accept_restatement: Optional[Iterable[str]] = None) -> dict:
    """Build every registry symbol that resolves to AAII from one downloaded
    sentiment.xls (scope with `symbols`). Local file only. Returns the producer
    result dict the CLI prints: ``{kind, ok, partial, wrote, failed, errors}``."""
    kind = "series_aaii"
    targets = _targets(symbols)
    if not targets:
        return {"kind": kind, "ok": True, "partial": False, "wrote": 0,
                "failed": 0, "errors": []}
    name = Path(path).name
    try:
        weeks = parse_sheet(read_sheet(path), name)
    except RawError as e:
        print(f"{name}: REFUSED -- {e}")
        return {"kind": kind, "ok": False, "partial": False, "wrote": 0,
                "failed": len(targets), "errors": [(name, str(e))]}
    print(f"{name}: {len(weeks)} weeks, {weeks.index.min().date()} to "
          f"{weeks.index.max().date()}")
    accept = set(accept_restatement or ())
    wrote = failed = 0
    errors = []
    for s in targets:
        try:
            n = build_symbol(s, weeks, accept=s.internal in accept)
        except RawError as e:
            print(f"{s.internal}: REFUSED -- {e}")
            failed += 1
            errors.append((s.internal, str(e)))
            continue
        if n:
            wrote += 1
            print(f"{s.internal}: +{n} week(s) -> store")
        else:
            print(f"{s.internal}: already current")
    return {"kind": kind, "ok": failed == 0, "partial": 0 < failed < len(targets),
            "wrote": wrote, "failed": failed, "errors": errors}

