"""Effective-dated multipliers: the parse, the lookup, and the staleness tripwire.

The tripwire is the test worth reading. `contract_regimes.yaml` restates each declared
symbol's CURRENT multiplier as its last regime, so that value can be compared against the
vendor-refreshed `contract_specs`. Without that comparison the file has exactly one silent
failure mode, and it is the bad one: an exchange changes a multiplier again, the vendor
picks it up, and this file keeps back-dating the superseded value over the new history
while every lookup still returns a plausible number.
"""
import os

import pandas as pd
import pytest
import yaml

from marketdata import regimes, store
from marketdata.regimes import RegimeError

# ── the packaged file ─────────────────────────────────────────────────────


def test_the_packaged_file_parses_and_declares_the_audited_symbols():
    table = regimes.load_regimes()
    assert not table.empty
    assert list(table.columns) == list(regimes.REGIME_COLUMNS)
    # RTY is the confirmed defect, LBR is carried for completeness. Both are argued in
    # cotmetrics/docs/analysis/2026-08-22-effective-dated-contract-multipliers.md.
    assert regimes.declared_symbols() == {"RTY": 2, "LBR": 2}


def test_every_regime_cites_a_source():
    """A multiplier with no citation is indistinguishable from a typo."""
    table = regimes.load_regimes()
    assert (table["Source"].str.len() > 40).all()


def test_the_russell_change_is_dated_to_the_exchange_notice():
    rty = regimes.read_contract_regimes("RTY")
    assert list(rty["Point_Value"]) == [100.0, 50.0]
    assert list(rty["Tick_Value"]) == [10.0, 5.0]
    # ICE FAQ 2016-10-31: effective with the start of trading for trade date 2016-12-05.
    assert pd.isna(rty["Valid_From"].iloc[0])
    assert rty["Valid_From"].iloc[1] == pd.Timestamp("2016-12-05")


# ── the lookup ────────────────────────────────────────────────────────────


def test_lookup_returns_the_regime_in_force_on_each_date():
    dates = pd.to_datetime(["2002-08-13", "2016-11-29", "2016-12-05", "2026-08-18"])
    assert list(regimes.point_value_asof("RTY", dates)) == [100.0, 100.0, 50.0, 50.0]
    assert list(regimes.tick_value_asof("RTY", dates)) == [10.0, 10.0, 5.0, 5.0]


def test_the_boundary_is_inclusive_of_its_own_date():
    """valid_from is the first date the new regime applies, not the last of the old."""
    assert regimes.point_value_asof("RTY", ["2016-12-04"]).iloc[0] == 100.0
    assert regimes.point_value_asof("RTY", ["2016-12-05"]).iloc[0] == 50.0


def test_the_result_is_indexed_by_the_dates_asked_for_in_that_order():
    dates = pd.to_datetime(["2026-08-18", "2002-08-13", "2016-12-06"])
    out = regimes.point_value_asof("RTY", dates)
    assert list(out.index) == list(dates)
    assert list(out) == [50.0, 100.0, 50.0]


def test_repeated_dates_resolve_rather_than_raising():
    """A trade log has many rows on one date. An earlier merge_asof implementation
    raised 'cannot reindex on an axis with duplicate labels' on exactly this input."""
    out = regimes.point_value_asof("RTY", ["2016-12-06", "2016-12-06", "2002-08-13"])
    assert list(out) == [50.0, 50.0, 100.0]
    assert len(out) == 3


def test_unsorted_dates_keep_the_caller_s_order():
    dates = ["2026-08-18", "2002-08-13", "2016-12-06"]
    assert list(regimes.point_value_asof("RTY", dates)) == [50.0, 100.0, 50.0]


def test_timezone_aware_dates_are_accepted():
    """valid_from is an exchange-local calendar date, so tz is dropped, not rejected."""
    dates = pd.to_datetime(["2016-12-06", "2002-08-13"]).tz_localize("UTC")
    out = regimes.point_value_asof("RTY", dates)
    assert list(out) == [50.0, 100.0]
    assert out.index.tz is None


def test_no_dates_gives_an_empty_series_not_an_error():
    assert regimes.point_value_asof("RTY", []).empty


