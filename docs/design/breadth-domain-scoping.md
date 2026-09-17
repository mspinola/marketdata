# Scoping: a breadth domain for marketdata, and the XLP registry entry

**Date:** 2026-09-13
**Status:** Part A landed (see UPDATE at the end); Part B landed 2026-09-17 with a
different vendor (see the second UPDATE at the end).
**Why now:** cot-analyzer wants a tape-context row beside COT positioning: net
new 52-week highs and lows, the share of stocks above a moving average, and a
defensive-versus-growth ratio (staples against QQQ). The prompt was the pair of
TradingView scripts that plot the "FOMO" read (percent of Nasdaq or S&P stocks
above their 5-day average, tickers `INDEX:NCFD` / `INDEX:S5FD`) and net new
highs/lows. This document scopes what the store would need to carry them.

---

## Two changes of very different size

| | Change | Size | Runs where |
|---|---|---|---|
| A | `XLP` in `registry.yaml` | one YAML entry, one seeding step | the existing 17:30 equities task |
| B | a `series` domain for Norgate breadth indicators | new domain, new producer path, new scheduled task, a subscription | the Windows box, like every other producer |

A can land this week. B has two facts that must be verified on the Windows box
before any code is written, and one purchase decision. They are independent:
A must not wait on B.

## Part A: XLP

### The entry

```yaml
Sector ETF:
  XLP:
    yahoo: "XLP"
    inception: "1998-12-16"
    note: "Consumer Staples Select Sector SPDR. Defensive leg of the XLP/QQQ risk-appetite ratio cot-analyzer draws beside positioning. First sector ETF here; the class name is deliberately generic so siblings (XLK, XLU) can join it."
```

`Sector ETF` is not in `_CLASS_DOMAIN`, so it takes `DEFAULT_DOMAIN`
(`equities`) with no code change. `norgate` is left unset and therefore defaults
to `XLP`, the same as every other ETF in the file; that is harmless only while
the deployment default vendor is yfinance, which it must already be on the box,
since every ETF there resolves the same way and the equities half works.

### The steps, in order

1. Add the entry. `load_registry` validates it at import; `--symbols XLP` is
   accepted from that moment.
2. **Seed on Windows, not on the Mac.** The nightly `robocopy /MIR` purges what
   the source lacks, so a symbol seeded here is a delayed-action delete (it has
   fired once). `run-equities.cmd` fetches every registry symbol unscoped, so
   pulling the registry change onto the box is the seeding step; the 17:30 task
   delivers it. Verify next morning with `marketdata-update --check` and the
   manifest entry `equities/yfinance/XLP`.
3. Nothing to do in `check_price_store`: the boot guard's wanted set comes from
   the cotmetrics params universe, not from the registry, so a registry symbol
   that has not arrived yet cannot refuse a boot.

### The one consumer decision

The ratio must be drawn on the **`total`** tier. XLP distributes roughly 2.5% a
year and QQQ under 1%, so a `split`-tier ratio drifts against the total-return
ratio by about two points a year, and over a five-year window the picture is
materially wrong in a direction that flatters QQQ. `get_bars("XLP", "total")`
is the read; say so in the panel hint.

## Part B: the breadth domain

### What the series are

Norgate computes US market breadth itself, from **point-in-time index
constituents**, and serves it as `#`-prefixed "economic or market indicator"
symbols (`$` is the index prefix, `&` the continuous-futures prefix the store
already uses). The published content list covers:

- 4, 13, 26 and 52-week new highs and new lows, plus cumulative highs-minus-lows
  lines
- advancing and declining issues, with volume; advance/decline ratios and the
  cumulative A/D line
- percent of stocks above their 20, 50, 100, 150 and 200-day moving averages
- average and cumulative Bollinger %B

That is the net new highs/lows read directly, and the "share above average"
read at 20 days and longer. **The 5-day share the FOMO script plots is not in
the list.** The shortest published window is 20 days. Reproducing FOMO exactly
means computing it ourselves from constituents, which Norgate sells only at
Platinum and above. The honest substitute is the 20-day share, labelled as
such, never called FOMO.

