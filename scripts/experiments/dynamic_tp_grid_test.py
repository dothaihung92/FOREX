"""Backtest the uploaded GoldBot_DynamicTP_FastClose EA on real XAUUSD M5."""
import sys; sys.path.insert(0, '/home/user/FOREX')
import pandas as pd

from gold_bot.config import load_config
from gold_bot.dynamic_tp_grid import EaParams, run_ea

cfg = load_config('/home/user/FOREX/config/config.yaml')
df = pd.read_csv('/home/user/FOREX/data/XAUUSD_M5_real.csv')
df['time'] = pd.to_datetime(df['time'], utc=True)
df = df.set_index('time').sort_index()

SPREAD = cfg.backtest.spread_points * 0.01
SLIP = cfg.backtest.slippage_points * 0.01

print("=" * 74)
print("  GoldBot_DynamicTP_FastClose.mq5  -  ported and backtested")
print("=" * 74)
print(f"  data  : {df.index[0].date()} -> {df.index[-1].date()}  ({len(df):,} M5 bars)")
print(f"  costs : spread ${SPREAD:.2f}, slippage ${SLIP:.2f}/side (Exness Standard)")
print(f"  EA    : EMA {50}/{100} entry, step ${1.0:.0f}, focus at 6 positions,")
print(f"          max 20 layers, TP = max($2, lots x $10), NO stop-loss")
print()

base = EaParams(spread=SPREAD, slippage=SLIP)


def show(label, r, bal):
    if r['ruined_at'] is not None:
        days = (df.index[r['ruined_at']] - df.index[0]).days
        outcome = f"WIPED OUT after {days} days"
    else:
        outcome = f"net ${r['net']:+,.2f} ({100 * r['net'] / bal:+.1f}%)"
    print(f"  {label:<26} {outcome:<30} worst float ${r['worst_floating_usd']:>12,.2f}")


print("--- Headline run, $500 start ---")
r = run_ea(df, base, start_balance=500.0)
print(f"  baskets closed        : {r['n_cycles']:,}  ({r['n_tp']:,} hit the dynamic TP)")
print(f"  final balance         : ${r['balance']:,.2f}")
print(f"  floating at end       : ${r['final_floating']:,.2f}")
print(f"  net                   : ${r['net']:,.2f}")
print(f"  worst floating loss   : ${r['worst_floating_usd']:,.2f}")
print(f"  largest basket        : {r['max_positions']} positions, {r['max_lots']:.2f} lots")
print(f"  max equity drawdown   : {r['max_dd_pct']:.1f}%")
if r['ruined_at'] is not None:
    days = (df.index[r['ruined_at']] - df.index[0]).days
    print(f"  ACCOUNT WIPED OUT     : {r['ruined_time']}  (after {days} days)")
else:
    print("  survived the whole period")
print()

print("--- Same EA, different starting capital ---")
for bal in (500, 1_000, 5_000, 10_000, 50_000, 100_000):
    show(f"${bal:,}", run_ea(df, base, start_balance=float(bal)), bal)
print()

print("--- Which setting is load-bearing? ($10,000 start) ---")
variants = [
    ("as uploaded", base),
    ("step $3", EaParams(step_price=3.0, spread=SPREAD, slippage=SLIP)),
    ("step $5", EaParams(step_price=5.0, spread=SPREAD, slippage=SLIP)),
    ("max 10 layers", EaParams(max_layers=10, spread=SPREAD, slippage=SLIP)),
    ("max 40 layers", EaParams(max_layers=40, spread=SPREAD, slippage=SLIP)),
    ("focus at 3 positions", EaParams(trigger_pos_count=3, spread=SPREAD, slippage=SLIP)),
    ("focus at 12 positions", EaParams(trigger_pos_count=12, spread=SPREAD, slippage=SLIP)),
    ("flat 0.01 lot (no ramp)", EaParams(lot_step=0.0, spread=SPREAD, slippage=SLIP)),
    ("TP $5/lot (easier)", EaParams(profit_per_lot_usd=5.0, spread=SPREAD, slippage=SLIP)),
    ("TP $20/lot (harder)", EaParams(profit_per_lot_usd=20.0, spread=SPREAD, slippage=SLIP)),
    ("zero costs (impossible)", EaParams(spread=0.0, slippage=0.0)),
]
for name, vp in variants:
    show(name, run_ea(df, vp, start_balance=10_000.0), 10_000)
print()

print("--- Does adding a basket loss cap save it? ($10,000 start) ---")
print(f"  {'cap':<10} {'result':<32} {'baskets':>9} {'stopped':>9}")
for cap in (50, 100, 250, 500, 1000):
    vp = EaParams(spread=SPREAD, slippage=SLIP, max_basket_loss_usd=float(cap))
    rr = run_ea(df, vp, start_balance=10_000.0)
    stopped = sum(1 for c in rr['cycles'] if c.reason == 'guard_stop')
    if rr['ruined_at'] is not None:
        days = (df.index[rr['ruined_at']] - df.index[0]).days
        res = f"WIPED OUT after {days} days"
    else:
        res = f"net ${rr['net']:+,.2f}"
    print(f"  ${cap:<9} {res:<32} {rr['n_cycles']:>9,} {stopped:>9,}")
