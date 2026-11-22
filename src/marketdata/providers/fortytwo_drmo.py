"""42 Macro's weekly "Dr. Mo" table: a hand-delivered weekly history, kept as published.

WHAT IT IS. 42 Macro publishes a weekly slide (dated a Saturday) titled the
Discretionary Risk Management Overlay, "Dr. Mo". Per asset it prints three things: a
VAMS state (Volatility-Adjusted Momentum Signal: bullish, neutral or bearish), a
14-day RSI, and a PROPER TRADE (long max, long half, no position, short half, short
max), under one market regime label for the whole report. It is subscriber material:
this module carries the SCHEMA and no reading. No value from a real report belongs in
this repository, in code, tests, fixtures, docs or commit messages. Design note:
``docs/design/drmo.md``.

TWO STAGES, the shape of :mod:`.tradingview_calendar`. There is no API. A Claude
session the subscriber runs transcribes the week's slide to a CSV, and that file
lands at ``<raw root>/drmo/<YYYY-MM-DD>.csv`` on the producer box (the raw root is
``_raw/fortytwo`` under the store, so the replica syncs' ``_raw`` exclusion keeps the
transcriptions off the Mac and the VPS). :func:`build` reads only those local files.
Nothing here opens a socket.

WHY A HISTORY, AND WHY APPEND-ONLY. Unlike the calendar, a published week is a fact:
what the report said that Saturday. So ``metadata/drmo.parquet`` holds EVERY week, one
row per (report_date, asset), and a stored week is never rewritten by a build. A raw
file that disagrees with its stored week is refused, naming the assets, until a person
names that week in ``replace``: the transcription is made by a model reading a slide,
and a silent overwrite would let a later bad re-read replace a good one. That is what
lets a consumer show a past week as it was (``read(asof=...)``).

WHAT IS REFUSED, each by file name, each writing nothing for that week:

- a file that is not the agreed CSV (header, one report date equal to the file name,
  one regime, the three vocabularies, an integer RSI in 0 to 100, unique assets and
  tickers);
- a report dated in the future;
- an asset list that differs from the previous stored week's, unless
  ``accept_universe``: a dropped row is the likeliest transcription error and looks
  exactly like the publisher retiring an asset, so a person says which;
- a week whose every reading equals the previous week's: last week's slide under a
  new date (about a hundred RSI values do not all repeat).

THE READER NEVER SERVES A STALE WEEK AS CURRENT. ``read()`` returns the newest report
on or before ``asof`` only while it is at most ``max_age_days`` old (default 6: a
Saturday report covers through the following Friday), else an EMPTY frame. A missing
table is an empty frame too. A consumer therefore sees absent, never last week's
reading in this week's slot.

WHAT IT REFUSES TO BE. Not a symbol map: assets are stored under the publisher's own
name and ticker (two crypto rows have no ticker), and relating one to a futures
market is the consumer's judgment. Not a signal: nothing here has been through
crucible, and the table is context for a reader.
"""
from __future__ import annotations

import csv
import datetime as dt
import io
import os
import re
import shutil
from pathlib import Path
from typing import Iterable, Optional

import pandas as pd

from .. import config, store

NAME = "fortytwo"
CAPTURE_DIR = "drmo"
TABLE = "drmo"

#: A Saturday report is current through the following Friday.
MAX_AGE_DAYS = 6

VAMS = ("bullish", "neutral", "bearish")
#: PROPER TRADE as printed, and the signed fraction of a full position it stands for.
POSITION = {"long_max": 1.0, "long_half": 0.5, "none": 0.0,
            "short_half": -0.5, "short_max": -1.0}
#: The slide highlights a PROPER TRADE that changed since the previous report, by
#: direction. Optional in the file: a transcription that cannot see it leaves it out.
CHANGE = ("bullish", "bearish")

REQUIRED = ("report_date", "regime", "group", "asset", "ticker", "vams", "rsi",
            "proper_trade")
OPTIONAL = ("change",)
#: Stored columns, in order. ``position`` is derived from ``proper_trade``.
COLUMNS = ("report_date", "regime", "group", "asset", "ticker", "vams", "rsi",
           "proper_trade", "position", "change")
