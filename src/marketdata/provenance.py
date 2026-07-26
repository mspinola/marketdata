"""What actually backs a series, so a vendor-blind read is not also vendor-ignorant.

`get_bars` deliberately takes no vendor argument: the store is the provider boundary
and a consumer should not care who filled it. That is right for the READ and wrong
for the NUMBERS. Vendors differ in ways no abstraction can hide:

  * **History depth.** A vendor whose feed starts in 2010 and one going back decades
    both answer `get_bars` without error. A lookback window silently runs on less
    data.
  * **Column coverage.** A vendor that cannot supply a derived column degrades to a
    fall-back rather than raising.
  * **Vendor mix.** Which vendor serves a symbol is per-symbol and per-deployment,
    never one answer for a whole store.

So the fix is not to leak the vendor into the read path. It is to make the read path
vendor-blind but capability-AWARE: ask this module when the answer matters, for a UI
badge, a startup assertion, or a reproducer line in a write-up.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

import pandas as pd

from . import store
from .registry import domain_for


@dataclass(frozen=True)
class Provenance:
    """What the store knows about one symbol's series, from the manifest only.

    No parquet is opened, so this is cheap enough for a UI to call per render.
    """
    symbol: str
    domain: str
    source: str
    first_date: Optional[str]
    last_date: Optional[str]
    n_rows: int
    updated_at: Optional[str]
    n_dividends: int = 0
    n_stock_splits: int = 0
    other_sources: tuple = ()

    def covers(self, start) -> bool:
        """Whether the series reaches back to `start`.

        The check to run before trusting a long lookback. A vendor with a shallow
        history floor answers `get_bars` perfectly happily on a window it cannot
        actually support.
        """
        if not self.first_date:
            return False
        return pd.Timestamp(self.first_date) <= pd.Timestamp(start)

    def describe(self) -> str:
        """One line for a UI badge or a log."""
        span = (f"{self.first_date}..{self.last_date}"
                if self.first_date else "empty")
        extra = f", +{len(self.other_sources)} other source(s)" if self.other_sources else ""
        return (f"{self.symbol} [{self.domain}] {self.source}: "
                f"{self.n_rows} bars {span}{extra}")


def provenance(symbol: str, *, domain: Optional[str] = None,
               source: Optional[str] = None) -> Optional[Provenance]:
    """What backs `symbol` in the store, or None if nothing does.

    Mirrors `get_bars` resolution and its loudness: absent everywhere returns None
    (the honest answer to "what is there"), but present under a DIFFERENT vendor
    raises, because silently reporting on a series the caller is not reading would
    be worse than no answer.
    """
    from .bars import default_source_for

    dom = domain or domain_for(symbol)
    src = source or default_source_for(symbol)
    present = store.sources_for(symbol, dom)

    if src not in present:
        others = [s for s in present if s != src]
        if others:
            raise FileNotFoundError(
                f"{symbol} is not in the store under source {src!r} (domain "
                f"{dom!r}), but it is under {others}. Pass source= explicitly. "
                f"Vendors are never silently substituted.")
        return None

    entry = store.load_manifest().get("bars", {}).get(f"{dom}/{src}/{symbol}")
    if entry is None:
        # Parquet on disk with no manifest row: a hand-copied file, or a manifest
        # lost to the read-modify-write hazard. Report what is knowable rather
        # than pretending the series is absent.
        return Provenance(symbol=symbol, domain=dom, source=src, first_date=None,
                          last_date=None, n_rows=0, updated_at=None,
                          other_sources=tuple(s for s in present if s != src))
    return Provenance(
        symbol=symbol,
        domain=dom,
        source=src,
        first_date=entry.get("first_date"),
        last_date=entry.get("last_date"),
        n_rows=int(entry.get("n_rows", 0)),
        updated_at=entry.get("updated_at"),
        n_dividends=int(entry.get("n_dividends", 0)),
        n_stock_splits=int(entry.get("n_stock_splits", 0)),
        other_sources=tuple(s for s in present if s != src),
    )
