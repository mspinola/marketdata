"""The databento provider's contract with the registry and the store.

`test_databento_ingest.py` covers Stage 1 and `test_databento_build.py` covers Stage 2.
This file covers the seams the port had to decide rather than copy: which symbols the
provider claims, how the registry expresses databento coverage across TWO domains where
cotdata had one, and that the store invariant holds on this producer too.

No network and no databento SDK — the provider imports it lazily, which is itself
asserted below.
"""
import pytest

from marketdata import store
from marketdata.adjust import stored_tiers_for
from marketdata.providers import databento as dprov
from marketdata.registry import PRICE_SOURCES, Symbol, all_symbols, resolve_source


# ── the registry rule this port introduced ───────────────────────────────────
def test_databento_is_a_declared_price_source():
    """The same string is the store path component and what `resolve_source` returns,
    so a provider NAME missing from PRICE_SOURCES is a series nothing can resolve to."""
    assert dprov.NAME in PRICE_SOURCES
    assert dprov.DOMAIN == "futures"


def test_futures_default_to_their_own_root_and_equities_never_do():
    """cotdata defaults `databento` to the internal symbol unconditionally, which is
    safe there because that registry has ONE domain. Here it would hand every equity a
    GLBX root and let `resolve_source` route SPY to a vendor that cannot serve it, so
    the default is futures-only."""
    fut = [s for s in all_symbols() if s.domain == "futures"]
    eq = [s for s in all_symbols() if s.domain == "equities"]

    assert eq, "no equities in the registry — this test would pass vacuously"
    assert all(s.databento is None for s in eq)
    # ES is on GLBX and inherits its own symbol as the root.
    assert next(s for s in fut if s.internal == "ES").databento == "ES"


def test_the_markets_glbx_does_not_carry_are_marked_null():
    """ICE softs, lumber and the dollar index are not on CME Globex. Marked in the
    registry rather than discovered as an empty fetch, so the producer skips them
    instead of paying for a query that returns nothing."""
    uncovered = sorted(s.internal for s in all_symbols()
                       if s.domain == "futures" and s.databento is None)
    assert uncovered == ["CC", "CT", "DX", "KC", "LBR", "OJ", "SB", "WBS"]


def test_an_uncovered_market_still_resolves_to_a_vendor():
    """`databento: null` is a statement about one vendor, not about the market. Norgate
    still carries all eight, so none of them becomes unservable."""
    for sym in ("SB", "CT", "LBR", "DX"):
        s = next(x for x in all_symbols() if x.internal == sym)
        assert resolve_source(s, "databento") == "norgate"


def test_a_databento_deployment_routes_covered_futures_to_it():
    es = next(s for s in all_symbols() if s.internal == "ES")
    assert resolve_source(es, "databento") == "databento"
    assert resolve_source(es, "norgate") == "norgate"       # unchanged elsewhere


def test_an_unknown_price_source_is_refused_by_name():
    from marketdata.registry import _can_serve
    with pytest.raises(ValueError, match="unknown price source"):
        _can_serve(next(iter(all_symbols())), "bloomberg")


# ── which symbols the provider claims ────────────────────────────────────────
def test_targets_are_futures_only_and_skip_what_glbx_lacks():
    names = [s.internal for s in dprov._targets()]
    assert "ES" in names and "SPY" not in names
    assert "SB" not in names                                  # databento: null
    assert len(names) == 41


def test_targets_scope_and_reject_an_unknown_symbol():
    assert [s.internal for s in dprov._targets(["ES", "GC"])] == ["ES", "GC"]
    assert dprov._targets(["SPY"]) == []                      # wrong domain
    with pytest.raises(KeyError):
        dprov._targets(["NOT_A_SYMBOL"])


def test_a_symbol_pinned_to_another_vendor_is_left_alone(monkeypatch):
    """A per-symbol `price_source` is a statement about the symbol, so it wins even on
    an explicit databento command. No registry symbol is pinned today, so this is built
    rather than found — the branch would otherwise be unreachable and untested."""
    pinned = Symbol(internal="ES", asset_class="Equity Index Futures", domain="futures",
                    databento="ES", price_source="norgate")
    free = Symbol(internal="GC", asset_class="Metals", domain="futures", databento="GC")
    monkeypatch.setattr(dprov, "all_symbols", lambda: [pinned, free])

    assert [s.internal for s in dprov._targets()] == ["GC"]


def test_the_deployment_default_does_not_silence_an_explicit_command(monkeypatch):
    """The Norgate provider filters on `resolve_source`, so it produces nothing when the
    deployment points elsewhere. databento must NOT: `--build-databento` is an explicit
    ask for this vendor, and every research box defaults to another one, so honouring
    the default would make the command silently do nothing."""
    monkeypatch.setenv("MARKETDATA_PRICE_SOURCE", "norgate")
    assert len(dprov._targets()) == 41
    # A guard rather than a demonstration: `_targets` does not consult the default at
    # all, so this passes by construction today and fails the day someone "fixes" that
    # by copying the Norgate provider's `resolve_source` filter.


# ── the store invariant, on this producer too ────────────────────────────────
def test_build_writes_exactly_the_tiers_the_domain_declares(tmp_path, monkeypatch):
    """Both tiers or neither. `propadj` derives from the pair, so a half-written symbol
    reads fine on `backadj` and mis-scales every percent return — and the producer's
    loop and the store's layout have to agree about which tiers exist, or a consumer
    asks for one nothing wrote."""
    import pandas as pd

    monkeypatch.setenv("MARKETDATA_STORE", str(tmp_path / "store"))
    monkeypatch.setenv("MARKETDATA_DATABENTO_RAW", str(tmp_path / "raw"))

    dates = pd.date_range("2020-01-01", periods=4, freq="D")
    raw = tmp_path / "raw" / "ohlcv"
    raw.mkdir(parents=True)
    for feed, close, ids in ((".n.0", [10, 11, 12, 20], ["A", "A", "B", "B"]),
                             (".n.1", [13, 14, 15, 23], ["B", "B", "C", "C"])):
        pd.DataFrame({"open": close, "high": close, "low": close, "close": close,
                      "volume": [1] * 4, "instrument_id": ids},
                     index=pd.Index(dates, name="Date")).to_parquet(
            raw / f"ES{feed}.parquet")

    res = dprov.build(["ES"])
    assert res["ok"] and res["wrote"] == 1

    for tier in stored_tiers_for(dprov.DOMAIN):
        assert store.has_bars("ES", dprov.DOMAIN, dprov.NAME, tier), tier
    # ...and the derived tier the pair exists for now resolves.
    from marketdata import get_bars
    assert not get_bars("ES", "propadj", source=dprov.NAME).empty


def test_the_sdk_is_imported_lazily_and_only_where_it_is_paid_for():
    """The SDK is an extra, and Stage 2 needs neither it nor a key. A module-level
    import would make a free, offline rebuild require a paid dependency to be installed
    — and this whole test file, which never installs it, is the proof that it is not.

    Asserted against the source rather than by catching an ImportError, because the
    import that would break this is exactly the one a refactor adds at the top without
    thinking about it.
    """
    import pathlib as _pl

    lines = _pl.Path(dprov.__file__).read_text().splitlines()
    top_level = [ln for ln in lines
                 if ln.startswith(("import databento", "from databento"))]
    assert not top_level, f"databento imported at module level: {top_level}"

    # And it IS imported somewhere — a provider that never touches the SDK would pass
    # the check above for the wrong reason.
    assert any("import databento" in ln for ln in lines)
