"""Pull intraday (ohlcv-1m) bars from databento via the BATCH api.

Why a separate script rather than a flag on the nightly producer: the two-stage
`--ingest-databento` path is a daily-bar pipeline that a scheduled task depends on, and
intraday differs from it in every dimension that matters. Volume is three orders of
magnitude larger, the storage namespace is separate, and the right transport is different.

**The transport is the point.** `providers/databento.py::_fetch` uses
`timeseries.get_range`, which streams one symbol at a time over HTTP. That is why the last
large pull ran for days. Databento's batch api submits a job, prepares the files
server-side, and hands back compressed downloads. Same data, same cost, very different
wall clock.

**Cost is known before you spend.** `scripts/databento_intraday_cost.py` prices any request
exactly with `metadata.get_cost`, free. This script refuses to submit without either
`--yes` or an interactive confirmation, and prints the cost first either way.

Priced 2026-09-06 (GLBX.MDP3, continuous front-month, 2022-01-01 to 2026-09-01):

    30 CME symbols   ohlcv-1m  $144.07     ohlcv-1h  $7.51
                     ohlcv-1s  $1,767.64   trades    $2,832.72
    6 liquid symbols ohlcv-1m  $35.67

Usage:
    DATABENTO_API_KEY=... python scripts/databento_intraday_pull.py \
        --symbols ES NQ RTY GC CL 6E --start 2022-01-01 --end 2026-09-01 [--yes]
    # then, once the job is ready:
    python scripts/databento_intraday_pull.py --collect <job_id>
"""

from __future__ import annotations

import argparse
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from marketdata.providers.databento import (  # noqa: E402
    INTRADAY_SCHEMA,
    intraday_raw_path,
    raw_root,
)

DATASET = "GLBX.MDP3"
PILOT = ["ES", "NQ", "RTY", "GC", "CL", "6E"]


def job_dir(job_id: str) -> Path:
    return raw_root() / "intraday" / "_jobs" / job_id


def submit(client, syms, start, end, schema, assume_yes: bool) -> str:
    dbsyms = [f"{s}.n.0" for s in syms]
    cost = client.metadata.get_cost(dataset=DATASET, symbols=dbsyms, schema=schema,
                                    stype_in="continuous", start=start, end=end)
    n = client.metadata.get_record_count(dataset=DATASET, symbols=dbsyms, schema=schema,
                                         stype_in="continuous", start=start, end=end)
    print(f"dataset {DATASET}  schema {schema}  {start} to {end}")
    print(f"symbols {len(syms)}: {', '.join(syms)}")
    print(f"\n  COST ${cost:,.2f}   records {n:,}\n")
    if not assume_yes:
        if not sys.stdin.isatty():
            print("Refusing to submit a paid job without --yes in a non-interactive shell.")
            raise SystemExit(3)
        if input(f"Submit this paid job for ${cost:,.2f}? [y/N] ").strip().lower() != "y":
            print("aborted, nothing spent")
            raise SystemExit(0)

    job = client.batch.submit_job(
        dataset=DATASET, symbols=dbsyms, schema=schema, stype_in="continuous",
        start=start, end=end, encoding="csv", compression="zstd", split_duration="month")
    jid = job["id"]
    print(f"submitted: job {jid}  state {job.get('state')}")
    print(f"collect with:  python {sys.argv[0]} --collect {jid}")
    return jid


def collect(client, jid: str, poll: int, timeout: int) -> int:
    out = job_dir(jid)
    out.mkdir(parents=True, exist_ok=True)
    waited = 0
    while True:
        det = client.batch.get_job_details(jid)
        state = det.get("state")
        if state == "done":
            break
        if state in ("expired", "cancelled", "failed"):
            print(f"job {jid} ended in state {state}")
            return 1
        if waited >= timeout:
            print(f"job {jid} still '{state}' after {waited}s; re-run --collect later")
            return 2
        print(f"  job {jid}: {state}, waited {waited}s")
        time.sleep(poll)
        waited += poll
    print(f"job {jid} ready, downloading to {out}")
    client.batch.download(job_id=jid, output_dir=str(out))
    files = sorted(p for p in out.rglob("*") if p.is_file())
    total = sum(p.stat().st_size for p in files)
    print(f"downloaded {len(files)} files, {total/1e6:.1f} MB")
    print(f"\nraw namespace: {raw_root() / 'intraday'}")
    print(f"per-symbol parquet target: {intraday_raw_path('ES')}")
    print("Normalizing into per-symbol parquet is the next step and costs nothing "
          "(local files only).")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbols", nargs="*", default=PILOT)
    ap.add_argument("--start", default="2022-01-01")
    ap.add_argument("--end", default="2026-09-01")
    ap.add_argument("--schema", default=INTRADAY_SCHEMA)
    ap.add_argument("--yes", action="store_true", help="skip the spend confirmation")
    ap.add_argument("--collect", metavar="JOB_ID", default=None)
    ap.add_argument("--poll", type=int, default=20)
    ap.add_argument("--timeout", type=int, default=1800)
    args = ap.parse_args()

    if not os.environ.get("DATABENTO_API_KEY"):
        print("DATABENTO_API_KEY not set", file=sys.stderr)
        return 2
    import databento as db
    client = db.Historical()

    if args.collect:
        return collect(client, args.collect, args.poll, args.timeout)
    jid = submit(client, args.symbols, args.start, args.end, args.schema, args.yes)
    return collect(client, jid, args.poll, args.timeout)


if __name__ == "__main__":
    raise SystemExit(main())
