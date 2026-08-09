"""The symbol registry: internal symbol -> vendor tickers + capability.

Same design as cotdata's registry, minus the COT axis (no cftc_code, because
equities and ETFs have no COT report — that requirement is precisely why this
does not live in cotdata).

Which vendor prices a symbol is a DEPLOYMENT choice, not a fixed identity fact.
It resolves at runtime from three inputs: the deployment default
($MARKETDATA_PRICE_SOURCE, 'yfinance' if unset), per-symbol capability (the yahoo /
norgate / databento mappings, null where a vendor has no series), and an optional
per-symbol override. One provider owns a symbol end to end; a series is never blended.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional

import yaml

PRICE_SOURCES = ("yfinance", "norgate", "databento")

# Instrument domains. The domain sets the adjustment axis (see adjust.DOMAIN_TIERS)
# and is a path component in the store, so a futures ES and an equity ES could
# coexist without either pretending to be the other.
DOMAINS = ("equities", "futures")
DEFAULT_DOMAIN = "equities"


@dataclass(frozen=True)
class Symbol:
    internal: str
    asset_class: str
    domain: str = DEFAULT_DOMAIN
    yahoo: Optional[str] = None
    norgate: Optional[str] = None
    # Databento GLBX.MDP3 continuous root — the producer queries "<databento>.n.0".
    # FUTURES ONLY: it defaults to the internal symbol there and is always None on
    # equities, because GLBX carries no equities and a defaulted value would let
    # `resolve_source` route SPY to a vendor that cannot serve it. cotdata's registry
    # defaults it unconditionally, which is safe only because that registry has one
    # domain. Explicit `databento: null` marks a futures market GLBX does not carry
    # (ICE softs, lumber, the dollar index).
    databento: Optional[str] = None
    price_source: Optional[str] = None
    inception: Optional[str] = None
    note: Optional[str] = None


# Asset class -> domain, so the YAML does not repeat `domain:` on every symbol.
# A per-symbol `domain:` still wins.
#
# The futures sector names are cotdata's, so a reader moving between the two
# registries meets the same vocabulary — with one deliberate exception. cotdata
# files the index futures under "Equities", which here would sit next to genuine
# equity ETFs in the other domain and read as a contradiction, so it is spelled
# "Equity Index Futures". Nothing consumes the class string across the seam:
# crowdmon reads bars, the manifest and contract specs, and every COT-side
# asset-class read still comes from cotdata's own registry.
_CLASS_DOMAIN = {
    "Futures": "futures",
    "Equity Index Futures": "futures",
    "Metals": "futures",
    "Energies": "futures",
    "Grains": "futures",
    "Dairy": "futures",
    "Currencies": "futures",
    "Fixed Income": "futures",
    "Softs": "futures",
    "Live Stock": "futures",
    "Crypto": "futures",
}


def _validate_source(value: Optional[str], internal: str) -> Optional[str]:
    if value is None:
        return None
    if value not in PRICE_SOURCES:
        raise ValueError(
            f"marketdata registry: symbol '{internal}' has price_source '{value}', "
            f"expected one of {PRICE_SOURCES}.")
    return value


def load_registry(yaml_path=None) -> Dict[str, Symbol]:
    """Path resolution: explicit arg, else $MARKETDATA_REGISTRY, else the packaged
    registry.yaml next to this module."""
    yaml_path = yaml_path or os.environ.get(
        "MARKETDATA_REGISTRY", Path(__file__).parent / "registry.yaml")
    try:
        with open(yaml_path, "r") as f:
            data = yaml.safe_load(f)
    except FileNotFoundError as e:
        raise FileNotFoundError(
            f"marketdata registry file not found: {yaml_path}. Set $MARKETDATA_REGISTRY "
            f"to a valid registry YAML, or restore the packaged registry.yaml.") from e
    except yaml.YAMLError as e:
        raise ValueError(f"marketdata registry YAML is malformed ({yaml_path}): {e}") from e

    if not isinstance(data, dict):
        raise ValueError(
            f"marketdata registry YAML must be a mapping of asset_class -> symbols "
            f"({yaml_path}); got {type(data).__name__}.")

    registry: Dict[str, Symbol] = {}
    for asset_class, symbols in data.items():
        if not isinstance(symbols, dict):
            raise ValueError(
                f"marketdata registry: asset class '{asset_class}' must map to a dict "
                f"of symbols, got {type(symbols).__name__}.")
        class_domain = _CLASS_DOMAIN.get(asset_class, DEFAULT_DOMAIN)
        for internal, attrs in symbols.items():
            attrs = attrs or {}
            if not isinstance(attrs, dict):
                raise ValueError(
                    f"marketdata registry: symbol '{internal}' must map to a dict of "
                    f"attrs, got {type(attrs).__name__}.")
            if internal in registry:
                raise ValueError(
                    f"marketdata registry: duplicate symbol '{internal}' "
                    f"(re-declared under asset class '{asset_class}').")
            dom = attrs.get("domain", class_domain)
            if dom not in DOMAINS:
                raise ValueError(
                    f"marketdata registry: symbol '{internal}' has domain "
                    f"'{dom}', expected one of {DOMAINS}.")
            sym = Symbol(
                internal=internal,
                asset_class=asset_class,
                domain=dom,
                yahoo=attrs.get("yahoo", internal),
                norgate=attrs.get("norgate", internal),
                databento=attrs.get("databento", internal if dom == "futures" else None),
                price_source=_validate_source(attrs.get("price_source"), internal),
                inception=attrs.get("inception"),
                note=attrs.get("note"),
            )
            if resolve_source(sym) is None:
                raise ValueError(
                    f"marketdata registry: symbol '{internal}' has no vendor that can "
                    f"serve it (yahoo, norgate and databento are all null).")
            registry[internal] = sym
    return registry


def _can_serve(sym: Symbol, source: str) -> bool:
    if source == "yfinance":
        return sym.yahoo is not None
    if source == "norgate":
        return sym.norgate is not None
    if source == "databento":
        return sym.databento is not None
    raise ValueError(f"unknown price source {source!r}; expected one of {PRICE_SOURCES}")


def default_price_source() -> str:
    """Deployment-wide default vendor from $MARKETDATA_PRICE_SOURCE ('yfinance' if
    unset). Set it to 'norgate' on a machine with a Norgate US Stocks
    subscription and the Data Updater running."""
    src = os.environ.get("MARKETDATA_PRICE_SOURCE", "").strip() or "yfinance"
    if src not in PRICE_SOURCES:
        raise ValueError(
            f"MARKETDATA_PRICE_SOURCE={src!r} is not one of {PRICE_SOURCES}.")
    return src


def resolve_source(sym: Symbol, default: Optional[str] = None) -> Optional[str]:
    """The vendor that serves `sym` on a deployment whose default is `default`.
    Explicit override wins, then the default if it can serve, then any vendor
    that can. None when nothing can (the caller skips the symbol)."""
    default = default or "yfinance"
    if sym.price_source:
        return sym.price_source
    if _can_serve(sym, default):
        return default
    for src in PRICE_SOURCES:
        if _can_serve(sym, src):
            return src
    return None


REGISTRY: Dict[str, Symbol] = load_registry()


def domain_for(internal: str) -> str:
    """The instrument domain for `internal`. Falls back to DEFAULT_DOMAIN for a
    symbol not in the registry, since the store is registry-free by design."""
    sym = REGISTRY.get(internal)
    return sym.domain if sym else DEFAULT_DOMAIN


def symbol(internal: str) -> Symbol:
    return REGISTRY[internal]


def all_symbols() -> List[Symbol]:
    return list(REGISTRY.values())


def by_asset_class(asset_class: str) -> List[Symbol]:
    return [s for s in REGISTRY.values() if s.asset_class == asset_class]
