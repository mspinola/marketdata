"""`marketdata-update --symbols`: a symbol the registry does not carry must be
refused, not filtered out.

The providers used to disagree. databento raised KeyError on an unregistered
symbol; yfinance and norgate dropped it from their target list and returned
ok=True with wrote=0, which `main` turns into exit 0. So `--symbols VIX` against a
registry that no longer carried VIX printed a line nobody reads and reported a
successful run, and a wrapper testing `if not errorlevel 1` went on to sync a
store nothing had been written to. That is the failure mode a retired symbol
hands to its consumers, months after the retirement, looking like a typo.
"""
import pytest

from marketdata import update
from marketdata.providers import norgate as nprov, yfinance as yprov

YF_OK = {"kind": "bars_yahoo", "ok": True, "wrote": 1, "failed": 0}
NG_OK = {"kind": "bars_futures_norgate", "ok": True, "wrote": 1, "failed": 0,
         "errors": [], "rows": 10, "newest": "2026-08-20"}


def test_an_unregistered_symbol_is_refused(capsys):
    with pytest.raises(SystemExit):
        update.main(["--bars", "--symbols", "NOTASYMBOL"])
    err = capsys.readouterr().err
    assert "not in the marketdata registry" in err
    assert "NOTASYMBOL" in err


def test_refused_even_when_other_requested_symbols_are_valid(capsys):
    """A partially satisfiable request is still not the request that was made. If
    one of four symbols has been retired out of the registry, the run must say so
    rather than quietly fetch three and exit 0."""
    with pytest.raises(SystemExit):
        update.main(["--bars", "--symbols", "SPY", "NOTASYMBOL"])
    err = capsys.readouterr().err
    assert "NOTASYMBOL" in err
    assert "SPY" not in err          # names what is wrong, not what is fine


def test_a_registered_symbol_is_not_refused(monkeypatch):
    """The guard must not over-fire on the case it exists to protect."""
    monkeypatch.setattr(yprov, "update", lambda *a, **k: YF_OK)
    monkeypatch.setattr(nprov, "update", lambda *a, **k: NG_OK)
    assert update.main(["--bars", "--domain", "equities", "--symbols", "VIX"]) == 0


def test_an_equities_symbol_does_not_fail_the_futures_half(monkeypatch):
    """An unscoped --bars runs every domain, so an equities symbol resolves to
    nothing in norgate and a futures symbol resolves to nothing in yfinance. That
    is why the check lives in main() and not in each provider: a per-provider
    refusal would fail a run the other half handled perfectly well."""
    monkeypatch.setattr(yprov, "update", lambda *a, **k: YF_OK)
    monkeypatch.setattr(nprov, "update", lambda *a, **k: NG_OK)
    assert update.main(["--bars", "--symbols", "VIX"]) == 0


def test_no_symbols_means_no_check(monkeypatch):
    monkeypatch.setattr(yprov, "update", lambda *a, **k: YF_OK)
    monkeypatch.setattr(nprov, "update", lambda *a, **k: NG_OK)
    assert update.main(["--bars", "--domain", "equities"]) == 0
