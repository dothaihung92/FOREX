"""Does a price-action pattern memory learn anything that holds up?

Every configuration tried is printed - no cherry-picking. Learning runs from
the first bar; trades count from 2021 so the memory has had a year to fill.
"""
import sys, time; sys.path.insert(0, '/home/user/FOREX')
from dataclasses import replace
import numpy as np, pandas as pd
from gold_bot.pa_learner import PAConfig, build_candidates, decode, resolve, run_online

df = pd.read_csv('/home/user/FOREX/data/XAUUSD_M5_real.csv'); df['time'] = pd.to_datetime(df['time'], utc=True)
df = df.set_index('time').sort_index()
LIVE = pd.Timestamp('2021-01-01', tz='UTC'); SPLIT = pd.Timestamp('2023-01-01', tz='UTC')


def halves(t):
    a = t[t.time < SPLIT]; b = t[t.time >= SPLIT]
    f = lambda x: f"n={len(x):>4} meanR {x.R.mean():+.3f} win {100*(x.R>0).mean():4.1f}%" if len(x) else "n=   0"
    return f"2021-22: {f(a)} | 2023-25: {f(b)}"


def no_learning(res):
    busy = -1; out = []
    for r in res.itertuples(index=False):
        if r.time >= LIVE and r.m5_idx > busy:
            out.append((r.time, r.R)); busy = r.resolved
    return pd.DataFrame(out, columns=["time", "R"])


for tf_rule, per in (("15min", 3), ("1h", 12)):
    for rr in (1.5, 2.0, 3.0):
        cfg = PAConfig(tf_rule=tf_rule, tf_bars=per, rr=rr)
        t0 = time.time()
        res = resolve(df, build_candidates(df, cfg), cfg)
        print(f"\n######## signal TF {tf_rule}, target {rr}R | {len(res):,} in-trend setups ({time.time()-t0:.0f}s)")
        print(f"all setups, unconditional: meanR {res.R.mean():+.3f}, win {100*(res.R>0).mean():.1f}%")

        # Does a pattern's record in 2020-22 say anything about 2023-25?
        a = res[res.time < SPLIT].groupby('code').R.agg(['mean', 'count'])
        b = res[res.time >= SPLIT].groupby('code').R.agg(['mean', 'count'])
        j = a.add_suffix('_a').join(b.add_suffix('_b')).dropna()
        j = j[(j.count_a >= 30) & (j.count_b >= 30)]
        corr = np.corrcoef(j.mean_a, j.mean_b)[0, 1] if len(j) > 3 else float('nan')
        top = j.sort_values('mean_a', ascending=False).head(max(1, len(j) // 5))
        print(f"pattern persistence ({len(j)} patterns seen 30+ times in both halves): "
              f"corr(2020-22 meanR, 2023-25 meanR) = {corr:+.2f}; best fifth in 2020-22 "
              f"scored {top.mean_a.mean():+.3f} -> {top.mean_b.mean():+.3f} in 2023-25")

        print(f"  no learning (take every in-trend setup) : {halves(no_learning(res))}")
        for thr in (0.0, 0.1, 0.2):
            for k in (10, 30, 100):
                c2 = replace(cfg, threshold_r=thr, prior_k=k, min_seen=30)
                t, mem = run_online(res, c2, warmup_until=LIVE)
                print(f"  learn thr {thr:.1f} prior {k:>3}            : {halves(t)}")
        if tf_rule == "15min" and rr == 2.0:
            c2 = replace(cfg, threshold_r=0.1, prior_k=30)
            t, mem = run_online(res, c2, warmup_until=LIVE)
            best = np.argsort([-mem.expected_r(c) if mem.n[c] >= 30 else 9 for c in range(len(mem.n))])[:8]
            print("  patterns the memory rated best by the end:")
            for c in best:
                print(f"     {mem.expected_r(c):+.3f}R  seen {int(mem.n[c]):>4}  {decode(int(c))}")
