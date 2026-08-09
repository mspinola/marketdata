"""Norgate futures producer — RUNS ON WINDOWS (Norgate Data Updater running +
``norgatedata``). Exchange settlement close, deep history, back-adjusted
continuous contracts.

``norgatedata`` talks to a locally installed Data Updater rather than an API, and
NDU exists for Windows only. There is no macOS or Linux path to this provider at
any Python version, which is why a synced store — not a second producer — is how
the other machines get futures bars.

ADJUSTMENT. Norgate selects continuous adjustment by SYMBOL SUFFIX, not by a
kwarg. The base symbol ``&ES`` is UNADJUSTED and shows real calendar-spread gaps
at each roll; ``&ES_CCB`` is BACK-ADJUSTED with those gaps stitched out. Verified
in cotdata 2026-07: the 2026-06 Jun-to-Sep roll moves ``&ES`` by +146 points and
``&ES_CCB`` not at all.

BOTH TIERS OR NEITHER. This provider writes ``backadj`` and ``unadj`` for every
symbol and fails the symbol if it cannot produce both. That is a hard
requirement, not a coverage nicety. ``propadj`` — the only futures series whose
percent returns are correct, and the one a volatility or position-sizing
consumer needs — is derived on read from the two of them, so a symbol with one
stored tier cannot serve that consumer at all. The failure is worth being loud
about because it is invisible to a spot check: additive back-adjusted percent
volatility comes out ~200x too high for soybeans and 0.47x for gold, and 0.47x
is a plausible-looking number that no implausibility screen catches.
"""
from __future__ import annotations

import datetime as dt
import re
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Iterable, Optional

import numpy as np
import pandas as pd

from .. import store
from ..adjust import stored_tiers_for
from ..registry import REGISTRY, all_symbols, default_price_source, resolve_source

# MUST match the registry's PRICE_SOURCES entry: the same string is the store
# path component and what `resolve_source` returns.
NAME = "norgate"
DOMAIN = "futures"

CCB_SUFFIX = "_CCB"  # Norgate "Continuous Contract Back-adjusted"

# Liquid continuous references for the finals gate: each trades every US futures
# session, so a newer settled bar on all of them means a session has landed.
_FINALS_REF_SYMBOLS = ("ES", "CL", "ZC")

# If roll-day overnight moves exceed this multiple of the normal-day median, the
# series looks UNADJUSTED. Self-calibrating per symbol, so it works across
# products with very different spread magnitudes.
ROLL_GAP_RATIO_WARN = 1.5

_COLMAP = {
    "Open": "Open", "High": "High", "Low": "Low", "Close": "Close",
    "Volume": "Volume", "Open Interest": "Open Interest",
    "Delivery Month": "Delivery Month",   # kept -> exact roll detection downstream
}

MONTH_CODES = {"F": 1, "G": 2, "H": 3, "J": 4, "K": 5, "M": 6,
               "N": 7, "Q": 8, "U": 9, "V": 10, "X": 11, "Z": 12}

# Contract-spec fields fetched per symbol. If Norgate returns nothing for ALL of
# them the row is junk — skip it rather than persist an all-null spec row.
_SPEC_FIELDS = ("Name", "Exchange", "Group", "Contract Size", "Tick Size",
                "Tick Value", "Point Value", "Currency", "Margin")


