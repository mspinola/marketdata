"""The `series` domain: published readings that are not prices, one `raw` tier,
stored as served. Registry, tiers, reader, coverage. No network."""
import pandas as pd
import pytest

from marketdata import bars, store
from marketdata.adjust import (
    DERIVED_TIERS,
    STORED_TIERS,
    check_tier,
    stored_tiers_for,
    tiers_for,
)
from marketdata.provenance import coverage_gaps
from marketdata.provenance import provenance as provenance_of
from marketdata.registry import DOMAINS, REGISTRY, SERIES_KINDS, _can_serve, resolve_source

D = "series"
SYM = "NASDAQ_FOMO_5D"


def frame(closes, start="2026-09-01"):
    idx = pd.bdate_range(start, periods=len(closes), name="Date")
    c = pd.Series(closes, index=idx, dtype=float)
    return pd.DataFrame({"Open": c, "High": c, "Low": c, "Close": c})


def test_series_is_a_domain_with_one_stored_raw_tier():
    assert "series" in DOMAINS
    assert tiers_for(D) == ("raw",)
    assert stored_tiers_for(D) == (None,)
    assert STORED_TIERS[D] == (None,) and DERIVED_TIERS[D] == ("raw",)
    check_tier("raw", D)
    with pytest.raises(ValueError, match="is a equities adjustment|is a futures adjustment"):
        check_tier("split", D)
    with pytest.raises(ValueError, match="not valid for domain 'series'"):
        check_tier("backadj", D)


def test_registry_series_symbols_name_tradingview_and_no_other_vendor():
    series = [s for s in REGISTRY.values() if s.domain == D]
    assert len(series) >= 11
    for s in series:
        assert s.tradingview and ":" in s.tradingview, s.internal
        assert s.yahoo is None and s.norgate is None and s.databento is None, s.internal
        assert s.kind in SERIES_KINDS, s.internal
        assert resolve_source(s) == "tradingview", s.internal
        assert _can_serve(s, "tradingview") and not _can_serve(s, "yfinance")
    fomo = REGISTRY[SYM]
    assert fomo.tradingview == "INDEX:NCFD" and fomo.kind == "percent"
    assert ("2026-07-29", 48.05) in fomo.anchors


def test_non_series_symbols_carry_no_series_attributes():
    for s in REGISTRY.values():
        if s.domain != D:
            assert s.tradingview is None and s.kind is None and s.anchors == (), s.internal


def test_a_series_symbol_stores_flat_under_its_domain_and_vendor(tmp_store):
    from marketdata import config
    store.write_bars(SYM, frame([1, 2, 3]), domain=D, source="tradingview")
    p = config.bars_path(SYM, D, "tradingview")
    assert p.relative_to(config.store_root()).as_posix() == f"bars/series/tradingview/{SYM}.parquet"
    assert store.load_manifest()["bars"][f"series/tradingview/{SYM}"]["n_rows"] == 3


def test_get_bars_serves_the_stored_frame_as_the_raw_tier(tmp_store):
    store.write_bars(SYM, frame([48.05, 59.7, 49.65]), domain=D, source="tradingview")
    df = bars.get_bars(SYM)
    assert list(df["Close"]) == [48.05, 59.7, 49.65]
    assert df.index.name == "Date"
    assert bars.get_bars(SYM, "raw").equals(df)
    assert list(bars.get_bars(SYM, end="2026-09-02")["Close"]) == [48.05, 59.7]


def test_get_bars_refuses_a_price_tier_and_asof_on_a_series(tmp_store):
    store.write_bars(SYM, frame([1, 2, 3]), domain=D, source="tradingview")
    with pytest.raises(ValueError, match="not valid for domain 'series'"):
        bars.get_bars(SYM, "total")
    with pytest.raises(NotImplementedError, match="pass end="):
        bars.get_bars(SYM, asof="2026-09-02")
    with pytest.raises(ValueError, match="futures concept"):
        bars.get_bars(SYM, volume="reconstructed")


def test_absent_series_reads_empty_and_available_lists_it_once_written(tmp_store):
    assert bars.get_bars(SYM).empty
    store.write_bars(SYM, frame([1, 2]), domain=D, source="tradingview")
    assert bars.available(domain=D) == {D: {"tradingview": [SYM]}}


def test_coverage_gaps_understands_the_series_domain(tmp_store):
    assert [g.reason for g in coverage_gaps([SYM])] == ["absent"]
    store.write_bars(SYM, frame([1, 2, 3]), domain=D, source="tradingview")
    assert coverage_gaps([SYM]) == []
    p = provenance_of(SYM)
    assert p.domain == D and p.source == "tradingview" and p.n_rows == 3 and p.tier is None
    assert [g.reason for g in coverage_gaps([SYM], start="2020-01-01")] == ["short"]
