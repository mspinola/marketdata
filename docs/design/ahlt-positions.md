# AHLT positions table

`metadata/ahlt_positions.parquet` (provider `providers/ahlt_positions.py`, build
`marketdata-update --build-ahlt CSV`, reader `marketdata.ahlt_positions()`). Added
2026-11-22. Consumer: npf's weekly setup watchlist, which shows a trend follower's
position beside each positioning setup.

## What it is

AHLT (American Beacon AHL Trend ETF) publishes Man AHL's trend book daily. The sibling
project `dbmf-tracker` collects and parses those files on the Windows producer and writes
a flat export, `data/ahlt/ahlt_positions.csv`. This table is that export, minus the long
contract description, filed where the store mirror delivers it to the Mac and the server.

| Column | Meaning |
|---|---|
| `date` | the close the book describes |
| `market` | dbmf-tracker's market code (ES, TY, EC, GC; DAX, BUND where only AHLT trades it). Not a registry symbol |
| `asset_class` | equity, rates, fx, commodity |
| `kind` | future, forward, balance |
| `exposure` | signed notional, dollars |
| `weight` | exposure / NAV, signed, negative is short |

History starts 2023-08-30 (the fund's launch). The source is the issuer's public daily
holdings file, so nothing in it is licensed.

## Why a table here

The consumer runs on the Mac. dbmf-tracker's sqlite store is on the Windows box and is
not mirrored. Before this the watchlist read the dataset embedded in the public monitor
page (ahlt.bluemagicai.com), which tied a build to a page template. `metadata/` already
reaches both replicas, as `contract_specs`, `econ_calendar` and `drmo` do.

## Rules

- Rewritten whole each build. A parsing fix in dbmf-tracker restates past rows, so an
  append would keep the old ones.
- Refused, with the table left as it was: a missing or malformed file, a duplicate
  (date, market), a file with fewer dates than the table holds, or one whose newest date
  is older than the table's.
- No network. The provider reads one local file.
- Market codes are mapped by the reader, not here. A registry symbol column would put
  dbmf-tracker's naming decisions into this package.

## On the producer

One line in `scheduler\run-equities.cmd` (weekdays 17:30), after the AAII step and
before the syncs, so the table leaves with that run's mirror. A failure must not stop
the equities delivery:

```bat
"%MDEXE%" --build-ahlt "C:\Users\matt\code\dbmf-tracker-windows-bundle\data\ahlt\ahlt_positions.csv"
set "AHLT_RC=%ERRORLEVEL%"
if not "%AHLT_RC%"=="0" echo ahlt: positions build failed with code %AHLT_RC% -- equities delivery continues
```

dbmf-tracker refreshes the CSV on its 08:30 and 10:30 runs, so the 17:30 build files the
same day's export. AHLT posts each day's book the next business morning: the table on a
Saturday ends on Thursday's close.

Reproducer: `python -c "import marketdata as m; d=m.ahlt_positions(); print(d.date.max(), d.date.nunique(), d.market.nunique())"`

## The KMLM table beside it (2026-11-24)

`metadata/kmlm_positions.parquet` (provider `providers/kmlm_positions.py`, build
`marketdata-update --build-kmlm CSV`, reader `marketdata.kmlm_positions()`) is the same
thing for KMLM (KraneShares Mount Lucas Managed Futures Index Strategy ETF), the MLM
trend index in an ETF. Same consumer, same rules, same refusals: the provider shares
this one's code and only names its table and columns.

It differs in two ways. It has no `kind` column (every position is a future) and it has
`contracts`, signed. The index holds a fixed contract count between its monthly
rebalances, so a weight that moves on an unchanged count moved with price alone, and a
changed count is a trade. History starts 2024-01-02, the oldest dated file the issuer
serves. KMLM holds the grains, softs, livestock and energy products AHLT does not (ZC,
ZS, ZW, SB, LC, RB, HO) and no equity index futures.

On the producer, a second line after the AHLT one, with its own exit code for the same
reason (a checkout that predates the flag fails argparse, and neither may stop the
equities delivery):

```bat
"%MDEXE%" --build-kmlm "C:\Users\matt\code\dbmf-tracker-windows-bundle\data\kmlm\kmlm_positions.csv"
set "KMLM_RC=%ERRORLEVEL%"
if not "%KMLM_RC%"=="0" echo kmlm: positions build failed with code %KMLM_RC% -- equities delivery continues
```

dbmf-tracker refreshes that CSV on its 08:30 and 18:00 runs, so the 17:30 build files the
morning's export.

Reproducer: `python -c "import marketdata as m; d=m.kmlm_positions(); print(d.date.max(), d.date.nunique(), d.market.nunique())"`
