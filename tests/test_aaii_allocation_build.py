"""`--build-aaii-allocation`: the AAII Asset Allocation Survey from a member spreadsheet.

The real asset.xls is AAII member data and is never committed, so the sheet here is
synthetic, laid out the way AAII's is: two title rows, a header row (Stock Funds /
Stocks / Bond Funds / Bonds / Cash / Total, a blank, then the headline Stocks / Bonds /
Cash), one row per month, then summary rows whose first cell is a label. The first
and last months carry the registry anchors' real values (already public in the
registry); the months between are invented. Every refusal is checked to have written
nothing. No network.
"""
import datetime as dt

import numpy as np
import pandas as pd
import pytest

from marketdata import bars, store, update
from marketdata.providers import aaii_allocation as al
from marketdata.providers.tradingview import RawError
from marketdata.registry import PRICE_SOURCES, REGISTRY

SYMS = tuple(k for k in REGISTRY if k.startswith("AAII_ALLOC_"))
FIRST = dt.datetime(1987, 11, 30)
# The anchor months as AAII's file has them: five detailed shares, then the headline.
FIRST_ROW = (0.1858960396039604, 0.2674504950495049, 0.08753465346534654, 0.1105,
             0.3491039603960396, 0.453, 0.198, 0.349)
LAST_ROW = (0.38152, 0.319197, 0.109143, 0.057073, 0.133068, 0.701, 0.166, 0.133)
N_MONTHS = 468   # November 1987 to October 2026


def month_end(i):
    return (pd.Timestamp(FIRST).to_period("M") + i).to_timestamp("M").to_pydatetime()


def invented(i):
    """A plausible month: detailed shares that sum to 1, headline as their rounding."""
    sf, sd, bf, bd = 0.2 + 0.001 * (i % 50), 0.27, 0.08, 0.1
    cash = 1 - sf - sd - bf - bd
    return (sf, sd, bf, bd, cash, round(sf + sd, 3), round(bf + bd, 3), round(cash, 3))


def rows(n=N_MONTHS):
    out = [(month_end(i), invented(i)) for i in range(n)]
    out[0] = (month_end(0), FIRST_ROW)
    if n == N_MONTHS:
        out[-1] = (month_end(n - 1), LAST_ROW)
    return out


def sheet(months=None, header=("Stock Funds", "Stocks", "Bond Funds", "Bonds", "Cash",
                               "Total", None, "Stocks", "Bonds", "Cash")):
    months = rows() if months is None else months
    out = [["American Association of Individual Investors"] + [None] * 10,
           ["AAII Individual Investor Asset Allocation Survey"] + [None] * 10,
           [None] + list(header)]
    for d, v in months:
        detail, head = list(v[:5]), list(v[5:])
        total = None if None in detail else sum(detail)
        out.append([d] + detail + [total, None] + head)
    out += [[None] * 11, ["Average", 0.32, 0.31, 0.1, 0.06, 0.22, 1.0, None, 0.6, 0.16, 0.22],
            ["Maximum"] + [0.5] * 10]
    return pd.DataFrame(out)


@pytest.fixture()
def feed(monkeypatch):
    """Point read_sheet at a synthetic frame; returns a setter."""
    box = {"raw": sheet()}
    monkeypatch.setattr(al, "read_sheet", lambda path: box["raw"])
    return lambda raw: box.__setitem__("raw", raw)


def stored(sym):
    return store.read_bars(sym, "series", al.NAME)


def test_the_registry_wires_seven_series_to_the_new_source():
    assert al.NAME in PRICE_SOURCES
    assert len(SYMS) == 7
    assert {REGISTRY[s].aaii_allocation for s in SYMS} == set(al.STORABLE)
    assert all(REGISTRY[s].kind == "fraction" and REGISTRY[s].domain == "series"
               for s in SYMS)


def test_parse_reads_one_row_per_month_on_its_month_end():
    m, repaired = al.parse_sheet(sheet())
    assert len(m) == N_MONTHS and repaired == []
    assert m.index[0] == pd.Timestamp("1987-11-30") and m.index[-1] == pd.Timestamp("2026-10-31")
    assert m.index.name == "Date" and m.index.is_unique and m.index.is_monotonic_increasing
    assert m.loc["2026-10-31", "stocks"] == 0.701 and m.loc["2026-10-31", "stocks_direct"] == 0.319197


def test_the_day_in_aaiis_date_does_not_matter_only_the_month():
    """AAII types the 30th, the 1st or the 24th; the calendar month is the survey."""
    months = rows()
    days = {1: 1, 2: 24, 3: 25}
    for i, day in days.items():
        months[i] = (months[i][0].replace(day=day), months[i][1])
    m, _ = al.parse_sheet(sheet(months))
    assert list(m.index[1:4]) == [pd.Timestamp(month_end(i)) for i in days]


def test_a_year_label_one_off_is_re_dated_and_reported():
    """AAII's file labels Feb to Dec 2006 as 2005, after a correct January 2006."""
    months = rows()
    jan_2006 = next(i for i, (d, _) in enumerate(months) if (d.year, d.month) == (2006, 1))
    for i in range(jan_2006 + 1, jan_2006 + 12):
        d, v = months[i]
        months[i] = (d.replace(year=2005), v)
    m, repaired = al.parse_sheet(sheet(months))
    assert len(m) == N_MONTHS and m.index.is_unique
    assert len(repaired) == 11
    assert repaired[0] == (dt.date(2005, 2, 28), dt.date(2006, 2, 28))
    assert repaired[-1] == (dt.date(2005, 12, 31), dt.date(2006, 12, 31))


