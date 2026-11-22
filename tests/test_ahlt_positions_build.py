"""`--build-ahlt`: dbmf-tracker's AHLT export to a table. Store-free beyond tmp_store."""
import pandas as pd
import pytest

import marketdata
from marketdata import update
from marketdata.providers import ahlt_positions as ap

HEAD = "date,market,asset_class,kind,exposure,weight,description\n"
ROWS = ("2026-11-18,GC,commodity,future,-11000000.0,-0.068,GOLD 100 OZ FUTR\n"
        "2026-11-18,EC,fx,forward,-5000000.0,-0.031,\n"
        "2026-11-19,GC,commodity,future,-11200000.0,-0.0695,GOLD 100 OZ FUTR\n"
        "2026-11-19,EC,fx,forward,-4000000.0,-0.025,\n")


def export(tmp_path, body=ROWS, name="ahlt_positions.csv"):
    p = tmp_path / name
    p.write_text(HEAD + body)
    return p


def test_build_writes_the_table_and_the_reader_returns_it(tmp_store, tmp_path):
    assert marketdata.ahlt_positions().empty                                   # no build yet
    out = ap.build(export(tmp_path))
    assert out["ok"] and out["wrote"] == 4
    df = marketdata.ahlt_positions()
    assert list(df.columns) == list(ap.COLUMNS)                                # description dropped
    gc = df[df.market == "GC"].sort_values("date")
    assert list(gc.weight) == [-0.068, -0.0695] and gc.date.iloc[-1] == pd.Timestamp("2026-11-19")


@pytest.mark.parametrize("body,why", [
    ("", "no rows"),
    ("2026-11-19,GC,commodity,future,1.0,x,\n", "numeric weight"),
    ("19/11/2026,GC,commodity,future,1.0,0.1,\n", "YYYY-MM-DD"),
    ("2026-11-19,GC,commodity,future,1.0,0.1,\n2026-11-19,GC,commodity,future,2.0,0.2,\n", "more than one row"),
])
def test_a_malformed_export_is_refused_by_name(tmp_store, tmp_path, body, why):
    with pytest.raises(ap.RawError, match=why):
        ap.parse_export(export(tmp_path, body))
    assert not ap.build(export(tmp_path, body))["ok"] and marketdata.ahlt_positions().empty


def test_missing_file_and_missing_column_are_refusals(tmp_store, tmp_path):
    assert not ap.build(tmp_path / "nope.csv")["ok"]
    p = tmp_path / "short.csv"
    p.write_text("date,market,weight\n2026-11-19,GC,0.1\n")
    assert "missing columns" in ap.build(p)["errors"][0][1]


def test_a_shorter_or_older_export_does_not_replace_the_table(tmp_store, tmp_path):
    ap.build(export(tmp_path))
    one_day = "2026-11-19,GC,commodity,future,-1.0,-0.07,\n"
    out = ap.build(export(tmp_path, one_day, "truncated.csv"))
    assert not out["ok"] and "shorter history" in out["errors"][0][1]
    older = ("2026-11-17,GC,commodity,future,-1.0,-0.07,\n"
             "2026-11-18,GC,commodity,future,-1.0,-0.07,\n")
    out = ap.build(export(tmp_path, older, "older.csv"))
    assert not out["ok"] and "older export" in out["errors"][0][1]
    assert len(marketdata.ahlt_positions()) == 4                              # untouched


def test_a_restated_history_of_the_same_length_is_written(tmp_store, tmp_path):
    ap.build(export(tmp_path))
    ap.build(export(tmp_path, ROWS.replace("-0.068", "-0.060")))
    assert marketdata.ahlt_positions().weight.min() == -0.0695 and -0.060 in set(marketdata.ahlt_positions().weight)


def test_cli_flag_builds_and_exits_nonzero_on_a_refusal(tmp_store, tmp_path):
    assert update.main(["--build-ahlt", str(export(tmp_path))]) == 0
    assert update.main(["--build-ahlt", str(tmp_path / "nope.csv")]) != 0
