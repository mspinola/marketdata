"""Reproducer for docs/design/korea-positioning-scoping.md.

Checks whether TradingView's KRX symbols are usable as a KOSPI 200 price
series, and prints every figure the note cites: the timestamp conventions, the
one-session offset in the cash index, the corrected basis, and the roll located
from second-month volume.

RUN IT WITH:  python3 -I scripts/korea_tradingview_probe.py

WHY THE DATA IS PASTED IN. The feed has no HTTP API. It reaches this stack only
through a TradingView connector that a Claude session can call, the same
constraint that shaped providers/tradingview.py. So the payloads below are
pasted verbatim from three mcp-tv-get-ohlcv calls made on 2026-10-25
(symbol KRX:K2I1! / KRX:K2I2! / KRX:KOSPI200, interval 1D, date_from
2026-08-01, format=columns), which also makes this reproducible offline and by
anyone without connector access. Re-pull and replace the arrays to extend it.

WHAT IT ESTABLISHED. The cash index is labelled one session late: joined on the
published dates the index and the front future disagree by a mean 3.398 pp of
daily return, and pulling the index back one session gives 0.298 pp. A
two-session shift is as bad as none, so the offset is exactly one and the
futures symbol is the correctly dated one. Anything joining these two symbols
on their own labels takes the index close from the session AFTER the futures
bar, which is a one-day look-ahead.
"""
import datetime as dt


def kst(t):
    return (dt.datetime.fromtimestamp(t, dt.timezone.utc) + dt.timedelta(hours=9)).date()

def utc(t):
    return dt.datetime.fromtimestamp(t, dt.timezone.utc)

# KRX:K2I1!  front-month continuous
f1_t = [1785747600,1785834000,1785920400,1786006800,1786093200,1786352400,1786438800,1786525200,1786611600,1786698000,1787043600,1787130000,1787216400,1787302800,1787562000,1787648400,1787734800,1787821200,1787907600,1788166800,1788253200,1788339600,1788426000,1788512400,1788771600,1788858000,1788944400,1789030800,1789117200,1789376400,1789462800,1789549200,1789635600,1789722000,1789981200,1790067600,1790154000,1790586000,1790672400,1790758800,1790845200,1790931600,1791277200,1791363600,1791450000,1791795600,1791882000,1791968400,1792054800,1792141200,1792400400,1792486800,1792573200,1792659600,1792746000]
f1_c = [1000,1042.05,981.15,978.75,979,989.8,1035.3,1073.7,1098.9,1078.25,1016.25,1082.25,1099.7,1051.65,1063.6,1073.5,1089.85,1068,1067.85,1078.15,1029.15,1030,1052.5,1109.75,1098.4,1117.2,1112,1088.3,1048.4,1039.8,1061.15,1062.75,1092.45,1115.1,1112,1127.75,1088.9,1096.1,1091.5,1107.75,1113.2,1103,1078.75,1048.25,1008.25,944.8,944.2,861.3,881.05,893.25,861.05,893.05,857,863.3,856.45]
f1_v = [167237,146041,145190,128016,123714,115607,142205,154498,136403,177676,184481,211212,143958,139096,144684,131640,126195,111121,145445,122392,136815,146447,127765,147436,125701,112188,73378,128027,140144,128337,103868,129541,107515,97546,107266,113589,107079,123287,125282,126411,103680,106448,110477,142249,142476,195217,149670,229242,193420,167476,174723,167165,157719,181560,93963]

# KRX:K2I2!  second-month continuous (same timestamps)
f2_c = [1009.1,1051.15,987.15,987.1,988.15,1004.2,1040.35,1077.5,1105.8,1090.1,1020.5,1084.05,1102.8,1056.15,1064.15,1075.7,1091.2,1070.4,1070,1076.95,1030.1,1033.75,1058.95,1111,1100.2,1119,1101.55,1088,1039.9,1028.9,1051,1047.45,1079.05,1100.6,1105,1112.65,1081.4,1084.15,1077.5,1098.1,1104.95,1092,1068.75,1043.85,997.15,931,931.55,846.5,865.55,881.35,849.25,881.45,847.4,853.05,843.65]
f2_v = [44,58,40,50,32,23,33,66,77,92,151,101,354,178,126,111,92,69,281,176,211,270,1642,4112,13904,24344,13,33,29,15,22,8,11,15,10,22,11,2,6,11,14,9,69,6,222,30,30,130,32,94,103,165,98,52,18]

# KRX:KOSPI200  cash index, its own timestamps (00:00 UTC) and no volume
ix_t = [1785715200,1785801600,1785888000,1785974400,1786060800,1786320000,1786406400,1786492800,1786579200,1786665600,1787011200,1787097600,1787184000,1787270400,1787529600,1787616000,1787702400,1787788800,1787875200,1788134400,1788220800,1788307200,1788393600,1788480000,1788739200,1788825600,1788912000,1788998400,1789084800,1789344000,1789430400,1789516800,1789603200,1789689600,1789948800,1790035200,1790121600,1790553600,1790640000,1790726400,1790812800,1790899200,1791244800,1791331200,1791417600,1791763200,1791849600,1791936000,1792022400,1792108800,1792368000,1792454400,1792540800,1792627200,1792713600,1792972800]
ix_c = [986.72,1000.03,1038.59,982.92,974.73,977.84,987.4,1029.43,1071.24,1098.18,1082,1012.61,1080.98,1096.25,1054.01,1060.68,1071.16,1088.61,1065.7,1071.85,1075.3,1031.53,1032.82,1051.52,1105.19,1100.08,1114.61,1112.13,1090.22,1050.83,1042.46,1060.05,1058.27,1090.23,1112.16,1113.3,1126.12,1089.28,1088.15,1081.82,1104.48,1109.05,1095.83,1073.63,1044.52,1004.92,942.07,936.82,860.7,878.16,891.3,858.78,891.04,858.02,862.77,851.06]

