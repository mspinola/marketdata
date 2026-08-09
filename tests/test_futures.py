"""Futures domain: two stored tiers, propadj derived from the pair.

The equity tests cover a domain where one stored frame yields every tier. These
cover the one where it does not, and the failure modes that creates.
"""
import numpy as np
import pandas as pd
import pytest

from marketdata import bars, config, registry, store

# Imported by name, not as a module: `marketdata.adjust` the package attribute is
# the FUNCTION (it is re-exported in __init__), which shadows the submodule.
from marketdata.adjust import (
    DERIVED_TIERS,
    DOMAIN_TIERS,
    STORED_TIERS,
    adjust,
    ratio_adjust,
    stored_tiers_for,
)
from marketdata.providers import norgate as nprov

FUT = "futures"


def frame(closes, delivery=None, start="2020-01-01"):
    idx = pd.date_range(start, periods=len(closes), freq="D")
    df = pd.DataFrame({"Open": closes, "High": closes, "Low": closes,
                       "Close": closes, "Volume": 100.0,
                       "Open Interest": 1000.0}, index=idx)
    if delivery is not None:
        df["Delivery Month"] = delivery
    df.index.name = "Date"
    return df


# ── Store layout ──────────────────────────────────────────────────────────
def test_stored_tier_is_a_filename_component_and_equities_keep_the_v1_layout(tmp_store):
    """The tier suffix must appear ONLY where a domain stores more than one
    series. An equity file growing a suffix would orphan every v1 store."""
    store.write_bars("SPY", frame([1, 2, 3]), domain="equities", source="yfinance")
    store.write_bars("ES", frame([1, 2, 3]), domain=FUT, source="norgate",
                     tier="backadj")

    assert config.bars_path("SPY", "equities", "yfinance").name == "SPY.parquet"
    assert config.bars_path("ES", FUT, "norgate", "backadj").name == "ES_backadj.parquet"
    assert store.has_bars("SPY", "equities", "yfinance")
    assert store.has_bars("ES", FUT, "norgate", tier="backadj")
    assert not store.has_bars("ES", FUT, "norgate", tier="unadj")


def test_the_two_tiers_do_not_overwrite_each_other(tmp_store):
    """The bug the tier component exists to prevent: one path per symbol means
    the second producer write silently replaces the first."""
    store.write_bars("ES", frame([1, 2, 3]), domain=FUT, source="norgate", tier="backadj")
    store.write_bars("ES", frame([10, 20, 30]), domain=FUT, source="norgate", tier="unadj")

    assert list(store.read_bars("ES", FUT, "norgate", "backadj")["Close"]) == [1, 2, 3]
    assert list(store.read_bars("ES", FUT, "norgate", "unadj")["Close"]) == [10, 20, 30]


def test_manifest_records_each_tier_separately(tmp_store):
    store.write_bars("ES", frame([1, 2, 3]), domain=FUT, source="norgate", tier="backadj")
    store.write_bars("ES", frame([1, 2]), domain=FUT, source="norgate", tier="unadj")

    entries = store.load_manifest()["bars"]
    assert entries["futures/norgate/ES_backadj"]["n_rows"] == 3
    assert entries["futures/norgate/ES_unadj"]["n_rows"] == 2


def test_available_reports_symbols_not_stored_series(tmp_store):
    """A caller asking what the store holds wants ES, not ES_backadj and
    ES_unadj — otherwise every caller re-parses the file naming."""
    for tier in ("backadj", "unadj"):
        store.write_bars("ES", frame([1, 2, 3]), domain=FUT, source="norgate", tier=tier)
    assert bars.available(domain=FUT) == {FUT: {"norgate": ["ES"]}}


# ── Reading the stored tiers ──────────────────────────────────────────────
def test_stored_tiers_read_back_unchanged(tmp_store):
    """backadj and unadj are vendor-computed facts. Nothing derives them, so a
    read is a read."""
    store.write_bars("ES", frame([10, 11, 12]), domain=FUT, source="norgate", tier="backadj")
    store.write_bars("ES", frame([20, 21, 22]), domain=FUT, source="norgate", tier="unadj")

    assert list(bars.get_bars("ES", "backadj")["Close"]) == [10, 11, 12]
    assert list(bars.get_bars("ES", "unadj")["Close"]) == [20, 21, 22]


