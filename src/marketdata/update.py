"""marketdata-update — the producer CLI. The only thing here that touches the network."""
from __future__ import annotations

import argparse
import sys

from . import config, store


def _check() -> int:
    m = store.load_manifest()
    bars = m.get("bars", {})
    if not bars:
        print(f"marketdata store at {config.store_root()}: empty (no bars written yet)")
        return 1
    print(f"marketdata store at {config.store_root()}  schema v{m.get('schema_version')}")
    pit = store.is_point_in_time()
    print("point-in-time universe: "
          + {True: "yes",
             False: "NO (survivors only, cross-sectional ranking is biased)",
             None: "NOT RECORDED (run --stamp-flags)"}[pit])
    # Width from the data: a futures entry carries a domain, a vendor and a stored
    # tier ("futures/norgate/ES_backadj"), so a fixed 10 columns silently ragged
    # every row the moment the second domain landed.
    w = max([len("symbol")] + [len(n) for n in bars])
    print(f"{'symbol':{w}s} {'rows':>7s}  {'first':10s} {'last':10s} "
          f"{'div':>5s} {'spl':>4s}  source")
    for name in sorted(bars):
        e = bars[name]
        print(f"{name:{w}s} {e.get('n_rows', 0):7d}  {str(e.get('first_date')):10s} "
              f"{str(e.get('last_date')):10s} {e.get('n_dividends', 0):5d} "
              f"{e.get('n_stock_splits', 0):4d}  {e.get('source')}")
    return 0


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="marketdata-update", description=__doc__)
    p.add_argument("--bars", action="store_true",
                   help="fetch bars for every registry symbol resolving to its vendor")
    p.add_argument("--domain", choices=("equities", "futures"),
                   help="with --bars: fetch only this domain. Default: every domain "
                        "this machine can produce. Futures need Windows + the Norgate "
                        "Data Updater, so a Mac or Linux box should pass "
                        "--domain equities rather than fail its way there.")
    p.add_argument("--metadata", action="store_true",
                   help="fetch futures contract specifications (point value, tick "
                        "size, margin) into metadata/contract_specs.parquet. Scope "
                        "with --symbols, which UPSERTS so unlisted markets keep "
                        "their specs. Windows + Norgate only.")
    p.add_argument("--full", action="store_true",
                   help="with --bars on futures: rebuild the reconstructed-volume "
                        "columns over the whole history instead of the trailing "
                        "incremental window. For a reconstruction LOGIC change.")
    p.add_argument("--symbols", nargs="+", metavar="SYM",
                   help="scope the fetch to these internal symbols")
    p.add_argument("--check", action="store_true",
                   help="read-only store summary from the manifest (no network)")
    p.add_argument("--pin", metavar="PATH",
                   help="capture the store's current state to a snapshot JSON, so a "
                        "study can prove later which data it used. Scope with "
                        "--symbols. No network.")
    p.add_argument("--verify-pin", metavar="PATH",
                   help="check the store still matches a snapshot. Exit 1 on drift, "
                        "naming every field that moved. No network.")
    p.add_argument("--note", metavar="TEXT",
                   help="with --pin: a line recorded in the snapshot saying what it is for")
    p.add_argument("--stamp-flags", action="store_true",
                   help="write the store-level flags (schema version, point-in-time) "
                        "into the manifest without fetching anything. For a store "
                        "written before a flag existed. No network, and it does not "
                        "touch any symbol's updated_at, so pinned snapshots survive.")
    args = p.parse_args(argv)

    if not (args.bars or args.metadata or args.check or args.pin or args.verify_pin
            or args.stamp_flags):
        p.error("nothing to do. Pass --bars, --metadata, --check, --pin, "
                "--verify-pin or --stamp-flags")

    if args.stamp_flags:
        m = store.stamp_flags()
        print(f"stamped flags into {config.manifest_path()}: "
              f"schema_version={m['schema_version']}, "
              f"universe_is_point_in_time={m['universe_is_point_in_time']}")
        if not args.check:
            return 0

    if args.verify_pin:
        from .pin import read_snapshot, verify_snapshot
        snap = read_snapshot(args.verify_pin)
        ok, problems = verify_snapshot(snap)
        captured = snap.get("captured_at", "?")
        if ok:
            print(f"pin OK: the store still matches {args.verify_pin} "
                  f"({len(snap.get('symbols', {}))} symbols, captured {captured})")
            return 0
        print(f"PIN DRIFTED from {args.verify_pin} (captured {captured}):")
        for prob in problems:
            print(f"  {prob}")
        print("\nFigures quoted against this snapshot are no longer reproducible. Say "
              "which ones, rather than re-pinning and moving on.")
        return 1

    if args.pin:
        from .pin import build_snapshot, write_snapshot
        snap = build_snapshot(args.symbols, note=args.note)
        out = write_snapshot(snap, args.pin)
        print(f"pinned {len(snap['symbols'])} symbols -> {out}")
        for sym, e in sorted(snap["symbols"].items()):
            print(f"  {sym:<6} {e['n_rows']:>6} rows  {e['first_date']}..{e['last_date']}"
                  f"  {e['updated_at']}")
        return 0

    if args.check:
        return _check()

    results = []
    if args.metadata:
        from .providers import norgate as nprov
        results.append(nprov.update_metadata(args.symbols))

    if args.bars:
        if args.domain in (None, "equities"):
            from .providers import yfinance as yprov
            results.append(yprov.update(args.symbols))
        if args.domain in (None, "futures"):
            from .providers import norgate as nprov
            # An unscoped --bars run should produce everything this machine CAN
            # produce, not die on the half it cannot. Norgate needs Windows and a
            # running Data Updater, so on a Mac or Linux box the futures half is
            # absent by construction and that is a skip, not a failure. When the
            # domain was named explicitly the user asked for futures specifically,
            # so the real error is theirs to see.
            try:
                results.append(nprov.update(args.symbols, full=args.full))
            except RuntimeError as e:
                if args.domain == "futures":
                    print(f"\n{e}")
                    return 1
                print(f"\nskipping futures: {e}")

    for res in results:
        print(f"\n{res['kind']}: wrote={res['wrote']} failed={res['failed']}")
        for sym, err in res.get("errors", []):
            print(f"  {sym}: {err}")
    return 0 if all(r["ok"] for r in results) else 1


if __name__ == "__main__":
    sys.exit(main())
