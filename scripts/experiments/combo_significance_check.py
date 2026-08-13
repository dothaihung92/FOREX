import sys; sys.path.insert(0,'/home/user/FOREX')
src = open('scripts/experiments/indicator_combo_sweep.py').read()
exec(src.split('SPLIT = int(')[0])
import numpy as np, pandas as pd
SPLIT = int(base.index.searchsorted(pd.Timestamp('2023-01-01', tz='UTC')))
ALL = np.ones(len(IDX), bool)

def sel(mask, lo, hi):
    out=[]; busy=-1
    for k in range(len(IDX)):
        i=IDX[k]
        if i<lo or i>=hi or i<=busy or not mask[k]: continue
        out.append(k); busy=EX[k]
    return out

b_full = sel(ALL,0,n)
for nm, m in [('Stoch', mask_cache['Stoch']),
              ('Ichimoku+Stoch', mask_cache['Ichimoku']&mask_cache['Stoch'])]:
    f = sel(m,0,n)
    tr = sel(m,0,SPLIT); te = sel(m,SPLIT,n)
    btr = sel(ALL,0,SPLIT); bte = sel(ALL,SPLIT,n)
    pf = lambda ks: (lambda a: a[a>0].sum()/max(1e-12,-a[a<=0].sum()))(R[ks])
    print(f"{nm}: full n={len(f)} netR={R[f].sum():+.2f} PF={pf(f):.4f}")
    print(f"    train PF={pf(tr):.4f} (n={len(tr)})  vs baseline {pf(btr):.4f} (n={len(btr)})")
    print(f"    test  PF={pf(te):.4f} (n={len(te)})  vs baseline {pf(bte):.4f} (n={len(bte)})")
    dropped=set(b_full)-set(f); added=set(f)-set(b_full)
    print(f"    trades removed vs baseline: {len(dropped)}  (their total R = {R[list(dropped)].sum():+.2f})")
    print(f"    trades newly freed:         {len(added)}  (their total R = {R[list(added)].sum() if added else 0:+.2f})")

# Randomisation test: is Stoch's gain better than dropping the same number at random?
print("\n=== Randomisation test for Stoch (does it beat dropping 2 trades at random?) ===")
rng=np.random.default_rng(0)
base_net=R[b_full].sum()
f=sel(mask_cache['Stoch'],0,n); obs=R[f].sum()-base_net
k=len(set(b_full)-set(f))
better=0; N=20000
arr=R[b_full]
for _ in range(N):
    drop=rng.choice(len(arr),size=k,replace=False)
    if -arr[drop].sum() >= obs: better+=1
print(f" observed gain from the filter: {obs:+.2f} R (by removing {k} trades)")
print(f" random removals of {k} trades doing at least as well: {100*better/N:.1f}% of 20,000 draws")
print(" -> a p-value near or above 5% means the 'edge' is indistinguishable from luck")
