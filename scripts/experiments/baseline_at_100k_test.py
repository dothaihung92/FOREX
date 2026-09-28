import sys; sys.path.insert(0,'/home/user/FOREX')
import numpy as np, pandas as pd
from gold_bot.config import load_config
from gold_bot.strategy import generate_signals
cfg=load_config('/home/user/FOREX/config/config.yaml')
df=pd.read_csv('/home/user/FOREX/data/XAUUSD_M5_real.csv'); df['time']=pd.to_datetime(df['time'],utc=True)
df=df.set_index('time').sort_index()
base=generate_signals(df.copy(),cfg.strategy,cfg.sessions)
h=base['high'].to_numpy();l=base['low'].to_numpy();c=base['close'].to_numpy()
atr=base['atr'].to_numpy();sig=base['signal'].to_numpy()
SP=cfg.backtest.spread_points*0.01; SL=cfg.backtest.slippage_points*0.01
SLM,TPM=cfg.strategy.atr_sl_mult,cfg.strategy.atr_tp_mult
n=len(c); valid=~np.isnan(atr)
SPLIT=int(base.index.searchsorted(pd.Timestamp('2023-01-01',tz='UTC')))

def run(cap, risk_pct, lo, hi):
    bal=cap; peak=cap; mdd=0.0; rs=[]; i=lo
    while i<hi:
        if sig[i]==0 or not valid[i]: i+=1; continue
        d=int(sig[i]); entry=c[i]+d*(SP/2+SL); sld=SLM*atr[i]
        if sld<=0: i+=1; continue
        lots=max(0.01, round((risk_pct/100*cap)/(sld*100.0)/0.01)*0.01)
        slp=entry-d*sld; tpp=entry+d*(TPM*atr[i]); j=i+1; ex=None
        while j<n:
            if ((l[j]<=slp) if d==1 else (h[j]>=slp)): ex=slp-d*SL; break
            if ((h[j]>=tpp) if d==1 else (l[j]<=tpp)): ex=tpp-d*SL; break
            j+=1
        if ex is None: break
        pnl=d*(ex-entry)*100.0*lots; bal+=pnl; rs.append(pnl)
        peak=max(peak,bal); mdd=min(mdd,(bal-peak)/peak); i=j+1
    a=np.array(rs) if rs else np.array([0.0])
    w=a[a>0].sum(); ls=-a[a<=0].sum()
    return dict(n=len(rs), net=bal-cap, pf=(w/ls) if ls>0 else 0, mdd=100*mdd, bal=bal)

print("Chien luoc MAC DINH cua repo (EMA+RSI+MACD, SL/TP 5:3), von $100,000\n")
print(f"{'risk/lenh':<12} {'so lenh':>8} {'net':>14} {'%/nam':>8} {'PF':>6} {'maxDD':>8}")
for rp in (1.0,2.0,3.0,5.0):
    r=run(100000.0,rp,0,n)
    print(f"{rp:>10.1f}% {r['n']:>8} ${r['net']:>+13,.0f} {100*r['net']/100000/5:>+7.2f}% {r['pf']:>6.2f} {r['mdd']:>7.1f}%")
print()
for label,lo,hi in [('TRAIN 2020-2022',0,SPLIT),('TEST 2023-2025',SPLIT,n)]:
    r=run(100000.0,2.0,lo,hi)
    print(f"  {label}: {r['n']} lenh, net ${r['net']:+,.0f}, PF {r['pf']:.2f}, maxDD {r['mdd']:.1f}%")
