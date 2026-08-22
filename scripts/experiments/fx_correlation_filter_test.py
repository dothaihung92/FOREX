"""Gold/FX correlation as an entry filter: check USD-side pairs before
placing a gold trade.

Requested design: before buying/selling gold, look at AUD/USD, EUR/USD and
USD/CHF (USD up => gold down, and vice versa) and only trade when they
agree with the intended direction.

Step 1 (scripts output below) measured whether the relationship is real.
It is - but only at macro timeframes:

    correlation of returns with gold, 2020-2022 overlap window
    timeframe   EURUSD  AUDUSD  USDCHF   USD basket
    M15         +0.004  +0.007  -0.015      -0.008
    H1          -0.017  -0.019  -0.005      +0.017
    H4          +0.150  +0.155  -0.215      -0.204
    D1          +0.404  +0.439  -0.512      -0.542
    W1          +0.410  +0.540  -0.679      -0.628

Every sign at D1/W1 matches the stated thesis. At M15/H1 the correlation
is ~0. So the honest way to use this is NOT bar-to-bar confirmation (there
is no information there) but as a DAILY-SCALE REGIME filter on M5 entries,
which is what this script tests.

Filter: measure the USD move over the last N hours as of the last FX bar
that closed BEFORE the gold signal (no lookahead), then allow gold longs
only while USD is weakening and shorts only while USD is strengthening.

IMPORTANT SAMPLE LIMIT: the repo's FX data ends 2022-03-04 while gold M5
runs to 2025-08. The usable overlap is ~1.5 years, which leaves very few
baseline trades. Results are reported with that caveat attached rather
than dressed up.
"""
import sys; sys.path.insert(0, '/home/user/FOREX')
import numpy as np
import pandas as pd
from gold_bot.config import load_config
from gold_bot.strategy import generate_signals

cfg = load_config('/home/user/FOREX/config/config.yaml')
gdf = pd.read_csv('/home/user/FOREX/data/XAUUSD_M5_real.csv')
gdf['time'] = pd.to_datetime(gdf['time'], utc=True)
gdf = gdf.set_index('time').sort_index()
base = generate_signals(gdf.copy(), cfg.strategy, cfg.sessions)


def load_fx(sym):
    d = pd.read_csv(f'/home/user/FOREX/data/{sym}_m15.csv')
    d['Date'] = pd.to_datetime(d['Date'], utc=True)
    return d.set_index('Date').sort_index()['close'].astype(float)


SYMS = ['EURUSD', 'AUDUSD', 'USDCHF', 'GBPUSD', 'USDJPY', 'USDCAD']
fxf = pd.DataFrame({s: load_fx(s) for s in SYMS}).dropna()
lg = np.log(fxf)
# USD strength index: pairs quoted USD-second are inverted so that a rise
# always means a stronger dollar.
usd_idx = (-lg['EURUSD'] - lg['GBPUSD'] - lg['AUDUSD']
           + lg['USDJPY'] + lg['USDCHF'] + lg['USDCAD']) / 6

FX_END = fxf.index[-1]
print(f"gold M5 : {base.index[0].date()} -> {base.index[-1].date()}")
print(f"FX M15  : {fxf.index[0].date()} -> {FX_END.date()}   <-- limits the test window")

# ---- align FX onto gold bars WITHOUT lookahead -------------------------
# For each gold bar take the most recent FX bar that closed strictly
# before it. asof/merge_asof with allow_exact_matches=False guarantees the
# FX value was already published when the gold signal fired.
def asof_series(src, target_index):
    left = pd.DataFrame(index=target_index).reset_index().rename(columns={'index': 't', 'time': 't'})
    right = src.rename('v').reset_index().rename(columns={src.index.name or 'index': 't', 'Date': 't'})
    m = pd.merge_asof(left.sort_values('t'), right.sort_values('t'), on='t',
                      direction='backward', allow_exact_matches=False)
    return pd.Series(m['v'].to_numpy(), index=target_index)


usd_on_gold = asof_series(usd_idx, base.index)
chf_on_gold = asof_series(lg['USDCHF'], base.index)
aud_on_gold = asof_series(lg['AUDUSD'], base.index)
eur_on_gold = asof_series(lg['EURUSD'], base.index)

