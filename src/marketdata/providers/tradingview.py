"""TradingView breadth and sentiment series: the `series` domain's producer.

WHAT IT IS. TradingView publishes the daily breadth readings the AGI market rules
run on (the "FOMO" share of Nasdaq stocks above their 5-day average, `INDEX:NCFD`;
new 52-week highs and lows, `INDEX:HIGQ`/`LOWQ`) and the Cboe put/call ratios, as
chart symbols with full daily history. No vendor this stack already pays for
carries the 5-day share at any tier. The scoping and the measurements behind this
module are in cot-analyzer's docs/design/tradingview-breadth-scoping.md and this
repo's docs/design/breadth-domain-scoping.md.

TWO STAGES, AND THE FIRST IS NOT CODE. TradingView has no data API for these
symbols; the feed reaches this stack through a claude.ai connector, which only a
Claude session can call. The producer box runs Claude Code Desktop on that
account, so a Desktop LOCAL ROUTINE there is stage 1: one connector call per
registry series symbol, each tool result written VERBATIM to
``<raw root>/<internal>/<YYYY-MM-DD>.json``. Stage 2 is :func:`build`, which is
this module: it reads only those local files, so it runs anywhere, costs nothing
and can be iterated offline. Same shape as the databento producer (paid ingest,
free build), and it inherits that producer's rule: the raw directory is
PRODUCER-INTERNAL, never part of the consumer contract, and excluded from every
store sync (the `_raw` exclusion in sync-store.cmd and push-to-server.cmd).

WHAT STANDS BETWEEN A LANGUAGE MODEL AND THE STORE. Stage 1 has a model in it, and
the design assumes the model can mis-transcribe a number, skip a symbol, or stop
half way. Every guard is here, in code, and every refusal names the file and the
bar:

  * No number passes through prose. The routine writes the connector's JSON; this
    parses it. A file that is not that shape is refused.
  * Overlap agreement. Every bar in a raw file whose session date the store already
    holds must equal the stored Close exactly. Any mismatch refuses the WHOLE file.
    With a ten-bar nightly pull nine bars are overlap, so a slip on an existing bar
    is caught that night and a slip on the new bar the next night. The build never
    rewrites a stored bar: a genuine vendor restatement surfaces as a refusal for a
    human to resolve, which is the workspace's posture on restated history.
  * Shape and range. Timestamps strictly increasing, one per session; the registry
    `kind` fixes the range (percent 0-100, count a non-negative integer, ratio in
    (0, 10]).
  * Session gate, both sides. The newest bar across the raw files and the store must
    be exactly the expected session (the latest weekday whose 16:30 ET close has
    passed). Older is refused as stale, and the scheduled retry is the routine's
    second run. Newer is refused as unsettled: a routine that fires before the close
    gets the day in progress, whose Close still moves, and a bar stored once is never
    rewritten. Neither side writes anything. Exchange holidays are not modelled: on
    one the evening build refuses and the next session's build carries both days,
    which is inside the freshness tolerance the verifier applies.
  * Pinned anchors. Registry `anchors` (a published reading on a date) are
    re-verified on every build whose input or output holds those dates. The
    standing proof that the vendor's symbol string still names the same series.

WHAT IT REFUSES TO BE. Not a fetcher: nothing here opens a socket. Not a fixer: a
mismatch is reported, never reconciled. Not a calendar: the session gate is a
weekday-and-clock rule, stated above.
"""
from __future__ import annotations

import datetime as dt
import json
import os
from pathlib import Path
from typing import Iterable, Optional
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

from .. import config, store
from ..registry import SERIES_KINDS, Symbol, all_symbols, default_price_source, resolve_source

# MUST match the registry's PRICE_SOURCES entry: the store path component and what
# `resolve_source` returns.
NAME = "tradingview"
DOMAIN = "series"

SESSION_TZ = ZoneInfo("America/New_York")
# A breadth count is computed from the session's closes, so it is settled shortly
# after 16:00 ET. Half an hour of slack, then the day counts as a completed session.
SESSION_CLOSE = dt.time(16, 30)

