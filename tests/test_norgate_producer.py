"""The Norgate producer's fetch/reconstruct/write path, exercised end to end.

ADR-0007 step 2 §7.1 ported this provider from cotdata but not its tests. The existing
suite covers the pieces that are pure functions (`_finals_ready_by_date`,
`_check_roll_gaps`, `_volume_passthrough`, `_targets`) and, since #12, the finals gate
end to end — leaving the code that only runs when `update()` drives it uncovered:

  * volume reconstruction attaches its columns without disturbing `Volume`,
  * First/Second are picked by VOLUME RANK, not expiry order,
  * an incremental run keeps rows it did not re-fetch,
  * `full=True` really does bypass the trailing window,
  * `update()` / `update_metadata()` abort before any fetch when NDU is down,
  * `update_metadata()` refuses to persist an all-null spec row.

cotdata §7.5 deletes its copies of these, so without this file the behaviours ship
untested. Ported from cotdata's `tests/test_norgate_provider.py` and adapted to the
tier-aware store: `write_bars(sym, df, domain=, source=, tier=)` where cotdata had
`write_prices(sym, tier, df, source=)`.

norgatedata is mocked into sys.modules, so this runs on any OS.
"""
import sys
import types
from unittest import mock

import numpy as np
import pandas as pd
import pytest

mock_norgatedata = types.ModuleType("norgatedata")
mock_norgatedata.PaddingType = mock.Mock()
mock_norgatedata.PaddingType.NONE = "NONE"
mock_norgatedata.status = mock.Mock(return_value=True)  # NDU reachable (preflight)
sys.modules["norgatedata"] = mock_norgatedata

from marketdata.providers import norgate  # noqa: E402 (after the sys.modules mock)

# ES is a real registry symbol resolving to Norgate as "&ES", so `_targets(["ES"])`
# needs no patching — the tests drive the same selection path production does.
SYM = "ES"


@pytest.fixture(autouse=True)
def ndu_up(monkeypatch):
    """Every test starts with NDU reachable; the abort tests opt out."""
    monkeypatch.setattr(mock_norgatedata, "status", mock.Mock(return_value=True))


def _continuous(dates, volume, delivery=202609):
    n = len(dates)
    return pd.DataFrame(
        {"Open": [100.0] * n, "High": [101.0] * n, "Low": [99.0] * n,
         "Close": [100.5] * n, "Volume": volume, "Open Interest": [5000] * n,
         "Delivery Month": [delivery] * n},
        index=pd.DatetimeIndex(dates))


def _indiv(dates, volume):
    return pd.DataFrame({"Date": [pd.Timestamp(d) for d in dates], "Volume": volume})


def _written(mock_write_bars, tier="backadj"):
    """The frame written for `tier`. write_bars(sym, df, domain=, source=, tier=)."""
    for call in mock_write_bars.call_args_list:
        if call.kwargs.get("tier") == tier:
            return call.args[1]
    raise AssertionError(f"nothing written for tier {tier!r}")


# ── Volume reconstruction ─────────────────────────────────────────────────
@mock.patch("marketdata.providers.norgate.store.write_bars")
@mock.patch("marketdata.providers.norgate.store.read_bars")
@mock.patch("norgatedata.database_symbols", create=True)
@mock.patch("norgatedata.price_timeseries", create=True)
def test_reconstruction_adds_columns_without_touching_front_month_volume(
        mock_price_ts, mock_db_symbols, mock_read_bars, mock_write_bars, tmp_store):
    """`Volume` stays front-month; the reconstruction lands in parallel columns.

    Consumers choose between the two at read time (`get_bars(volume=...)`), which
    only works if the producer keeps both.
    """
    cont = _continuous(["2026-07-01"], [1000])

    def ts(sym, **kw):
        if "-" not in sym:
            return cont.copy()
        return {"ES-2026H": _indiv(["2026-07-01"], [600]),
                "ES-2026M": _indiv(["2026-07-01"], [400])}.get(sym, pd.DataFrame()).copy()

    mock_price_ts.side_effect = ts
    mock_db_symbols.return_value = ["ES-2026H", "ES-2026M", "ES-2025Z"]
    mock_read_bars.return_value = pd.DataFrame()      # empty store -> full backfill

    norgate.update(symbols=[SYM])

    df = _written(mock_write_bars)
    assert df["Volume"].iloc[0] == 1000               # UNTOUCHED front-month
    assert df["FirstVolume"].iloc[0] == 600
    assert df["SecondVolume"].iloc[0] == 400
    assert df["Volume_Reconstructed"].iloc[0] == 1000
    assert df["Volume_Source"].iloc[0] == "reconstructed"
    assert df["FirstContract"].iloc[0] == "ES-2026H"
    assert df["SecondContract"].iloc[0] == "ES-2026M"