def test_backadj_is_the_default_tier_for_futures(tmp_store):
    store.write_bars("ES", frame([10, 11, 12]), domain=FUT, source="norgate", tier="backadj")
    assert list(bars.get_bars("ES")["Close"]) == [10, 11, 12]


def test_futures_resolve_to_norgate_even_when_the_deployment_default_is_yahoo(monkeypatch):
    """Yahoo cannot serve them (yahoo: null in the registry), so capability has to
    win over the deployment default or every futures read looks in an empty
    directory."""
    monkeypatch.setenv("MARKETDATA_PRICE_SOURCE", "yfinance")
    assert bars.default_source_for("ES") == "norgate"
    assert bars.default_source_for("SPY") == "yfinance"


# ── propadj, derived from both ────────────────────────────────────────────
def test_propadj_preserves_percent_returns_across_a_roll():
    """The property that makes propadj the volatility series.

    The old contract is front on days 0-1 at 100 -> 110. The new one takes over on
    day 2 at 126, and was trading at 120 on day 1, so the calendar spread is 10.

    The true return across the roll is what the NEW contract actually did between
    day 1 and day 2: 126/120 - 1 = +5%. The unadjusted series shows +14.5%
    (110 -> 126), which is mostly the spread rather than a market move. Additive
    back-adjustment removes that fake jump but computes it off shifted prices, so
    it reports the day-1 move as +9.1% instead of the +10% that traded. Ratio
    adjustment gets both right.
    """
    unadj_close = [100.0, 110.0, 126.0, 132.0]
    delivery = ["2020H", "2020H", "2020M", "2020M"]
    # Norgate's additive continuous: the pre-roll history shifted up by the spread.
    backadj_close = [110.0, 120.0, 126.0, 132.0]

    u = frame(unadj_close, delivery=delivery)
    b = frame(backadj_close, delivery=delivery)
    out = ratio_adjust(u, b)
    p_ret = out["Close"].pct_change().reset_index(drop=True)

    # Within each segment, percent returns are exactly the traded ones.
    u_ret = pd.Series(unadj_close).pct_change()
    assert p_ret[1] == pytest.approx(u_ret[1])   # +10%, inside the old segment
    assert p_ret[3] == pytest.approx(u_ret[3])   # inside the new segment

    # Across the roll: the new contract's own return, not the spread.
    assert p_ret[2] == pytest.approx(126.0 / 120.0 - 1)

    # And what additive back-adjustment gets wrong on the same day.
    assert pd.Series(backadj_close).pct_change()[1] == pytest.approx(120.0 / 110.0 - 1)
    assert p_ret[1] != pytest.approx(pd.Series(backadj_close).pct_change()[1])

    # The most recent segment is anchored to actual prices.
    assert list(out["Close"])[-2:] == pytest.approx(unadj_close[-2:])


def test_propadj_needs_no_delivery_month_column():
    """A producer that dropped 'Delivery Month' still gets a usable series: rolls
    fall back to material steps in the additive offset."""
    out = ratio_adjust(frame([100.0, 110.0, 126.0, 132.0]),
                       frame([110.0, 120.0, 126.0, 132.0]))
    assert out["Close"].pct_change().iloc[2] == pytest.approx(126.0 / 120.0 - 1)
    assert out["Close"].pct_change().iloc[1] == pytest.approx(0.10)


def test_propadj_keeps_the_sign_of_the_underlying():
    """Ratio adjustment scales by a POSITIVE factor, so it preserves the sign
    rather than imposing one. WTI settled at -37.63 on 2020-04-20 and propadj is
    negative there too. Documented because an earlier docstring claimed the
    output was strictly positive, and a consumer believed it."""
    u = frame([100.0, 110.0, -20.0, 40.0], delivery=["A", "A", "B", "B"])
    b = frame([110.0, 120.0, -20.0, 40.0], delivery=["A", "A", "B", "B"])
    out = ratio_adjust(u, b)
    assert (out["Close"] < 0).sum() == 1


def test_propadj_passes_non_price_columns_through(tmp_store):
    u = frame([100.0, 110.0], delivery=["A", "A"])
    u["Volume_Source"] = "reconstructed"
    b = frame([100.0, 110.0], delivery=["A", "A"])
    out = ratio_adjust(u, b)
    assert list(out["Volume_Source"]) == ["reconstructed", "reconstructed"]
    assert list(out["Open Interest"]) == [1000.0, 1000.0]


