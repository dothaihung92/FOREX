import sys; sys.path.insert(0,'/home/user/FOREX')
import pandas as pd
from gold_bot.config import load_config
from gold_bot.dynamic_tp_grid import EaParams, run_ea

cfg=load_config('/home/user/FOREX/config/config.yaml')
df=pd.read_csv('/home/user/FOREX/data/XAUUSD_M5_real.csv'); df['time']=pd.to_datetime(df['time'],utc=True)
df=df.set_index('time').sort_index()
S=cfg.backtest.spread_points*0.01; L=cfg.backtest.slippage_points*0.01

RULES=[('profit  (hiện tại)','profit'),('ema     (xu hướng EMA)','ema'),
       ('momentum(60 phút)','momentum'),('net     (bên nhiều lot hơn)','net'),
       ('onesided(không hedge)','onesided'),('loser   (trung bình giá)','loser')]
print(f"{'quy tắc chọn hướng':<28} {'$500':<20} {'$1,000':<20} {'$5,000':<20} {'wf':>9}")
for label, rule in RULES:
    row=[]; wf=0
    for bal in (500.0,1000.0,5000.0):
        p=EaParams(lot_step=0.0, spread=S, slippage=L, max_layers=20,
                   max_basket_loss_usd=100.0, focus_rule=rule)
        r=run_ea(df,p,start_balance=bal)
        wf=min(wf,r['worst_floating_usd'])
        if r['ruined_at'] is not None:
            d=(df.index[r['ruined_at']]-df.index[0]).days; row.append(f"CHAY {d}d")
        else:
            row.append(f"{100*r['net']/bal:+.0f}% ({r['n_cycles']:,} ro)")
    print(f"{label:<28} {row[0]:<20} {row[1]:<20} {row[2]:<20} {wf:>9,.0f}")