# The value a caller passes as `expect_session` to switch the gate off (a build of
# old raw files, a test). A sentinel rather than None, so that "not passed" keeps
# its meaning of "compute the expected session".
NO_GATE = "none"

OHLC = ("Open", "High", "Low", "Close")
_BAR_KEYS = ("t", "o", "h", "l", "c")

# Inclusive ranges per kind. `count` has no ceiling; `ratio` has a loose one that a
# put/call ratio has never approached (the series' printed high is 5.4).
_RANGES = {"percent": (0.0, 100.0), "count": (0.0, None), "ratio": (0.0, 10.0)}
assert tuple(_RANGES) == SERIES_KINDS

# TWO VENDOR CONVENTIONS, measured on the full-history pulls of 2026-09-17 and
# handled in the BUILT series only; the raw file keeps what the vendor sent.
#
# A count of zero is printed as 0.01. Nasdaq new 52-week highs read 0.01 on
# 2020-03-13 (the crash week's bottom) and NYSE new lows read 0.01 on 65 of 5000
# days. Anything below this threshold on a count series is a zero, and is stored
# as one; the whole-number check then applies to everything else.
COUNT_ZERO_BELOW = 0.5
# A put/call ratio of zero is impossible, and the equity-only ratio carried two
# bars (2026-06-18, 2026-07-02) with a real open and high and a zero low and
# close. Those are vendor holes, not readings: a ratio bar with a non-positive
# Close is dropped and named in the build's output, never stored as 0.


class RawError(ValueError):
    """A raw file, or a symbol's set of them, that the build refuses. The message
    names the file and the bar, because the fix is a human looking at both."""


# ── Where the raw files live ───────────────────────────────────────────────
def raw_root() -> Path:
    """$MARKETDATA_TRADINGVIEW_RAW if set, else ``_raw/tradingview`` under the store.

    The leading underscore is what keeps it out of the replicas: both sync scripts
    exclude `_raw` by name at any depth, for databento's paid raw store. Rename the
    directory and both exclusions silently stop applying.
    """
    env = os.environ.get("MARKETDATA_TRADINGVIEW_RAW", "").strip()
    return Path(env) if env else (config.store_root() / "_raw" / "tradingview")


def raw_files(internal: str) -> list:
    """Every raw file for one symbol, oldest name first. The routine names files by
    run date, so name order is run order."""
    d = raw_root() / internal
    if not d.is_dir():
        return []
    return sorted(p for p in d.iterdir() if p.suffix == ".json" and p.is_file())


# ── Sessions ──────────────────────────────────────────────────────────────
def session_date(t) -> pd.Timestamp:
    """The US session a vendor bar stamp belongs to. TradingView stamps a daily bar
    at the session OPEN in UTC (13:30Z in summer, 14:30Z in winter), so the Eastern
    calendar date of the stamp is the session date in both halves of the year."""
    return (pd.Timestamp(int(t), unit="s", tz="UTC").tz_convert(SESSION_TZ)
            .normalize().tz_localize(None))


def expected_session(now: Optional[dt.datetime] = None) -> pd.Timestamp:
    """The latest weekday whose close has passed, in Eastern time.

    Before 16:30 ET on a weekday the expected session is the previous weekday; on a
    weekend it is Friday. Exchange holidays are not modelled (see module docstring).
    """
    now = now.astimezone(SESSION_TZ) if now is not None else dt.datetime.now(SESSION_TZ)
    day = now.date()
    if now.weekday() >= 5 or now.time() < SESSION_CLOSE:
        day -= dt.timedelta(days=1)
    while day.weekday() >= 5:
        day -= dt.timedelta(days=1)
    return pd.Timestamp(day)


# ── The backfill: TradingView's own CSV export, converted into a raw file ─────
_CSV_COLS = ("time", "open", "high", "low", "close")