def test_propadj_is_empty_when_a_frame_is_empty():
    assert ratio_adjust(pd.DataFrame(), frame([1, 2])).empty
    assert ratio_adjust(frame([1, 2]), pd.DataFrame()).empty


def test_get_bars_derives_propadj_from_the_two_stored_tiers(tmp_store):
    delivery = ["2020H", "2020H", "2020M", "2020M"]
    store.write_bars("ES", frame([100.0, 110.0, 126.0, 132.0], delivery),
                     domain=FUT, source="norgate", tier="unadj")
    store.write_bars("ES", frame([110.0, 120.0, 126.0, 132.0], delivery),
                     domain=FUT, source="norgate", tier="backadj")

    out = bars.get_bars("ES", "propadj")
    assert out["Close"].pct_change().iloc[2] == pytest.approx(126.0 / 120.0 - 1)
    assert list(out["Close"])[-2:] == pytest.approx([126.0, 132.0])


# ── The half-stored symbol ────────────────────────────────────────────────
@pytest.mark.parametrize("present,missing", [("backadj", "unadj"),
                                             ("unadj", "backadj")])
def test_propadj_with_one_stored_tier_raises_and_names_the_missing_one(
        tmp_store, present, missing):
    """The failure the both-tiers-or-neither rule exists to make loud. Returning
    an empty frame here would read as 'no data for this symbol' when the truth is
    'the producer half-finished'."""
    store.write_bars("ES", frame([1, 2, 3]), domain=FUT, source="norgate", tier=present)

    with pytest.raises(FileNotFoundError) as e:
        bars.get_bars("ES", "propadj")
    assert missing in str(e.value)
    assert "BOTH" in str(e.value)


def test_a_symbol_absent_everywhere_still_reads_as_empty(tmp_store):
    """Absent is not an error — only half-present and wrong-vendor are."""
    assert bars.get_bars("ES", "propadj").empty
    assert bars.get_bars("ES", "backadj").empty


def test_missing_under_this_vendor_but_present_under_another_names_it(tmp_store):
    store.write_bars("ES", frame([1, 2, 3]), domain=FUT, source="yfinance",
                     tier="backadj")
    with pytest.raises(FileNotFoundError) as e:
        bars.get_bars("ES", "backadj", source="norgate")
    assert "yfinance" in str(e.value)


# ── Domain / tier vocabulary ──────────────────────────────────────────────
def test_asking_for_an_equity_tier_on_a_futures_symbol_names_the_right_ones():
    with pytest.raises(ValueError) as e:
        bars.get_bars("ES", "total")
    assert "backadj" in str(e.value) and "equities adjustment" in str(e.value)


def test_the_equity_adjuster_points_a_futures_tier_at_the_right_door():
    """adjust() takes one frame, so it cannot express propadj. The message has to
    say where to go instead of listing the equity tiers and stopping."""
    with pytest.raises(ValueError) as e:
        adjust(frame([1, 2, 3]), "propadj")
    assert "get_bars" in str(e.value)


def test_stored_and_derived_tiers_partition_each_domain():
    """Every declared tier is either stored or derived, and none is both —
    otherwise a producer and a reader can disagree about who owns it."""
    for domain, tiers in DOMAIN_TIERS.items():
        stored = {t for t in STORED_TIERS[domain] if t}
        derived = set(DERIVED_TIERS[domain])
        assert stored | derived == set(tiers)
        assert not (stored & derived)


# ── Registry ──────────────────────────────────────────────────────────────
def test_every_futures_symbol_is_norgate_served_with_an_ampersand_symbol():
    """Two mistakes this catches. An unset `norgate:` defaults to the internal
    name, and 'ES' is not a Norgate symbol — '&ES' is. An unset `yahoo:` defaults
    the same way and would resolve a futures read to an equity ticker."""
    futures = [s for s in registry.all_symbols() if s.domain == "futures"]
    assert len(futures) >= 45
    for s in futures:
        assert s.norgate and s.norgate.startswith("&"), s.internal
        assert s.yahoo is None, s.internal
        assert registry.resolve_source(s) == "norgate", s.internal