def test_a_bounded_first_regime_returns_nan_before_it_rather_than_a_guess():
    """LBR's pre-1995 sizes were never established. A gap is visible; a guess is not."""
    out = regimes.point_value_asof("LBR", ["1995-09-26", "1995-12-12", "2022-08-08"])
    assert pd.isna(out.iloc[0])
    assert list(out.iloc[1:]) == [110.0, 27.5]


def test_an_unverified_tick_value_is_nan_while_its_point_value_still_resolves():
    when = ["2000-01-04"]
    assert regimes.point_value_asof("LBR", when).iloc[0] == 110.0
    assert pd.isna(regimes.tick_value_asof("LBR", when).iloc[0])


def test_an_undeclared_symbol_falls_back_to_its_current_spec(tmp_store):
    """The whole point of the fallback: a caller writes one code path for every market."""
    store.write_metadata(pd.DataFrame([
        {"Symbol": "ES", "Point Value": 50.0, "Tick Value": 12.5},
    ]), source="test")
    out = regimes.point_value_asof("ES", ["1997-09-16", "2026-08-18"])
    assert list(out) == [50.0, 50.0]
    assert regimes.read_contract_regimes("ES").empty


def test_an_unknown_symbol_is_nan_rather_than_an_error(tmp_store):
    """One unpriceable market must not take the other 46 down with it."""
    store.write_metadata(pd.DataFrame([{"Symbol": "ES", "Point Value": 50.0}]),
                         source="test")
    assert regimes.point_value_asof("NOPE", ["2026-08-18"]).isna().all()


def test_a_declared_symbol_needs_no_store_at_all(tmp_store):
    """Regimes are packaged, not stored, so the lookup works before any producer ran."""
    assert store.read_metadata().empty
    assert regimes.point_value_asof("RTY", ["2010-01-05"]).iloc[0] == 100.0


# ── the parse refuses what it cannot answer ───────────────────────────────


def _write(tmp_path, monkeypatch, doc):
    p = tmp_path / "regimes.yaml"
    p.write_text(yaml.safe_dump(doc) if isinstance(doc, dict) else doc)
    monkeypatch.setenv("MARKETDATA_CONTRACT_REGIMES", str(p))
    return p


def test_a_missing_file_is_an_empty_table_not_an_error(tmp_path, monkeypatch):
    """No declared regimes means every symbol uses its current spec, as before."""
    monkeypatch.setenv("MARKETDATA_CONTRACT_REGIMES", str(tmp_path / "absent.yaml"))
    assert regimes.load_regimes().empty


def test_a_malformed_file_raises(tmp_path, monkeypatch):
    """An unreadable claim is not the same as an absence of claims."""
    _write(tmp_path, monkeypatch, "RTY: [oops\n")
    with pytest.raises(RegimeError, match="malformed"):
        regimes.load_regimes()


def test_a_regime_without_a_source_raises(tmp_path, monkeypatch):
    _write(tmp_path, monkeypatch,
           {"XX": [{"valid_from": None, "point_value": 5.0, "name": "x"}]})
    with pytest.raises(RegimeError, match="source"):
        regimes.load_regimes()


def test_a_null_valid_from_on_a_later_regime_raises(tmp_path, monkeypatch):
    """It would silently reorder the series and back-date the wrong multiplier."""
    _write(tmp_path, monkeypatch, {"XX": [
        {"valid_from": "2000-01-01", "point_value": 5.0, "name": "a", "source": "s" * 50},
        {"valid_from": None, "point_value": 10.0, "name": "b", "source": "s" * 50},
    ]})
    with pytest.raises(RegimeError, match="first regime only"):
        regimes.load_regimes()


def test_out_of_order_regimes_raise(tmp_path, monkeypatch):
    _write(tmp_path, monkeypatch, {"XX": [
        {"valid_from": "2010-01-01", "point_value": 5.0, "name": "a", "source": "s" * 50},
        {"valid_from": "2000-01-01", "point_value": 10.0, "name": "b", "source": "s" * 50},
    ]})
    with pytest.raises(RegimeError, match="valid_from order"):
        regimes.load_regimes()


def test_two_regimes_on_one_date_raise(tmp_path, monkeypatch):
    _write(tmp_path, monkeypatch, {"XX": [
        {"valid_from": "2000-01-01", "point_value": 5.0, "name": "a", "source": "s" * 50},
        {"valid_from": "2000-01-01", "point_value": 10.0, "name": "b", "source": "s" * 50},
    ]})
    with pytest.raises(RegimeError, match="same valid_from"):
        regimes.load_regimes()


