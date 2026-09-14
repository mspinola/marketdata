"""The Cboe provider: the volatility indices from Cboe's own CSV, pinned per symbol.

Two rules worth holding. The provider serves ONLY symbols the registry names a `cboe`
key for and resolves to it: Yahoo carries the same tickers, so without the explicit
pin the deployment default would take the symbol back and the patchy series with it.
And the stored frame is the equities frame, so the one equity reader serves it with
every tier a passthrough. No network: the CSV is a string here.
"""
import pandas as pd

from marketdata import bars, registry
from marketdata.providers import cboe as cprov

CSV = """DATE,OPEN,HIGH,LOW,CLOSE
09/18/2009,25.910000,26.660000,25.910000,26.540000
09/21/2009,26.100000,26.300000,25.700000,25.900000
09/22/2009,25.500000,25.800000,25.100000,25.300000
"""


def test_provider_name_is_in_the_registry_vocabulary():
    assert cprov.NAME in registry.PRICE_SOURCES
    # Last, so it never wins a symbol by being tried first.
    assert registry.PRICE_SOURCES[-1] == cprov.NAME


def test_parse_yields_the_equities_frame():
    df = cprov.parse(CSV)
    assert list(df.columns) == list(cprov.KEEP)
    assert df.index.name == "Date"
    assert df.index[0] == pd.Timestamp("2009-09-18")
    assert df["Close"].tolist() == [26.54, 25.9, 25.3]
    # An index trades nothing and has no corporate actions.
    assert (df["Volume"] == 0).all()
    for c in ("Dividends", "Stock Splits", "Capital Gains"):
        assert (df[c] == 0).all()


def test_vix3m_resolves_to_cboe_whatever_the_deployment_default():
    s = registry.REGISTRY["VIX3M"]
    assert s.cboe == "VIX3M" and s.yahoo == "^VIX3M"
    assert registry.resolve_source(s, "yfinance") == "cboe"
    assert registry.resolve_source(s, "norgate") == "cboe"


def test_cboe_is_never_defaulted_onto_other_symbols():
    """A defaulted key would let SPY resolve to a vendor that cannot serve it."""
    for s in registry.all_symbols():
        if s.internal != "VIX3M":
            assert s.cboe is None, s.internal
    assert not [s for s in registry.all_symbols()
                if s.internal != "VIX3M" and registry.resolve_source(s, "yfinance") == "cboe"]


def test_update_writes_the_pinned_symbol_and_the_equity_reader_serves_it(
        tmp_store, monkeypatch):
    monkeypatch.setattr(cprov, "_download", lambda sym, timeout=60: CSV)
    res = cprov.update()
    assert res == {"kind": "bars_cboe", "ok": True, "wrote": 1, "failed": 0}

    got = bars.get_bars("VIX3M")
    assert got["Close"].tolist() == [26.54, 25.9, 25.3]
    for tier in ("split", "raw", "total"):
        assert bars.get_bars("VIX3M", tier)["Close"].tolist() == [26.54, 25.9, 25.3]


def test_update_scoped_to_a_symbol_it_does_not_serve_is_a_no_op(tmp_store, monkeypatch):
    monkeypatch.setattr(cprov, "_download", lambda sym, timeout=60: CSV)
    assert cprov.update(["SPY"]) == {"kind": "bars_cboe", "ok": True, "wrote": 0,
                                     "failed": 0}


def test_a_failed_download_is_a_failed_run(tmp_store, monkeypatch):
    def boom(sym, timeout=60):
        raise OSError("cdn unreachable")
    monkeypatch.setattr(cprov, "_download", boom)
    res = cprov.update()
    assert res["ok"] is False and res["failed"] == 1 and res["wrote"] == 0
