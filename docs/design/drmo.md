# Dr. Mo: 42 Macro's weekly table, transcribed on the box, kept as published

Status: built 2026-11-21 (`providers/fortytwo_drmo.py`, `marketdata-update --build-drmo`,
readers `marketdata.drmo()` and `marketdata.drmo_history()`). First consumer: cot-analyzer's
weekly setup watchlist, which shows the reading beside each set-up market as context.
Not yet run on the producer box: the routine and wrapper under `docs/examples/windows/`
(`drmo-routine.md`, `run-drmo.cmd`) are proposals to apply there.

**Licence rule for this repository.** The table is 42 Macro subscriber material. The owner
approved committing its column names and encodings here (2026-11-21). No reading, no asset
list copied from a report, no sample row and no paraphrase of report text may appear in code,
tests, fixtures, docs or commit messages. The test fixture is invented end to end.

## The source

A weekly slide dated a Saturday, titled the Discretionary Risk Management Overlay ("Dr. Mo").
Its shape was read off one report (2026-11-21); the shape, not the content, is recorded here:

- one **market regime** label for the whole report;
- about a hundred assets in five titled groups, each printed as a name with an ETF ticker in
  parentheses (a couple of crypto rows carry no ticker);
- per asset, **VAMS** (Volatility-Adjusted Momentum Signal), a three-state mark that the
  slide's legend defines as bullish, neutral or bearish;
- per asset, **RSI**, a 14-day Relative Strength Index printed as a whole number;
- per asset, **PROPER TRADE**, one of five prescribed positions: long max, long half, no
  position, short half, short max. The legend says a highlighted cell marks a change since
  the previous report, by direction.

It is printed in the weekend **Around the Horn** deck, which normally appears Sunday
afternoon around the 17:00 ET Sunday call, although the slide is dated the Saturday. The
weekday **Leadoff Morning Note** deck carries the same slide under its own date and is the
fallback when the weekend deck is not available. So `report_date` is the date printed on
the slide transcribed: usually a Saturday, a weekday when the fallback was used.

There is no API and no file export. It reaches a machine only through a Claude session that
can see the slide, which until now was a claude.ai Project of the owner's.

## Why the store, and why built on Windows

The watchlist is built on the Mac and served from the VPS, and both read a replica of
`MARKETDATA_STORE` that the Windows box mirrors with `robocopy /MIR` and rsync. Anything the
Windows store lacks is purged from the Mac by the next mirror, so the table has to be born on
the box. `metadata/` is in neither sync's exclusion list (the bar pass excludes `_raw` only),
so a table there reaches both replicas, as `contract_specs` and `econ_calendar` do. The raw
transcriptions sit under `_raw/`, which both syncs exclude by name, so they never leave the
box.

## Layout

```
<MARKETDATA_STORE>/
  _raw/fortytwo/drmo/YYYY-MM-DD.csv   one transcription per report, named by report date
  metadata/drmo.parquet               every week, one row per (report_date, asset)
  manifest.json                       "metadata" -> "drmo" entry per build
```

`$MARKETDATA_FORTYTWO_RAW` overrides the raw root, as the NAAIM and AAII variables do.

## The handover file

UTF-8 CSV, this header exactly, one row per asset:

```
report_date,regime,group,asset,ticker,vams,rsi,proper_trade
```

| column | content |
|---|---|
| `report_date` | the report's date, ISO, the same on every row and equal to the file name |
| `regime` | the report's market regime label, the same on every row (stored upper-case) |
| `group` | the title of the block the asset is printed in |
| `asset` | the asset's name as printed, without the ticker. Quoted if it holds a comma |
| `ticker` | the ticker in parentheses, or empty when none is printed |
| `vams` | `bullish`, `neutral` or `bearish` |
| `rsi` | whole number, 0 to 100 |
| `proper_trade` | `long_max`, `long_half`, `none`, `short_half` or `short_max` |
| `change` (optional column) | `bullish` or `bearish` where the cell is highlighted as changed, else empty. Leave the column out when the highlight cannot be read reliably |

