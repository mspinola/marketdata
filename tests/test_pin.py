"""The pin test: our derivation must reproduce the vendor's own adjusted column.

This is the test that lets us DROP ``Adj Close`` from the store. If we can rebuild
it from ``Close + Dividends`` to within float noise, then storing the restated
column buys nothing and costs reproducibility.

Hits the network. Deselect with ``-m 'not network'`` or set MARKETDATA_NO_NETWORK.
"""
import numpy as np
import pytest

from marketdata.adjust import PIN_SYMBOLS, cumulative_split_factor, dividend_factor

# Observed max relative error, yfinance 1.5.2, fetched 2026-07-25:
#   AAPL 9.49e-07   MSFT 1.04e-06   KO 3.35e-05   TLT 1.58e-06   SPY 1.13e-06
#
# KO is the loose one: 16k bars back to 1962 and 258 dividends, so Yahoo's own
# stored Adj Close accumulates rounding across a long multiplicative chain. The
# tolerance is set above that, not tuned down to exclude KO — a long, heavily
# distributing name is exactly the stress case worth pinning.
PIN_TOL = 1e-4

# A tolerance loose enough to hide a double-applied split would make this test
# worthless. The split-term bug produces ~1e0 error, so assert the gap directly.
BUG_FLOOR = 1e-2


@pytest.fixture(scope="module")
def histories(yf):
    out = {}
    for sym in PIN_SYMBOLS:
        h = yf.Ticker(sym).history(period="max", auto_adjust=False, actions=True)
        if h.empty or "Adj Close" not in h.columns:
            pytest.skip(f"Yahoo served no usable history for {sym}")
        out[sym] = h
    return out


def _rel_err(h, factor):
    adj = h["Adj Close"].to_numpy(float)
    recon = h["Close"].to_numpy(float) * factor
    return np.nanmax(np.abs(recon - adj) / adj)


@pytest.mark.network
@pytest.mark.parametrize("sym", PIN_SYMBOLS)
def test_dividend_factor_reproduces_vendor_adj_close(histories, sym):
    err = _rel_err(histories[sym], dividend_factor(histories[sym]))
    assert err < PIN_TOL, (
        f"{sym}: reconstruction drifted from Yahoo's Adj Close by {err:.3e} "
        f"(tolerance {PIN_TOL:.0e}). Either Yahoo changed its adjustment "
        f"convention or dividend_factor regressed.")


@pytest.mark.network
def test_pin_set_actually_exercises_the_split_path(histories):
    """A pin set of never-split symbols would pass even with the split bug."""
    with_splits = [s for s, h in histories.items() if (h["Stock Splits"] > 0).any()]
    assert len(with_splits) >= 2, (
        f"PIN_SYMBOLS must include split-heavy names; only {with_splits} have "
        f"splits. TLT and SPY have never split and cannot catch the bug.")


@pytest.mark.network
@pytest.mark.parametrize("sym", PIN_SYMBOLS)
def test_adding_a_split_term_breaks_reconstruction(histories, sym):
    """Regression guard for the specific bug: Yahoo's Close is ALREADY
    split-adjusted, so folding splits into the dividend factor double-applies
    them. On a split name this must fail loudly, not drift quietly."""
    h = histories[sym]
    if not (h["Stock Splits"] > 0).any():
        pytest.skip(f"{sym} has never split — the bug is undetectable here")

    close = h["Close"].to_numpy(float)
    div = h["Dividends"].to_numpy(float)
    spl = h["Stock Splits"].to_numpy(float)
    spl = np.where(spl > 0, spl, 1.0)

    n = len(h)
    buggy = np.ones(n)
    f = 1.0
    for i in range(n - 1, 0, -1):
        f /= spl[i]                                   # the bug
        if div[i] > 0 and close[i - 1] > 0:
            f *= 1.0 - div[i] / close[i - 1]
        buggy[i - 1] = f

    assert _rel_err(h, buggy) > BUG_FLOOR, (
        f"{sym}: the split-term bug did NOT produce a large error, so this "
        f"symbol no longer guards the regression.")


@pytest.mark.network
@pytest.mark.parametrize("sym", ["AAPL"])
def test_as_traded_reconstruction_matches_known_tape_price(histories, sym):
    """AAPL closed at 499.23 as-traded on 2020-08-28, the last session before the
    4:1 split. Yahoo serves it split-adjusted at ~124.81."""
    h = histories[sym]
    raw_close = h["Close"].to_numpy(float) * cumulative_split_factor(h)
    i = h.index.get_indexer([h.index[h.index.tz_localize(None).normalize()
                                     == np.datetime64("2020-08-28")][0]])[0]
    assert h["Close"].to_numpy(float)[i] == pytest.approx(124.81, abs=0.05)
    assert raw_close[i] == pytest.approx(499.23, abs=0.05)
