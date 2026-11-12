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

PROVISIONAL WEEKS, A SEPARATE SERIES. The store is only as current as the last
spreadsheet someone downloaded, while AAII's public results page
(https://www.aaii.com/sentimentsurvey/sent_results) shows each new week the day
it is published. Those newer weeks come from ``provisional.csv`` in the raw
directory (``$MARKETDATA_AAII_RAW``, else ``_raw/aaii`` under the store), kept by
hand: one row a week, ``week, bullish, neutral, bearish, source``, typed exactly
as the page prints them (``2026-11-04,34.1,17.5,48.4,sent_results``), most easily
with ``--add-aaii-week BULLISH NEUTRAL BEARISH`` (`add_provisional_week`). They are
built into ``AAII_BULLISH_PROVISIONAL`` and its two siblings (registry key
``aaii: provisional``; each trims against the leg its name carries without the
suffix), never into the official legs, for the reasons NAAIM's provisional
series gives (providers/naaim.py): the official build never rewrites a stored
week, and studies read the official legs.

The series is REWRITTEN on every build from the file and the official leg's
current end, keeping only the file's weeks after that end, so each drops out on
its own once a newer spreadsheet is built. A superseded week is compared with the
official one and the difference printed, in percentage points.

DATING. The public page labels a survey by its Wednesday ("Nov 4"); the
spreadsheet reports the same survey on the Thursday after (2026-10-29 for the
page's "Oct 28"). Checked on 2026-11-09 against every week the page then showed
that the store also held: 21 of 21 matched on all three legs (to the page's 0.1
point) one day later, and none on any other offset. The file therefore takes the
date AS PRINTED, refuses any day but a Wednesday, and the build stores each week
on the Thursday after, where the spreadsheet will put it. The spreadsheet has
dated a holiday week differently before (2021-11-24, a Wednesday, the day before
Thanksgiving), though not since 2021; a holiday week entered here would then
simply not be compared when it is superseded, and the build says so.

The values are the page's rounded percentages (one decimal), stored as decimals
(34.1 -> 0.341) so a consumer reads both series the same way. The file is
refused, and nothing written, on a row that does not parse, a day other than
Wednesday, a share outside 0-100, legs that do not sum to 100 within
``100 * SUM_TOLERANCE`` (which also catches a row typed as decimals), a repeated
week, or two weeks under five days apart.

WHY BY HAND. An honest automated client is refused: on 2026-11-09 the results
page answered a request naming itself (``marketdata AAII capture``, or Python's
default agent) with an Incapsula 403 and served the table only to an agent
posing as a desktop browser. Getting past a bot wall by pretending to be a
browser is not something this package does, so the page is read by a person, one
row a week, as NAAIM's provisional weeks are.
"""
from __future__ import annotations

import datetime as dt
import os
from pathlib import Path
from typing import Iterable, Optional

import numpy as np
import pandas as pd

from .. import config, store
from ..registry import REGISTRY, Symbol, all_symbols, default_price_source, resolve_source
from .naaim import _same
from .tradingview import OHLC, RawError, check_anchors

NAME = "aaii"
DOMAIN = "series"
SHEET = "SENTIMENT"
LEGS = ("Bullish", "Neutral", "Bearish")
# AAII's own Total column reads as low as 0.99998 from rounding to six places.
SUM_TOLERANCE = 0.005
# The registry `aaii:` value of the hand-kept provisional legs, the suffix that
# turns each one's name into the official leg it trims against, and its file.
PROVISIONAL = "provisional"
PROVISIONAL_SUFFIX = "_PROVISIONAL"
PROVISIONAL_FILE = "provisional.csv"
PROVISIONAL_COLUMNS = ("week", "bullish", "neutral", "bearish", "source")
# The public page's survey date (a Wednesday) to the spreadsheet's Reported Date.
PUBLIC_WEEKDAY = 2
PUBLIC_TO_REPORTED = pd.Timedelta(days=1)
MIN_SPACING_DAYS = 5


def raw_root() -> Path:
    """$MARKETDATA_AAII_RAW if set, else ``_raw/aaii`` under the store."""
    env = os.environ.get("MARKETDATA_AAII_RAW")
    return Path(env) if env else config.store_root() / "_raw" / NAME


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
            if s.domain == DOMAIN and s.aaii and s.aaii != PROVISIONAL
            and resolve_source(s, default) == NAME
            and (wanted is None or s.internal in wanted)]


def _provisional_targets(symbols: Optional[Iterable[str]] = None) -> list:
    wanted = set(symbols) if symbols is not None else None
    return [s for s in all_symbols()
            if s.domain == DOMAIN and s.aaii == PROVISIONAL
            and (wanted is None or s.internal in wanted)]


def read_provisional(path: Path) -> pd.DataFrame:
    """The hand-kept file as one row per week, columns `LEGS` in decimals, indexed
    by the Reported Date the spreadsheet would give each week (the page's Wednesday
    plus one day). Raises `RawError` on anything it cannot read as written."""
    what = path.name
    try:
        df = pd.read_csv(path, dtype=str, comment="#", skipinitialspace=True)
    except Exception as e:  # noqa: BLE001 -- any unreadable file is one refusal
        raise RawError(f"{what}: unreadable ({e})") from e
    df.columns = [c.strip() for c in df.columns]
    missing = [c for c in PROVISIONAL_COLUMNS if c not in df.columns]
    if missing:
        raise RawError(f"{what}: needs columns {', '.join(PROVISIONAL_COLUMNS)}; "
                       f"missing {missing}")
    if df.empty:
        return pd.DataFrame(columns=list(LEGS), index=pd.DatetimeIndex([], name="Date"),
                            dtype=float)
    weeks = pd.to_datetime(df["week"].str.strip(), format="%Y-%m-%d", errors="coerce")
    legs = df[[c.lower() for c in LEGS]].apply(
        lambda c: pd.to_numeric(c.str.strip().str.rstrip("%"), errors="coerce"))
    bad = weeks.isna() | legs.isna().any(axis=1)
    if bad.any():
        row = df[bad].iloc[0]
        raise RawError(f"{what}: row {', '.join(str(row[c]) for c in PROVISIONAL_COLUMNS[:4])}"
                       f" does not parse")
    if weeks.duplicated().any():
        raise RawError(f"{what}: week {weeks[weeks.duplicated()].iloc[0].date()} "
                       f"appears twice")
    not_wed = weeks.dt.dayofweek != PUBLIC_WEEKDAY
    if not_wed.any():
        w = weeks[not_wed].iloc[0]
        raise RawError(f"{what}: week {w.date()} is a {w.day_name()}. Enter the date "
                       f"the results page prints, the survey's Wednesday; the build "
                       f"moves it to the Thursday the spreadsheet reports it on.")
    v = legs.to_numpy(dtype=float)
    if (v < 0).any() or (v > 100).any():
        raise RawError(f"{what}: a share is outside 0-100 (min {v.min():g}, max "
                       f"{v.max():g}); enter the percentages as the page prints them")
    off = np.abs(v.sum(axis=1) - 100) > 100 * SUM_TOLERANCE
    if off.any():
        i = int(np.argmax(off))
        raise RawError(f"{what}: week {weeks.iloc[i].date()} legs sum to "
                       f"{v[i].sum():g}, not 100. Enter the percentages as printed "
                       f"(34.1, not 0.341).")
    out = pd.DataFrame(v / 100.0, columns=list(LEGS),
                       index=pd.DatetimeIndex(weeks + PUBLIC_TO_REPORTED, name="Date"))
    out = out.sort_index()
    gaps = out.index.to_series().diff().dt.days
    if (gaps < MIN_SPACING_DAYS).any():
        d = gaps[gaps < MIN_SPACING_DAYS].index[0]
        raise RawError(f"{what}: week {(d - PUBLIC_TO_REPORTED).date()} is "
                       f"{int(gaps[d])} day(s) after the week before it. A row is "
                       f"misdated; look before building.")
    return out


#: The `source` column a row added by `add_provisional_week` carries.
PUBLIC_SOURCE = "aaii.com/sentimentsurvey/sent_results"


def latest_survey_week(today: Optional[dt.date] = None) -> dt.date:
    """The newest survey week the results page can show on `today`: the latest
    Wednesday on or before it. AAII publishes a week's results on the Thursday
    after its Wednesday, so on a Thursday this is yesterday; on a Wednesday it is
    today, whose results are not out until tomorrow, and the caller says which."""
    today = today or dt.date.today()
    return today - dt.timedelta(days=(today.weekday() - PUBLIC_WEEKDAY) % 7)


def add_provisional_week(bullish: float, neutral: float, bearish: float, *,
                         week: Optional[dt.date] = None,
                         today: Optional[dt.date] = None) -> str:
    """Append one week's row, as the results page prints it, to ``provisional.csv``
    (created if absent) and return what was done, in words. The week defaults to
    `latest_survey_week`. The new file must pass `read_provisional` (a Wednesday,
    shares that sum to 100, spaced a week from its neighbours) or nothing is
    written. Re-adding a week with the same numbers changes nothing; with other
    numbers it is refused (a typo is fixed in the file, by hand, where it shows).
    Numbers identical to the previous week's are refused too: that is the page
    before it updated, typed under the new date. Raises `RawError`."""
    week = week or latest_survey_week(today)
    if week.weekday() != PUBLIC_WEEKDAY:
        raise RawError(f"week {week} is a {week:%A}. Give the results page's date, the "
                       f"survey's Wednesday.")
    row = (round(float(bullish), 1), round(float(neutral), 1), round(float(bearish), 1))
    path = raw_root() / PROVISIONAL_FILE
    # Bytes, not read_text: universal newlines would fold the producer's CRLF away.
    text = path.read_bytes().decode("utf-8") if path.exists() else ""
    newline = "\r\n" if "\r\n" in text else "\n"
    if not text.strip():
        text = ",".join(PROVISIONAL_COLUMNS) + newline
    elif not text.endswith(("\n", "\r")):
        text += newline
    have = read_provisional(path) if path.exists() and text.strip() else None
    reported = pd.Timestamp(week) + PUBLIC_TO_REPORTED
    if have is not None and reported in have.index:
        old = tuple(round(float(v) * 100, 1) for v in have.loc[reported, list(LEGS)])
        if old == row:
            return f"week {week} is already in {path.name} with these numbers; nothing to add"
        raise RawError(f"week {week} is already in {path.name} as {old}, not {row}. "
                       f"Fix that row by hand if the page changed.")
    if have is not None and len(have):
        prev = have[have.index < reported]
        if len(prev):
            last = tuple(round(float(v) * 100, 1) for v in prev.iloc[-1][list(LEGS)])
            if last == row:
                raise RawError(
                    f"{row} are the numbers of week {(prev.index[-1] - PUBLIC_TO_REPORTED).date()}"
                    f" too. The page has probably not published week {week} yet "
                    f"(AAII posts it on Thursday); try again later.")
    line = ",".join([f"{week:%Y-%m-%d}", *(f"{v:.1f}" for v in row), PUBLIC_SOURCE])
    new = text + line + newline
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
    return (f"added week {week} (page date {week:%b} {week.day}, stored on "
            f"{reported:%Y-%m-%d}): bullish {row[0]}, neutral {row[1]}, bearish {row[2]}")


def official_of(sym: Symbol) -> Symbol:
    """The official leg a provisional symbol trims against, named by its own name
    without the suffix. Raises `RawError` if the registry has no such leg."""
    name = sym.internal
    base = name[:-len(PROVISIONAL_SUFFIX)] if name.endswith(PROVISIONAL_SUFFIX) else None
    off = REGISTRY.get(base) if base else None
    if off is None or not off.aaii or off.aaii == PROVISIONAL or off.aaii not in LEGS:
        raise RawError(f"{name}: a provisional AAII symbol must be named "
                       f"<official leg>{PROVISIONAL_SUFFIX}, and {base!r} is not an "
                       f"official AAII leg in the registry")
    return off


def build_provisional(sym: Symbol, hand: pd.DataFrame) -> dict:
    """Rewrite `sym` from the hand-kept weeks: only those after its official leg's
    last week. Writes only when that differs from what is stored. Returns
    {"weeks", "dropped", "compared", "changed"}; raises `RawError` and writes
    nothing on a refusal."""
    off = official_of(sym)
    week = hand[off.aaii].astype(float)
    stored = store.read_bars(off.internal, DOMAIN, NAME)
    if stored.empty:
        end, covered = None, pd.Series(dtype=float)
    else:
        idx = pd.to_datetime(stored.index).tz_localize(None).normalize()
        closes = pd.Series(stored["Close"].to_numpy(dtype=float), index=idx)
        end = idx.max()
        covered = closes.reindex(week.index).dropna()
    keep = week if end is None else week[week.index > end]
    superseded = len(week) - len(keep)
    if len(covered):
        diff = (week.reindex(covered.index) - covered) * 100.0
        print(f"{sym.internal}: {len(covered)} provisional week(s) now official; "
              f"provisional minus official, points: median abs "
              f"{diff.abs().median():.2f}, max abs {diff.abs().max():.2f} "
              f"(worst {diff.abs().idxmax().date()})")
    if superseded > len(covered):
        print(f"{sym.internal}: {superseded - len(covered)} superseded provisional "
              f"week(s) have no official week on the same date, so were not "
              f"compared (a holiday week the spreadsheet dated differently, or a "
              f"week AAII skipped)")
    out = pd.DataFrame({k: keep for k in OHLC})
    out.index.name = "Date"
    before = store.read_bars(sym.internal, DOMAIN, NAME)
    changed = not _same(before, out)
    if changed:
        store.write_bars(sym.internal, out, domain=DOMAIN, source=NAME)
    return {"weeks": len(keep), "dropped": superseded,
            "compared": len(covered), "changed": changed}


def build_provisionals(symbols: Optional[Iterable[str]] = None) -> dict:
    """Every provisional leg from ``provisional.csv`` (no file means no weeks).
    A file the reader refuses refuses every leg. Returns the producer result dict."""
    kind = "series_aaii_provisional"
    targets = _provisional_targets(symbols)
    if not targets:
        return {"kind": kind, "ok": True, "partial": False, "wrote": 0,
                "failed": 0, "errors": [], "targets": 0}
    path = raw_root() / PROVISIONAL_FILE
    try:
        hand = (read_provisional(path) if path.exists()
                else pd.DataFrame(columns=list(LEGS), dtype=float,
                                  index=pd.DatetimeIndex([], name="Date")))
    except RawError as e:
        print(f"aaii provisional: REFUSED -- {e}")
        return {"kind": kind, "ok": False, "partial": False, "wrote": 0,
                "failed": len(targets), "errors": [(PROVISIONAL_FILE, str(e))],
                "targets": len(targets)}
    wrote = failed = 0
    errors = []
    for s in targets:
        try:
            got = build_provisional(s, hand)
        except RawError as e:
            print(f"{s.internal}: REFUSED -- {e}")
            failed += 1
            errors.append((s.internal, str(e)))
            continue
        wrote += int(got["changed"])
        print(f"{s.internal}: {got['weeks']} provisional week(s) after the official end"
              f" ({got['dropped']} superseded){'' if got['changed'] else ', unchanged'}")
    return {"kind": kind, "ok": failed == 0, "partial": 0 < failed < len(targets),
            "wrote": wrote, "failed": failed, "errors": errors, "targets": len(targets)}


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
    sentiment.xls (scope with `symbols`), then rebuild the provisional legs, so
    they trim to the new official end. Local files only. Returns the producer
    result dict the CLI prints: ``{kind, ok, partial, wrote, failed, errors}``."""
    official = _build_official(path, symbols, accept_restatement)
    prov = build_provisionals(symbols)
    total = official["targets"] + prov["targets"]
    failed = official["failed"] + prov["failed"]
    return {"kind": "series_aaii", "ok": failed == 0,
            "partial": 0 < failed < total,
            "wrote": official["wrote"] + prov["wrote"], "failed": failed,
            "errors": official["errors"] + prov["errors"]}


def _build_official(path, symbols, accept_restatement) -> dict:
    kind = "series_aaii"
    targets = _targets(symbols)
    if not targets:
        return {"kind": kind, "ok": True, "partial": False, "wrote": 0,
                "failed": 0, "errors": [], "targets": 0}
    name = Path(path).name
    try:
        weeks = parse_sheet(read_sheet(path), name)
    except RawError as e:
        print(f"{name}: REFUSED -- {e}")
        return {"kind": kind, "ok": False, "partial": False, "wrote": 0,
                "failed": len(targets), "errors": [(name, str(e))],
                "targets": len(targets)}
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
            "wrote": wrote, "failed": failed, "errors": errors, "targets": len(targets)}