_READINGS = ["vams", "rsi", "proper_trade"]

_FILE_RE = re.compile(r"^(\d{4}-\d{2}-\d{2})\.csv$")
_TICKER_RE = re.compile(r"^[A-Z0-9][A-Z0-9.\-]{0,11}$")


class RawError(ValueError):
    """A transcription the build refuses. The message names the file."""


def raw_root() -> Path:
    """$MARKETDATA_FORTYTWO_RAW if set, else ``_raw/fortytwo`` under the store."""
    env = os.environ.get("MARKETDATA_FORTYTWO_RAW")
    return Path(env) if env else config.store_root() / "_raw" / NAME


def capture_dir() -> Path:
    return raw_root() / CAPTURE_DIR


def table_path() -> Path:
    return config.metadata_dir() / f"{TABLE}.parquet"


def captures() -> list:
    """Every ``YYYY-MM-DD.csv`` under the capture folder, oldest first. Anything else
    there (a note, a temp file) is ignored, not refused."""
    d = capture_dir()
    if not d.is_dir():
        return []
    return sorted(p for p in d.iterdir() if p.is_file() and _FILE_RE.match(p.name))


def _empty() -> pd.DataFrame:
    return _typed(pd.DataFrame(columns=list(COLUMNS)))


def _typed(df: pd.DataFrame) -> pd.DataFrame:
    df = df.reindex(columns=list(COLUMNS))
    df["report_date"] = pd.to_datetime(df["report_date"])
    df["rsi"] = df["rsi"].astype("int64")
    df["position"] = df["position"].astype("float64")
    for c in ("regime", "group", "asset", "ticker", "vams", "proper_trade", "change"):
        df[c] = df[c].astype("object").where(df[c].notna(), None)
    return df


def parse_capture(path, *, expect_date: Optional[dt.date] = None,
                  today: Optional[dt.date] = None) -> pd.DataFrame:
    """One transcription to the stored frame, or :class:`RawError` naming the file.

    ``expect_date`` is the date the file's rows must carry. It defaults to the file's
    own name; pass it only for a file not yet filed under its date (:func:`file_capture`).
    """
    path = Path(path)
    if expect_date is None:
        m = _FILE_RE.match(path.name)
        if not m:
            raise RawError(f"{path}: a transcription is named YYYY-MM-DD.csv")
        try:
            expect_date = dt.date.fromisoformat(m.group(1))
        except ValueError as e:
            raise RawError(f"{path}: the name is not a date ({e})") from None
    try:
        text = path.read_bytes().decode("utf-8-sig")
    except (OSError, UnicodeDecodeError) as e:
        raise RawError(f"{path}: not readable UTF-8 text ({e})") from None
    reader = csv.reader(io.StringIO(text))
    try:
        header = [h.strip() for h in next(reader)]
    except StopIteration:
        raise RawError(f"{path}: empty file") from None
    allowed = REQUIRED + OPTIONAL
    missing = [c for c in REQUIRED if c not in header]
    extra = [c for c in header if c not in allowed]
    if missing or extra or len(set(header)) != len(header):
        raise RawError(f"{path}: header is {header}; expected {list(REQUIRED)} "
                       f"with an optional {list(OPTIONAL)} and nothing else")

    rows, assets, tickers = [], set(), set()
    regimes = set()
    for n, raw in enumerate(reader, start=2):
        if not any(c.strip() for c in raw):
            continue
        where = f"{path} line {n}"
        if len(raw) != len(header):
            raise RawError(f"{where}: {len(raw)} fields, the header has {len(header)} "
                           f"(an asset name with a comma must be quoted)")
        r = {k: v.strip() for k, v in zip(header, raw)}
        asset = r["asset"]
        if not asset:
            raise RawError(f"{where}: no asset name")
        where = f"{where} ({asset})"
        if r["report_date"] != expect_date.isoformat():
            raise RawError(f"{where}: report_date is {r['report_date']!r}, expected "
                           f"{expect_date.isoformat()}")
        if not r["regime"]:
            raise RawError(f"{where}: no regime")
        regimes.add(r["regime"].upper())
        if not r["group"]:
            raise RawError(f"{where}: no group")
        if asset.lower() in assets:
            raise RawError(f"{where}: asset appears twice")
        assets.add(asset.lower())
        ticker = r["ticker"].upper() or None
        if ticker is not None:
            if not _TICKER_RE.match(ticker):
                raise RawError(f"{where}: ticker {r['ticker']!r} is not a ticker "
                               f"(leave it empty for an asset printed without one)")
            if ticker in tickers:
                raise RawError(f"{where}: ticker {ticker} appears twice")
            tickers.add(ticker)
        vams = r["vams"].lower()
        if vams not in VAMS:
            raise RawError(f"{where}: vams is {r['vams']!r}, expected one of {VAMS}")
        if not re.fullmatch(r"\d{1,3}", r["rsi"]) or int(r["rsi"]) > 100:
            raise RawError(f"{where}: rsi is {r['rsi']!r}, expected a whole number "
                           f"from 0 to 100")
        trade = r["proper_trade"].lower()
        if trade not in POSITION:
            raise RawError(f"{where}: proper_trade is {r['proper_trade']!r}, expected "
                           f"one of {tuple(POSITION)}")
        change = r.get("change", "").lower() or None
        if change is not None and change not in CHANGE:
            raise RawError(f"{where}: change is {r['change']!r}, expected empty or one "
                           f"of {CHANGE}")
        rows.append({"report_date": pd.Timestamp(expect_date), "regime": r["regime"].upper(),
                     "group": r["group"], "asset": asset, "ticker": ticker, "vams": vams,
                     "rsi": int(r["rsi"]), "proper_trade": trade,
                     "position": POSITION[trade], "change": change})
    if not rows:
        raise RawError(f"{path}: a header and no rows")
    if len(regimes) != 1:
        raise RawError(f"{path}: {len(regimes)} different regimes ({sorted(regimes)}); "
                       f"a report has one")
    if expect_date > (today or dt.date.today()):
        raise RawError(f"{path}: report_date {expect_date} is in the future")
    return _typed(pd.DataFrame(rows))


