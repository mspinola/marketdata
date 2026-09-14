"""The tape-context legs are registered, on the equities task, from yfinance.

Four ratios cot-analyzer draws beside positioning each need one leg the registry
did not carry (docs/design/breadth-domain-scoping.md). What this pins is that each
leg exists, sits in the equities domain so the 17:30 task fetches it unscoped, and
resolves to yfinance on a default deployment; a leg that quietly resolved to
Norgate would fail on the box, which has no US Stocks subscription.
"""
import pytest

from marketdata import registry

RATIOS = {
    "XLP": "QQQ",
    "RSP": "SPY",
    "HYG": "IEF",
    "VIX3M": "VIX",
}


# The one leg not on Yahoo: pinned to Cboe's own CSV because Yahoo serves it
# patchily (see the registry note and providers/cboe.py).
PINNED = {"VIX3M": "cboe"}


@pytest.mark.parametrize("leg, other", sorted(RATIOS.items()))
def test_both_legs_of_each_ratio_are_registered_equities(leg, other):
    for sym in (leg, other):
        s = registry.REGISTRY[sym]
        assert s.domain == "equities"
        assert s.yahoo is not None
        assert registry.resolve_source(s, "yfinance") == PINNED.get(sym, "yfinance")


def test_the_vol_legs_are_yahoo_index_tickers():
    assert registry.REGISTRY["VIX3M"].yahoo == "^VIX3M"
    assert registry.REGISTRY["VIX"].yahoo == "^VIX"
