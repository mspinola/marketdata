# marketdata

[![CI](https://github.com/mspinola/marketdata/actions/workflows/python-test.yml/badge.svg)](https://github.com/mspinola/marketdata/actions/workflows/python-test.yml)
[![Vendor pin](https://github.com/mspinola/marketdata/actions/workflows/vendor-pin.yml/badge.svg)](https://github.com/mspinola/marketdata/actions/workflows/vendor-pin.yml)
[![Python](https://img.shields.io/badge/python-3.10%E2%80%933.14-blue)](pyproject.toml)
[![License: MIT](https://img.shields.io/badge/license-MIT-green)](LICENSE)

Equity and ETF daily bars: a producer/consumer split over a file-based store,
with corporate-action adjustment **derived on read, never stored**.

Sibling of [cotdata](https://github.com/mspinola/cotdata) and built on the same
design ideas, but deliberately a separate package. cotdata's registry requires a
`cftc_code` and equities have no COT report. See
[docs/design.md](docs/design.md) for the full reasoning and for everything about
yfinance's behaviour that was verified rather than assumed.

## Install

```bash
uv venv --python 3.11 && uv pip install -e ".[yahoo,dev]" "setuptools<81"
```

## Use

```bash
export MARKETDATA_STORE=~/code/marketdata_store
marketdata-update --bars                    # every registry symbol
marketdata-update --bars --symbols SPY TLT  # scoped
marketdata-update --check                   # read-only summary, no network

marketdata-update --pin snap.json           # capture the store's state
marketdata-update --verify-pin snap.json    # prove it has not moved, exit 1 on drift
```

### Pinning a store for a study

A study that quotes numbers is only reproducible if the data behind them is
identifiable, and `--bars` rewrites every symbol's `updated_at` while Yahoo restates
adjusted history whenever a dividend lands. So the same command against the same path
can produce different figures on different days.

`--pin` captures row counts, date spans, source and `updated_at` per symbol.
`--verify-pin` compares them and exits non-zero naming every field that moved. Commit
the snapshot next to the study that depends on it.

A snapshot is **evidence, not configuration**. If verification fails, the honest
response is to say which figures are now unreproducible, not to re-pin and move on.

```python
from marketdata import get_bars

px = get_bars("TLT", "total", start="2010-01-01")
```

## The three adjustment tiers

The store holds one frame per symbol exactly as the vendor serves it, plus the
dated action columns. Yahoo's `Adj Close` is **not** stored: it is restated every
time a new dividend lands, so a backtest pinned to it is not reproducible. Raw
bars plus dated actions are immutable facts, and `marketdata.adjust` rebuilds any
tier from them deterministically.

| Tier | Splits | Dividends | Use |
|---|---|---|---|
| `split` (default) | applied | no | continuous price series, price-based signals |
| `raw` | un-applied | no | as-traded. Price-level logic, or an engine that models dividends itself |
| `total` | applied | reinvested | any hold spanning an ex-date, where the return is what you measure |

This is not a cosmetic distinction. TLT over its full history returns **+2.1%** on
the price series and **+132.3%** on total return.

## Store layout

```
$MARKETDATA_STORE/
  bars/<domain>/<source>/<symbol>.parquet
  manifest.json
```

The vendor is part of the **path**, not just the manifest. Yahoo and Norgate overlap
almost completely on equities and ETFs and do not store the same columns, so a single
`bars/<symbol>.parquet` would let whichever producer ran last silently win. The domain
sits above it because futures and equities have entirely different adjustment axes, so
their frames are not interchangeable even when symbol strings collide. Separate
directories also make a vendor A/B comparison possible:

```python
get_bars("SPY", "total", source="yfinance")
get_bars("SPY", "total", source="norgate")
```

Omit `source=` and the registry resolves one for this deployment. A symbol missing under
the resolved vendor but present under another raises rather than returning empty, because
silently substituting a vendor is what ADR-0006 forbids.

## Environment

| Var | Meaning |
|---|---|
| `MARKETDATA_STORE` | the store root. Required. Reads and writes both guard on it |
| `MARKETDATA_REGISTRY` | override the packaged `registry.yaml` |
| `MARKETDATA_PRICE_SOURCE` | deployment default vendor, `yfinance` if unset |
| `MARKETDATA_NO_NETWORK` | skip the network tests |

The store root may share a parent folder with cotdata's, but the two must not
share a `manifest.json`. Both producers do a read-modify-write on it.

## Tests

```bash
.venv/bin/python -m pytest tests/ -q -m "not network"
```

```bash
.venv/bin/python -m pytest tests/ -q
```

CI runs the first form on every push and PR across Python 3.10 to 3.14. The network
tests are **not** part of that gate. They run weekly in a separate `vendor-pin`
workflow, because their result depends on a third party rather than on this code: a
change in Yahoo's adjustment convention is worth hearing about within a week, but is
never a reason to block an unrelated PR.

`tests/test_pin.py` is the load-bearing one. It reconstructs Yahoo's own
`Adj Close` from `Close + Dividends` across five symbols and asserts the match to
1e-4. That test is what earns the right to drop the restated column from the
store. It also asserts the pin set contains split-heavy names, because TLT and SPY
have never split and would pass even with a double-applied-split bug.

## Survivorship

Every registry symbol is currently listed. yfinance cannot serve delisted
securities or point-in-time index membership, so **this is not a point-in-time
universe and must never be treated as one**. Fine for liquid ETFs, a hard ceiling
for a broad equity study. Norgate US Stocks fixes it, but only at Platinum and
above.

## Not built yet

The RealTest CSV exporter, pending verification of RealTest's current data-import
layout. The intent is a pure projection: parquet stays authoritative, CSV is
regenerated, and the export records which store state fed it.
