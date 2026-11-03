"""How to raise the return of the default strategy - and what each lever costs.

All runs use the corrected cost model (full round-trip spread). A change is
only worth adopting if it helps BOTH the 2020-2022 and the 2023-2025 halves.
"""
import sys; sys.path.insert(0, '/home/user/FOREX')
from dataclasses import replace
import numpy as np, pandas as pd
from gold_bot.backtester import run_backtest
from gold_bot.config import load_config
from gold_bot.strategy import generate_signals

cfg = load_config('/home/user/FOREX/config/config.yaml')
df = pd.read_csv('/home/user/FOREX/data/XAUUSD_M5_real.csv'); df['time'] = pd.to_datetime(df['time'], utc=True)
df = df.set_index('time').sort_index()
sig = generate_signals(df.copy(), cfg.strategy, cfg.sessions)
SPLIT = pd.Timestamp('2023-01-01', tz='UTC')
parts = {'ALL': sig, 'TRAIN 20-22': sig[sig.index < SPLIT], 'TEST 23-25': sig[sig.index >= SPLIT]}


def run(name, risk=cfg.risk, strat=cfg.strategy, bt=cfg.backtest, verbose=True):
    out = {}
    for label, d in parts.items():
        r = run_backtest(d, strat, risk, bt)
        pnl = np.array([t.pnl for t in r.trades if t.exit_time is not None])
        eq = r.equity_curve
        peak = eq.cummax()
        out[label] = dict(n=len(pnl), net=eq.iloc[-1] - r.initial_balance,
                          ret=100 * (eq.iloc[-1] / r.initial_balance - 1),
                          pf=(pnl[pnl > 0].sum() / -pnl[pnl <= 0].sum()) if (pnl <= 0).any() else float('inf'),
                          dd=100 * ((eq - peak) / peak).min())
    if verbose:
        a, tr, te = out['ALL'], out['TRAIN 20-22'], out['TEST 23-25']
        print(f"{name:<38} ALL {a['ret']:>+7.1f}% DD {a['dd']:>6.1f}% PF {a['pf']:4.2f} | "
              f"train {tr['ret']:>+6.1f}% PF {tr['pf']:4.2f} | test {te['ret']:>+6.1f}% PF {te['pf']:4.2f}")
    return out


print("=== 1. RISK PER TRADE (fixed capital, no compounding) ===")
for rp in (2.0, 3.0, 4.0, 5.0, 6.0, 8.0):
    run(f"risk {rp:.0f}% of $500 per trade", risk=replace(cfg.risk, risk_per_trade_pct=rp))

print("\n=== 2. COMPOUNDING (risk % of CURRENT equity) ===")
for rp in (1.0, 2.0, 3.0):
    run(f"compounding {rp:.0f}% of equity", risk=replace(cfg.risk, sizing_mode='percent_risk', risk_per_trade_pct=rp))

print("\n=== 3. TRAILING STOP distance (x ATR) ===")
for m in (0.8, 1.0, 1.2, 1.5, 2.0, 3.0):
    run(f"trailing {m} ATR", risk=replace(cfg.risk, trailing_atr_mult=m))
run("trailing OFF", risk=replace(cfg.risk, use_trailing_stop=False))

print("\n=== 4. STOP / TARGET multiples (x ATR), trailing on ===")
for sl, tp in ((1.5, 3.0), (1.8, 3.0), (2.0, 3.0), (1.8, 4.0), (1.8, 5.0), (1.8, 8.0), (2.5, 5.0)):
    run(f"SL {sl} / TP {tp}", strat=replace(cfg.strategy, atr_sl_mult=sl, atr_tp_mult=tp))