def csv_export_to_raw(csv_path, sym: Symbol, *, when: Optional[dt.date] = None) -> Path:
    """Turn a TradingView chart export ("Export chart data", CSV) into a raw file in
    the connector's shape, so the same build and the same guards apply to a backfill.

    Why this exists: a first fill of history through the routine would mean writing
    thousands of bars verbatim through a language model, with no stored bars for the
    overlap guard to catch a slip against. The export is exact and needs no
    transcription. Its columns are ``time`` (unix seconds at the session open, or an
    ISO timestamp) and ``open``, ``high``, ``low``, ``close``; any other column is
    ignored. The file is written as ``<date>-csv-export.json`` beside the routine's
    files and carries ``"source": "csv-export"`` so its origin is visible. Nothing is
    validated here beyond the columns; :func:`build` does the rest.
    """
    raw = pd.read_csv(csv_path)
    raw.columns = [str(c).strip().lower() for c in raw.columns]
    missing = [c for c in _CSV_COLS if c not in raw.columns]
    if missing:
        raise RawError(f"{Path(csv_path).name}: a TradingView export needs columns "
                       f"{_CSV_COLS}, lacking {missing}; got {list(raw.columns)}")
    t = raw["time"]
    if pd.api.types.is_numeric_dtype(t):
        stamps = t.astype("int64")
    else:
        parsed = pd.to_datetime(t, utc=True, errors="coerce")
        if parsed.isna().any():
            raise RawError(f"{Path(csv_path).name}: a `time` value is neither unix "
                           f"seconds nor an ISO timestamp")
        # Unit-agnostic: pandas may parse ISO strings at microsecond rather than
        # nanosecond resolution, and an astype("int64") then silently means a
        # different unit. Timedelta arithmetic does not care.
        stamps = (parsed - pd.Timestamp(0, tz="UTC")) // pd.Timedelta(seconds=1)
    bars = [{"t": int(s), "o": float(o), "h": float(h), "l": float(lo), "c": float(c),
             "v": None}
            for s, o, h, lo, c in zip(stamps, raw["open"], raw["high"], raw["low"], raw["close"])]
    bars.sort(key=lambda b: b["t"])
    payload = {"success": True, "symbol": sym.tradingview, "interval": "1D",
               "count": len(bars), "bars": bars, "source": "csv-export",
               "file": Path(csv_path).name}
    when = when or dt.datetime.now(SESSION_TZ).date()
    out = raw_root() / sym.internal / f"{when.isoformat()}-csv-export.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload), encoding="utf-8")
    return out


# ── Parsing and validation ─────────────────────────────────────────────────
def parse_raw(path: Path, sym: Symbol) -> pd.DataFrame:
    """One raw file as a Date-indexed OHLC frame, or `RawError` naming why not.

    The connector's shape: ``{"success": true, "symbol": "INDEX:NCFD",
    "interval": "1D", "bars": [{"t": unix_s, "o", "h", "l", "c", "v": null}, ...]}``
    plus summary and notice fields this ignores. Verified on the producer box on
    2026-09-17 against the same pull from a Mac: identical bytes.
    """
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        raise RawError(f"{path.name}: not readable as JSON ({e})") from e
    if not isinstance(data, dict) or not data.get("success", False):
        raise RawError(f"{path.name}: not a successful connector result "
                       f"(success={data.get('success') if isinstance(data, dict) else '?'})")
    if data.get("symbol") != sym.tradingview:
        raise RawError(f"{path.name}: symbol is {data.get('symbol')!r}, the registry "
                       f"maps {sym.internal} to {sym.tradingview!r}")
    if data.get("interval") != "1D":
        raise RawError(f"{path.name}: interval is {data.get('interval')!r}, not '1D'")
    bars = data.get("bars")
    if not isinstance(bars, list) or not bars:
        raise RawError(f"{path.name}: no bars")
    rows = []
    for i, b in enumerate(bars):
        if not isinstance(b, dict) or any(k not in b for k in _BAR_KEYS):
            raise RawError(f"{path.name}: bar {i} lacks one of {_BAR_KEYS}")
        try:
            rows.append((session_date(b["t"]), float(b["o"]), float(b["h"]),
                         float(b["l"]), float(b["c"])))
        except (TypeError, ValueError) as e:
            raise RawError(f"{path.name}: bar {i} is not numeric ({e})") from e
    ts = [int(b["t"]) for b in bars]
    if any(b <= a for a, b in zip(ts, ts[1:])):
        raise RawError(f"{path.name}: bar stamps are not strictly increasing")
    df = pd.DataFrame(rows, columns=["Date", *OHLC]).set_index("Date")
    if df.index.has_duplicates:
        dup = df.index[df.index.duplicated()][0].date()
        raise RawError(f"{path.name}: two bars on session {dup}")
    if df[list(OHLC)].isna().any().any():
        raise RawError(f"{path.name}: a bar carries NaN")
    df = _check_kind(df, sym, path)
    if df.empty:
        raise RawError(f"{path.name}: no bars left after dropping vendor holes")
    return df


