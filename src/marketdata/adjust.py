"""Corporate-action adjustment: stored inputs in, the requested price series out.

The store holds ONE frame per symbol, exactly as the vendor serves it, plus the
dated action columns. Every adjustment tier is DERIVED here, never stored. That
is the whole point: a vendor's own adjusted column (Yahoo's ``Adj Close``) is
restated every time a new dividend lands, so a backtest pinned to it is not
reproducible. Raw bars plus dated actions are immutable facts, and this module
turns them back into any tier deterministically.

Same shape as cotdata's ``propadj``: stored inputs, versioned derivation.

The three tiers
---------------
``split``  the stored frame as-is. Split-adjusted, dividends NOT reinvested.
           Continuous price series. Use for price-return signals.
``raw``    as-traded. Splits un-applied, so bars match what printed on the tape.
           Use for price-level logic (round numbers, tick size) and for handing
           to an engine that models dividends itself.
``total``  split- AND dividend-adjusted total return. Use whenever a hold spans
           an ex-date and you care about the actual return (TLT, any bond fund,
           any dividend payer held for weeks).

The critical asymmetry (verified, see docs/design.md)
-----------------------------------------------------
Yahoo's ``history(auto_adjust=False)`` OHLC is ALREADY split-adjusted. It is not
as-traded. So:

  * ``raw``   must UN-apply splits (multiply by the cumulative forward ratio).
  * ``total`` must apply dividends ONLY. Including a split term double-applies
    the split and produces ~99% error on any symbol that has ever split.

A symbol that has never split (TLT, SPY) hides that second bug completely, which
is exactly why :data:`PIN_SYMBOLS` includes split-heavy names.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

# Bump when the derivation changes in a way that alters output. Consumers that
# cache derived series should key on this.
ADJUSTMENT_VERSION = 1

TIERS = ("split", "raw", "total")

# The adjustment axis is a property of the DOMAIN, not of the package. Futures
# adjust by roll splicing and equities by corporate actions, and the two share no
# vocabulary. Keeping the map here means asking for a futures tier on an equity
# symbol raises a message naming the right ones, instead of silently doing
# something plausible. The futures entry is declared before its provider exists so
# the error message is right from the first day rather than "unknown tier".
DOMAIN_TIERS = {
    "equities": ("split", "raw", "total"),
    "futures": ("backadj", "unadj", "propadj"),
}


def tiers_for(domain: str) -> tuple:
    if domain not in DOMAIN_TIERS:
        raise ValueError(
            f"unknown domain {domain!r}; expected one of {tuple(DOMAIN_TIERS)}")
    return DOMAIN_TIERS[domain]


def check_tier(tier: str, domain: str) -> None:
    """Raise unless `tier` belongs to `domain`'s adjustment axis."""
    valid = tiers_for(domain)
    if tier not in valid:
        other = next((d for d, ts in DOMAIN_TIERS.items() if tier in ts), None)
        hint = f" ({tier!r} is a {other} adjustment)" if other else ""
        raise ValueError(
            f"adjustment {tier!r} is not valid for domain {domain!r}{hint}. "
            f"Valid: {valid}.")

OHLC = ("Open", "High", "Low", "Close")
# Columns the store carries verbatim from the vendor.
ACTION_COLS = ("Dividends", "Stock Splits", "Capital Gains")


def _splits(df: pd.DataFrame) -> np.ndarray:
    """Split ratios as a multiplicative array, 1.0 on non-split bars."""
    if "Stock Splits" not in df.columns:
        return np.ones(len(df))
    s = df["Stock Splits"].to_numpy(dtype=float)
    return np.where(np.isfinite(s) & (s > 0), s, 1.0)


def cumulative_split_factor(df: pd.DataFrame) -> np.ndarray:
    """Per-bar factor that converts split-adjusted prices back to as-traded.

    Bar ``i`` was divided by every split effective AFTER ``i``, so multiplying by
    the product of those ratios restores the tape price. The split bar itself
    already trades post-split, so its own ratio is excluded.
    """
    spl = _splits(df)
    if len(spl) == 0:
        return np.ones(0)
    # Reverse cumulative product, then shift so bar i excludes its own split.
    cum = np.cumprod(spl[::-1])[::-1]
    return np.r_[cum[1:], 1.0]


def dividend_factor(df: pd.DataFrame, include_capital_gains: bool = False) -> np.ndarray:
    """Per-bar back-adjustment factor for cash distributions.

    Standard CRSP-style construction: walk backwards from the most recent bar,
    and on each ex-dividend bar scale prior history by ``1 - div / prior_close``.
    The PRIOR bar's close is the denominator, which is the convention that
    reproduces Yahoo's ``Adj Close``.

    No split term. Yahoo's ``Close`` is already split-adjusted (see module
    docstring), so applying one here would double-count.

    ``include_capital_gains`` folds the ``Capital Gains`` column into the
    distribution. Off by default: that column exists in Yahoo's payload but was
    never observed to be populated (see docs/design.md), so switching it on
    changes nothing for the symbols checked and is untrusted for the rest.
    """
    n = len(df)
    if n == 0:
        return np.ones(0)
    close = df["Close"].to_numpy(dtype=float)
    div = (df["Dividends"].to_numpy(dtype=float)
           if "Dividends" in df.columns else np.zeros(n))
    div = np.nan_to_num(div, nan=0.0)
    if include_capital_gains and "Capital Gains" in df.columns:
        div = div + np.nan_to_num(df["Capital Gains"].to_numpy(dtype=float), nan=0.0)

    factor = np.ones(n)
    f = 1.0
    for i in range(n - 1, 0, -1):
        if div[i] > 0 and close[i - 1] > 0:
            f *= 1.0 - div[i] / close[i - 1]
        factor[i - 1] = f
    return factor


def _scaled(df: pd.DataFrame, price_factor: np.ndarray,
            volume_factor: np.ndarray | None = None) -> pd.DataFrame:
    out = df.copy()
    for c in OHLC:
        if c in out.columns:
            out[c] = out[c].to_numpy(dtype=float) * price_factor
    if volume_factor is not None and "Volume" in out.columns:
        out["Volume"] = out["Volume"].to_numpy(dtype=float) * volume_factor
    return out


def to_raw(df: pd.DataFrame) -> pd.DataFrame:
    """As-traded bars: un-apply splits. Volume moves the other way."""
    f = cumulative_split_factor(df)
    return _scaled(df, f, volume_factor=1.0 / f)


def to_total(df: pd.DataFrame, include_capital_gains: bool = False) -> pd.DataFrame:
    """Total-return bars: split-adjusted (as stored) plus dividends reinvested."""
    f = dividend_factor(df, include_capital_gains=include_capital_gains)
    return _scaled(df, f)


def adjust(df: pd.DataFrame, tier: str = "split", *,
           include_capital_gains: bool = False) -> pd.DataFrame:
    """Derive one tier from a stored frame. See module docstring for the tiers."""
    if tier not in TIERS:
        raise ValueError(f"tier must be one of {TIERS}, got {tier!r}")
    if df.empty:
        return df
    if tier == "split":
        return df.copy()
    if tier == "raw":
        return to_raw(df)
    return to_total(df, include_capital_gains=include_capital_gains)


# Symbols the pin test reconstructs against the vendor's own adjusted column.
# Chosen so the split path is actually exercised: TLT/SPY have never split and
# would pass even with the split-term bug this module exists to avoid.
PIN_SYMBOLS = ("AAPL", "MSFT", "KO", "TLT", "SPY")
