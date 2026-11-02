"""`--fetch-naaim` / `--build-naaim`: the NAAIM Exposure Index from the free table.

The captured page is NAAIM's data and is not committed, so the pages here are
synthetic in the same markup (one table, a thead row, one tbody row per week,
newest first). The two anchor weeks carry their published values. Every
refusal is checked to have written nothing. No network: `fetch` is exercised
with urlopen patched.
"""
import datetime as dt
import io

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
    assert not res["ok"] and "an earlier capture read 93.77" in res["errors"][0][1]
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


def test_registry_entry():
    s = REGISTRY[SYM]
    assert s.naaim == "NAAIM Number" and s.kind == "exposure" and s.domain == "series"
    assert ("2024-01-31", 87.36) in s.anchors
