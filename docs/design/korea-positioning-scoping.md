# Scoping: is there a COT equivalent for KOSPI, and could marketdata carry it?

**Date:** 2026-10-25
**Status:** scoping only. No decision, no ADR, no registry entry, and nothing
written to any store. Updated 2026-10-25 with a measured answer to the price
gate: Norgate does not carry KOSPI 200, TradingView does, and its cash index
arrives dated one session late.
**Raised by:** a chat question, "is there an equivalent of COT positioning data
for Korea's KOSPI?". Everything below marked *verified* was read off KRX's own
pages on 2026-10-25. Everything else is flagged as a check to run.

---

## The short answer

There is no Korean COT. No regulator and no exchange publishes a weekly
trader-category net open interest report for KOSPI 200 futures or options. The
closest thing KRX publishes is **daily turnover by investor type**, which is a
flow, not a position, and it is split by legal entity type rather than by
hedger/speculator.

## What KRX actually publishes

| Dataset | What it is | Granularity | Access | Verified |
|---|---|---|---|---|
| `투자자별 거래실적` (derivatives), menu `MDC0201050302` | buy / sell / net by investor category for the derivatives market | daily | Data Marketplace screen, login required | menu exists, column set not seen |
| `전종목 시세` (derivatives), menu `MDC0201050101` | per-contract quotes including open interest | daily | screen, login required | menu exists |
| Open API `fut_bydd_trd`, `opt_bydd_trd` | per-contract daily trading info | daily | free, API key | yes, full field list read |
| investor-split open interest | would be the real COT analogue | n/a | **not offered** | no such screen among the 17 derivatives statistics menus |

The free Open API's futures endpoint (`fut_bydd_trd`, "선물 일별매매정보
(주식선물外)", data from 2010-01-04) returns exactly fifteen fields per contract
per day: `BAS_DD`, `PROD_NM`, `MKT_NM` (regular or night session), `ISU_CD`,
`ISU_NM`, close, change, open, high, low, `SPOT_PRC`, `SETL_PRC`, `ACC_TRDVOL`,
`ACC_TRDVAL`, `ACC_OPNINT_QTY`. So **aggregate open interest is free and has a
15-year history, and there is no investor dimension anywhere in the free API**.
The whole derivatives Open API is six endpoints, all of them per-contract daily
trading info.

The investor categories on the screen-only dataset are the Korean market's
standard ones: individuals, foreigners, and institutions broken into securities
firms, insurers, investment trusts, private funds, banks, pension funds, plus
other corporates. That taxonomy is the one used for the familiar "foreigners were
net buyers of X trillion won" headlines on the cash market.

### Access terms are the binding constraint, not the data

KRX Open API notice of 2026-06-01 (read in full): only the published service list
is served as an API. For anything else, use the Data Marketplace screens' own
search and download functions, **unofficial libraries such as pykrx are
prohibited**, and otherwise the data must be purchased through the Data
Marketplace's data-products menu.

Two things follow, and both were confirmed by probing:

- The statistics screens now sit behind a login. `MDC0201050302` and the
  data-product catalog both redirect to a sign-in page, and the underlying JSON
  endpoint (`/comm/bldAttendant/getJsonData.cmd`) answers an unauthenticated POST
  with the literal body `LOGOUT`.
- So there are exactly three legal routes to investor-type data: a free account
  plus manual screen downloads, a paid data purchase, or a vendor who already
  licenses it (Yonhap Infomax, FnGuide, Koscom, Bloomberg, LSEG). Scraping is
  explicitly out, which rules out the shape of producer this repo would otherwise
  reach for first.

## Why it is not COT, in the four ways that matter here

**1. Flow, not level.** COT hands you a reported net position each Tuesday.
KRX hands you a day's net buying. Cumulating flows into a position proxy has no
anchor and accumulates error without bound, and it mixes outright, spread and
roll activity into one number. Every positioning index in `cotmetrics` is built
on a level.

