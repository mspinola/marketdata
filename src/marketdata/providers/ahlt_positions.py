"""AHLT's daily book as a table: ``metadata/ahlt_positions.parquet``.

AHLT (American Beacon AHL Trend ETF) is Man AHL's trend program in an ETF, and its
issuer publishes the whole book every day. The sibling project ``dbmf-tracker`` collects
those files on the Windows producer, parses them into a sqlite store, and already writes
a flat export beside it: ``data/ahlt/ahlt_positions.csv``, one row per date and market
with the position's notional and its weight (notional / NAV, signed, negative is short).
This provider files that export into the market store, so it rides the same mirror to
the Mac and the server as every other table here.

WHY HERE. The consumers (npf's weekly watchlist) run on the Mac, the sqlite store is on
the Windows box and is not mirrored, and the store under ``metadata/`` already reaches
both replicas. The alternative in use before this (2026-11-22) was reading the dataset
embedded in the public monitor page, which tied a build to a page template.

WHAT IS KEPT. ``date``, ``market`` (dbmf-tracker's market code: ES, TY, EC, GC, and
descriptive names where only AHLT trades it, such as DAX and BUND), ``asset_class``,
``kind`` (future, forward, balance), ``exposure`` (dollars) and ``weight``. The export's
``description`` (the contract's long name) is dropped: nothing reads it and it is most
of the file. Market codes are dbmf-tracker's, not registry symbols. A reader maps them.

A SNAPSHOT OF A GROWING HISTORY, rewritten whole each build, because a parsing fix in
dbmf-tracker restates past rows. Two refusals protect the stored table: a file with
fewer dates than the table already holds, or one whose newest date is older, is not
written (a truncated or stale export must not replace a good table).

The data is the issuer's public daily holdings file, so nothing here is licensed.
"""
from __future__ import annotations

from pathlib import Path

import pandas as pd

from .. import config, store

NAME = "dbmf-tracker"
TABLE = "ahlt_positions"
REQUIRED = ("date", "market", "asset_class", "kind", "exposure", "weight")
COLUMNS = REQUIRED


class RawError(ValueError):
    """An export the build refuses. The message names the file."""


def table_path() -> Path:
    return config.metadata_dir() / f"{TABLE}.parquet"


def _empty() -> pd.DataFrame:
    return pd.DataFrame(columns=list(COLUMNS))


def parse_export(path) -> pd.DataFrame:
    """dbmf-tracker's ``ahlt_positions.csv`` as the stored table. Raises RawError."""
    p = Path(path)
    if not p.is_file():
        raise RawError(f"no AHLT export at {p}")
    try:
        df = pd.read_csv(p)
    except Exception as e:  # noqa: BLE001 (any unreadable file is one refusal)
        raise RawError(f"{p.name}: not readable as CSV ({e})") from e
    missing = [c for c in REQUIRED if c not in df.columns]
    if missing:
        raise RawError(f"{p.name}: missing columns {missing}")
    if df.empty:
        raise RawError(f"{p.name}: no rows")
    df = df[list(COLUMNS)].copy()
    try:
        df["date"] = pd.to_datetime(df["date"], format="%Y-%m-%d")
    except Exception as e:  # noqa: BLE001
        raise RawError(f"{p.name}: a date is not YYYY-MM-DD ({e})") from e
    for c in ("exposure", "weight"):
        df[c] = pd.to_numeric(df[c], errors="coerce")
    bad = df[df["weight"].isna() | df["market"].isna()]
    if len(bad):
        raise RawError(f"{p.name}: {len(bad)} rows with no market or no numeric weight, "
                       f"first on {bad['date'].iloc[0]:%Y-%m-%d}")
    dup = df.duplicated(["date", "market"])
    if dup.any():
        d = df[dup].iloc[0]
        raise RawError(f"{p.name}: more than one row for {d['market']} on {d['date']:%Y-%m-%d}")
    for c in ("market", "asset_class", "kind"):
        df[c] = df[c].astype("string")
    return df.sort_values(["date", "market"]).reset_index(drop=True)


def build(path) -> dict:
    """Replace the table with the export at ``path``. Returns the CLI's result dict. A
    refused export leaves the table on disk as it was."""
    def refused(msg: str) -> dict:
        print(f"ahlt positions: REFUSED -- {msg}")
        return {"kind": TABLE, "ok": False, "wrote": 0, "failed": 1, "errors": [(TABLE, msg)]}

    try:
        df = parse_export(path)
    except RawError as e:
        return refused(str(e))
    old = read()
    if len(old):
        n_new, n_old = df["date"].nunique(), old["date"].nunique()
        if n_new < n_old:
            return refused(f"{Path(path).name} has {n_new} dates, the table holds {n_old}: "
                           "a shorter history is not written over a longer one")
        if df["date"].max() < old["date"].max():
            return refused(f"{Path(path).name} ends {df['date'].max():%Y-%m-%d}, the table "
                           f"ends {old['date'].max():%Y-%m-%d}: an older export is not written")
    store._atomic_write_parquet(df, table_path())
    store._touch_manifest("metadata", TABLE, df.set_index("date"), NAME)
    print(f"ahlt positions: {len(df)} rows, {df['date'].nunique()} dates, "
          f"{df['date'].min():%Y-%m-%d} to {df['date'].max():%Y-%m-%d}, "
          f"{df['market'].nunique()} markets -> {table_path()}")
    return {"kind": TABLE, "ok": True, "wrote": len(df), "failed": 0, "errors": []}


def read() -> pd.DataFrame:
    """The stored table, or an EMPTY frame when no build has run."""
    p = table_path()
    return pd.read_parquet(p) if p.exists() else _empty()