def _require_norgate_service() -> None:
    """Fail fast, with a normal exception, if NDU is not reachable — BEFORE any fetch.

    ``norgatedata`` retries each data call 10x and then calls bare ``sys.exit()``,
    which (a) exits 0, so a scheduled producer run looks successful while writing
    nothing and never triggers the scheduler's retry, and (b) raises SystemExit,
    which a per-symbol ``except Exception`` does not catch, so the whole run dies
    on the first symbol. ``norgatedata.status()`` is a safe probe
    (haltonerror=False, maxretries=1 -> returns False instead of exiting).
    """
    try:
        import norgatedata
    except ImportError as e:
        # Not an accident to be back-traced: `norgatedata` drives a locally
        # installed Data Updater that exists for Windows only, so its absence is
        # the normal state of every other machine.
        raise RuntimeError(
            "norgatedata is not installed, so this machine cannot produce futures "
            "bars. It drives a local Norgate Data Updater install, which is "
            "Windows-only — a Mac or Linux box reads a SYNCED store instead of "
            "producing one. Use --domain equities here.") from e
    try:
        reachable = bool(norgatedata.status())
    except BaseException:  # noqa: BLE001 — never let the probe itself take us down
        reachable = False
    if not reachable:
        raise RuntimeError(
            "Norgate Data service is not reachable — is the Norgate Data Updater "
            "(NDU) running and authenticated? marketdata futures bars are produced "
            "on Windows with NDU running. Aborting before fetch (non-zero exit so a "
            "scheduler retries).")


def _norgate_symbol(internal: str, tier: str) -> str:
    ng = REGISTRY[internal].norgate
    return ng + CCB_SUFFIX if tier == "backadj" else ng


def fetch(internal_symbol: str, tier: str = "backadj",
          start: str = "1970-01-01") -> pd.DataFrame:
    """Norgate continuous bars at settlement close, for one stored tier."""
    import norgatedata  # lazily; only present on the Windows producer

    df = norgatedata.price_timeseries(
        _norgate_symbol(internal_symbol, tier),
        padding_setting=norgatedata.PaddingType.NONE,
        timeseriesformat="pandas-dataframe",
        start_date=start,
    )
    df = df.rename(columns=_COLMAP)
    out = df[[c for c in _COLMAP.values() if c in df.columns]].copy()
    out.index = pd.to_datetime(out.index).tz_localize(None).normalize()
    out.index.name = "Date"
    return out.sort_index()


def _check_roll_gaps(internal_symbol: str, df: pd.DataFrame) -> bool:
    """Warn if roll-day overnight moves >> normal-day moves — the signature of an
    UNADJUSTED continuous arriving where the back-adjusted one was expected.
    Returns True if it looks unadjusted. Self-calibrating: compares each symbol's
    roll-day moves against its own non-roll baseline."""
    if "Delivery Month" not in df.columns or len(df) < 60:
        return False
    dm = df["Delivery Month"]
    roll = dm.ne(dm.shift()) & dm.shift().notna()
    if int(roll.sum()) < 8:
        return False
    overnight = (df["Close"] - df["Close"].shift(1)).abs()
    roll_med, nonroll_med = overnight[roll].median(), overnight[~roll].median()
    if nonroll_med and roll_med > ROLL_GAP_RATIO_WARN * nonroll_med:
        print(f"  !! {internal_symbol}: roll-day moves {roll_med:.1f} vs normal "
              f"{nonroll_med:.1f} ({roll_med / nonroll_med:.1f}x) — series looks "
              f"UNADJUSTED. Expected the _CCB back-adjusted symbol; a close-based "
              f"stop would false-trigger on roll gaps.")
        return True
    return False