def test_futures_and_equity_symbols_never_collide():
    """The registry raises on a duplicate internal symbol, so a collision is a
    hard import failure rather than a silent domain mix-up. Assert the property
    directly so the reason survives."""
    seen = {}
    for s in registry.all_symbols():
        assert s.internal not in seen, s.internal
        seen[s.internal] = s.domain


# ── Provider ──────────────────────────────────────────────────────────────
def test_provider_name_matches_the_registry_vocabulary():
    """The source is a store PATH component AND what `resolve_source` returns."""
    assert nprov.NAME in registry.PRICE_SOURCES
    assert nprov.DOMAIN in registry.DOMAINS


def test_provider_writes_exactly_the_tiers_the_domain_declares():
    """The provider's loop and the store's layout must agree about which tiers
    exist, or a consumer asks for one nothing wrote."""
    assert set(stored_tiers_for(nprov.DOMAIN)) == {"backadj", "unadj"}


def test_backadj_uses_the_ccb_suffix_and_unadj_does_not():
    """Norgate selects continuous adjustment by SYMBOL SUFFIX, not by a kwarg.
    Fetching the base symbol for backadj silently returns the unadjusted series."""
    assert nprov._norgate_symbol("ES", "backadj") == "&ES_CCB"
    assert nprov._norgate_symbol("ES", "unadj") == "&ES"


def test_targets_are_futures_only_and_unknown_symbols_raise():
    names = [s.internal for s in nprov._targets()]
    assert "ES" in names and "SPY" not in names
    assert [s.internal for s in nprov._targets(["ES", "GC"])] == ["ES", "GC"]
    assert nprov._targets(["SPY"]) == []
    with pytest.raises(KeyError):
        nprov._targets(["NOT_A_SYMBOL"])


def test_roll_gap_check_flags_an_unadjusted_series_where_backadj_was_expected():
    """The sanity check that catches a dropped _CCB suffix. Self-calibrating, so
    it works on products with very different spread magnitudes."""
    rng = np.random.default_rng(0)
    n, seg = 600, 50          # 12 segments -> 11 rolls, over the >=8 the check needs
    closes = 100 + np.cumsum(rng.normal(0, 0.5, n))
    delivery = [f"seg{i // seg}" for i in range(n)]
    assert not nprov._check_roll_gaps("ES", frame(list(closes), delivery=delivery))

    gapped = closes.copy()
    for r in range(seg, n, seg):
        gapped[r:] += 40.0    # an unstitched calendar spread at every roll
    assert nprov._check_roll_gaps("ES", frame(list(gapped), delivery=delivery))


def test_volume_passthrough_flags_itself_as_raw():
    """Products with no individual contracts (crypto, some ICE softs) get
    front-month volume. It has to say so — a consumer comparing true market
    volume across symbols must be able to exclude them."""
    out = nprov._volume_passthrough(frame([1, 2, 3]))
    assert list(out["Volume_Source"]) == ["raw"] * 3
    assert list(out["Volume_Reconstructed"]) == [100.0] * 3


# ── Finals gate ───────────────────────────────────────────────────────────
def test_finals_ready_when_norgate_has_a_newer_settled_session():
    import datetime as dt
    ready, detail = nprov._finals_ready_by_date(dt.date(2026, 8, 7), dt.date(2026, 8, 6))
    assert ready and detail["norgate_last"] == "2026-08-07"

    assert not nprov._finals_ready_by_date(dt.date(2026, 8, 6), dt.date(2026, 8, 6))[0]
    assert not nprov._finals_ready_by_date(None, dt.date(2026, 8, 6))[0]
    assert nprov._finals_ready_by_date(dt.date(2026, 8, 7), None)[0]


def test_finals_quorum_requires_every_reference_symbol():
    """One lagging reference must not green-light a partial capture."""
    import datetime as dt
    new, old = dt.date(2026, 8, 7), dt.date(2026, 8, 6)
    assert nprov._finals_ready_quorum({"ES": new, "CL": new}, {"ES": old, "CL": old})[0]
    assert not nprov._finals_ready_quorum({"ES": new, "CL": old},
                                          {"ES": old, "CL": old})[0]


