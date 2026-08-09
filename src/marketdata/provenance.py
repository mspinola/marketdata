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

import datetime as _dt
from dataclasses import dataclass
from typing import Iterable, Optional

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
    #: Which STORED series this describes. None for a domain that stores one frame
    #: per symbol (equities); 'backadj' or 'unadj' for futures, where the two are
    #: fetched in the same run but are separate files with separate provenance.
    tier: Optional[str] = None

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
        tier = f" {self.tier}" if self.tier else ""
        return (f"{self.symbol}{tier} [{self.domain}] {self.source}: "
                f"{self.n_rows} bars {span}{extra}")


def provenance(symbol: str, *, domain: Optional[str] = None,
               source: Optional[str] = None,
               tier: Optional[str] = None) -> Optional[Provenance]:
    """What backs `symbol` in the store, or None if nothing does.

    Mirrors `get_bars` resolution and its loudness: absent everywhere returns None
    (the honest answer to "what is there"), but present under a DIFFERENT vendor
    raises, because silently reporting on a series the caller is not reading would
    be worse than no answer.

    `tier` picks one STORED series in a domain that has several. It defaults to
    the domain's first — `backadj` for futures — so the common call stays
    `provenance("ES")`. The two futures tiers are written by one run but are
    separate files, so each has its own row count and date span, and a caller
    checking whether `propadj` is available has to ask about both.
    """
    from .adjust import stored_tiers_for
    from .bars import default_source_for

    dom = domain or domain_for(symbol)
    src = source or default_source_for(symbol)
    t = tier if tier is not None else stored_tiers_for(dom)[0]
    present = store.sources_for(symbol, dom, t)

    if src not in present:
        others = [s for s in present if s != src]
        if others:
            raise FileNotFoundError(
                f"{symbol} is not in the store under source {src!r} (domain "
                f"{dom!r}), but it is under {others}. Pass source= explicitly. "
                f"Vendors are never silently substituted.")
        return None

    name = f"{dom}/{src}/{symbol}" + (f"_{t}" if t else "")
    entry = store.load_manifest().get("bars", {}).get(name)
    if entry is None:
        # Parquet on disk with no manifest row: a hand-copied file, or a manifest
        # lost to the read-modify-write hazard. Report what is knowable rather
        # than pretending the series is absent.
        return Provenance(symbol=symbol, domain=dom, source=src, first_date=None,
                          last_date=None, n_rows=0, updated_at=None, tier=t,
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
        tier=t,
    )


# ── The startup guard ─────────────────────────────────────────────────────
# `provenance` answers "what is there". This answers "is that enough", which is
# the question a deployment has to ask BEFORE it renders anything.
#
# WHY THIS IS A SEPARATE, OPT-IN CHECK RATHER THAN A CHANGE TO `get_bars`.
# `_missing` in bars.py is deliberate: a symbol absent everywhere reads as an
# empty frame, because the store is registry-free and a caller probing for a
# one-off symbol should get an honest empty answer rather than an exception. That
# is right for one read and wrong for a deployment, where the same silence means
# every chart renders blank and nothing says why.
#
# The distinction that resolves it is who is asking. A read asks about one
# symbol and may legitimately not know whether it exists. A deployment asks about
# a universe it has already committed to, so absence is a failure by definition.
# Keeping the loudness here means the read path keeps its documented semantics
# and the consumer opts into refusal, which is also the seam: this package owns
# the FACT, the consumer owns the POLICY.


class CoverageError(RuntimeError):
    """A store cannot support the universe a deployment asked for."""


@dataclass(frozen=True)
class Gap:
    """One reason one stored series is not good enough.

    A futures symbol yields up to two of these, one per stored tier, because the
    tiers are separate files with separate provenance and `propadj` is derived
    from the pair. A symbol whose `backadj` landed and whose `unadj` did not
    reads perfectly on the default tier and then mis-scales the moment anything
    asks for a return series, so half-present has to be reported as a gap rather
    than rounded up to present.
    """
    symbol: str
    tier: Optional[str]
    #: 'absent' nothing in the store | 'empty' a file with no rows | 'short' the
    #: history does not reach the requested start | 'stale' the newest bar is
    #: older than the deployment tolerates.
    reason: str
    detail: str

    def __str__(self) -> str:
        tier = f" {self.tier}" if self.tier else ""
        return f"{self.symbol}{tier}: {self.reason}, {self.detail}"


