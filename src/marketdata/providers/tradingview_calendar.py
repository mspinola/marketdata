"""TradingView's US economic calendar: a snapshot table, not a series.

WHAT IT IS. The connector tool ``mcp-tv-get-economic-calendar`` returns scheduled
macro releases (CPI, payrolls, the FOMC decision, regional Fed surveys, auctions)
with a UTC release time, an importance flag, and the consensus forecast, the
previous reading and, once released, the actual. cot-analyzer's internals page shows
this week and next from it, as context. Scoping and the check against a published
member calendar (Nov 16-20 2026, every figure matched) are in this repo's
``docs/design/econ-calendar.md``.

TWO STAGES, the same shape as :mod:`.tradingview` (breadth): a Claude Desktop routine
on the producer box makes the connector call and puts the result, unchanged, at
``<raw root>/_calendar/<YYYY-MM-DD>.json`` (the raw root is the breadth producer's,
``_raw/tradingview`` under the store, so the replica syncs' ``_raw`` exclusion
already keeps captures off the Mac and the VPS). :func:`build` reads only that local
file. Nothing here opens a socket.

WHY A SNAPSHOT, REWRITTEN EVERY BUILD. A calendar row is not a fact until it has
happened: the forecast moves as economists revise, the release time can move, and the
actual arrives on the day. So each build replaces ``metadata/econ_calendar.parquet``
with the newest capture in full, and the table means "the calendar as of the capture
date" (its ``captured`` column says which). There is no history and no overlap check
to keep, which is why it lives under ``metadata/`` beside the contract specs rather
than under ``bars/``. A study that wanted surprise history (actual minus forecast as
it stood the night before) would keep the raw captures, not this table.

WHAT IS REFUSED. A file that is not the connector's shape (an object whose
``result`` is a list of event objects, each with an id, an ISO UTC ``date``, a title,
a category and an importance of -1, 0 or 1, and numeric-or-null actual, forecast and
previous), a duplicate event id, or an empty result. Every refusal names the file and
the event. A refused build writes nothing, so the table on disk stays the last good
snapshot and its ``captured`` date shows how old it is.

THE RAW LAYOUT IS SHARED. ``_calendar/`` sits beside the breadth symbols' folders
under the TradingView raw root. The leading underscore marks it as a capture KIND
rather than a registry symbol (no registry internal starts with one), and leaves room
for ``_news/`` beside it when TradingView news captures move onto the store.

WHAT IT REFUSES TO BE. Not a curator: every US event in the capture is stored,
auctions and rig counts included. Choosing which ones a reader sees, and how to
describe them, is the consumer's job (cot-analyzer keeps an allowlist keyed by
``ticker``). Not a Fed calendar: FOMC minutes and Fed speeches are not in the feed at
any importance (checked on the 2026-11-19 capture, importance -1 included), and this
module does not invent them.
"""
from __future__ import annotations

import datetime as dt
import json
import math
import re
from pathlib import Path
from typing import Optional

import pandas as pd

from .. import config, store
from .tradingview import raw_root

NAME = "tradingview"
CAPTURE_DIR = "_calendar"
TABLE = "econ_calendar"

_FILE_RE = re.compile(r"^(\d{4}-\d{2}-\d{2})\.json$")
_REQUIRED = ("id", "date", "title", "category", "importance")
_NUMERIC = ("actual", "forecast", "previous")
_IMPORTANCE = (-1, 0, 1)

# Stored columns, in order. The vendor's long ``comment`` prose is deliberately not
# carried: a reader describes an event in its own words.
COLUMNS = ("id", "time_utc", "title", "indicator", "ticker", "category", "importance",
           "period", "reference_date", "actual", "forecast", "previous", "unit",
           "scale", "country", "currency", "source", "captured")


class RawError(ValueError):
    """A calendar capture the build refuses. The message names the file."""


def capture_dir(kind: str = CAPTURE_DIR) -> Path:
    """Where one capture kind lives under the TradingView raw root."""
    if not kind.startswith("_"):
        raise ValueError(f"capture kinds start with '_' to stay apart from registry "
                         f"symbols: {kind!r}")
    return raw_root() / kind


def table_path() -> Path:
    return config.metadata_dir() / f"{TABLE}.parquet"


def newest_capture() -> Optional[Path]:
    """The capture with the latest date in its name, or None. Files not named
    ``YYYY-MM-DD.json`` (a temp file, a note) are ignored, not refused."""
    d = capture_dir()
    if not d.is_dir():
        return None
    found = sorted(p for p in d.iterdir() if p.is_file() and _FILE_RE.match(p.name))
    return found[-1] if found else None


def _num(v, field: str, where: str):
    if v is None:
        return None
    if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v):
        raise RawError(f"{where}: {field} is {v!r}, expected a number or null")
    return float(v)


def _text(v):
    return None if v is None else str(v)


