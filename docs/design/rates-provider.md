# Design: a rates provider for marketdata

**Date:** 2026-09-03
**Status:** proposed. No code, no registry entries, no store writes.
**Why now:** 42 Macro stopped publishing the `.xlsb`, whose Prices sheet was the
only source in this workspace for Treasury yields, the curve, breakevens, real
yields and credit spreads. Last workbook is `2026-06-27.xlsb`, last data
2026-06-26. See [42macro-vendor-scoping.md](42macro-vendor-scoping.md).

---

## What is being proposed

A third provider under `src/marketdata/providers/`, alongside `norgate.py`
(futures), `databento.py` (futures, cross-platform) and `yfinance.py`
(equities), serving **macro rate and spread series** from FRED.

Suggested name `fred.py`, following the existing convention of naming the
provider after the vendor rather than the data class.

## Source decision: FRED, with Treasury as the validator

Two candidates, both free, both public, both redistributable.

**FRED** (Federal Reserve Bank of St. Louis) is the recommendation. One code
path and one identifier convention cover the whole surface: the curve, the
spread, breakevens, real yields, credit OAS and VIX. History is long (DGS10 to
1962-01-02, DGS30 to 1977, DGS2 to 1976). No API key is needed for the CSV
endpoint, and a keyed JSON API exists if rate limits ever bite.

**US Treasury** publishes the daily par yield curve directly and is the upstream
that FRED redistributes for the CMT series. One call returns the entire curve
for a calendar year:

```
https://home.treasury.gov/resource-center/data-chart-center/interest-rates/
  daily-treasury-rates.csv/<YYYY>/all?type=daily_treasury_yield_curve&_format=csv
```

Keep it as the **cross-check**, not the primary. It covers only the curve, so
using it as primary would mean two providers for one concern.

**Verification note, and it is about tooling not about FRED.** During scoping
the Treasury endpoint returned clean CSV while FRED's `fredgraph.csv` returned
bytes the session's fetch tool could not decode, and FRED's `/data/<id>.txt`
returned the full 1962-onward history as HTML. Both are artifacts of that fetch
tool. A normal HTTP client on the producer host will handle either. Do not read
the scoping session's difficulty as evidence against FRED.

## Series to carry

| Internal | FRED id | Replaces (.xlsb) |
|---|---|---|
| `UST2Y` | `DGS2` | `USGG2YR` |
| `UST10Y` | `DGS10` | `USGG10YR` |
| `UST30Y` | `DGS30` | `USGG30YR` |
| `UST2Y10Y` | `T10Y2Y` | `USYC2Y10` |
| `USBE10Y` | `T10YIE` | `USGGBE10` |
| `USREAL10Y` | `DFII10` | `USGGT10Y` |
| `HYOAS` | `BAMLH0A0HYM2` | `LF98OAS` |
| `IGOAS` | `BAMLC0A0CM` | `LUACOAS` |
| `VIXCLS` | `VIXCLS` | `VIX` (also on yfinance as `^VIX`) |

`UST2Y10Y` is derivable from the two legs and is carried anyway because FRED
publishes it directly, which makes it a free consistency check on the pair.

**Not replaceable from FRED**, and therefore genuinely lost when the workbook
stopped: `MOVE`, `CVIX`, the FX-implied vol series, the forward-rate curves
(`S0042FC 1Y1M` and siblings), and daily foreign government yields (`GDBR10`,
`GJGB10`, `GUKG10`). FRED carries foreign long rates monthly only. That gap
should be recorded rather than quietly papered over with a proxy.

## The design question that has to be settled first

**A yield is not a bar, and the store is a bar store.**

ADR-0007 settled that bars live in `marketdata`. It did not contemplate
single-valued daily series. Three options, and this is the human's call:

1. **Store as degenerate bars.** `Close` carries the value, `Open`/`High`/`Low`
   mirror it, `Volume` is NA. Uniform with everything else, so `get_bars` works
   unchanged and consumers need no new code path. Dishonest in the schema: an
   OHLC on a constant-maturity yield is fiction, and a future reader could
   compute a range from it and get nonsense.
