# Scoping: should the 42 Macro price sheet become a marketdata vendor?

**Date:** 2026-08-29
**Status:** scoping only. No decision, no ADR, and nothing written to any store.
**Raised by:** the 42 Macro KISS evaluation work, which needs bear-market price
history that `marketdata` does not currently carry.

---

## The asset

42 Macro ships a weekly `.xlsb` workbook whose `Prices` sheet holds **111 named
tickers, 7,693 daily rows, 1997-01-02 to the workbook date**. Verified against
`2026-06-27.xlsb`. Coverage spot-checks:

| ticker | first bar |
|---|---|
| SPY | 1997-01-02 |
| XLK | 1998-12-22 |
| QQQ | 1999-03-10 |
| IWM, EWZ | 2000 |
| TLT | 2002-07-26 |
| GLD | 2004-11-18 |
| SLV, GDX, USO | 2006 |
| DBB, HYG | 2007 |
| Bitcoin | 2010-07-19 |

The universe spans US sectors (all eleven SPDRs), factors, country and regional
ETFs, credit (HYG, LQD, BKLN, EMB, EMLC, PFF, CWB, BIZD), rates (SHY through
TLT, plus 2y/10y/30y yields, breakevens, MOVE, CVIX), FX (DXY, UUP, six FX ETFs),
commodities and metals, and two crypto series.

## Why it is tempting

`marketdata`'s registry currently carries 16 entries. The 42 Macro sheet would
add roughly seven times that in breadth and, more importantly, **history through
four bear markets**: 2000-02, 2008-09, 2020 and 2022. Every study in this
workspace that touches equities is currently limited to post-2021 daily history,
which contains no sustained equity decline. That is a real and recurring
constraint, not a nice-to-have.

## Why it is not a small change

Four reasons, roughly in order of how much damage a casual seed would do.

**1. ADR-0007 governs where bars live, and this is bars.** Adding a price source
is exactly the decision ADR-0007 settled for Norgate and databento. A third
vendor needs the same treatment: a provider module under
`marketdata/src/marketdata/providers/`, registry entries carrying the reason
inline, and an ADR recording the scope. It does not get to arrive as a CSV.

**2. The equities half of the store is pinned and mirrored, and a one-sided seed
is a delayed-action delete.** `CLAUDE.md` records this twice over: a
`robocopy /MIR` from the Windows producer deleted the Mac's entire equities half
once, and `EEM`/`EFA` were later seeded on the Mac alone and removed by the next
mirror. Any new symbol must exist on the Windows store first. Seeding 111 tickers
from a Mac session is the failure mode that file warns about, at scale.

**3. Vendor disagreement is unmeasured.** The sheet is Bloomberg-sourced and
price-only (no dividend adjustment), while the existing equities half is yfinance
with corporate-action adjustment derived on read. Those are different quantities.
`make_daily.py` in the 42 Macro folder validates the sheet against an IBKR tail
and finds exact agreement over 203 overlapping days, which is encouraging but
covers one vendor pair over three months, not Bloomberg against yfinance across
thirty years.

**4. Provenance and licence.** The workbook is subscriber material. Deriving a
store from it raises a redistribution question that a public repo like
`marketdata` should answer explicitly before, not after.

## Options

**A. Do nothing.** Studies that need long history keep reading the workbook
directly from the 42 Macro folder, as
`npf/scripts/forecast_verification_42macro.py` does now. Zero risk, zero
governance cost, and the data stays outside version control and outside the
coverage checks.

**B. npf-local projected fixture.** Build a study-scoped parquet from the sheet,
in the spirit of `npf/tests/fixtures/make_marketdata_store.py`, which projects
rather than duplicates. Keeps the governed store untouched. Cost is a second
copy of price data with its own drift risk, which is precisely what the fixture
pattern was designed to avoid for the cotdata case.

**C. Full marketdata vendor.** `providers/fortytwo.py`, registry entries, an ADR,
and a producer path. The only option that makes the history a first-class,
coverage-checked citizen. Also the only one that touches the pinned equities
half, and it needs the Windows-first ordering to be respected.

**D. Vendor, but a separate domain.** Same as C, except the 42 Macro history
lands under its own domain rather than merging into the existing equities half,
so the pin, the mirror lists and the yfinance producer are untouched. Consumers
opt in explicitly. Heavier registry work, much lower blast radius.

## What would need deciding

1. Is redistribution of a subscriber-sourced price series acceptable in a public
   repo, and if not, does the vendor belong in a private sibling instead?
2. Price-only versus adjusted: does a consumer reading `get_bars` need to know
   which basis it got, and does that need a column rather than a convention?
3. If C or D, who produces it? The workbook arrives weekly on a Mac, and every
   other bar producer in this workspace runs on the Windows box.
4. Is the demand real, or is it one study? Worth answering before building
   anything: if only the 42 Macro evaluation needs it, option A or B is correct.

## UPDATE 2026-09-03: the source is dead, which moots question 4

42 Macro has stopped publishing the `.xlsb`. The newest workbook is
`2026-06-27.xlsb` and there will be no more.

That changes the problem from "should we ingest a convenient extra feed" to
"this is the only copy of a series that can no longer be regenerated". Question
4 above asked whether demand was real or whether it was one study. It no longer
matters: the data cannot be re-fetched later if the answer turns out to be yes.

Two consequences.

**1. Preservation is now urgent and separable from ingestion.** Whatever is
decided about a marketdata vendor, the workbooks themselves should be backed up
somewhere durable and checksummed. They are the sole record of 111 Bloomberg
sourced series from 1997-01-02 to 2026-06-26, covering the 2000-02, 2008, 2020
and 2022 bears that no other source in this workspace reaches. Losing them is
irreversible in a way the earlier draft of this note did not contemplate.

**2. Going forward, the feed splits by data class.** The replacement is not one
vendor:

| Class | Replacement | Notes |
|---|---|---|
| US yields, curve, breakevens, real | **FRED** `DGS2` `DGS10` `DGS30` `T10Y2Y` `T10YIE` `DFII10` | free, no key, daily to 1962, official H.15. Verified current 2026-09-02 |
| Credit spreads | **FRED** `BAMLH0A0HYM2` (HY OAS), `BAMLC0A0CM` (IG OAS) | free |
| VIX | **FRED** `VIXCLS` or yfinance `^VIX` | free |
| ETFs (most of the 111) | **yfinance / IBKR**, already wired | the existing equities producer covers this |
| MOVE, CVIX, FX-implied vol, forward curves, daily foreign govvies | **no clean free source** | genuinely Bloomberg. FRED has foreign long rates monthly only |

So the forward-looking gap is narrow: a handful of vol and forward-curve series,
not the whole workbook.

**A FRED provider is the obvious next build**, and it is a much smaller decision
than the one this note was originally about. FRED is public, redistributable,
and free, so it raises none of the licence questions in section 4 above. It
would sit alongside `norgate.py` and `databento.py` and it does not touch the
pinned equities half.

## Recommendation

Preserve the workbooks now, as a separate action from any ingestion decision.
On the vendor question itself this note still makes no recommendation, but the
option set has shifted: option A (do nothing) now carries a real cost, because
"read it from the folder later" assumes a folder that no longer refreshes and a
file set with no second copy.

## Bottom line, plain language

There is a genuinely valuable pile of price history sitting in a spreadsheet: it
reaches back to 1997 and covers the bear markets none of our current data has.
Pulling it into the shared store would help several studies, but the store has
tripwires that have already caused real data loss twice, and this data comes from
a paid subscription. Nothing has been moved. The first question to settle is
whether more than one study actually needs it.