**2. The taxonomy cannot express a hedger.** COT's commercial / non-commercial
split is what makes "fade the crowd" meaningful: one side is presumed to hedge a
physical exposure. KRX categories are legal entity types. A Korean pension fund
and a Korean prop desk can sit in adjacent rows with no statement about why
either holds the position.

**3. The informed-party prior is flipped.** The Korean literature consistently
finds foreign institutions informed and domestic individuals the noise traders
(for example Ryu, *Who wins in the money game? The case of KOSPI 200 futures*,
2012, and the sentiment work in *Investment Analysts Journal* 50(4), 2021). The
natural trade is therefore to **follow foreigners and fade individuals**, which
is close to the opposite of a commercial-hedger fade. A port of the NPF
positioning logic would be testing a different hypothesis under the same name.

**4. No release structure, so cotdata's machinery buys nothing.** Daily
publication means no Tuesday snapshot, no Friday release lag, and no
restatements. The vintage subsystem and `asof=` point-in-time reads have nothing
to do here. That removes a whole class of look-ahead bug and also removes the
event the COT studies trade around.

## Where would it live? This is a seam decision, not a file decision

**Not `cotdata`.** ADR-0007 narrowed cotdata to CFTC positioning only, and
`cotdata/src/cotdata/registry.py` hard-requires a `cftc_code` of every symbol.
KOSPI 200 has no CFTC code, because there is no US-listed KOSPI futures contract.
ADR-0002's pattern (a new CFTC report type is in scope) does not reach a
different regulator's data. Putting KRX investor flow in cotdata would reopen the
boundary ADR-0007 closed.

**`marketdata`'s `series` domain is the closest existing shape.** It already
exists for published daily readings that are not prices: one `raw` tier, stored
as served, and a two-stage producer (`providers/tradingview.py`) whose stage 1 is
a Claude Code Desktop local routine writing the vendor's results verbatim to a
producer-internal raw directory, and whose stage 2 is code that validates and
writes, refusing a whole file on any overlap disagreement. A dataset with no free
API that can only be downloaded from a screen is exactly the problem that
producer was built for.

Two concrete mismatches, both small but neither zero:

- `SERIES_KINDS` is `("percent", "count", "ratio")`. An investor net is a
  **signed** count of contracts or a signed won value, so it needs a new kind
  with its own range guard. A `count` guard that assumes non-negative would
  silently reject or mangle half the series.
- A Korean investor reading is a **matrix** (category x buy/sell/net x futures or
  options), not one scalar a day. The `series` schema stores one value per symbol
  per day, so it is either roughly eight to ten registry symbols per product
  (one per category, net only) or a schema change. Eight symbols for one dataset
  is a smell worth noticing before writing any of them.

**Or it belongs in neither.** It is positioning data, not price data, and
`crowdmon` is the precedent that a dataset which is nobody's natural child gets
its own sibling. That is an ADR's decision, not this note's.

## What ingestion would actually take, in order, with the obstacle for each

