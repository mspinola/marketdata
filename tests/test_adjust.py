"""Offline unit tests for the adjustment derivation. No network."""
import numpy as np
import pandas as pd
import pytest

from marketdata.adjust import (
    TIERS,
    adjust,
    cumulative_split_factor,
    dividend_factor,
    to_raw,
    to_total,
)


def frame(closes, div=None, spl=None):
    n = len(closes)
    idx = pd.date_range("2020-01-01", periods=n, freq="B")
    c = np.asarray(closes, dtype=float)
    return pd.DataFrame(
        {"Open": c, "High": c * 1.01, "Low": c * 0.99, "Close": c,
         "Volume": np.full(n, 1_000_000.0),
         "Dividends": np.zeros(n) if div is None else np.asarray(div, float),
         "Stock Splits": np.zeros(n) if spl is None else np.asarray(spl, float),
         "Capital Gains": np.zeros(n)},
        index=idx)


# ── splits / raw ─────────────────────────────────────────────────────────
def test_no_splits_means_raw_is_a_noop():
    df = frame([10, 11, 12, 13])
    assert np.allclose(cumulative_split_factor(df), 1.0)
    pd.testing.assert_frame_equal(to_raw(df), df)


def test_raw_unapplies_a_split_and_inverts_volume():
    # A 4:1 split on bar 2. Yahoo would show pre-split bars already divided by 4,
    # so as-traded is 4x the stored close, and as-traded volume is 1/4.
    df = frame([25, 25, 30, 31], spl=[0, 0, 4, 0])
    raw = to_raw(df)
    assert list(raw["Close"]) == pytest.approx([100.0, 100.0, 30.0, 31.0])
    assert list(raw["Volume"]) == pytest.approx([250_000, 250_000, 1_000_000, 1_000_000])
    # The split bar itself already trades post-split — its own ratio is excluded.
    assert cumulative_split_factor(df)[2] == pytest.approx(1.0)


def test_raw_compounds_multiple_splits():
    df = frame([10, 10, 10, 10, 10], spl=[0, 2, 0, 3, 0])
    # bar 0 sits before both splits -> 6x; bar 1 is the 2:1 bar, before the 3:1 -> 3x
    assert list(to_raw(df)["Close"]) == pytest.approx([60, 30, 30, 10, 10])


# ── dividends / total ────────────────────────────────────────────────────
def test_dividend_factor_has_no_split_term():
    """The bug this module exists to prevent. Yahoo's Close is ALREADY
    split-adjusted, so a split must not enter the dividend factor."""
    df = frame([25, 25, 30, 31], spl=[0, 0, 4, 0])
    assert np.allclose(dividend_factor(df), 1.0)
    pd.testing.assert_frame_equal(to_total(df), df)


def test_total_scales_history_below_an_ex_date():
    # 1.00 dividend on bar 2, prior close 50 -> prior bars scale by 0.98
    df = frame([50, 50, 50, 50], div=[0, 0, 1.0, 0])
    tot = to_total(df)
    assert list(tot["Close"]) == pytest.approx([49.0, 49.0, 50.0, 50.0])
    # OHLC all move together; the last bar is always unscaled.
    assert tot["Open"].iloc[0] == pytest.approx(49.0)
    assert tot["Close"].iloc[-1] == pytest.approx(50.0)


def test_total_return_beats_price_return_for_a_payer():
    df = frame([50, 50, 50, 50], div=[0, 0, 1.0, 0])
    price_ret = df["Close"].iloc[-1] / df["Close"].iloc[0] - 1
    tot = to_total(df)
    total_ret = tot["Close"].iloc[-1] / tot["Close"].iloc[0] - 1
    assert price_ret == pytest.approx(0.0)
    assert total_ret > price_ret


def test_capital_gains_are_opt_in():
    df = frame([50, 50, 50, 50])
    df["Capital Gains"] = [0, 0, 1.0, 0]
    assert np.allclose(dividend_factor(df, include_capital_gains=False), 1.0)
    assert dividend_factor(df, include_capital_gains=True)[0] == pytest.approx(0.98)


# ── dispatch ─────────────────────────────────────────────────────────────
def test_adjust_dispatches_and_rejects_unknown_tiers():
    df = frame([10, 11], div=[0, 1.0], spl=[0, 2])
    pd.testing.assert_frame_equal(adjust(df, "split"), df)
    pd.testing.assert_frame_equal(adjust(df, "raw"), to_raw(df))
    pd.testing.assert_frame_equal(adjust(df, "total"), to_total(df))
    with pytest.raises(ValueError, match="tier must be one of"):
        adjust(df, "adjusted")


def test_adjust_never_mutates_the_input():
    df = frame([25, 25, 30], div=[0, 1.0, 0], spl=[0, 0, 4])
    before = df.copy()
    for tier in TIERS:
        adjust(df, tier)
    pd.testing.assert_frame_equal(df, before)


def test_empty_frame_survives_every_tier():
    empty = pd.DataFrame()
    for tier in TIERS:
        assert adjust(empty, tier).empty
