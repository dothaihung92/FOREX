"""Exhaustive sweep of INDICATOR COMBINATIONS on the M5 strategy.

The project already swept 12 popular indicators one at a time as
confirmation filters (README "TradingView indicator sweep") and adopted
none. This goes the step further that was never done: every combination of
1, 2 and 3 filters, ranked by profit, to answer "which combination makes
the most money".

Method notes that make the answer trustworthy rather than flattering:

* Outcomes are precomputed per signal bar, then each combination re-walks
  the timeline selecting non-overlapping trades. Dropping a trade can free
  a later signal that was previously shadowed, so filters are not just a
  subset of the baseline trade list - that shortcut would misreport.
* Results are in R multiples (profit / initial risk), which is independent
  of lot rounding and account size.
* Everything is scored on train (2020-2022) AND test (2023-2025). A
  combination that wins on the combined period but not on both halves is
  a curve-fit, and the ranking makes that visible instead of hiding it.

Real Exness Standard costs throughout.
"""
import sys; sys.path.insert(0, '/home/user/FOREX')
from itertools import combinations
import numpy as np
import pandas as pd
from gold_bot.config import load_config
from gold_bot.strategy import generate_signals
from gold_bot.indicators import atr as atr_f, ema, stochastic

cfg = load_config('/home/user/FOREX/config/config.yaml')
df = pd.read_csv('/home/user/FOREX/data/XAUUSD_M5_real.csv')
df['time'] = pd.to_datetime(df['time'], utc=True)
df = df.set_index('time').sort_index()
base = generate_signals(df.copy(), cfg.strategy, cfg.sessions)

close, high, low = base['close'], base['high'], base['low']


# ---------------------------------------------------------------- indicators
def supertrend(d, period=10, mult=3.0):
    a = atr_f(d, period)
    hl2 = (d['high'] + d['low']) / 2
    ub, lb = hl2 + mult * a, hl2 - mult * a
    c = d['close'].to_numpy(); ubn, lbn = ub.to_numpy(), lb.to_numpy()
    n = len(c); dirn = np.ones(n, dtype=int)
    fub, flb = ubn.copy(), lbn.copy()
    for i in range(1, n):
        fub[i] = ubn[i] if (ubn[i] < fub[i-1] or c[i-1] > fub[i-1]) else fub[i-1]
        flb[i] = lbn[i] if (lbn[i] > flb[i-1] or c[i-1] < flb[i-1]) else flb[i-1]
        if c[i] > fub[i-1]: dirn[i] = 1
        elif c[i] < flb[i-1]: dirn[i] = -1
        else: dirn[i] = dirn[i-1]
    return pd.Series(dirn, index=d.index)


def psar(d, af0=0.02, afmax=0.2):
    h, l = d['high'].to_numpy(), d['low'].to_numpy()
    n = len(h); dirn = np.ones(n, dtype=int)
    sar = l[0]; ep = h[0]; af = af0
    for i in range(1, n):
        sar = sar + af * (ep - sar)
        if dirn[i-1] == 1:
            if l[i] < sar:
                dirn[i] = -1; sar = ep; ep = l[i]; af = af0
            else:
                dirn[i] = 1
                if h[i] > ep: ep = h[i]; af = min(af + af0, afmax)
        else:
            if h[i] > sar:
                dirn[i] = 1; sar = ep; ep = h[i]; af = af0
            else:
                dirn[i] = -1
                if l[i] < ep: ep = l[i]; af = min(af + af0, afmax)
    return pd.Series(dirn, index=d.index)


def cci(d, period=20):
    tp = (d['high'] + d['low'] + d['close']) / 3
    ma = tp.rolling(period).mean()
    md = (tp - ma).abs().rolling(period).mean()
    return (tp - ma) / (0.015 * md)


def williams_r(d, period=14):
    hh = d['high'].rolling(period).max(); ll = d['low'].rolling(period).min()
    return -100 * (hh - d['close']) / (hh - ll)