# ── Contract specs ────────────────────────────────────────────────────────
def test_scoped_metadata_upsert_preserves_unlisted_markets(tmp_store):
    """Specs share ONE table, so a scoped refresh that wrote instead of upserting
    would drop every market outside the request."""
    store.write_metadata(pd.DataFrame([{"Symbol": "ES", "Point Value": 50},
                                       {"Symbol": "GC", "Point Value": 100}]),
                         source="norgate")
    store.upsert_metadata(pd.DataFrame([{"Symbol": "ES", "Point Value": 5}]),
                          source="norgate")

    specs = store.read_metadata().set_index("Symbol")["Point Value"].to_dict()
    assert specs == {"ES": 5, "GC": 100}


# ── Pinning ───────────────────────────────────────────────────────────────
def test_pinning_a_futures_symbol_covers_both_stored_tiers(tmp_store):
    """propadj is derived from both, so pinning one would let the other drift and
    still verify green — under a study quoting a volatility number."""
    from marketdata import pin

    for tier in ("backadj", "unadj"):
        store.write_bars("ES", frame([1, 2, 3]), domain=FUT, source="norgate", tier=tier)

    snap = pin.build_snapshot(["ES"])
    assert sorted(snap["symbols"]) == ["ES_backadj", "ES_unadj"]
    assert pin.verify_snapshot(snap)[0]

    # A tier moving under the study is drift, and it has to be named.
    store.write_bars("ES", frame([1, 2, 3, 4]), domain=FUT, source="norgate", tier="unadj")
    ok, problems = pin.verify_snapshot(snap)
    assert not ok
    assert any("ES_unadj.n_rows" in p for p in problems)


def test_unscoped_pin_covers_a_mixed_domain_store(tmp_store):
    """The regression that motivated the change: an unscoped --pin derived its
    symbol list from manifest keys, so futures arrived as 'ES_backadj' and were
    then looked up as if that were a symbol."""
    from marketdata import pin

    store.write_bars("SPY", frame([1, 2, 3]), domain="equities", source="yfinance")
    for tier in ("backadj", "unadj"):
        store.write_bars("ES", frame([1, 2, 3]), domain=FUT, source="norgate", tier=tier)

    snap = pin.build_snapshot()
    assert sorted(snap["symbols"]) == ["ES_backadj", "ES_unadj", "SPY"]
    assert pin.verify_snapshot(snap)[0]


def test_a_v1_snapshot_still_verifies(tmp_store):
    """Old snapshots are evidence attached to published studies. They key on a
    plain symbol and record no tier, and that has to keep meaning what it meant."""
    from marketdata import pin, provenance

    store.write_bars("SPY", frame([1, 2, 3]), domain="equities", source="yfinance")
    p = provenance("SPY")
    v1 = {"snapshot_version": 1, "captured_at": "2026-01-01T00:00:00Z",
          "symbols": {"SPY": {"n_rows": p.n_rows, "first_date": p.first_date,
                              "last_date": p.last_date, "source": p.source,
                              "updated_at": p.updated_at, "domain": "equities"}}}
    assert pin.verify_snapshot(v1)[0]


def test_provenance_reports_each_futures_tier_separately(tmp_store):
    from marketdata import provenance

    store.write_bars("ES", frame([1, 2, 3]), domain=FUT, source="norgate", tier="backadj")
    store.write_bars("ES", frame([1, 2]), domain=FUT, source="norgate", tier="unadj")

    assert provenance("ES").tier == "backadj"        # domain default
    assert provenance("ES").n_rows == 3
    assert provenance("ES", tier="unadj").n_rows == 2
    assert "backadj" in provenance("ES").describe()


# ── Packaging ─────────────────────────────────────────────────────────────
def test_the_norgate_extra_is_declared():
    """A provider whose vendor package nothing installs is a provider nobody can run.

    This shipped missing: the futures provider landed with no `norgate` extra, so
    `--domain futures` on the Windows producer stopped at the import guard with the
    package never having been pulled. The guard did its job; the packaging had not.

    Read as text rather than parsed: `tomllib` is stdlib only from 3.11 and this
    package supports 3.10, so parsing would skip the check on the floor version —
    the one most likely to be a stale producer environment.
    """
    import re
    from pathlib import Path

    pyproject = (Path(__file__).resolve().parents[1] / "pyproject.toml").read_text()
    declared = re.search(r"^norgate\s*=\s*\[([^\]]*)\]", pyproject, re.M)
    assert declared, (
        "providers/norgate.py imports norgatedata, but no extra installs it")
    assert "norgatedata" in declared.group(1)


