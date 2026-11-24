import sys; sys.path.insert(0,'/home/user/FOREX')
import numpy as np, pandas as pd
from gold_bot.config import load_config
from gold_bot.strategy import generate_signals
cfg=load_config('/home/user/FOREX/config/config.yaml')
df=pd.read_csv('/home/user/FOREX/data/XAUUSD_M5_real.csv'); df['time']=pd.to_datetime(df['time'],utc=True); df=df.set_index('time').sort_index()
sig=generate_signals(df.copy(),cfg.strategy,cfg.sessions)
H=df.high.to_numpy(); L=df.low.to_numpy(); C=df.close.to_numpy(); n=len(C); idx=df.index
COST=0.25+0.10

def run(entries, tp_usd, label, lots=1.0):
    out=[]
    for i,d in entries:
        e=C[i]+d*COST/2; tp=e+d*tp_usd
        j=i+1; mae=0.0
        while j<n:
            adv = (e-L[j]) if d==1 else (H[j]-e)
            mae=max(mae,adv)
            if (H[j]-COST/2>=tp) if d==1 else (L[j]+COST/2<=tp): break
            j+=1
        hit=j<n
        out.append(dict(i=i,d=d,e=e,j=j if hit else n-1,hit=hit,mae=mae,
                        days=(idx[min(j,n-1)]-idx[i]).days,
                        open_loss=(d*(C[-1]-COST/2*d-e)) if not hit else 0.0))
    t=pd.DataFrame(out)
    # account: floating P&L of all positions open at the same time, on hourly closes
    hours=df.close.resample('1h').last().dropna(); hpos=idx.searchsorted(hours.index)
    eq=np.zeros(len(hours)); real=0.0
    for r in t.itertuples():
        a=np.searchsorted(hpos,r.i,side='right'); b=np.searchsorted(hpos,r.j,side='left')
        eq[a:b]+=r.d*(hours.values[a:b]-r.e)*100*lots
        if r.hit: eq[b:]+=tp_usd*100*lots
    print(f"\n=== {label}: TP ${tp_usd}, NO stop-loss, {lots} lot ({lots*100:.0f} oz) per trade ===")
    print(f"trades {len(t)} | reached TP {t.hit.sum()} ({100*t.hit.mean():.0f}%) | still open & losing at end of data: {(~t.hit).sum()}")
    print(f"worst move against a single trade (MAE): ${t.mae.max():,.0f}/oz  -> ${t.mae.max()*100*lots:,.0f} floating loss on that trade")
    print(f"longest wait for TP: {t[t.hit].days.max()} days | trades that waited > 30 days: {(t[t.hit].days>30).sum()}")
    print(f"total profit from the TPs that DID hit: +${t.hit.sum()*tp_usd*100*lots:,.0f}")
    print(f"WORST ACCOUNT MOMENT (all open trades together): ${eq.min():,.0f} on {hours.index[eq.argmin()]:%Y-%m-%d}")
    if (~t.hit).any():
        print(f"loss still open at the end of the data: ${t.open_loss.sum()*100*lots:,.0f}")
    return t

ent=[(i,int(s)) for i,s in enumerate(sig.signal.to_numpy()) if s!=0]
for tp in (2,5):
    run(ent,tp,"current strategy's 97 entries, both directions")
# the 'always buy gold' version people usually mean
daily=[(i,1) for i in np.flatnonzero((idx.hour==13)&(idx.minute==0))]
run(daily,5,"BUY ONLY, one entry every day at 13:00 UTC",lots=0.1)

print("\n\n################ STRESS: same 5 years, trend reversed (gold falls ~42%, like 2011-2015) ################")
P0=df.close.iloc[0]
mir=pd.DataFrame({'open':P0**2/df.open,'high':P0**2/df.low,'low':P0**2/df.high,'close':P0**2/df.close},index=df.index)
print(f"mirrored path: starts ${mir.close.iloc[0]:,.0f}, ends ${mir.close.iloc[-1]:,.0f} ({100*(mir.close.iloc[-1]/mir.close.iloc[0]-1):+.0f}%), lowest ${mir.low.min():,.0f}")
df=mir; H=df.high.to_numpy(); L=df.low.to_numpy(); C=df.close.to_numpy(); n=len(C); idx=df.index
sig2=generate_signals(df.copy(),cfg.strategy,cfg.sessions)
ent2=[(i,int(s)) for i,s in enumerate(sig2.signal.to_numpy()) if s!=0]
run(ent2,2,"strategy entries on the reversed market")
run(daily,5,"BUY ONLY daily at 13:00 UTC on the reversed market",lots=0.1)
