import sys; sys.path.insert(0,'/home/user/FOREX')
src=open('scripts/experiments/fx_correlation_filter_test.py').read()
exec(src.split('b = run(sig0')[0])
import numpy as np
# collect baseline trade R's in the overlap window
def trades(sig,lo,hi):
    rs=[];i=lo
    while i<hi:
        if sig[i]==0 or not valid[i]: i+=1; continue
        d=int(sig[i]); entry=cn[i]+d*(SPREAD/2+SLIP); sld=SL_M*atrn[i]
        if sld<=0: i+=1; continue
        slp=entry-d*sld; tpp=entry+d*(TP_M*atrn[i]); j=i+1; ex=None
        while j<n:
            if ((ln[j]<=slp) if d==1 else (hn[j]>=slp)): ex=slp-d*SLIP; break
            if ((hn[j]>=tpp) if d==1 else (ln[j]<=tpp)): ex=tpp-d*SLIP; break
            j+=1
        if ex is None: break
        rs.append(d*(ex-entry)/sld); i=j+1
    return np.array(rs)
b=trades(sig0,0,LIMIT)
print(f"baseline trades in overlap window: {len(b)}, net {b.sum():+.2f}R")
print(f"  this 1.5-year window is a LOSING period for the baseline (PF {b[b>0].sum()/-b[b<=0].sum():.2f})\n")

flg,fsh=FILTERS['USD basket 12h']
s=np.where((sig0==1)&flg.to_numpy(),1,np.where((sig0==-1)&fsh.to_numpy(),-1,0)).astype(int)
f=trades(s,0,LIMIT)
print("=== Randomisation test: USD basket 12h filter ===")
print(f" filter keeps {len(f)} of {len(b)} trades, net {f.sum():+.2f}R (baseline {b.sum():+.2f}R)")
rng=np.random.default_rng(0); N=50000; hit=0
for _ in range(N):
    pick=rng.choice(len(b),size=len(f),replace=False)
    if b[pick].sum()>=f.sum(): hit+=1
print(f" random subsets of {len(f)} trades doing at least as well: {100*hit/N:.1f}% of {N:,} draws")
print(f" -> p = {hit/N:.3f}")

print("\n=== Why 12h and not 4h/24h/72h? ===")
for h in (4,12,24,72):
    fl,fs=FILTERS[f'USD basket {h}h']
    ss=np.where((sig0==1)&fl.to_numpy(),1,np.where((sig0==-1)&fs.to_numpy(),-1,0)).astype(int)
    t=trades(ss,0,LIMIT)
    print(f"  USD basket {h:>2}h: n={len(t):>2}  net={t.sum():+6.2f}R")
print("  A real effect would not appear at 12h and vanish at 4h and 24h.")
