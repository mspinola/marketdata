"""KMLM's daily book as a table: ``metadata/kmlm_positions.parquet``.

KMLM (KraneShares Mount Lucas Managed Futures Index Strategy ETF) tracks the MLM Index,
a published rules-based trend index: about 22 futures, each long or short by its price
against a long moving average, resized once a month, no equity index futures. Its issuer
publishes the whole book every day. ``dbmf-tracker`` collects those files on the Windows
producer and writes a flat export, ``data/kmlm/kmlm_positions.csv``: one row per date and
market with the contract count, the notional and the weight (notional / NAV, signed,
negative is short). This provider files that export into the market store, exactly as
providers/ahlt_positions.py does for AHLT, and for the same reason: the consumers (npf's
weekly watchlist) run on the Mac, and the store under ``metadata/`` already reaches both
replicas.

WHAT IS KEPT. ``date``, ``market`` (dbmf-tracker's market code, shared with the AHLT table
where both funds hold the market: CL, GC, EC, JY; cotdata's symbols for the grains and
softs only KMLM holds: ZC, ZS, ZW, SB; and descriptive names for the non-US bonds: BUND,
GILT, JGB, CGB), ``asset_class``, ``contracts`` (signed), ``exposure`` (dollars) and
``weight``. The export's ``ticker`` and ``description`` are dropped. Market codes are
dbmf-tracker's, not registry symbols. A reader maps them.

``contracts`` is the column AHLT's table does not have, and it is what separates a
decision from drift: between rebalances the index holds a fixed number of contracts, so
a weight that moves on an unchanged count moved with price alone.

A SNAPSHOT OF A GROWING HISTORY, rewritten whole each build, with the AHLT table's two
refusals (a shorter history, an older last date). The code is shared: this module only
names the table and its columns.

The data is the issuer's public daily holdings file, so nothing here is licensed.
"""
from __future__ import annotations

import pandas as pd

from . import ahlt_positions as _shared
from .ahlt_positions import RawError  # noqa: F401 (the refusal type, re-exported)

TABLE = "kmlm_positions"
REQUIRED = ("date", "market", "asset_class", "contracts", "exposure", "weight")
COLUMNS = REQUIRED


def table_path():
    return _shared.table_path(TABLE)


def parse_export(path) -> pd.DataFrame:
    """dbmf-tracker's ``kmlm_positions.csv`` as the stored table. Raises RawError."""
    return _shared.parse_export(path, required=REQUIRED, numeric=("contracts", "exposure", "weight"),
                                strings=("market", "asset_class"), fund="KMLM")


def build(path) -> dict:
    """Replace the table with the export at ``path``. A refused export writes nothing."""
    return _shared.build(path, table=TABLE, parse=parse_export, label="kmlm positions")


def read() -> pd.DataFrame:
    """The stored table, or an EMPTY frame when no build has run."""
    return _shared.read(TABLE, COLUMNS)
