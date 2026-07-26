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
    print(f"{'symbol':10s} {'rows':>7s}  {'first':10s} {'last':10s} "
          f"{'div':>5s} {'spl':>4s}  source")
    for name in sorted(bars):
        e = bars[name]
        print(f"{name:10s} {e.get('n_rows', 0):7d}  {str(e.get('first_date')):10s} "
              f"{str(e.get('last_date')):10s} {e.get('n_dividends', 0):5d} "
              f"{e.get('n_stock_splits', 0):4d}  {e.get('source')}")
    return 0


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="marketdata-update", description=__doc__)
    p.add_argument("--bars", action="store_true",
                   help="fetch bars for every registry symbol resolving to its vendor")
    p.add_argument("--symbols", nargs="+", metavar="SYM",
                   help="scope the fetch to these internal symbols")
    p.add_argument("--check", action="store_true",
                   help="read-only store summary from the manifest (no network)")
    args = p.parse_args(argv)

    if not (args.bars or args.check):
        p.error("nothing to do — pass --bars or --check")

    if args.check:
        return _check()

    from .providers import yfinance as yprov
    res = yprov.update(args.symbols)
    print(f"\n{res['kind']}: wrote={res['wrote']} failed={res['failed']}")
    return 0 if res["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