def _check_kind(df: pd.DataFrame, sym: Symbol, path: Path) -> pd.DataFrame:
    """Range and shape by kind, applying the two vendor conventions above. Returns
    the frame to keep, which is the input except for dropped ratio holes."""
    lo, hi = _RANGES[sym.kind]
    vals = df[list(OHLC)].to_numpy(dtype=float)
    if (vals < lo).any() or (hi is not None and (vals > hi).any()):
        raise RawError(f"{path.name}: a value is outside the {sym.kind} range "
                       f"[{lo:g}, {'inf' if hi is None else f'{hi:g}'}]: "
                       f"min {vals.min():g}, max {vals.max():g}")
    if sym.kind == "count":
        vals = np.where(vals < COUNT_ZERO_BELOW, 0.0, vals)
        if not np.array_equal(vals, np.round(vals)):
            raise RawError(f"{path.name}: a count is not a whole number")
        df = df.copy()
        df[list(OHLC)] = vals
    if sym.kind == "ratio":
        hole = df["Close"] <= 0
        if hole.any():
            days = ", ".join(d.strftime("%Y-%m-%d") for d in df.index[hole][:5])
            more = f" and {int(hole.sum()) - 5} more" if hole.sum() > 5 else ""
            print(f"{sym.internal}: {path.name}: dropped {int(hole.sum())} bar(s) with a "
                  f"zero close, a vendor hole on a ratio series ({days}{more})")
            df = df.loc[~hole]
        if (df[list(OHLC)].to_numpy(dtype=float) <= 0).any():
            raise RawError(f"{path.name}: a ratio bar has a non-positive open, high or "
                           f"low beside a positive close")
    return df


def check_anchors(df: pd.DataFrame, sym: Symbol, what: str) -> None:
    """Every registry anchor whose date `df` holds must match its Close exactly."""
    for date, value in sym.anchors:
        d = pd.Timestamp(date)
        if d in df.index:
            got = float(df.loc[d, "Close"])
            if not np.isclose(got, value, rtol=0, atol=1e-9):
                raise RawError(f"{what}: anchor {date} is {value:g} in the registry and "
                               f"{got:g} in the data. The symbol string may no longer "
                               f"name the same series; do not build until resolved.")


