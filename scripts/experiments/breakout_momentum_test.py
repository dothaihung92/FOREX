"""Momentum/breakout entries: when gold breaks hard, trade WITH the break.

Requested design: "when price breaks out strongly, enter in the direction
of the move". This is the opposite framing to the rejected BB reversal
tests - instead of fading an extreme, it joins it. The project has never
tested breakout as a standalone entry system (Donchian appeared only as a
confirmation filter in the indicator sweep), so this is genuinely new
ground rather than a re-run.

Triggers (each direction-symmetric):
  D<N>   Donchian: close breaks the highest high / lowest low of N bars
  M<k>   Momentum candle: body > k x ATR, closing in the direction
  R<k>   Range expansion: bar range > k x ATR and close in the top/bottom
         25% of the bar (a decisive close, not a wick)
  S<N>   Squeeze break: Donchian break that follows a contraction (ATR
         below its own average) - the textbook "coil then explode" setup

Filter levels, applied to every trigger:
  raw    trigger only
  trend  + M15 and H1 both confirm the break's direction
  full   + trading session and excluded-hour filters as well

Exits: ATR stop/target. Shipped 5:3 (1.8/3.0) plus a wider 2.5/5.0, since
breakout systems conventionally need more room than pullback systems.

Real Exness Standard costs. Scored on train 2020-2022 and test 2023-2025 -
a breakout system that only works in one regime is a curve fit, and gold
trended hard in the test half, which is exactly the regime that flatters
this family of strategy.
"""
import sys; sys.path.insert(0, '/home/user/FOREX')
import numpy as np
import pandas as pd
from gold_bot.config import load_config
from gold_bot.strategy import generate_signals, in_session

cfg = load_config('/home/user/FOREX/config/config.yaml')
df = pd.read_csv('/home/user/FOREX/data/XAUUSD_M5_real.csv')
df['time'] = pd.to_datetime(df['time'], utc=True)
df = df.set_index('time').sort_index()
base = generate_signals(df.copy(), cfg.strategy, cfg.sessions)

o, h, l, c = base['open'], base['high'], base['low'], base['close']
atr_s = base['atr']

# ------------------------------------------------------------------ triggers
TRIGGERS = {}
for N in (20, 50, 100):
    prior_hi = h.shift(1).rolling(N).max()
    prior_lo = l.shift(1).rolling(N).min()
    TRIGGERS[f'D{N} donchian'] = (c > prior_hi, c < prior_lo)

body = c - o
for k in (1.0, 1.5, 2.0):
    TRIGGERS[f'M{k} momentum-candle'] = (body > k * atr_s, body < -k * atr_s)

rng = (h - l).replace(0, np.nan)
close_pos = (c - l) / rng
for k in (1.5, 2.0):
    big = rng > k * atr_s
    TRIGGERS[f'R{k} range-expansion'] = (big & (close_pos > 0.75), big & (close_pos < 0.25))

contracted = atr_s < atr_s.rolling(50).mean()
for N in (20, 50):
    prior_hi = h.shift(1).rolling(N).max()
    prior_lo = l.shift(1).rolling(N).min()
    TRIGGERS[f'S{N} squeeze-break'] = (contracted.shift(1).fillna(False) & (c > prior_hi),
                                       contracted.shift(1).fillna(False) & (c < prior_lo))

# ------------------------------------------------------------------- filters
trend_up = (base['htf_trend'] > 0) & (base['htf2_trend'] > 0)
trend_dn = (base['htf_trend'] < 0) & (base['htf2_trend'] < 0)
session_ok = base.index.to_series().apply(lambda ts: in_session(ts, cfg.sessions))
hour_ok = pd.Series(~base.index.hour.isin(cfg.strategy.excluded_hours), index=base.index)

FILTER_LEVELS = {
    'raw':   (pd.Series(True, index=base.index), pd.Series(True, index=base.index)),
    'trend': (trend_up, trend_dn),
    'full':  (trend_up & session_ok & hour_ok, trend_dn & session_ok & hour_ok),
}

# ------------------------------------------------------------------ backtest
on = o.to_numpy(); hn = h.to_numpy(); ln = l.to_numpy(); cn = c.to_numpy()
atrn = atr_s.to_numpy()
SPREAD = cfg.backtest.spread_points * 0.01
SLIP = cfg.backtest.slippage_points * 0.01
n = len(cn)
valid = ~np.isnan(atrn)
SPLIT = int(base.index.searchsorted(pd.Timestamp('2023-01-01', tz='UTC')))


