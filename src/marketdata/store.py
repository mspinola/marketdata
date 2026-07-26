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
    m["schema_version"] = config.SCHEMA_VERSION
    _write_manifest(m)


def schema_version() -> int:
    return int(load_manifest().get("schema_version", 0))


def require_schema(minimum: int) -> None:
    v = schema_version()
    if v < minimum:
        raise RuntimeError(
            f"marketdata store is schema v{v}, this consumer needs >= v{minimum}. "
            f"Re-run the producer (marketdata-update)."
        )