def hull_ma(s, period=20):
    half = max(1, period // 2); sq = max(1, int(np.sqrt(period)))
    wma = lambda x, p: x.rolling(p).apply(
        lambda w: np.dot(w, np.arange(1, p + 1)) / (p * (p + 1) / 2), raw=True)
    return wma(2 * wma(s, half) - wma(s, period), sq)


def vortex(d, period=14):
    tr = pd.concat([d['high'] - d['low'], (d['high'] - d['close'].shift()).abs(),
                    (d['low'] - d['close'].shift()).abs()], axis=1).max(axis=1)
    vp = (d['high'] - d['low'].shift()).abs().rolling(period).sum()
    vm = (d['low'] - d['high'].shift()).abs().rolling(period).sum()
    trs = tr.rolling(period).sum()
    return vp / trs, vm / trs


def dmi(d, period=14):
    up = d['high'].diff(); dn = -d['low'].diff()
    plus = np.where((up > dn) & (up > 0), up, 0.0)
    minus = np.where((dn > up) & (dn > 0), dn, 0.0)
    tr = pd.concat([d['high'] - d['low'], (d['high'] - d['close'].shift()).abs(),
                    (d['low'] - d['close'].shift()).abs()], axis=1).max(axis=1)
    atr_s = tr.ewm(alpha=1 / period, adjust=False).mean()
    pdi = 100 * pd.Series(plus, index=d.index).ewm(alpha=1 / period, adjust=False).mean() / atr_s
    mdi = 100 * pd.Series(minus, index=d.index).ewm(alpha=1 / period, adjust=False).mean() / atr_s
    return pdi, mdi


st = supertrend(base)
sar_dir = psar(base)
cci_v = cci(base)
wr = williams_r(base)
hull = hull_ma(close)
vip, vim = vortex(base)
pdi, mdi = dmi(base)
ao = ((high + low) / 2).rolling(5).mean() - ((high + low) / 2).rolling(34).mean()
kelt_mid = ema(close, 20)
stoch_k, stoch_d = stochastic(base)
ich_conv = (high.rolling(9).max() + low.rolling(9).min()) / 2
ich_base = (high.rolling(26).max() + low.rolling(26).min()) / 2
span_a = ((ich_conv + ich_base) / 2).shift(26)
span_b = ((high.rolling(52).max() + low.rolling(52).min()) / 2).shift(26)
cloud_top = pd.concat([span_a, span_b], axis=1).max(axis=1)
cloud_bot = pd.concat([span_a, span_b], axis=1).min(axis=1)
atr_exp = base['atr'] > base['atr'].rolling(cfg.strategy.atr_expansion_period).mean()

# Each filter: (long_ok, short_ok). Direction-aware, as a real filter must be.
FILTERS = {
    'SuperTrend':  (st > 0, st < 0),
    'PSAR':        (sar_dir > 0, sar_dir < 0),
    'CCI':         (cci_v > 0, cci_v < 0),
    'Ichimoku':    (close > cloud_top, close < cloud_bot),
    'HullMA':      (hull > hull.shift(1), hull < hull.shift(1)),
    'Vortex':      (vip > vim, vim > vip),
    'DMI':         (pdi > mdi, mdi > pdi),
    'AO':          (ao > 0, ao < 0),
    'Keltner':     (close > kelt_mid, close < kelt_mid),
    'Stoch':       (stoch_k > stoch_d, stoch_k < stoch_d),
    'WilliamsR':   ((wr > -80) & (wr < -20), (wr > -80) & (wr < -20)),
    'ATRexp':      (atr_exp, atr_exp),
}
NAMES = list(FILTERS)

# ------------------------------------------------------- precompute outcomes
o = base['open'].to_numpy(); h = base['high'].to_numpy()
l = base['low'].to_numpy(); c = base['close'].to_numpy()
atr = base['atr'].to_numpy(); sig = base['signal'].to_numpy()
SPREAD = cfg.backtest.spread_points * 0.01
SLIP = cfg.backtest.slippage_points * 0.01
SL_M, TP_M = cfg.strategy.atr_sl_mult, cfg.strategy.atr_tp_mult
n = len(c)

sig_idx = np.flatnonzero((sig != 0) & ~np.isnan(atr))
exit_at = {}; r_mult = {}
for i in sig_idx:
    d = int(sig[i])
    entry = c[i] + d * (SPREAD / 2 + SLIP)
    sl_dist = SL_M * atr[i]
    if sl_dist <= 0:
        continue
    sl_p = entry - d * sl_dist; tp_p = entry + d * (TP_M * atr[i])
    j = i + 1; ex = None
    while j < n:
        if ((l[j] <= sl_p) if d == 1 else (h[j] >= sl_p)): ex = sl_p - d * SLIP; break
        if ((h[j] >= tp_p) if d == 1 else (l[j] <= tp_p)): ex = tp_p - d * SLIP; break
        j += 1
    if ex is None:
        continue
    exit_at[i] = j
    r_mult[i] = d * (ex - entry) / sl_dist

usable = np.array([i for i in sig_idx if i in r_mult])
print(f"baseline signals with a resolved outcome: {len(usable)}")

mask_cache = {}
for name, (lg, sh) in FILTERS.items():
    lgn, shn = lg.to_numpy(), sh.to_numpy()
    mask_cache[name] = np.array([bool(lgn[i]) if sig[i] == 1 else bool(shn[i]) for i in usable])

R = np.array([r_mult[i] for i in usable])
EX = np.array([exit_at[i] for i in usable])
IDX = usable


def evaluate(mask, lo, hi):
    """Walk the timeline, take the next allowed non-overlapping signal."""
    rs = []
    busy_until = -1
    for k in range(len(IDX)):
        i = IDX[k]
        if i < lo or i >= hi or i <= busy_until or not mask[k]:
            continue
        rs.append(R[k]); busy_until = EX[k]
    if not rs:
        return {'trades': 0, 'pf': 0.0, 'net_r': 0.0, 'win': 0.0}
    a = np.array(rs); w = a[a > 0].sum(); ls = -a[a <= 0].sum()
    return {'trades': len(a), 'pf': (w / ls) if ls > 0 else float('inf'),
            'net_r': a.sum(), 'win': 100 * (a > 0).mean()}


SPLIT = int(base.index.searchsorted(pd.Timestamp('2023-01-01', tz='UTC')))
ALL = np.ones(len(IDX), bool)

results = []
for size in (0, 1, 2, 3):
    for combo in combinations(NAMES, size):
        m = ALL.copy()
        for nm in combo:
            m = m & mask_cache[nm]
        full = evaluate(m, 0, n)
        if full['trades'] < 20:      # below this, PF is noise - excluded from ranking
            continue
        tr = evaluate(m, 0, SPLIT); te = evaluate(m, SPLIT, n)
        results.append({
            'combo': ' + '.join(combo) if combo else 'BASELINE (no extra filter)',
            'full': full, 'train': tr, 'test': te,
        })

print(f"combinations evaluated with >=20 trades: {len(results)}\n")


def line(r):
    f, tr, te = r['full'], r['train'], r['test']
    pf = lambda x: '  inf' if x == float('inf') else f"{x:6.2f}"
    return (f"{r['combo']:<34} {f['trades']:>6} {f['win']:>6.1f} {f['net_r']:>+8.1f}"
            f" {pf(f['pf'])} | {tr['trades']:>4} {pf(tr['pf'])} | {te['trades']:>4} {pf(te['pf'])}")


HDR = (f"{'combination':<34} {'trades':>6} {'win%':>6} {'net R':>8} {'PF':>6} |"
       f" {'trN':>4} {'trPF':>6} | {'teN':>4} {'tePF':>6}")

print("=== TOP 15 by total profit (net R) over the full period ===")
print(HDR)
for r in sorted(results, key=lambda x: -x['full']['net_r'])[:15]:
    print(line(r))

print("\n=== TOP 15 by profit factor over the full period ===")
print(HDR)
for r in sorted(results, key=lambda x: -x['full']['pf'])[:15]:
    print(line(r))

print("\n=== Survivors: beat baseline PF in BOTH train and test (the only honest bar) ===")
b = next(r for r in results if r['combo'].startswith('BASELINE'))
print(f"baseline: train PF={b['train']['pf']:.2f} (n={b['train']['trades']}), "
      f"test PF={b['test']['pf']:.2f} (n={b['test']['trades']})")
surv = [r for r in results
        if r['train']['pf'] > b['train']['pf'] and r['test']['pf'] > b['test']['pf']
        and r['train']['trades'] >= 20 and r['test']['trades'] >= 20]
if surv:
    print(HDR)
    for r in sorted(surv, key=lambda x: -x['full']['net_r']):
        print(line(r))
else:
    print("NONE - no combination beats the baseline in both regimes with a usable sample.")

print("\n=== Reality check: does the best-on-train combo hold up on test? ===")
bt = max(results, key=lambda x: x['train']['pf'])
print(f"best on TRAIN: {bt['combo']}")
print(f"  train PF={bt['train']['pf']:.2f} (n={bt['train']['trades']})  ->  "
      f"test PF={bt['test']['pf']:.2f} (n={bt['test']['trades']})")
