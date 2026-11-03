"""Does past experience predict the future? Walk-forward, no peeking.

For every month from 2022 on, the 'experience' available is ONLY the examples
that had already resolved before that month began. Two learners are asked to
predict the net R of each situation in the month:

  kNN   what the pool of similar past situations would hand to a language model
  HGB   gradient boosting - a stronger learner, as an upper bound on what is
        learnable at all from these features

If neither can rank future situations by realised outcome, putting the same
information in front of an LLM cannot create an edge.
"""
import sys, time; sys.path.insert(0, '/home/user/FOREX')
import numpy as np, pandas as pd
from sklearn.ensemble import HistGradientBoostingRegressor
from sklearn.neighbors import NearestNeighbors
from sklearn.preprocessing import StandardScaler
from gold_bot.ai_trader.experience import FEATURES, build_examples

PKL = sys.argv[1] if len(sys.argv) > 1 else None
df = pd.read_csv('/home/user/FOREX/data/XAUUSD_M5_real.csv'); df['time'] = pd.to_datetime(df['time'], utc=True)
df = df.set_index('time').sort_index()
ex = pd.read_pickle(PKL) if PKL else build_examples(df)
t_bar = df.index[ex['bar'].to_numpy()]; t_res = df.index[ex['resolved'].to_numpy()]
ex['t_bar'], ex['t_res'] = t_bar, t_res
X_all = ex[FEATURES].to_numpy(float); R_all = ex['R'].to_numpy(float)

K = 150
months = pd.date_range('2022-01-01', '2025-08-01', freq='MS', tz='UTC')
parts = []
t0 = time.time()
for m0, m1 in zip(months[:-1], months[1:]):
    exp_mask = np.asarray(t_res < m0)                       # resolved BEFORE the month: the only legal knowledge
    test_mask = np.asarray((t_bar >= m0) & (t_bar < m1))
    if exp_mask.sum() < 20000 or test_mask.sum() == 0:
        continue
    sc = StandardScaler().fit(X_all[exp_mask])
    Xe, Re = sc.transform(X_all[exp_mask]), R_all[exp_mask]
    Xt = sc.transform(X_all[test_mask])
    nn = NearestNeighbors(n_neighbors=K, algorithm='kd_tree').fit(Xe)
    _, nb = nn.kneighbors(Xt)
    knn = Re[nb].mean(axis=1)
    hgb = HistGradientBoostingRegressor(max_depth=4, learning_rate=0.05, max_iter=150,
                                        min_samples_leaf=200, random_state=0).fit(Xe, np.clip(Re, -3, 3))
    p = ex.loc[test_mask, ['bar', 'dir', 'R', 'resolved', 't_bar']].copy()
    p['knn'], p['hgb'] = knn, hgb.predict(Xt)
    parts.append(p)
oos = pd.concat(parts)
print(f"walk-forward done in {time.time()-t0:.0f}s | out-of-sample situations: {len(oos):,} "
      f"({oos.t_bar.min():%Y-%m} to {oos.t_bar.max():%Y-%m})\n")
oos.to_pickle('/tmp/claude-0/-home-user-FOREX/54a11648-6990-5ad9-a93a-58d45b489686/scratchpad/oos.pkl')

base = oos['R'].mean()
print(f"unconditional out-of-sample: mean R {base:+.3f}, win {100*(oos.R>0).mean():.1f}%   (breakeven = 0)\n")

def deciles(col):
    q = pd.qcut(oos[col], 10, labels=False, duplicates='drop')
    g = oos.groupby(q)['R'].agg(['mean', 'count', lambda s: 100 * (s > 0).mean()])
    g.columns = ['meanR', 'n', 'win%']
    return g

for col in ('knn', 'hgb'):
    g = deciles(col)
    print(f"--- realised outcome by PREDICTED decile ({col}); 0 = worst predicted, 9 = best ---")
    print(g.round(3).to_string())
    corr = np.corrcoef(oos[col], oos['R'])[0, 1]
    print(f"correlation(predicted, realised R) = {corr:+.3f}\n")

# ---- the question that matters: top slice, year by year, and as a tradable sequence
def tradable(sel, label):
    sel = sel.sort_values('bar'); last = -1; took = []
    for _, r in sel.iterrows():
        if r['bar'] > last:                                   # one position at a time
            took.append(r); last = r['resolved']
    t = pd.DataFrame(took)
    return t

def block_ci(t, n=2000, seed=0):
    wk = t['t_bar'].dt.to_period('W').astype(str).to_numpy()
    uniq = np.unique(wk); rng = np.random.default_rng(seed)
    by = {w: t.loc[wk == w, 'R'].to_numpy() for w in uniq}
    means = []
    for _ in range(n):
        pick = rng.choice(uniq, len(uniq))
        v = np.concatenate([by[w] for w in pick])
        means.append(v.mean())
    return np.percentile(means, [2.5, 97.5])

for col in ('knn', 'hgb'):
    thr = oos[col].quantile(0.90)
    top = oos[oos[col] >= thr]
    print(f"=== top 10% by {col} (predicted R >= {thr:+.3f}) ===")
    print(f"  all selected:   n={len(top):,}  meanR {top.R.mean():+.3f}  win {100*(top.R>0).mean():.1f}%")
    t = tradable(top, col)
    lo, hi = block_ci(t)
    print(f"  tradable (one position at a time): n={len(t)}  meanR {t.R.mean():+.3f}  95% CI [{lo:+.3f}, {hi:+.3f}]  "
          f"win {100*(t.R>0).mean():.1f}%")
    for y, g in t.groupby(t['t_bar'].dt.year):
        print(f"      {y}: n={len(g):>4}  meanR {g.R.mean():+.3f}  win {100*(g.R>0).mean():.1f}%")
    print()