# ── Volume reconstruction ─────────────────────────────────────────────────
def _reconstruct_volume(internal_symbol: str, continuous_df: pd.DataFrame,
                        tier: str, full: bool = False) -> pd.DataFrame:
    """Attach FirstVolume / SecondVolume / Volume_Reconstructed to a continuous frame.

    The continuous series carries FRONT-MONTH volume, which understates true
    market activity around a roll, when the next contract is already carrying
    much of the flow. Reconstructed volume sums the two highest-volume contracts
    trading that day.

    Incremental by default: only the window since the last successful
    reconstruction is re-fetched. ``full=True`` recomputes the entire history,
    which is what a change to the reconstruction LOGIC needs — otherwise the
    trailing window leaves older rows on the previous algorithm.
    """
    import norgatedata

    existing_df = store.read_bars(internal_symbol, DOMAIN, NAME, tier)
    last_date = pd.Timestamp("1970-01-01")
    if not full and "Volume_Reconstructed" in existing_df.columns:
        valid_dates = existing_df.dropna(subset=["Volume_Reconstructed"]).index
        if len(valid_dates) > 0:
            # Recompute a trailing window to catch late data and bridge partial failures.
            last_date = pd.to_datetime(valid_dates.max()) - pd.Timedelta(days=60)

    base_sym = REGISTRY[internal_symbol].norgate.lstrip("&").split("_")[0]
    pattern = re.compile(rf"^{re.escape(base_sym)}-(\d{{4}})([FGHJKMNQUVXZ])$")

    needed = []
    for sym in norgatedata.database_symbols("Futures"):
        m = pattern.match(sym)
        if m:
            year, month = int(m.group(1)), MONTH_CODES[m.group(2)]
            expiry = pd.Timestamp(year=year, month=month, day=1) + pd.DateOffset(months=1)
            if expiry >= last_date:
                needed.append(sym)

    frames = []
    if needed:
        with ThreadPoolExecutor(max_workers=10) as pool:
            futs = {pool.submit(norgatedata.price_timeseries, c,
                                padding_setting=norgatedata.PaddingType.NONE,
                                timeseriesformat="pandas-dataframe",
                                start_date=last_date.strftime("%Y-%m-%d")): c
                    for c in needed}
            for f in as_completed(futs):
                c = futs[f]
                try:
                    df_c = f.result()
                    if "Date" not in df_c.columns:
                        df_c = df_c.reset_index()
                    if not df_c.empty:
                        df_c["Symbol"] = c
                        frames.append(df_c[["Date", "Volume", "Symbol"]])
                except Exception as e:  # noqa: BLE001
                    print(f"  !! failed to fetch individual contract {c}: {e}")

    if not frames:
        # No individual contracts (crypto, some ICE softs) — front-month volume
        # IS the answer available, recorded as such rather than left ambiguous.
        return _volume_passthrough(continuous_df)

    all_indiv = pd.concat(frames, ignore_index=True)
    all_indiv["Date"] = (pd.to_datetime(all_indiv["Date"])
                         .dt.tz_localize(None).dt.normalize())

    # First / Second = the two HIGHEST-VOLUME contracts trading that day, NOT the
    # two nearest by expiry. Products with serial months around a bi-monthly
    # liquid cycle (GC, SI) carry almost no volume in the nearest serial month,
    # so an expiry-order pick would sum near-empty contracts and badly understate
    # true volume. Ties break by nearest expiry: columns are pre-sorted by expiry
    # and the sort is stable. NaN (not trading that day) sorts last.
    pivot = all_indiv.pivot(index="Date", columns="Symbol", values="Volume")

    def _expiry(sym: str) -> pd.Timestamp:
        m = pattern.match(sym)
        return pd.Timestamp(year=int(m.group(1)), month=MONTH_CODES[m.group(2)], day=1)

    pivot = pivot[sorted(pivot.columns, key=_expiry)]
    vol = pivot.values
    order = np.argsort(-np.where(np.isnan(vol), -np.inf, vol), axis=1, kind="stable")
    ranked = np.take_along_axis(vol, order, axis=1)
    names = np.array(pivot.columns)

    rec = pd.DataFrame(index=pivot.index)
    for i, (vcol, ccol) in enumerate((("FirstVolume", "FirstContract"),
                                      ("SecondVolume", "SecondContract"))):
        if ranked.shape[1] > i:
            rec[vcol] = ranked[:, i]
            rec[ccol] = np.where(np.isnan(ranked[:, i]), "", names[order[:, i]])
        else:
            rec[vcol] = np.nan
            rec[ccol] = ""

    rec["Volume_Reconstructed"] = rec["FirstVolume"].fillna(0) + rec["SecondVolume"].fillna(0)
    rec.loc[rec["FirstVolume"].isna() & rec["SecondVolume"].isna(),
            "Volume_Reconstructed"] = np.nan
    rec["Volume_Source"] = "reconstructed"

    res = continuous_df.copy()
    for col in ("FirstVolume", "SecondVolume", "FirstContract", "SecondContract",
                "Volume_Reconstructed", "Volume_Source"):
        if col not in res.columns:
            if col in existing_df.columns:
                res[col] = existing_df[col]
            else:
                res[col] = "" if col in ("FirstContract", "SecondContract",
                                         "Volume_Source") else np.nan

    res.update(rec)
    common = res.index.intersection(rec.index)
    for col in ("FirstContract", "SecondContract"):
        res.loc[common, col] = rec.loc[common, col]

    # Per-ROW fall-back to front-month, flagged, so a consumer can tell which
    # rows are true market volume and which are the front month standing in.
    mask = res["Volume_Reconstructed"].isna()
    res.loc[mask, "Volume_Reconstructed"] = res.loc[mask, "Volume"]
    res.loc[mask, "Volume_Source"] = "raw"
    return res


