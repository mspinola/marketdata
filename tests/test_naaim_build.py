"""`--fetch-naaim` / `--build-naaim`: the NAAIM Exposure Index from the free table
and from NAAIM's full-history spreadsheet.

The captured page and the spreadsheet are NAAIM's data and are not committed, so
the pages and workbooks here are synthetic in the same markup (one table, a thead row, one tbody row per week,
newest first). The two anchor weeks carry their published values. Every
refusal is checked to have written nothing. No network: `fetch` is exercised
with urlopen patched.
"""
import datetime as dt
import io
import zipfile

import pandas as pd
import pytest

from marketdata import bars, store, update
from marketdata.providers import naaim as nprov
from marketdata.providers.tradingview import RawError
from marketdata.registry import REGISTRY

SYM = "NAAIM_EXPOSURE"
COLS = ("Date", "NAAIM Number", "Bearish", "Quarter 1", "Quarter 2", "Quarter 3",
        "Bullish", "Deviation")


def page(weeks, header=COLS):
    """weeks: [(\"MM/DD/YYYY\", value), ...] newest first, as NAAIM serves them."""
    th = "".join(f'<th class="text-center">{h}</th>' for h in header)
    rows = "".join(
        f"<tr><td>{d}</td><td class=\"text-end\">{v}</td><td>0</td><td>60.00</td>"
        f"<td>90.00</td><td>100.00</td><td>200</td><td>48.99</td></tr>"
        for d, v in weeks)
    return (f'<html><body><div><table class="table table-naaim"><thead><tr>{th}</tr>'
            f"</thead><tbody>{rows}</tbody></table></div><table><tr><td>x</td></tr>"
            f"</table></body></html>")


FIRST = [("02/07/2024", "93.77"), ("01/31/2024", "87.36")]

_SS = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"


def workbook(path, weeks, header=("Date", "Mean/Average", "NAAIM Number")):
    """A one-sheet xlsx as NAAIM ships it: shared-string header, dates as Excel
    serials, newest first. weeks: [(\"YYYY-MM-DD\", value), ...]."""
    def col(i):
        return chr(65 + i)
    head = "".join(f'<c r="{col(i)}1" t="s"><v>{i}</v></c>' for i in range(len(header)))
    rows = [f'<row r="1">{head}</row>']
    for n, (d, v) in enumerate(weeks, start=2):
        serial = (pd.Timestamp(d) - pd.Timestamp("1899-12-30")).days
        rows.append(f'<row r="{n}"><c r="A{n}" s="1"><v>{serial}</v></c>'
                    f'<c r="B{n}"><v>{v!r}</v></c><c r="C{n}"><v>{v!r}</v></c></row>')
    strings = "".join(f"<si><t>{h}</t></si>" for h in header)
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("xl/workbook.xml", f'<workbook xmlns="{_SS}"><workbookPr/></workbook>')
        z.writestr("xl/sharedStrings.xml", f'<sst xmlns="{_SS}">{strings}</sst>')
        z.writestr("xl/worksheets/sheet1.xml",
                   f'<worksheet xmlns="{_SS}"><sheetData>{"".join(rows)}</sheetData></worksheet>')


HISTORY = [("2024-02-07", 93.77), ("2024-01-31", 87.36), ("2017-12-13", 120.55555555555556),
           ("2017-12-06", 99.5), ("2006-07-12", 31.2), ("2006-07-05", 19.444444444444443),
           ("2006-07-12", 31.2), ("2006-07-05", 19.44)]
LATER = [("07/29/2026", "79.70"), ("02/07/2024", "93.77")]


@pytest.fixture()
def raw(tmp_store):
    root = nprov.raw_root()
    root.mkdir(parents=True)

    def put(name, weeks, **kw):
        (root / f"{name}.html").write_text(page(weeks, **kw), encoding="utf-8")
    return put


def stored():
    return store.read_bars(SYM, "series", "naaim")


def test_parse_reads_the_first_table_oldest_first():
    s = nprov.parse_page(page(FIRST), "p")
    assert list(s.index) == [pd.Timestamp("2024-01-31"), pd.Timestamp("2024-02-07")]
    assert list(s) == [87.36, 93.77]


def test_captures_accumulate_past_the_table_window(raw):
    raw("2026-11-01", FIRST)
    assert nprov.build()["wrote"] == 1
    raw("2027-02-01", LATER)   # 01/31/2024 has slid out of this capture
    res = nprov.build()
    assert res["ok"] and res["wrote"] == 1
    got = bars.get_bars(SYM)["Close"]
    assert list(got.index.strftime("%Y-%m-%d")) == ["2024-01-31", "2024-02-07", "2026-07-29"]
    assert nprov.build()["wrote"] == 0


