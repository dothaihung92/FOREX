import sys; sys.path.insert(0,'/home/user/FOREX')
src=open('scripts/experiments/breakout_momentum_test.py').read()
exec(src.split('b_full = run(')[0])
import numpy as np, pandas as pd

def mk(t,f):
    lg,sh=TRIGGERS[t]; flg,fsh=FILTER_LEVELS[f]
    return np.where((lg&flg).to_numpy(),1,np.where((sh&fsh).to_numpy(),-1,0)).astype(int)

print("=== Zero-cost check: does breakout momentum have an edge before costs? ===")
print(f"{'variant':<40} {'n':>6} {'PF no-cost':>11} {'PF real':>9} {'cost in R/trade':>16}")
_S,_L=SPREAD,SLIP
for t,f,sl,tp in [('M2.0 momentum-candle','full',2.5,5.0),
                  ('R2.0 range-expansion','full',2.5,5.0),
                  ('D20 donchian','full',2.5,5.0),
                  ('D20 donchian','raw',2.5,5.0)]:
    s=mk(t,f)
    globals()['SPREAD']=0.0; globals()['SLIP']=0.0
    z=run(s,sl,tp,0,n)
    globals()['SPREAD']=_S; globals()['SLIP']=_L
    r=run(s,sl,tp,0,n)
    per=(z['net_r']-r['net_r'])/max(1,r['n'])
    print(f"{t+' '+f:<40} {r['n']:>6} {z['pf']:>11.3f} {r['pf']:>9.3f} {per:>16.3f}")

print("\n=== Baseline for comparison ===")
bs=base['signal'].to_numpy()
globals()['SPREAD']=0.0; globals()['SLIP']=0.0
z=run(bs,cfg.strategy.atr_sl_mult,cfg.strategy.atr_tp_mult,0,n)
globals()['SPREAD']=_S; globals()['SLIP']=_L
r=run(bs,cfg.strategy.atr_sl_mult,cfg.strategy.atr_tp_mult,0,n)
print(f"{'BASELINE pullback':<40} {r['n']:>6} {z['pf']:>11.3f} {r['pf']:>9.3f} {(z['net_r']-r['net_r'])/r['n']:>16.3f}")

print("\n=== What the best breakout variant is actually worth, in money ===")
s=mk('M2.0 momentum-candle','full'); r=run(s,2.5,5.0,0,n)
print(f" M2.0 full SL2.5/TP5.0: {r['n']} trades over 5 years, net {r['net_r']:+.2f}R")
print(f"   at 2% risk on a $500 account (=$10/R): ${10*r['net_r']:+.2f} total, "
      f"${10*r['net_r']/5:+.2f}/year")
print(f"   per-trade expectancy: {r['net_r']/r['n']:+.4f}R  (baseline: {16.6/115:+.4f}R)")
