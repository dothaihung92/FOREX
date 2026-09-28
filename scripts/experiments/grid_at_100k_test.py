import sys; sys.path.insert(0,'/home/user/FOREX')
import pandas as pd
from gold_bot.config import load_config
from gold_bot.dynamic_tp_grid import EaParams, run_ea
cfg=load_config('/home/user/FOREX/config/config.yaml')
df=pd.read_csv('/home/user/FOREX/data/XAUUSD_M5_real.csv'); df['time']=pd.to_datetime(df['time'],utc=True)
df=df.set_index('time').sort_index()
S=cfg.backtest.spread_points*0.01; L=cfg.backtest.slippage_points*0.01
BAL=100000.0
print("EA da sua, von $100,000, 5 nam du lieu that\n")
print(f"{'cau hinh':<34} {'ket qua':<24} {'%/nam':>8} {'lo noi te nhat':>16} {'max lot':>9}")
def run(name, lot, cap):
    p=EaParams(initial_lot=lot, lot_step=0.0, spread=S, slippage=L,
               max_layers=20, max_basket_loss_usd=cap,
               broker_max_lot=200.0, min_lot=0.01)
    r=run_ea(df,p,start_balance=BAL)
    if r['ruined_at'] is not None:
        d=(df.index[r['ruined_at']]-df.index[0]).days; res=f"CHAY sau {d} ngay"; yr='-'
    else:
        tot=100*r['net']/BAL; res=f"net ${r['net']:+,.0f} ({tot:+.2f}%)"; yr=f"{tot/5:+.2f}%"
    print(f"{name:<34} {res:<24} {yr:>8} {r['worst_floating_usd']:>16,.0f} {r['max_lots']:>9.2f}")

run('lot 0.01 (mac dinh hien tai)', 0.01, 100.0)
run('lot 0.10 (x10), cap $1,000', 0.10, 1000.0)
run('lot 0.50 (x50), cap $5,000', 0.50, 5000.0)
run('lot 1.00 (x100), cap $10,000', 1.00, 10000.0)
run('lot 2.00 (x200), cap $20,000', 2.00, 20000.0)
