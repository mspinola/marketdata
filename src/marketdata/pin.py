"""Pin a store's state, and prove later that it has not moved.

A study that quotes numbers is only reproducible if the data behind them is
identifiable. Recording "I used the marketdata store" is not that: `--bars` rewrites
every symbol's ``updated_at``, and Yahoo restates adjusted history whenever a
dividend lands, so the same command against the same path can produce different
figures on different days.

The convention so far was a table pasted into a pre-registration and a sentence
saying a re-fetch invalidates it. That is a claim nobody can check without doing it
by hand, and a claim nobody checks is a claim that quietly stops being true.

    marketdata-update --pin snapshot.json                 # capture
    marketdata-update --verify-pin snapshot.json          # prove, exit 1 on drift

`verify` compares row counts, date spans, source and ``updated_at`` per symbol. It
reads the manifest only, so it is fast and never opens a parquet.

**A snapshot is evidence, not configuration.** Commit it next to the study that
depends on it. If verification fails, the honest response is to say which figures
are now unreproducible, not to re-pin and move on.
"""
from __future__ import annotations

import datetime as _dt
import json
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Tuple

from . import store
from .provenance import provenance

SNAPSHOT_VERSION = 1
#: The fields a snapshot compares. `updated_at` is the strictest and the one that
#: catches a re-fetch that happened to return identical data: the bytes may match,
#: but the run that produced them is not the run the study cited.
PINNED_FIELDS = ("n_rows", "first_date", "last_date", "source", "updated_at")


def build_snapshot(symbols: Optional[Iterable[str]] = None, *,
                   note: Optional[str] = None) -> dict:
    """Capture the store's current state for ``symbols`` (default: everything)."""
    bars = store.load_manifest().get("bars", {})
    if symbols is None:
        symbols = sorted({name.split("/")[-1] for name in bars})
    entries: Dict[str, dict] = {}
    missing: List[str] = []
    for sym in sorted(symbols):
        try:
            p = provenance(sym)
        except Exception:
            missing.append(sym)
            continue
        entries[sym] = {
            "n_rows": p.n_rows, "first_date": p.first_date, "last_date": p.last_date,
            "source": p.source, "updated_at": p.updated_at, "domain": p.domain,
        }
    if missing:
        raise KeyError(f"not in the store, cannot pin: {', '.join(missing)}")
    return {
        "snapshot_version": SNAPSHOT_VERSION,
        "captured_at": _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds"),
        "store": str(store.config.store_root()) if hasattr(store, "config") else None,
        "note": note,
        "symbols": entries,
    }


def verify_snapshot(snap: dict) -> Tuple[bool, List[str]]:
    """``(ok, problems)``. Compares every pinned field for every pinned symbol.

    A symbol missing from the store is a failure, not a skip: the study cited it.
    """
    problems: List[str] = []
    entries = (snap or {}).get("symbols") or {}
    if not entries:
        return False, ["snapshot contains no symbols"]

    for sym, pinned in sorted(entries.items()):
        try:
            p = provenance(sym)
        except Exception as e:
            problems.append(f"{sym}: no longer in the store ({type(e).__name__})")
            continue
        now = {"n_rows": p.n_rows, "first_date": p.first_date,
               "last_date": p.last_date, "source": p.source,
               "updated_at": p.updated_at}
        for field in PINNED_FIELDS:
            want, got = pinned.get(field), now.get(field)
            if want is None:
                continue          # older snapshot did not pin this field
            if want != got:
                problems.append(f"{sym}.{field}: pinned {want!r}, store has {got!r}")
    return (not problems), problems


def write_snapshot(snap: dict, path: Path) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(snap, indent=2, sort_keys=True) + "\n")
    return path


def read_snapshot(path: Path) -> dict:
    return json.loads(Path(path).read_text())