### Two facts to verify on the box before any code

Neither could be established from here: the Windows share was not mounted
during scoping and `norgatedata` does not run on a Mac.

1. **Which package carries breadth.** `registry.yaml`'s header says the
   `norgate` key is left defaulted "so the seam is ready if a Norgate US Stocks
   subscription lands", which is the workspace's own record that the box has the
   Futures package only. Norgate's FAQ files breadth under stocks, computed from
   index constituents, and nothing found says the Futures package includes it.
   Assume a US Stocks subscription is a prerequisite until the box says
   otherwise. Tiers and 12-month prices as published: Silver $270 (10 years of
   history), Gold $360 (20 years), Platinum $630 (to 1990, adds historical
   constituents), Diamond $787.50 (to 1950).
2. **The exact symbol strings and which indices they cover.** The content list
   names the indicators, not the symbols. On the box, with NDU running:

   ```
   python -c "import norgatedata; print(norgatedata.databases())"
   ```

   then `norgatedata.database_symbols(<name>)` on whichever database holds the
   `#` symbols, and record the strings for the S&P 500, Nasdaq 100 and NYSE
   composite variants. The registry entries are written from that list, not
   from this document.

Run `scheduler\verify-scheduling.ps1` before and after, per the workspace rule.

### The store question, and it is the same one the rates proposal raised

A count of stocks at new highs is not a bar, and the store is a bar store.
[rates-provider.md](rates-provider.md) laid out the three options for a daily
yield: degenerate bars, a separate domain, or keep it out of the store. The
recommendation there was the separate domain, and the same reasoning holds
here with one addition: **rates and breadth should land on the same new domain,
so the store learns one new thing rather than two.**

Proposed shape, following `adjust.DOMAIN_TIERS` and `STORED_TIERS`:

| | equities | futures | **series** (new) |
|---|---|---|---|
| adjustment axis | split / raw / total | backadj / unadj / propadj | `raw` only |
| stored | one flat frame | two tiers | one flat frame |
| derived | all three | propadj | nothing |
| path | `bars/equities/<src>/<sym>.parquet` | `bars/futures/<src>/<sym>_<tier>.parquet` | `bars/series/<src>/<sym>.parquet` |

The frame is stored **exactly as the vendor serves it**, which is the store's
own rule. Norgate returns indicator series through the same `price_timeseries`
call as everything else, with the usual columns, so the parquet carries whatever
comes back and `Close` is the value consumers read. The domain is what says "no
adjustment applies here": `get_bars("<sym>")` resolves to the `raw` tier,
`check_tier` refuses `split` or `backadj` with a message naming the domain, and
nothing derives anything. That is the honest version of "store what you got".

Touch points, each small, none optional:

- `registry.DOMAINS` gains `"series"`; a new asset class (`Market Breadth`) maps
  to it in `_CLASS_DOMAIN`. Entries carry `norgate: "#..."`, `yahoo: null`, and
  `databento` is already None off the futures domain.
- `adjust.DOMAIN_TIERS`, `STORED_TIERS`, `DERIVED_TIERS` gain the row above.
  `adjust()` on the series domain is a passthrough.
- `bars.get_bars` gains a series branch beside `_equity_bars` and
  `_futures_bars`; `asof=` raises for it as it does for equities.
- `provenance.coverage_gaps` works unchanged once `_stored_tiers` knows the
  domain. `bars._symbol_of` and `available()` need the flat-path case, which is
  the equities case.
- `update.py`'s `--domain` choices gain `series`.

### The producer

The Norgate provider cannot serve this as it stands, and the reasons are
structural rather than a missing flag:

- `DOMAIN = "futures"` is a module constant and `_targets` filters on it.
- `update()` fetches two tiers and refuses a symbol unless both arrive.
- `_norgate_symbol` appends `_CCB` for the backadj tier.
- `_reconstruct_volume` enumerates per-contract symbols from a regex on the base
  symbol. On `#NH52` that matches nothing and falls through to the volume
  passthrough, which is wasted work at best.

