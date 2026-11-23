"""Option 3: add a price-action filter to the validated strategy.

Each of the strategy's signals is kept or dropped according to the candle it
fires on. A filter is adopted only if it improves BOTH 2020-22 and 2023-25,
AND beats dropping the same share of signals at random (permutation test).

The permutation test needs thousands of runs, so trades are simulated by a
fast replica of backtester.py (same entry, stop, target, trailing stop, full
costs, one position at a time). The replica is checked against the real
backtester before anything else is trusted; finalists are re-run on the real
backtester at the end.
"""
import sys, time; sys.path.insert(0, '/home/user/FOREX')
import numpy as np, pandas as pd
from gold_bot.backtester import run_backtest
from gold_bot.config import load_config
from gold_bot.strategy import generate_signals

cfg = load_config('/home/user/FOREX/config/config.yaml')
df = pd.read_csv('/home/user/FOREX/data/XAUUSD_M5_real.csv'); df['time'] = pd.to_datetime(df['time'], utc=True)
df = df.set_index('time').sort_index()
sig = generate_signals(df.copy(), cfg.strategy, cfg.sessions)
SPLIT = pd.Timestamp('2023-01-01', tz='UTC')

O, H, L, C = (sig[k].to_numpy() for k in ('open', 'high', 'low', 'close'))
A = sig['atr'].to_numpy(); E50 = sig['ema_fast'].to_numpy()
S = sig['signal'].to_numpy()
SLM, TPM, TRAIL = cfg.strategy.atr_sl_mult, cfg.strategy.atr_tp_mult, cfg.risk.trailing_atr_mult
SPREAD = cfg.backtest.spread_points * 0.01; SLIP = cfg.backtest.slippage_points * 0.01
n = len(C)


# ------------------------------------------------------------ fast replica
def simulate(i, d):
    """One trade exactly as backtester.py runs it. Returns (R, exit bar)."""
    entry = C[i] + d * (SPREAD / 2 + SLIP)
    sl = entry - d * SLM * A[i]; tp = entry + d * TPM * A[i]
    for j in range(i + 1, n):
        if d == 1:
            if L[j] <= sl: px = sl; break
            if H[j] >= tp: px = tp; break
            sl = max(sl, C[j] - TRAIL * A[j]) if np.isfinite(A[j]) else sl
        else:
            if H[j] >= sl: px = sl; break
            if L[j] <= tp: px = tp; break
            sl = min(sl, C[j] + TRAIL * A[j]) if np.isfinite(A[j]) else sl
    else:
        return np.nan, n - 1
    px -= d * (SLIP + SPREAD / 2)
    return d * (px - entry) / (SLM * A[i]), j


sig_idx = np.flatnonzero((S != 0) & np.isfinite(A) & (A > 0))
outcome = {i: simulate(i, int(S[i])) for i in sig_idx}


def take(keep_mask):
    """One position at a time over the kept signals -> list of (bar, R)."""
    out, busy = [], -1
    for i in sig_idx[keep_mask]:
        if i >= busy:
            r, j = outcome[i]
            if np.isfinite(r):
                out.append((i, r)); busy = j
    return out


def stats(trades):
    if not trades:
        return dict(n=0, meanR=np.nan, pf=np.nan, win=np.nan)
    r = np.array([x[1] for x in trades])
    lose = -r[r <= 0].sum()
    return dict(n=len(r), meanR=r.mean(), pf=r[r > 0].sum() / lose if lose > 0 else np.inf, win=100 * (r > 0).mean())


def split(trades):
    a = [t for t in trades if sig.index[t[0]] < SPLIT]
    b = [t for t in trades if sig.index[t[0]] >= SPLIT]
    return stats(trades), stats(a), stats(b)


ALL = np.ones(len(sig_idx), bool)
base_all, base_a, base_b = split(take(ALL))
real = run_backtest(sig, cfg.strategy, cfg.risk, cfg.backtest)
rp = np.array([t.pnl for t in real.trades if t.exit_time is not None])
print(f"replica check - fast sim: {base_all['n']} trades, win {base_all['win']:.1f}%, PF {base_all['pf']:.2f} | "
      f"real backtester: {len(rp)} trades, win {100*(rp>0).mean():.1f}%, PF {rp[rp>0].sum()/-rp[rp<=0].sum():.2f}\n")

# ------------------------------------------------------- price-action features
pos = np.arange(n)
m15 = sig.resample('15min', label='left', closed='left').agg({'open': 'first', 'close': 'last', 'high': 'max', 'low': 'min'})
cnt = sig['close'].resample('15min', label='left', closed='left').count()
m15 = m15[cnt >= 3].dropna()
m15_avail = pd.DataFrame({'end': m15.index + pd.Timedelta('15min'),
                          'm15_dir': np.sign(m15['close'] - m15['open']).values})
q = pd.DataFrame({'t': sig.index + pd.Timedelta('5min')})
M15DIR = pd.merge_asof(q, m15_avail, left_on='t', right_on='end', direction='backward')['m15_dir'].to_numpy()