def test_the_missing_package_message_names_the_extra_that_fixes_it():
    """Two different problems with two different fixes: a missing package is an
    install away on Windows, and unfixable anywhere else. The message has to
    separate them or it sends the Windows producer to --domain equities."""
    import builtins

    real_import = builtins.__import__

    def no_norgatedata(name, *a, **kw):
        if name == "norgatedata":
            raise ImportError("No module named 'norgatedata'")
        return real_import(name, *a, **kw)

    builtins.__import__ = no_norgatedata
    try:
        with pytest.raises(RuntimeError) as e:
            nprov._require_norgate_service()
    finally:
        builtins.__import__ = real_import

    msg = str(e.value)
    assert "[norgate]" in msg          # the fix, for the box that can be fixed
    assert "Windows" in msg            # and why it is not the fix anywhere else


# ── Reconstructed volume, consumer side ───────────────────────────────────
def _recon_frame():
    df = frame([1.0, 2.0, 3.0])
    df["Volume"] = [100.0, 200.0, 300.0]
    df["Volume_Reconstructed"] = [150.0, 250.0, float("nan")]
    df["Volume_Source"] = ["reconstructed", "reconstructed", "raw"]
    return df


def test_volume_defaults_to_front_month(tmp_store):
    """The pre-existing shape. A caller that never heard of the parameter keeps
    getting Norgate's continuous front-month volume."""
    store.write_bars("ES", _recon_frame(), domain=FUT, source="norgate", tier="backadj")
    assert list(bars.get_bars("ES", "backadj")["Volume"]) == [100.0, 200.0, 300.0]


def test_reconstructed_volume_is_served_with_a_per_row_fallback(tmp_store):
    """The producer writes the columns; this is the switch that serves them.
    npf's ml/labels.py passes `volume=` through, so without it a repointed call
    raises TypeError rather than returning the wrong number — but it still does
    not work."""
    store.write_bars("ES", _recon_frame(), domain=FUT, source="norgate", tier="backadj")
    out = bars.get_bars("ES", "backadj", volume="reconstructed")

    # Row 3 could not be reconstructed, so it falls back to front-month...
    assert list(out["Volume"]) == [150.0, 250.0, 300.0]
    # ...and says so, which is what lets a consumer exclude it.
    assert list(out["Volume_Source"]) == ["reconstructed", "reconstructed", "raw"]


def test_a_store_predating_reconstruction_degrades_to_raw_and_says_so(tmp_store):
    store.write_bars("ES", frame([1.0, 2.0]), domain=FUT, source="norgate", tier="backadj")
    out = bars.get_bars("ES", "backadj", volume="reconstructed")
    assert list(out["Volume_Source"]) == ["raw", "raw"]
    assert list(out["Volume"]) == [100.0, 100.0]


def test_reconstructed_volume_is_refused_on_equities(tmp_store):
    """It sums two expiries. An equity has none, and silently returning front-month
    would answer a question that was not asked."""
    store.write_bars("SPY", frame([1.0]), domain="equities", source="yfinance")
    with pytest.raises(ValueError, match="futures concept"):
        bars.get_bars("SPY", "split", volume="reconstructed")


def test_an_unknown_volume_series_is_refused(tmp_store):
    store.write_bars("ES", frame([1.0]), domain=FUT, source="norgate", tier="backadj")
    with pytest.raises(ValueError, match="front"):
        bars.get_bars("ES", "backadj", volume="whole_market")


def test_reconstructed_is_a_subset_not_whole_market(tmp_store):
    """The naming trap, pinned. `reconstructed` sums the two biggest expiries and
    is therefore SMALLER than front-month-plus-everything-else; `front` is what a
    whole-market denominator wants. crowdmon's volume.py refuses anything else for
    exactly this reason, and the test exists so the docstring cannot drift from it."""
    df = frame([1.0])
    df["Volume"] = [1000.0]
    df["Volume_Reconstructed"] = [520.0]      # ~natural gas's measured 0.52 ratio
    df["Volume_Source"] = ["reconstructed"]
    store.write_bars("NG", df, domain=FUT, source="norgate", tier="backadj")

    front = bars.get_bars("NG", "backadj")["Volume"].iloc[0]
    recon = bars.get_bars("NG", "backadj", volume="reconstructed")["Volume"].iloc[0]
    assert recon < front
