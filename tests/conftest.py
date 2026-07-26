import os
import tempfile

import pytest


def pytest_configure(config):
    config.addinivalue_line(
        "markers", "network: hits Yahoo — deselect with -m 'not network'")


@pytest.fixture()
def tmp_store(monkeypatch):
    """An isolated MARKETDATA_STORE for tests that write."""
    with tempfile.TemporaryDirectory() as d:
        monkeypatch.setenv("MARKETDATA_STORE", d)
        yield d


@pytest.fixture(scope="session")
def yf():
    """yfinance, or skip. Also skips when MARKETDATA_NO_NETWORK is set."""
    if os.environ.get("MARKETDATA_NO_NETWORK"):
        pytest.skip("MARKETDATA_NO_NETWORK set")
    yfinance = pytest.importorskip("yfinance")
    return yfinance
