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

PROVISIONAL WEEKS, A SEPARATE SERIES. The free table runs three months behind and
the current readings are subscriber-only, so the weeks between the official
series' end and today come from elsewhere: ``provisional.csv`` in the raw
directory, kept by hand (columns ``week, value, source``; ``week`` is NAAIM's
survey date), a row a week added with ``--add-naaim-week VALUE``
(:func:`add_provisional_week`). They are built into ``NAAIM_EXPOSURE_PROVISIONAL`` (registry key
``naaim: provisional``), never into ``NAAIM_EXPOSURE``, for two reasons:

  * The official build never rewrites a stored week. An approximate value stored
    there would make every later official week a restatement the build refuses.
  * Studies read ``NAAIM_EXPOSURE`` (npf's NAAIM study among them). Hand values
    in it would change what a frozen study reads.

Unlike the official series this one is REWRITTEN on every build, from the file
and the official series' current end: it holds only the file's weeks AFTER that
end, so each provisional week drops out as NAAIM publishes it, with no step by
anyone. A file week the official series now covers is compared with it and the
difference printed, so the provisional source keeps being measured. The file is
refused, and nothing written, on a week or value that does not parse, a value
outside -200 to 200, a repeated week, or two weeks under five days apart.

The first file (2026-11-06) backfilled 2026-08-12 to 2026-11-04: 12 weeks read off
a StockCharts weekly chart image by bar height, and 2026-11-04 from the chart's
own label. Read the same way, the chart's bars matched the official series to a
median 0.19 points over 249 shared weeks (90% within 0.51). StockCharts' own
series differs from NAAIM's on a few weeks (2022-05-04 drawn at about 57 against
31.76, 2024-11-27 drawn at the previous week's value), so the source, not only
the reading, carries error. ``scripts/read_naaim_chart.py`` is the reproducer.
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
# The registry `naaim:` value of the hand-kept provisional series, and its file.
PROVISIONAL = "provisional"
PROVISIONAL_FILE = "provisional.csv"


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
            if s.domain == DOMAIN and s.naaim and s.naaim != PROVISIONAL
            and resolve_source(s, default) == NAME
            and (wanted is None or s.internal in wanted)]


def _provisional_targets(symbols: Optional[Iterable[str]] = None) -> list:
    wanted = set(symbols) if symbols is not None else None
    return [s for s in all_symbols()
            if s.domain == DOMAIN and s.naaim == PROVISIONAL
            and (wanted is None or s.internal in wanted)]


def read_provisional(path: Path) -> pd.Series:
    """The hand-kept file as a week -> value series, oldest first. Raises `RawError`
    on anything it cannot read as written."""
    what = path.name
    try:
        df = pd.read_csv(path, dtype=str, comment="#")
    except Exception as e:  # noqa: BLE001 -- any unreadable file is one refusal
        raise RawError(f"{what}: unreadable ({e})") from e
    missing = [c for c in ("week", "value", "source") if c not in df.columns]
    if missing:
        raise RawError(f"{what}: needs columns week, value, source; missing {missing}")
    weeks = pd.to_datetime(df["week"].str.strip(), format="%Y-%m-%d", errors="coerce")
    values = pd.to_numeric(df["value"].str.strip(), errors="coerce")
    bad = weeks.isna() | values.isna()
    if bad.any():
        row = df[bad].iloc[0]
        raise RawError(f"{what}: row week={row['week']!r} value={row['value']!r} "
                       f"does not parse")
    s = pd.Series(values.to_numpy(dtype=float), index=pd.DatetimeIndex(weeks))
    if s.index.duplicated().any():
        raise RawError(f"{what}: week {s.index[s.index.duplicated()][0].date()} "
                       f"appears twice")
    return _checked(s.sort_index(), what)


#: The `source` column a row added by `add_provisional_week` carries: the value
#: label StockCharts prints on its `!NAAIM` chart, the weekly reading's free source.
PUBLIC_SOURCE = "stockcharts chart label"
#: NAAIM's usual survey day. 1038 of the 1048 official weeks through 2026-08-05
#: fall on it; holiday weeks move to the Thursday (after July 4, Christmas) or the
#: Tuesday (Thanksgiving), so other days are allowed when named, never defaulted.
SURVEY_WEEKDAY = 2


def latest_survey_week(today: Optional[dt.date] = None) -> dt.date:
    """The latest Wednesday on or before `today`: on a Thursday, yesterday's survey."""
    today = today or dt.date.today()
    return today - dt.timedelta(days=(today.weekday() - SURVEY_WEEKDAY) % 7)


