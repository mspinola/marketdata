"""Effective-dated contract multipliers: what a contract was worth at a past date.

`store.read_metadata` answers "what is this contract today", one row per symbol with no
effective date. That is the right shape for its question and the wrong input for any
consumer that multiplies a HISTORICAL position or trade by it. Where an exchange
re-denominated a contract, every date before the change needs the old multiplier, and
today's table cannot express that.

This module is the effective-dated companion, backed by the packaged
``contract_regimes.yaml``. Read that file first: it carries the schema, the two
invariants, and the reason each regime is believed, next to the regime itself.

**This is additive.** ``read_metadata`` and the ``contract_specs`` table are untouched
and still return exactly one current row per symbol, so nothing that reads them changes
behaviour. That was the design constraint rather than an accident: consumers index specs
by ``Symbol`` (npf's ``validation/costs.py`` does ``specs.loc[sym]``), so giving a symbol
a second row there would turn a Series into a DataFrame and yield a wrong answer instead
of an error.

The undeclared case is the one that decides whether callers have to branch, and they do
not. A symbol with no entry here has had one regime for its whole stored history as far
as anyone has established, so `point_value_asof` falls back to the current
``contract_specs`` value for every date. A caller therefore writes the same code for all
47 markets and only the two declared ones behave differently.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Dict, List, Optional

import numpy as np
import pandas as pd
import yaml

#: One row per regime. `Valid_From` is NaT on a first regime declared unbounded.
REGIME_COLUMNS = ("Symbol", "Valid_From", "Point_Value", "Tick_Value", "Name", "Source")

_REQUIRED_KEYS = ("valid_from", "point_value", "name", "source")


class RegimeError(ValueError):
    """The regime file cannot be trusted to answer a multiplier question."""


def _regimes_path(path=None) -> Path:
    """Explicit arg, else $MARKETDATA_CONTRACT_REGIMES, else the packaged YAML.

    Same three-step resolution as `registry.load_registry`, so a deployment that
    overrides one can override the other the same way.
    """
    return Path(path or os.environ.get(
        "MARKETDATA_CONTRACT_REGIMES", Path(__file__).parent / "contract_regimes.yaml"))


def _parse_symbol(internal: str, rows) -> List[dict]:
    if not isinstance(rows, list) or not rows:
        raise RegimeError(
            f"contract regimes: symbol '{internal}' must map to a non-empty list of "
            f"regimes, got {type(rows).__name__}.")

    out = []
    for i, row in enumerate(rows):
        if not isinstance(row, dict):
            raise RegimeError(
                f"contract regimes: {internal} regime {i} must be a mapping, "
                f"got {type(row).__name__}.")
        missing = [k for k in _REQUIRED_KEYS if k not in row]
        if missing:
            raise RegimeError(
                f"contract regimes: {internal} regime {i} is missing {missing}. "
                f"`source` is required on every row because a multiplier with no "
                f"citation is indistinguishable from a typo.")

        # Only the first regime may be unbounded. A null valid_from on a later row would
        # silently reorder the series and back-date the wrong multiplier.
        if row["valid_from"] is None and i != 0:
            raise RegimeError(
                f"contract regimes: {internal} regime {i} has a null valid_from, which "
                f"is allowed on the first regime only (it means 'all history before the "
                f"next one'). Give this row the date the regime took effect.")

        point = row["point_value"]
        if not isinstance(point, (int, float)) or not point > 0:
            raise RegimeError(
                f"contract regimes: {internal} regime {i} has point_value {point!r}; "
                f"expected a positive number.")
        tick = row.get("tick_value")
        if tick is not None and (not isinstance(tick, (int, float)) or not tick > 0):
            raise RegimeError(
                f"contract regimes: {internal} regime {i} has tick_value {tick!r}; "
                f"expected a positive number or null (null means unverified).")

        out.append({
            "Symbol": internal,
            "Valid_From": pd.Timestamp(row["valid_from"]) if row["valid_from"] else pd.NaT,
            "Point_Value": float(point),
            "Tick_Value": float(tick) if tick is not None else float("nan"),
            "Name": str(row["name"]),
            "Source": str(row["source"]),
        })

    dated = [r["Valid_From"] for r in out if pd.notna(r["Valid_From"])]
    if dated != sorted(dated):
        raise RegimeError(
            f"contract regimes: {internal} regimes are not in valid_from order. "
            f"They are read in file order and the order is the meaning, so sort them.")
    if len(set(dated)) != len(dated):
        raise RegimeError(
            f"contract regimes: {internal} has two regimes with the same valid_from. "
            f"A date cannot resolve to two multipliers.")
    return out


def load_regimes(path=None) -> pd.DataFrame:
    """Parse the regime YAML into a frame of `REGIME_COLUMNS`, validating as it goes.

    A missing file is an EMPTY table rather than an error: no declared regimes means
    every symbol uses its current spec, which is what the package did before this
    existed. A malformed file is an error, because that is a claim that cannot be read
    rather than an absence of claims.
    """
    p = _regimes_path(path)
    if not p.exists():
        return pd.DataFrame(columns=list(REGIME_COLUMNS))
    try:
        with open(p, "r") as f:
            data = yaml.safe_load(f)
    except yaml.YAMLError as e:
        raise RegimeError(f"contract regimes YAML is malformed ({p}): {e}") from e

    if data is None:
        return pd.DataFrame(columns=list(REGIME_COLUMNS))
    if not isinstance(data, dict):
        raise RegimeError(
            f"contract regimes YAML must be a mapping of symbol -> regimes ({p}); "
            f"got {type(data).__name__}.")

    rows: List[dict] = []
    for internal, regimes in data.items():
        rows.extend(_parse_symbol(str(internal), regimes))
    return pd.DataFrame(rows, columns=list(REGIME_COLUMNS))


def read_contract_regimes(symbol: Optional[str] = None) -> pd.DataFrame:
    """Every declared regime, or one symbol's, in effective order.

    An undeclared symbol returns an empty frame, which is a positive statement: nothing
    here re-denominated it, so its current spec applies to its whole history.
    """
    table = load_regimes()
    if symbol is None:
        return table
    return table[table["Symbol"] == symbol].reset_index(drop=True)


def declared_symbols() -> Dict[str, int]:
    """Symbol -> regime count, for callers that want to report what is covered."""
    table = load_regimes()
    if table.empty:
        return {}
    return {str(s): int(n) for s, n in table["Symbol"].value_counts().items()}


def _current_spec(symbol: str, field: str) -> float:
    """Today's value from contract_specs, for a symbol with no declared regimes."""
    from . import store
    specs = store.read_metadata()
    if specs is None or specs.empty or "Symbol" not in specs.columns:
        return float("nan")
    row = specs[specs["Symbol"].astype(str) == symbol]
    if row.empty or field not in row.columns:
        return float("nan")
    value = pd.to_numeric(row[field], errors="coerce").iloc[0]
    return float(value) if pd.notna(value) and value > 0 else float("nan")


