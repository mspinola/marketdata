"""`--build-econ-calendar`: TradingView's calendar capture to a snapshot table.

The fixture is a subset of a real ``mcp-tv-get-economic-calendar`` result (US,
2026-11-12 to 2026-12-10, captured 2026-11-19): every event at importance 0 or 1
that carries a ticker, plus a few ticker-less rows (ISM Services, an auction, the Fed
balance sheet). Rows are the vendor's own, except that the long ``comment`` prose is
dropped to keep the file small; the build ignores that field. No network.
"""
import json
from pathlib import Path

import pandas as pd
import pytest

import marketdata
from marketdata import store, update
from marketdata.providers import tradingview_calendar as cal

FIXTURE = Path(__file__).parent / "fixtures" / "tradingview_calendar_2026-11-19.json"


@pytest.fixture()
def capdir(tmp_store):
    d = cal.capture_dir()
    d.mkdir(parents=True)
    return d


def place(d, name="2026-11-19.json", data=None):
    p = d / name
    if data is None:
        p.write_bytes(FIXTURE.read_bytes())
    else:
        p.write_text(json.dumps(data))
    return p


def fixture_doc():
    return json.loads(FIXTURE.read_text())


def test_layout_sits_beside_the_breadth_raw_root(tmp_store):
    # Under the TradingView raw root, so the `_raw` sync exclusion covers it, and
    # leaves room for a `_news` kind beside it.
    assert cal.capture_dir() == Path(tmp_store) / "_raw" / "tradingview" / "_calendar"
    assert cal.capture_dir("_news").parent == cal.capture_dir().parent
    with pytest.raises(ValueError):
        cal.capture_dir("news")


def test_build_writes_the_snapshot_and_the_reader_returns_it(capdir):
    place(capdir)
    assert update.main(["--build-econ-calendar"]) == 0
    df = marketdata.econ_calendar()
    assert len(df) == len(fixture_doc()["result"])
    assert str(df["time_utc"].dt.tz) == "UTC"
    assert (df["captured"] == pd.Timestamp("2026-11-19")).all()
    # The figures matched against a published member calendar for Nov 16-20 2026.
    row = df[df["ticker"] == "ECONOMICS:USNYESMI"].iloc[0]
    assert (row["actual"], row["forecast"]) == (7.8, 18.0)
    assert row["time_utc"] == pd.Timestamp("2026-11-16 13:30", tz="UTC")
    hs = df[df["ticker"] == "ECONOMICS:USHST"].iloc[0]
    assert (hs["actual"], hs["forecast"], hs["scale"]) == (1.309, 1.245, "M")
    fomc = df[df["ticker"] == "ECONOMICS:USINTR"].iloc[0]
    assert fomc["importance"] == 1 and fomc["category"] == "mny"
    assert "comment" not in df.columns
    m = store.load_manifest()["metadata"]["econ_calendar"]
    assert m["n_rows"] == len(df) and m["source"] == "tradingview"


def test_newest_capture_wins_and_strays_are_ignored(capdir):
    place(capdir, "2026-11-18.json")
    doc = fixture_doc()
    doc["result"] = doc["result"][:3]
    place(capdir, "2026-11-19.json", doc)
    (capdir / "notes.txt").write_text("not a capture")
    (capdir / "2026-11-20.json.tmp").write_text("{")
    assert cal.newest_capture().name == "2026-11-19.json"
    assert cal.build()["ok"]
    assert len(cal.read()) == 3


def test_rewritten_whole_each_build(capdir):
    place(capdir, "2026-11-18.json")
    cal.build()
    doc = fixture_doc()
    doc["result"] = doc["result"][:5]
    place(capdir, "2026-11-19.json", doc)
    cal.build()
    df = cal.read()
    assert len(df) == 5 and (df["captured"] == pd.Timestamp("2026-11-19")).all()


def test_no_capture_is_refused_and_reader_is_empty(tmp_store):
    assert update.main(["--build-econ-calendar"]) == 1
    df = marketdata.econ_calendar()
    assert df.empty and "time_utc" in df.columns


@pytest.mark.parametrize("mutate, needle", [
    (lambda d: d.pop("result"), "'result' list"),
    (lambda d: d.update(status="error"), "status"),
    (lambda d: d.update(result=[]), "empty"),
    (lambda d: d["result"][0].pop("date"), "missing date"),
    (lambda d: d["result"][0].update(date="Nov 19"), "not ISO"),
    (lambda d: d["result"][0].update(date="2026-11-19T13:30:00"), "no zone"),
    (lambda d: d["result"][0].update(importance=2), "importance"),
    (lambda d: d["result"][0].update(actual="7.8"), "actual"),
    (lambda d: d["result"].append(dict(d["result"][0])), "duplicate id"),
])
def test_malformed_capture_is_refused_by_name_and_writes_nothing(capdir, mutate, needle,
                                                                capsys):
    place(capdir, "2026-11-18.json")
    assert cal.build()["ok"]
    before = cal.table_path().read_bytes()
    doc = fixture_doc()
    mutate(doc)
    place(capdir, "2026-11-19.json", doc)
    assert update.main(["--build-econ-calendar"]) == 1
    out = capsys.readouterr().out
    assert "REFUSED" in out and "2026-11-19.json" in out and needle in out
    assert cal.table_path().read_bytes() == before


def test_not_json_is_refused(capdir):
    (capdir / "2026-11-19.json").write_text("Here is the calendar you asked for: ...")
    r = cal.build()
    assert not r["ok"] and "not readable JSON" in r["errors"][0][1]
    assert not cal.table_path().exists()