def _skip_a_month():
    m = rows()
    return m[:10] + m[11:]


def _repeat_a_month():
    m = rows()
    return m[:10] + [m[9]] + m[10:]


def _swap_two_months():
    m = rows()
    m[10], m[11] = m[11], m[10]
    return m


def _with(i, values):
    m = rows()
    m[i] = (m[i][0], values)
    return m


@pytest.mark.parametrize("raw, match", [
    (sheet(_skip_a_month()), "expected a"),
    (sheet(_repeat_a_month()), "expected a"),
    (sheet(_swap_two_months()), "expected a"),
    (sheet(header=("Stock Funds", "Stocks", "Bond Funds", "Bonds", "Cash", "Total", None,
                   "Equity", "Bonds", "Cash")), "no header row"),
    (sheet(_with(5, (0.2, None, 0.08, 0.1, 0.35, 0.47, 0.18, 0.35))), "blank share"),
    (sheet(_with(5, (20.0, 27.0, 8.0, 10.0, 35.0, 47.0, 18.0, 35.0))), "outside 0-1"),
    (sheet(_with(5, (0.2, 0.27, 0.08, 0.1, 0.45, 0.47, 0.18, 0.35))), "detailed shares sum"),
    (sheet(_with(5, (0.2, 0.27, 0.08, 0.1, 0.35, 0.47, 0.18, 0.40))), "headline shares sum"),
    (sheet(_with(5, (0.2, 0.27, 0.08, 0.1, 0.35, 0.474, 0.176, 0.35))), "from its detailed parts"),
    (pd.DataFrame([[1] * 11] * 3), "no header row"),
])
def test_bad_sheets_are_refused_and_write_nothing(tmp_store, feed, raw, match):
    with pytest.raises(RawError, match=match):
        al.parse_sheet(raw)
    feed(raw)
    res = al.build("bad.xls")
    assert not res["ok"] and res["wrote"] == 0
    assert all(stored(s).empty for s in SYMS)


def test_build_writes_seven_series_as_flat_raw_bars(tmp_store, feed):
    res = al.build("asset.xls")
    assert res["ok"] and res["wrote"] == 7 and res["failed"] == 0
    for sym in SYMS:
        df = stored(sym)
        assert len(df) == N_MONTHS
        assert (df["Open"] == df["Close"]).all() and (df["High"] == df["Low"]).all()
    head = sum(bars.get_bars(s)["Close"] for s in
               ("AAII_ALLOC_STOCKS", "AAII_ALLOC_BONDS", "AAII_ALLOC_CASH"))
    assert np.allclose(head, 1.0, atol=0.002)
    assert bars.get_bars("AAII_ALLOC_STOCKS")["Close"].iloc[-1] == 0.701


def test_rebuild_is_idempotent_and_appends_only_new_months(tmp_store, feed):
    # An older download ends a month earlier, and its last month is not an anchor.
    feed(sheet(rows()[:-1]))
    assert al.build("old.xls")["wrote"] == 7
    assert len(stored("AAII_ALLOC_STOCKS")) == N_MONTHS - 1
    feed(sheet())
    assert al.build("new.xls")["wrote"] == 7
    assert len(stored("AAII_ALLOC_STOCKS")) == N_MONTHS
    res = al.build("new.xls")
    assert res["ok"] and res["wrote"] == 0


def test_a_changed_stored_month_is_refused_unless_accepted(tmp_store, feed):
    al.build("a.xls")
    revised = _with(5, invented(5)[:5] + (round(invented(5)[5] + 0.001, 3),) + invented(5)[6:])
    feed(sheet(revised))
    res = al.build("b.xls")
    assert not res["ok"] and res["partial"]
    assert {e[0] for e in res["errors"]} == {"AAII_ALLOC_STOCKS"}
    before = stored("AAII_ALLOC_STOCKS")["Close"].iloc[5]
    res = al.build("b.xls", accept_restatement=["AAII_ALLOC_STOCKS"])
    assert res["ok"] and stored("AAII_ALLOC_STOCKS")["Close"].iloc[5] == pytest.approx(before + 0.001)


def test_a_wrong_anchor_is_refused(tmp_store, feed):
    wrong = rows()
    wrong[-1] = (wrong[-1][0], LAST_ROW[:5] + (0.702, 0.165, 0.133))
    feed(sheet(wrong))
    res = al.build("x.xls")
    assert not res["ok"]
    assert stored("AAII_ALLOC_STOCKS").empty and stored("AAII_ALLOC_BONDS").empty
    assert not stored("AAII_ALLOC_CASH").empty      # its anchor still matches


def test_cli_builds_and_scopes(tmp_store, feed):
    assert update.main(["--build-aaii-allocation", "asset.xls", "--symbols",
                        "AAII_ALLOC_CASH"]) == 0
    assert not stored("AAII_ALLOC_CASH").empty and stored("AAII_ALLOC_STOCKS").empty


def test_cli_accepts_restatement_with_this_build():
    with pytest.raises(SystemExit):
        update.main(["--check", "--accept-restatement", "AAII_ALLOC_STOCKS"])
