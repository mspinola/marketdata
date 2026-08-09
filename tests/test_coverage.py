"""The startup guard: can this store serve the universe asked for? No network.

The condition these tests exist for is not hypothetical. ADR-0007 step 4 repointed
cotmetrics at this package while the futures bars were still in cotdata's store, and
the result was a dashboard that booted, bound its port, rendered every positioning
page and drew blank price charts, because a futures read against a store with no
`bars/futures/` returns an empty frame rather than raising. It went unnoticed until
someone looked at a chart. Every case below is one shape of that silence.
"""
import datetime as dt
import json
import pathlib

import pandas as pd
import pytest

from marketdata import store
from marketdata.provenance import CoverageError, coverage_gaps, require_coverage


def eq_frame(n, start="2020-01-01"):
    idx = pd.date_range(start, periods=n, freq="B")
    c = pd.Series(range(1, n + 1), index=idx, dtype=float)
    return pd.DataFrame(
        {"Open": c, "High": c, "Low": c, "Close": c, "Volume": 1_000.0,
         "Dividends": 0.0, "Stock Splits": 0.0, "Capital Gains": 0.0}, index=idx)


def fut_frame(n, start="2020-01-01"):
    idx = pd.date_range(start, periods=n, freq="B")
    c = pd.Series(range(1, n + 1), index=idx, dtype=float)
    return pd.DataFrame(
        {"Open": c, "High": c, "Low": c, "Close": c, "Volume": 1_000.0,
         "Open Interest": 5_000.0, "Delivery Month": 202003.0}, index=idx)


def write_futures(symbol, n=50, start="2020-01-01", tiers=("backadj", "unadj")):
    for tier in tiers:
        store.write_bars(symbol, fut_frame(n, start), domain="futures",
                         source="norgate", tier=tier)


def test_a_store_that_can_serve_reports_no_gaps(tmp_store):
    write_futures("ES")
    store.write_bars("SPY", eq_frame(50), domain="equities", source="yfinance")
    assert coverage_gaps(["ES", "SPY"]) == []


def test_absent_is_the_case_that_used_to_be_silent(tmp_store):
    """An empty store. `get_bars` returns 0 rows here and raises nothing, which is
    the documented read semantics; the guard is what turns it into a refusal."""
    gaps = coverage_gaps(["ES"])
    assert {g.reason for g in gaps} == {"absent"}
    # Both stored tiers are reported, not just the default one.
    assert {g.tier for g in gaps} == {"backadj", "unadj"}


def test_half_an_import_is_a_gap_not_a_pass(tmp_store):
    """`backadj` present and `unadj` missing reads perfectly on the default tier
    and then mis-scales anything asking for `propadj`, which needs the pair."""
    write_futures("ES", tiers=("backadj",))
    gaps = coverage_gaps(["ES"])
    assert [(g.tier, g.reason) for g in gaps] == [("unadj", "absent")]


def test_short_history_is_only_checked_when_a_start_is_stated(tmp_store):
    write_futures("ES", n=50, start="2015-01-01")
    assert coverage_gaps(["ES"]) == []
    gaps = coverage_gaps(["ES"], start="2010-01-01")
    assert {g.reason for g in gaps} == {"short"}
    assert "2015-01-01" in gaps[0].detail
    assert coverage_gaps(["ES"], start="2016-01-01") == []


def test_staleness_is_opt_in_and_measured_against_as_of(tmp_store):
    write_futures("ES", n=10, start="2020-01-01")   # newest bar 2020-01-14
    as_of = dt.date(2020, 2, 1)
    # Off by default: no tolerance stated, no opinion offered.
    assert coverage_gaps(["ES"], as_of=as_of) == []
    gaps = coverage_gaps(["ES"], stale_after_days=7, as_of=as_of)
    assert {g.reason for g in gaps} == {"stale"}
    assert "2020-01-14" in gaps[0].detail
    # Inside the tolerance, nothing is reported.
    assert coverage_gaps(["ES"], stale_after_days=90, as_of=as_of) == []


def test_a_weekend_does_not_read_as_stale(tmp_store):
    """The reason the tolerance is the caller's to choose: bars only move on
    trading days, so a Monday reading of three days old is a healthy store."""
    write_futures("ES", n=10, start="2020-01-01")
    monday = pd.Timestamp("2020-01-14").date() + dt.timedelta(days=4)
    assert coverage_gaps(["ES"], stale_after_days=7, as_of=monday) == []


def test_a_file_with_no_manifest_row_reports_empty(tmp_store):
    write_futures("ES")
    manifest = store.load_manifest()
    manifest["bars"].pop("futures/norgate/ES_backadj")
    (pathlib.Path(tmp_store) / "manifest.json").write_text(json.dumps(manifest))
    gaps = coverage_gaps(["ES"])
    assert [(g.tier, g.reason) for g in gaps] == [("backadj", "empty")]


def test_require_coverage_is_silent_when_the_store_is_good(tmp_store):
    write_futures("ES")
    assert require_coverage(["ES"]) is None


def test_require_coverage_names_every_gap_and_counts_by_reason(tmp_store):
    write_futures("ES", tiers=("backadj",))
    with pytest.raises(CoverageError) as e:
        require_coverage(["ES", "GC"], start="2010-01-01")
    msg = str(e.value)
    assert "ES" in msg and "GC" in msg
    assert "absent" in msg and "short" in msg
    # The count line groups by reason, so a reader can tell an unfilled store
    # from a lookback the vendor cannot support without reading every line.
    assert "3 absent" in msg


def test_gap_str_is_one_line(tmp_store):
    gaps = coverage_gaps(["ES"])
    assert str(gaps[0]).startswith("ES backadj: absent, ")
    assert "\n" not in str(gaps[0])
