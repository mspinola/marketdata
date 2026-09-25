"""`--build-tradingview`: the series domain's stage 2, from raw connector JSON.

The fixture is the ten-bar INDEX:NCFD result a Desktop local routine wrote on the
producer box on 2026-09-16, byte-identical to the interactive probe there and to
the same pull from a Mac. Every guard in the module docstring has a case here, and
each refusal is checked to have written nothing. No network.
"""
import datetime as dt
import json
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd
import pytest

from marketdata import store, update
from marketdata.providers import tradingview as tprov
from marketdata.registry import REGISTRY

FIXTURE = Path(__file__).parent / "fixtures" / "tradingview_ncfd_10bars.json"
SYM = "NASDAQ_FOMO_5D"
ET = ZoneInfo("America/New_York")
# The fixture ends on the 2026-09-16 session, which is also a registry anchor.
LAST = pd.Timestamp("2026-09-16")


@pytest.fixture()
def raw(tmp_store):
    """A raw root under the temp store with the fixture as the symbol's first file."""
    root = tprov.raw_root()
    (root / SYM).mkdir(parents=True)
    (root / SYM / "2026-09-16.json").write_bytes(FIXTURE.read_bytes())
    return root


def payload(**overrides):
    d = json.loads(FIXTURE.read_text())
    d.update(overrides)
    return d


def write(root, name, data, sym=SYM):
    (root / sym).mkdir(parents=True, exist_ok=True)
    (root / sym / name).write_text(json.dumps(data))


def stored():
    return store.read_bars(SYM, "series", "tradingview")


# ── the happy path ──────────────────────────────────────────────────────────
def test_raw_root_defaults_under_the_store_and_honours_the_env(tmp_store, monkeypatch):
    assert tprov.raw_root() == Path(tmp_store) / "_raw" / "tradingview"
    monkeypatch.setenv("MARKETDATA_TRADINGVIEW_RAW", "/elsewhere/raw")
    assert tprov.raw_root() == Path("/elsewhere/raw")


def test_provider_name_is_in_the_registry_vocabulary():
    from marketdata.registry import PRICE_SOURCES
    assert tprov.NAME in PRICE_SOURCES


def test_session_date_is_the_eastern_date_of_the_open_stamp():
    assert tprov.session_date(1789565400) == pd.Timestamp("2026-09-16")   # 13:30Z, EDT
    assert tprov.session_date(1767191400) == pd.Timestamp("2025-12-31")   # 14:30Z, EST


def test_build_writes_the_fixture_and_the_reader_serves_it(raw):
    res = tprov.build([SYM], expect_session=str(LAST.date()))
    assert res["ok"] and res["wrote"] == 1 and res["failed"] == 0
    df = stored()
    assert len(df) == 10
    assert df.index[0] == pd.Timestamp("2026-09-02") and df.index[-1] == LAST
    assert df.loc[LAST, "Close"] == 32.23
    assert list(df.columns) == ["Open", "High", "Low", "Close"]
    from marketdata import get_bars
    assert get_bars(SYM)["Close"].iloc[-1] == 32.23
    entry = store.load_manifest()["bars"][f"series/tradingview/{SYM}"]
    assert entry["source"] == "tradingview" and entry["last_date"] == "2026-09-16"


def test_a_second_build_is_a_no_op(raw):
    tprov.build([SYM], expect_session=str(LAST.date()))
    before = store.load_manifest()["bars"][f"series/tradingview/{SYM}"]["updated_at"]
    res = tprov.build([SYM], expect_session=str(LAST.date()))
    assert res["ok"] and res["wrote"] == 0 and res["failed"] == 0
    assert store.load_manifest()["bars"][f"series/tradingview/{SYM}"]["updated_at"] == before


def test_a_later_file_appends_only_the_new_bars_and_leaves_the_old_untouched(raw):
    tprov.build([SYM], expect_session=str(LAST.date()))
    d = payload()
    d["bars"] = d["bars"][-3:] + [{"c": 40.1, "h": 41.0, "l": 39.0, "o": 39.5,
                                   "t": 1789651800, "v": None}]   # 2026-09-17
    write(raw, "2026-09-17.json", d)
    res = tprov.build([SYM], expect_session="2026-09-17")
    assert res["ok"] and res["wrote"] == 1
    df = stored()
    assert len(df) == 11 and df.index[-1] == pd.Timestamp("2026-09-17")
    assert df.loc[LAST, "Close"] == 32.23
    assert df.loc["2026-09-17", "Close"] == 40.1


