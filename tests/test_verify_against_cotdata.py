"""The port-verification harness, tested against synthetic frames.

The script itself needs two real stores and a Norgate install, so it cannot run in
CI. Its comparison logic can, and must: a verifier that reports "identical" on
frames that differ is worse than no verifier, because it would clear the port for
deletion of the only thing it could ever be compared against.

Same posture as cotdata's `tests/test_validate_databento.py` beside the harness it
covers.
"""
import importlib.util
import sys
from pathlib import Path

import pandas as pd
import pytest

_SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "verify_against_cotdata.py"


@pytest.fixture(scope="module")
def verify():
    spec = importlib.util.spec_from_file_location("verify_against_cotdata", _SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


def frame(closes, *, volume=100.0, delivery=None, start="2020-01-01"):
    idx = pd.date_range(start, periods=len(closes), freq="D")
    df = pd.DataFrame({"Open": closes, "High": closes, "Low": closes,
                       "Close": closes, "Volume": volume,
                       "Open Interest": 1000.0}, index=idx)
    if delivery is not None:
        df["Delivery Month"] = delivery
    df.index.name = "Date"
    return df


# ── The verdict has to be right in both directions ────────────────────────
def test_identical_frames_pass(verify):
    df = frame([1.0, 2.0, 3.0], delivery=["A", "A", "B"])
    rep = verify.compare_tier(df, df.copy())
    assert rep["ok"]
    assert rep["problems"] == []
    assert rep["n_common"] == 3


def test_a_single_changed_price_fails_and_is_located(verify):
    """The check that earns the harness its keep. One wrong bar in ten thousand is
    exactly what a silent port bug looks like."""
    cot = frame([1.0, 2.0, 3.0])
    mkt = cot.copy()
    mkt.iloc[1, mkt.columns.get_loc("Close")] = 2.0001

    rep = verify.compare_tier(cot, mkt)
    assert not rep["ok"]
    assert rep["passthrough"]["Close"]["n_differing"] == 1
    assert rep["passthrough"]["Close"]["first_date"] == "2020-01-02"
    assert any("Close" in p for p in rep["problems"])


def test_tolerance_is_exact_not_approximate(verify):
    """Two INDEPENDENT vendors would need a tolerance. These are two code paths
    over one vendor, so a float away is still a difference."""
    cot = frame([100.0])
    mkt = frame([100.0 + 1e-12])
    assert not verify.compare_tier(cot, mkt)["ok"]


def test_a_changed_delivery_month_fails(verify):
    """Non-numeric, and it drives roll detection — so propadj is wrong downstream
    of a difference here, silently."""
    cot = frame([1.0, 2.0], delivery=["2020H", "2020H"])
    mkt = frame([1.0, 2.0], delivery=["2020H", "2020M"])
    rep = verify.compare_tier(cot, mkt)
    assert not rep["ok"]
    assert rep["passthrough"]["Delivery Month"]["n_differing"] == 1


def test_matching_nans_are_agreement_not_difference(verify):
    """An Open Interest Norgate never supplied is missing in both stores. Comparing
    with `!=` would call every such row a difference and drown the real ones."""
    cot = frame([1.0, 2.0])
    cot["Open Interest"] = [float("nan"), 5.0]
    rep = verify.compare_tier(cot, cot.copy())
    assert rep["ok"]
    assert rep["passthrough"]["Open Interest"]["n_differing"] == 0


def test_a_nan_on_one_side_only_is_a_difference(verify):
    cot = frame([1.0, 2.0])
    mkt = cot.copy()
    mkt["Open Interest"] = [float("nan"), 1000.0]
    assert not verify.compare_tier(cot, mkt)["ok"]


# ── Absence and partial overlap ───────────────────────────────────────────
def test_missing_from_marketdata_fails_and_says_which_side(verify):
    rep = verify.compare_tier(frame([1.0]), pd.DataFrame())
    assert not rep["ok"]
    assert "marketdata" in rep["problems"][0]


def test_missing_from_cotdata_fails_and_says_which_side(verify):
    rep = verify.compare_tier(pd.DataFrame(), frame([1.0]))
    assert not rep["ok"]
    assert "cotdata" in rep["problems"][0]


def test_differing_tails_are_counted_not_failed(verify):
    """One producer having run more recently is normal. Only the OVERLAP is
    compared, and the extra days are reported so a gap in the middle is still
    visible."""
    cot = frame([1.0, 2.0, 3.0, 4.0])
    mkt = frame([1.0, 2.0, 3.0])
    rep = verify.compare_tier(cot, mkt)
    assert rep["ok"]
    assert rep["n_common"] == 3
    assert rep["cot_only"] == 1
    assert rep["mkt_only"] == 0


def test_no_overlap_at_all_fails(verify):
    cot = frame([1.0, 2.0], start="2020-01-01")
    mkt = frame([1.0, 2.0], start="2021-01-01")
    rep = verify.compare_tier(cot, mkt)
    assert not rep["ok"]
    assert "no overlapping dates" in rep["problems"][0]


# ── Reconstruction columns are reported, never failed ─────────────────────
def test_reconstructed_volume_differences_do_not_fail_the_tier(verify):
    """Both producers reconstruct incrementally over their own store's history, so
    a fresh marketdata store and a months-old cotdata one legitimately differ here.
    Reported for the operator; --strict-volume is the opt-in."""
    cot = frame([1.0, 2.0])
    cot["Volume_Reconstructed"] = [10.0, 20.0]
    mkt = frame([1.0, 2.0])
    mkt["Volume_Reconstructed"] = [10.0, 999.0]

    rep = verify.compare_tier(cot, mkt)
    assert rep["ok"], "a reconstruction difference must not fail the passthrough verdict"
    assert rep["reconstruction"]["Volume_Reconstructed"]["n_differing"] == 1


def test_a_price_difference_still_fails_alongside_a_reconstruction_difference(verify):
    """The reconstruction leniency must not swallow a real one."""
    cot = frame([1.0, 2.0])
    cot["Volume_Reconstructed"] = [10.0, 20.0]
    mkt = frame([1.0, 2.5])
    mkt["Volume_Reconstructed"] = [10.0, 999.0]
    assert not verify.compare_tier(cot, mkt)["ok"]


# ── Store readers match each layout ───────────────────────────────────────
def test_readers_use_each_stores_own_layout(verify, tmp_path):
    """The two stores name files differently, and reading the wrong path would
    report a symbol absent rather than compare it."""
    cot_store, mkt_store = tmp_path / "cot", tmp_path / "mkt"
    (cot_store / "prices").mkdir(parents=True)
    (mkt_store / "bars" / "futures" / "norgate").mkdir(parents=True)

    frame([1.0, 2.0]).to_parquet(cot_store / "prices" / "ES_backadj.parquet")
    frame([1.0, 2.0]).to_parquet(
        mkt_store / "bars" / "futures" / "norgate" / "ES_backadj.parquet")

    assert len(verify.read_cotdata(cot_store, "ES", "backadj")) == 2
    assert len(verify.read_marketdata(mkt_store, "ES", "backadj")) == 2
    assert verify.read_cotdata(cot_store, "ES", "unadj").empty
    assert verify.read_marketdata(mkt_store, "GC", "backadj").empty


# ── Contract specs ────────────────────────────────────────────────────────
def _write_specs(root, rows):
    (root / "metadata").mkdir(parents=True, exist_ok=True)
    pd.DataFrame(rows).to_parquet(root / "metadata" / "contract_specs.parquet")


def test_matching_specs_pass_and_a_changed_point_value_fails(verify, tmp_path):
    """Point Value is the multiplier every notional and risk-unit figure scales by,
    so a silent change here rescales an entire book."""
    cot, mkt = tmp_path / "cot", tmp_path / "mkt"
    rows = [{"Symbol": "ES", "Point Value": 50.0, "Tick Size": 0.25,
             "Currency": "USD"}]
    _write_specs(cot, rows)
    _write_specs(mkt, rows)
    assert verify.compare_specs(cot, mkt, ["ES"])["ok"]

    _write_specs(mkt, [{"Symbol": "ES", "Point Value": 5.0, "Tick Size": 0.25,
                        "Currency": "USD"}])
    rep = verify.compare_specs(cot, mkt, ["ES"])
    assert not rep["ok"]
    assert "Point Value" in rep["problems"][0]


def test_a_symbol_missing_from_marketdata_specs_fails(verify, tmp_path):
    cot, mkt = tmp_path / "cot", tmp_path / "mkt"
    _write_specs(cot, [{"Symbol": "ES", "Point Value": 50.0}])
    _write_specs(mkt, [{"Symbol": "GC", "Point Value": 100.0}])
    rep = verify.compare_specs(cot, mkt, ["ES"])
    assert not rep["ok"]
    assert "no contract_specs row in marketdata" in rep["problems"][0]


def test_expected_absent_symbols_are_not_reported_as_missing(verify, tmp_path):
    """MME/MFS have no Norgate series and are deliberately unported. Failing on
    them would train the operator to ignore a red run."""
    cot, mkt = tmp_path / "cot", tmp_path / "mkt"
    _write_specs(cot, [{"Symbol": "MME", "Point Value": 50.0}])
    _write_specs(mkt, [{"Symbol": "ES", "Point Value": 50.0}])
    assert verify.compare_specs(cot, mkt, ["MME"])["ok"]
    assert "MME" in verify.EXPECTED_ABSENT


def test_missing_specs_table_is_a_failure_naming_the_fix(verify, tmp_path):
    cot, mkt = tmp_path / "cot", tmp_path / "mkt"
    _write_specs(cot, [{"Symbol": "ES", "Point Value": 50.0}])
    mkt.mkdir(parents=True)
    rep = verify.compare_specs(cot, mkt, ["ES"])
    assert not rep["ok"]
    assert "--metadata" in rep["problems"][0]


# ── Exit code is the gate ─────────────────────────────────────────────────
def test_exit_code_is_zero_only_when_everything_matches(verify, tmp_path):
    cot, mkt = tmp_path / "cot", tmp_path / "mkt"
    (cot / "prices").mkdir(parents=True)
    bars = mkt / "bars" / "futures" / "norgate"
    bars.mkdir(parents=True)
    for tier in ("backadj", "unadj"):
        frame([1.0, 2.0]).to_parquet(cot / "prices" / f"ES_{tier}.parquet")
        frame([1.0, 2.0]).to_parquet(bars / f"ES_{tier}.parquet")
    specs = [{"Symbol": "ES", "Point Value": 50.0}]
    _write_specs(cot, specs)
    _write_specs(mkt, specs)

    argv = ["--cotdata-store", str(cot), "--marketdata-store", str(mkt),
            "--symbols", "ES"]
    assert verify.main(argv) == 0

    frame([1.0, 99.0]).to_parquet(bars / "ES_unadj.parquet")
    assert verify.main(argv) == 1


def test_a_nonexistent_store_exits_two_rather_than_passing(verify, tmp_path):
    """A typo'd path must not read as 'nothing differed'."""
    assert verify.main(["--cotdata-store", str(tmp_path / "nope"),
                        "--marketdata-store", str(tmp_path)]) == 2


# ── Coverage must be visible, or a PASS overstates itself ─────────────────
def test_a_column_missing_from_one_store_is_named_not_silently_skipped(verify):
    """The flaw a real run exposed. Reconstruction columns were reported only when
    they DIFFERED, so 'compared and identical' and 'never compared' printed the
    same nothing — and a PASS could not be told apart from a PASS that skipped
    half the frame."""
    cot = frame([1.0, 2.0])
    cot["Volume_Reconstructed"] = [10.0, 20.0]
    mkt = frame([1.0, 2.0])          # no reconstruction columns at all

    rep = verify.compare_tier(cot, mkt)
    assert rep["ok"]                                    # passthrough still agrees
    assert any("Volume_Reconstructed" in s and "only in cotdata" in s
               for s in rep["not_compared"])


def test_columns_in_neither_store_are_also_named(verify):
    rep = verify.compare_tier(frame([1.0]), frame([1.0]))
    assert rep["ok"]
    assert any("in neither store" in s for s in rep["not_compared"])


def test_nothing_is_flagged_uncompared_when_both_stores_carry_it(verify):
    cot = frame([1.0, 2.0])
    for col, val in (("Volume_Reconstructed", 10.0), ("FirstVolume", 6.0),
                     ("SecondVolume", 4.0), ("FirstContract", "ES-2020H"),
                     ("SecondContract", "ES-2020M"), ("Volume_Source", "reconstructed")):
        cot[col] = val
    rep = verify.compare_tier(cot, cot.copy())
    assert rep["ok"]
    assert rep["not_compared"] == []
    assert set(rep["reconstruction"]) == set(verify.RECONSTRUCTION)
