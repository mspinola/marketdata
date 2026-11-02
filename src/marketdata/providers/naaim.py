"""NAAIM Exposure Index: the free, delayed table, captured and accumulated.

WHAT IT IS. The National Association of Active Investment Managers polls its
member managers weekly on their equity exposure, from 200% short to 200% long,
and publishes the mean as the NAAIM Exposure Index (history from 2006). Since
2026-08-01 the current value and the full history are a paid subscription; the
public page embeds a table, ``index.naaim.org/embeddable/table``, three months
behind and about two and a half years deep (131 weeks, 2024-01-31 to 2026-07-29,
when first captured on 2026-11-01). No download is offered.

TWO STAGES, BOTH HERE. :func:`fetch` saves that page VERBATIM as
``<raw root>/<capture date>.html`` and nothing else, so a parser fix can be
re-run over every capture ever taken. :func:`build` parses every capture and
appends the weeks the store lacks. The raw directory is producer-internal and
excluded from every sync (the `_raw` exclusion), like TradingView's.

WHY CAPTURE EVERY WEEK. The table may be a sliding window. If it is, a week
drops off the front each time one is added at the back, and a store built from
captures is the only free history that grows. The Windows equities task runs
the capture, weekdays, so a missed run costs nothing while the window lasts.

WHAT THE BUILD REFUSES, and writes nothing when it does:

  * A page whose table header does not start Date / NAAIM Number. The parser
    does not guess at a moved layout.
  * A value outside -200 to 200, a repeated week within one capture, or a row
    whose date or value does not parse.
  * A week two captures disagree on, or a week the store holds at a different
    value, unless the symbol is named in ``accept_restatement``. NAAIM has not
    been seen to revise; if it does, a person should look.
  * A registry anchor that does not match.

A FULL-HISTORY FILE. NAAIM sells the whole series as a spreadsheet (one sheet,
a header row naming "Date" and "NAAIM Number", newest week first). Dropped into
the raw directory as ``*.xlsx``, :func:`build` reads it beside the captures and
the two must agree wherever they overlap. It is read with the standard library
(:func:`read_xlsx`) so the producer venv needs no Excel package. The file as
first received (2026-11-01, through 2026-07-29) had three defects, each handled
openly rather than silently:

  * Two weeks (2006-07-05, 2006-07-12) appear twice, once rounded to two places.
    Repeats that agree to the rounding collapse to the more precise value; any
    other repeat is refused.
  * One row is misdated: "2014-03-18" (a Tuesday, between the 2014-03-12 and
    2014-03-19 Wednesdays) carries the S&P 500 close 2099.50, which is the
    2015-03-18 close (2014-03-18 closed 1872.25), and 2015-03-18 is the week the
    file is missing. :data:`CORRECTIONS` redates that row, matched on its exact
    value, so a corrected file simply stops matching.
  * Three rows (2018-08-08, 2021-04-07, 2022-06-22) differ between the file's
    "Mean/Average" and "NAAIM Number" columns. The build reads "NAAIM Number",
    the headline the public table also publishes; the disagreement is recorded
    in the registry note, not resolved.

Every source also passes a spacing check: two weeks under five days apart are
refused, which is how a misdated row shows itself.

The other columns (the bearish and bullish extremes, the quartiles, the
deviation) stay in the raw files; only the headline index is a stored series.
"""
from __future__ import annotations

import datetime as dt
import os
import re
import urllib.request
import xml.etree.ElementTree as ET
import zipfile
from html.parser import HTMLParser
from pathlib import Path
from typing import Iterable, Optional

import numpy as np
import pandas as pd

from .. import config, store
from ..registry import Symbol, all_symbols, default_price_source, resolve_source
from .tradingview import OHLC, RawError, check_anchors

NAME = "naaim"
DOMAIN = "series"
URL = "https://index.naaim.org/embeddable/table"
# The page is served to browsers; a bare urllib agent is refused by some CDNs.
_UA = "Mozilla/5.0 (marketdata NAAIM capture)"
HEADER = ("Date", "NAAIM Number")
MIN_SPACING_DAYS = 5
# Rows the full-history file is known to misdate, keyed (date as served, exact
# value) -> true date. See the module docstring for the evidence.
CORRECTIONS = {("2014-03-18", 67.77057142857143): "2015-03-18"}


def raw_root() -> Path:
    """$MARKETDATA_NAAIM_RAW if set, else ``_raw/naaim`` under the store."""
    env = os.environ.get("MARKETDATA_NAAIM_RAW")
    return Path(env) if env else config.store_root() / "_raw" / NAME


def raw_files() -> list:
    """Every capture (``*.html``) and full-history file (``*.xlsx``), by name."""
    root = raw_root()
    return sorted([*root.glob("*.html"), *root.glob("*.xlsx")])