# ── refusals, each writing nothing ─────────────────────────────────────────
def test_an_overlapping_bar_that_disagrees_with_the_store_refuses_the_file(raw):
    tprov.build([SYM], expect_session=str(LAST.date()))
    # The first raw file rotated away, so the only witness to 2026-09-15 is the
    # store itself: this is the store guard, not the file-against-file one.
    (raw / SYM / "2026-09-16.json").unlink()
    d = payload()
    d["bars"][-2]["c"] = 38.91        # 2026-09-15 is 38.9 in the store
    d["bars"].append({"c": 40.1, "h": 41.0, "l": 39.0, "o": 39.5, "t": 1789651800, "v": None})
    write(raw, "2026-09-17.json", d)
    res = tprov.build([SYM], expect_session="2026-09-17")
    assert not res["ok"] and res["failed"] == 1 and res["wrote"] == 0
    assert "2026-09-15" in res["errors"][0][1] and "never rewritten" in res["errors"][0][1]
    assert len(stored()) == 10           # the new bar did not land either


def test_two_raw_files_that_disagree_refuse_before_the_store_is_touched(raw):
    d = payload()
    d["bars"][3]["c"] = 43.4
    write(raw, "2026-09-17.json", d)
    res = tprov.build([SYM], expect_session=str(LAST.date()))
    assert not res["ok"] and "earlier raw file" in res["errors"][0][1]
    assert stored().empty


def test_an_anchor_mismatch_refuses(raw, monkeypatch):
    sym = REGISTRY[SYM]
    bad = sym.__class__(**{**sym.__dict__, "anchors": (("2026-09-16", 32.24),)})
    monkeypatch.setattr(tprov, "_targets", lambda symbols=None: [bad])
    res = tprov.build([SYM], expect_session=str(LAST.date()))
    assert not res["ok"] and "anchor 2026-09-16" in res["errors"][0][1]
    assert stored().empty


def test_the_registry_anchor_is_checked_on_the_real_fixture(raw):
    # 48.05 on 2026-07-29 is not in the ten-bar file, so only the 2026-09-16 anchor
    # bites here, and the fixture satisfies it: this is the proof the box's pull
    # is the same series the EDGE report printed.
    assert tprov.build([SYM], expect_session=str(LAST.date()))["ok"]


@pytest.mark.parametrize("mutate, why", [
    (lambda d: d.update(success=False), "not a successful"),
    (lambda d: d.update(symbol="INDEX:S5FD"), "the registry maps"),
    (lambda d: d.update(interval="1W"), "not '1D'"),
    (lambda d: d.update(bars=[]), "no bars"),
    (lambda d: d["bars"][0].pop("c"), "lacks one of"),
    (lambda d: d["bars"].__setitem__(5, {**d["bars"][5], "c": 100.5}), "outside the percent range"),
    (lambda d: d["bars"].__setitem__(5, {**d["bars"][5], "l": -0.1}), "outside the percent range"),
    (lambda d: d["bars"].__setitem__(5, {**d["bars"][5], "c": None}), "not numeric"),
    (lambda d: d["bars"].reverse(), "not strictly increasing"),
    (lambda d: d["bars"].append(dict(d["bars"][-1])), "not strictly increasing"),
])
def test_malformed_or_out_of_range_files_are_refused_by_name(raw, mutate, why):
    d = payload()
    mutate(d)
    write(raw, "2026-09-16.json", d)
    res = tprov.build([SYM], expect_session=str(LAST.date()))
    assert not res["ok"], why
    assert why in res["errors"][0][1] and "2026-09-16.json" in res["errors"][0][1]
    assert stored().empty


def test_not_json_is_refused(raw):
    (raw / SYM / "2026-09-16.json").write_text("I'll load the schema and call the tool")
    res = tprov.build([SYM], expect_session=str(LAST.date()))
    assert not res["ok"] and "not readable as JSON" in res["errors"][0][1]


def _typed(kind):
    sym = REGISTRY[SYM]
    return sym.__class__(**{**sym.__dict__, "kind": kind, "anchors": ()})


def _as_ratio(d):
    d["bars"] = [{**b, **{k: b[k] / 100 for k in "ohlc"}} for b in d["bars"]]
    return d


