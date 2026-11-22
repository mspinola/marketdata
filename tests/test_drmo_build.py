"""`--build-drmo`: weekly Dr. Mo transcriptions to an append-only history table.

EVERY ROW HERE IS INVENTED. The assets, tickers, groups, regime names and readings
are made up for the test and correspond to no published report: the real table is
42 Macro subscriber data, and only its schema lives in this repository. No network.
"""
import datetime as dt
from pathlib import Path

import pandas as pd
import pytest

import marketdata
from marketdata import store, update
from marketdata.providers import fortytwo_drmo as drmo

HEADER = "report_date,regime,group,asset,ticker,vams,rsi,proper_trade"
W1, W2, W3 = dt.date(2021, 3, 6), dt.date(2021, 3, 13), dt.date(2021, 3, 20)
TODAY = dt.date(2021, 3, 21)

# (group, asset, ticker, vams, rsi, proper_trade)
BASE = [
    ("Group One", "Alpha Fund", "AAA", "bullish", 61, "long_max"),
    ("Group One", "Beta Fund", "BBB", "neutral", 48, "long_half"),
    ("Group Two", "Gamma, Quoted", "CCC", "bearish", 22, "short_max"),
    ("Group Two", "Delta Fund", "DDD", "neutral", 35, "short_half"),
    ("Group Two", "Tickerless Coin", "", "bearish", 40, "none"),
]


def text(date, rows=BASE, regime="TESTREGIME", header=HEADER, shift=0):
    lines = [header]
    for g, a, t, v, r, p in rows:
        a = f'"{a}"' if "," in a else a
        lines.append(f"{date},{regime},{g},{a},{t},{v},{r + shift},{p}")
    return "\n".join(lines) + "\n"


@pytest.fixture()
def capdir(tmp_store):
    d = drmo.capture_dir()
    d.mkdir(parents=True)
    return d


def place(d, date, **kw):
    p = d / f"{date}.csv"
    p.write_text(text(date, **kw), encoding="utf-8")
    return p


def build(*a, **kw):
    kw.setdefault("today", TODAY)
    return drmo.build(*a, **kw)


def test_layout_is_under_raw_so_the_sync_exclusion_covers_it(tmp_store, monkeypatch):
    assert drmo.capture_dir() == Path(tmp_store) / "_raw" / "fortytwo" / "drmo"
    assert drmo.table_path() == Path(tmp_store) / "metadata" / "drmo.parquet"
    monkeypatch.setenv("MARKETDATA_FORTYTWO_RAW", "/elsewhere")
    assert drmo.capture_dir() == Path("/elsewhere") / "drmo"


def test_build_stores_the_week_and_the_reader_returns_it(capdir):
    place(capdir, W1)
    assert build()["ok"]
    df = marketdata.drmo(asof=W1)
    assert list(df.columns) == list(drmo.COLUMNS) and len(df) == len(BASE)
    assert (df["report_date"] == pd.Timestamp(W1)).all()
    assert set(df["regime"]) == {"TESTREGIME"}
    row = df.set_index("ticker").loc["CCC"]
    assert (row["asset"], row["vams"], row["rsi"], row["proper_trade"], row["position"]) \
        == ("Gamma, Quoted", "bearish", 22, "short_max", -1.0)
    assert dict(zip(df["proper_trade"], df["position"])) == {
        "long_max": 1.0, "long_half": 0.5, "short_max": -1.0, "short_half": -0.5,
        "none": 0.0}
    coin = df[df["asset"] == "Tickerless Coin"].iloc[0]
    assert coin["ticker"] is None and df["change"].isna().all()
    m = store.load_manifest()["metadata"]["drmo"]
    assert m["n_rows"] == len(BASE) and m["source"] == "fortytwo"
    assert m["last_date"] == str(W1)


def test_cli_flag_and_no_transcription_is_refused(tmp_store, capsys):
    assert update.main(["--build-drmo"]) == 1
    assert "REFUSED" in capsys.readouterr().out
    assert marketdata.drmo().empty and marketdata.drmo_history().empty
    assert list(marketdata.drmo().columns) == list(drmo.COLUMNS)
    with pytest.raises(SystemExit):
        update.main(["--accept-drmo-universe"])