def _stale_detail(last_date: Optional[str], as_of: _dt.date,
                  stale_after_days: int) -> Optional[str]:
    if not last_date:
        return None
    age = (as_of - pd.Timestamp(last_date).date()).days
    if age <= stale_after_days:
        return None
    return f"newest bar {last_date} is {age} days old (tolerance {stale_after_days})"


def coverage_gaps(symbols: Iterable[str], *, start=None,
                  stale_after_days: Optional[int] = None,
                  as_of: Optional[_dt.date] = None,
                  domain: Optional[str] = None,
                  source: Optional[str] = None) -> list:
    """Every reason the store cannot serve `symbols`, as a list of `Gap`.

    Empty list means the store is good for the question asked. Reads the manifest
    only, so this is cheap enough to run on every boot: no parquet is opened.

    `start` turns on the history-depth check, which is the one a long lookback
    needs. A vendor with a shallow floor answers `get_bars` happily on a window it
    cannot support, so the window has to be stated to be checked.

    `stale_after_days` turns on the freshness check, and it is deliberately opt-in
    with no default. There is no store-side answer to "how old is too old": bars
    only move on trading days, so a Monday morning reading of 3 days is healthy
    and the same number midweek is not. The caller knows its own calendar. A value
    around 7 covers a weekend plus a holiday for a daily deployment.

    Freshness is checked against wall-clock rather than against another store,
    because the case worth catching is a producer that ran while whatever feeds
    this store did not, and this package cannot see the other side of that without
    importing a sibling it has no business importing.
    """
    as_of = as_of or _dt.date.today()
    gaps = []
    for sym in symbols:
        dom = domain or domain_for(sym)
        for tier in _stored_tiers(dom):
            try:
                p = provenance(sym, domain=dom, source=source, tier=tier)
            except FileNotFoundError as e:
                # Present under a different vendor. Loud already, and a gap for
                # this deployment: it is not reading that vendor.
                gaps.append(Gap(sym, tier, "absent", str(e)))
                continue
            if p is None:
                gaps.append(Gap(sym, tier, "absent",
                                f"nothing in the store for domain {dom!r}"))
                continue
            if p.n_rows == 0 or not p.first_date:
                gaps.append(Gap(sym, tier, "empty",
                                "a file with no rows, or no manifest entry "
                                "describing it"))
                continue
            if start is not None and not p.covers(start):
                gaps.append(Gap(sym, tier, "short",
                                f"history starts {p.first_date}, after the "
                                f"requested {pd.Timestamp(start).date()}"))
            if stale_after_days is not None:
                why = _stale_detail(p.last_date, as_of, stale_after_days)
                if why:
                    gaps.append(Gap(sym, tier, "stale", why))
    return gaps


def require_coverage(symbols: Iterable[str], **kwargs) -> None:
    """`coverage_gaps`, but raise `CoverageError` instead of returning.

    The one-line form for a startup assertion. The message names every gap and
    groups the count by reason, because the shapes need different fixes and a
    deployment reading one line should be able to tell them apart: 'absent' is a
    store that was never filled, 'stale' is a producer that ran without whatever
    feeds this store, 'short' is a lookback the vendor cannot support.
    """
    gaps = coverage_gaps(symbols, **kwargs)
    if not gaps:
        return
    by_reason = {}
    for g in gaps:
        by_reason[g.reason] = by_reason.get(g.reason, 0) + 1
    summary = ", ".join(f"{n} {r}" for r, n in sorted(by_reason.items()))
    lines = "\n  ".join(str(g) for g in gaps)
    raise CoverageError(
        f"the marketdata store cannot serve {len(gaps)} requested series "
        f"({summary}):\n  {lines}")


def _stored_tiers(domain: str) -> tuple:
    from .adjust import stored_tiers_for

    return stored_tiers_for(domain)