def test_count_and_ratio_kinds_have_their_own_ranges(raw):
    for kind, value, why in [("count", 46.5, "not a whole number"),
                             ("ratio", 12.0, "outside the ratio range")]:
        d = payload()
        if kind == "ratio":
            _as_ratio(d)
        d["bars"][2]["c"] = value
        write(raw, "2026-09-16.json", d)
        with pytest.raises(tprov.RawError, match=why):
            tprov.parse_raw(raw / SYM / "2026-09-16.json", _typed(kind))


def test_a_count_of_0_01_is_the_vendors_zero_and_is_stored_as_zero(raw):
    # Measured: INDEX:HIGQ printed 0.01 on 2020-03-13, INDEX:LOWN on 65 days.
    d = payload()
    d["bars"] = [{**b, **{k: float(round(b[k])) for k in "ohlc"}} for b in d["bars"]]
    d["bars"][5] = {**d["bars"][5], "o": 0.01, "h": 0.01, "l": 0.01, "c": 0.01}
    write(raw, "2026-09-16.json", d)
    df = tprov.parse_raw(raw / SYM / "2026-09-16.json", _typed("count"))
    assert df.iloc[5]["Close"] == 0.0 and df.iloc[5]["High"] == 0.0
    assert len(df) == 10


def test_a_ratio_bar_with_a_zero_close_is_a_vendor_hole_dropped_and_named(raw, capsys):
    # Measured: USI:PCCE on 2026-06-18 and 2026-07-02, real open and high, zero low
    # and close.
    d = _as_ratio(payload())
    d["bars"][3] = {**d["bars"][3], "l": 0.0, "c": 0.0}
    write(raw, "2026-09-16.json", d)
    df = tprov.parse_raw(raw / SYM / "2026-09-16.json", _typed("ratio"))
    assert len(df) == 9 and pd.Timestamp("2026-09-08") not in df.index
    assert "dropped 1 bar(s) with a zero close" in capsys.readouterr().out
    # A hole is dropped, but a zero open beside a positive close is still refused.
    d = _as_ratio(payload())
    d["bars"][3] = {**d["bars"][3], "o": 0.0}
    write(raw, "2026-09-16.json", d)
    with pytest.raises(tprov.RawError, match="non-positive open"):
        tprov.parse_raw(raw / SYM / "2026-09-16.json", _typed("ratio"))


def test_a_symbol_with_no_raw_files_is_a_failed_run(tmp_store):
    res = tprov.build([SYM], expect_session=str(LAST.date()))
    assert not res["ok"] and "no raw files" in res["errors"][0][1]


def test_scoping_to_a_non_series_symbol_is_a_no_op(tmp_store):
    res = tprov.build(["SPY"], expect_session=tprov.NO_GATE)
    assert res == {"kind": "series_tradingview", "ok": True, "partial": False,
                   "wrote": 0, "failed": 0, "errors": []}


# ── the session gate ────────────────────────────────────────────────────────
def test_stale_raw_files_are_refused_with_nothing_written(raw):
    res = tprov.build([SYM], expect_session="2026-09-17")
    assert not res["ok"] and "Stale" in res["errors"][0][1]
    assert stored().empty


def test_the_gate_can_be_switched_off_for_old_files(raw):
    assert tprov.build([SYM], expect_session=tprov.NO_GATE)["ok"]


def test_a_current_store_passes_the_gate_even_when_the_raw_files_add_nothing(raw):
    tprov.build([SYM], expect_session=str(LAST.date()))
    d = payload()
    d["bars"] = d["bars"][:5]                        # an old, short file
    write(raw, "2026-09-10.json", d)
    res = tprov.build([SYM], expect_session=str(LAST.date()))
    assert res["ok"] and res["wrote"] == 0


@pytest.mark.parametrize("now, expected", [
    ("2026-09-16 18:30", "2026-09-16"),   # Wednesday evening: today
    ("2026-09-16 15:59", "2026-09-15"),   # Wednesday before the close: yesterday
    ("2026-09-19 10:00", "2026-09-18"),   # Saturday: Friday
    ("2026-09-20 23:00", "2026-09-18"),   # Sunday: Friday
    ("2026-09-21 09:00", "2026-09-18"),   # Monday morning: Friday
    ("2026-09-21 16:30", "2026-09-21"),   # Monday at the cutoff: today
])
def test_expected_session_is_the_latest_closed_weekday(now, expected):
    when = dt.datetime.strptime(now, "%Y-%m-%d %H:%M").replace(tzinfo=ET)
    assert tprov.expected_session(when) == pd.Timestamp(expected)