def test_weeks_accumulate_and_a_past_week_reads_as_published(capdir):
    place(capdir, W1)
    build()
    before = drmo.history()
    place(capdir, W2, shift=3)
    assert build()["ok"]
    hist = marketdata.drmo_history()
    assert len(hist) == 2 * len(BASE)
    pd.testing.assert_frame_equal(hist[hist["report_date"] == pd.Timestamp(W1)]
                                  .reset_index(drop=True), before)
    assert set(marketdata.drmo(asof=W2)["rsi"]) == {r[4] + 3 for r in BASE}
    # Any day of W1's week sees W1, not W2.
    assert (marketdata.drmo(asof=W1 + dt.timedelta(days=6))["report_date"]
            == pd.Timestamp(W1)).all()
    # A rebuild with nothing new writes nothing.
    stamp = drmo.table_path().stat().st_mtime_ns
    assert build()["ok"] and drmo.table_path().stat().st_mtime_ns == stamp


def test_a_late_week_reads_as_absent_never_as_last_weeks(capdir):
    place(capdir, W1)
    build()
    # The Saturday after: this week's report is not in yet.
    assert marketdata.drmo(asof=W2).empty
    assert not marketdata.drmo(asof=W2 - dt.timedelta(days=1)).empty
    # The caller may ask for the old one explicitly, and it says its own date.
    old = marketdata.drmo(asof=W2, max_age_days=None)
    assert (old["report_date"] == pd.Timestamp(W1)).all()
    # Before the first report there is nothing at all.
    assert marketdata.drmo(asof=W1 - dt.timedelta(days=1), max_age_days=None).empty


def test_a_stored_week_is_never_rewritten_without_being_named(capdir, capsys):
    place(capdir, W1)
    build()
    before = drmo.table_path().read_bytes()
    edited = [BASE[0][:4] + (62,) + BASE[0][5:]] + BASE[1:]
    place(capdir, W1, rows=edited)
    r = build()
    out = capsys.readouterr().out
    assert not r["ok"] and r["partial"]
    assert "never rewritten" in out and "Group One / Alpha Fund" in out and f"{W1}.csv" in out
    assert drmo.table_path().read_bytes() == before
    assert update.main(["--build-drmo", "--replace-drmo-week", str(W1)]) == 0
    assert marketdata.drmo(asof=W1).set_index("ticker").loc["AAA", "rsi"] == 62


def test_changed_asset_list_is_refused_until_accepted(capdir, capsys):
    place(capdir, W1)
    build()
    place(capdir, W2, rows=BASE[:-1], shift=1)
    r = build()
    out = capsys.readouterr().out
    assert not r["ok"] and "Tickerless Coin" in out and "asset list differs" in out
    assert len(drmo.history()) == len(BASE)
    assert build(accept_universe=True)["ok"]
    assert len(marketdata.drmo(asof=W2)) == len(BASE) - 1


def test_last_weeks_slide_under_a_new_date_is_refused(capdir, capsys):
    place(capdir, W1)
    build()
    place(capdir, W2)
    assert not build()["ok"]
    assert "last week's slide" in capsys.readouterr().out
    assert marketdata.drmo(asof=W2).empty


def test_one_bad_week_does_not_block_a_good_one(capdir):
    place(capdir, W1)
    place(capdir, W2, rows=[BASE[0][:3] + ("sideways",) + BASE[0][4:]] + BASE[1:])
    place(capdir, W3, shift=5)
    (capdir / "notes.txt").write_text("not a transcription")
    r = build()
    assert not r["ok"] and r["partial"] and r["failed"] == 1
    assert r["errors"][0][0] == f"{W2}.csv"
    assert sorted(drmo.history()["report_date"].dt.date.unique()) == [W1, W3]


