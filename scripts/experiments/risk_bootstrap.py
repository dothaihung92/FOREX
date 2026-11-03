"""What can go wrong at each risk level? The single historical path hides it.

Reshuffles the strategy's real per-trade results (in R) thousands of times to
see the spread of outcomes: final return, deepest drawdown, and how often the
account is hurt badly, at each risk-per-trade setting. Trades are drawn
independently, which ignores clustering of losses - if anything that
understates the danger.
"""
import sys; sys.path.insert(0, '/home/user/FOREX')
import numpy as np, pandas as pd
from gold_bot.backtester import run_backtest
from gold_bot.config import load_config
from gold_bot.strategy import generate_signals

cfg = load_config('/home/user/FOREX/config/config.yaml')
df = pd.read_csv('/home/user/FOREX/data/XAUUSD_M5_real.csv'); df['time'] = pd.to_datetime(df['time'], utc=True)
df = df.set_index('time').sort_index()
sig = generate_signals(df.copy(), cfg.strategy, cfg.sessions)
r = run_backtest(sig, cfg.strategy, cfg.risk, cfg.backtest)
risk_usd = cfg.risk.base_equity * cfg.risk.risk_per_trade_pct / 100
R = np.array([t.pnl for t in r.trades if t.exit_time is not None]) / risk_usd
print(f"{len(R)} trades | mean {R.mean():+.3f}R | win {100*(R>0).mean():.1f}% | worst {R.min():+.2f}R | best {R.max():+.2f}R")
longest = cur = 0
for x in R:
    cur = cur + 1 if x < 0 else 0
    longest = max(longest, cur)
print(f"longest historical losing streak: {longest}\n")

rng = np.random.default_rng(0)
N = 20000
idx = rng.integers(0, len(R), (N, len(R)))
paths = R[idx]                                       # N x trades, in R
print(f"{'risk/trade':>10} | {'median 5y':>9} {'5th pct':>8} {'95th pct':>8} | {'median maxDD':>12} {'95th pct DD':>11} | "
      f"{'P(DD>25%)':>9} {'P(DD>50%)':>9} {'P(lose money)':>13}")
for rp in (1, 2, 3, 4, 5, 6, 8, 10):
    eq = 1 + np.cumsum(paths * rp / 100.0, axis=1)   # fixed-capital: each trade risks rp% of the ORIGINAL base
    peak = np.maximum.accumulate(np.concatenate([np.ones((N, 1)), eq], axis=1), axis=1)[:, 1:]
    dd = ((eq - peak) / peak).min(axis=1) * 100
    fin = (eq[:, -1] - 1) * 100
    print(f"{rp:>9}% | {np.median(fin):>+8.0f}% {np.percentile(fin, 5):>+7.0f}% {np.percentile(fin, 95):>+7.0f}% | "
          f"{np.median(dd):>11.1f}% {np.percentile(dd, 5):>10.1f}% | "
          f"{100*(dd < -25).mean():>8.1f}% {100*(dd < -50).mean():>8.1f}% {100*(fin < 0).mean():>12.1f}%")