def run(sig, sl_m, tp_m, lo, hi):
    """R-multiple scoring: independent of lot size and account balance."""
    rs = []
    i = lo
    while i < hi:
        if sig[i] == 0 or not valid[i]:
            i += 1; continue
        d = int(sig[i])
        entry = cn[i] + d * (SPREAD / 2 + SLIP)
        sl_dist = sl_m * atrn[i]
        if sl_dist <= 0:
            i += 1; continue
        sl_p = entry - d * sl_dist; tp_p = entry + d * (tp_m * atrn[i])
        j = i + 1; ex = None
        while j < n:
            if ((ln[j] <= sl_p) if d == 1 else (hn[j] >= sl_p)): ex = sl_p - d * SLIP; break
            if ((hn[j] >= tp_p) if d == 1 else (ln[j] <= tp_p)): ex = tp_p - d * SLIP; break
            j += 1
        if ex is None:
            break
        rs.append(d * (ex - entry) / sl_dist)
        i = j + 1
    if not rs:
        return {'n': 0, 'pf': 0.0, 'net_r': 0.0, 'win': 0.0}
    a = np.array(rs); w = a[a > 0].sum(); ls = -a[a <= 0].sum()
    return {'n': len(a), 'pf': (w / ls) if ls > 0 else float('inf'),
            'net_r': a.sum(), 'win': 100 * (a > 0).mean()}


b_full = run(base['signal'].to_numpy(), cfg.strategy.atr_sl_mult, cfg.strategy.atr_tp_mult, 0, n)
b_tr = run(base['signal'].to_numpy(), cfg.strategy.atr_sl_mult, cfg.strategy.atr_tp_mult, 0, SPLIT)
b_te = run(base['signal'].to_numpy(), cfg.strategy.atr_sl_mult, cfg.strategy.atr_tp_mult, SPLIT, n)

print(f"XAUUSD M5, {n} bars | costs: spread ${SPREAD:.2f}, slippage ${SLIP:.2f}/side, no commission")
print(f"BASELINE (shipped pullback strategy): n={b_full['n']} netR={b_full['net_r']:+.1f} "
      f"PF={b_full['pf']:.2f} | train PF={b_tr['pf']:.2f} | test PF={b_te['pf']:.2f}\n")

EXITS = [(cfg.strategy.atr_sl_mult, cfg.strategy.atr_tp_mult), (2.5, 5.0)]
rows = []
for tname, (lg, sh) in TRIGGERS.items():
    for fname, (flg, fsh) in FILTER_LEVELS.items():
        s = np.where((lg & flg).to_numpy(), 1, np.where((sh & fsh).to_numpy(), -1, 0)).astype(int)
        for sl_m, tp_m in EXITS:
            full = run(s, sl_m, tp_m, 0, n)
            if full['n'] < 20:
                continue
            rows.append({
                'name': f"{tname:<22} {fname:<6} SL{sl_m}/TP{tp_m}",
                'full': full,
                'train': run(s, sl_m, tp_m, 0, SPLIT),
                'test': run(s, sl_m, tp_m, SPLIT, n),
            })

pf = lambda x: '   inf' if x == float('inf') else f"{x:6.2f}"
HDR = (f"{'trigger / filter / exit':<46} {'n':>6} {'win%':>6} {'netR':>8} {'PF':>6} |"
       f" {'trN':>5} {'trPF':>6} | {'teN':>5} {'tePF':>6}")


def show(r):
    f, t, e = r['full'], r['train'], r['test']
    print(f"{r['name']:<46} {f['n']:>6} {f['win']:>6.1f} {f['net_r']:>+8.1f} {pf(f['pf'])} |"
          f" {t['n']:>5} {pf(t['pf'])} | {e['n']:>5} {pf(e['pf'])}")


print(f"variants with >=20 trades: {len(rows)}\n")
print("=== TOP 15 by total profit (net R) ===")
print(HDR)
for r in sorted(rows, key=lambda x: -x['full']['net_r'])[:15]:
    show(r)

print("\n=== TOP 15 by profit factor ===")
print(HDR)
for r in sorted(rows, key=lambda x: -x['full']['pf'])[:15]:
    show(r)

print("\n=== Profitable in BOTH regimes (PF > 1 on train AND test, >=20 trades each) ===")
surv = [r for r in rows if r['train']['pf'] > 1 and r['test']['pf'] > 1
        and r['train']['n'] >= 20 and r['test']['n'] >= 20]
if surv:
    print(HDR)
    for r in sorted(surv, key=lambda x: -x['full']['net_r']):
        show(r)
else:
    print("NONE")

print(f"\n=== How many of the {len(rows)} variants are profitable at all? ===")
print(f"  net R > 0 over the full period : {sum(1 for r in rows if r['full']['net_r'] > 0)}")
print(f"  PF > 1 on train                : {sum(1 for r in rows if r['train']['pf'] > 1)}")
print(f"  PF > 1 on test                 : {sum(1 for r in rows if r['test']['pf'] > 1)}")
print(f"  PF > 1 on both                 : {sum(1 for r in rows if r['train']['pf'] > 1 and r['test']['pf'] > 1)}")
