# Dr. Mo: 42 Macro's weekly table, transcribed on the box, kept as published

Status: built 2026-11-21 (`providers/fortytwo_drmo.py`, `marketdata-update --build-drmo`,
readers `marketdata.drmo()` and `marketdata.drmo_history()`). First consumer: cot-analyzer's
weekly setup watchlist, which shows the reading beside each set-up market as context.
Live on the producer box since 2026-11-22, when the first week was filed and reached the
Mac replica. The routine, the fetch script and the wrapper under `docs/examples/windows/`
are templates of what runs there (the filled-in copies live in the private scheduler repo).

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

The decks are saved as PDFs on the Mac, one folder per deck kind, each file named by its
date. In the PDF the slide's **title is text and its table is an image** (checked on decks
from 2023 to 2026 with pypdf: the title extracts, no cell does). So no parser can read the
table, and a session with eyes has to. What the text layer does give is the page:
`marketdata-update --find-drmo-slide PDF` prints the one page whose text carries the
overlay's name and a dated title, and that date. Other pages carry the name (an index page,
per-asset chart pages) but none a dated title. A deck with no such page is refused, which is
also what an old deck that predates the slide looks like.

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
| `vams` | `bullish`, `neutral` or `bearish`; empty where the cell prints n/a (an asset with no momentum reading yet) |
| `rsi` | whole number, 0 to 100; empty where the slide prints no RSI (the earliest weeks have no RSI column) or the cell holds no number. Stored as nullable `Int64` |
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
   range, an asset twice in one group, a ticker twice. A model's prose reply saved as the file fails here.
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
been filed since. Keys: a row's identity within a report is (`group`, `asset`). The asset name alone is
**not** unique: the publisher prints the same name in two groups over two different
tickers (found on the first real build, 2026-11-22, which the first version of the guard
wrongly refused). `ticker` is unique where present and is the natural join for a consumer's own market-to-ETF map. That
map (which futures market a given ETF row speaks to) is the consumer's judgment and does not
live here.

## History: the backfill from 2023-10-21

Built 2026-11-22 from the deck PDFs already on the Mac, one report per week, 157 weeks from
2023-10-21 to 2026-11-14, joining the first live week (2026-11-21) without a break in block
names, assets or tickers.

**What is in it, and which slide.** Weeks run Sunday to Saturday. Each week holds the
latest-dated table slide published in it, from the weekend deck where there is one and from
a weekday deck otherwise (Around the Horn preferred on a tie), so `report_date` is usually a
Saturday and sometimes a weekday. That is the same reading the live task takes each Sunday.
Four weeks have no deck at all and are absent (the reader returns empty for them). Decks
before 2023-10-21 carry no such table.

**The table changed shape over time**, and the store keeps each week as printed:

| from | what changed |
|---|---|
| 2023-10-21 | first table: VAMS and an action column, no RSI |
| 2023-12-02 | RSI column appears |
| 2024-01-20 | the action column is relabelled; same five positions |
| 2024-01-27 to 2024-03-02 | a model-signal band above each block (a header, not an asset; not stored) |
| 2024-06-28 | some weeks print a hybrid regime, stored as `X WITH Y CHARACTERISTICS` |
| 2026-07-11 | redesign: two blocks renamed, about 82 rows instead of 69 |

Assets come and go and tickers change across the whole span (about two dozen weeks change
the list). A consumer following one asset through time should join on `ticker` and expect
renames; a ticker change is a new key.

**Not recorded:** the cell highlighting (the publisher's change markers, which the
optional `change` column could hold but the backfill leaves out), and a trailing asterisk
on a trade, which the slide's footnote defines as a manual override of the rule in hybrid
regimes. The trade is stored without it.

**How it was checked**, since nothing in a transcription is redundant enough to check
itself:

- every file passes the build's parse, and every block's row count equals the slide's;
- a cross-week check over all 158 weeks: every row that is missing for a single week and
  present on both sides, or present for a single week only, was looked up on its slide.
  All three such cases are printed that way by the publisher;
- a proofread by a second reader of 396 rows across 7 weeks spanning every era and layout,
  against the slide images: no discrepancy;
- a dry-run build of all 158 weeks into a scratch store, through the same command the box
  runs, with every guard on.

The build needs `--accept-drmo-universe` for a backfill: the asset-list guard compares each
week with the stored week before it, so once one genuine change is refused every later week
is compared with an older week and refused too. The cross-week check above does that guard's
job for history. The transcriptions themselves are subscriber data and live only in the
box's raw folder.

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

The box cannot see the Mac's deck folder by default (the Mac shares only the two stores),
so that folder is shared to the box read-only as a third share. The alternative, transcribing
on the Mac and handing the CSV to the box, was declined (2026-11-21): it needs a second
scheduled job and a new Mac-to-box hand-off, where the share needs neither.

A Claude Desktop scheduled task that runs on NUCBOX_M8 itself transcribes the slide, writes the CSV into the raw folder, and runs
`run-drmo.cmd`, which builds and then calls the two sync scripts. Exact instructions and the
proposed schedule: `docs/examples/windows/drmo-routine.md`. Delivery does not depend on that
wrapper's syncs alone: every later producer run mirrors both stores, the unconditional 08:10
catch-up included, so a Sunday evening build that somehow missed its own syncs is on the Mac by Monday 08:10 at
the latest. A consumer that wants the week in a Sunday page must build after the routine:
later than about 18:15 ET, or 20:00 ET to cover the retry.

### What the first live run taught (2026-11-22)

- **The task must run on the box, and how it is created decides that.** Two tasks created
  by asking Claude in a chat came out as cloud tasks: file access to the attached folder,
  no shell, no network share. They cannot run either `.cmd` and wrote nothing, correctly.
  The working task was made by hand in the Code tab's scheduled-task dialog with the
  repository cleared (a repository makes it a cloud task) and **Require this computer**
  turned on. Local or cloud is fixed at creation; an existing task cannot be converted.
- **That task runs without pausing for approval**, so the allow rules in the routine doc do
  not confine it. What confines it is the build's own refusals and the routine's
  instruction never to pass an override flag. The allow rules still apply to a session
  run by hand.
- **`\\tsclient\...` is not a share.** It is the Mac's disk passed through a Remote
  Desktop connection and exists only inside that connection, so a hand test over Remote
  Desktop can pass on a path a scheduled run will never see. The fetch script must name
  the SMB share.
- **An asset name is not unique within a report**: the publisher prints one name in two
  groups over two tickers. The first build refused a correct file on that; the row key is
  now (group, asset), see "The readers".