def merge_raw(sym: Symbol, files: Iterable[Path], *,
              accept: bool = False, log: Optional[list] = None) -> pd.DataFrame:
    """The union of a symbol's raw files, refusing any bar two files disagree on.

    `accept` takes the LATER file's close instead of refusing, which is what
    "accept the vendor's restatement" means at this stage: files are read oldest
    first, so the later one is the newer pull and therefore the vendor's current
    statement. Only an operator naming the symbol turns this on; see `build`.

    Changes are appended to `log` rather than printed, because a later guard can
    still refuse the symbol and write nothing: printing here announced an overwrite
    that a session-gate refusal then never performed.
    """
    merged = None
    for path in files:
        df = parse_raw(path, sym)
        check_anchors(df, sym, path.name)
        if merged is None:
            merged = df
            continue
        common = merged.index.intersection(df.index)
        if len(common):
            a = merged.loc[common, "Close"].to_numpy()
            b = df.loc[common, "Close"].to_numpy()
            bad = ~np.isclose(a, b, rtol=0, atol=1e-9)
            if bad.any():
                if not accept:
                    d = common[bad][0].date()
                    raise RawError(
                        f"{path.name}: session {d} closes at {b[bad][0]:g} here and "
                        f"{a[bad][0]:g} in an earlier raw file for {sym.internal}. This "
                        f"is the guard a vendor restatement trips FIRST, before the "
                        f"store comparison. Once you have checked it against the vendor "
                        f"and it is not a mis-transcription, --accept-restatement "
                        f"{sym.internal} takes the newer file's value.")
                for day, old, now in zip(common[bad], a[bad], b[bad]):
                    (log if log is not None else []).append(
                        f"  ACCEPTED {sym.internal} {day.date()}: raw {old:g} -> "
                        f"{now:g} (from {path.name}, the newer pull)")
                merged.loc[common[bad], "Close"] = b[bad]
        merged = pd.concat([merged, df.loc[df.index.difference(merged.index)]]).sort_index()
    if merged is None:
        raise RawError(f"{sym.internal}: no raw files under {raw_root() / sym.internal}")
    merged.index.name = "Date"
    return merged


# ── The build ─────────────────────────────────────────────────────────────
def _targets(symbols: Optional[Iterable[str]] = None) -> list:
    default = default_price_source()
    wanted = set(symbols) if symbols is not None else None
    return [s for s in all_symbols()
            if s.domain == DOMAIN and s.tradingview and resolve_source(s, default) == NAME
            and (wanted is None or s.internal in wanted)]


def build_symbol(sym: Symbol, expect, *, accept: bool = False) -> int:
    """Validate a symbol's raw files against each other, the store and the
    registry, then append the bars the store lacks. Returns rows written, which
    is 0 when the store already holds everything. Raises `RawError` and writes
    nothing on any refusal.

    `expect` is the session the data must reach (a Timestamp), or `NO_GATE`.
    """
    # Accepted-overwrite lines are held until the write actually happens: the
    # session gate below can still refuse the symbol, and an ACCEPTED line above a
    # refusal reads as history having been rewritten when nothing was touched.
    pending: list = []
    merged = merge_raw(sym, raw_files(sym.internal), accept=accept, log=pending)
    restated = 0
    stored = store.read_bars(sym.internal, DOMAIN, NAME)
    if not stored.empty:
        stored = stored.copy()
        stored.index = pd.to_datetime(stored.index).tz_localize(None).normalize()
        stored = stored.sort_index()
        common = stored.index.intersection(merged.index)
        if len(common):
            a = stored.loc[common, "Close"].to_numpy(dtype=float)
            b = merged.loc[common, "Close"].to_numpy(dtype=float)
            bad = ~np.isclose(a, b, rtol=0, atol=1e-9)
            if bad.any() and not accept:
                d = common[bad][0].date()
                raise RawError(
                    f"{sym.internal}: session {d} closes at {b[bad][0]:g} in the raw "
                    f"files and {a[bad][0]:g} in the store. The store is never rewritten "
                    f"by a build; a vendor restatement or a transcription slip has to be "
                    f"resolved by hand. Once you have decided it is the vendor, "
                    f"--accept-restatement {sym.internal} takes the raw value.")
        new = merged.loc[merged.index.difference(stored.index)]
        out = pd.concat([stored[list(OHLC)], new]).sort_index()
        if accept and len(common) and bad.any():
            # Rewriting stored history, which nothing else in this producer does.
            # Loud by the bar, because a silent overwrite is the failure mode the
            # refusal exists to prevent.
            for day, old, now in zip(common[bad], a[bad], b[bad]):
                pending.append(
                    f"  ACCEPTED {sym.internal} {day.date()}: store {old:g} -> "
                    f"{now:g} (the vendor's current close)")
            out.loc[common[bad], list(OHLC)] = merged.loc[common[bad], list(OHLC)].to_numpy()
            restated = int(bad.sum())
    else:
        new = merged
        out = merged
    out.index.name = "Date"
    newest = out.index.max()
    # The gate has two sides, and neither writes anything. TOO OLD is a pull that
    # has not landed yet, and the routine's later run is the retry. TOO NEW is a
    # bar whose session has not closed: asked before 16:30 ET the vendor serves
    # the day in progress, and on some symbols (the put/call ratios) it serves it
    # a whole bar ahead of the breadth counts. Its Close still moves, and since
    # the build never rewrites a stored bar, storing it once would refuse every
    # later build of that session until a human cleaned the store by hand.
    if expect != NO_GATE:
        want = pd.Timestamp(expect)
        if newest < want:
            raise RawError(
                f"{sym.internal}: newest bar is {newest.date()}, the expected session is "
                f"{want.date()}. Stale; nothing written. The routine's "
                f"later run is the retry.")
        if newest > want:
            raise RawError(
                f"{sym.internal}: newest bar is {newest.date()}, past the expected "
                f"session {want.date()}. That session has not closed, so the bar is "
                f"still moving; nothing written. Pull again after 16:30 ET.")
    check_anchors(out, sym, sym.internal)
    if new.empty and not restated:
        return 0
    for line in pending:
        print(line)
    store.write_bars(sym.internal, out.astype(float), domain=DOMAIN, source=NAME)
    return int(len(new))