@mock.patch("marketdata.providers.norgate.store.write_bars")
@mock.patch("marketdata.providers.norgate.store.read_bars")
@mock.patch("norgatedata.database_symbols", create=True)
@mock.patch("norgatedata.price_timeseries", create=True)
def test_reconstruction_picks_by_volume_not_expiry(
        mock_price_ts, mock_db_symbols, mock_read_bars, mock_write_bars, tmp_store):
    """Models the GC/SI case: the nearest serial month is near-empty while a later
    contract carries the flow. An expiry-order pick would name the empty serial
    'First' and understate true volume; volume rank must name the dominant one."""
    cont = _continuous(["2026-07-01"], [1000], delivery=202606)

    def ts(sym, **kw):
        if "-" not in sym:
            return cont.copy()
        return {"ES-2026H": _indiv(["2026-07-01"], [50]),      # nearer, near-empty
                "ES-2026M": _indiv(["2026-07-01"], [900])      # later, dominant
                }.get(sym, pd.DataFrame()).copy()

    mock_price_ts.side_effect = ts
    mock_db_symbols.return_value = ["ES-2026H", "ES-2026M"]
    mock_read_bars.return_value = pd.DataFrame()

    norgate.update(symbols=[SYM])

    df = _written(mock_write_bars)
    assert df["FirstContract"].iloc[0] == "ES-2026M"
    assert df["FirstVolume"].iloc[0] == 900
    assert df["SecondContract"].iloc[0] == "ES-2026H"
    assert df["SecondVolume"].iloc[0] == 50
    assert df["Volume_Reconstructed"].iloc[0] == 950


@mock.patch("marketdata.providers.norgate.store.write_bars")
@mock.patch("marketdata.providers.norgate.store.read_bars")
@mock.patch("norgatedata.database_symbols", create=True)
@mock.patch("norgatedata.price_timeseries", create=True)
def test_incremental_run_keeps_rows_it_did_not_refetch(
        mock_price_ts, mock_db_symbols, mock_read_bars, mock_write_bars, tmp_store):
    """Only the trailing window is re-fetched, so history must survive the merge —
    including a row previously recorded as 'raw', which must not silently become
    'reconstructed' just because a later run had contracts available."""
    existing = pd.DataFrame(
        {"Volume": [500, 800], "Volume_Reconstructed": [500, 800],
         "FirstVolume": [np.nan, 500], "SecondVolume": [np.nan, 300],
         "FirstContract": ["", "ES-2026H"], "SecondContract": ["", "ES-2026M"],
         "Volume_Source": ["raw", "reconstructed"]},
        index=pd.DatetimeIndex(["2020-01-01", "2026-06-01"]))
    mock_read_bars.return_value = existing.copy()

    cont = _continuous(["2020-01-01", "2026-06-01", "2026-07-01"], [500, 800, 1000],
                       delivery=0)

    def ts(sym, **kw):
        if "-" not in sym:
            return cont.copy()
        return {"ES-2026U": _indiv(["2026-07-01"], [600]),
                "ES-2026Z": _indiv(["2026-07-01"], [400])}.get(sym, pd.DataFrame()).copy()

    mock_price_ts.side_effect = ts
    mock_db_symbols.return_value = ["ES-2026U", "ES-2026Z"]

    norgate.update(symbols=[SYM])

    df = _written(mock_write_bars)
    assert df.loc["2020-01-01", "Volume_Source"] == "raw"          # preserved
    assert df.loc["2020-01-01", "Volume_Reconstructed"] == 500
    assert df.loc["2026-06-01", "Volume_Source"] == "reconstructed"
    assert df.loc["2026-07-01", "Volume_Source"] == "reconstructed"  # newly computed
    assert df.loc["2026-07-01", "Volume_Reconstructed"] == 1000
    assert df.loc["2026-07-01", "FirstContract"] == "ES-2026U"