def test_the_default_gate_is_the_expected_session(raw, monkeypatch):
    monkeypatch.setattr(tprov, "expected_session", lambda now=None: pd.Timestamp("2026-09-17"))
    assert not tprov.build([SYM])["ok"]
    monkeypatch.setattr(tprov, "expected_session", lambda now=None: LAST)
    assert tprov.build([SYM])["ok"]


# ── the CLI ─────────────────────────────────────────────────────────────────
def test_cli_builds_and_exits_by_the_result(raw, capsys):
    assert update.main(["--build-tradingview", "--symbols", SYM,
                        "--expect-session", "2026-09-16"]) == 0
    assert "series_tradingview: wrote=1 failed=0" in capsys.readouterr().out
    assert update.main(["--build-tradingview", "--symbols", SYM,
                        "--expect-session", "2026-09-17"]) == 1
    assert update.main(["--build-tradingview", "--symbols", SYM,
                        "--expect-session", "none"]) == 0


# ── a partial refusal must not withhold the symbols that worked ─────────────
#
# The 2026-09-25 outage, as a test. TradingView restated two put/call closes, the
# build refused those two exactly as designed, and because the only signal the
# wrapper had was "non-zero" it exited before its replica syncs -- so thirteen
# correct breadth series sat on the producer and stale on both replicas. The build
# now says WHICH kind of non-zero it is.

def _second_symbol(root):
    """A second series symbol whose raw file is sound, beside SYM's. The payload
    carries its OWN vendor symbol: the build checks the raw file's `symbol` against
    the registry, so reusing the fixture verbatim refuses on that instead."""
    other = "SPX_FOMO_5D"
    write(root, "2026-09-16.json", payload(symbol=REGISTRY[other].tradingview),
          sym=other)
    return other


def test_a_partial_refusal_exits_distinctly_from_a_total_one(raw, capsys):
    """Some refused, some did not: EXIT_PARTIAL, and the message tells the wrapper
    to sync anyway. The refused symbol is refused for a real reason (its raw file
    disagrees with a bar the store already holds), which is the restatement case."""
    other = _second_symbol(raw)
    # Both land first.
    both = ["--symbols", SYM, other]
    assert update.main(["--build-tradingview", *both,
                        "--expect-session", "2026-09-16"]) == 0
    capsys.readouterr()
    # Now SYM's raw file restates a stored close, exactly as the vendor did.
    d = payload()
    d["bars"][-1]["c"] = float(d["bars"][-1]["c"]) + 0.5
    write(raw, "2026-09-17.json", d)

    rc = update.main(["--build-tradingview", *both, "--expect-session", "2026-09-16"])
    out = capsys.readouterr().out
    assert rc == update.EXIT_PARTIAL and rc != 0 and rc != 1
    assert "REFUSED" in out and SYM in out
    assert f"PARTIAL (exit {update.EXIT_PARTIAL})" in out
    assert "replica syncs" in out
    # The symbol that did not refuse is untouched and still correct.
    assert not store.read_bars(other, "series", "tradingview").empty


def test_every_symbol_refusing_is_still_a_plain_failure(raw, capsys):
    """Nothing usable came of the run, so the wrapper has nothing to deliver and
    the later routine is the retry. This must NOT become EXIT_PARTIAL."""
    assert update.main(["--build-tradingview", "--symbols", SYM,
                        "--expect-session", "2026-09-16"]) == 0
    capsys.readouterr()
    d = payload()
    d["bars"][-1]["c"] = float(d["bars"][-1]["c"]) + 0.5
    write(raw, "2026-09-17.json", d)
    rc = update.main(["--build-tradingview", "--symbols", SYM,
                      "--expect-session", "2026-09-16"])
    assert rc == 1
    assert "PARTIAL" not in capsys.readouterr().out