def _volume_passthrough(df: pd.DataFrame) -> pd.DataFrame:
    res = df.copy()
    res["FirstVolume"] = np.nan
    res["SecondVolume"] = np.nan
    res["FirstContract"] = ""
    res["SecondContract"] = ""
    res["Volume_Reconstructed"] = res["Volume"] if "Volume" in res.columns else np.nan
    res["Volume_Source"] = "raw"
    return res


# ── Finals gate ───────────────────────────────────────────────────────────
def _finals_ready_by_date(norgate_last, store_last):
    """Pure core: ready when Norgate's latest continuous bar is a NEWER completed
    session than the store already holds.

    Norgate is end-of-day and publishes a session's bar only once that session is
    complete, so a bar date newer than the store's is a new SETTLED session to
    capture. This needs no trading calendar (weekends and holidays simply produce
    no new bar) and no wall-clock cutoff (early publish -> ready early, late
    publish -> not there yet, and a retry catches it), which is what makes it
    immune to Norgate's publish-time drift. Returns (ready, detail).
    """
    def _d(x):
        if x is None:
            return None
        return x.date() if isinstance(x, dt.datetime) else x

    nl, sl = _d(norgate_last), _d(store_last)
    detail = {"norgate_last": nl.isoformat() if nl else None,
              "store_last": sl.isoformat() if sl else None}
    if nl is None:
        return False, detail  # Norgate has no bar to offer -> defer
    return (sl is None or nl > sl), detail


def _finals_ready_quorum(norgate_dates: dict, store_dates: dict):
    """Ready only when EVERY reference symbol has a newer settled bar than the
    store holds. Requiring the whole quorum means a session is captured once and
    complete; one lagging reference cannot green-light a partial capture."""
    per, ready_all = {}, True
    for sym in norgate_dates:
        r, d = _finals_ready_by_date(norgate_dates.get(sym), store_dates.get(sym))
        per[sym] = {**d, "ready": r}
        ready_all = ready_all and r
    return ready_all, {"mode": "data", "per_symbol": per}


def _norgate_last_bar_date(sym: str):
    """Latest back-adjusted continuous bar date Norgate holds for `sym`, or None.
    Pulls a short trailing window (cheap) and takes the last index."""
    import norgatedata  # Windows producer only

    df = norgatedata.price_timeseries(
        _norgate_symbol(sym, "backadj"),
        padding_setting=norgatedata.PaddingType.NONE,
        timeseriesformat="pandas-dataframe",
        start_date=(dt.date.today() - dt.timedelta(days=10)).isoformat(),
    )
    if df is None or len(df) == 0:
        return None
    return pd.to_datetime(df.index[-1]).tz_localize(None).normalize().date()


def _store_last_bar_date(sym: str):
    """Latest back-adjusted date already captured for `sym`, or None. Read from the
    manifest — no price I/O."""
    entry = store.load_manifest().get("bars", {}).get(f"{DOMAIN}/{NAME}/{sym}_backadj")
    ld = (entry or {}).get("last_date")
    return pd.to_datetime(ld).date() if ld else None


