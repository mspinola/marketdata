"""`asof=`: the futures series as it stood on a past date, not today's restated one.

Additive back-adjustment re-anchors to whatever contract is front NOW, so every
past price moves every time the front month rolls. A backtest reading today's
series is therefore reading prices that were not on any screen at the time. That
is invisible to difference-based logic and decisive for ratio-based logic, and
these tests pin both halves of that.

The fixture is built so the arithmetic is checkable by hand:

    segment   delivery   unadj close      roll spread entering the segment
    1         202003     100, 101, 102    -
    2         202006     110, 111, 112    110 - 102 =  8
    3         202009     120, 121, 122    120 - 112 =  8

Back-adjusted, anchored to segment 3: segment 2 gains 8, segment 1 gains 16.
"""
import numpy as np
import pandas as pd
import pytest

from marketdata import bars, store
from marketdata.adjust import backadj_asof

FUT = "futures"
UNADJ = [100.0, 101.0, 102.0, 110.0, 111.0, 112.0, 120.0, 121.0, 122.0]
BACKADJ = [116.0, 117.0, 118.0, 118.0, 119.0, 120.0, 120.0, 121.0, 122.0]
DELIVERY = [202003] * 3 + [202006] * 3 + [202009] * 3
DATES = pd.date_range("2020-01-01", periods=9, freq="D")
END_OF_SEG2 = "2020-01-06"


def _frame(closes):
    df = pd.DataFrame({"Open": closes, "High": closes, "Low": closes,
                       "Close": closes, "Volume": 100.0,
                       "Open Interest": 1000.0, "Delivery Month": DELIVERY},
                      index=DATES)
    df.index.name = "Date"
    return df


@pytest.fixture()
def two_tier_store(tmp_store):
    store.write_bars("ES", _frame(UNADJ), domain=FUT, source="norgate", tier="unadj")
    store.write_bars("ES", _frame(BACKADJ), domain=FUT, source="norgate", tier="backadj")
    return tmp_store


def test_the_fixture_offset_is_piecewise_constant():
    """Guards the fixture itself: if this drifts, every expectation below is
    measuring the wrong thing."""
    off = np.array(BACKADJ) - np.array(UNADJ)
    assert list(off) == [16.0] * 3 + [8.0] * 3 + [0.0] * 3


def test_vintage_re_anchors_to_the_contract_that_was_front_then(two_tier_store):
    """As of the last day of segment 2, the offset was 8, not 16. Segment 2 must
    read at its as-traded prices and segment 1 must sit 8 above its own."""
    got = bars.get_bars("ES", "backadj", asof=END_OF_SEG2)["Close"].tolist()
    assert got == [108.0, 109.0, 110.0, 110.0, 111.0, 112.0]


def test_asof_is_not_end(two_tier_store):
    """The trap this parameter exists for. Both return the same ROWS; only asof
    returns the prices those rows actually had at the time."""
    a = bars.get_bars("ES", "backadj", asof=END_OF_SEG2)["Close"]
    e = bars.get_bars("ES", "backadj", end=END_OF_SEG2)["Close"]
    assert list(a.index) == list(e.index)
    assert not np.allclose(a.to_numpy(), e.to_numpy())
    assert np.allclose((e - a).to_numpy(), 8.0), "vintage differs by one constant"


def test_difference_signals_are_invariant_and_ratio_signals_are_not(two_tier_store):
    """The whole point, in one test. A constant offset cancels out of a
    comparison between two points on one series, and does not cancel out of a
    ratio between them."""
    a = bars.get_bars("ES", "backadj", asof=END_OF_SEG2)["Close"]
    e = bars.get_bars("ES", "backadj", end=END_OF_SEG2)["Close"]

    # difference-based: today minus 2 bars ago
    assert np.allclose((a - a.shift(2)).dropna(), (e - e.shift(2)).dropna())
    # ratio-based: percent change over the same span
    assert not np.allclose(a.pct_change(2).dropna(), e.pct_change(2).dropna())


def test_unadj_asof_is_a_plain_truncation(two_tier_store):
    """As-traded prices do not restate, so there is nothing to re-anchor."""
    a = bars.get_bars("ES", "unadj", asof=END_OF_SEG2)["Close"]
    e = bars.get_bars("ES", "unadj", end=END_OF_SEG2)["Close"]
    assert np.allclose(a.to_numpy(), e.to_numpy())
    assert a.tolist() == UNADJ[:6]


def test_propadj_asof_re_anchors_before_ratio_adjusting(two_tier_store):
    """Ratio-adjusting first and truncating second would scale segment 1 by a
    roll ratio from a roll that had not happened yet."""
    got = bars.get_bars("ES", "propadj", asof=END_OF_SEG2)["Close"]
    # Anchored so the most recent segment IS the as-traded price of that segment.
    assert np.allclose(got.iloc[-3:].to_numpy(), [110.0, 111.0, 112.0])
    # Segment 1 scaled by the single roll inside the window: 110/102.
    assert np.allclose(got.iloc[:3].to_numpy(),
                       np.array([100.0, 101.0, 102.0]) * (110.0 / 102.0))
    # ...and that is NOT what the un-asof'd series says for those rows.
    full = bars.get_bars("ES", "propadj", end=END_OF_SEG2)["Close"]
    assert not np.allclose(got.to_numpy(), full.to_numpy())


def test_asof_at_or_after_the_last_bar_is_todays_series(two_tier_store):
    for when in ("2020-01-09", "2099-01-01"):
        a = bars.get_bars("ES", "backadj", asof=when)["Close"]
        assert np.allclose(a.to_numpy(), BACKADJ), f"asof={when} should not re-anchor"


def test_asof_before_the_first_bar_is_empty_not_an_error(two_tier_store):
    assert bars.get_bars("ES", "backadj", asof="1990-01-01").empty


def test_backadj_asof_needs_the_unadj_tier(tmp_store):
    """The offset IS the difference between the tiers, so one tier cannot answer.
    Loud, because a silent fallback to today's series defeats the parameter."""
    store.write_bars("ES", _frame(BACKADJ), domain=FUT, source="norgate", tier="backadj")
    with pytest.raises(FileNotFoundError, match="unadj"):
        bars.get_bars("ES", "backadj", asof=END_OF_SEG2)


def test_equity_asof_refuses_rather_than_ignoring(tmp_store):
    """Silently returning today's series for a point-in-time request is exactly
    the failure this parameter exists to prevent, so the equity gap is a raise."""
    idx = pd.date_range("2020-01-01", periods=3, freq="D")
    df = pd.DataFrame({"Open": 1.0, "High": 1.0, "Low": 1.0, "Close": 1.0,
                       "Volume": 1.0, "Dividends": 0.0, "Stock Splits": 0.0},
                      index=idx)
    df.index.name = "Date"
    store.write_bars("SPY", df, domain="equities", source="yfinance")
    with pytest.raises(NotImplementedError, match="futures domain only"):
        bars.get_bars("SPY", "total", asof="2020-01-02")


def test_backadj_asof_is_empty_on_empty_input():
    assert backadj_asof(pd.DataFrame(), pd.DataFrame(), "2020-01-01").empty
