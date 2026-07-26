"""yfinance provider — free Yahoo OHLCV + corporate actions.

Research-grade. Yahoo is a free, unofficial feed: expect gaps, silent revisions,
and API breakage. Not a production replacement for a paid vendor.

What this provider deliberately does NOT do
-------------------------------------------
It does not store ``Adj Close``, and it does not fetch with ``auto_adjust=True``.
Yahoo's adjusted column is RESTATED every time a new dividend lands, so the whole
back-history shifts and a backtest pinned to it is not reproducible. Raw-ish bars
plus dated actions are immutable facts. ``marketdata.adjust`` rebuilds any tier from
them, reproducing Yahoo's own ``Adj Close`` to ~1e-6 (see tests/test_pin.py).

Note the OHLC that comes back under ``auto_adjust=False`` is already
SPLIT-adjusted, not as-traded. That is a Yahoo behaviour, verified, and it is why
``adjust.to_raw`` exists and why ``adjust.dividend_factor`` carries no split term.
"""
from __future__ import annotations

from typing import Iterable, Optional

import pandas as pd

from .. import store
from ..registry import all_symbols, default_price_source, resolve_source

# MUST match the registry's PRICE_SOURCES entry for this provider. The source is
# a store PATH component and the value `resolve_source` returns, so one vocabulary
# governs both or a default read looks in a directory nothing wrote to.
NAME = "yfinance"
# This provider serves listed securities only. A futures provider writes its own.
DOMAIN = "equities"

# Stored verbatim. 'Adj Close' is excluded on purpose (see module docstring).
KEEP = ("Open", "High", "Low", "Close", "Volume",
        "Dividends", "Stock Splits", "Capital Gains")


def fetch(ticker: str) -> pd.DataFrame:
    """Full available history for one Yahoo ticker, as stored.

    Returns an empty frame if Yahoo serves nothing. Index is a tz-naive
    DatetimeIndex named 'Date' (Yahoo returns exchange-local tz-aware stamps;
    dropping the tz keeps the store source-agnostic and comparable across
    vendors).
    """
    import yfinance as yf

    raw = yf.Ticker(ticker).history(period="max", auto_adjust=False, actions=True)
    if raw is None or raw.empty:
        return pd.DataFrame()
    if isinstance(raw.columns, pd.MultiIndex):
        raw.columns = raw.columns.get_level_values(0)

    df = raw[[c for c in KEEP if c in raw.columns]].copy()
    for c in ("Dividends", "Stock Splits", "Capital Gains"):
        if c not in df.columns:
            df[c] = 0.0
        df[c] = df[c].fillna(0.0)

    # Yahoo returns exchange-local tz-aware stamps; dropping the tz keeps the
    # local trading date, which is what a daily bar means.
    idx = pd.to_datetime(df.index)
    if idx.tz is not None:
        idx = idx.tz_localize(None)
    df.index = idx.normalize()
    df.index.name = "Date"
    return df.dropna(subset=["Close"]).sort_index()


def update(symbols: Optional[Iterable[str]] = None) -> dict:
    """Fetch every registry symbol that RESOLVES to yfinance on this deployment.
    Pass `symbols` to scope. Returns {kind, ok, wrote, failed}."""
    default = default_price_source()
    wanted = set(symbols) if symbols is not None else None
    targets = [s for s in all_symbols()
               if s.yahoo and resolve_source(s, default) == "yfinance"
               and (wanted is None or s.internal in wanted)]
    if not targets:
        print("yfinance: no registry symbols resolve to this provider"
              + (f" among {sorted(wanted)}" if wanted else ""))
        return {"kind": "bars_yahoo", "ok": True, "wrote": 0, "failed": 0}

    wrote = failed = 0
    for s in targets:
        try:
            df = fetch(s.yahoo)
        except Exception as e:  # noqa: BLE001 — yfinance/network is flaky by nature
            print(f"{s.internal}: yfinance fetch failed ({s.yahoo}) — {e}")
            failed += 1
            continue
        if df.empty:
            print(f"{s.internal}: yfinance returned no data ({s.yahoo})")
            failed += 1
            continue
        store.write_bars(s.internal, df, domain=DOMAIN, source=NAME)
        wrote += 1
        n_div = int((df["Dividends"] > 0).sum())
        n_spl = int((df["Stock Splits"] > 0).sum())
        print(f"{s.internal}: {len(df):6d} bars ({s.yahoo}) "
              f"{df.index.min().date()}..{df.index.max().date()} "
              f"div={n_div} split={n_spl} -> store")
    return {"kind": "bars_yahoo", "ok": failed == 0, "wrote": wrote, "failed": failed}