def fetch(*, when: Optional[dt.date] = None, timeout: int = 60) -> Path:
    """Save today's page verbatim. Parses it once to refuse a page that is not
    the table (a login wall, an error page) before it joins the raw history."""
    req = urllib.request.Request(URL, headers={"User-Agent": _UA})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        body = r.read()
    parse_page(body.decode("utf-8", errors="replace"), "fetched page")
    root = raw_root()
    root.mkdir(parents=True, exist_ok=True)
    out = root / f"{(when or dt.date.today()).isoformat()}.html"
    out.write_bytes(body)
    return out


class _Table(HTMLParser):
    """Collects the cell text of every row of the first table."""

    def __init__(self):
        super().__init__()
        self.rows, self._row, self._cell, self._depth, self._done = [], None, None, 0, False

    def handle_starttag(self, tag, attrs):
        if self._done:
            return
        if tag == "table":
            self._depth += 1
        elif tag == "tr" and self._depth:
            self._row = []
        elif tag in ("td", "th") and self._row is not None:
            self._cell = []

    def handle_endtag(self, tag):
        if self._done:
            return
        if tag in ("td", "th") and self._cell is not None:
            self._row.append(" ".join("".join(self._cell).split()))
            self._cell = None
        elif tag == "tr" and self._row is not None:
            self.rows.append(self._row)
            self._row = None
        elif tag == "table" and self._depth:
            self._depth -= 1
            self._done = self._depth == 0

    def handle_data(self, data):
        if self._cell is not None:
            self._cell.append(data)


def parse_page(text: str, what: str) -> pd.Series:
    """The headline index by week from one captured page. Raises `RawError`."""
    t = _Table()
    t.feed(text)
    if not t.rows or tuple(t.rows[0][:2]) != HEADER:
        got = tuple(t.rows[0][:2]) if t.rows else ()
        raise RawError(f"{what}: table header starts {got}, expected {HEADER}. "
                       f"Not the NAAIM table, or the layout moved.")
    body = [r for r in t.rows[1:] if r]
    try:
        dates = pd.to_datetime([r[0] for r in body], format="%m/%d/%Y")
        vals = np.array([float(r[1]) for r in body])
    except (ValueError, IndexError) as e:
        raise RawError(f"{what}: a row does not parse as date, number: {e}") from e
    s = pd.Series(vals, index=pd.DatetimeIndex(dates, name="Date")).sort_index()
    return _checked(s, what)


def _checked(s: pd.Series, what: str) -> pd.Series:
    """The checks every source passes, whatever its format."""
    if s.empty:
        raise RawError(f"{what}: the table has no rows")
    if s.index.duplicated().any():
        raise RawError(f"{what}: week {s.index[s.index.duplicated()][0].date()} appears twice")
    if (s.abs() > 200).any():
        raise RawError(f"{what}: a value is outside -200 to 200 (min {s.min():g}, "
                       f"max {s.max():g})")
    gaps = s.index.to_series().diff().dt.days
    if (gaps < MIN_SPACING_DAYS).any():
        d = gaps[gaps < MIN_SPACING_DAYS].index[0]
        raise RawError(f"{what}: week {d.date()} is {int(gaps[d])} day(s) after the "
                       f"week before it. A row is misdated; look before building.")
    return s


_NS = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
_CELL = re.compile(r"([A-Z]+)(\d+)")


def read_xlsx(path: Path) -> list:
    """The first sheet's rows as lists of cell values (str or float, None where
    blank), standard library only. Dates come back as Excel serial numbers."""
    with zipfile.ZipFile(path) as z:
        wb = ET.fromstring(z.read("xl/workbook.xml"))
        pr = wb.find(f"{_NS}workbookPr")
        if pr is not None and pr.get("date1904") in ("1", "true"):
            raise RawError(f"{path.name}: 1904 date system, not supported")
        shared = []
        if "xl/sharedStrings.xml" in z.namelist():
            for si in ET.fromstring(z.read("xl/sharedStrings.xml")).iter(f"{_NS}si"):
                shared.append("".join(x.text or "" for x in si.iter(f"{_NS}t")))
        sheets = sorted(n for n in z.namelist() if re.fullmatch(r"xl/worksheets/sheet\d+\.xml", n))
        if not sheets:
            raise RawError(f"{path.name}: no worksheet")
        root = ET.fromstring(z.read(sheets[0]))
    rows = []
    for row in root.iter(f"{_NS}row"):
        cells = {}
        for c in row.iter(f"{_NS}c"):
            col = 0
            for ch in _CELL.fullmatch(c.get("r")).group(1):
                col = col * 26 + ord(ch) - 64
            kind, v = c.get("t"), c.find(f"{_NS}v")
            if kind == "inlineStr":
                cells[col - 1] = "".join(x.text or "" for x in c.iter(f"{_NS}t"))
            elif v is None or v.text is None:
                continue
            elif kind == "s":
                cells[col - 1] = shared[int(v.text)]
            elif kind in ("str", "e"):
                cells[col - 1] = v.text
            else:
                cells[col - 1] = float(v.text)
        rows.append([cells.get(i) for i in range(max(cells) + 1)] if cells else [])
    return rows


