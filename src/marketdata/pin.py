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

# v2 — an entry is one STORED SERIES rather than one symbol, so a futures symbol
#      pins as `ES_backadj` AND `ES_unadj`. Pinning one of them would leave
#      `propadj` — derived from both — half covered, and a study quoting a
#      volatility figure would verify green against a store that had moved under
#      it. A v1 snapshot still verifies unchanged: its keys are plain symbols and
#      an absent `tier` field reads as "the domain's default stored series".
SNAPSHOT_VERSION = 2
#: The fields a snapshot compares. `updated_at` is the strictest and the one that
#: catches a re-fetch that happened to return identical data: the bytes may match,
#: but the run that produced them is not the run the study cited.
PINNED_FIELDS = ("n_rows", "first_date", "last_date", "source", "updated_at")


def _symbol_of(manifest_name: str) -> str:
    """``'futures/norgate/ES_backadj'`` -> ``'ES'``."""
    from .adjust import STORED_TIERS, stored_tiers_for

    parts = manifest_name.split("/")
    dom, leaf = (parts[0], parts[-1]) if len(parts) > 1 else ("", parts[-1])
    for tier in (stored_tiers_for(dom) if dom in STORED_TIERS else ()):
        if tier and leaf.endswith(f"_{tier}"):
            return leaf[: -len(tier) - 1]
    return leaf


def _stored_series(symbols: Optional[Iterable[str]]) -> List[tuple]:
    """``(entry_key, symbol, tier)`` for every stored series to pin.

    A caller naming symbols means the SYMBOL, so ``--symbols ES`` pins both of
    ES's tiers instead of requiring the caller to know the store's file naming.
    """
    from .adjust import stored_tiers_for
    from .registry import domain_for

    if symbols is None:
        symbols = {_symbol_of(n) for n in store.load_manifest().get("bars", {})}
    out = []
    for sym in sorted(symbols):
        for tier in stored_tiers_for(domain_for(sym)):
            out.append((f"{sym}_{tier}" if tier else sym, sym, tier))
    return out


def build_snapshot(symbols: Optional[Iterable[str]] = None, *,
                   note: Optional[str] = None) -> dict:
    """Capture the store's current state for ``symbols`` (default: everything)."""
    entries: Dict[str, dict] = {}
    missing: List[str] = []
    for key, sym, tier in _stored_series(symbols):
        try:
            p = provenance(sym, tier=tier)
            if p is None:
                raise KeyError(key)
        except Exception:
            missing.append(key)
            continue
        entries[key] = {
            "n_rows": p.n_rows, "first_date": p.first_date, "last_date": p.last_date,
            "source": p.source, "updated_at": p.updated_at, "domain": p.domain,
            "symbol": p.symbol, "tier": p.tier,
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

    for key, pinned in sorted(entries.items()):
        # A v1 snapshot has neither field, and its key IS the symbol.
        sym, tier = pinned.get("symbol", key), pinned.get("tier")
        try:
            p = provenance(sym, tier=tier)
            if p is None:
                raise KeyError(key)
        except Exception as e:
            problems.append(f"{key}: no longer in the store ({type(e).__name__})")
            continue
        now = {"n_rows": p.n_rows, "first_date": p.first_date,
               "last_date": p.last_date, "source": p.source,
               "updated_at": p.updated_at}
        for field in PINNED_FIELDS:
            want, got = pinned.get(field), now.get(field)
            if want is None:
                continue          # older snapshot did not pin this field
            if want != got:
                problems.append(f"{key}.{field}: pinned {want!r}, store has {got!r}")
    return (not problems), problems


def write_snapshot(snap: dict, path: Path) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(snap, indent=2, sort_keys=True) + "\n")
    return path


def read_snapshot(path: Path) -> dict:
    return json.loads(Path(path).read_text())
