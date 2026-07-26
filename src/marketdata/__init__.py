"""marketdata — equity and ETF daily bars, producer/consumer split over a file store.

Sibling of cotdata, deliberately not part of it: cotdata's registry requires a
cftc_code and equities have no COT report. Same design ideas (registry identity,
producer writes / consumers read, vendor resolution as a deployment choice,
atomic writes + manifest provenance, adjustment derived on read), different
domain.
"""
from .adjust import ADJUSTMENT_VERSION, DOMAIN_TIERS, TIERS, adjust, tiers_for
from .bars import available, get_bars
from .provenance import Provenance, provenance
from .registry import (
    DOMAINS,
    REGISTRY,
    Symbol,
    all_symbols,
    by_asset_class,
    domain_for,
    symbol,
)
from .store import load_manifest, require_schema, schema_version

__version__ = "0.1.0"
__all__ = [
    "get_bars", "available",
    "provenance", "Provenance",
    "adjust", "TIERS", "DOMAIN_TIERS", "tiers_for", "ADJUSTMENT_VERSION",
    "symbol", "all_symbols", "by_asset_class", "domain_for", "DOMAINS",
    "REGISTRY", "Symbol",
    "load_manifest", "schema_version", "require_schema",
]