def test_a_non_positive_point_value_raises(tmp_path, monkeypatch):
    _write(tmp_path, monkeypatch,
           {"XX": [{"valid_from": None, "point_value": 0, "name": "x", "source": "s" * 50}]})
    with pytest.raises(RegimeError, match="point_value"):
        regimes.load_regimes()


# ── the tripwire ──────────────────────────────────────────────────────────


def test_the_last_regime_must_match_the_current_spec(tmp_store):
    """Invariant 2, on a fixture: this is the comparison, exercised where it can fail."""
    store.write_metadata(pd.DataFrame([
        {"Symbol": "RTY", "Point Value": 50.0, "Tick Value": 5.0},
        {"Symbol": "LBR", "Point Value": 27.5, "Tick Value": 13.75},
    ]), source="test")
    for sym, table in regimes.load_regimes().groupby("Symbol"):
        last = table.iloc[-1]
        assert regimes._current_spec(sym, "Point Value") == last["Point_Value"]


def test_the_tripwire_fires_when_the_vendor_moves_under_the_file(tmp_store):
    """A guard that has never fired is indistinguishable from one that is not wired in."""
    store.write_metadata(pd.DataFrame([
        {"Symbol": "RTY", "Point Value": 25.0, "Tick Value": 2.5},   # a third regime
    ]), source="test")
    last = regimes.read_contract_regimes("RTY").iloc[-1]
    assert regimes._current_spec("RTY", "Point Value") != last["Point_Value"]


@pytest.mark.parametrize("field,column", [("Point Value", "Point_Value"),
                                          ("Tick Value", "Tick_Value")])
def test_live_store_agrees_with_the_last_declared_regime(field, column):
    """The real tripwire, against whatever `MARKETDATA_STORE` currently holds.

    Skipped rather than failed when there is no store to check, because CI has no store
    and a red suite there would say nothing about the regime file.

    **The guard needs both arms, and the env one has to come first.** `read_metadata`
    resolves the store root, which RAISES by design when the variable is unset, so a
    guard that only asked whether the specs were empty never got to run in a plain
    shell: the skip was written and what fired was a `RuntimeError` two frames down,
    which reads like a real breakage on an untouched tree rather than a missing
    variable. CI sets the variable to an empty directory, so it is the second arm that
    fires there and the first arm is invisible to CI by construction. Same shape as the
    npf skip guards that keyed on one store after the price read moved to another.
    """
    if not os.environ.get("MARKETDATA_STORE", "").strip():
        pytest.skip("MARKETDATA_STORE is not set: no live store to check regimes against")
    specs = store.read_metadata()
    if specs.empty or "Symbol" not in specs.columns:
        pytest.skip("no contract_specs in MARKETDATA_STORE")
    for sym, table in regimes.load_regimes().groupby("Symbol"):
        if sym not in set(specs["Symbol"].astype(str)):
            continue
        current = regimes._current_spec(sym, field)
        declared = table.iloc[-1][column]
        if pd.isna(current) or pd.isna(declared):
            continue
        assert current == declared, (
            f"{sym}: contract_specs says {field}={current} but the last regime in "
            f"contract_regimes.yaml says {declared}. Either the exchange changed the "
            f"contract again (add a regime) or a regime is wrong. Do NOT edit the last "
            f"regime to match without checking which one moved.")


@pytest.mark.parametrize("value", [None, "", "   "])
def test_the_live_tripwire_skips_by_name_when_the_store_is_unset(monkeypatch, value):
    """The guard above, exercised where it can fail. Same reason as the tripwire itself:
    a guard that has never fired is indistinguishable from one that is not wired in.

    Asserting on the skip REASON, not merely that a skip happened, is the point. The
    failure this replaces was a `RuntimeError` from `config.store_root` that named the
    variable perfectly well and still read as a broken tree, because it arrived as an
    error rather than as a skip. A blank value is the same mistake as an unset one, so
    it has to reach the same skip (`store_root` strips before checking, and so does the
    guard).
    """
    if value is None:
        monkeypatch.delenv("MARKETDATA_STORE", raising=False)
    else:
        monkeypatch.setenv("MARKETDATA_STORE", value)
    with pytest.raises(pytest.skip.Exception, match="MARKETDATA_STORE is not set"):
        test_live_store_agrees_with_the_last_declared_regime("Point Value", "Point_Value")