feat = {}
for k, i in enumerate(sig_idx):
    d = int(S[i])
    o, h, l, c = (O[i], H[i], L[i], C[i]) if d == 1 else (-O[i], -L[i], -H[i], -C[i])
    o1, c1 = (O[i - 1], C[i - 1]) if d == 1 else (-O[i - 1], -C[i - 1])
    rng = h - l if h > l else 1e-9
    body = c - o
    lw = (min(o, c) - l) / rng; uw = (h - max(o, c)) / rng
    lows3 = L[i - 2:i + 1].min() if d == 1 else -H[i - 2:i + 1].max()
    e50 = E50[i] if d == 1 else -E50[i]
    feat[i] = dict(
        with_body=body > 0,
        strong=body > 0 and body / rng >= 0.6,
        close_top=(c - l) / rng >= 0.7,
        pin=lw >= 0.5 and (c - l) / rng >= 0.6,
        engulf=body > 0 and (c1 - o1) < 0 and c >= max(o1, c1) and o <= min(o1, c1),
        two_bar_reversal=(c1 - o1) < 0 and body > 0,
        upper_wick_bad=uw >= 0.5,                      # rejection AGAINST the trade
        touched_ema50=lows3 <= e50,
        not_extended=(c - e50) <= 1.0 * A[i],
        m15_with=(M15DIR[i] * d) > 0,
    )
F = pd.DataFrame.from_dict(feat, orient='index').loc[sig_idx]

FILTERS = {
    'candle closes with trend':        F.with_body,
    'strong body with trend (>=60%)':  F.strong,
    'closes in top 30% of range':      F.close_top,
    'pin bar':                         F.pin,
    'engulfing':                       F.engulf,
    'pin OR engulfing':                F.pin | F.engulf,
    '2-bar reversal (against->with)':  F.two_bar_reversal,
    'pullback touched EMA50 (3 bars)': F.touched_ema50,
    'not extended (<=1 ATR from EMA50)': F.not_extended,
    'last M15 candle with trend':      F.m15_with,
    'VETO long wick against trade':    ~F.upper_wick_bad,
    'with-body AND close top 30%':     F.with_body & F.close_top,
    'touched EMA50 AND closes with trend': F.touched_ema50 & F.with_body,
}

rng = np.random.default_rng(0)
print(f"{'filter':<38} {'kept':>5} | {'ALL n  meanR  PF':>17} | {'2020-22 meanR PF':>17} | {'2023-25 meanR PF':>17} | {'both?':>5} {'p(random)':>9}")
print(f"{'BASELINE (no filter)':<38} {len(sig_idx):>5} | {base_all['n']:>4} {base_all['meanR']:+.3f} {base_all['pf']:4.2f} | "
      f"{base_a['meanR']:+.3f} {base_a['pf']:4.2f}      | {base_b['meanR']:+.3f} {base_b['pf']:4.2f}      |")
results = []
for name, m in FILTERS.items():
    m = m.to_numpy().astype(bool)
    a, x, y = split(take(m))
    both = (x['meanR'] > base_a['meanR']) and (y['meanR'] > base_b['meanR'])
    # permutation: drop the same number of signals at random
    k = m.sum(); null = []
    for _ in range(2000):
        rm = np.zeros(len(m), bool); rm[rng.choice(len(m), k, replace=False)] = True
        null.append(stats(take(rm))['meanR'])
    p = (np.sum(np.array(null) >= a['meanR']) + 1) / (len(null) + 1)
    results.append((name, m, a, x, y, both, p))
    print(f"{name:<38} {k:>5} | {a['n']:>4} {a['meanR']:+.3f} {a['pf']:4.2f} | {x['meanR']:+.3f} {x['pf']:4.2f} (n{x['n']:>3}) | "
          f"{y['meanR']:+.3f} {y['pf']:4.2f} (n{y['n']:>3}) | {'YES' if both else 'no':>5} {p:9.3f}")

print(f"\n{len(FILTERS)} filters tried; with that many tries, expect ~{len(FILTERS)*0.05:.1f} to reach p<0.05 by luck alone.")
final = [r for r in results if r[5] and r[6] < 0.05]
print("finalists (better in both halves AND p<0.05):", [r[0] for r in final] or "none")

for name, m, *_ in final:
    s2 = sig.copy()
    drop = sig_idx[~m]
    s2.iloc[drop, s2.columns.get_loc('signal')] = 0
    for label, part in (('ALL', s2), ('2020-22', s2[s2.index < SPLIT]), ('2023-25', s2[s2.index >= SPLIT])):
        r = run_backtest(part, cfg.strategy, cfg.risk, cfg.backtest)
        pn = np.array([t.pnl for t in r.trades if t.exit_time is not None]); eq = r.equity_curve
        print(f"  REAL backtester, {name} [{label}]: {len(pn)} trades, win {100*(pn>0).mean():.1f}%, "
              f"PF {pn[pn>0].sum()/-pn[pn<=0].sum():.2f}, return {100*(eq.iloc[-1]/r.initial_balance-1):+.1f}%, "
              f"maxDD {100*((eq-eq.cummax())/eq.cummax()).min():.1f}%")
