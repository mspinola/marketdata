"""`--build-kmlm`: dbmf-tracker's KMLM export to a table. Store-free beyond tmp_store."""
import pandas as pd

import marketdata
from marketdata import update
from marketdata.providers import ahlt_positions as ap
from marketdata.providers import kmlm_positions as kp

HEAD = "date,market,asset_class,ticker,contracts,exposure,weight,description\n"
ROWS = ("2026-11-19,CL,commodity,CLF7,-247.0,-17443140.0,-0.067,WTI CRUDE FUTURE JAN27\n"
        "2026-11-19,ZC,commodity,C H7,310.0,7000000.0,0.027,CORN FUTURE MAR27\n"
        "2026-11-20,CL,commodity,CLF7,-247.0,-17600000.0,-0.068,WTI CRUDE FUTURE JAN27\n"
        "2026-11-20,ZC,commodity,C H7,-310.0,-7100000.0,-0.0275,CORN FUTURE MAR27\n")


def export(tmp_path, body=ROWS, name="kmlm_positions.csv"):
    p = tmp_path / name
    p.write_text(HEAD + body)
    return p


def test_build_writes_the_table_with_contracts_and_the_reader_returns_it(tmp_store, tmp_path):
    assert marketdata.kmlm_positions().empty and list(marketdata.kmlm_positions().columns) == list(kp.COLUMNS)
    out = kp.build(export(tmp_path))
    assert out["ok"] and out["wrote"] == 4 and out["kind"] == "kmlm_positions"
    df = marketdata.kmlm_positions()
    assert list(df.columns) == list(kp.COLUMNS)                     # ticker and description dropped
    zc = df[df.market == "ZC"].sort_values("date")
    assert list(zc.contracts) == [310.0, -310.0] and zc.date.iloc[-1] == pd.Timestamp("2026-11-20")


def test_it_is_its_own_table_beside_the_ahlt_one(tmp_store, tmp_path):
    kp.build(export(tmp_path))
    assert marketdata.ahlt_positions().empty and kp.table_path() != ap.table_path()
    a = tmp_path / "ahlt_positions.csv"
    a.write_text("date,market,asset_class,kind,exposure,weight\n2026-11-20,GC,commodity,future,-1.0,-0.07\n")
    assert ap.build(a)["ok"] and len(marketdata.kmlm_positions()) == 4 and len(marketdata.ahlt_positions()) == 1


def test_refusals_name_the_fund_and_leave_the_table(tmp_store, tmp_path):
    assert "no KMLM export" in kp.build(tmp_path / "nope.csv")["errors"][0][1]
    ahlt_shaped = tmp_path / "wrong.csv"
    ahlt_shaped.write_text("date,market,asset_class,kind,exposure,weight\n2026-11-20,GC,commodity,future,-1.0,-0.07\n")
    assert "missing columns ['contracts']" in kp.build(ahlt_shaped)["errors"][0][1]
    kp.build(export(tmp_path))
    one_day = "2026-11-20,CL,commodity,CLF7,-1.0,-1.0,-0.07,x\n"
    out = kp.build(export(tmp_path, one_day, "truncated.csv"))
    assert not out["ok"] and "shorter history" in out["errors"][0][1] and len(marketdata.kmlm_positions()) == 4


def test_cli_flag_builds_and_exits_nonzero_on_a_refusal(tmp_store, tmp_path):
    assert update.main(["--build-kmlm", str(export(tmp_path))]) == 0
    assert update.main(["--build-kmlm", str(tmp_path / "nope.csv")]) != 0


def test_the_real_export_shape_parses(tmp_store, tmp_path):
    """Header and a row copied from dbmf-tracker's data/kmlm/kmlm_positions.csv (2026-11-24)."""
    body = "2024-01-02,CL,commodity,CLH4,-247.0,-17443140.0,-0.06702462181959425,WTI CRUDE FUTURE MAR24\n"
    df = kp.parse_export(export(tmp_path, body))
    assert abs(df.weight.iloc[0] + 0.06702462181959425) < 1e-12 and df.market.iloc[0] == "CL" and "ticker" not in df.columns
