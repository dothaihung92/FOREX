import sys; sys.path.insert(0,'/home/user/FOREX')
import numpy as np, pandas as pd
from gold_bot.dynamic_tp_grid import EaParams, EaState, CONTRACT

p = EaParams(spread=0.25, slippage=0.05, lot_step=0.0)
HALF = p.spread/2

print("=== Rổ 12 lệnh hedge (6 buy / 6 sell), lot phẳng 0.01 ===")
st = EaState(p)
# xây rổ giống thực tế: giá dao động nên cả 2 bên đều có lệnh rải rác
entries = [(True,1998.0),(False,2002.0),(True,1997.0),(False,2003.0),
           (True,1996.0),(False,2004.0),(True,1999.0),(False,2001.0),
           (True,1995.0),(False,2005.0),(True,2000.0),(False,2000.0)]
for is_buy, price in entries:
    st._add(is_buy, 0.01, price)

print(f"  buy lots={sum(q.lots for q in st.positions if q.is_buy):.2f}  "
      f"sell lots={sum(q.lots for q in st.positions if not q.is_buy):.2f}  "
      f"net exposure=${st.net_exposure() if hasattr(st,'net_exposure') else 0:.2f}")
print()
print(f"  {'giá':>9} {'buyProfit':>11} {'sellProfit':>12} {'EA nhồi vào':>14}")
prev=None; flips=0; samples=0
for price in np.arange(1999.0, 2001.05, 0.10):
    bp, sp = st.side_profit(price-HALF, price+HALF)
    side = 'BUY' if bp >= sp else 'SELL'
    if prev is not None and side != prev: flips += 1
    prev = side; samples += 1
    print(f"  {price:>9.2f} {bp:>11.2f} {sp:>12.2f} {side:>14}")
print(f"\n  Chỉ trong biên độ $2, quyết định đổi chiều {flips} lần / {samples} mẫu.")

print("\n=== Cần giá nhích bao nhiêu để lật quyết định? ===")
for n_pos in (6, 10, 14, 20):
    st2 = EaState(p)
    for k in range(n_pos):
        st2._add(k % 2 == 0, 0.01, 2000.0 + (1.0 if k % 2 else -1.0)*(k//2 + 1)*0.5)
    lo, hi = 1990.0, 2010.0
    base = None; flip_at = None
    for price in np.arange(lo, hi, 0.01):
        bp, sp = st2.side_profit(price-HALF, price+HALF)
        side = bp >= sp
        if base is None:
            base = side; start = price
        elif side != base:
            flip_at = price; break
    delta = abs(flip_at - 2000.0) if flip_at else None
    d = f"${delta:.2f}" if delta else "khong lat"
    print(f"  rổ {n_pos:>2} lệnh: quyết định lật khi giá đi {d} so với 2000.00")
