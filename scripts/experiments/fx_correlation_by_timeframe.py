import sys; sys.path.insert(0,'/home/user/FOREX')
import numpy as np, pandas as pd
def load_fx(s):
    d=pd.read_csv(f'/home/user/FOREX/data/{s}_m15.csv')
    d['Date']=pd.to_datetime(d['Date'],utc=True)
    return d.set_index('Date').sort_index()['close'].astype(float)
g=pd.read_csv('/home/user/FOREX/data/XAUUSD_M5_real.csv')
g['time']=pd.to_datetime(g['time'],utc=True)
gold=g.set_index('time').sort_index()['close']
SYMS=['EURUSD','AUDUSD','USDCHF','GBPUSD','USDJPY','USDCAD']
frame=pd.DataFrame({'gold':gold.resample('15min').last()})
for s in SYMS: frame[s]=load_fx(s)
frame=frame.dropna()
print(f"overlap window: {frame.index[0].date()} -> {frame.index[-1].date()}  ({len(frame)} M15 bars)")
print("NOTE: the repo's FX data ends 2022-03-04, so only ~1.5 years overlap with gold M5.\n")

print("=== Correlation with gold at increasing timeframes ===")
print(f"{'timeframe':<12} " + " ".join(f"{s:>9}" for s in SYMS) + f" {'USDbasket':>10} {'n':>7}")
for label,rule in [('M15','15min'),('H1','1h'),('H4','4h'),('D1','1D'),('W1','1W')]:
    r=frame.resample(rule).last().dropna()
    if len(r)<30: continue
    lr=np.log(r).diff().dropna()
    usd=(-lr['EURUSD']-lr['GBPUSD']-lr['AUDUSD']+lr['USDJPY']+lr['USDCHF']+lr['USDCAD'])/6
    cells=" ".join(f"{lr['gold'].corr(lr[s]):>+9.3f}" for s in SYMS)
    print(f"{label:<12} {cells} {lr['gold'].corr(usd):>+10.3f} {len(lr):>7}")
print("\nIf |r| grows with timeframe, the relationship is real but MACRO -")
print("it lives in daily/weekly moves, not in the 5-minute bars this bot trades.")
