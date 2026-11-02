"""`--build-aaii`: the AAII Sentiment Survey from a member spreadsheet.

The real sentiment.xls is AAII member data and is never committed, so the sheet
here is synthetic, laid out the way AAII's SENTIMENT sheet is: title rows, a
header row whose first four cells are Date / Bullish / Neutral / Bearish, weekly
rows (the first ones blank), then summary blocks whose first cell is a label.
Every refusal is checked to have written nothing. No network.
"""
import datetime as dt

import numpy as np
import pandas as pd
import pytest

from marketdata import bars, store, update
from marketdata.providers import aaii as aprov
from marketdata.providers.tradingview import RawError
from marketdata.registry import REGISTRY

SYMS = ("AAII_BULLISH", "AAII_NEUTRAL", "AAII_BEARISH")

# Two real published weeks (the registry anchors) around one blank week.
WEEKS = [
    (dt.datetime(1987, 6, 26), None, None, None),
    (dt.datetime(1987, 7, 24), 0.36, 0.5, 0.14),
    (dt.datetime(1987, 7, 31), None, None, None),
    (dt.datetime(2026, 10, 29), 0.378378, 0.207207, 0.414414),
]


def sheet(weeks=WEEKS, header=("Date", "Bullish", "Neutral", "Bearish")):
    rows = [
        [None, None, None, "American Association of Individual Investors", None],
        [None, None, None, None, None],
        ["Reported", None, None, None, None],
        list(header) + ["Total"],
        [None] * 5,
    ]
    for d, b, n, s in weeks:
        total = None if None in (b, n, s) else b + n + s
        rows.append([d, b, n, s, total])
    rows += [["Observations over life of survey", None, None, None, None],
             ["Avg", 0.375, 0.31, 0.31, None],
             ["Max '87", 0.6, 0.5, 0.4, None]]
    return pd.DataFrame(rows)


@pytest.fixture()
def feed(monkeypatch):
    """Point read_sheet at a synthetic frame; returns a setter."""
    box = {"raw": sheet()}
    monkeypatch.setattr(aprov, "read_sheet", lambda path: box["raw"])
    return lambda raw: box.__setitem__("raw", raw)


def stored(sym):
    return store.read_bars(sym, "series", "aaii")


def test_parse_keeps_published_weeks_and_drops_blanks_and_summary_rows():
    w = aprov.parse_sheet(sheet())
    assert list(w.columns) == list(aprov.LEGS)
    assert list(w.index) == [pd.Timestamp("1987-07-24"), pd.Timestamp("2026-10-29")]
    assert w.loc["1987-07-24", "Bullish"] == 0.36
    assert w.index.name == "Date"


def test_build_writes_three_legs_as_flat_raw_bars(tmp_store, feed):
    res = aprov.build("sentiment.xls")
    assert res["ok"] and res["wrote"] == 3 and res["failed"] == 0
    for sym in SYMS:
        df = stored(sym)
        assert len(df) == 2
        assert (df["Open"] == df["Close"]).all() and (df["High"] == df["Low"]).all()
    bull = bars.get_bars("AAII_BULLISH")
    assert list(bull["Close"]) == [0.36, 0.378378]
    legs = sum(bars.get_bars(s)["Close"] for s in SYMS)
    assert np.allclose(legs, 1.0, atol=1e-6)


def test_rebuild_is_idempotent_and_appends_only_new_weeks(tmp_store, feed):
    feed(sheet(WEEKS[:2]))
    assert aprov.build("old.xls")["wrote"] == 3
    assert len(stored("AAII_BULLISH")) == 1
    feed(sheet())
    assert aprov.build("new.xls")["wrote"] == 3
    assert len(stored("AAII_BULLISH")) == 2
    res = aprov.build("new.xls")
    assert res["ok"] and res["wrote"] == 0


def test_a_changed_stored_week_is_refused_unless_accepted(tmp_store, feed):
    plain = WEEKS[:3] + [(dt.datetime(2026, 10, 22), 0.368715, 0.173184, 0.458101)]
    feed(sheet(plain))
    aprov.build("a.xls")
    revised = plain[:3] + [(dt.datetime(2026, 10, 22), 0.37, 0.171899, 0.458101)]
    feed(sheet(revised))
    res = aprov.build("b.xls")
    assert not res["ok"] and res["partial"]
    assert {e[0] for e in res["errors"]} == {"AAII_BULLISH", "AAII_NEUTRAL"}
    assert stored("AAII_BULLISH")["Close"].iloc[-1] == 0.368715
    res = aprov.build("b.xls", accept_restatement=["AAII_NEUTRAL"])
    assert stored("AAII_NEUTRAL")["Close"].iloc[-1] == 0.171899
    assert {e[0] for e in res["errors"]} == {"AAII_BULLISH"}


def test_accepting_a_value_that_contradicts_an_anchor_is_still_refused(tmp_store, feed):
    aprov.build("a.xls")
    revised = WEEKS[:3] + [(dt.datetime(2026, 10, 29), 0.38, 0.205207, 0.414414)]
    feed(sheet(revised))
    res = aprov.build("b.xls", accept_restatement=["AAII_BULLISH"])
    assert "AAII_BULLISH" in {e[0] for e in res["errors"]}
    assert stored("AAII_BULLISH")["Close"].iloc[-1] == 0.378378


@pytest.mark.parametrize("raw, match", [
    (sheet(header=("Date", "Bull", "Neutral", "Bearish")), "header reads"),
    (sheet([WEEKS[1], WEEKS[1]]), "appears twice"),
    (sheet([WEEKS[3], WEEKS[1]]), "backwards"),
    (sheet([(dt.datetime(1987, 7, 24), 0.36, None, 0.14)]), "some legs blank"),
    (sheet([(dt.datetime(1987, 7, 24), 36.0, 50.0, 14.0)]), "outside 0-1"),
    (sheet([(dt.datetime(1987, 7, 24), 0.36, 0.5, 0.24)]), "sum to"),
    (pd.DataFrame([[1, 2, 3, 4]] * 3), "no 'Date' header"),
])
def test_bad_sheets_are_refused_and_write_nothing(tmp_store, feed, raw, match):
    with pytest.raises(RawError, match=match):
        aprov.parse_sheet(raw)
    feed(raw)
    res = aprov.build("bad.xls")
    assert not res["ok"] and res["wrote"] == 0
    assert all(stored(s).empty for s in SYMS)


def test_a_wrong_anchor_is_refused(tmp_store, feed):
    wrong = list(WEEKS)
    wrong[1] = (dt.datetime(1987, 7, 24), 0.37, 0.49, 0.14)
    feed(sheet(wrong))
    res = aprov.build("x.xls")
    assert not res["ok"]
    assert stored("AAII_BULLISH").empty and stored("AAII_NEUTRAL").empty
    assert not stored("AAII_BEARISH").empty


def test_registry_carries_the_three_legs():
    for sym, col in zip(SYMS, aprov.LEGS):
        s = REGISTRY[sym]
        assert s.aaii == col and s.kind == "fraction" and s.domain == "series"
        assert s.asset_class == "Investor Sentiment"
        assert ("1987-07-24",) == tuple(d for d, _ in s.anchors[:1])


def test_cli_builds_and_scopes(tmp_store, feed):
    assert update.main(["--build-aaii", "sentiment.xls", "--symbols", "AAII_BEARISH"]) == 0
    assert not stored("AAII_BEARISH").empty and stored("AAII_BULLISH").empty


def test_cli_accept_restatement_needs_a_build():
    with pytest.raises(SystemExit):
        update.main(["--check", "--accept-restatement", "AAII_BULLISH"])
