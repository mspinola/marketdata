"""The vendor is part of the store PATH, not just the manifest.

Two vendors that both carry a symbol must not contend for one file. Yahoo and
Norgate overlap almost completely on equities and ETFs and do not store the same
columns, so a single bars/{symbol}.parquet would let the last producer to run
silently win. No network.
"""
import pandas as pd
import pytest

from marketdata import bars, store

D = "equities"


def frame(closes, start="2020-01-01"):
    idx = pd.date_range(start, periods=len(closes), freq="B")
    c = pd.Series(closes, index=idx, dtype=float)
    return pd.DataFrame(
        {"Open": c, "High": c, "Low": c, "Close": c, "Volume": 1_000_000.0,
         "Dividends": 0.0, "Stock Splits": 0.0, "Capital Gains": 0.0},
        index=idx)


def test_two_vendors_do_not_overwrite_each_other(tmp_store):
    store.write_bars("SPY", frame([1, 2, 3]), domain=D, source="yfinance")
    store.write_bars("SPY", frame([10, 20, 30]), domain=D, source="norgate")

    assert list(store.read_bars("SPY", D, "yfinance")["Close"]) == [1, 2, 3]
    assert list(store.read_bars("SPY", D, "norgate")["Close"]) == [10, 20, 30]
    assert store.sources_for("SPY", D) == ["norgate", "yfinance"]


def test_manifest_keys_are_namespaced_by_source(tmp_store):
    store.write_bars("SPY", frame([1, 2, 3]), domain=D, source="yfinance")
    store.write_bars("SPY", frame([10, 20, 30]), domain=D, source="norgate")
    entries = store.load_manifest()["bars"]
    assert set(entries) == {f"{D}/yfinance/SPY", f"{D}/norgate/SPY"}
    assert entries[f"{D}/yfinance/SPY"]["n_rows"] == 3


def test_provider_name_matches_the_registry_vocabulary():
    """The source is a store PATH component AND what `resolve_source` returns.
    If a provider names itself differently, a default read looks in a directory
    nothing ever wrote to."""
    from marketdata.providers import yfinance as yprov
    from marketdata.registry import PRICE_SOURCES
    assert yprov.NAME in PRICE_SOURCES


def test_get_bars_reads_the_resolved_vendor(tmp_store, monkeypatch):
    from marketdata.providers import yfinance as yprov
    store.write_bars("SPY", frame([1, 2, 3]), domain=D, source=yprov.NAME)
    store.write_bars("SPY", frame([10, 20, 30]), domain=D, source="norgate")

    monkeypatch.setenv("MARKETDATA_PRICE_SOURCE", "yfinance")
    assert bars.default_source_for("SPY") == "yfinance"
    # The round trip that matters: what the provider wrote is what a default
    # read finds, with no source= argument anywhere.
    assert list(bars.get_bars("SPY")["Close"]) == [1, 2, 3]
    assert list(bars.get_bars("SPY", source="norgate")["Close"]) == [10, 20, 30]


def test_missing_under_one_vendor_but_present_under_another_is_loud(tmp_store):
    """Silently substituting a vendor would blend two series across a re-run,
    which is exactly what ADR-0006 forbids."""
    store.write_bars("SPY", frame([1, 2, 3]), domain=D, source="yfinance")
    with pytest.raises(FileNotFoundError, match="but it is under"):
        bars.get_bars("SPY", source="norgate")


def test_absent_symbol_returns_empty_not_an_error(tmp_store):
    store.write_bars("SPY", frame([1, 2, 3]), domain=D, source="yfinance")
    assert bars.get_bars("NOPE", source="yfinance").empty


def test_available_groups_by_source(tmp_store):
    store.write_bars("SPY", frame([1, 2, 3]), domain=D, source="yfinance")
    store.write_bars("TLT", frame([4, 5, 6]), domain=D, source="yfinance")
    store.write_bars("SPY", frame([7, 8, 9]), domain=D, source="norgate")
    assert bars.available() == {D: {"norgate": ["SPY"], "yfinance": ["SPY", "TLT"]}}
    assert bars.available(source="yfinance") == {D: {"yfinance": ["SPY", "TLT"]}}


@pytest.mark.parametrize("bad", ["", "../escape", "a/b", ".hidden"])
def test_source_cannot_escape_the_store_root(tmp_store, bad):
    from marketdata import config
    with pytest.raises(ValueError, match="invalid price source"):
        config.bars_dir(D, bad)


def test_domain_separates_the_adjustment_axis(tmp_store):
    """A futures tier on an equity symbol must name the right ones, not guess."""
    from marketdata.adjust import check_tier, tiers_for
    assert tiers_for("equities") == ("split", "raw", "total")
    assert tiers_for("futures") == ("backadj", "unadj", "propadj")
    check_tier("total", "equities")
    with pytest.raises(ValueError, match="is a futures adjustment"):
        check_tier("backadj", "equities")
    with pytest.raises(ValueError, match="unknown domain"):
        tiers_for("crypto")


def test_registry_defaults_symbols_to_the_equities_domain():
    from marketdata import domain_for
    assert domain_for("SPY") == "equities"
    assert domain_for("NOT_IN_REGISTRY") == "equities"


def test_get_bars_rejects_a_futures_tier_on_an_equity(tmp_store):
    store.write_bars("SPY", frame([1, 2, 3]), domain=D, source="yfinance")
    with pytest.raises(ValueError, match="not valid for domain 'equities'"):
        bars.get_bars("SPY", "backadj")


@pytest.mark.parametrize("bad", ["", "../escape", "a/b", ".hidden"])
def test_domain_cannot_escape_the_store_root(tmp_store, bad):
    from marketdata import config
    with pytest.raises(ValueError, match="invalid domain"):
        config.bars_dir(bad, "yfinance")