def test_a_symbol_that_was_already_current_counts_as_succeeding(raw, capsys):
    """The flag answers "did every symbol refuse", not "did this run write rows".
    The live case: the thirteen good series were ALREADY current on the producer,
    so a run that writes nothing and refuses two must still be partial, or the
    sync stays blocked for as long as the restatement is unresolved."""
    other = _second_symbol(raw)
    both = ["--symbols", SYM, other]
    assert update.main(["--build-tradingview", *both,
                        "--expect-session", "2026-09-16"]) == 0
    capsys.readouterr()
    d = payload()
    d["bars"][-1]["c"] = float(d["bars"][-1]["c"]) + 0.5
    write(raw, "2026-09-17.json", d)
    rc = update.main(["--build-tradingview", *both, "--expect-session", "2026-09-16"])
    out = capsys.readouterr().out
    assert rc == update.EXIT_PARTIAL
    assert "already current" in out          # `other` wrote nothing this run
    assert "wrote=0" in out
    assert other


def test_the_partial_flag_is_on_the_result_dict(raw):
    other = _second_symbol(raw)
    two = [SYM, other]
    assert tprov.build(two, expect_session="2026-09-16")["partial"] is False
    d = payload()
    d["bars"][-1]["c"] = float(d["bars"][-1]["c"]) + 0.5
    write(raw, "2026-09-17.json", d)
    res = tprov.build(two, expect_session="2026-09-16")
    assert res["partial"] is True and res["ok"] is False and res["failed"] == 1
    assert tprov.build([SYM], expect_session="2026-09-16")["partial"] is False
    assert other


# ── accepting a vendor restatement ──────────────────────────────────────────
#
# The 2026-09-25 case: TradingView restated two put/call closes after the
# backfill, so the raw files disagreed with each other AND with the store. The
# refusal is right by default -- a restatement and a mis-transcription look
# identical in the file -- so accepting is a named, per-symbol act.

#: The fixture's LAST bar is the registry anchor, so restating it trips the anchor
#: check before the overlap one. The bar before it is an ordinary bar.
RESTATED_DAY = "2026-09-15"


def _restate(raw, new_close, sym=SYM, day_index=-2):
    """A newer raw file that restates one bar, the vendor's own behaviour.
    Returns the close it replaced."""
    d = payload(symbol=REGISTRY[sym].tradingview)
    was = float(d["bars"][day_index]["c"])
    d["bars"][day_index]["c"] = new_close
    write(raw, "2026-09-17.json", d, sym=sym)
    return was


def test_a_restatement_refuses_by_default_and_the_message_names_the_way_out(raw, capsys):
    assert update.main(["--build-tradingview", "--symbols", SYM,
                        "--expect-session", "2026-09-16"]) == 0
    capsys.readouterr()
    _restate(raw, 41.0)
    assert update.main(["--build-tradingview", "--symbols", SYM,
                        "--expect-session", "2026-09-16"]) == 1
    out = capsys.readouterr().out
    assert "REFUSED" in out and RESTATED_DAY in out
    assert f"--accept-restatement {SYM}" in out


def test_accepting_overwrites_the_stored_bar_and_prints_both_values(raw, capsys):
    assert update.main(["--build-tradingview", "--symbols", SYM,
                        "--expect-session", "2026-09-16"]) == 0
    capsys.readouterr()
    was = _restate(raw, 41.0)

    rc = update.main(["--build-tradingview", "--symbols", SYM,
                      "--expect-session", "2026-09-16",
                      "--accept-restatement", SYM])
    out = capsys.readouterr().out
    assert rc == 0
    assert float(stored()["Close"].loc[RESTATED_DAY]) == 41.0 != was
    assert "ACCEPTED" in out and f"{was:g}" in out and "41" in out
    # The row count is unchanged: a restatement replaces a bar, never appends one.
    assert len(stored()) == 10


def test_accepting_is_scoped_to_the_named_symbol(raw, capsys):
    other = _second_symbol(raw)
    both = ["--symbols", SYM, other]
    assert update.main(["--build-tradingview", *both,
                        "--expect-session", "2026-09-16"]) == 0
    capsys.readouterr()
    # Both symbols restate the same bar; only one is named.
    _restate(raw, 41.0)
    _restate(raw, 41.0, sym=other)

    rc = update.main(["--build-tradingview", *both, "--expect-session", "2026-09-16",
                      "--accept-restatement", SYM])
    out = capsys.readouterr().out
    assert rc == update.EXIT_PARTIAL
    assert float(stored()["Close"].loc[RESTATED_DAY]) == 41.0
    assert f"{other}: REFUSED" in out
    untouched = store.read_bars(other, "series", "tradingview")
    assert float(untouched["Close"].loc[RESTATED_DAY]) != 41.0


