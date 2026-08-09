"""`marketdata-update --require-final`: the futures producer must never capture a
session Norgate has not settled, and must exit non-zero when it declines to, so
Task Scheduler's restart-on-failure becomes "run when the Finals land".

The gate itself (the pure date/quorum cores) is tested in test_futures.py. These
cover the CLI contract around it: what defers, what runs, what exit code comes
out, and which flag combinations are refused rather than silently ignored.
"""
import pytest

from marketdata import store, update
from marketdata.providers import norgate as nprov

OK = {"kind": "bars_futures_norgate", "ok": True, "wrote": 1, "failed": 0,
      "errors": [], "rows": 10, "newest": "2026-08-07"}

NOT_READY = (False, {"mode": "data", "per_symbol": {
    "ES": {"norgate_last": "2026-08-06", "store_last": "2026-08-06", "ready": False},
    "CL": {"norgate_last": "2026-08-06", "store_last": "2026-08-06", "ready": False},
}})


def test_defers_without_fetching_when_no_new_settled_session(monkeypatch, capsys):
    """The whole point: no fetch at all, so no interim bar can reach the store."""
    calls = []
    monkeypatch.setattr(nprov, "finals_ready", lambda: NOT_READY)
    monkeypatch.setattr(nprov, "update", lambda *a, **k: calls.append(a) or OK)

    rc = update.main(["--bars", "--domain", "futures", "--require-final"])

    assert rc != 0, "a defer must be non-zero so the scheduler retries"
    assert calls == [], "deferring must not fetch"
    assert "Deferring" in capsys.readouterr().out


def test_fetches_when_norgate_has_a_newer_settled_session(monkeypatch):
    calls = []
    monkeypatch.setattr(nprov, "finals_ready", lambda: (True, {"mode": "data"}))
    monkeypatch.setattr(nprov, "update", lambda *a, **k: calls.append(a) or OK)

    rc = update.main(["--bars", "--domain", "futures", "--require-final"])

    assert rc == 0
    assert len(calls) == 1


def test_without_the_flag_the_producer_does_not_consult_the_gate(monkeypatch):
    """Unconditional runs stay unconditional. The gate is opt-in, so an existing
    scheduled task keeps its old behaviour until someone adds the flag."""
    def boom():
        raise AssertionError("finals_ready must not be called without --require-final")

    monkeypatch.setattr(nprov, "finals_ready", boom)
    monkeypatch.setattr(nprov, "update", lambda *a, **k: OK)
    assert update.main(["--bars", "--domain", "futures"]) == 0


def test_the_defer_report_names_each_reference_and_its_two_dates(monkeypatch, capsys):
    """A deferred nightly run is read from a Windows console days later. It has to
    say which reference is lagging and by how much, or the only way to tell a
    working gate from a stuck one is to instrument it."""
    monkeypatch.setattr(nprov, "finals_ready", lambda: NOT_READY)
    monkeypatch.setattr(nprov, "update", lambda *a, **k: OK)

    update.main(["--bars", "--domain", "futures", "--require-final"])

    out = capsys.readouterr().out
    for sym in ("ES", "CL"):
        assert sym in out
    assert out.count("2026-08-06") >= 4  # norgate + store, for both references


def test_equities_refuse_the_flag_rather_than_ignoring_it(capsys):
    """yfinance has no settled-versus-interim distinction. Accepting the flag there
    would leave a scheduled task looking gated when nothing gates it."""
    with pytest.raises(SystemExit):
        update.main(["--bars", "--domain", "equities", "--require-final"])
    assert "futures-only" in capsys.readouterr().err


def test_the_flag_is_refused_when_there_is_nothing_to_gate():
    with pytest.raises(SystemExit):
        update.main(["--check", "--require-final"])


def test_final_cutoff_is_accepted_and_ignored(monkeypatch, capsys):
    """cotdata's wall-clock cutoff deferred every attempt on 2026-07-27, when
    Norgate published at 8:49pm against a 20:55 cutoff. The flag stays accepted so
    a scheduler carrying it does not break, and stays inert so it cannot do that
    again here."""
    monkeypatch.setattr(nprov, "finals_ready", lambda: (True, {"mode": "data"}))
    monkeypatch.setattr(nprov, "update", lambda *a, **k: OK)

    rc = update.main(["--bars", "--domain", "futures", "--require-final",
                      "--final-cutoff", "20:55"])

    assert rc == 0
    assert "ignored" in capsys.readouterr().out


def test_store_last_bar_date_reads_the_key_write_bars_actually_writes(tmp_store):
    """The gate compares against the manifest, so it is one string away from
    reading nothing forever, and a gate that always sees an empty store always
    says ready, which is the failure that looks like success. Pin the two ends
    together by writing through the real producer path.
    """
    import pandas as pd

    idx = pd.date_range("2026-08-03", periods=3, freq="D", name="Date")
    df = pd.DataFrame({"Close": [1.0, 2.0, 3.0]}, index=idx)
    store.write_bars("ES", df, domain=nprov.DOMAIN, source=nprov.NAME, tier="backadj")

    assert nprov._store_last_bar_date("ES") == pd.Timestamp("2026-08-05").date()
    assert nprov._store_last_bar_date("CL") is None  # never captured