@mock.patch("marketdata.providers.norgate.store.write_bars")
@mock.patch("marketdata.providers.norgate.store.read_bars")
@mock.patch("norgatedata.database_symbols", create=True)
@mock.patch("norgatedata.price_timeseries", create=True)
def test_full_rebuild_bypasses_the_incremental_window(
        mock_price_ts, mock_db_symbols, mock_read_bars, mock_write_bars, tmp_store):
    """`full=True` is what a change to the reconstruction LOGIC needs: without it
    the trailing window leaves every older row on the previous algorithm, and the
    series silently mixes two definitions of volume. Checked at the fetch, since
    that is what proves the window was not applied."""
    mock_read_bars.return_value = pd.DataFrame(
        {"Volume": [800], "Volume_Reconstructed": [800],
         "FirstVolume": [500], "SecondVolume": [300],
         "FirstContract": ["ES-2026H"], "SecondContract": ["ES-2026M"],
         "Volume_Source": ["reconstructed"]},
        index=pd.DatetimeIndex(["2026-06-01"]))

    cont = _continuous(["2026-06-01", "2026-07-01"], [800, 1000], delivery=0)

    def ts(sym, **kw):
        if "-" not in sym:
            return cont.copy()
        return _indiv(["2026-07-01"], [1000]) if sym == "ES-2026U" else pd.DataFrame()

    mock_price_ts.side_effect = ts
    mock_db_symbols.return_value = ["ES-2026U"]

    norgate.update(symbols=[SYM], full=True)

    starts = [c.kwargs.get("start_date") for c in mock_price_ts.call_args_list
              if "-" in c.args[0]]
    assert starts, "expected at least one individual-contract fetch"
    assert all(s == "1970-01-01" for s in starts), starts


# ── Both tiers, or neither ────────────────────────────────────────────────
@mock.patch("marketdata.providers.norgate.store.write_bars")
@mock.patch("marketdata.providers.norgate.store.read_bars")
@mock.patch("norgatedata.database_symbols", create=True)
@mock.patch("norgatedata.price_timeseries", create=True)
def test_update_fetches_and_writes_both_stored_tiers(
        mock_price_ts, mock_db_symbols, mock_read_bars, mock_write_bars, tmp_store):
    """Norgate selects adjustment by SYMBOL SUFFIX, so a wrong symbol yields the
    wrong series rather than an error. Both the fetched symbols and the written
    tiers are asserted, because `propadj` is derived from the pair."""
    mock_db_symbols.return_value = []
    mock_read_bars.return_value = pd.DataFrame()
    mock_price_ts.return_value = _continuous(["2026-07-01"], [1000])

    norgate.update(symbols=[SYM])

    fetched = [c.args[0] for c in mock_price_ts.call_args_list]
    assert fetched == ["&ES_CCB", "&ES"]          # backadj by suffix, then unadj

    written = [(c.args[0], c.kwargs["tier"]) for c in mock_write_bars.call_args_list]
    assert written == [("ES", "backadj"), ("ES", "unadj")]
    assert all(c.kwargs["source"] == "norgate" for c in mock_write_bars.call_args_list)


# ── NDU down ──────────────────────────────────────────────────────────────
def test_update_aborts_before_any_fetch_when_ndu_is_unreachable(monkeypatch):
    """`norgatedata` retries 10x then calls bare `sys.exit()`, which exits 0 — a
    scheduled run would look successful having written nothing, and never trigger
    the scheduler's retry. `status()` returning False is the trip wire."""
    monkeypatch.setattr(mock_norgatedata, "status", mock.Mock(return_value=False))
    with mock.patch("norgatedata.price_timeseries", create=True) as ts:
        with pytest.raises(RuntimeError, match="not reachable"):
            norgate.update(symbols=[SYM])
    ts.assert_not_called()