def test_accepting_still_checks_the_anchors(raw, capsys):
    """Accepting a restatement is not a way past the proof that the vendor symbol
    still names the same series. The fixture's last bar IS the registry anchor."""
    assert update.main(["--build-tradingview", "--symbols", SYM,
                        "--expect-session", "2026-09-16"]) == 0
    capsys.readouterr()
    _restate(raw, 41.0, day_index=-1)     # the anchor bar itself
    rc = update.main(["--build-tradingview", "--symbols", SYM,
                      "--expect-session", "2026-09-16", "--accept-restatement", SYM])
    out = capsys.readouterr().out
    assert rc == 1
    assert "anchor" in out.lower() and "REFUSED" in out
    assert "ACCEPTED" not in out


def test_the_flag_is_refused_without_the_build_or_with_an_unknown_symbol(tmp_store):
    with pytest.raises(SystemExit):
        update.main(["--check", "--accept-restatement", SYM])
    with pytest.raises(SystemExit):
        update.main(["--build-tradingview", "--accept-restatement", "NOT_A_SYMBOL"])
    with pytest.raises(SystemExit):
        # An equities symbol is not a series symbol.
        update.main(["--build-tradingview", "--accept-restatement", "SPY"])


def test_cli_refuses_bars_on_the_series_domain_and_a_stray_gate_flag(tmp_store):
    with pytest.raises(SystemExit):
        update.main(["--bars", "--domain", "series"])
    with pytest.raises(SystemExit):
        update.main(["--check", "--expect-session", "2026-09-16"])


# ── the backfill path: a chart export becomes a raw file ───────────────────
def _export_csv(path, bars, time_as="unix"):
    lines = ["time,open,high,low,close,Volume"]
    for b in bars:
        t = b["t"] if time_as == "unix" else pd.Timestamp(b["t"], unit="s", tz="UTC").isoformat()
        lines.append(f"{t},{b['o']},{b['h']},{b['l']},{b['c']},")
    Path(path).write_text("\n".join(lines) + "\n")


def test_csv_export_becomes_a_raw_file_the_build_accepts(tmp_store, tmp_path):
    bars = payload()["bars"]
    csv = tmp_path / "INDEX_NCFD, 1D.csv"
    _export_csv(csv, list(reversed(bars)))          # exports are not always in order
    out = tprov.csv_export_to_raw(csv, REGISTRY[SYM], when=dt.date(2026, 9, 17))
    assert out.name == "2026-09-17-csv-export.json"
    data = json.loads(out.read_text())
    assert data["symbol"] == "INDEX:NCFD" and data["source"] == "csv-export"
    assert [b["t"] for b in data["bars"]] == [b["t"] for b in bars]   # sorted back
    res = tprov.build([SYM], expect_session=str(LAST.date()))
    assert res["ok"] and res["wrote"] == 1
    assert stored().loc[LAST, "Close"] == 32.23


def test_csv_export_accepts_iso_timestamps_and_refuses_missing_columns(tmp_store, tmp_path):
    bars = payload()["bars"]
    csv = tmp_path / "iso.csv"
    _export_csv(csv, bars, time_as="iso")
    out = tprov.csv_export_to_raw(csv, REGISTRY[SYM], when=dt.date(2026, 9, 17))
    assert json.loads(out.read_text())["bars"][0]["t"] == bars[0]["t"]
    (tmp_path / "bad.csv").write_text("date,close\n2026-09-16,32.23\n")
    with pytest.raises(tprov.RawError, match="needs columns"):
        tprov.csv_export_to_raw(tmp_path / "bad.csv", REGISTRY[SYM])


def test_cli_csv_import_needs_exactly_one_symbol_and_then_builds(tmp_store, tmp_path, capsys):
    csv = tmp_path / "ncfd.csv"
    _export_csv(csv, payload()["bars"])
    with pytest.raises(SystemExit):
        update.main(["--tradingview-csv", str(csv)])
    with pytest.raises(SystemExit):
        update.main(["--tradingview-csv", str(csv), "--symbols", SYM, "SPX_FOMO_5D"])
    assert update.main(["--tradingview-csv", str(csv), "--symbols", SYM]) == 0
    assert "csv-export.json" in capsys.readouterr().out
    assert update.main(["--build-tradingview", "--symbols", SYM,
                        "--expect-session", "none"]) == 0
    assert len(stored()) == 10