def parse_history(path: Path) -> pd.Series:
    """The headline index by week from a full-history spreadsheet. Raises `RawError`."""
    what = path.name
    rows = [r for r in read_xlsx(path) if any(x is not None for x in r)]
    head = [str(x).strip() if x is not None else "" for x in rows[0]] if rows else []
    if not all(h in head for h in HEADER):
        raise RawError(f"{what}: header {head[:4]} does not name {HEADER}")
    di, vi = head.index(HEADER[0]), head.index(HEADER[1])
    try:
        pairs = [(pd.Timestamp("1899-12-30") + pd.Timedelta(days=float(r[di])), float(r[vi]))
                 for r in rows[1:]]
    except (TypeError, ValueError, IndexError) as e:
        raise RawError(f"{what}: a row does not parse as date, number: {e}") from e
    week = {}
    for d, v in pairs:
        if d in week:
            a = week[d]
            if abs(a - v) > 0.005:
                raise RawError(f"{what}: week {d.date()} appears twice, as {a:g} and {v:g}")
            week[d] = a if len(repr(a)) >= len(repr(v)) else v   # keep the unrounded one
        else:
            week[d] = v
    for (served, value), true in CORRECTIONS.items():
        d, t = pd.Timestamp(served), pd.Timestamp(true)
        if d in week and abs(week[d] - value) <= 1e-9:
            if t in week:
                raise RawError(f"{what}: correction {served} -> {true} would collide "
                               f"with a week the file already holds")
            week[t] = week.pop(d)
            print(f"naaim: {what}: redated {served} -> {true} (CORRECTIONS)")
    s = pd.Series(week, dtype=float).sort_index()
    s.index = pd.DatetimeIndex(s.index, name="Date")
    return _checked(s, what)


def merge_raw(files: Iterable[Path]) -> pd.Series:
    """Every source's weeks, which must agree wherever two sources overlap."""
    merged: Optional[pd.Series] = None
    for f in files:
        s = (parse_history(f) if f.suffix == ".xlsx"
             else parse_page(f.read_text(encoding="utf-8", errors="replace"), f.name))
        if merged is None:
            merged = s
            continue
        common = merged.index.intersection(s.index)
        bad = ~np.isclose(merged.loc[common].to_numpy(), s.loc[common].to_numpy(),
                          rtol=0, atol=1e-9)
        if bad.any():
            d = common[bad][0]
            raise RawError(f"{f.name}: week {d.date()} reads {s.loc[d]:g}, an earlier "
                           f"source read {merged.loc[d]:g}. NAAIM revised it, or one "
                           f"source is bad; look before building.")
        merged = pd.concat([merged, s.loc[s.index.difference(merged.index)]]).sort_index()
    if merged is None:
        raise RawError(f"no raw captures under {raw_root()}; run --fetch-naaim first")
    return merged


def _targets(symbols: Optional[Iterable[str]] = None) -> list:
    default = default_price_source()
    wanted = set(symbols) if symbols is not None else None
    return [s for s in all_symbols()
            if s.domain == DOMAIN and s.naaim and resolve_source(s, default) == NAME
            and (wanted is None or s.internal in wanted)]


def build_symbol(sym: Symbol, weeks: pd.Series, *, accept: bool = False) -> int:
    """Append the weeks the store lacks. Returns rows written. Raises `RawError`
    and writes nothing on any refusal."""
    merged = pd.DataFrame({k: weeks.astype(float) for k in OHLC})
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
                f"captures and {a[bad][0]:g} in the store. The store is never rewritten "
                f"by a build; --accept-restatement {sym.internal} takes the capture's "
                f"value once you have decided NAAIM revised it.")
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


def build(symbols: Optional[Iterable[str]] = None, *,
          accept_restatement: Optional[Iterable[str]] = None) -> dict:
    """Build every registry symbol that resolves to NAAIM from all raw captures.
    Local files only. Returns the producer result dict the CLI prints."""
    kind = "series_naaim"
    targets = _targets(symbols)
    if not targets:
        return {"kind": kind, "ok": True, "partial": False, "wrote": 0,
                "failed": 0, "errors": []}
    try:
        weeks = merge_raw(raw_files())
    except RawError as e:
        print(f"naaim: REFUSED -- {e}")
        return {"kind": kind, "ok": False, "partial": False, "wrote": 0,
                "failed": len(targets), "errors": [("naaim", str(e))]}
    print(f"naaim: {len(raw_files())} raw file(s), {len(weeks)} weeks, "
          f"{weeks.index.min().date()} to {weeks.index.max().date()}")
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
