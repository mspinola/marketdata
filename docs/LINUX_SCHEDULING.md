# Scheduling marketdata on Linux (cron)

For the **databento** producer — the cross-platform one, no Norgate and no Windows. The
Norgate producer runs on Windows and is scheduled from Task Scheduler; see
[cotdata's WINDOWS_SCHEDULING.md](https://github.com/mspinola/cotdata/blob/main/docs/WINDOWS_SCHEDULING.md),
which still covers that box because the COT job lives there too.

> **Moved here by ADR-0007.** This used to be `cotdata/docs/LINUX_SCHEDULING.md`, back when
> databento lived in cotdata. A server upgrading past that split has a `run-prices.sh`
> calling `cotdata-prices`, **an entry point that no longer exists** — the job fails every
> night until the wrapper is replaced with the one beside this file.

## Goal

Bars nightly, from a box that cannot run Norgate. Two properties make the schedule simple:

- **Resumable.** Stage 1 records what it fetched per (symbol, feed, schema) and restarts
  from `last_date + 1`, so a re-run pulls only new days and a mid-pull failure costs
  nothing but the time. Running before new data lands is a harmless no-op.
- **Fails loudly.** A run exits non-zero on a hard fetch error, not on "nothing new". A
  failed or missed run is picked up by the next one, so no explicit retry logic is needed.

## Wrapper script

Cron runs with a bare environment, so keep the config and the venv path in a wrapper.

> **Ready-made template:** copy [`examples/linux/run-prices.sh`](examples/linux/run-prices.sh)
> into your `<DIR>`, `chmod +x` it, and fill in the markers — keep it outside the repo so a
> `git pull` never clobbers your edited paths.

Overwrite the plain-text markers: `REPLACE_WITH_STORE_PATH` = your bar store,
`REPLACE_WITH_DATABENTO_KEY` = your Databento key, `REPLACE_WITH_VENV_PATH` = your venv.
They are plain markers rather than `<...>` placeholders because an unedited `<...>` would
be read as a shell redirection and the script would fail on its own comments.

```bash
#!/usr/bin/env bash
set -uo pipefail          # NOT -e: see below

export MARKETDATA_STORE=REPLACE_WITH_STORE_PATH
export DATABENTO_API_KEY=REPLACE_WITH_DATABENTO_KEY
BIN=REPLACE_WITH_VENV_PATH/bin/marketdata-update

rc=0
"$BIN" --ingest-databento || rc=$?     # Stage 1 (PAID)
"$BIN" --build-databento  || rc=$?     # Stage 2 (FREE)
exit "$rc"
```

**Why Stage 2 runs even when Stage 1 fails.** Stage 1 is the only step that costs money,
it writes incrementally, and it is resumable — so a network failure partway through still
leaves new raw data on disk. Refusing to build from it would discard something already
paid for, and the next run would have to pay again to reach the same place. The Stage 1
exit code is preserved and returned, so cron still sees the failure and still retries.
This is why the script does not use `set -e`.

**`MARKETDATA_STORE` is a different directory from `COTDATA_STORE`**, not an alias. Both
packages keep a `manifest.json` at their own root and each does a read-modify-write on it,
so one shared root means two producers dropping each other's entries. Sharing a *parent*
folder is fine and makes the pair easy to sync.

## Crontab entries

Add the job with `crontab -e`. Cron uses the **server's local** timezone, so convert the
ET time below if it is not on Eastern. `flock` stops a slow run from overlapping the next,
and the redirect keeps a log:

```cron
# Bars — nightly (Mon-Sat). GLBX settlements are disseminated the morning after the
# session, so an early-morning run captures the prior session's finalized settlement.
30 6 * * 1-6     flock -n /tmp/marketdata-bars.lock <DIR>/run-prices.sh >> <DIR>/bars.log 2>&1
```

Set `MAILTO=you@example.com` at the top of the crontab to have cron email any run that
writes to stderr or exits non-zero. Check coverage and freshness any time with
`marketdata-update --check`, which reads the manifest and no network.

## The cold-start backfill is not the nightly job

Run the first pull by hand, not from cron — it is the expensive one, and it wants a flag
the nightly job does not:

```bash
marketdata-update --ingest-databento --windowed-n1-stats
marketdata-update --build-databento
```

`--windowed-n1-stats` fetches the second contract's statistics only in a window around
each roll date. That series is read *only* at rolls, so restricting it is accuracy-neutral
and drops the largest avoidable download. Leave it off the nightly job, where the
incremental window is a few days anyway.

If a from-inception pull keeps timing out at the gateway, `--batch` submits a databento
batch job instead of streaming. It is not the better default — a single from-inception
continuous job can 504 — but it is the right tool for a bounded catch-up.

## Troubleshooting

### The job fails with "command not found"

Almost certainly a wrapper still calling `cotdata-prices`. That entry point was removed
with ADR-0007; the command is `marketdata-update` and the store variable is
`MARKETDATA_STORE`. Replace the wrapper with the template above.

### Cron job runs manually but not on schedule

Cron's environment is far barer than an interactive shell — no `PATH` beyond
`/usr/bin:/bin`, no `.bashrc`/`.profile`, no venv activation. That is why the wrapper calls
the venv's binary by full path and `export`s every variable rather than relying on a login
shell. If a script works by hand but not under cron, start by asking what your interactive
shell set up implicitly.

### The run "succeeded" but wrote nothing

Check the log for `no databento-capable symbols`. Eight registry markets are not on CME
Globex — the ICE softs, lumber and the dollar index — and carry `databento: null`, so a run
scoped to only those has nothing to do and says so. Otherwise `marketdata-update --check`
shows what the store actually holds.

### A symbol is stuck and never re-fetches

The resume ledger and the disk have diverged. An entry claiming rows whose parquet is
missing still carries a current `last_date`, so every run skips it as "already current" —
no error, no row, a permanent hole in a paid dataset. Repair it from local files:

```bash
marketdata-update --reconcile-databento
```

It fixes both directions: it records tables an interrupted run left unrecorded (so a
restart does not re-pay for them) and prunes ghosts (so a restart re-fetches them). It
never touches the API.

### Overlapping runs / stale lock

`flock -n` fails fast rather than blocking, so a slow run will not stack with the next —
the second invocation no-ops and exits. The lock releases when the holding process exits,
including on a crash, so a lock that blocks forever means a *hung* process rather than
something to delete. Check `ps aux | grep marketdata` before removing anything in `/tmp`.

### The store is filling up

`_raw/` is databento's append-only bronze store: producer-internal, the bulk of the bytes,
and useless to a consumer. Point `MARKETDATA_DATABENTO_RAW` at cheaper disk, and exclude
`_raw/` from any sync to replicas.
