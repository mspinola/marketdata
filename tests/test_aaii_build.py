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


# ── provisional weeks (hand-kept from the public page, a separate series) ────

PROV = tuple(f"{s}_PROVISIONAL" for s in SYMS)


def hand(rows, header="week,bullish,neutral,bearish,source"):
    """Write the hand-kept file: rows [("YYYY-MM-DD", bull, neutral, bear), ...]."""
    root = aprov.raw_root()
    root.mkdir(parents=True, exist_ok=True)
    lines = [header] + [",".join(str(x) for x in (*r, "sent_results")) for r in rows]
    (root / aprov.PROVISIONAL_FILE).write_text("\n".join(lines) + "\n", encoding="utf-8")


def provisional(sym="AAII_BULLISH_PROVISIONAL"):
    return store.read_bars(sym, "series", "aaii")


# The public page's rows for the weeks the spreadsheet reports on 10-29 and 11-05.
OCT_28 = ("2026-10-28", 37.8, 20.7, 41.4)
NOV_4 = ("2026-11-04", 34.1, 17.5, 48.4)


def test_registry_carries_three_provisional_legs_each_naming_its_official_one():
    for sym, off in zip(PROV, SYMS):
        s = REGISTRY[sym]
        assert s.aaii == aprov.PROVISIONAL and s.kind == "fraction" and s.domain == "series"
        assert aprov.official_of(s).internal == off
    assert {s.internal for s in aprov._targets()} == set(SYMS)


def test_public_wednesday_lands_on_the_spreadsheets_thursday(tmp_path):
    p = tmp_path / "provisional.csv"
    p.write_text("week,bullish,neutral,bearish,source\n"
                 "2026-11-04, 34.1, 17.5, 48.4%, sent_results\n", encoding="utf-8")
    got = aprov.read_provisional(p)
    assert list(got.index) == [pd.Timestamp("2026-11-05")]
    assert got.index[0].day_name() == "Thursday"
    assert list(got.iloc[0]) == pytest.approx([0.341, 0.175, 0.484])


def test_provisional_weeks_after_the_official_end_go_to_their_own_legs(tmp_store, feed):
    hand([OCT_28, NOV_4])
    res = aprov.build("sentiment.xls")                     # official ends 2026-10-29
    assert res["ok"] and res["wrote"] == 6
    assert stored("AAII_BULLISH").index.max() == pd.Timestamp("2026-10-29")
    for sym, want in zip(PROV, (0.341, 0.175, 0.484)):
        got = bars.get_bars(sym)["Close"]
        assert list(got.index) == [pd.Timestamp("2026-11-05")]
        assert got.iloc[0] == pytest.approx(want)
    spread = (bars.get_bars(PROV[0])["Close"] - bars.get_bars(PROV[2])["Close"]) * 100
    assert spread.iloc[0] == pytest.approx(-14.3)


def test_a_newer_spreadsheet_supersedes_provisional_weeks_and_measures_them(
        tmp_store, feed, capsys):
    feed(sheet(WEEKS[:2]))                                  # official ends 1987-07-24
    aprov.build("old.xls")
    hand([OCT_28, NOV_4])
    assert aprov.build_provisionals()["ok"]
    assert len(provisional()) == 2
    feed(sheet())                                           # now through 2026-10-29
    capsys.readouterr()
    assert aprov.build("new.xls")["ok"]
    assert list(provisional().index) == [pd.Timestamp("2026-11-05")]
    assert stored("AAII_BULLISH")["Close"].iloc[-1] == 0.378378   # official, untouched
    out = capsys.readouterr().out
    # 37.8 printed against 37.8378 stored: the page's rounding, measured.
    assert "1 provisional week(s) now official" in out and "max abs 0.04" in out


def test_a_superseded_week_with_no_official_twin_is_reported(tmp_store, feed, capsys):
    aprov.build("sentiment.xls")
    hand([("2026-10-21", 36.9, 17.3, 45.8)])                # official 10-22 is not in WEEKS
    capsys.readouterr()
    aprov.build_provisionals()
    assert "were not compared" in capsys.readouterr().out
    assert provisional().empty


def test_an_unchanged_provisional_series_is_not_rewritten(tmp_store, feed):
    aprov.build("sentiment.xls")
    hand([NOV_4])
    assert aprov.build_provisionals()["wrote"] == 3
    assert aprov.build_provisionals()["wrote"] == 0


def test_no_hand_file_writes_no_provisional_series(tmp_store, feed):
    assert aprov.build("sentiment.xls")["wrote"] == 3
    assert all(provisional(s).empty for s in PROV)


@pytest.mark.parametrize("rows, header, match", [
    ([("2026-11-04", "n/a", 17.5, 48.4)], None, "does not parse"),
    ([("11/04/2026", 34.1, 17.5, 48.4)], None, "does not parse"),
    ([("2026-11-05", 34.1, 17.5, 48.4)], None, "is a Thursday"),
    ([("2026-11-04", 0.341, 0.175, 0.484)], None, "not 100"),
    ([("2026-11-04", 34.1, 17.5, 58.4)], None, "not 100"),
    ([("2026-11-04", 134.1, -82.5, 48.4)], None, "outside 0-100"),
    ([NOV_4, NOV_4], None, "appears twice"),
    ([("2026-11-04", 34.1, 17.5, 48.4)], "week,bullish,neutral,bearish", "missing"),
])
def test_a_bad_hand_file_is_refused_and_writes_nothing(tmp_store, feed, rows, header, match):
    hand(rows, **({"header": header} if header else {}))
    if header:      # the helper appends a source column; drop it to match the header
        p = aprov.raw_root() / aprov.PROVISIONAL_FILE
        p.write_text("\n".join(line.rsplit(",", 1)[0] for line in
                               p.read_text().splitlines()) + "\n")
    res = aprov.build("sentiment.xls")
    assert not res["ok"] and res["partial"]                 # the official build still ran
    assert match in res["errors"][0][1]
    assert not stored("AAII_BULLISH").empty
    assert all(provisional(s).empty for s in PROV)