def file_capture(path, *, replace: Iterable[dt.date] = (),
                 today: Optional[dt.date] = None) -> Path:
    """Copy a transcription saved anywhere (a browser's Downloads folder, under any
    name) to ``<capture dir>/<its report date>.csv``, after it parses. The date comes
    from the file's own ``report_date`` column. A different file already filed for
    that date is refused unless the date is in ``replace``. Raises `RawError`."""
    path = Path(path)
    try:
        text = path.read_bytes().decode("utf-8-sig")
        first = next(csv.DictReader(io.StringIO(text)))
        date = dt.date.fromisoformat((first.get("report_date") or "").strip())
    except (OSError, UnicodeDecodeError) as e:
        raise RawError(f"{path}: not readable UTF-8 text ({e})") from None
    except (StopIteration, ValueError):
        raise RawError(f"{path}: no first row with an ISO report_date, so there is no "
                       f"week to file it under") from None
    parse_capture(path, expect_date=date, today=today)
    dest = capture_dir() / f"{date.isoformat()}.csv"
    if dest.exists() and dest.resolve() != path.resolve():
        if dest.read_bytes() == path.read_bytes():
            return dest
        if date not in set(replace):
            raise RawError(f"{path}: {dest} already holds a different transcription of "
                           f"{date}. Compare the two with the slide, then pass "
                           f"--replace-drmo-week {date} to file this one over it.")
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.resolve() != path.resolve():
        shutil.copyfile(path, dest)
    return dest


#: What singles the table slide out of a deck, from its text layer alone: the overlay's
#: name plus a full date in the title. Measured on five decks, 2023 to 2026: several
#: other slides carry the name (an index page, per-asset chart pages) and none of them
#: a dated title. The table itself is an image, so this finds the page and its date and
#: nothing more; reading the table still takes eyes.
SLIDE_NAME = "Discretionary Risk Management Overlay"
_TITLE_DATE_RE = re.compile(
    r"(?:Mon|Tues|Wednes|Thurs|Fri|Satur|Sun)day,\s+"
    r"(January|February|March|April|May|June|July|August|September|October|November|"
    r"December)\s+(\d{1,2}),\s+(20\d\d)")
