"""Store location + schema version. The store is set via MARKETDATA_STORE.

Deliberately a SEPARATE root (and a separate manifest) from cotdata's. The two
producers can point at the same synced parent folder, but they must not share one
manifest.json: cotdata's ``_touch_manifest`` is a read-modify-write, so two
producers writing one manifest will eventually lose an entry.
"""
from __future__ import annotations

import os
from pathlib import Path

# v1 — bars stored exactly as the vendor serves them (split-adjusted OHLCV for
#      yfinance) plus dated action columns. Every adjustment tier is derived on
#      read by marketdata.adjust; no adjusted column is ever stored.
# v2 — the futures domain arrives, and it does not fit v1's one-frame-per-symbol
#      assumption. Every equity tier is derivable from a single stored frame
#      because corporate actions are DATED EVENTS the vendor hands over with the
#      bars. Norgate's back-adjustment is not: it is vendor-computed roll
#      splicing, recoverable from no other single series, so `backadj` and
#      `unadj` are two separate stored facts rather than one frame plus a
#      derivation. Hence the stored-tier component in the filename
#      (`<symbol>_<tier>.parquet`), used by futures and absent for equities —
#      whose v1 layout is unchanged and still read by exactly the same path.
SCHEMA_VERSION = 2

#: Can this store's symbol universe be sliced AS IT STOOD on a past date?
#:
#: No, and it is not close. yfinance serves currently-listed securities only: it
#: cannot return a delisted ticker at all, and it has no notion of index
#: membership on a given date. So the universe is the survivors, and any study
#: that RANKS or SELECTS across symbols (pick the top N, trade the spread between
#: the best and worst) inherits a survivorship bias it cannot see. A study that
#: trades each symbol on its own terms is unaffected, which is why this is a flag
#: rather than a refusal.
#:
#: Stamped into the manifest so the fact travels with the DATA. A consumer who
#: copies the store somewhere else, or reads it years later, gets the warning
#: without having to find this file. Enforce with `store.require_point_in_time()`.
UNIVERSE_IS_POINT_IN_TIME = False


def store_root() -> Path:
    root = os.environ.get("MARKETDATA_STORE", "").strip()
    if not root:
        raise RuntimeError(
            "MARKETDATA_STORE is not set. Point it at the equity/ETF bar store "
            "(the folder holding bars/ and manifest.json)."
        )
    return Path(root)


def _safe(part: str, what: str) -> str:
    if not part or "/" in part or part.startswith("."):
        raise ValueError(f"invalid {what} for a store path: {part!r}")
    return part


def bars_dir(domain: str, source: str) -> Path:
    """``bars/<domain>/<source>/``.

    DOMAIN is the instrument class, and it is the axis that matters: futures and
    equities have entirely different adjustment axes (roll splicing against
    corporate actions), so their frames are not interchangeable even when the
    symbol strings collide.

    SOURCE is the vendor, and it is in the path rather than only the manifest
    because two vendors that both carry a symbol would otherwise write the same
    file and the last producer to run would silently win. Yahoo and Norgate
    overlap almost completely on equities, and they do not even store the same
    columns. Keeping them apart is also what makes a vendor A/B comparison
    possible at all.
    """
    return store_root() / "bars" / _safe(domain, "domain") / _safe(source, "price source")


def bars_path(symbol: str, domain: str, source: str, tier: str | None = None) -> Path:
    """The parquet holding one stored series.

    ``tier`` names a series the VENDOR computed and we therefore have to keep,
    not a tier we can derive. Equities pass ``None`` and keep the flat
    ``<symbol>.parquet`` of schema v1; futures pass ``'backadj'`` / ``'unadj'``
    and get ``<symbol>_<tier>.parquet``, because Norgate's roll splicing cannot
    be reconstructed from the unadjusted series alone.

    A derived tier never appears here. ``propadj`` is computed on read from the
    two stored futures frames, exactly as the equity tiers are computed from the
    one stored equity frame.
    """
    name = f"{_safe(symbol, 'symbol')}_{_safe(tier, 'stored tier')}" if tier \
        else _safe(symbol, "symbol")
    return bars_dir(domain, source) / f"{name}.parquet"


def metadata_dir() -> Path:
    """``metadata/`` — tables keyed by symbol rather than by date.

    Contract specifications live here. Unlike bars, this is ONE table for every
    symbol, which is why writing it needs an upsert (see ``store.upsert_metadata``).
    """
    return store_root() / "metadata"


def manifest_path() -> Path:
    return store_root() / "manifest.json"