0. **Name the hypothesis first.** Governance here puts pre-registration before
   data work. Without a stated question ("does foreign futures flow predict
   KOSPI 200 returns at horizon h?") this is data collection, and the stack has
   a standing rule against building a producer for a signal nobody has tested.
1. **Decide the access route.** Free account plus manual screen downloads is
   lawful but manual, which means a human or a Desktop routine in the loop
   forever. Purchase has an unknown price for a single researcher. Neither is a
   `requests.get`.
2. **Bars for the underlying. This was the first gate, and it is now open.**
   Neither stored vendor can serve KOSPI 200: databento is GLBX-only, and Norgate
   does not carry it (confirmed 2026-10-25). TradingView does, through the
   connector this repo already uses for the `series` domain. The measurements are
   in the section below. That turns step 2 from a blocker into a vendor decision,
   and it moves the ETF-proxy escape hatch (EWY, the way
   `cotmetrics.market_data.PRICE_PROXIES` prices MFS and MME off EFA and EEM) off
   the table, which is a relief: MME and MFS yield no trades in an npf book and
   must be evaluated GROSS because the proxy has no futures cost specs.
3. **Registry work.** A new asset class, a domain decision (`series` or
   something new), and the `kind` extension above. `_CLASS_DOMAIN` in
   `registry.py` is where the class-to-domain mapping lands.
4. **Producer.** Shaped like `providers/tradingview.py` if the route is screen
   downloads: raw directory producer-internal, excluded from both store syncs
   (the `_raw` exclusion in `sync-store.cmd` and `push-to-server.cmd`), overlap
   agreement on every bar the store already holds, and a refusal rather than a
   rewrite when a published number disagrees with a stored one.
5. **Store and manifest.** `MARKETDATA_STORE`, and it must not come to share a
   `manifest.json` with another producer: `_touch_manifest` is a
   read-modify-write and two producers on one manifest lose entries.
6. **Seed on Windows first.** The nightly `robocopy /MIR` purges whatever the
   source lacks, so a symbol seeded on the Mac alone is a delayed-action delete.
   This has already fired once in this workspace.
7. **Chase the variable into every launcher.** Any launcher that will make the
   new read needs it defaulted, because launchd and Task Scheduler read no
   profile.
8. **Scheduling is a third task, not a step in an existing one.** The KRX regular
   session closes mid-afternoon KST (verify the exact minute before writing a
   trigger), which is overnight for the Windows box and a different time from
   both existing bars tasks. It therefore needs its own task with its own
   `--domain` flag, for the same reasons the equities and futures halves are
   separate, and `scheduler\verify-scheduling.ps1` has to learn its expected
   args and trigger shape or it will not be checked.
9. **Tests.** Fixture projected rather than committed twice, the way
   `tests/fixtures/make_marketdata_store.py` projects the marketdata fixture from
   cotdata's.

That is nine steps and a subscription question for a dataset whose predictive
content nobody here has measured.

## TradingView serves the price series, measured 2026-10-25

Norgate does not carry KOSPI 200 and databento cannot (GLBX only), so the
remaining route is the TradingView connector this repo already uses for the
`series` domain. It works. Everything in this section was read through that
connector on 2026-10-25 and is reproducible with `mcp-tv-get-ohlcv`.

| Symbol | What it is | Daily bars | Range | Volume | Open interest |
|---|---|---|---|---|---|
| `KRX:K2I1!` | front-month continuous KOSPI 200 futures | 3,568 | 2012-03-09 to 2026-10-23 | yes, 100k to 300k contracts | **no** |
| `KRX:K2I2!` | second-month continuous | same calendar | same window probed | yes, but 2 to 350 a day away from the roll | **no** |
| `KRX:KOSPI200` | cash index | about 9,040 | roughly 1990 to date | null | n/a |

The futures history floor is the feed's, not the contract's: KOSPI 200 futures
have traded since 1996, and the connector serves 2012-03-09 onward. The cash
index reaches back far enough to cover 1997-98 and 2008, with 1,467 bars before
1996 alone, which makes it the better series for anything needing a crisis.

### Three defects to guard, one of them serious

**1. The cash index is labelled one session late.** This is the finding that
matters. Joining `KRX:KOSPI200` to `KRX:K2I1!` on the dates each one publishes
gives a mean absolute daily return difference of **3.398 pp** (max 12.908).
Pulling the index back one session gives **0.298 pp** (max 0.765). A two-session
shift is as bad as none (3.366 pp), so the offset is exactly one session, and the
futures symbol is the correctly dated one. The basis tells the same story: as
labelled it swings between -75 and +70 index points, and after the correction it
is +1.86 points on average, range -4.00 to +9.68, which is 0.0 to 0.6 percent of
index and a plausible futures basis. The index's newest bar is stamped
2026-10-26 while the futures' is 2026-10-23.

The consequence is the reason this is written down. A study that joins these two
symbols on their own labels gets the index's close from the session *after* the
futures bar, which is a one-day look-ahead on the signal side. It would not
announce itself in any backtest. Any ingest has to re-derive the index's session
date rather than trust it, and the guard belongs in code with this measurement
cited beside it.

**2. The newest bar mutates.** The same timestamp (2026-10-23) read three times
within one session returned closes of 858.45, 857.65 and 856.45, with volume
93,517 then 93,636 then 93,963. The tool's own notice says the last bar is still
forming. So the newest bar must never be stored as final, or the existing
overlap-agreement guard in `providers/tradingview.py` will refuse the next
night's file for a disagreement the producer caused itself.

**3. No open interest.** The payload is `t, o, h, l, c, v` and nothing else. This
closes the price gate and does nothing at all for the positioning question that
started the note. TradingView is not a substitute for the KRX investor data.

### What the roll looks like

The roll is locatable without a calendar: `KRX:K2I2!` volume spikes from its 2 to
350 baseline to 1,642, 4,112, 13,904 and 24,344 across 2026-09-03 to 2026-09-08,
then collapses to 13. The front-to-second spread through that week was +1.25 to
+6.45 points, about 0.1 to 0.6 percent, so whatever the adjustment convention,
the roll error in this contract is small. Small is not zero, and the convention
is **not** established by this probe, which is now an open question below.

### Where it would live, which is a different answer from the rest of the note

The `series` domain is the wrong home for this. `registry.yaml` says the
`tradingview` field is "never defaulted; only the `series` domain lives there",
and that domain is one `raw` tier of non-price readings. A KOSPI 200 bar series
is **bars**, so it belongs in the `futures` domain with a third price source
beside `norgate` and `databento`, which means `PRICE_SOURCES`, a provider module,
and an ADR, exactly the treatment ADR-0007 gave the first two. Two further holes
to fill before that: this repo has no KRX market calendar, and the night-session
question above decides what a daily bar even means here.

What does carry over from the `series` work is the producer shape, because the
constraint is identical. There is no data API, the feed reaches this stack
through a connector only a Claude session can call, so stage 1 is a local routine
on the producer box writing results verbatim and stage 2 is offline code that
validates and writes. The guards that module already has (no number through
prose, overlap agreement, refuse rather than rewrite) are the ones this needs,
plus the session-date re-derivation from defect 1.

## The cheaper first probe

If the real question is "does Korean investor positioning predict KOSPI", the
first pass should cost nothing in this repo:

- Hand-download two or three years of `투자자별 거래실적` from the KRX screen,
  or take the US-side proxies (EWY short interest and options open interest),
  and run a one-off study under `npf/docs/` against a pre-registered spec. No
  store, no registry, no producer, no scheduled task.
- Promote to an ingest only if that study says a nightly series is worth having.

The ordering matters because a data-layer scoping note naturally tempts the
opposite: build the pipe, then look for the signal.

## Other Korea datasets worth knowing about

- **Foreign ownership percentage per stock.** A genuine level rather than a flow,
  published by KRX. Structurally the closest thing Korea has to a positioning
  level, but it is cash equity, not futures.
- **Short-sale volume and short balance.** Also position levels, with two
  structural breaks in the series: the full ban from 2023-11-05 and the
  resumption on 2025-03-31. Any study spanning those dates is studying a regime
  change in the data, not in the market.
- **Program trading data**, published daily, which is adjacent to the
  institutional-flow question.
- **V-KOSPI 200**, KRX's implied volatility index, which if it is served the same
  way as the Cboe indices here would be a `series`-shaped reading and a much
  smaller first piece of Korean data than any of the above. Not checked on
  2026-10-25.
- **CFTC COT will never cover this.** COT reports US-listed futures, and KOSPI
  200 is KRX-listed. There is no partial coverage to lean on.

## Open questions, each with how to settle it

| Question | How to settle it |
|---|---|
| Does KRX publish investor-split open interest behind the login? | Free Data Marketplace account, one look at `MDC0201050302`'s column set. No account was created on 2026-10-25. |
| Is `투자자별 거래실적` futures-only or futures-and-options combined? | Same look. This matters the way the CIT Supplemental's combined basis matters in cotdata: a combined series is a different quantity for the same market and week, and nothing in the file says so. |
| Is the front-month continuous series back-adjusted or raw-stitched? | Compare two bars either side of several rolls against the cash index. One roll cannot settle it: an unadjusted roll gap here is about 0.16 pp, inside this probe's 0.765 pp noise floor. |
| Does the daily futures bar include the night session? | The tool's notice says regular session only, but the newest bar kept moving on a Sunday, which is when the Monday night session opens. Read an intraday pull and compare. |
| Does TradingView serve KRX open interest anywhere? | Not in the OHLCV payload. If it exists at all it is a chart study rather than a data field, so assume no until shown otherwise. |
| Is KRX investor data priced for a single researcher? | Data Marketplace data-purchase menu, which is also login-walled. |
| Do the broker terminals' `투자자별 미결제약정` charts come from a KRX licensed feed or a broker computation? | Ask a member firm, or find the field in a KRX product sheet. It decides whether an investor-level position series is purchasable at all. |

## What was read on 2026-10-25

- TradingView connector, `mcp-tv-get-ohlcv` against `KRX:K2I1!`, `KRX:K2I2!` and
  `KRX:KOSPI200`, daily interval. The join, shift test and roll figures above
  were computed from those payloads.

- KRX Open API derivatives service list: <https://openapi.krx.co.kr/contents/OPP/USES/service/OPPUSES005_S1.cmd>
- KRX Open API field spec for `fut_bydd_trd`: the detail page of the above
- KRX Open API notice, "KRX Open API 미제공 데이터에 대한 안내", 2026-06-01: <https://openapi.krx.co.kr/contents/OPP/COMM/notice/OPPCOMM001_S2.cmd?bbsSeq=5>
- KRX Data Marketplace statistics menu tree, derivatives branch `MDC020105*`, enumerated from the portal's own menu data: <https://data.krx.co.kr/contents/MDC/MAIN/main/index.cmd>
- KRX Data Marketplace English statistics menu: <https://data.krx.co.kr/contents/MDC/MAIN/main/index.cmd?locale=en>
- Ryu, *Who wins in the money game? The case of KOSPI 200 futures*: <https://www.sciencedirect.com/science/article/abs/pii/S0927538X12000339>
- *Investor sentiment and market dynamics: evidence from index futures markets*, Investment Analysts Journal 50(4), 2021: <https://www.tandfonline.com/doi/abs/10.1080/10293523.2021.2010376>
- TAIFEX large-trader and institutional open interest, as the contrast case of an Asian exchange that does publish investor-level open interest: <https://www.taifex.com.tw/enl/eng3/largeTraderOptQry>

## Bottom line

In plain language: Korea has no COT, and the substitute is weaker than it first
looks. What KRX gives away free is one aggregate open interest number per
contract per day, back to 2010. What it gives behind a login is daily turnover by
investor type, which is a flow rather than a holding, split by what kind of firm
traded rather than by why. That is enough to test a "follow the foreigners, fade
the individuals" flow hypothesis, and not enough to port a positioning-level fade
to KOSPI. The price gate that used to block all of it is open: TradingView serves
a KOSPI 200 futures series back to 2012 and the cash index back to about 1990,
which is enough to run a study. It is not enough to run it carelessly, because
the cash index arrives dated one session late, and a join on the published dates
would hand a study a one-day look-ahead that no later test would catch.