def finals_ready(ref_symbols=_FINALS_REF_SYMBOLS):
    """Ready once Norgate has a NEWER settled continuous bar than the store, for a
    quorum of liquid reference symbols. Returns (ready, detail)."""
    _require_norgate_service()  # NDU-down guard: norgatedata calls bare sys.exit otherwise
    return _finals_ready_quorum({s: _norgate_last_bar_date(s) for s in ref_symbols},
                                {s: _store_last_bar_date(s) for s in ref_symbols})


# ── Producer ──────────────────────────────────────────────────────────────
def _targets(symbols: Optional[Iterable[str]] = None) -> list:
    """Registry symbols this provider serves: futures domain, Norgate-covered, and
    resolving to Norgate on this deployment."""
    default = default_price_source()
    wanted = set(symbols) if symbols is not None else None
    if wanted:
        unknown = sorted(wanted - set(REGISTRY))
        if unknown:
            raise KeyError(f"not in the marketdata registry: {unknown}")
    targets, skipped = [], []
    for s in all_symbols():
        if s.domain != DOMAIN or (wanted is not None and s.internal not in wanted):
            continue
        (targets if s.norgate and resolve_source(s, default) == NAME
         else skipped).append(s)
    if skipped:
        print(f"  skipping {len(skipped)} futures symbol(s) Norgate cannot serve "
              f"(priced elsewhere): {', '.join(s.internal for s in skipped)}")
    return targets


def update(symbols: Optional[Iterable[str]] = None, full: bool = False) -> dict:
    """Fetch and store BOTH tiers for every target symbol.

    A symbol whose second tier fails is left unwritten rather than half-written:
    both frames are fetched and reconstructed before either is stored, so an
    empty or erroring fetch fails the symbol with nothing on disk. See the module
    docstring for why a half-stored symbol is worse than a missing one.

    ``full=True`` rebuilds the reconstructed-volume columns over the entire
    history instead of the trailing incremental window.
    """
    _require_norgate_service()  # abort cleanly if NDU is down
    targets = _targets(symbols)
    if not targets:
        print("norgate: no futures symbols resolve to this provider")
        return {"kind": "bars_futures_norgate", "ok": True, "wrote": 0, "failed": 0,
                "symbols_failed": [], "rows": 0, "newest": None}

    tiers = stored_tiers_for(DOMAIN)
    prior = store.load_manifest().get("bars", {})
    t0 = time.time()
    wrote, failed, rows, newest = 0, [], 0, None

    for s in targets:
        sym = s.internal
        try:
            frames = {}
            for tier in tiers:
                df = fetch(sym, tier=tier)
                if df.empty:
                    raise RuntimeError(
                        f"Norgate returned no {tier} bars for "
                        f"{_norgate_symbol(sym, tier)}")
                if tier == "backadj":
                    _check_roll_gaps(sym, df)  # sanity: warn if backadj looks unadjusted
                frames[tier] = _reconstruct_volume(sym, df, tier, full=full)

            # Nothing is written until every tier is in hand.
            for tier, df in frames.items():
                store.write_bars(sym, df, domain=DOMAIN, source=NAME, tier=tier)

            wrote += 1
            rows += sum(len(df) for df in frames.values())
            back = frames["backadj"]
            new = str(back.index.max().date()) if len(back) else "—"
            newest = max(newest, new) if newest else new
            was = (prior.get(f"{DOMAIN}/{NAME}/{sym}_backadj") or {}).get("last_date")
            delta = new if (was is None or was == new) else f"{was} -> {new}"
            print(f"{sym:5s}: " + ", ".join(
                f"{len(frames[t]):6d} {t}" for t in tiers) + f"  [{delta}]")
        except Exception as e:  # noqa: BLE001 — one bad symbol must not end the run
            failed.append((sym, str(e)))
            print(f"{sym:5s}: FAILED — {e}")

    return {"kind": "bars_futures_norgate", "ok": not failed, "wrote": wrote,
            "failed": len(failed), "symbols_failed": [s for s, _ in failed],
            "errors": failed, "rows": rows, "newest": newest,
            "seconds": round(time.time() - t0, 1)}