@pytest.mark.parametrize("make, needle", [
    (lambda: text(W1, header=HEADER.replace(",rsi", "")), "header"),
    (lambda: text(W1, header=HEADER + ",notes"), "header"),
    (lambda: HEADER + "\n", "no rows"),
    (lambda: text(W2), "report_date"),
    (lambda: text(W1, rows=BASE + [BASE[0]]), "appears twice"),
    (lambda: text(W1, rows=BASE + [("G", "Other", "AAA", "bullish", 50, "none")]),
     "ticker AAA appears twice"),
    (lambda: text(W1, rows=[("G", "A", "not a ticker", "bullish", 50, "none")]),
     "not a ticker"),
    (lambda: text(W1, rows=[("G", "A", "AAA", "bullish", 101, "none")]), "rsi"),
    (lambda: text(W1).replace(",61,", ",61.5,"), "rsi"),
    (lambda: text(W1, rows=[("G", "A", "AAA", "bullish", 50, "long")]), "proper_trade"),
    (lambda: text(W1).replace("TESTREGIME", "OTHER", 1), "different regimes"),
    (lambda: text(W1).replace('"Gamma, Quoted"', "Gamma, Quoted"), "must be quoted"),
    (lambda: "Here is the table you asked for:\n", "header"),
])
def test_malformed_transcription_is_refused_by_name_and_writes_nothing(capdir, make,
                                                                      needle, capsys):
    (capdir / f"{W1}.csv").write_text(make(), encoding="utf-8")
    assert not build()["ok"]
    out = capsys.readouterr().out
    assert "REFUSED" in out and f"{W1}.csv" in out and needle in out
    assert not drmo.table_path().exists()


def test_future_dated_report_is_refused(capdir, capsys):
    place(capdir, W3)
    assert not build(today=W2)["ok"]
    assert "in the future" in capsys.readouterr().out


def test_rsi_may_be_empty_for_slides_that_print_none(capdir):
    no_rsi = text(W1).replace(",61,", ",,").replace(",48,", ",,")
    (capdir / f"{W1}.csv").write_text(no_rsi, encoding="utf-8")
    assert build()["ok"]
    df = marketdata.drmo(asof=W1).set_index("ticker")
    assert pd.isna(df.loc["AAA", "rsi"]) and df.loc["CCC", "rsi"] == 22
    assert str(df["rsi"].dtype) == "Int64"


def test_vams_may_be_empty_where_the_slide_prints_na(capdir):
    (capdir / f"{W1}.csv").write_text(text(W1).replace(",AAA,bullish,", ",AAA,,"),
                                      encoding="utf-8")
    assert build()["ok"]
    assert marketdata.drmo(asof=W1).set_index("ticker").loc["AAA", "vams"] is None


def test_unchanged_week_without_rsi_is_kept(capdir):
    # With no RSI printed, a week whose VAMS and trades all equal the last one's
    # can be genuine, so it is not refused as a duplicate slide.
    rows = [r[:4] + (0,) + r[5:] for r in BASE]
    for w in (W1, W2):
        (capdir / f"{w}.csv").write_text(text(w, rows=rows).replace(",0,", ",,"),
                                          encoding="utf-8")
    assert build()["ok"]
    assert len(drmo.history()) == 2 * len(BASE)


def test_optional_change_column(capdir):
    rows = text(W1, header=HEADER + ",change").splitlines()
    rows = [rows[0], rows[1] + ",bullish"] + [r + "," for r in rows[2:]]
    (capdir / f"{W1}.csv").write_text("\n".join(rows) + "\n", encoding="utf-8")
    assert build()["ok"]
    df = marketdata.drmo(asof=W1).set_index("ticker")
    assert df.loc["AAA", "change"] == "bullish" and df.loc["BBB", "change"] is None
    (capdir / f"{W1}.csv").write_text("\n".join(
        [rows[0], rows[1].replace(",bullish", ",up")] + rows[2:]) + "\n")
    assert not build()["ok"]


def test_path_files_a_download_under_its_own_report_date(tmp_store, tmp_path, capsys):
    dl = tmp_path / "drmo (3).csv"
    dl.write_bytes(("﻿" + text(W1)).encode("utf-8"))
    assert build(dl)["ok"]
    assert (drmo.capture_dir() / f"{W1}.csv").read_bytes() == dl.read_bytes()
    # The same file again changes nothing; a different one for that week is refused.
    assert build(dl)["ok"]
    dl.write_text(text(W1, shift=1), encoding="utf-8")
    r = build(dl)
    assert not r["ok"] and "already holds a different transcription" in r["errors"][0][1]
    assert build(dl, replace=[W1])["ok"]
    assert marketdata.drmo(asof=W1).set_index("ticker").loc["AAA", "rsi"] == 62
    bad = tmp_path / "x.csv"
    bad.write_text("nothing\n")
    assert not build(bad)["ok"]


