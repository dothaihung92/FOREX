"""Backtest the requested two-sided martingale grid on real XAUUSD M5 data.

Design under test: BUY+SELL opened together, 10 pending levels per side at
$1 spacing, +0.01 lot per level, basket take-profit $1.50, no stop-loss.
"""
import sys; sys.path.insert(0, '/home/user/FOREX')
import pandas as pd

from gold_bot.config import load_config
from gold_bot.two_sided_grid import (
    GridParams, opening_cost, run_grid, total_ladder_lots,
)

cfg = load_config('/home/user/FOREX/config/config.yaml')
df = pd.read_csv('/home/user/FOREX/data/XAUUSD_M5_real.csv')
df['time'] = pd.to_datetime(df['time'], utc=True)
df = df.set_index('time').sort_index()

SPREAD = cfg.backtest.spread_points * 0.01
SLIP = cfg.backtest.slippage_points * 0.01
START = 500.0

p = GridParams(step=1.0, levels=10, base_lot=0.01, lot_increment=0.01,
               tp_usd=1.5, spread=SPREAD, slippage=SLIP)

print("=" * 72)
print("  TWO-SIDED MARTINGALE GRID - exactly as specified")
print("=" * 72)
print(f"  data      : {df.index[0].date()} -> {df.index[-1].date()}  ({len(df):,} M5 bars)")
print(f"  costs     : spread ${SPREAD:.2f}, slippage ${SLIP:.2f}/side (Exness Standard)")
print(f"  grid      : {p.levels} levels/side, ${p.step:.0f} apart, "
      f"lots {p.base_lot} +{p.lot_increment} per level")
print(f"  basket TP : ${p.tp_usd:.2f}    stop-loss: NONE")
print()

# ---------------------------------------------------------------- arithmetic
ladder = total_ladder_lots(p)
cost = opening_cost(p)
print("--- The arithmetic, before any backtest ---")
print(f"  lots if one side fills completely : {ladder} lots = {ladder * 100:.0f} oz")
print(f"  a $1 move against a full side     : ${ladder * 100:.2f}")
print(f"  cost to open the full grid        : ${cost:.2f}  (spread + slippage, both sides)")
print(f"  profit target for that same grid  : ${p.tp_usd:.2f}")
print(f"  target / cost                     : {p.tp_usd / cost:.3f}x")
print()

# ------------------------------------------------------------------ backtest
print("--- Backtest, $500 start ---")
r = run_grid(df, p, start_balance=START)
print(f"  cycles completed        : {r['n_cycles']:,}   "
      f"({r['n_tp']:,} hit the $1.50 target)")
print(f"  final balance           : ${r['balance']:,.2f}")
print(f"  floating at end         : ${r['final_floating']:,.2f}")
print(f"  net                     : ${r['net']:,.2f}")
print(f"  worst floating loss     : ${r['worst_floating_usd']:,.2f}")
print(f"  largest one-side lots   : {r['max_lots_one_side']}")
print(f"  max equity drawdown     : {r['max_dd_pct']:.1f}%")
if r['ruined_at'] is not None:
    print(f"  ACCOUNT WIPED OUT       : {r['ruined_time']}  (bar {r['ruined_at']:,})")
    frac = r['ruined_at'] / len(df)
    days = (df.index[r['ruined_at']] - df.index[0]).days
    print(f"                            after {days} days "
          f"({100 * frac:.1f}% of the test period)")
else:
    print("  survived the whole period")
print()

# ---------------------------------------- how much capital would it need?
print("--- What starting balance would have survived? ---")
for bal in (500, 1_000, 5_000, 10_000, 50_000, 100_000):
    rr = run_grid(df, p, start_balance=float(bal))
    if rr['ruined_at'] is None:
        print(f"  ${bal:>7,}: survived   net ${rr['net']:>+10,.2f}  "
              f"({100 * rr['net'] / bal:>+7.1f}%)  worst float ${rr['worst_floating_usd']:>10,.2f}")
    else:
        days = (df.index[rr['ruined_at']] - df.index[0]).days
        print(f"  ${bal:>7,}: WIPED OUT after {days:>4} days   "
              f"worst float ${rr['worst_floating_usd']:>10,.2f}")
print()

# --------------------------------------------- does a wider grid help?
print("--- Variations: does anything rescue it? ---")
print(f"  {'variant':<34} {'result':>26} {'worst float':>14}")
variants = [
    ("as requested ($1 step, TP $1.5)", GridParams(step=1.0, levels=10, tp_usd=1.5,
                                                   spread=SPREAD, slippage=SLIP)),
    ("wider step $3", GridParams(step=3.0, levels=10, tp_usd=1.5, spread=SPREAD, slippage=SLIP)),
    ("wider step $5", GridParams(step=5.0, levels=10, tp_usd=1.5, spread=SPREAD, slippage=SLIP)),
    ("bigger target $15", GridParams(step=1.0, levels=10, tp_usd=15.0, spread=SPREAD, slippage=SLIP)),
    ("bigger target $50", GridParams(step=1.0, levels=10, tp_usd=50.0, spread=SPREAD, slippage=SLIP)),
    ("5 levels only", GridParams(step=1.0, levels=5, tp_usd=1.5, spread=SPREAD, slippage=SLIP)),
    ("20 levels", GridParams(step=1.0, levels=20, tp_usd=1.5, spread=SPREAD, slippage=SLIP)),
    ("flat 0.01 lot (no martingale)", GridParams(step=1.0, levels=10, tp_usd=1.5,
                                                 lot_increment=0.0, spread=SPREAD, slippage=SLIP)),
    ("zero cost (impossible, for diagnosis)", GridParams(step=1.0, levels=10, tp_usd=1.5,
                                                         spread=0.0, slippage=0.0)),
]
for name, vp in variants:
    rr = run_grid(df, vp, start_balance=START)
    if rr['ruined_at'] is not None:
        days = (df.index[rr['ruined_at']] - df.index[0]).days
        result = f"WIPED OUT after {days} days"
    else:
        result = f"net ${rr['net']:+,.2f}"
    print(f"  {name:<34} {result:>26} {rr['worst_floating_usd']:>14,.2f}")
print()

# ----------------------------------------------- with a basket loss cap
print("--- Same design but WITH a basket loss cap (the only change that matters) ---")
print(f"  {'cap':<12} {'net':>14} {'cycles':>9} {'stopped':>9} {'max DD':>9}")
for cap in (25, 50, 100, 200):
    vp = GridParams(step=1.0, levels=10, tp_usd=1.5, spread=SPREAD, slippage=SLIP,
                    max_basket_loss_usd=float(cap))
    rr = run_grid(df, vp, start_balance=START)
    stopped = sum(1 for c in rr['cycles'] if c.closed_reason == 'guard_stop')
    tag = "WIPED" if rr['ruined_at'] is not None else f"${rr['net']:+,.2f}"
    print(f"  ${cap:<11} {tag:>14} {rr['n_cycles']:>9,} {stopped:>9,} {rr['max_dd_pct']:>8.1f}%")
