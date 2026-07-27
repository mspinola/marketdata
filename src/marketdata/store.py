"""Canonical store I/O: atomic Parquet writes + a manifest.

The store is the contract between the producer (writes, needs network) and every
consumer (reads only, never touches the network). Registry-free by design: keyed
on a plain symbol string, so a one-off symbol can be written without amending the
registry. The registry governs what the PRODUCER fetches, not what the store can
hold.
"""
from __future__ import annotations

import datetime as dt
import json
import os
import tempfile
from pathlib import Path

import pandas as pd

from . import config


def _atomic_write_parquet(df: pd.DataFrame, path: Path) -> None:
    """Temp file in the same dir, then os.replace, so a consumer reading (or a
    sync client uploading) never sees a half-written parquet."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), suffix=".tmp")
    os.close(fd)
    try:
        df.to_parquet(tmp)
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.remove(tmp)


def write_bars(symbol: str, df: pd.DataFrame, domain: str, source: str) -> None:
    _atomic_write_parquet(df, config.bars_dir(domain, source) / f"{symbol}.parquet")
    _touch_manifest("bars", f"{domain}/{source}/{symbol}", df, source)


def read_bars(symbol: str, domain: str, source: str) -> pd.DataFrame:
    p = config.bars_dir(domain, source) / f"{symbol}.parquet"
    return pd.read_parquet(p) if p.exists() else pd.DataFrame()


def has_bars(symbol: str, domain: str, source: str) -> bool:
    return (config.bars_dir(domain, source) / f"{symbol}.parquet").exists()


def sources_for(symbol: str, domain: str) -> list:
    """Every vendor holding a series for `symbol` in `domain`, sorted. Used to
    turn a missing-file read into a message naming what IS present."""
    root = config.store_root() / "bars" / domain
    if not root.exists():
        return []
    return sorted(d.name for d in root.iterdir()
                  if d.is_dir() and (d / f"{symbol}.parquet").exists())


# ── Manifest ──────────────────────────────────────────────────────────────
def load_manifest() -> dict:
    p = config.manifest_path()
    if not p.exists():
        return {}
    try:
        return json.loads(p.read_text())
    except json.JSONDecodeError:
        return {}


def _write_manifest(m: dict) -> None:
    tmp = config.manifest_path().with_suffix(".json.tmp")
    tmp.parent.mkdir(parents=True, exist_ok=True)
    tmp.write_text(json.dumps(m, indent=2, sort_keys=True))
    os.replace(tmp, config.manifest_path())


def _touch_manifest(kind: str, name: str, df: pd.DataFrame, source: str) -> None:
    """Record provenance for one entry. `n_actions` is carried because a silent
    drop in dividend rows is the failure mode that would quietly corrupt every
    derived total-return series."""
    m = load_manifest()
    last = first = None
    if len(df) and isinstance(df.index, pd.DatetimeIndex):
        first, last = str(df.index.min().date()), str(df.index.max().date())
    entry = {
        "first_date": first,
        "last_date": last,
        "n_rows": int(len(df)),
        "source": source,
        "updated_at": dt.datetime.now(dt.timezone.utc)
                        .replace(microsecond=0).isoformat().replace("+00:00", "Z"),
    }
    for col in ("Dividends", "Stock Splits", "Capital Gains"):
        if col in df.columns:
            key = "n_" + col.lower().replace(" ", "_")
            entry[key] = int((df[col].fillna(0) > 0).sum())
    m.setdefault(kind, {})[name] = entry
    _stamp_flags(m)
    _write_manifest(m)


def _stamp_flags(m: dict) -> None:
    """Store-level facts, refreshed on every write. Mutates `m` in place."""
    m["schema_version"] = config.SCHEMA_VERSION
    m["universe_is_point_in_time"] = config.UNIVERSE_IS_POINT_IN_TIME


def stamp_flags() -> dict:
    """Write the store-level flags without touching any bars.

    Needed because a store written before a flag existed will never grow it
    otherwise: the flags ride along with `_touch_manifest`, and re-fetching every
    symbol just to record a constant would rewrite every `updated_at` and break
    any pinned snapshot for no reason at all.
    """
    m = load_manifest()
    _stamp_flags(m)
    _write_manifest(m)
    return m


def schema_version() -> int:
    return int(load_manifest().get("schema_version", 0))


def require_schema(minimum: int) -> None:
    v = schema_version()
    if v < minimum:
        raise RuntimeError(
            f"marketdata store is schema v{v}, this consumer needs >= v{minimum}. "
            f"Re-run the producer (marketdata-update)."
        )


def is_point_in_time():
    """``True`` / ``False`` / ``None`` when the store never recorded it.

    The three-way answer is the whole point. A store predating the flag has no
    opinion, and collapsing that to ``False`` would be luck rather than knowledge:
    it happens to be the right answer for today's yfinance-only stores and would
    be the wrong one the moment a vendor with delisted coverage writes here. A
    caller must be able to tell "we checked, and no" from "nobody ever said".
    """
    v = load_manifest().get("universe_is_point_in_time")
    return v if isinstance(v, bool) else None


def require_point_in_time() -> None:
    """Refuse unless the store's universe is point-in-time.

    For the studies that actually need it: anything that ranks or selects ACROSS
    symbols. A flag sitting in a JSON file is passive, and a survivorship-inflated
    cross-sectional result looks exactly like a good one, so give that study a
    single line it can assert instead of a fact it has to remember.
    """
    v = is_point_in_time()
    if v is True:
        return
    if v is None:
        raise RuntimeError(
            "marketdata store does not record whether its universe is "
            "point-in-time, so it cannot be assumed to be. Run "
            "`marketdata-update --stamp-flags` against it, then read the answer."
        )
    raise RuntimeError(
        "marketdata store's universe is NOT point-in-time: it holds only "
        "currently-listed symbols, because yfinance cannot serve delisted "
        "securities or index membership as of a past date. Any result that ranks "
        "or selects across symbols on this store carries survivorship bias. "
        "Per-symbol studies are unaffected and need not call this."
    )