print("== raw timestamp conventions")
print(f"futures first/last bar: {utc(f1_t[0])}  ..  {utc(f1_t[-1])}   (KST date {kst(f1_t[0])} .. {kst(f1_t[-1])})")
print(f"index   first/last bar: {utc(ix_t[0])}  ..  {utc(ix_t[-1])}   (KST date {kst(ix_t[0])} .. {kst(ix_t[-1])})")
print(f"futures stamps at {utc(f1_t[0]).time()} UTC, index stamps at {utc(ix_t[0]).time()} UTC")
print(f"a raw join on t would match {len(set(f1_t) & set(ix_t))} of {len(f1_t)} futures bars")

# Join on the KST session date instead.
fut = {kst(t): c for t, c in zip(f1_t, f1_c)}
fut2 = {kst(t): c for t, c in zip(f1_t, f2_c)}
futv2 = {kst(t): v for t, v in zip(f1_t, f2_v)}
idx = {kst(t): c for t, c in zip(ix_t, ix_c)}
both = sorted(set(fut) & set(idx))
print(f"\n== join on KST session date: {len(both)} matched sessions "
      f"(futures-only {sorted(set(fut) - set(idx))}, index-only {sorted(set(idx) - set(fut))})")

print("\n== the roll, located by second-month volume")
for d in both:
    if futv2[d] > 1000:
        print(f"  {d}  K2I2! volume {futv2[d]:>6}  front {fut[d]:>8}  second {fut2[d]:>8}  "
              f"spread {fut2[d] - fut[d]:+.2f}")

print("\n== basis (front future minus cash index), around the roll")
rows = [(d, fut[d] - idx[d]) for d in both]
for d, b in rows:
    flag = "  <-- 2nd-month volume spike" if futv2[d] > 1000 else ""
    print(f"  {d}  future {fut[d]:>8}  index {idx[d]:>8}  basis {b:+7.2f}{flag}")

print("\n== does the continuous series jump at the roll? front-month vs index daily returns")
print("   (a non-adjusted continuous series shows a one-day gap the index does not)")
for i in range(1, len(both)):
    d0, d1 = both[i - 1], both[i]
    rf = fut[d1] / fut[d0] - 1
    ri = idx[d1] / idx[d0] - 1
    gap = (rf - ri) * 100
    if abs(gap) > 0.30 or futv2[d1] > 1000:
        print(f"  {d1}  future {rf * 100:+6.2f}%  index {ri * 100:+6.2f}%  "
              f"difference {gap:+6.2f} pp{'   <-- roll week' if futv2[d1] > 1000 else ''}")

diffs = [abs((fut[both[i]] / fut[both[i - 1]] - 1) - (idx[both[i]] / idx[both[i - 1]] - 1)) * 100
         for i in range(1, len(both))]
print(f"\n  mean |future - index| daily return difference: {sum(diffs) / len(diffs):.3f} pp")
print(f"  max  |future - index| daily return difference: {max(diffs):.3f} pp")

print("\n\n== SHIFT TEST: is one of the two series off by a session?")
fut_dates = sorted(fut)
idx_dates = sorted(idx)
fr = {fut_dates[i]: fut[fut_dates[i]] / fut[fut_dates[i - 1]] - 1 for i in range(1, len(fut_dates))}
ir = {idx_dates[i]: idx[idx_dates[i]] / idx[idx_dates[i - 1]] - 1 for i in range(1, len(idx_dates))}

def compare(shift):
    """shift = how many index SESSIONS to pull the index back by before joining."""
    pairs = []
    for i in range(1, len(idx_dates)):
        j = i - shift
        if j < 1:
            continue
        d_i, d_f = idx_dates[i], idx_dates[j]
        if d_f in fr:
            pairs.append((ir[d_i], fr[d_f]))
    if not pairs:
        return None
    diffs = [abs(a - b) * 100 for a, b in pairs]
    return len(pairs), sum(diffs) / len(diffs), max(diffs)

for shift in (0, 1, 2):
    n, mean, mx = compare(shift)
    label = {0: "as labelled", 1: "index bar D holds session D-1", 2: "index bar D holds session D-2"}[shift]
    print(f"  shift {shift} ({label}): n={n}  mean |diff| {mean:.3f} pp  max |diff| {mx:.3f} pp")

print("\n== basis after correcting the one-session offset (index bar D -> futures session D-1)")
corrected = []
for i in range(1, len(idx_dates)):
    d_i, d_f = idx_dates[i], idx_dates[i - 1]
    if d_f in fut:
        corrected.append((d_f, fut[d_f], idx[d_i], fut[d_f] - idx[d_i]))
bs = [b for _, _, _, b in corrected]
print(f"  n={len(bs)}  mean basis {sum(bs) / len(bs):+.2f}  min {min(bs):+.2f}  max {max(bs):+.2f}")
print("  last 8 sessions:")
for d, f, x, b in corrected[-8:]:
    print(f"    session {d}  future {f:>8}  index {x:>8}  basis {b:+7.2f}  ({b / x * 100:+.3f}%)")
