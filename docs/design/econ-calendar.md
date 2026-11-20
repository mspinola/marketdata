# The economic calendar: TradingView capture to a snapshot table

Status: built 2026-11-19 (`providers/tradingview_calendar.py`, `marketdata-update
--build-econ-calendar`, reader `marketdata.econ_calendar()`). Consumer: cot-analyzer's
internals page, "Macro calendar" section.

## Why TradingView, and why through the box

The user chose TradingView's calendar, captured on the producer box, over scraping public
release schedules: one source carries the release time, the consensus forecast and the
actual, and the box already runs a Claude Desktop routine against the same connector for the
breadth series (`providers/tradingview.py`). Same two stages: the routine writes the connector
result unchanged to a raw file, and a build that never opens a socket turns it into the store.

## What the connector returns

Measured on one call, `countries=US`, 2026-11-12 to 2026-12-10, `min_importance=-1`, made
2026-11-19 (saved as the scratch capture the tests' fixture is cut from):

- 624 events. One object, `{"result": [...], "status": "ok"}`. Every event carries `id`,
  `date` (UTC ISO with `Z`), `title`, `indicator`, `category`, `importance`, `period`,
  `actual`/`forecast`/`previous` (numbers already divided by `scale`) plus `*Raw`,
  `referenceDate`, `source`, `country`, `currency`. `ticker` is present on 266 of 624, `unit`
  on 231, `scale` on 334. No duplicate ids.
- Importance -1 is 385 rows, 0 is 217, 1 is 22. The 0 tier is noisy (Treasury auctions as
  three rows each, MBA mortgage rates, EIA and USDA weekly stocks, rig counts), so importance
  alone does not give a reader's list; the consumer keeps an allowlist.
- Several important releases have **no ticker**: ISM Services PMI, the S&P Global flash PMIs,
  Chicago PMI, Conference Board consumer confidence. An allowlist keyed by ticker alone misses
  them; cot-analyzer's falls back to the exact title for those.
- **Fed coverage.** The FOMC decision is present (`ECONOMICS:USINTR`, "Interest Rate",
  importance 1, category `mny`, 19:00Z on 2026-12-09), with the reserve-balance rate and the
  rate projections beside it. **FOMC minutes and Fed speeches are absent at every importance,
  -1 included.** Category `mny` at -1 holds only the weekly Fed balance sheet, money supply
  and foreign bond investment. No other source is added for them.
- Cross-check against a published member calendar for Nov 16-20 2026: Empire 7.8 vs est 18.0,
  Retail Sales 0.2 vs 0.2, Industrial Production -0.4 vs 0.1, Housing Starts 1.309M vs
  1.245M, Jobless Claims est 197K (actual 203K), Philly Fed est 20.0 (actual 6.3), UMich est
  51.5. All match.

Reproducer: `python -c "import marketdata as m; print(m.econ_calendar().groupby('importance').size())"`
against a store built from that capture; the fixture
`tests/fixtures/tradingview_calendar_2026-11-19.json` is its importance 0/1 rows with a
ticker plus seven ticker-less rows, `comment` prose dropped.

## Importance for the routine: 0

Importance 0 carries everything the page's allowlist names (the weekly claims, the regional
Fed surveys, PCE headline, ADP and the PMIs are all 0; nothing on the list is -1 only). On
this window that is 239 events, 165 KB, against 412 KB at -1, which adds nothing the
page uses. Both sizes are over the session's inline tool-result limit, so the harness saves
the result to a file and the routine **copies that file**, never retyping it (see the routine
text in cot-analyzer's PR, and `series-routine.md`'s backfill section for the same mechanism).

## Layout

```
<MARKETDATA_STORE>/
  _raw/tradingview/              producer-internal, excluded from both replica syncs
    NASDAQ_FOMO_5D/...           breadth, one folder per registry symbol
    _calendar/YYYY-MM-DD.json    this capture, one per night, ET run date
    _news/...                    reserved for the TradingView news captures
  metadata/
    contract_specs.parquet
    econ_calendar.parquet        snapshot of the newest capture, rewritten each build
  manifest.json                  "metadata" -> "econ_calendar" entry per build
```

The leading underscore is what separates a capture kind from a registry symbol folder, and it
is what the sync exclusions match (`/XD _raw` in sync-store.cmd's bar pass, `--exclude
"_raw/"` in push-to-server.cmd's), so captures never reach a replica. `metadata/` is not
excluded by either, so the table reaches the Mac and the VPS on the next sync, as
contract_specs does. Checked against cotdata `docs/examples/windows/` on 2026-11-19.

## Snapshot, not series

A calendar row is a forecast until the day, then an actual that can be revised. The build
replaces the table whole from the newest capture, so the table means "the calendar as of
`captured`". There is no overlap check because there is nothing settled to protect. A refused
capture writes nothing, so the table stays the last good snapshot and the page can say how
old it is. A study of release surprises would read the raw captures (each night's forecast as
it then stood), not this table.

## On the box

The capture needs two lines beyond the routine call: the routine writes the file, and the
series wrapper (or a calendar step after it) runs `marketdata-update --build-econ-calendar`
before its syncs. Build exit 1 on a refusal; the breadth build's exit codes are unaffected.