So the series path is a **second entry point in the same vendor module**,
`update_series(symbols)`, keeping the convention that modules are named for the
vendor: one `price_timeseries` call per symbol, no suffix, no reconstruction,
one `store.write_bars(sym, df, domain="series", source="norgate")`. The
NDU-reachability guard and the "wrote nothing is a failure" posture carry over
verbatim.

**Freshness gate.** Norgate computes breadth from the session's closes, so it
lands with, or shortly after, the US stock session. The futures finals quorum
(ES, CL, ZC) is the wrong reference: it can be ready before the stock-derived
series has updated, and a step that runs after a satisfied futures gate would
then capture a stale day and report success. The gate for series is the same
`_finals_ready_by_date` core against the series' own last bar, and the store's
manifest entry under `series/norgate/<sym>`.

### The scheduled task

Two ways to run it, and the wrapper rules in the workspace `CLAUDE.md` decide:

- **A step in `run-prices.cmd` after the futures step.** No new task. But each
  step captures its own `ERRORLEVEL` and a futures defer exits non-zero before
  the series step, so on a night where the series lands later than futures the
  repeats defer at futures and never reach the series step. The first success
  would carry stale breadth and nothing retries it.
- **A separate task, `marketdata series`**, with its own repeating trigger and
  `--domain series --require-final`. Retry is the trigger, not restart-on-
  failure. Its wrapper calls the same `sync-store.cmd` and `push-to-server.cmd`
  pair, so whichever producer ran last carries the other halves. This is the
  recommendation.

`verify-scheduling.ps1` gains a row for it: expected args, repetition present,
and the store freshness check on `series/norgate/`. The mirror needs no change:
`sync-store.cmd`'s bars pass mirrors the whole store root and does not exclude
`manifest.json`, so a new `bars/series/` directory rides along. Confirm both on
the box rather than trusting this paragraph.

### The consumer

cot-analyzer reads `get_bars("<internal>")` and scores each series as a range
index over the crowd board's own 13, 26 and 52-week windows, using
`calculate_range_index` unchanged, so "large specs at 92 on NQ" and "net new
highs at 12" read in one language. Decisions that belong to that side, listed
here so they are not discovered late:

- **Internal names.** Registry internals are store filenames, so no `#`. A
  scheme like `SPX_NH52W`, `SPX_NL52W`, `SPX_PCT_ABOVE_20D`, `NDX_...` is
  suggested, and the Norgate string sits in the `norgate` key. Fix the scheme
  after the enumeration, since the covered indices are not yet known.
- **No composite.** One "FOMO" number is what crowdmon was, and it closed with
  no positive result after four pre-registered tests. Each series stays visible
  on its own.
- **Context, not signal.** Nothing here has been through the evaluation ladder
  or crucible. The row is labelled as tape context until something has.
- **The 20-day share is not FOMO.** Name it "% above 20-day" and say in the
  hint that the 5-day read is not available from this vendor.

### Things that will bite

- **`#` in a symbol.** `_norgate_symbol` and the provider's regexes were written
  for `&` roots. `re.escape` handles the character, but the base-symbol
  extraction `lstrip("&").split("_")[0]` does not; the series path must not
  call it.
- **Silver's 10-year floor.** Enough for 52-week windows, and for the board's
  full-history column, whose minimum is 104 weeks. Not enough for anything that
  wants 2008. `coverage_gaps(start=...)` is the check; state the window.
- **Point-in-time is Norgate's, not ours.** Breadth is computed from historical
  constituents on their side, so unlike the ETF universe it is not survivorship
  biased. The manifest's `universe_is_point_in_time` flag describes the symbol
  universe and stays false; do not read a breadth series as making it true.
- **`--symbols` validation** already refuses unknown symbols at the CLI, so a
  series symbol retired from the registry fails loudly. Keep it that way.
- **Editable-install phantom.** The consumer side lives in cot-analyzer, whose
  venv installs marketdata editable. A domain added on a branch is invisible to
  the app until that checkout moves; check `marketdata.__file__` and
  `registry.DOMAINS` from the app's venv, not from this one.

## Order of work

1. Part A, XLP: registry entry, pull onto the box, verify the manifest next
   morning. Independent of everything below.
