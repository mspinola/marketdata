"""The consumer API: bars out of the store, at the adjustment tier you ask for.

Reads are local and never hit the network. This is the only function most
consumers (a crucible study, a RealTest export) should need.

The DOMAIN is a registry fact, not an argument. `get_bars("ES", "backadj")` and
`get_bars("SPY", "total")` both just work, and asking for a futures tier on an
equity symbol raises a message naming the right ones.
"""
from __future__ import annotations

from typing import Optional

import pandas as pd

from . import store
from .adjust import adjust, check_tier, tiers_for
from .registry import REGISTRY, default_price_source, domain_for, resolve_source

_COLS = ("Open", "High", "Low", "Close", "Volume")


def default_source_for(symbol: str) -> str:
    """Which vendor this deployment reads for `symbol`. Registry resolution when
    the symbol is known, the deployment default otherwise (the store is
    registry-free, so a one-off symbol still reads)."""
    sym = REGISTRY.get(symbol)
    default = default_price_source()
    return (resolve_source(sym, default) or default) if sym else default


def get_bars(symbol: str, adjustment: Optional[str] = None, *,
             source: Optional[str] = None, domain: Optional[str] = None,
             start: Optional[str] = None, end: Optional[str] = None,
             include_capital_gains: bool = False) -> pd.DataFrame:
    """Daily bars for `symbol`, adjusted to `adjustment`.

    Equity and ETF tiers:
      'split' : as stored — split-adjusted, dividends not reinvested. The
                continuous price series. Default, and the right input for a
                price-based signal.
      'raw'   : as-traded. Splits un-applied, volume un-adjusted with them.
      'total' : split- and dividend-adjusted total return. Use this whenever a
                hold spans an ex-date and the return is what you are measuring.

    `domain` is resolved from the registry and rarely passed. `source` pins the
    vendor. Omit it and the registry resolves one for this deployment. Pass it
    explicitly to compare vendors on the same symbol, which is the point of
    keeping their series in separate files.

    Slicing happens AFTER adjustment, so a windowed request returns the same
    numbers as the same window taken out of the full series. Raises if the symbol
    is absent from the chosen vendor but present under another, rather than
    returning an empty frame that reads as "no data".
    """
    dom = domain or domain_for(symbol)
    adjustment = adjustment or tiers_for(dom)[0]
    check_tier(adjustment, dom)
    src = source or default_source_for(symbol)

    df = store.read_bars(symbol, dom, src)
    if df.empty:
        others = [s for s in store.sources_for(symbol, dom) if s != src]
        if others:
            raise FileNotFoundError(
                f"{symbol} is not in the store under source {src!r} (domain "
                f"{dom!r}), but it is under {others}. Pass source= explicitly, "
                f"or set MARKETDATA_PRICE_SOURCE. Vendors are never silently "
                f"substituted.")
        return df

    df.index = pd.to_datetime(df.index).tz_localize(None).normalize()
    df.index.name = "Date"
    df = df.sort_index()

    out = adjust(df, adjustment, include_capital_gains=include_capital_gains)
    if start is not None:
        out = out[out.index >= pd.Timestamp(start)]
    if end is not None:
        out = out[out.index <= pd.Timestamp(end)]
    return out


def available(domain: Optional[str] = None, source: Optional[str] = None) -> dict:
    """What the store holds, as {domain: {source: [symbol, ...]}}."""
    from . import config
    root = config.store_root() / "bars"
    if not root.exists():
        return {}
    out: dict = {}
    for dom_dir in sorted(root.iterdir()):
        if not dom_dir.is_dir() or (domain and dom_dir.name != domain):
            continue
        per_source = {}
        for src_dir in sorted(dom_dir.iterdir()):
            if not src_dir.is_dir() or (source and src_dir.name != source):
                continue
            syms = sorted(p.stem for p in src_dir.glob("*.parquet"))
            if syms:
                per_source[src_dir.name] = syms
        if per_source:
            out[dom_dir.name] = per_source
    return out
