"""Cboe index histories from Cboe's own daily CSV, for the volatility indices.

Why a fourth vendor for four symbols. Yahoo carries the Cboe term-structure indices
(VIX3M, VIX6M, VIX9D) but serves them PATCHILY: measured 2026-09-14, a full-history
pull of ^VIX3M ended on 2026-07-17 while a three-month pull returned 24 rows against
64 for ^VIX over the same span. A ratio built on that is a ratio with holes and a
stale tail, and the tape-context row that reads VIX/VIX3M spent two months trailing
the board because of it. Cboe publishes the same indices as a complete daily OHLC CSV
with no key and no account, which is the series of record.

Equities domain, like the Yahoo-served VIX: an index has no corporate actions, so the
three equity tiers are a passthrough, and `Volume` is written as 0 because an index
trades nothing, the same frame shape Yahoo's ``^VIX`` arrives in. Dividends, splits
and capital gains are zero for the same reason. A symbol is served here only when the
registry names a ``cboe`` key for it AND resolves it to this vendor, which in practice
means an explicit ``price_source: cboe``: Yahoo still serves these tickers, so without
the pin the deployment default would win and the hole would be back.

The file starts where Cboe's history does (VIX3M from 2009-09-18, VIX from 1990) and
is served from a CDN, so a fetch is one GET of a few hundred KB. Runs anywhere with
outbound HTTPS, the Windows producer included, on the equities task.
"""
from __future__ import annotations

import io
import urllib.request
from typing import Iterable, Optional

import pandas as pd

from .. import store
from ..registry import all_symbols, default_price_source, resolve_source

# MUST match the registry's PRICE_SOURCES entry: the store path component and what
# `resolve_source` returns.
NAME = "cboe"
DOMAIN = "equities"

URL = "https://cdn.cboe.com/api/global/us_indices/daily_prices/{symbol}_History.csv"

_COLMAP = {"DATE": "Date", "OPEN": "Open", "HIGH": "High", "LOW": "Low", "CLOSE": "Close"}
# The equities store frame: what yfinance writes, so one reader serves both.
KEEP = ("Open", "High", "Low", "Close", "Volume",
        "Dividends", "Stock Splits", "Capital Gains")


def _download(cboe_symbol: str, timeout: int = 60) -> str:
    with urllib.request.urlopen(URL.format(symbol=cboe_symbol), timeout=timeout) as r:
        return r.read().decode("utf-8")


def parse(text: str) -> pd.DataFrame:
    """Cboe's CSV (DATE,OPEN,HIGH,LOW,CLOSE; US dates) as the stored equity frame."""
    raw = pd.read_csv(io.StringIO(text))
    raw.columns = [c.strip().upper() for c in raw.columns]
    missing = [c for c in _COLMAP if c not in raw.columns]
    if missing:
        raise ValueError(f"Cboe CSV lacks columns {missing}; got {list(raw.columns)}")
    df = raw.rename(columns=_COLMAP)
    df["Date"] = pd.to_datetime(df["Date"], format="%m/%d/%Y")
    df = df.set_index("Date").sort_index()
    for c in ("Open", "High", "Low", "Close"):
        df[c] = pd.to_numeric(df[c], errors="coerce")
    df["Volume"] = 0.0
    for c in ("Dividends", "Stock Splits", "Capital Gains"):
        df[c] = 0.0
    df.index.name = "Date"
    return df[list(KEEP)].dropna(subset=["Close"])


def fetch(cboe_symbol: str) -> pd.DataFrame:
    """Full available history for one Cboe index, as stored."""
    return parse(_download(cboe_symbol))


def _targets(symbols: Optional[Iterable[str]] = None) -> list:
    default = default_price_source()
    wanted = set(symbols) if symbols is not None else None
    return [s for s in all_symbols()
            if s.cboe and resolve_source(s, default) == NAME
            and (wanted is None or s.internal in wanted)]


def update(symbols: Optional[Iterable[str]] = None) -> dict:
    """Fetch every registry symbol that RESOLVES to Cboe on this deployment.
    Pass `symbols` to scope. Returns {kind, ok, wrote, failed}."""
    targets = _targets(symbols)
    if not targets:
        return {"kind": "bars_cboe", "ok": True, "wrote": 0, "failed": 0}

    wrote = failed = 0
    for s in targets:
        try:
            df = fetch(s.cboe)
        except Exception as e:  # noqa: BLE001 -- network, like yfinance
            print(f"{s.internal}: cboe fetch failed ({s.cboe}) -- {e}")
            failed += 1
            continue
        if df.empty:
            print(f"{s.internal}: cboe returned no data ({s.cboe})")
            failed += 1
            continue
        store.write_bars(s.internal, df, domain=DOMAIN, source=NAME)
        wrote += 1
        print(f"{s.internal}: {len(df):6d} bars ({s.cboe}) "
              f"{df.index.min().date()}..{df.index.max().date()} -> store")
    return {"kind": "bars_cboe", "ok": failed == 0, "wrote": wrote, "failed": failed}