2. On the box: settle the two facts above. If breadth needs a US Stocks
   subscription, that is the human's purchase decision, and the tier decides
   history depth. Record the answer in `registry.yaml`'s header comment where
   the current "if a subscription lands" note sits.
3. The `series` domain in marketdata, with the rates proposal folded in so the
   two share it. Tests: a `test_series_domain.py` mirroring `test_store_layout`
   (flat path, no tier suffix, `check_tier` refusals), a CLI test for
   `--domain series`, and a provider test with a stubbed `norgatedata`, as
   `test_norgate_producer.py` does.
4. `update_series` in the Norgate provider and the `--require-final` branch for
   it.
5. The Windows task, wrapper, and `verify-scheduling.ps1` row. Run the verifier.
6. The cot-analyzer row, on the board's windows.

## Decisions for the human

1. Buy a Norgate US Stocks tier, and which. Silver covers the board; Platinum is
   the only route to an exact FOMO read and to a survivorship-free equity
   universe, which `design.md`'s known holes already want for other reasons.
2. One `series` domain shared with rates, or a `breadth` domain of its own. This
   document recommends sharing.
3. A separate scheduled task rather than a step. This document recommends the
   task.
4. The internal naming scheme, after the enumeration.

## Bottom line, plain language

Adding the staples ETF is a one-line change plus the usual "seed it on the
Windows machine" step, and it can go in now. The breadth numbers are a real
project: our futures vendor publishes them, but almost certainly only under a
stock-data subscription the Windows machine does not have, and the store has no
place to put a daily count that is not a price bar. The right fix is the same
one the rates proposal asked for, one new "series" domain that both can share,
plus a new nightly task on the Windows box. The exact FOMO read is not
available from this vendor at any tier without building it ourselves; the
20-day version is, and it should be called what it is.

## UPDATE 2026-09-13: no Norgate upgrade for now, so Part A grew to four legs

Decision: do not buy a US Stocks tier yet. Part B stays proposed. Without the
vendor breadth series the context row is built from ETF ratios, and the registry
gained the legs it was missing, all on the existing equities task:

| Ratio | Reads | New leg | Tier |
|---|---|---|---|
| XLP / QQQ | defensive against growth | `XLP` | total |
| RSP / SPY | equal-weight against cap-weight, the breadth proxy | `RSP` | total |
| HYG / IEF | credit appetite | `HYG` | total |
| VIX3M / VIX | vol term structure, below 1 is inversion | `VIX3M` | passthrough |

Each is scored on the crowd board's 13, 26 and 52-week windows with the range
index the board already uses. The same rules apply as for XLP alone: seed on the
Windows box by pulling this registry change there, let the 17:30 task deliver,
verify the four `equities/yfinance/` manifest entries next morning. Home-grown
breadth from constituents was considered and rejected for now: several hundred
extra yfinance symbols a night, and a survivorship-biased history that is only
honest if recorded append-only from launch. The exact net new-highs count remains
the one read gated on the subscription.

---

## UPDATE 2026-09-17: Part B landed, from TradingView rather than Norgate

The `series` domain exists as designed above (`raw` tier only, one flat frame,
`bars/series/<source>/<symbol>.parquet`, `--domain series` refused by `--bars`,
`coverage_gaps` unchanged). The vendor is not Norgate. TradingView publishes the
exact series the AGI scripts read, including the 5-day share this document said
had to be substituted or rebuilt, and the Windows producer box runs Claude Code
Desktop on the account that holds the TradingView connector, so a Desktop local
routine there is the fetch. `providers/tradingview.py` is the build step, with
the guards that stand between a language model and the store. The registry
carries eleven series under `Market Breadth` and `Options Sentiment`, with
`kind` and `anchors`. The measurements, the routine's shape and the verifier
changes are in cot-analyzer's `docs/design/tradingview-breadth-scoping.md`;
the amendment to this document's Part B conclusions is recorded in cot-analyzer's
`docs/design/amendments-2026-09-16.md`. The Norgate path above remains the
design for the advance/decline family if it is ever wanted.