def _pdf(path, page_texts):
    """A minimal text-only PDF, one Helvetica line per page. Invented titles."""
    objs = ["<< /Type /Catalog /Pages 2 0 R >>", None,
            "<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>"]
    kids = []
    for t in page_texts:
        stream = f"BT /F1 12 Tf 40 700 Td ({t}) Tj ET"
        objs.append(f"<< /Length {len(stream)} >>\nstream\n{stream}\nendstream")
        objs.append(f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
                    f"/Contents {len(objs)} 0 R /Resources << /Font << /F1 3 0 R >> >> >>")
        kids.append(f"{len(objs)} 0 R")
    objs[1] = f"<< /Type /Pages /Kids [{' '.join(kids)}] /Count {len(kids)} >>"
    out, offs = "%PDF-1.4\n", []
    for i, o in enumerate(objs, start=1):
        offs.append(len(out))
        out += f"{i} 0 obj\n{o}\nendobj\n"
    xref = len(out)
    out += f"xref\n0 {len(objs) + 1}\n0000000000 65535 f \n"
    out += "".join(f"{o:010d} 00000 n \n" for o in offs)
    out += f"trailer\n<< /Size {len(objs) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n"
    path.write_bytes(out.encode("latin-1"))
    return path


def test_find_slide_by_name_and_dated_title(tmp_path, capsys):
    pytest.importorskip("pypdf")
    name = drmo.SLIDE_NAME
    deck = _pdf(tmp_path / "deck.pdf", [
        "An index page",
        f"A chart page about the {name}",          # the name, no dated title
        "Some other slide: Saturday, March 6, 2021",  # a date, not the name
        f"Use the {name}: Saturday, March 6, 2021",
        f"Use the {name}: Saturday, March 6, 2021",  # printed twice, same date
    ])
    assert drmo.find_slide(deck) == (4, W1)
    assert update.main(["--find-drmo-slide", str(deck)]) == 0
    assert f"page 4, report date {W1}" in capsys.readouterr().out
    none = _pdf(tmp_path / "none.pdf", ["An index page", f"About the {name}"])
    with pytest.raises(drmo.RawError, match="no page"):
        drmo.find_slide(none)
    two = _pdf(tmp_path / "two.pdf", [f"{name}: Saturday, March 6, 2021",
                                      f"{name}: Friday, March 5, 2021"])
    with pytest.raises(drmo.RawError, match="disagree"):
        drmo.find_slide(two)
    (tmp_path / "bad.pdf").write_text("not a pdf")
    assert update.main(["--find-drmo-slide", str(tmp_path / "bad.pdf")]) == 1


def test_same_asset_name_in_two_groups_is_two_rows(capdir, capsys):
    # The publisher prints one name in two groups over two tickers.
    rows = BASE + [("Group One", "Delta Fund", "EEE", "bullish", 70, "long_half")]
    place(capdir, W1, rows=rows)
    assert build()["ok"]
    df = marketdata.drmo(asof=W1)
    twins = df[df["asset"] == "Delta Fund"].set_index("ticker")
    assert (twins.loc["DDD", "group"], twins.loc["EEE", "group"]) == ("Group Two", "Group One")
    # A later week is compared row for row, each twin with itself.
    place(capdir, W2, rows=rows, shift=2)
    assert build()["ok"]
    # Editing one twin of a stored week names that twin, group included.
    edited = rows[:-1] + [rows[-1][:4] + (71,) + rows[-1][5:]]
    place(capdir, W1, rows=edited)
    assert not build()["ok"]
    out = capsys.readouterr().out
    assert "Group One / Delta Fund" in out and "Group Two / Delta Fund" not in out
    # Twice in ONE group is still a duplicate.
    place(capdir, W3, rows=rows + [("Group One", "Delta Fund", "FFF", "neutral", 50, "none")])
    assert not build()["ok"]
    assert "appears twice in group" in capsys.readouterr().out