def test_captures_that_disagree_are_refused(raw):
    raw("2026-11-01", FIRST)
    raw("2026-11-02", [("02/07/2024", "93.70")])
    res = nprov.build()
    assert not res["ok"] and "an earlier source read 93.77" in res["errors"][0][1]
    assert stored().empty


def test_a_changed_stored_week_is_refused_unless_accepted(raw, tmp_store):
    raw("2026-11-01", FIRST)
    nprov.build()
    for f in nprov.raw_files():
        f.unlink()
    raw("2026-11-02", [("02/07/2024", "93.70"), ("01/31/2024", "87.36")])
    res = nprov.build()
    assert not res["ok"] and stored()["Close"].iloc[-1] == 93.77
    res = nprov.build(accept_restatement=[SYM])
    assert res["ok"] and stored()["Close"].iloc[-1] == 93.70


@pytest.mark.parametrize("html, match", [
    (page(FIRST, header=("Week", "NAAIM Number")), "header starts"),
    ("<html><body>Please log in</body></html>", "header starts"),
    (page([("02/07/2024", "250.0")]), "outside -200 to 200"),
    (page([("02/07/2024", "93.77"), ("02/07/2024", "93.77")]), "appears twice"),
    (page([("2024-02-07", "93.77")]), "does not parse"),
    (page([("02/07/2024", "n/a")]), "does not parse"),
])
def test_bad_pages_are_refused_and_write_nothing(raw, html, match):
    with pytest.raises(RawError, match=match):
        nprov.parse_page(html, "p")
    (nprov.raw_root() / "2026-11-01.html").write_text(html, encoding="utf-8")
    assert not nprov.build()["ok"]
    assert stored().empty


def test_a_wrong_anchor_is_refused(raw):
    raw("2026-11-01", [("01/31/2024", "87.00")])
    assert not nprov.build()["ok"]
    assert stored().empty


def test_no_captures_is_a_refusal(tmp_store):
    res = nprov.build()
    assert not res["ok"] and "no raw captures" in res["errors"][0][1]


def test_fetch_saves_the_page_verbatim_and_refuses_a_non_table(tmp_store, monkeypatch):
    body = page(FIRST).encode()

    class Resp(io.BytesIO):
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    monkeypatch.setattr(nprov.urllib.request, "urlopen", lambda req, timeout: Resp(body))
    out = nprov.fetch(when=dt.date(2026, 11, 2))
    assert out.name == "2026-11-02.html" and out.read_bytes() == body
    monkeypatch.setattr(nprov.urllib.request, "urlopen",
                        lambda req, timeout: Resp(b"<html>login</html>"))
    with pytest.raises(RawError):
        nprov.fetch(when=dt.date(2026, 11, 3))
    assert not (nprov.raw_root() / "2026-11-03.html").exists()


def test_cli_fetch_failure_still_builds_saved_captures(raw, monkeypatch):
    raw("2026-11-01", FIRST)

    def boom(req, timeout):
        raise OSError("network down")
    monkeypatch.setattr(nprov.urllib.request, "urlopen", boom)
    assert update.main(["--fetch-naaim", "--build-naaim"]) == 1
    assert len(stored()) == 2


def test_history_file_reads_and_collapses_rounded_repeats(tmp_path):
    workbook(tmp_path / "h.xlsx", HISTORY)
    s = nprov.parse_history(tmp_path / "h.xlsx")
    assert len(s) == 6 and s.index.is_monotonic_increasing
    assert s[pd.Timestamp("2006-07-05")] == 19.444444444444443   # the unrounded copy


def test_history_and_captures_build_together(raw):
    raw("2026-11-01", LATER)
    workbook(nprov.raw_root() / "history.xlsx", HISTORY)
    res = nprov.build()
    assert res["ok"] and res["wrote"] == 1
    got = bars.get_bars(SYM)["Close"]
    assert got.index[0] == pd.Timestamp("2006-07-05") and got.index[-1] == pd.Timestamp("2026-07-29")
    assert len(got) == 7


def test_history_that_contradicts_a_capture_is_refused(raw):
    raw("2026-11-01", FIRST)
    workbook(nprov.raw_root() / "history.xlsx", [("2024-02-07", 93.70)])
    assert not nprov.build()["ok"]
    assert stored().empty


def test_the_known_misdated_row_is_redated(tmp_path):
    workbook(tmp_path / "h.xlsx", [("2015-03-25", 84.3), ("2014-03-19", 89.8),
                                   ("2014-03-18", 67.77057142857143), ("2014-03-12", 85.2)])
    s = nprov.parse_history(tmp_path / "h.xlsx")
    assert s[pd.Timestamp("2015-03-18")] == 67.77057142857143
    assert pd.Timestamp("2014-03-18") not in s.index


