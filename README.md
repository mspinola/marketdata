# marketdata

[![CI](https://github.com/mspinola/marketdata/actions/workflows/python-test.yml/badge.svg)](https://github.com/mspinola/marketdata/actions/workflows/python-test.yml)
[![Vendor pin](https://github.com/mspinola/marketdata/actions/workflows/vendor-pin.yml/badge.svg)](https://github.com/mspinola/marketdata/actions/workflows/vendor-pin.yml)
[![Python](https://img.shields.io/badge/python-3.10%E2%80%933.14-blue)](pyproject.toml)
[![License: MIT](https://img.shields.io/badge/license-MIT-green)](LICENSE)

Daily bars — equities, ETFs and futures — as a producer/consumer split over a
file-based store, with adjustment **derived on read wherever it can be**.

Sibling of [cotdata](https://github.com/mspinola/cotdata) and built on the same
design ideas, but deliberately a separate package. cotdata's registry requires a
`cftc_code` and equities have no COT report. See
[docs/design.md](docs/design.md) for the full reasoning and for everything about
each vendor's behaviour that was verified rather than assumed.

Futures arrived with ADR-0007, which makes cotdata CFTC positioning only and
moves every bar here.

## Install

```bash
uv venv --python 3.11 && uv pip install -e ".[yahoo,dev]" "setuptools<81"
```

From an index, the distribution is **`crucible-marketdata`** and the import stays
`marketdata`:

```bash
uv pip install crucible-marketdata     # then: import marketdata
```

The two differ because `marketdata` is taken on PyPI by an unrelated, abandoned
project (`marketData` 0.2.0, last released 2020-04-19 — PyPI normalises both to the
same name). Same split as `python-dateutil` → `import dateutil`. **A dependency on
this package must name `crucible-marketdata`**, since that is what pip resolves;
depending on `marketdata` would fetch a stranger's 2020 module.

On the **Windows futures producer**, add the `norgate` extra — nothing else pulls
`norgatedata`, and without it `--domain futures` stops before it fetches:

```bash
uv pip install -e ".[yahoo,norgate,dev]" "setuptools<81"
```

Installing it elsewhere does not help. It drives a locally installed Norgate Data
Updater rather than an API, and NDU is Windows-only, so every other machine reads
a synced store instead of producing one.

## Use

```bash
export MARKETDATA_STORE=~/code/marketdata_store
marketdata-update --bars                    # every registry symbol this box can produce
marketdata-update --bars --symbols SPY TLT  # scoped
marketdata-update --bars --domain equities  # skip futures (no Norgate on this box)
marketdata-update --metadata                # futures contract specs (Windows + Norgate)
marketdata-update --check                   # read-only summary, no network

marketdata-update --bars --domain futures --require-final   # only once Norgate has settled

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

## Two domains, two adjustment axes

The domain a symbol belongs to decides which tiers it has, and `get_bars`
resolves it from the registry rather than taking it as an argument. Asking for a
futures tier on an equity — or the reverse — raises a message naming the right
ones.

| Domain | Tiers | Stored | Derived on read |
|---|---|---|---|
| `equities` | `split`, `raw`, `total` | one frame | all three |
| `futures` | `backadj`, `unadj`, `propadj` | **both** `backadj` and `unadj` | `propadj` |

Equities derive everything because corporate actions arrive as dated events
alongside the bars. Futures cannot: Norgate's back-adjustment is roll splicing it
performed itself, and the stitched calendar spread at each roll appears in no
other series, so `backadj` and `unadj` are two separate stored facts.

## The three equity adjustment tiers

The store holds one frame per equity symbol exactly as the vendor serves it, plus
the dated action columns. Yahoo's `Adj Close` is **not** stored: it is restated
every time a new dividend lands, so a backtest pinned to it is not reproducible.
Raw bars plus dated actions are immutable facts, and `marketdata.adjust` rebuilds
any tier from them deterministically.

| Tier | Splits | Dividends | Use |
|---|---|---|---|
| `split` (default) | applied | no | continuous price series, price-based signals |
| `raw` | un-applied | no | as-traded. Price-level logic, or an engine that models dividends itself |
| `total` | applied | reinvested | any hold spanning an ex-date, where the return is what you measure |

This is not a cosmetic distinction. TLT over its full history returns **+2.1%** on
the price series and **+132.3%** on total return.

## The three futures adjustment tiers

| Tier | What it is | Use |
|---|---|---|
| `backadj` (default) | additive back-adjustment, as Norgate computes it | signals and stops. Preserves absolute daily price *changes* |
| `unadj` | raw front-month, real spread gaps at each roll | absolute price level, point-value sizing |
| `propadj` | ratio back-adjustment, derived from the two above | volatility and any percent return |

`propadj` is not an optional refinement. Additive adjustment accumulates roll
gaps downward, and across the cotdata store **52.3% of ZS's back-adjusted closes
and 41.2% of DC's are non-positive** — a percent return or an R-multiple is
meaningless on those. Ratio adjustment preserves percentage returns. It does not
make the series strictly positive: it scales by a positive factor, so it keeps
the underlying's sign, and CL prints −24.11 on 2020-04-20 because WTI really
settled at −37.63. That is one bar out of the whole store.

Because `propadj` needs both stored tiers, **the futures producer writes both or
neither**, and a read that finds only one raises instead of returning empty. A
half-stored symbol is worth being loud about: additive back-adjusted percent
volatility comes out ~200x too high for soybeans and 0.47x for gold, and 0.47x
passes every implausibility screen a spot check would apply.

## Store layout

```
$MARKETDATA_STORE/
  bars/<domain>/<source>/<symbol>.parquet          # equities — one stored frame
  bars/<domain>/<source>/<symbol>_<tier>.parquet   # futures  — one per stored tier
  metadata/contract_specs.parquet                  # futures point value, tick size, margin
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
| `MARKETDATA_PRICE_SOURCE` | deployment default vendor, `yfinance` if unset. Futures ignore it — only Norgate serves them |
| `MARKETDATA_NO_NETWORK` | skip the network tests |

The store root may share a parent folder with cotdata's, but the two must not
share a `manifest.json`. Both producers do a read-modify-write on it.

### On the Windows futures producer

That box now runs **two producers**, so it needs **both** store variables set at
once, pointing at **different roots**. This is new with the futures domain: until
ADR-0007 moved bars here, `COTDATA_STORE` alone was the whole story.

```cmd
setx COTDATA_STORE    C:\Users\YourUsername\cotdata_store
setx MARKETDATA_STORE C:\Users\YourUsername\marketdata_store
```

`setx` persists; plain `set` lasts only for the current Command Prompt, which is
the usual reason a scheduled task cannot find a store an interactive shell could.
Open a NEW prompt afterwards — `setx` does not affect the one you typed it in —
and verify:

```cmd
echo %COTDATA_STORE%
echo %MARKETDATA_STORE%
marketdata-update --check
```

`--check` reads the manifest and no network, so it is the cheap confirmation that
the variable points where you think. An unset variable is refused by name rather
than defaulted, because a silent default would write a second store somewhere
nobody looks.

**Do not point them at one root.** Sharing a parent folder is fine and makes the
pair easy to sync; sharing a root is not, because both packages keep a
`manifest.json` at their root and each does a read-modify-write on it, so the two
producers would eventually drop each other's entries.

Python, virtualenv and Task Scheduler setup are identical to cotdata's and are
not duplicated here — see
[cotdata's Windows setup guide](https://github.com/mspinola/cotdata/blob/main/docs/WINDOWS_SETUP.md).
The only marketdata-specific pieces are the `norgate` extra in **Install** above,
the two variables here, and the finals gate below.

### Waiting for Norgate's Finals

Schedule the nightly futures run with `--require-final`:

```cmd
marketdata-update --bars --domain futures --require-final
```

Norgate's Final futures prices land in the evening, but the Norgate Data Updater
still has to *pull* them on its next poll. `--require-final` fetches only once
Norgate holds a **newer settled session than the store already does** (checked
across `ES`, `CL` and `ZC`, all of which must have advanced). Until then it prints
which reference is lagging and **exits non-zero**.

Give the task a **restart on failure** in Task Scheduler. That turns "fire at 9pm"
into "run the moment the Finals land": each retry is one short date comparison that
exits immediately until they do, and on a weekend or holiday the retries simply
exhaust, harmlessly. `schtasks` cannot set restart-on-failure, so use PowerShell, as
in [cotdata's scheduling guide](https://github.com/mspinola/cotdata/blob/main/docs/WINDOWS_SCHEDULING.md).

The gate is **opt-in and futures-only**. Without the flag the run is unconditional
as before; with `--domain equities` it is refused rather than ignored, because
yfinance has no settled-versus-interim distinction to gate on. `--final-cutoff` is
accepted and ignored, so a scheduler carrying cotdata's flag does not break: the
gate compares data, not clocks, and [docs/design.md](docs/design.md) records why
(cotdata's fixed cutoff deferred every attempt on 2026-07-27, when Norgate
published at 8:49pm against a 20:55 threshold).

If the bars task is currently **chained behind cotdata's `run-prices.cmd`** with an
`ERRORLEVEL` guard, cotdata's gate has been protecting this one. That still works;
the flag makes the task correct on its own, so the chain becomes a convenience
rather than the only thing standing between the store and an unsettled bar.

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