def add_provisional_week(value: float, *, week: Optional[dt.date] = None,
                         today: Optional[dt.date] = None,
                         source: str = PUBLIC_SOURCE) -> str:
    """Append one week's reading to ``provisional.csv`` (created if absent) and
    return what was done, in words. The week defaults to `latest_survey_week`; a
    holiday week's Thursday or Tuesday must be named. The new file must pass
    `read_provisional` (a parseable value inside -200 to 200, weeks at least five
    days apart) or nothing is written. Re-adding a week with the same value changes
    nothing; with another value it is refused (a typo is fixed in the file, by
    hand, where it shows). A value identical to the previous week's is refused
    too: that is the label before it updated, typed under the new date. Raises
    `RawError`."""
    week = week or latest_survey_week(today)
    value = round(float(value), 2)
    path = raw_root() / PROVISIONAL_FILE
    # Bytes, not read_text: universal newlines would fold a CRLF file to LF.
    text = path.read_bytes().decode("utf-8") if path.exists() else ""
    newline = "\r\n" if "\r\n" in text else "\n"
    if not text.strip():
        text = "week,value,source" + newline
    elif not text.endswith(("\n", "\r")):
        text += newline
    have = read_provisional(path) if path.exists() and text.count("\n") > 1 else None
    stamp = pd.Timestamp(week)
    if have is not None and stamp in have.index:
        old = round(float(have.loc[stamp]), 2)
        if old == value:
            return f"week {week} is already in {path.name} at {value:.2f}; nothing to add"
        raise RawError(f"week {week} is already in {path.name} at {old:.2f}, not "
                       f"{value:.2f}. Fix that row by hand if the reading changed.")
    if have is not None and len(have):
        prev = have[have.index < stamp]
        if len(prev) and round(float(prev.iloc[-1]), 2) == value:
            raise RawError(
                f"{value:.2f} is week {prev.index[-1].date()}'s reading too. The label "
                f"has probably not updated to week {week} yet; try again later.")
    new = text + f"{week:%Y-%m-%d},{value:.2f},{source}" + newline
    # Checked as a whole before it replaces the file, so a bad row writes nothing.
    tmp = path.with_name(path.name + ".new")
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp.write_bytes(new.encode("utf-8"))
    try:
        read_provisional(tmp)
    except RawError as e:
        tmp.unlink()
        raise RawError(str(e).replace(tmp.name, path.name)) from None
    os.replace(tmp, path)
    return f"added week {week} at {value:.2f} to {path.name}"


def build_provisional(sym: Symbol, official: str) -> dict:
    """Rewrite `sym` from the provisional file: only weeks after the official
    series' last week. Writes only when that differs from what is stored. Returns
    {"weeks", "dropped", "compared", "changed"}; raises `RawError` and writes nothing
    on a refused file."""
    path = raw_root() / PROVISIONAL_FILE
    hand = (read_provisional(path) if path.exists()
            else pd.Series(dtype=float, index=pd.DatetimeIndex([])))
    stored = store.read_bars(official, DOMAIN, NAME)
    if stored.empty:
        end, covered = None, pd.Series(dtype=float)
    else:
        idx = pd.to_datetime(stored.index).tz_localize(None).normalize()
        closes = pd.Series(stored["Close"].to_numpy(dtype=float), index=idx)
        end = idx.max()
        covered = closes.reindex(hand.index).dropna()
    keep = hand if end is None else hand[hand.index > end]
    if len(covered):
        diff = hand.reindex(covered.index) - covered
        print(f"{sym.internal}: {len(covered)} provisional week(s) now official; "
              f"provisional minus official: median abs {diff.abs().median():.2f}, "
              f"max abs {diff.abs().max():.2f} (worst {diff.abs().idxmax().date()})")
    out = pd.DataFrame({k: keep.astype(float) for k in OHLC})
    out.index.name = "Date"
    before = store.read_bars(sym.internal, DOMAIN, NAME)
    changed = not _same(before, out)
    if changed:
        store.write_bars(sym.internal, out, domain=DOMAIN, source=NAME)
    return {"weeks": len(keep), "dropped": len(hand) - len(keep),
            "compared": len(covered), "changed": changed}


def _same(stored: pd.DataFrame, out: pd.DataFrame) -> bool:
    """True when the stored provisional series already holds exactly `out`. No file
    and nothing stored is the same too, so a build with neither writes nothing."""
    if stored.empty:
        return out.empty
    idx = pd.to_datetime(stored.index).tz_localize(None).normalize()
    return (len(idx) == len(out) and (idx == out.index).all()
            and np.allclose(stored["Close"].to_numpy(dtype=float),
                            out["Close"].to_numpy(dtype=float), rtol=0, atol=1e-12))


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


#: The official series the provisional one trims against.
OFFICIAL = "NAAIM_EXPOSURE"


def build(symbols: Optional[Iterable[str]] = None, *,
          accept_restatement: Optional[Iterable[str]] = None) -> dict:
    """Build every registry symbol that resolves to NAAIM from all raw captures.
    Local files only. Returns the producer result dict the CLI prints."""
    kind = "series_naaim"
    official = _build_official(symbols, accept_restatement)
    prov = _build_provisionals(symbols)
    wrote, failed = official["wrote"] + prov["wrote"], official["failed"] + prov["failed"]
    total = official["targets"] + prov["targets"]
    return {"kind": kind, "ok": failed == 0, "partial": 0 < failed < total,
            "wrote": wrote, "failed": failed,
            "errors": official["errors"] + prov["errors"]}


def _build_provisionals(symbols) -> dict:
    """Every provisional symbol, after the official ones so it trims to their end."""
    targets = _provisional_targets(symbols)
    wrote = failed = 0
    errors = []
    for s in targets:
        try:
            got = build_provisional(s, OFFICIAL)
        except RawError as e:
            print(f"{s.internal}: REFUSED -- {e}")
            failed += 1
            errors.append((s.internal, str(e)))
            continue
        wrote += int(got["changed"])
        print(f"{s.internal}: {got['weeks']} provisional week(s) after the official end"
              f" ({got['dropped']} superseded){'' if got['changed'] else ', unchanged'}")
    return {"targets": len(targets), "wrote": wrote, "failed": failed, "errors": errors}


def _build_official(symbols, accept_restatement) -> dict:
    targets = _targets(symbols)
    if not targets:
        return {"targets": 0, "wrote": 0, "failed": 0, "errors": []}
    try:
        weeks = merge_raw(raw_files())
    except RawError as e:
        print(f"naaim: REFUSED -- {e}")
        return {"targets": len(targets), "wrote": 0, "failed": len(targets),
                "errors": [("naaim", str(e))]}
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
    return {"targets": len(targets), "wrote": wrote, "failed": failed, "errors": errors}
