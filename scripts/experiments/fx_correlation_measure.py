"""Step 1: are the claimed gold/FX correlations actually true on real data?

Build the filter only if the relationship exists. Measured on the same
2020-2025 window the gold strategy is tested on.
"""
import sys; sys.path.insert(0, '/home/user/FOREX')
import numpy as np, pandas as pd

def load_fx(sym):
    d = pd.read_csv(f'/home/user/FOREX/data/{sym}_m15.csv')
    d['Date'] = pd.to_datetime(d['Date'], utc=True)
    d = d.set_index('Date').sort_index()
    return d['close'].astype(float)

g = pd.read_csv('/home/user/FOREX/data/XAUUSD_M5_real.csv')
g['time'] = pd.to_datetime(g['time'], utc=True)
gold = g.set_index('time').sort_index()['close']
print(f"gold M5: {gold.index[0]} -> {gold.index[-1]}  ({len(gold)} bars)")

SYMS = ['EURUSD','AUDUSD','USDCHF','GBPUSD','USDJPY','USDCAD']
fx = {s: load_fx(s) for s in SYMS}
for s in SYMS:
    print(f"  {s}: {fx[s].index[0].date()} -> {fx[s].index[-1].date()} ({len(fx[s])} bars)")

# Resample gold to M15 to match FX granularity, then align on common index.
gold15 = gold.resample('15min').last().dropna()
frame = pd.DataFrame({'gold': gold15})
for s in SYMS:
    frame[s] = fx[s]
frame = frame.dropna()
print(f"\naligned M15 bars in common: {len(frame)}  "
      f"({frame.index[0].date()} -> {frame.index[-1].date()})")

# Correlation of RETURNS (levels correlate spuriously via shared trend).
rets = np.log(frame).diff().dropna()
print("\n=== Correlation of 15-minute returns with gold (2020-2025) ===")
print(f"{'pair':<10} {'claimed':<12} {'actual r':>9}  verdict")
CLAIM = {'EURUSD':'positive','AUDUSD':'positive','USDCHF':'negative',
         'GBPUSD':'(positive)','USDJPY':'(negative)','USDCAD':'(negative)'}
for s in SYMS:
    r = rets['gold'].corr(rets[s])
    exp_pos = 'positive' in CLAIM[s]
    ok = 'MATCHES claim' if ((r > 0) == exp_pos) else 'CONTRADICTS claim'
    print(f"{s:<10} {CLAIM[s]:<12} {r:>+9.3f}  {ok}")

# Year by year - a correlation that flips sign is not tradeable.
print("\n=== Stability: same correlation, year by year ===")
print(f"{'year':<6} " + " ".join(f"{s:>9}" for s in SYMS))
for y, grp in rets.groupby(rets.index.year):
    if len(grp) < 500: continue
    print(f"{y:<6} " + " ".join(f"{grp['gold'].corr(grp[s]):>+9.3f}" for s in SYMS))

# A USD-strength composite (DXY-like) - the user's core thesis is "USD up
# => gold down", which is better measured by a basket than one pair.
usd = (-np.log(frame['EURUSD']).diff() - np.log(frame['GBPUSD']).diff()
       - np.log(frame['AUDUSD']).diff() + np.log(frame['USDJPY']).diff()
       + np.log(frame['USDCHF']).diff() + np.log(frame['USDCAD']).diff()) / 6
usd = usd.dropna()
common = rets.index.intersection(usd.index)
print(f"\n=== USD strength basket vs gold returns: r = {rets.loc[common,'gold'].corr(usd.loc[common]):+.3f} ===")

# The decisive question: does the FX move PREDICT the next gold move, or
# only move at the same time? Only a predictive lead is tradeable.
print("\n=== Does FX LEAD gold, or just move with it? (correlation at lag) ===")
print(f"{'pair':<10} {'same bar':>10} {'FX leads 1':>12} {'FX leads 2':>12} {'FX leads 4':>12}")
for s in SYMS + ['USDbasket']:
    ser = usd.reindex(rets.index) if s == 'USDbasket' else rets[s]
    row = [rets['gold'].corr(ser.shift(k)) for k in (0,1,2,4)]
    print(f"{s:<10} " + " ".join(f"{v:>+12.3f}" for v in row))
print("\n(lagged r near 0.00 => FX carries no advance information about gold)")