2. **A separate `series` schema and domain.** Honest, and it keeps a bar store a
   bar store. Costs a new read path, new manifest handling, and a second thing
   for consumers to learn.
3. **Keep rates out of the store**, fetched on demand by whoever needs them.
   Cheapest now, and it reproduces exactly the failure this document exists
   because of: a data class with no home, no coverage check and no freshness
   guarantee.

Option 1 is tempting and probably wrong for the same reason the `spread_contracts`
NA convention in `cotdata` exists: a column that looks like data but is not
invites a silent wrong result. Option 2 is the honest one. Option 3 should be
rejected explicitly rather than by default.

## Why this is the easiest new domain in the workspace

**Rates need no Windows box.** Every existing bar producer runs on `NUCBOX_M8`,
because NDU is Windows-only and the equities half is chained to the same
scheduled tasks and mirror passes. A FRED fetch is a public HTTP call with no
platform dependency, so `--domain rates` can run anywhere.

That has a concrete consequence for the two standing mirror rules. Those rules
exist because the nightly `robocopy /MIR` purges whatever the source lacks, so
a symbol seeded on the Mac alone is a delayed-action delete. **If rates are
written by a Mac-side job into a store that Windows mirrors, that trap fires
immediately.** Either rates get their own store root, or the rates job runs on
Windows like everything else, or the mirror exclusions grow a rates carve-out.
Pick one deliberately. This is the single highest-risk part of the change.

## Sketch of the provider surface

Mirror `yfinance.py`, which is the closest analogue (network fetch, no vendor
SDK, no platform lock):

```python
def fetch(series_id: str) -> pd.DataFrame:      # one FRED series -> tidy frame
def update(symbols: Optional[Iterable[str]] = None) -> dict:   # registry-driven
```

Registry entries gain a `fred` key beside `yahoo` and `norgate`, keeping the
established pattern that the internal symbol is the identity and vendor tickers
are attributes. Carry the reason inline, as `registry.yaml` already does for the
four CBOE vol indices.

## Things that will bite

- **Missing values are dots.** FRED writes `.` for holidays and non-trading
  days. Parse to NaN explicitly; a naive read gives an object column and the
  failure surfaces far downstream.
- **Percent, not decimal.** `4.79` means 4.79%. The `.xlsb` used the same
  convention, so existing consumers are safe, but it must be asserted rather
  than assumed.
- **Revisions exist but are rare.** Treasury CMT is effectively never revised
  and H.15 rarely. Lower risk than the futures vintage problem that
  `get_bars(asof=)` exists for, but not zero. If point-in-time rates are ever
  needed, ALFRED serves FRED vintages.
- **Two vendors, two definitions.** Bloomberg generic (`USGG10YR`) and Treasury
  CMT (`DGS10`) are not the same series. Over 2026-06-22 to 06-26 they agree to
  1 to 3bp. That is close enough to plot on one axis and not close enough to
  concatenate silently. Any splice of historical `.xlsb` values onto FRED values
  must be **marked**, the way `claude/long_rate_chart.py` in the 42 Macro folder
  marks its join.
- **Backfill shape differs by source.** FRED returns full history in one call.
  The Treasury CSV is per calendar year, so a backfill loops years.

## Prerequisite, and it is independent of this proposal

**Back up the `.xlsb` workbooks first.** They are the only copy of 111
Bloomberg-sourced series from 1997-01-02 to 2026-06-26, they cover the 2000-02,
2008, 2020 and 2022 bears that nothing else here reaches, and they will never be
reissued. That is a preservation task, not a provider task, and it should not
wait on any decision made here.

## Bottom line, plain language

The dead spreadsheet can be replaced for rates and credit spreads with free
public data from the Fed, and doing it properly means adding one more provider
next to the three that already exist. The real decision is not where to get the
numbers, it is how to store a daily yield in a store designed for price bars,
and the tempting shortcut of pretending a yield is a price bar is the kind of
thing that reads fine now and produces a wrong answer later. There is also one
trap worth being careful about: this would be the first data that does not have
to come from the Windows machine, and the existing mirror setup deletes anything
it does not see there. Separately and more urgently, the old workbooks should be
backed up, because they hold history that cannot be bought or downloaded again.
