"""Store location. An unset MARKETDATA_STORE is refused by name rather than
defaulted, because a silent default would write a second store somewhere nobody
looks. No network.
"""
import pytest

from marketdata import config


def test_an_unset_store_is_refused_by_name(monkeypatch):
    monkeypatch.delenv("MARKETDATA_STORE", raising=False)
    with pytest.raises(RuntimeError, match="MARKETDATA_STORE is not set"):
        config.store_root()


def test_a_whitespace_only_store_counts_as_unset(monkeypatch):
    """`setx MARKETDATA_STORE " "` and an unset variable are the same mistake, and
    a path of spaces would otherwise resolve to a directory named ' '."""
    monkeypatch.setenv("MARKETDATA_STORE", "   ")
    with pytest.raises(RuntimeError, match="not set"):
        config.store_root()


def test_the_refusal_names_every_domain_the_store_holds(monkeypatch):
    """It described an "equity/ETF bar store" for a release after futures landed,
    so a futures producer hitting it was told the variable was for equities."""
    monkeypatch.delenv("MARKETDATA_STORE", raising=False)
    with pytest.raises(RuntimeError) as ei:
        config.store_root()
    msg = str(ei.value)
    for domain in ("equities", "ETFs", "futures"):
        assert domain in msg


def test_the_refusal_warns_against_pointing_at_cotdatas_store(monkeypatch):
    """The person most likely to see this message is on the Windows box, where
    COTDATA_STORE is already set and reusing it looks reasonable. It is not: both
    packages keep a manifest.json at the root and each does a read-modify-write,
    so one root means the two producers drop each other's entries."""
    monkeypatch.delenv("MARKETDATA_STORE", raising=False)
    with pytest.raises(RuntimeError, match="NOT cotdata's store"):
        config.store_root()