_MONTHS = {m: i for i, m in enumerate(
    ("January", "February", "March", "April", "May", "June", "July", "August",
     "September", "October", "November", "December"), start=1)}


def find_slide(pdf) -> tuple:
    """``(page, report_date)`` of the Dr. Mo table slide in a deck PDF: the 1-based
    page for a PDF reader and the date printed in the slide's title. A deck that
    prints the slide twice (same date) returns the first. Raises `RawError` when no
    page qualifies or two qualify with different dates. Needs ``pypdf`` (the ``drmo``
    extra); reads the local file only."""
    try:
        from pypdf import PdfReader
    except ImportError:
        raise RawError("finding the slide needs pypdf: install the drmo extra "
                       "(uv pip install pypdf)") from None
    try:
        pages = PdfReader(str(pdf)).pages
        texts = [" ".join((p.extract_text() or "").split()) for p in pages]
    except Exception as e:  # pypdf raises its own family, plus OSError
        raise RawError(f"{pdf}: not a readable PDF ({e})") from None
    hits = []
    for n, t in enumerate(texts, start=1):
        m = _TITLE_DATE_RE.search(t) if SLIDE_NAME in t else None
        if m:
            try:
                hits.append((n, dt.date(int(m.group(3)), _MONTHS[m.group(1)],
                                        int(m.group(2)))))
            except ValueError:
                continue
    if not hits:
        raise RawError(f"{pdf}: no page carries the overlay's name with a dated title "
                       f"({len(texts)} pages read). Not a deck with the Dr. Mo slide, or "
                       f"the slide's title changed.")
    if len({d for _, d in hits}) > 1:
        raise RawError(f"{pdf}: candidate pages disagree on the date: "
                       + ", ".join(f"page {n} {d}" for n, d in hits))
    return hits[0]


def _same(a: pd.DataFrame, b: pd.DataFrame) -> list:
    """Assets on which two frames of one week disagree, in any stored column."""
    cols = [c for c in COLUMNS if c != "report_date"]
    a = a.set_index("asset")[[c for c in cols if c != "asset"]]
    b = b.set_index("asset")[[c for c in cols if c != "asset"]]
    out = sorted(set(a.index) ^ set(b.index))
    for asset in sorted(set(a.index) & set(b.index)):
        ra, rb = a.loc[asset], b.loc[asset]
        if any(not ((pd.isna(x) and pd.isna(y)) or x == y) for x, y in zip(ra, rb)):
            out.append(asset)
    return out


def _check_against_previous(df: pd.DataFrame, prev: pd.DataFrame, src: Path,
                            accept_universe: bool) -> None:
    week = f"{df['report_date'].iloc[0]:%Y-%m-%d}"
    pweek = f"{prev['report_date'].iloc[0]:%Y-%m-%d}"
    new, old = set(df["asset"]), set(prev["asset"])
    if new != old and not accept_universe:
        raise RawError(
            f"{src}: the asset list differs from week {pweek}'s "
            f"(added: {sorted(new - old) or 'none'}; missing: {sorted(old - new) or 'none'}). "
            f"A dropped or renamed row is the likeliest transcription error. Check the "
            f"slide; if the publisher did change the list, pass --accept-drmo-universe.")
    if new == old:
        a = df.set_index("asset")[_READINGS].sort_index()
        b = prev.set_index("asset")[_READINGS].sort_index()
        if a.equals(b):
            raise RawError(f"{src}: every reading equals week {pweek}'s. That is last "
                           f"week's slide filed as {week}, not a new report.")


