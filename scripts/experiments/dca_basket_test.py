"""DCA on the validated strategy's entries: add legs as price moves against the
trade, close the whole basket at the take-profit, with ONE hard stop for the
basket. Fair comparison: a full basket can lose at most the same 2% of base
that a single trade risks, so more legs means smaller legs.

Every variant is printed. A variant must beat the current strategy in BOTH
2020-22 and 2023-25 to be worth building.
"""
import sys; sys.path.insert(0, '/home/user/FOREX')
import numpy as np, pandas as pd
from gold_bot.config import load_config
from gold_bot.strategy import generate_signals

cfg = load_config('/home/user/FOREX/config/config.yaml')
df = pd.read_csv('/home/user/FOREX/data/XAUUSD_M5_real.csv'); df['time'] = pd.to_datetime(df['time'], utc=True)
df = df.set_index('time').sort_index()
sig = generate_signals(df.copy(), cfg.strategy, cfg.sessions)
O, H, L, C = (sig[k].to_numpy() for k in ('open', 'high', 'low', 'close'))
A = sig['atr'].to_numpy(); S = sig['signal'].to_numpy(); n = len(C)
HALF = cfg.backtest.spread_points * 0.01 / 2; SLIP = cfg.backtest.slippage_points * 0.01
SLM, TPM, TRAIL = cfg.strategy.atr_sl_mult, cfg.strategy.atr_tp_mult, cfg.risk.trailing_atr_mult
SPLIT = pd.Timestamp('2023-01-01', tz='UTC'); RISK_PCT = cfg.risk.risk_per_trade_pct
sig_idx = np.flatnonzero((S != 0) & np.isfinite(A) & (A > 0))


def single_with_trailing(i, d):
    """The current strategy, exactly as backtester.py runs it (R, exit bar)."""
    e = C[i] + d * (HALF + SLIP); sl = e - d * SLM * A[i]; tp = e + d * TPM * A[i]
    for j in range(i + 1, n):
        if (L[j] <= sl) if d == 1 else (H[j] >= sl): px = sl; break
        if (H[j] >= tp) if d == 1 else (L[j] <= tp): px = tp; break
        sl = max(sl, C[j] - TRAIL * A[j]) if d == 1 else min(sl, C[j] + TRAIL * A[j])
    else:
        return np.nan, n - 1
    px -= d * (SLIP + HALF)
    return d * (px - e) / (SLM * A[i]), j


def basket(i, d, legs, step, tp_mode):
    """DCA basket. Returns (R, exit bar, legs used). R is per the basket's full risk."""
    a = A[i]
    levels = [C[i] - d * k * step * a for k in range(legs)]
    fills = [lv + d * (HALF + SLIP) for lv in levels]
    stop = levels[-1] - d * SLM * a                       # 1.8 ATR beyond the deepest leg
    stop_exit = stop - d * (SLIP + HALF)
    worst = sum(d * (f - stop_exit) for f in fills)       # loss per oz-per-leg if every leg fills
    filled = [fills[0]]
    def target():
        return C[i] + d * TPM * a if tp_mode == 'original TP' else np.mean(filled) + d * 1.0 * a
    tp = target()
    for j in range(i + 1, n):
        lo, hi = (L[j], H[j]) if d == 1 else (-H[j], -L[j])     # oriented: adverse = low
        for k in range(len(filled), legs):                      # adverse move first: legs fill...
            if lo <= d * levels[k]:
                filled.append(fills[k])
        if lo <= d * stop:                                      # ...then the basket stop
            pnl = sum(d * (stop_exit - f) for f in filled)
            return pnl / worst, j, len(filled)
        if hi >= d * tp:
            pnl = sum(d * (tp - d * (SLIP + HALF) - f) for f in filled)
            return pnl / worst, j, len(filled)
        tp = target()
    return np.nan, n - 1, len(filled)


def run(fn):
    out, busy = [], -1
    for i in sig_idx:
        if i >= busy:
            res = fn(i, int(S[i]))
            if np.isfinite(res[0]):
                out.append((i,) + tuple(res)); busy = res[1]
    return out


def summary(trades):
    def one(t):
        r = np.array([x[1] for x in t])
        eq = 1 + np.cumsum(r * RISK_PCT / 100)
        pk = np.maximum.accumulate(np.concatenate([[1.0], eq]))[1:]
        return dict(n=len(r), ret=100 * (eq[-1] - 1), dd=100 * ((eq - pk) / pk).min(),
                    pf=r[r > 0].sum() / -r[r <= 0].sum() if (r <= 0).any() else np.inf, win=100 * (r > 0).mean())
    a = one(trades)
    x = one([t for t in trades if sig.index[t[0]] < SPLIT]); y = one([t for t in trades if sig.index[t[0]] >= SPLIT])
    return a, x, y


print(f"Each basket risks at most {RISK_PCT}% of the $100,000 base. Closed-trade equity, 5 years.\n")
print(f"{'variant':<40} {'trades':>6} {'win':>6} {'PF':>5} {'return':>8} {'max DD':>7} {'ret/DD':>6} | {'2020-22':>8} {'2023-25':>8} | beats current in both?")
base = summary(run(single_with_trailing))
a, x, y = base
print(f"{'CURRENT: 1 trade, trailing ON':<40} {a['n']:>6} {a['win']:>5.1f}% {a['pf']:>5.2f} {a['ret']:>+7.1f}% {a['dd']:>6.1f}% {a['ret']/abs(a['dd']):>6.2f} | {x['ret']:>+7.1f}% {y['ret']:>+7.1f}% |")
for tp_mode in ('original TP', 'small TP (avg + 1 ATR)'):
    for legs in (1, 2, 3, 4):
        for step in ((0.5, 1.0) if legs > 1 else (0.0,)):
            if legs == 1 and tp_mode != 'original TP':
                continue
            tr = run(lambda i, d: basket(i, d, legs, step, tp_mode))
            a, x, y = summary(tr)
            used = np.mean([t[3] for t in tr])
            better = x['ret'] > base[1]['ret'] and y['ret'] > base[2]['ret']
            name = f"{legs} legs, step {step} ATR, {tp_mode}" if legs > 1 else "1 trade, no trailing (reference)"
            print(f"{name:<40} {a['n']:>6} {a['win']:>5.1f}% {a['pf']:>5.2f} {a['ret']:>+7.1f}% {a['dd']:>6.1f}% {a['ret']/abs(a['dd']):>6.2f} | "
                  f"{x['ret']:>+7.1f}% {y['ret']:>+7.1f}% | {'YES' if better else 'no'}  (avg legs {used:.1f})")