# ── Contract specifications ───────────────────────────────────────────────
def get_symbol_metadata(internal_symbol: str) -> dict:
    """Contract specifications for one continuous futures symbol.

    Every field is fetched defensively: Norgate raises rather than returning None
    for a field it has no value for, and one missing field must not cost the
    whole row.
    """
    import norgatedata  # lazily

    ng_sym = _norgate_symbol(internal_symbol, "backadj")
    data = {"Symbol": internal_symbol, "Norgate_Symbol": ng_sym}

    def _try(key, fn):
        try:
            data[key] = fn()
        except Exception:  # noqa: BLE001
            data[key] = None

    _try("Name", lambda: norgatedata.security_name(ng_sym))
    _try("Exchange", lambda: norgatedata.exchange_name(ng_sym))
    _try("Group", lambda: norgatedata.classification_at_level(
        ng_sym, schemename="NorgateDataFuturesClassification",
        classificationresulttype="Name", level=1))
    _try("Contract Size", lambda: norgatedata.point_value(ng_sym))
    _try("Tick Size", lambda: norgatedata.tick_size(ng_sym))
    _try("Currency", lambda: norgatedata.currency(ng_sym))
    _try("Margin", lambda: norgatedata.margin(ng_sym))

    ts, cs = data["Tick Size"], data["Contract Size"]
    data["Tick Value"] = (ts * cs) if (ts is not None and cs is not None) else None
    data["Point Value"] = cs
    return data


def update_metadata(symbols: Optional[Iterable[str]] = None) -> dict:
    """Fetch and store contract specifications.

    A SCOPED run upserts by Symbol, so markets outside the request keep their
    specs — contract specs share one table, and a plain write would drop them.
    With no `symbols`, the full table is regenerated and replaced.
    """
    import concurrent.futures

    scoped = symbols is not None
    _require_norgate_service()
    targets = _targets(symbols)
    if not targets:
        print("norgate: no futures symbols resolve to this provider")
        return {"kind": "contract_specs", "ok": True, "wrote": 0, "failed": 0}

    print(f"fetching contract specs for {len(targets)} symbols...")
    rows, skipped = [], []
    with concurrent.futures.ThreadPoolExecutor(max_workers=10) as pool:
        futs = {pool.submit(get_symbol_metadata, s.internal): s.internal
                for s in targets}
        for f in concurrent.futures.as_completed(futs):
            result = f.result()
            # A covered symbol whose specs ALL came back None is a transient
            # Norgate failure, not real data. Skipping beats persisting a null
            # row — and on a scoped upsert, beats overwriting good specs with nulls.
            if all(result.get(k) is None for k in _SPEC_FIELDS):
                print(f"  !! {result['Symbol']}: all specs empty (Norgate returned "
                      f"nothing) — skipping to avoid a null row")
                skipped.append(result["Symbol"])
                continue
            rows.append(result)

    if not rows:
        print("no contract specs fetched.")
        return {"kind": "contract_specs", "ok": False, "wrote": 0,
                "failed": len(skipped), "symbols_failed": skipped}

    df = pd.DataFrame(rows).sort_values("Symbol").reset_index(drop=True)
    if scoped:
        store.upsert_metadata(df, source=NAME)
        print(f"upserted contract specs for {len(df)} symbols "
              f"(markets outside the request preserved)")
    else:
        store.write_metadata(df, source=NAME)
        print(f"wrote contract specs for {len(df)} symbols")
    return {"kind": "contract_specs", "ok": not skipped, "wrote": len(df),
            "failed": len(skipped), "symbols_failed": skipped}
