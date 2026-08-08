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
from .adjust import adjust, check_tier, ratio_adjust, stored_tiers_for, tiers_for
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

    Futures tiers:
      'backadj' : additive (arithmetic) back-adjustment, as Norgate computes it.
                  Gap-free rolls, settlement close, preserves absolute daily
                  price CHANGES. Default, and the right input for signals and
                  stops. STORED.
      'unadj'   : raw front-month prices, real calendar-spread gaps at each roll.
                  For absolute price level and point-value sizing. STORED.
      'propadj' : proportional (ratio) back-adjustment, DERIVED on read from the
                  two stored tiers. Preserves daily PERCENT returns, so it is the
                  series to use for volatility and for any long-history contract
                  where additive adjustment has driven the back-history through
                  zero (ZS, DC). See `adjust.ratio_adjust`.

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

    if dom == "futures":
        out = _futures_bars(symbol, src, adjustment)
    else:
        out = _equity_bars(symbol, dom, src, adjustment,
                           include_capital_gains=include_capital_gains)
    if out.empty:
        return out

    if start is not None:
        out = out[out.index >= pd.Timestamp(start)]
    if end is not None:
        out = out[out.index <= pd.Timestamp(end)]
    return out


def _normalized(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df.index = pd.to_datetime(df.index).tz_localize(None).normalize()
    df.index.name = "Date"
    return df.sort_index()


def _missing(symbol: str, dom: str, src: str, tier=None) -> None:
    """Raise if the series exists under a DIFFERENT vendor. Silence (return) when
    it exists nowhere, so a genuinely absent symbol still reads as an empty frame
    rather than an error."""
    others = [s for s in store.sources_for(symbol, dom, tier) if s != src]
    if others:
        what = f"{symbol} ({tier})" if tier else symbol
        raise FileNotFoundError(
            f"{what} is not in the store under source {src!r} (domain {dom!r}), "
            f"but it is under {others}. Pass source= explicitly, or set "
            f"MARKETDATA_PRICE_SOURCE. Vendors are never silently substituted.")


def _equity_bars(symbol: str, dom: str, src: str, tier: str, *,
                 include_capital_gains: bool) -> pd.DataFrame:
    """One stored frame, every tier derived from it."""
    df = store.read_bars(symbol, dom, src)
    if df.empty:
        _missing(symbol, dom, src)
        return df
    return adjust(_normalized(df), tier,
                  include_capital_gains=include_capital_gains)


def _futures_bars(symbol: str, src: str, tier: str) -> pd.DataFrame:
    """Two stored frames; `propadj` derived from both.

    The half-stored case gets its own message rather than an empty frame. A
    consumer that needs `propadj` (position sizing off percent volatility) is
    exactly the one that must not silently receive nothing when only `backadj`
    landed: additive back-adjusted returns are wrong by ~200x on soybeans and
    0.47x on gold, and the second of those passes every implausibility screen a
    spot check would apply.
    """
    if tier in stored_tiers_for("futures"):
        df = store.read_bars(symbol, "futures", src, tier)
        if df.empty:
            _missing(symbol, "futures", src, tier)
            return df
        return _normalized(df)

    unadj = store.read_bars(symbol, "futures", src, "unadj")
    backadj = store.read_bars(symbol, "futures", src, "backadj")
    if unadj.empty and backadj.empty:
        _missing(symbol, "futures", src, "backadj")
        return pd.DataFrame()
    if unadj.empty or backadj.empty:
        have, missing = ("backadj", "unadj") if unadj.empty else ("unadj", "backadj")
        raise FileNotFoundError(
            f"{symbol}: 'propadj' is derived from BOTH stored futures tiers, and "
            f"only {have!r} is in the store under source {src!r} ({missing!r} is "
            f"missing). Re-run the futures producer for this symbol — it writes "
            f"both tiers or neither, so a half-written symbol means a failed run.")
    return ratio_adjust(_normalized(unadj), _normalized(backadj))


def _symbol_of(stem: str, dom: str) -> str:
    """Strip a stored-tier suffix off a filename stem, so `available()` answers in
    SYMBOLS in every domain. Without this, futures would report 'ES_backadj' and
    'ES_unadj' and every caller would have to re-parse the store's file naming."""
    for tier in stored_tiers_for(dom) if dom in ("equities", "futures") else ():
        if tier and stem.endswith(f"_{tier}"):
            return stem[: -len(tier) - 1]
    return stem


def available(domain: Optional[str] = None, source: Optional[str] = None) -> dict:
    """What the store holds, as {domain: {source: [symbol, ...]}}.

    Symbols, not stored series: a futures symbol appears once even though it has
    a `backadj` and an `unadj` file behind it.
    """
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
            syms = sorted({_symbol_of(p.stem, dom_dir.name)
                           for p in src_dir.glob("*.parquet")})
            if syms:
                per_source[src_dir.name] = syms
        if per_source:
            out[dom_dir.name] = per_source
    return out