def build(symbols: Optional[Iterable[str]] = None, *, expect_session=None,
          accept_restatement: Optional[Iterable[str]] = None) -> dict:
    """Stage 2 for every registry series symbol that resolves to TradingView (scope
    with `symbols`). Local files only. Returns the producer result dict the CLI
    prints: ``{kind, ok, partial, wrote, failed, errors}``; `ok` is False on any
    refusal, and a symbol with no raw files at all is a refusal, because the
    routine pulls every registry series symbol every night and a missing one
    means it did not.

    ``partial`` is True when SOME symbols refused and others did not. It exists
    for the wrapper, which must tell two situations apart that both used to be
    "exit non-zero": every symbol refused, where there is nothing to deliver and
    the later routine is the retry; and a couple refused while the rest landed in
    the store, where withholding the replica sync punishes the symbols that
    worked. That second case froze the whole domain on 2026-09-25 -- the vendor
    restated two put/call closes, the build refused those two by design, the
    wrapper exited on the non-zero code before its syncs, and thirteen breadth
    series sat correct on the producer and stale on both replicas until someone
    looked. A refusal must stop the symbol, not the delivery.

    A symbol that was already current counts as succeeding: the question the flag
    answers is whether every symbol refused, not whether this run wrote rows.

    ``accept_restatement`` names the symbols allowed to overwrite stored bars with
    the raw value, which is how a vendor restatement gets resolved. It is a LIST,
    never a blanket switch, and the default is empty, because the overlap check
    catches two different things that look identical in the file: the vendor
    restating a close, and the routine's model mis-transcribing one. Only a person
    who has compared the two can tell them apart, so the refusal stays the default
    and accepting is a named, one-off act.
    """
    targets = _targets(symbols)
    if not targets:
        return {"kind": "series_tradingview", "ok": True, "partial": False,
                "wrote": 0, "failed": 0, "errors": []}
    expect = expected_session() if expect_session is None else (
        NO_GATE if expect_session == NO_GATE else pd.Timestamp(expect_session))
    accept = set(accept_restatement or ())
    wrote = failed = 0
    errors = []
    for s in targets:
        try:
            n = build_symbol(s, expect, accept=s.internal in accept)
        except RawError as e:
            print(f"{s.internal}: REFUSED -- {e}")
            failed += 1
            errors.append((s.internal, str(e)))
            continue
        if n:
            wrote += 1
            print(f"{s.internal}: +{n} bar(s) ({s.tradingview}) -> store")
        else:
            print(f"{s.internal}: already current ({s.tradingview})")
    return {"kind": "series_tradingview", "ok": failed == 0,
            "partial": 0 < failed < len(targets), "wrote": wrote,
            "failed": failed, "errors": errors}