def test_update_metadata_aborts_when_ndu_is_unreachable(monkeypatch):
    monkeypatch.setattr(mock_norgatedata, "status", mock.Mock(return_value=False))
    with pytest.raises(RuntimeError, match="not reachable"):
        norgate.update_metadata(symbols=[SYM])


def test_a_probe_that_raises_is_treated_as_unreachable(monkeypatch):
    """The guard exists because norgatedata fails in unusual ways; a probe that
    blows up must not be read as a pass."""
    monkeypatch.setattr(mock_norgatedata, "status",
                        mock.Mock(side_effect=RuntimeError("NDU pipe closed")))
    with pytest.raises(RuntimeError, match="not reachable"):
        norgate.update(symbols=[SYM])


# ── Contract specs ────────────────────────────────────────────────────────
def test_all_null_spec_rows_are_skipped_not_persisted(tmp_store):
    """A covered symbol whose specs ALL come back None is a transient Norgate
    failure, not data. Persisting it would put a null row in the shared table —
    and on a scoped run, overwrite good specs with nulls."""
    from marketdata import store

    def fake_meta(sym):
        base = {"Symbol": sym, "Norgate_Symbol": f"&{sym}_CCB",
                **{k: None for k in norgate._SPEC_FIELDS}}
        return base if sym == "GC" else {**base, "Tick Size": 0.25}

    with mock.patch("marketdata.providers.norgate.get_symbol_metadata",
                    side_effect=fake_meta):
        res = norgate.update_metadata(symbols=["ES", "GC"])

    assert set(store.read_metadata()["Symbol"]) == {"ES"}   # GC's null row skipped
    assert res["symbols_failed"] == ["GC"] and not res["ok"]


def test_symbols_norgate_does_not_cover_are_never_fetched():
    """Yahoo-only markets (registry `norgate: null`) must not reach the Norgate
    metadata producer. The regression it prevents: `&MME_CCB not found` spam plus
    all-null spec rows in contract_specs."""
    covered = mock.Mock(internal="ES", domain="futures", norgate="&ES")
    uncovered = mock.Mock(internal="MME", domain="futures", norgate=None)

    with mock.patch("marketdata.providers.norgate.all_symbols",
                    return_value=[covered, uncovered]), \
         mock.patch("marketdata.providers.norgate.resolve_source",
                    return_value=norgate.NAME), \
         mock.patch.dict("marketdata.providers.norgate.REGISTRY",
                         {"ES": covered, "MME": uncovered}, clear=True):
        assert [s.internal for s in norgate._targets()] == ["ES"]


# ── Finals gate: input normalisation ──────────────────────────────────────
def test_finals_gate_compares_dates_whatever_it_is_handed():
    """Not a duplicate of `test_cli_finals.py`, which drives the gate from the CLI
    with dates already normalised. This is the input-shape case cotdata's suite
    covered separately: the two sides come from different places — Norgate's last
    bar index and the store's manifest string — so one arriving as a datetime (or
    tz-aware) must not make a same-day comparison look like a new session."""
    import datetime as dt

    same_day_dt = dt.datetime(2026, 8, 7, 21, 30)
    assert not norgate._finals_ready_by_date(same_day_dt, dt.date(2026, 8, 7))[0]
    assert norgate._finals_ready_by_date(same_day_dt, dt.date(2026, 8, 6))[0]

    aware = dt.datetime(2026, 8, 7, 21, 30, tzinfo=dt.timezone.utc)
    assert not norgate._finals_ready_by_date(aware, dt.date(2026, 8, 7))[0]

    # And the detail is reported as plain ISO dates either way, for the log line.
    _, detail = norgate._finals_ready_by_date(same_day_dt, dt.date(2026, 8, 6))
    assert detail == {"norgate_last": "2026-08-07", "store_last": "2026-08-06"}
