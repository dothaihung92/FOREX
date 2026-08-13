"""BB + RSI 30/70 mean-reversion, TP swept over 3..6.

User's spec: "BB 30-70 buy/sell, khung TP 3-6". Two readings of "TP 3-6"
are plausible in this project's history (ATR multiples, as in the 5:3 R:R
work; or dollars on gold, as in the recent TP-$2 tests), so both are
measured rather than guessed.

Entry (pure BB+RSI, no trend filter - that is what was asked for):
  BUY  : close back inside the lower band AND RSI < 30
  SELL : close back inside the upper band AND RSI > 70
Exit: fixed SL/TP, whichever the bar touches first (SL wins ties).

Real Exness Standard costs: 25-point spread, 5-point slippage/side, no
commission.
"""
import sys; sys.path.insert(0, '/home/user/FOREX')
import numpy as np
import pandas as pd
from gold_bot.config import load_config
from gold_bot.indicators import atr as atr_f, bollinger_bands, rsi as rsi_f

cfg = load_config('/home/user/FOREX/config/config.yaml')
df = pd.read_csv('/home/user/FOREX/data/XAUUSD_M5_real.csv')
df['time'] = pd.to_datetime(df['time'], utc=True)
df = df.set_index('time').sort_index()

close, high, low = df['close'], df['high'], df['low']
bb_u, bb_m, bb_l = bollinger_bands(close, cfg.strategy.bb_period, cfg.strategy.bb_std_mult)
df['rsi'] = rsi_f(close, cfg.strategy.rsi_period)
df['atr'] = atr_f(df, cfg.strategy.atr_period)
df['bb_u'], df['bb_l'] = bb_u, bb_l

prev_c = close.shift(1)
long_sig = (prev_c < bb_l.shift(1)) & (close >= bb_l) & (df['rsi'] < 30)
short_sig = (prev_c > bb_u.shift(1)) & (close <= bb_u) & (df['rsi'] > 70)

sig = np.where(long_sig, 1, np.where(short_sig, -1, 0)).astype(int)

o = df['open'].to_numpy(); h = df['high'].to_numpy()
l = df['low'].to_numpy(); c = df['close'].to_numpy()
atr = df['atr'].to_numpy()
valid = ~np.isnan(atr) & ~np.isnan(df['rsi'].to_numpy()) & ~np.isnan(bb_l.to_numpy())
sig = np.where(valid, sig, 0)

SPREAD = cfg.backtest.spread_points * 0.01
SLIP = cfg.backtest.slippage_points * 0.01
CONTRACT = 100.0
MIN_LOT, LOT_STEP = 0.01, 0.01
n = len(c)

print(f"bars={n}  raw BUY signals={int((sig==1).sum())}  raw SELL signals={int((sig==-1).sum())}")
print(f"costs: spread=${SPREAD:.2f}  slippage=${SLIP:.2f}/side  commission=$0\n")


def run(sl_val, tp_val, mode, capital=500.0, risk_pct=2.0):
    """mode='atr' -> sl/tp are ATR multiples; mode='usd' -> absolute $ moves."""
    balance = capital; peak = capital; mdd = 0.0
    wins = losses = 0; gross_w = gross_l = 0.0
    i = 0
    equity_curve = []
    while i < n:
        if sig[i] == 0:
            i += 1; continue
        d = int(sig[i])
        entry = c[i] + d * (SPREAD / 2 + SLIP)
        if mode == 'atr':
            sl_dist = sl_val * atr[i]; tp_dist = tp_val * atr[i]
        else:
            sl_dist = sl_val; tp_dist = tp_val
        if sl_dist <= 0:
            i += 1; continue
        # fixed-capital risk sizing, same rule the project uses live
        lot = (risk_pct / 100.0 * capital) / (sl_dist * CONTRACT)
        lot = max(MIN_LOT, round(lot / LOT_STEP) * LOT_STEP)
        sl_p = entry - d * sl_dist
        tp_p = entry + d * tp_dist

        j = i + 1
        exit_p = None
        while j < n:
            hit_sl = (l[j] <= sl_p) if d == 1 else (h[j] >= sl_p)
            hit_tp = (h[j] >= tp_p) if d == 1 else (l[j] <= tp_p)
            if hit_sl:          # conservative: SL resolves first on a tie
                exit_p = sl_p - d * SLIP; break
            if hit_tp:
                exit_p = tp_p - d * SLIP; break
            j += 1
        if exit_p is None:
            break
        pnl = d * (exit_p - entry) * CONTRACT * lot
        balance += pnl
        if pnl >= 0: wins += 1; gross_w += pnl
        else: losses += 1; gross_l -= pnl
        if balance > peak: peak = balance
        if (balance - peak) / peak < mdd: mdd = (balance - peak) / peak
        equity_curve.append(balance)
        i = j + 1

    trades = wins + losses
    pf = (gross_w / gross_l) if gross_l > 0 else float('inf')
    return {
        'trades': trades, 'win_rate': 100 * wins / trades if trades else 0.0,
        'net': balance - capital, 'ret_pct': 100 * (balance - capital) / capital,
        'pf': pf, 'mdd': 100 * mdd,
        'avg_w': gross_w / wins if wins else 0.0,
        'avg_l': gross_l / losses if losses else 0.0,
    }


def table(title, rows, sl_label, tp_label):
    print(f"=== {title} ===")
    print(f"{sl_label:>8} {tp_label:>6} {'trades':>7} {'win%':>7} {'net$':>10} {'ret%':>8} {'PF':>6} {'maxDD%':>8}")
    for sl, tp, s in rows:
        pf = '  inf' if s['pf'] == float('inf') else f"{s['pf']:6.2f}"
        print(f"{sl:>8} {tp:>6} {s['trades']:>7} {s['win_rate']:>7.1f} {s['net']:>+10.2f} "
              f"{s['ret_pct']:>+8.1f} {pf} {s['mdd']:>8.1f}")
    print()


rows = []
for sl in [1.5, 2.0, 3.0]:
    for tp in [3.0, 4.0, 5.0, 6.0]:
        rows.append((sl, tp, run(sl, tp, 'atr')))
table("Reading A: TP/SL as ATR multiples (TP swept 3..6)", rows, 'SL(ATR)', 'TP(ATR)')

rows = []
for sl in [2.0, 3.0, 4.0]:
    for tp in [3.0, 4.0, 5.0, 6.0]:
        rows.append((sl, tp, run(sl, tp, 'usd')))
table("Reading B: TP/SL as absolute USD moves on gold (TP swept $3..$6)", rows, 'SL($)', 'TP($)')
