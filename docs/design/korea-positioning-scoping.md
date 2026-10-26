# Scoping: is there a COT equivalent for KOSPI, and could marketdata carry it?

**Date:** 2026-10-25
**Status:** scoping only. No decision, no ADR, no registry entry, and nothing
written to any store.
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
2. **Find bars for the underlying, and treat this as the first gate.** There is
   nothing to test positioning against without a KOSPI 200 price series.
   databento is GLBX-only, so it cannot serve it. Whether Norgate carries KRX
   KOSPI 200 futures is a one-minute check on the Windows box and should happen
   before anything else in this list. The ETF-proxy escape hatch (EWY, the way
   `cotmetrics.market_data.PRICE_PROXIES` prices MFS and MME off EFA and EEM) is
   the known trap: MME and MFS yield no trades in an npf book and must be
   evaluated GROSS because the proxy has no futures cost specs. Do not plan on
   it.
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
| Does Norgate carry KRX KOSPI 200 futures? | One query on the Windows box. This is step 2 and the cheapest gate in the list. |
| Is KRX investor data priced for a single researcher? | Data Marketplace data-purchase menu, which is also login-walled. |
| Do the broker terminals' `투자자별 미결제약정` charts come from a KRX licensed feed or a broker computation? | Ask a member firm, or find the field in a KRX product sheet. It decides whether an investor-level position series is purchasable at all. |

## What was read on 2026-10-25

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
to KOSPI. Before any of that, the gating fact is unglamorous: this stack has no
KOSPI 200 price series, and nothing about positioning can be tested until it
does.
