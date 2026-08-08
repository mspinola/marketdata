"""marketdata — daily bars, producer/consumer split over a file store.

Sibling of cotdata, deliberately not part of it: cotdata's registry requires a
cftc_code and equities have no COT report. Same design ideas (registry identity,
producer writes / consumers read, vendor resolution as a deployment choice,
atomic writes + manifest provenance, adjustment derived on read).

Two domains, two adjustment axes. EQUITIES adjust by corporate actions, which
arrive as dated events, so one stored frame yields every tier. FUTURES adjust by
roll splicing, which the vendor performs and does not explain, so `backadj` and
`unadj` are both stored and `propadj` is derived from the pair.
"""
from .adjust import (
    ADJUSTMENT_VERSION,
    DOMAIN_TIERS,
    STORED_TIERS,
    TIERS,
    adjust,
    ratio_adjust,
    stored_tiers_for,
    tiers_for,
)
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

# read_metadata is public API rather than an internal reached for from outside:
# contract specs (point value, tick size) are what turns a futures bar into
# notional or risk units, so a package that reads bars reads specs too.
from .store import load_manifest, read_metadata, require_schema, schema_version

__version__ = "0.1.0"
__all__ = [
    "get_bars", "available",
    "provenance", "Provenance",
    "adjust", "ratio_adjust", "TIERS", "DOMAIN_TIERS", "STORED_TIERS",
    "tiers_for", "stored_tiers_for", "ADJUSTMENT_VERSION",
    "symbol", "all_symbols", "by_asset_class", "domain_for", "DOMAINS",
    "REGISTRY", "Symbol",
    "load_manifest", "read_metadata", "schema_version", "require_schema",
]