The stored table adds `position`, the signed fraction `proper_trade` stands for (1.0, 0.5,
0.0, -0.5, -1.0), so a consumer can sort or colour without re-deriving it.

The asset list is deliberately **not** in this repository. The build learns it from the first
week filed and holds each later week to the week before.

## The build

```
marketdata-update --build-drmo            build from the files already in the raw folder
marketdata-update --build-drmo PATH       first file PATH under its own report_date, then build
```

No network. Every filed week the table lacks is added; every week it holds is re-verified
against its file. Refusals, each naming the file and writing nothing for that week:

1. Not the agreed CSV: header, one date matching the name, one regime, the vocabularies, RSI
   range, duplicate asset or ticker. A model's prose reply saved as the file fails here.
2. A report dated in the future.
3. **A stored week is never rewritten.** A file that differs from its stored week is refused
   with the assets that differ, until `--replace-drmo-week YYYY-MM-DD` names that week. The
   transcriber is a model reading a slide; a second read that disagrees with the first needs
   a person to look at the slide, not a last-writer-wins.
4. **Asset list differs from the previous stored week.** A dropped or renamed row is the
   likeliest transcription error and is indistinguishable from the publisher changing the
   list, so `--accept-drmo-universe` is a person's statement that it was the publisher.
5. **Every reading equals the previous week's.** That is last week's slide under a new date.

Exit codes follow `update.py`: 0 nothing refused, 2 some week refused while the table holds
good weeks (a wrapper still syncs), 1 nothing usable.

What the guards do **not** catch: a single misread mark or digit on a first read. Nothing in
the file is redundant enough to check one cell against another (the regime changes how VAMS
maps to a trade, so no fixed rule relates the two). The routine therefore reads the slide
twice and compares before writing, and the weekly consumer shows `report_date` so a human
glance at the slide remains possible. Treat the table as context, not as an input to
anything that trades.

## The readers, and point-in-time

```python
marketdata.drmo(asof=None, max_age_days=6)   # one report, as published
marketdata.drmo_history()                    # every week
```

`drmo()` returns the newest report dated on or before `asof` (default today). Because weeks
are append-only, `drmo(asof="2026-11-25")` returns what that week's report said, whatever has
been filed since. Keys: `asset` is unique within a report and always present; `ticker` is
unique where present and is the natural join for a consumer's own market-to-ETF map. That
map (which futures market a given ETF row speaks to) is the consumer's judgment and does not
live here.

## What a missing or late week looks like

| situation | `drmo()` returns |
|---|---|
| no build has ever run, or the table has not synced | empty frame, the full column list |
| this week's report not filed yet (any Saturday, and Sunday before the routine) | empty frame: last Saturday's report is 7 days old, past `max_age_days=6` |
| filed Sunday evening, the normal case | the report, from the wrapper's sync onward (minutes after the 18:00 ET routine) |
| filed from the Leadoff fallback | the report under its weekday date, so it ages out correspondingly earlier |
| a week skipped entirely | empty for that week; `drmo(asof=...)` for a day in it is empty too |
| the caller wants the last one regardless | `max_age_days=None`, and `report_date` on every row says how old it is |

Never an exception for a missing table, and never last week's rows in this week's slot. Six
days is "a Saturday report covers through the following Friday".

## On the box

A Claude Desktop local routine on NUCBOX_M8 (the same transport as the TradingView series
and calendar routines) transcribes the slide, writes the CSV into the raw folder, and runs
`run-drmo.cmd`, which builds and then calls the two sync scripts. Exact instructions and the
proposed schedule: `docs/examples/windows/drmo-routine.md`. Delivery does not depend on that
wrapper's syncs alone: every later producer run mirrors both stores, the unconditional 08:10
catch-up included, so a Sunday evening build that somehow missed its own syncs is on the Mac by Monday 08:10 at
the latest. A consumer that wants the week in a Sunday page must build after the routine:
later than about 18:15 ET, or 20:00 ET to cover the retry.
