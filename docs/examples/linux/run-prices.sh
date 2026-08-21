#!/usr/bin/env bash
# marketdata bar update wrapper for cron / systemd (Linux, databento producer).
#
# Copy this file next to your crontab dir and overwrite the markers below:
#   REPLACE_WITH_STORE_PATH    = your bar store        e.g. /srv/marketdata_store
#   REPLACE_WITH_DATABENTO_KEY = your Databento API key e.g. db-...
#   REPLACE_WITH_VENV_PATH     = your venv             e.g. /opt/marketdata/.venv
# (Plain-text markers, not angle-bracket placeholders: an unedited <...> would be
# read as a shell redirection and the script would fail on its own comments.)
#
# REPLACES cotdata's run-prices.sh. ADR-0007 moved every price producer here, so
# the old script's `cotdata-prices --ingest-databento` no longer resolves at all --
# that entry point is gone. Note MARKETDATA_STORE: a DIFFERENT directory from
# COTDATA_STORE, not an alias for it. COT is still cotdata's job and still has its
# own wrapper (run-cot.sh) in that repo.
#
# See docs/LINUX_SCHEDULING.md for the crontab and flock setup.
set -uo pipefail          # NOT -e: see the two-stage note below

export MARKETDATA_STORE=REPLACE_WITH_STORE_PATH
export DATABENTO_API_KEY=REPLACE_WITH_DATABENTO_KEY
# Optional. Defaults to _raw/databento under the store. Put it on cheap disk if the
# store is on something small or expensive -- the bronze store is the bulk of the
# bytes and never leaves this machine.
# export MARKETDATA_DATABENTO_RAW=/srv/databento_raw

BIN=REPLACE_WITH_VENV_PATH/bin/marketdata-update

# Stage 1 is the PAID pull and Stage 2 is a free local rebuild, so Stage 2 runs even
# when Stage 1 fails -- and that is deliberate rather than sloppy. Stage 1 is
# resumable and writes incrementally, so a network failure partway through still
# leaves new raw data on disk; refusing to build from it would discard something
# already paid for, and the next run would have to pay again to get back to the same
# place. The Stage 1 exit code is kept and returned at the end, so cron still sees a
# failure and still retries.
rc=0
"$BIN" --ingest-databento || rc=$?     # Stage 1 (PAID): raw .n.0/.n.1 -> raw store
"$BIN" --build-databento  || rc=$?     # Stage 2 (FREE): backadj+unadj bars

exit "$rc"