BARS_PER_HOUR = 12  # gold M5
FILTERS = {}
for hours in (4, 12, 24, 72):
    k = hours * BARS_PER_HOUR
    d_usd = usd_on_gold - usd_on_gold.shift(k)
    FILTERS[f'USD basket {hours}h'] = (d_usd < 0, d_usd > 0)      # USD down -> gold up
    d_chf = chf_on_gold - chf_on_gold.shift(k)
    FILTERS[f'USDCHF {hours}h'] = (d_chf < 0, d_chf > 0)
    d_aud = aud_on_gold - aud_on_gold.shift(k)
    FILTERS[f'AUDUSD {hours}h'] = (d_aud > 0, d_aud < 0)          # AUD up -> gold up
    d_eur = eur_on_gold - eur_on_gold.shift(k)
    FILTERS[f'EURUSD {hours}h'] = (d_eur > 0, d_eur < 0)
    FILTERS[f'ALL THREE agree {hours}h'] = ((d_chf < 0) & (d_aud > 0) & (d_eur > 0),
                                            (d_chf > 0) & (d_aud < 0) & (d_eur < 0))

# ---- backtest ---------------------------------------------------------
hn = base['high'].to_numpy(); ln = base['low'].to_numpy(); cn = base['close'].to_numpy()
atrn = base['atr'].to_numpy(); sig0 = base['signal'].to_numpy()
SPREAD = cfg.backtest.spread_points * 0.01
SLIP = cfg.backtest.slippage_points * 0.01
SL_M, TP_M = cfg.strategy.atr_sl_mult, cfg.strategy.atr_tp_mult
n = len(cn)
valid = ~np.isnan(atrn)
LIMIT = int(base.index.searchsorted(FX_END))   # only bars where FX exists


def run(sig, lo, hi):
    rs = []
    i = lo
    while i < hi:
        if sig[i] == 0 or not valid[i]:
            i += 1; continue
        d = int(sig[i])
        entry = cn[i] + d * (SPREAD / 2 + SLIP)
        sl_dist = SL_M * atrn[i]
        if sl_dist <= 0:
            i += 1; continue
        sl_p = entry - d * sl_dist; tp_p = entry + d * (TP_M * atrn[i])
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


b = run(sig0, 0, LIMIT)
print(f"\nusable overlap: {base.index[0].date()} -> {FX_END.date()}")
print(f"BASELINE in that window: n={b['n']} netR={b['net_r']:+.2f} PF={b['pf']:.2f} "
      f"win={b['win']:.1f}%\n")

pf = lambda x: '   inf' if x == float('inf') else f"{x:6.2f}"
print(f"{'filter':<26} {'n':>5} {'kept%':>7} {'win%':>7} {'netR':>8} {'PF':>7}   vs baseline")
print(f"{'BASELINE (no FX check)':<26} {b['n']:>5} {'100.0':>7} {b['win']:>7.1f} "
      f"{b['net_r']:>+8.2f} {pf(b['pf'])}")
rows = []
for name, (flg, fsh) in FILTERS.items():
    s = np.where((sig0 == 1) & flg.to_numpy(), 1,
                 np.where((sig0 == -1) & fsh.to_numpy(), -1, 0)).astype(int)
    r = run(s, 0, LIMIT)
    rows.append((name, r))
for name, r in rows:
    keep = 100 * r['n'] / b['n'] if b['n'] else 0
    delta = r['net_r'] - b['net_r']
    print(f"{name:<26} {r['n']:>5} {keep:>7.1f} {r['win']:>7.1f} {r['net_r']:>+8.2f} "
          f"{pf(r['pf'])}   {delta:+.2f}R")

print(f"\nfilters that improved net profit: "
      f"{sum(1 for _, r in rows if r['net_r'] > b['net_r'])} of {len(rows)}")
print(f"filters with >=20 trades AND better PF: "
      f"{sum(1 for _, r in rows if r['n'] >= 20 and r['pf'] > b['pf'])} of {len(rows)}")
