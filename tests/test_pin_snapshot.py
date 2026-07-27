"""A pinned snapshot must actually catch a store that moved.

The convention it replaces was a table pasted into a pre-registration plus a
sentence saying a re-fetch invalidates it. That is a claim nobody can check without
doing it by hand, and a claim nobody checks quietly stops being true.

`--bars` rewrites every symbol's `updated_at`, and Yahoo restates adjusted history
whenever a dividend lands, so the same command against the same path can produce
different figures on different days.
"""
import json

import pytest

from marketdata import pin


def _snap(**over):
    entry = {"n_rows": 100, "first_date": "2020-01-01", "last_date": "2020-12-31",
             "source": "yfinance", "updated_at": "2026-01-01T00:00:00Z",
             "domain": "equities"}
    entry.update(over)
    return {"snapshot_version": 1, "captured_at": "2026-01-01T00:00:00Z",
            "symbols": {"SPY": entry}}


class _Prov:
    def __init__(self, **kw):
        self.symbol = kw.get("symbol", "SPY")
        self.n_rows = kw.get("n_rows", 100)
        self.first_date = kw.get("first_date", "2020-01-01")
        self.last_date = kw.get("last_date", "2020-12-31")
        self.source = kw.get("source", "yfinance")
        self.updated_at = kw.get("updated_at", "2026-01-01T00:00:00Z")
        self.domain = kw.get("domain", "equities")


def test_unchanged_store_verifies(monkeypatch):
    monkeypatch.setattr(pin, "provenance", lambda s: _Prov())
    ok, problems = pin.verify_snapshot(_snap())
    assert ok and problems == []


@pytest.mark.parametrize("field,changed", [
    ("updated_at", "2026-08-01T00:00:00Z"),
    ("n_rows", 101),
    ("last_date", "2021-01-04"),
    ("first_date", "2019-12-31"),
    ("source", "norgate"),
])
def test_every_pinned_field_is_actually_compared(monkeypatch, field, changed):
    """A field in PINNED_FIELDS that nothing compares is decoration."""
    monkeypatch.setattr(pin, "provenance", lambda s: _Prov(**{field: changed}))
    ok, problems = pin.verify_snapshot(_snap())
    assert not ok
    assert any(field in p for p in problems), problems


def test_updated_at_catches_a_refetch_that_returned_identical_data(monkeypatch):
    """The strictest field, and the reason it is pinned: the bytes may match, but
    the run that produced them is not the run the study cited."""
    monkeypatch.setattr(pin, "provenance",
                        lambda s: _Prov(updated_at="2026-09-09T09:09:09Z"))
    ok, problems = pin.verify_snapshot(_snap())
    assert not ok and "updated_at" in problems[0]


def test_a_symbol_vanishing_is_a_failure_not_a_skip(monkeypatch):
    """The study cited it. Silence would be worse than a wrong number."""
    def _boom(sym):
        raise KeyError(sym)
    monkeypatch.setattr(pin, "provenance", _boom)
    ok, problems = pin.verify_snapshot(_snap())
    assert not ok and "no longer in the store" in problems[0]


def test_empty_snapshot_does_not_pass_vacuously():
    ok, problems = pin.verify_snapshot({"symbols": {}})
    assert not ok
    ok, problems = pin.verify_snapshot({})
    assert not ok


def test_an_older_snapshot_missing_a_field_is_tolerated(monkeypatch):
    """Forward compatibility: a snapshot that predates a pinned field must not fail
    on it. Only fields the snapshot actually recorded are compared."""
    monkeypatch.setattr(pin, "provenance", lambda s: _Prov())
    snap = _snap()
    del snap["symbols"]["SPY"]["source"]
    ok, _ = pin.verify_snapshot(snap)
    assert ok


def test_build_refuses_to_pin_a_symbol_the_store_lacks(monkeypatch):
    """Pinning something absent would produce a snapshot that can never verify."""
    def _boom(sym):
        raise KeyError(sym)
    monkeypatch.setattr(pin, "provenance", _boom)
    monkeypatch.setattr(pin.store, "load_manifest", lambda: {"bars": {}})
    with pytest.raises(KeyError, match="cannot pin"):
        pin.build_snapshot(["NOPE"])


def test_roundtrip(tmp_path, monkeypatch):
    monkeypatch.setattr(pin, "provenance", lambda s: _Prov())
    monkeypatch.setattr(pin.store, "load_manifest",
                        lambda: {"bars": {"equities/yfinance/SPY": {}}})
    p = pin.write_snapshot(pin.build_snapshot(["SPY"], note="a study"), tmp_path / "s.json")
    back = pin.read_snapshot(p)
    assert back["note"] == "a study"
    assert back["symbols"]["SPY"]["n_rows"] == 100
    assert json.loads(p.read_text())["snapshot_version"] == pin.SNAPSHOT_VERSION
    ok, _ = pin.verify_snapshot(back)
    assert ok