@pytest.mark.parametrize("weeks, match", [
    ([("2014-03-19", 89.8), ("2014-03-18", 60.0)], "1 day"),            # misdated, not the known row
    ([("2006-07-05", 19.44), ("2006-07-05", 25.0)], "appears twice"),  # repeat that disagrees
])
def test_bad_history_is_refused(tmp_path, weeks, match):
    workbook(tmp_path / "h.xlsx", weeks)
    with pytest.raises(RawError, match=match):
        nprov.parse_history(tmp_path / "h.xlsx")


def test_history_without_the_headline_column_is_refused(tmp_path):
    workbook(tmp_path / "h.xlsx", [("2024-01-31", 87.36)], header=("Date", "Mean", "Other"))
    with pytest.raises(RawError, match="does not name"):
        nprov.parse_history(tmp_path / "h.xlsx")


def test_registry_entry():
    s = REGISTRY[SYM]
    assert s.naaim == "NAAIM Number" and s.kind == "exposure" and s.domain == "series"
    assert ("2024-01-31", 87.36) in s.anchors and s.inception == "2006-07-05"


# ── provisional weeks (hand-kept, a separate series) ─────────────────────────

PROV = "NAAIM_EXPOSURE_PROVISIONAL"


def hand(rows, header="week,value,source"):
    """Write the hand-kept file: rows [(\"YYYY-MM-DD\", value, source), ...]."""
    root = nprov.raw_root()
    root.mkdir(parents=True, exist_ok=True)
    lines = [header] + [f"{w},{v},{s}" for w, v, s in rows]
    (root / nprov.PROVISIONAL_FILE).write_text("\n".join(lines) + "\n", encoding="utf-8")


def provisional():
    return store.read_bars(PROV, "series", "naaim")


def test_provisional_weeks_after_the_official_end_go_to_their_own_series(raw):
    raw("2026-11-01", FIRST)                                  # official ends 2024-02-07
    hand([("2024-02-14", 90.1, "chart"), ("2024-02-21", 88.4, "chart")])
    res = nprov.build()
    assert res["ok"]
    assert list(stored().index.strftime("%Y-%m-%d")) == ["2024-01-31", "2024-02-07"]
    got = bars.get_bars(PROV)["Close"]
    assert list(got.index.strftime("%Y-%m-%d")) == ["2024-02-14", "2024-02-21"]
    assert list(got) == [90.1, 88.4]


def test_official_weeks_replace_provisional_ones_and_are_measured(raw, capsys):
    raw("2026-11-01", FIRST)
    hand([("2024-02-14", 90.1, "chart"), ("2024-02-21", 88.4, "chart")])
    nprov.build()
    raw("2026-11-02", [("02/14/2024", "90.50"), ("02/07/2024", "93.77"),
                       ("01/31/2024", "87.36")])               # NAAIM publishes 02-14
    capsys.readouterr()
    assert nprov.build()["ok"]
    assert stored()["Close"].iloc[-1] == 90.50                 # official, untouched by hand
    assert list(provisional().index.strftime("%Y-%m-%d")) == ["2024-02-21"]
    out = capsys.readouterr().out
    assert "1 provisional week(s) now official" in out and "max abs 0.40" in out


def test_an_unchanged_provisional_series_is_not_rewritten(raw):
    raw("2026-11-01", FIRST)
    hand([("2024-02-14", 90.1, "chart")])
    assert nprov.build()["wrote"] == 2                         # official + provisional
    assert nprov.build()["wrote"] == 0


def test_no_hand_file_writes_no_provisional_series(raw):
    raw("2026-11-01", FIRST)
    assert nprov.build()["wrote"] == 1
    assert provisional().empty


@pytest.mark.parametrize("rows, header, match", [
    ([("2024-02-14", "n/a", "chart")], "week,value,source", "does not parse"),
    ([("02/14/2024", 90.1, "chart")], "week,value,source", "does not parse"),
    ([("2024-02-14", 290.0, "chart")], "week,value,source", "outside -200 to 200"),
    ([("2024-02-14", 90.1, "a"), ("2024-02-14", 90.2, "b")], "week,value,source",
     "appears twice"),
    ([("2024-02-14", 90.1, "a"), ("2024-02-16", 90.2, "b")], "week,value,source", "2 day"),
    ([("2024-02-14", 90.1, "a")], "week,value,note", "missing"),
])
def test_a_bad_hand_file_is_refused_and_writes_nothing(raw, rows, header, match):
    raw("2026-11-01", FIRST)
    hand(rows, header=header)
    res = nprov.build()
    assert not res["ok"] and res["partial"]                    # the official build still ran
    assert match in res["errors"][0][1]
    assert len(stored()) == 2 and provisional().empty


def test_the_provisional_series_is_registered_apart_from_the_official_one():
    s = REGISTRY[PROV]
    assert s.naaim == nprov.PROVISIONAL and s.kind == "exposure" and s.domain == "series"
    assert PROV not in [t.internal for t in nprov._targets()]
    assert [t.internal for t in nprov._provisional_targets()] == [PROV]
    assert "NOT official" in s.note