def test_cli_builds_the_provisional_legs_alone(tmp_store, feed):
    aprov.build("sentiment.xls")
    hand([NOV_4])
    assert update.main(["--build-aaii-provisional"]) == 0
    assert not provisional().empty
    assert update.main(["--build-aaii-provisional", "--symbols",
                        "AAII_BEARISH_PROVISIONAL"]) == 0


# ── adding a week from the command line ─────────────────────────────────────

NOV_11 = (30.8, 17.8, 51.4)


def hand_text():
    return (aprov.raw_root() / aprov.PROVISIONAL_FILE).read_bytes().decode("utf-8")


@pytest.mark.parametrize("today, want", [
    (dt.date(2026, 11, 12), dt.date(2026, 11, 11)),     # Thursday: yesterday's survey
    (dt.date(2026, 11, 11), dt.date(2026, 11, 11)),     # Wednesday: itself
    (dt.date(2026, 11, 10), dt.date(2026, 11, 4)),      # Tuesday: last week's
    (dt.date(2026, 11, 15), dt.date(2026, 11, 11))])    # Sunday
def test_the_latest_survey_week_is_the_last_wednesday(today, want):
    assert aprov.latest_survey_week(today) == want


def test_add_appends_the_row_as_printed_and_builds_it(tmp_store, feed):
    aprov.build("sentiment.xls")                           # official ends 2026-10-29
    hand([NOV_4])
    msg = aprov.add_provisional_week(*NOV_11, today=dt.date(2026, 11, 12))
    assert "added week 2026-11-11" in msg and "stored on 2026-11-12" in msg
    assert hand_text().splitlines()[-1] == \
        "2026-11-11,30.8,17.8,51.4,aaii.com/sentimentsurvey/sent_results"
    assert aprov.build_provisionals()["ok"]
    got = (bars.get_bars(PROV[0])["Close"] - bars.get_bars(PROV[2])["Close"]) * 100
    assert list(got.index) == [pd.Timestamp("2026-11-05"), pd.Timestamp("2026-11-12")]
    assert got.iloc[-1] == pytest.approx(-20.6)


def test_add_keeps_the_producers_crlf_and_creates_a_missing_file(tmp_store):
    root = aprov.raw_root()
    root.mkdir(parents=True, exist_ok=True)
    path = root / aprov.PROVISIONAL_FILE
    path.write_bytes(b"week,bullish,neutral,bearish,source\r\n"
                     b"2026-11-04,34.1,17.5,48.4,sent_results")      # no final newline
    aprov.add_provisional_week(*NOV_11, week=dt.date(2026, 11, 11))
    assert path.read_bytes().endswith(
        b"sent_results\r\n2026-11-11,30.8,17.8,51.4,aaii.com/sentimentsurvey/sent_results\r\n")
    path.unlink()
    aprov.add_provisional_week(*NOV_11, week=dt.date(2026, 11, 11))
    assert hand_text().splitlines() == [
        "week,bullish,neutral,bearish,source",
        "2026-11-11,30.8,17.8,51.4,aaii.com/sentimentsurvey/sent_results"]


def test_adding_the_same_week_twice_changes_nothing(tmp_store):
    aprov.add_provisional_week(*NOV_11, week=dt.date(2026, 11, 11))
    before = hand_text()
    msg = aprov.add_provisional_week(*NOV_11, week=dt.date(2026, 11, 11))
    assert "already in" in msg and hand_text() == before


@pytest.mark.parametrize("row, week, match", [
    ((30.8, 17.8, 50.4), "2026-11-11", "sum to 99"),               # a typo
    ((0.308, 0.178, 0.514), "2026-11-11", "sum to"),               # decimals, not percent
    (NOV_11, "2026-11-12", "is a Thursday"),
    ((31.0, 17.8, 51.2), "2026-11-04", "already in provisional.csv as"),
    (NOV_4[1:], "2026-11-11", "page has probably not published"),  # last week's numbers
])
def test_a_bad_add_is_refused_and_writes_nothing(tmp_store, row, week, match):
    hand([NOV_4])
    before = hand_text()
    with pytest.raises(RawError, match=match):
        aprov.add_provisional_week(*row, week=dt.date.fromisoformat(week))
    assert hand_text() == before
    assert not (aprov.raw_root() / "provisional.csv.new").exists()


def test_cli_adds_a_week_and_builds_it(tmp_store, feed):
    aprov.build("sentiment.xls")
    hand([NOV_4])
    assert update.main(["--add-aaii-week", "30.8", "17.8", "51.4",
                        "--aaii-week", "2026-11-11"]) == 0
    assert list(provisional().index)[-1] == pd.Timestamp("2026-11-12")
    # A refused add exits non-zero and builds nothing.
    assert update.main(["--add-aaii-week", "30.8", "17.8", "51.4",
                        "--aaii-week", "2026-11-18"]) == 1
    assert list(provisional().index)[-1] == pd.Timestamp("2026-11-12")


def test_cli_aaii_week_needs_an_add():
    with pytest.raises(SystemExit):
        update.main(["--aaii-week", "2026-11-11"])
