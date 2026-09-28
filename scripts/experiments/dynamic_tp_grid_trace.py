"""Step-by-step trace of what GoldBot_DynamicTP_FastClose actually does.

The aggregate backtest says "$500 wiped out on day one". This replays that
day bar by bar so the mechanism is visible: how the basket fills, what the
dynamic target does as it fills, and where the account dies.
"""
import sys; sys.path.insert(0, '/home/user/FOREX')
import pandas as pd

from gold_bot.config import load_config
from gold_bot.dynamic_tp_grid import CONTRACT, EaParams, EaState

cfg = load_config('/home/user/FOREX/config/config.yaml')
df = pd.read_csv('/home/user/FOREX/data/XAUUSD_M5_real.csv')
df['time'] = pd.to_datetime(df['time'], utc=True)
df = df.set_index('time').sort_index()

SPREAD = cfg.backtest.spread_points * 0.01
SLIP = cfg.backtest.slippage_points * 0.01
p = EaParams(spread=SPREAD, slippage=SLIP)
HALF = SPREAD / 2.0

close = df['close'].to_numpy(float)
high = df['high'].to_numpy(float)
low = df['low'].to_numpy(float)
open_ = df['open'].to_numpy(float)
ema_f = df['close'].ewm(span=p.ema_fast, adjust=False).mean().to_numpy()
ema_s = df['close'].ewm(span=p.ema_slow, adjust=False).mean().to_numpy()

START = 500.0
balance = START
st = EaState(p)
events = []
ruined_bar = None

for i in range(max(p.ema_fast, p.ema_slow), 400):
    if st.count():
        b = st.profit(low[i] - HALF, low[i] + HALF)
        s = st.profit(high[i] - HALF, high[i] + HALF)
        path = (low[i], high[i]) if b <= s else (high[i], low[i])
    else:
        path = (low[i], high[i]) if close[i] >= open_[i] else (high[i], low[i])

    for mid in (open_[i], path[0], path[1], close[i]):
        bid, ask = mid - HALF, mid + HALF
        for _ in range(p.max_layers + 2):
            cnt = st.count()
            if cnt:
                pnl = st.profit(bid, ask)
                st.worst_floating = min(st.worst_floating, pnl)
                equity = balance + pnl
                if pnl >= st.required_tp() or equity <= 0.0:
                    reason = 'TP' if pnl >= st.required_tp() else 'STOP-OUT'
                    lots, n = st.volume(), cnt
                    realised = st.close_all(bid, ask)
                    balance += realised
                    events.append((df.index[i], reason, n, lots, realised, balance, equity))
                    if reason == 'STOP-OUT' or balance <= 0:
                        ruined_bar = i
                        break
                    st = EaState(p)
                    continue
            if st.count() == 0:
                if ema_f[i] > ema_s[i]:
                    st._add(True, p.initial_lot, mid)
                elif ema_f[i] < ema_s[i]:
                    st._add(False, p.initial_lot, mid)
                break
            if st.count() >= p.max_layers:
                break
            lot = st.next_lot()
            if st.count() >= p.trigger_pos_count:
                bp, sp = st.side_profit(bid, ask)
                if bp >= sp:
                    last = st.last_price(True)
                    if last == 0.0 or abs(ask - last) >= p.step_price:
                        st._add(True, lot, mid); continue
                else:
                    last = st.last_price(False)
                    if last == 0.0 or abs(bid - last) >= p.step_price:
                        st._add(False, lot, mid); continue
                break
            else:
                hi, lo = st.extremes()
                if ask >= hi + p.step_price:
                    st._add(True, lot, mid); continue
                if lo > 0.0 and bid <= lo - p.step_price:
                    st._add(False, lot, mid); continue
                break
        if ruined_bar is not None:
            break
    if ruined_bar is not None:
        break

print("=" * 78)
print("  TRACE: GoldBot_DynamicTP_FastClose, $500 account, real XAUUSD M5")
print(f"  from {df.index[100]}   (costs: spread ${SPREAD:.2f}, slip ${SLIP:.2f}/side)")
print("=" * 78)
print(f"\n{'time':<22} {'event':<9} {'pos':>4} {'lots':>6} {'realised':>10} {'balance':>10}")
for t, reason, n, lots, realised, bal, eq in events:
    print(f"{str(t):<22} {reason:<9} {n:>4} {lots:>6.2f} {realised:>+10.2f} {bal:>10.2f}")

if ruined_bar is not None:
    last = events[-1]
    print(f"\n  ACCOUNT DEAD at {last[0]}")
    print(f"    it had won {sum(1 for e in events if e[1] == 'TP')} baskets in a row first")
    print(f"    final basket: {last[2]} positions, {last[3]:.2f} lots "
          f"= {last[3] * CONTRACT:.0f} oz")
    print(f"    equity at the moment of death: ${last[6]:,.2f}")
    print(f"    realised on forced close:      ${last[4]:,.2f}")
    hours = (last[0] - df.index[100]).total_seconds() / 3600
    print(f"    elapsed: {hours:.1f} hours")

print("\n" + "=" * 78)
print("  WHY: the target recedes exactly as the basket gets dangerous")
print("=" * 78)
demo = EaState(EaParams(spread=0.0, slippage=0.0))
print(f"\n{'positions':>10} {'lots':>7} {'target':>9} {'net exp':>9} {'$ move needed':>15}")
for k in range(1, 21):
    demo._add(k % 2 == 0, demo.next_lot(), 2000.0)
    if k in (1, 6, 10, 15, 20):
        net = abs(sum((1 if q.is_buy else -1) * q.lots for q in demo.positions)) * CONTRACT
        need = demo.required_tp() / net if net else float('inf')
        print(f"{k:>10} {demo.volume():>7.2f} ${demo.required_tp():>8.2f} "
              f"${net:>8.2f} ${need:>14.2f}")
print("\n  A perfectly balanced basket has zero net exposure and a non-zero")
print("  target, so no price on earth closes it. It just sits there.")
