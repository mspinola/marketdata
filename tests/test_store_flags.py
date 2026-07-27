"""Store-level facts live in the MANIFEST, so they travel with the data.

The survivorship warning used to exist only as a comment in registry.yaml, which
also told the reader to go check `universe_is_point_in_time` in the manifest. That
key was never written by anything. A consumer following the instruction found
nothing, and nothing is indistinguishable from a pass.

These tests hold the flag to the property that makes it worth having: absence must
never read as permission. No network.
"""
import json

import pandas as pd
import pytest

from marketdata import config, store, update

D = "equities"


def frame(n=3):
    idx = pd.date_range("2020-01-01", periods=n, freq="B")
    c = pd.Series(range(1, n + 1), index=idx, dtype=float)
    return pd.DataFrame(
        {"Open": c, "High": c, "Low": c, "Close": c, "Volume": 1_000_000.0,
         "Dividends": 0.0, "Stock Splits": 0.0, "Capital Gains": 0.0},
        index=idx)


def test_writing_bars_stamps_the_flag(tmp_store):
    store.write_bars("SPY", frame(), domain=D, source="yfinance")
    m = store.load_manifest()
    assert m["universe_is_point_in_time"] is config.UNIVERSE_IS_POINT_IN_TIME
    assert store.is_point_in_time() is False


def test_a_store_that_never_recorded_it_answers_None(tmp_store):
    """Not False. `None` is "nobody said", and the caller must be able to tell."""
    store.write_bars("SPY", frame(), domain=D, source="yfinance")
    m = store.load_manifest()
    del m["universe_is_point_in_time"]
    config.manifest_path().write_text(json.dumps(m))

    assert store.is_point_in_time() is None


def test_require_refuses_on_a_false_flag(tmp_store):
    store.write_bars("SPY", frame(), domain=D, source="yfinance")
    with pytest.raises(RuntimeError, match="NOT point-in-time"):
        store.require_point_in_time()


def test_require_refuses_when_the_flag_is_MISSING(tmp_store):
    """The defect this whole module exists for: silence must not pass the guard."""
    store.write_bars("SPY", frame(), domain=D, source="yfinance")
    m = store.load_manifest()
    del m["universe_is_point_in_time"]
    config.manifest_path().write_text(json.dumps(m))

    with pytest.raises(RuntimeError, match="does not record"):
        store.require_point_in_time()


def test_stamp_flags_adds_the_key_without_touching_updated_at(tmp_store):
    """The migration path has to be safe for a store somebody already pinned.

    Re-fetching to acquire a constant would rewrite every `updated_at`, which is
    exactly the field a pinned snapshot compares, so the fix would break every
    study it was meant to protect.
    """
    store.write_bars("SPY", frame(), domain=D, source="yfinance")
    before = store.load_manifest()
    m = dict(before)
    del m["universe_is_point_in_time"]
    config.manifest_path().write_text(json.dumps(m))

    after = store.stamp_flags()

    assert after["universe_is_point_in_time"] is False
    assert after["bars"] == before["bars"]      # every entry byte-identical


def test_stamp_flags_cli_needs_no_network(tmp_store, capsys):
    store.write_bars("SPY", frame(), domain=D, source="yfinance")
    assert update.main(["--stamp-flags"]) == 0
    assert "universe_is_point_in_time=False" in capsys.readouterr().out


def test_check_says_so_out_loud(tmp_store, capsys):
    """A flag nobody reads is the comment we started with. --check reads it."""
    store.write_bars("SPY", frame(), domain=D, source="yfinance")
    assert update.main(["--check"]) == 0
    assert "survivors only" in capsys.readouterr().out
