# marketdata design notes

Why this repo exists, what was verified rather than assumed, and where the known
holes are.

## Why not inside cotdata

Two hard reasons, not preference.

`cotdata.registry.load_registry` raises on any symbol missing `cftc_code`
(`cotdata/src/cotdata/registry.py:130`). Equities and ETFs have no COT report, so
adding them would mean relaxing the invariant that makes that registry
trustworthy for the four repos already reading it.

And the workspace dependency direction runs `cotdata -> cotmetrics ->
cot-analyzer`. An equity bar layer importing cotdata points the arrow backwards.

The store machinery worth reusing is roughly 150 lines (atomic parquet write,
manifest touch, store-root guard). It is cheaper to reimplement than to couple.
Extract a shared core only if a third consumer appears.

**Store roots may share a parent folder, but never a manifest.**
`cotdata.store._touch_manifest` is a read-modify-write, so two producers writing
one `manifest.json` will eventually lose an entry.

## What transferred from cotdata

- Registry as the single source of symbol identity. Internal symbol is the key
  everywhere, vendor tickers are attributes.
- Producer writes, consumers only read, reads never hit the network.
- Vendor resolution as a deployment choice (`resolve_source`), not a baked
  per-symbol fact. Kept even though yfinance is the only provider today, because
  Norgate covers US equities and ETFs and RealTest reads Norgate natively.
- Atomic writes plus a manifest carrying provenance and a schema version.
- Adjustment derived on read, never stored. Same posture as cotdata's `propadj`.

## Verified yfinance behaviour

Probed live against **yfinance 1.5.2 on 2026-07-25**. Every claim below was
measured, not read off a docstring.

`Ticker.history(period="max", auto_adjust=False, actions=True)` returns:

```
Open, High, Low, Close, Adj Close, Volume, Dividends, Stock Splits, Capital Gains
```

### 1. `auto_adjust=False` OHLC is already split-adjusted

It is **not** as-traded. AAPL's close on 2020-08-28, the last session before the
4:1 split, comes back as 124.807503. The tape price was 499.23.

As-traded is recoverable by un-applying splits, and it reconstructs exactly:

```
                Close  as_traded_close      Volume  as_traded_vol  Splits
2020-08-28  124.807503       499.230011   187630000     46907500.0     0.0
2020-08-31  129.039993       129.039993   225702700    225702700.0     4.0
```

Holds back through the 1987 2:1 split as well (79.00 into 41.50). Volume divides
by the same factor.

### 2. `Adj Close` is fully reconstructible, with NO split term

Because `Close` is already split-adjusted, folding splits into the dividend
back-adjustment double-applies them. Max relative error against Yahoo's own
`Adj Close`, full history:

| Symbol | Rows | Splits | Divs | no split term | with split term |
|---|---|---|---|---|---|
| AAPL | 11,495 | 5 | 91 | 9.49e-07 | 9.96e-01 |
| MSFT | 10,169 | 9 | 90 | 1.04e-06 | 9.97e-01 |
| KO | 16,248 | 8 | 258 | 3.35e-05 | 9.97e-01 |
| TLT | 6,035 | 0 | 286 | 1.58e-06 | 1.58e-06 |
| SPY | 8,428 | 0 | 135 | 1.13e-06 | 1.13e-06 |

**TLT and SPY have never split, so they pass either way.** A pin set built only
from ETFs would have shipped the bug. `tests/test_pin.py` asserts the pin set
actually contains split-heavy names, and separately asserts that the buggy form
fails loudly.

KO is the loose one at 3.35e-05: 16k bars back to 1962 and 258 dividends, so
Yahoo's own stored `Adj Close` accumulates rounding across a long multiplicative
chain. `PIN_TOL` is 1e-4, set above KO rather than tuned down to exclude it.

The dividend factor uses the **prior** bar's close as the denominator. That is the
convention that reproduces Yahoo.

### 3. `auto_adjust=True` adds nothing

