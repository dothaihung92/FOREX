import sys; sys.path.insert(0,'/home/user/FOREX')
from dataclasses import replace
import numpy as np, pandas as pd
from gold_bot.backtester import run_backtest
from gold_bot.config import load_config
from gold_bot.strategy import generate_signals
cfg=load_config('/home/user/FOREX/config/config.yaml')
df=pd.read_csv('/home/user/FOREX/data/XAUUSD_M5_real.csv'); df['time']=pd.to_datetime(df['time'],utc=True); df=df.set_index('time').sort_index()
sig=generate_signals(df.copy(),cfg.strategy,cfg.sessions)
SPLIT=pd.Timestamp('2023-01-01',tz='UTC')
BT=replace(cfg.backtest,initial_balance=10000.0)
print("REAL BACKTESTER - $10,000 fixed base, full costs, daily loss limit 3% of base (same as the EA)\n")
print(f"{'risk':>4} | {'trades':>6} {'5y return':>9} {'per yr':>7} {'max DD':>7} {'worst day':>9} | {'2020-22':>8} {'2023-25':>8}")
Rs=None
for rp in (2,3,4,5,6,7,8,9,10):
    rk=replace(cfg.risk,base_equity=10000.0,risk_per_trade_pct=rp,min_lot=0.01)
    out={}
    for lab,part in (('ALL',sig),('A',sig[sig.index<SPLIT]),('B',sig[sig.index>=SPLIT])):
        r=run_backtest(part,cfg.strategy,rk,BT); eq=r.equity_curve
        tr=[t for t in r.trades if t.exit_time is not None]
        out[lab]=(len(tr),100*(eq.iloc[-1]/10000-1),100*((eq-eq.cummax())/eq.cummax()).min(),tr)
    n,ret,dd,tr=out['ALL']
    day=pd.Series([t.pnl for t in tr],index=[t.exit_time.date() for t in tr]).groupby(level=0).sum().min()
    if rp==2: Rs=np.array([t.pnl for t in tr])/200.0
    print(f"{rp:>3}% | {n:>6} {ret:>+8.0f}% {ret/4.95:>+6.1f}% {dd:>6.1f}% {100*day/10000:>8.1f}% | {out['A'][1]:>+7.0f}% {out['B'][1]:>+7.0f}%")
streak=cur=0
for x in Rs:
    cur=cur+1 if x<0 else 0; streak=max(streak,cur)
print(f"\nlongest losing streak in the history: {streak} trades  ->  at 10% risk that is -{streak*10}% of the base")
rng=np.random.default_rng(0); N=20000; idx=rng.integers(0,len(Rs),(N,len(Rs)))
for keep,label in ((1.0,"edge as measured"),(0.5,"edge HALF of measured")):
    R=(Rs-Rs.mean())+Rs.mean()*keep
    print(f"\nBOOTSTRAP, {label} (mean {R.mean():+.3f}R/trade):")
    print(f"{'risk':>4} | {'median 5y':>9} {'5th pct':>8} | {'95th DD':>8} | {'P(DD>30%)':>9} {'P(DD>50%)':>9} {'P(lose)':>8}")
    for rp in (5,6,7,8,9,10):
        eq=1+np.cumsum(R[idx]*rp/100,axis=1)
        pk=np.maximum.accumulate(np.concatenate([np.ones((N,1)),eq],axis=1),axis=1)[:,1:]
        dd=((eq-pk)/pk).min(axis=1)*100; fin=(eq[:,-1]-1)*100
        print(f"{rp:>3}% | {np.median(fin):>+8.0f}% {np.percentile(fin,5):>+7.0f}% | {np.percentile(dd,5):>7.1f}% | {100*(dd<-30).mean():>8.1f}% {100*(dd<-50).mean():>8.1f}% {100*(fin<0).mean():>7.1f}%")
