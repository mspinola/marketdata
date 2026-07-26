"""A vendor-blind read must not also be vendor-ignorant. No network."""
import pandas as pd
import pytest

from marketdata import provenance, store

D = "equities"


def frame(n, start="2020-01-01", divs=0):
    idx = pd.date_range(start, periods=n, freq="B")
    c = pd.Series(range(1, n + 1), index=idx, dtype=float)
    div = pd.Series(0.0, index=idx)
    if divs:
        div.iloc[:divs] = 0.25
    return pd.DataFrame(
        {"Open": c, "High": c, "Low": c, "Close": c, "Volume": 1_000.0,
         "Dividends": div, "Stock Splits": 0.0, "Capital Gains": 0.0}, index=idx)


def test_reports_what_backs_the_series(tmp_store):
    store.write_bars("SPY", frame(10, divs=2), domain=D, source="yfinance")
    p = provenance("SPY")
    assert (p.symbol, p.domain, p.source) == ("SPY", D, "yfinance")
    assert p.n_rows == 10
    assert p.n_dividends == 2
    assert p.first_date == "2020-01-01"
    assert p.updated_at.endswith("Z")
    assert p.other_sources == ()


def test_absent_symbol_returns_none_not_an_error(tmp_store):
    assert provenance("NOPE") is None


def test_present_under_another_vendor_is_loud(tmp_store):
    """Reporting on a series the caller is not reading is worse than no answer."""
    store.write_bars("SPY", frame(10), domain=D, source="yfinance")
    with pytest.raises(FileNotFoundError, match="but it is under"):
        provenance("SPY", source="norgate")


def test_other_sources_are_listed(tmp_store):
    store.write_bars("SPY", frame(10), domain=D, source="yfinance")
    store.write_bars("SPY", frame(4), domain=D, source="norgate")
    assert provenance("SPY", source="yfinance").other_sources == ("norgate",)
    assert provenance("SPY", source="norgate").n_rows == 4


def test_covers_is_the_lookback_guard(tmp_store):
    """The whole point: a shallow vendor answers get_bars happily on a window it
    cannot support. `covers` is how a caller finds out before trusting it."""
    store.write_bars("SPY", frame(10, start="2015-01-01"), domain=D, source="yfinance")
    p = provenance("SPY")
    assert p.covers("2015-06-01") is True
    assert p.covers("2015-01-01") is True
    assert p.covers("2010-01-01") is False


def test_describe_is_one_line(tmp_store):
    store.write_bars("SPY", frame(10), domain=D, source="yfinance")
    line = provenance("SPY").describe()
    assert "\n" not in line
    assert "SPY" in line and "yfinance" in line and "10 bars" in line


def test_parquet_without_a_manifest_row_still_reports(tmp_store):
    """A hand-copied file, or a manifest lost to a concurrent write, should report
    what is knowable rather than claiming the series is absent."""
    from marketdata import config
    store.write_bars("SPY", frame(10), domain=D, source="yfinance")
    config.manifest_path().unlink()
    p = provenance("SPY")
    assert p is not None
    assert p.n_rows == 0 and p.first_date is None
    assert p.covers("2020-01-01") is False