def parse_capture(path) -> pd.DataFrame:
    """One capture file to the stored frame, or :class:`RawError` naming the file."""
    path = Path(path)
    m = _FILE_RE.match(path.name)
    if not m:
        raise RawError(f"{path}: a capture is named YYYY-MM-DD.json")
    try:
        captured = pd.Timestamp(dt.date.fromisoformat(m.group(1)))
    except ValueError as e:
        raise RawError(f"{path}: the name is not a date ({e})") from None
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, UnicodeDecodeError) as e:
        raise RawError(f"{path}: not readable JSON ({e})") from None
    if not isinstance(doc, dict) or not isinstance(doc.get("result"), list):
        raise RawError(f"{path}: expected the connector's object with a 'result' list")
    if "status" in doc and doc["status"] != "ok":
        raise RawError(f"{path}: status is {doc['status']!r}, not 'ok'")
    events = doc["result"]
    if not events:
        raise RawError(f"{path}: 'result' is empty; a 4-week US window always has "
                       f"events, so this is a failed call, not a quiet calendar")

    rows, seen = [], set()
    for i, e in enumerate(events):
        where = f"{path} event {i}"
        if not isinstance(e, dict):
            raise RawError(f"{where}: not an object")
        missing = [k for k in _REQUIRED if e.get(k) in (None, "")]
        if missing:
            raise RawError(f"{where}: missing {', '.join(missing)}")
        eid = str(e["id"])
        where = f"{path} event {eid} ({e['title']})"
        if eid in seen:
            raise RawError(f"{where}: duplicate id")
        seen.add(eid)
        try:
            # fromisoformat, not pd.Timestamp: pandas reads "Nov 19" as a date. The
            # Z swap is for Python 3.10, whose fromisoformat does not take it.
            iso = re.sub(r"Z$", "+00:00", str(e["date"]))
            t = pd.Timestamp(dt.datetime.fromisoformat(iso))
        except (ValueError, TypeError):
            raise RawError(f"{where}: date {e['date']!r} is not ISO") from None
        if t.tzinfo is None:
            raise RawError(f"{where}: date {e['date']!r} carries no zone")
        imp = e["importance"]
        if isinstance(imp, bool) or imp not in _IMPORTANCE:
            raise RawError(f"{where}: importance {imp!r} is not -1, 0 or 1")
        ref = e.get("referenceDate")
        rows.append({
            "id": eid,
            "time_utc": t.tz_convert("UTC"),
            "title": str(e["title"]),
            "indicator": _text(e.get("indicator")),
            "ticker": _text(e.get("ticker")),
            "category": str(e["category"]),
            "importance": int(imp),
            "period": _text(e.get("period")),
            "reference_date": pd.Timestamp(ref).tz_localize(None).normalize()
                              if ref else pd.NaT,
            **{k: _num(e.get(k), k, where) for k in _NUMERIC},
            "unit": _text(e.get("unit")),
            "scale": _text(e.get("scale")),
            "country": _text(e.get("country")),
            "currency": _text(e.get("currency")),
            "source": _text(e.get("source")),
            "captured": captured,
        })
    df = pd.DataFrame(rows, columns=list(COLUMNS))
    for k in _NUMERIC:
        df[k] = df[k].astype("float64")
    df["reference_date"] = pd.to_datetime(df["reference_date"])
    return df.sort_values(["time_utc", "id"]).reset_index(drop=True)


def build(path=None) -> dict:
    """Replace the calendar table with the newest capture (or ``path``).

    Returns the CLI's result dict (``errors`` as ``(name, message)`` pairs). A missing capture is a refusal (the routine did not
    run), and so is a bad one; either way the table on disk is left as it was.
    """
    src = Path(path) if path else newest_capture()
    if src is None:
        msg = f"no capture under {capture_dir()}; the calendar routine has not run"
        print(f"econ calendar: REFUSED -- {msg}")
        return {"kind": "econ_calendar", "ok": False, "wrote": 0, "failed": 1,
                "errors": [(TABLE, msg)]}
    try:
        df = parse_capture(src)
    except RawError as e:
        print(f"econ calendar: REFUSED -- {e}")
        return {"kind": "econ_calendar", "ok": False, "wrote": 0, "failed": 1,
                "errors": [(TABLE, str(e))]}
    store._atomic_write_parquet(df, table_path())
    store._touch_manifest("metadata", TABLE, df.set_index("time_utc"), NAME)
    span = f"{df['time_utc'].min():%Y-%m-%d} to {df['time_utc'].max():%Y-%m-%d}"
    print(f"econ calendar: {len(df)} events, {span}, captured "
          f"{df['captured'].iloc[0]:%Y-%m-%d} ({src.name}) -> {table_path()}")
    return {"kind": "econ_calendar", "ok": True, "wrote": len(df), "failed": 0,
            "errors": []}


def read() -> pd.DataFrame:
    """The stored snapshot, or an EMPTY frame when no build has run. Empty rather than
    an error because every consumer so far treats the calendar as optional context."""
    p = table_path()
    return pd.read_parquet(p) if p.exists() else pd.DataFrame(columns=list(COLUMNS))