def _normalize(dates) -> pd.DatetimeIndex:
    """Whatever the caller passed, as naive ns timestamps in the order given.

    Timezone is dropped rather than rejected, matching how the rest of the package
    normalizes a bar index. `valid_from` is an exchange-local calendar date, so a
    tz-aware instant has more precision than the question can use anyway.
    """
    index = pd.DatetimeIndex(pd.to_datetime(dates))
    if index.tz is not None:
        index = index.tz_localize(None)
    return index


def _asof(symbol: str, dates, column: str, spec_field: str) -> pd.Series:
    index = _normalize(dates)
    table = read_contract_regimes(symbol)

    if table.empty:
        return pd.Series(_current_spec(symbol, spec_field), index=index, dtype="float64")

    # searchsorted rather than merge_asof, and the reason is a caller we have: a trade
    # log has many rows on one date, and merge_asof's result has to be reindexed back
    # onto the input, which raises on a duplicate label. This preserves input order,
    # tolerates duplicates and needs no sort of the caller's dates.
    #
    # An unbounded first regime is the minimum representable timestamp, so no date can
    # land before it. A BOUNDED first regime leaves earlier dates at position -1, which
    # is where the NaN comes from: the multiplier was never established there.
    values = table[column].to_numpy(dtype="float64")
    starts = table["Valid_From"].fillna(pd.Timestamp.min).to_numpy(dtype="datetime64[ns]")

    pos = np.searchsorted(starts.astype("int64"),
                          index.to_numpy(dtype="datetime64[ns]").astype("int64"),
                          side="right") - 1
    out = np.where(pos >= 0, values[pos.clip(min=0)], np.nan)
    return pd.Series(out, index=index, dtype="float64")


def point_value_asof(symbol: str, dates) -> pd.Series:
    """USD per 1.00 of quoted price, for each date, indexed by `dates`.

    A declared symbol resolves against its regimes; an undeclared one gets its current
    `contract_specs` value for every date, so a caller needs no branch. Dates before a
    BOUNDED first regime return NaN: where the multiplier was never established,
    returning nothing beats returning a guess, because a gap is visible downstream and a
    guess is not.
    """
    return _asof(symbol, dates, "Point_Value", "Point Value")


def tick_value_asof(symbol: str, dates) -> pd.Series:
    """USD per minimum tick, for each date. NaN where a regime left it unverified."""
    return _asof(symbol, dates, "Tick_Value", "Tick Value")