Its `Close` is bit-identical to `auto_adjust=False`'s `Adj Close` (checked on
TLT). No extra information in either mode.

### 4. Why total return is not optional

TLT over its full history:

```
price return  (split tier):   +2.1%
total return  (total tier):  +132.3%
```

Any study holding a bond ETF across an ex-date on the price series is measuring
the wrong thing by two orders of magnitude.

## Vendor is a path component

`bars/<domain>/<source>/<symbol>.parquet`, not `bars/<symbol>.parquet`.

The domain is the outer level because it sets the adjustment axis: futures adjust by roll
splicing and equities by corporate actions, sharing no vocabulary. `adjust.DOMAIN_TIERS`
declares the futures tiers before a futures provider exists, so asking for `backadj` on an
equity gets a message naming the right tiers rather than "unknown tier".

cotdata gets away with keeping the vendor in the manifest only, because its providers
serve largely disjoint symbols (Norgate for nearly everything, Yahoo for the MSCI proxies
and, on a databento host, the softs). Here the overlap is total: Yahoo and Norgate both
carry SPY, TLT and QQQ. Worse, they do not store the same frame. Yahoo stores
split-adjusted OHLCV plus dated actions and derives the tiers, while Norgate's tiers are
vendor-computed. Same filename, different columns.

Three consequences worth stating:

- A provider's `NAME` **must** be a member of `registry.PRICE_SOURCES`, because the same
  string is both the store path component and what `resolve_source` returns. A provider
  that names itself differently makes a default read look in a directory nothing wrote to.
  Pinned by `test_provider_name_matches_the_registry_vocabulary`.
- Reading a symbol that is missing under the resolved vendor but present under another
  raises and names the alternatives. Returning empty would read as "no data", and falling
  back would blend two vendors across a re-run, which ADR-0006 forbids.
- Storage duplicates for overlapping symbols. Irrelevant at daily resolution, where a full
  SPY history back to 1993 is a few thousand rows.

## Known holes

**Capital Gains looks unpopulated.** The column exists but fired zero times across
TLT, VFINX, PRHSX, and FCNTX, including two funds with 11,735 rows each. Four
tickers is not proof it never fires. `include_capital_gains` is off by default and
documented as untrusted. For a fund that genuinely distributes capital gains, the
total-return tier would understate with no error raised.

**Spinoffs and return-of-capital are not represented at all.** Neither a split nor
a dividend, so they surface as an unexplained gap. Yahoo does not model them.

**Restatement is detected, not eliminated.** Yahoo can revise or backfill a
dividend. Storing dated actions means a re-fetch diff shows the change instead of
silently shifting the back-history. Same posture as the Norgate note in the
workspace `CLAUDE.md`: the recompute detects events, it does not absorb them.

**No point-in-time universe.** yfinance serves only currently-listed symbols. It
cannot provide delisted securities or historical index membership, so any universe
assembled from it is survivorship-biased by construction. This is a hard ceiling,
not a bug to work around. Scope to ETFs and hand-named liquid equities, and record
the limitation in the store rather than a README footnote.

Norgate US Stocks fixes the last two, but only at **Platinum** and above. Silver
and Gold carry currently-listed securities only, so they are survivorship-biased
in exactly the same way yfinance is. Silver's 10-year history cap would also be
too short for a monthly-frequency study.

**Pin the yfinance version.** Sibling virtualenvs in the same working tree were
found carrying 1.4.1 and 1.5.2 side by side. Since the meaning of `auto_adjust`
has shifted across versions, two versions in one workspace is a live
reproducibility hazard. The floor here is `>=1.5.2`.

## Not built yet

The RealTest CSV exporter. Deliberately deferred: RealTest's data import layout
needs verifying against the current version first, the same way the trade-export
columns in `crucible/README.md` were. Writing it speculatively would bake in a
guess.

The intended shape is a **pure projection**: parquet stays authoritative, the CSV
is regenerated and never a second source of truth, and the export records which
store state it was built from so an `.rts` run and a crucible verdict can be shown
to have been fed the same bars.