def build(path=None, *, replace: Iterable[dt.date] = (), accept_universe: bool = False,
          today: Optional[dt.date] = None) -> dict:
    """Add every filed week the table lacks, and verify every week it holds.

    ``path`` files one transcription first (:func:`file_capture`). ``replace`` names
    weeks whose filed transcription may overwrite the stored week (a corrected
    re-read). Returns the CLI's result dict. A refused week leaves the table's other
    weeks as they were, and the result is ``partial`` when the table still holds at
    least one good week.
    """
    replace = set(replace)
    errors = []
    if path is not None:
        try:
            dest = file_capture(path, replace=replace, today=today)
            print(f"drmo: filed {path} as {dest}")
        except RawError as e:
            print(f"drmo: REFUSED -- {e}")
            errors.append((Path(path).name, str(e)))
    files = captures()
    if not files and not errors:
        msg = f"no transcription under {capture_dir()}; this week's has not been filed"
        print(f"drmo: REFUSED -- {msg}")
        errors.append((TABLE, msg))

    table = history()
    weeks = {d: g.reset_index(drop=True) for d, g in table.groupby("report_date")}
    added, replaced = [], []
    for src in files:
        try:
            df = parse_capture(src, today=today)
            date = df["report_date"].iloc[0]
            if date in weeks:
                diff = _same(df, weeks[date])
                if not diff:
                    continue
                if date.date() not in replace:
                    raise RawError(
                        f"{src}: differs from the stored week {date:%Y-%m-%d} on "
                        f"{len(diff)} asset(s): {', '.join(diff[:8])}"
                        f"{' ...' if len(diff) > 8 else ''}. A stored week is never "
                        f"rewritten by a build. Compare with the slide, then pass "
                        f"--replace-drmo-week {date:%Y-%m-%d} if the file is right.")
            earlier = [d for d in weeks if d < date]
            if earlier:
                _check_against_previous(df, weeks[max(earlier)], src, accept_universe)
            (replaced if date in weeks else added).append(date)
            weeks[date] = df
        except RawError as e:
            print(f"drmo: REFUSED -- {e}")
            errors.append((src.name, str(e)))

    if added or replaced:
        out = _typed(pd.concat([weeks[d] for d in sorted(weeks)], ignore_index=True))
        store._atomic_write_parquet(out, table_path())
        store._touch_manifest("metadata", TABLE, out.set_index("report_date"), NAME)
        for label, dates in (("added", added), ("REPLACED", replaced)):
            for d in sorted(dates):
                print(f"drmo: {label} week {d:%Y-%m-%d}, {len(weeks[d])} assets, regime "
                      f"{weeks[d]['regime'].iloc[0]}")
        print(f"drmo: {len(weeks)} week(s) -> {table_path()}")
    elif not errors:
        print(f"drmo: already current, {len(weeks)} week(s), newest "
              f"{max(weeks):%Y-%m-%d}")
    return {"kind": "drmo", "ok": not errors, "partial": bool(errors) and bool(weeks),
            "wrote": sum(len(weeks[d]) for d in added + replaced),
            "failed": len(errors), "errors": errors}


def history() -> pd.DataFrame:
    """Every stored week, oldest first, or an EMPTY frame when no build has run."""
    p = table_path()
    return _typed(pd.read_parquet(p)) if p.exists() else _empty()


def read(asof=None, *, max_age_days: Optional[int] = MAX_AGE_DAYS) -> pd.DataFrame:
    """The report current on ``asof`` (default today), as it was published, or EMPTY.

    The newest report dated on or before ``asof``, unless it is more than
    ``max_age_days`` older than ``asof``: then EMPTY, because a late or missing week
    must read as absent rather than as last week's reading in this week's slot.
    ``max_age_days=None`` returns the newest report on or before ``asof`` whatever
    its age (the caller then owns saying how old it is; ``report_date`` is on every
    row). Empty too when no build has run.
    """
    asof = pd.Timestamp(asof if asof is not None else dt.date.today()).normalize()
    if asof.tzinfo is not None:
        asof = asof.tz_localize(None)
    table = history()
    dates = table["report_date"][table["report_date"] <= asof]
    if dates.empty:
        return _empty()
    newest = dates.max()
    if max_age_days is not None and (asof - newest).days > max_age_days:
        return _empty()
    return table[table["report_date"] == newest].reset_index(drop=True)
