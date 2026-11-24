"""Strategy review: more trades AND more profit - what actually delivers it?

The yardstick is return per unit of drawdown (ret/DD), not profit factor:
with fixed-fraction sizing, risk per trade can be scaled until drawdown hits
a chosen limit, so the strategy with the best ret/DD makes the most money at
any given level of pain. Every stream must be profitable in BOTH 2020-22 and
2023-25.

All runs: real backtester, full round-trip spread + slippage, $10,000 fixed
base at 1% risk (large enough that the minimum lot never inflates risk).
"""
import sys, time; sys.path.insert(0, '/home/user/FOREX')
from dataclasses import replace
import numpy as np, pandas as pd
from gold_bot.backtester import run_backtest
from gold_bot.config import SessionWindow, load_config
from gold_bot.indicators import atr, ema
from gold_bot.strategy import generate_signals

cfg = load_config('/home/user/FOREX/config/config.yaml')
RISK = replace(cfg.risk, sizing_mode='fixed_capital_percent_risk', base_equity=10000.0,
               risk_per_trade_pct=1.0, min_lot=0.01)
BT = replace(cfg.backtest, initial_balance=10000.0)
m5 = pd.read_csv('/home/user/FOREX/data/XAUUSD_M5_real.csv'); m5['time'] = pd.to_datetime(m5['time'], utc=True)
m5 = m5.set_index('time').sort_index()
SPLIT = pd.Timestamp('2023-01-01', tz='UTC')
YEARS = (m5.index[-1] - m5.index[0]).days / 365.25
ALLDAY = [SessionWindow('all', '00:00', '23:59')]
LONDON_NY = [SessionWindow('ldn_ny', '07:00', '20:00')]


def resample(rule, per):
    g = m5.resample(rule, label='left', closed='left')
    b = g.agg({'open': 'first', 'high': 'max', 'low': 'min', 'close': 'last'})
    b['n'] = g['close'].count()
    return b[b['n'] >= per].drop(columns='n').dropna()


def evaluate(name, sig, strat=cfg.strategy, risk=RISK, keep=None):
    rows = {}
    for label, part in (('ALL', sig), ('20-22', sig[sig.index < SPLIT]), ('23-25', sig[sig.index >= SPLIT])):
        r = run_backtest(part, strat, risk, BT)
        tr = [t for t in r.trades if t.exit_time is not None]
        pnl = np.array([t.pnl for t in tr])
        eq = r.equity_curve
        dd = 100 * ((eq - eq.cummax()) / eq.cummax()).min()
        ret = 100 * (eq.iloc[-1] / r.initial_balance - 1)
        pf = pnl[pnl > 0].sum() / -pnl[pnl <= 0].sum() if (pnl <= 0).any() else np.inf
        rows[label] = dict(n=len(pnl), ret=ret, dd=dd, pf=pf, rdd=ret / abs(dd) if dd < 0 else np.inf)
        if label == 'ALL' and keep is not None:
            keep[name] = [(t.exit_time, t.pnl) for t in tr]
    a, x, y = rows['ALL'], rows['20-22'], rows['23-25']
    ok = x['ret'] > 0 and y['ret'] > 0
    print(f"{name:<44} {a['n']/YEARS:>6.0f}/yr | ret {a['ret']:>+6.1f}% DD {a['dd']:>6.1f}% ret/DD {a['rdd']:>5.2f} PF {a['pf']:4.2f} | "
          f"20-22 {x['ret']:>+6.1f}% | 23-25 {y['ret']:>+6.1f}% | {'OK' if ok else 'FAIL'}")
    return rows, ok


streams = {}
t0 = time.time()
print(f"{'stream':<44} {'trades':>9} | 5 years, 1% risk per trade on a fixed base\n")

print("--- 1. current M5 strategy, and what each filter costs ---")
base = generate_signals(m5.copy(), cfg.strategy, cfg.sessions)
evaluate('M5 current (all filters)', base, keep=streams)
for nm, st, ses in (
    ('M5 sessions 07-20 continuous', cfg.strategy, LONDON_NY),
    ('M5 no H1 confirmation', replace(cfg.strategy, require_htf2_confirmation=False), cfg.sessions),
    ('M5 no ATR-expansion filter', replace(cfg.strategy, require_atr_expansion=False), cfg.sessions),
    ('M5 no close-position filter', replace(cfg.strategy, min_close_position=0.0), cfg.sessions),
    ('M5 no trend-strength filter', replace(cfg.strategy, min_trend_strength_pct=0.0), cfg.sessions),
    ('M5 07-20 + no ATR-expansion', replace(cfg.strategy, require_atr_expansion=False), LONDON_NY),
):
    evaluate(nm, generate_signals(m5.copy(), st, ses), strat=st)

print("\n--- 2. the same logic on higher timeframes (different trades) ---")
m15 = resample('15min', 3)
s15 = replace(cfg.strategy, htf_timeframe='H1', htf2_timeframe='H4')
evaluate('M15 same logic (HTF H1, HTF2 H4)', generate_signals(m15.copy(), s15, cfg.sessions), strat=s15, keep=streams)
evaluate('M15 same logic, sessions 07-20', generate_signals(m15.copy(), s15, LONDON_NY), strat=s15, keep=streams)
h1 = resample('1h', 12)
s60 = replace(cfg.strategy, htf_timeframe='H4', htf2_timeframe='D1')
evaluate('H1 same logic (HTF H4, HTF2 D1), 07-20', generate_signals(h1.copy(), s60, LONDON_NY), strat=s60, keep=streams)

print("\n--- 3. classic trend-following: H1 Donchian 20 breakout with H4 trend, all hours ---")
print("    (pre-set textbook parameters: 20-bar channel, stop 2 ATR, trail 3 ATR, target 8 ATR; not tuned)")
d = h1.copy()
d['atr'] = atr(d, 14)
h4c = resample('4h', 48)['close']
h4t = np.sign(h4c - ema(h4c, 50)).shift(1)                       # closed H4 bars only
d['h4'] = h4t.reindex(d.index, method='ffill')
hi20 = d['high'].rolling(20).max().shift(1); lo20 = d['low'].rolling(20).min().shift(1)
d['signal'] = np.where((d['close'] > hi20) & (d['h4'] > 0), 1, np.where((d['close'] < lo20) & (d['h4'] < 0), -1, 0))
d['signal_type'] = np.where(d['signal'] != 0, 'trend', None)
s_dc = replace(cfg.strategy, atr_sl_mult=2.0, atr_tp_mult=8.0)
r_dc = replace(RISK, trailing_atr_mult=3.0)
evaluate('H1 Donchian-20 + H4 trend', d, strat=s_dc, risk=r_dc, keep=streams)
dl = d.copy(); dl['signal'] = dl['signal'].clip(lower=0)
evaluate('H1 Donchian-20 + H4 trend, LONG only', dl, strat=s_dc, risk=r_dc)
ds = d.copy(); ds['signal'] = ds['signal'].clip(upper=0)
evaluate('H1 Donchian-20 + H4 trend, SHORT only', ds, strat=s_dc, risk=r_dc)

print(f"\n({time.time()-t0:.0f}s)")
pd.to_pickle(streams, '/tmp/claude-0/-home-user-FOREX/54a11648-6990-5ad9-a93a-58d45b489686/scratchpad/streams.pkl')
