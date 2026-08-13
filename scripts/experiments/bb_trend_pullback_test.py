"""BB used as a trend-following entry trigger, not a reversal signal.

The rejected BB+RSI 30/70 test bought band pierces against the trend. This
tests the opposite framing: keep every validated filter from the default
strategy (EMA 50/100 stack, M15 + H1 confluence, trend-strength gate,
session and excluded-hour filters) and swap ONLY the entry trigger from
the RSI pullback to a Bollinger-based one.

Variants:
  V1 mid-bounce  : price dipped to/below the BB midline, then closed back
                   above it (long); mirrored for short.
  V2 mid+macd    : V1 plus MACD histogram confirming.
  V3 lower-band  : price touched the lower band and closed back above it,
                   but only while the higher timeframes confirm an uptrend.
  V4 mid+rsi     : V1 plus the RSI pullback also firing (both must agree).

Baseline = the shipped default strategy, same costs, same period.
Reported on the project's train (2020-2023) / test (2023-2025) split, so a
variant has to work in both regimes to count.
"""
import sys; sys.path.insert(0, '/home/user/FOREX')
import numpy as np
import pandas as pd
from gold_bot.config import load_config
from gold_bot.strategy import generate_signals

cfg = load_config('/home/user/FOREX/config/config.yaml')
df = pd.read_csv('/home/user/FOREX/data/XAUUSD_M5_real.csv')
df['time'] = pd.to_datetime(df['time'], utc=True)
df = df.set_index('time').sort_index()

base = generate_signals(df.copy(), cfg.strategy, cfg.sessions)

close = base['close']
bb_mid, bb_low, bb_up = base['bb_mid'], base['bb_lower'], base['bb_upper']

# Same trend/quality gates the default strategy uses, reused verbatim so
# the only thing changing between baseline and variants is the trigger.
uptrend = (close > base['ema_slow']) & (base['htf_trend'] > 0) & (base['htf2_trend'] > 0)
downtrend = (close < base['ema_slow']) & (base['htf_trend'] < 0) & (base['htf2_trend'] < 0)
trend_established = base['trend_strength_pct'] > cfg.strategy.min_trend_strength_pct
hour_ok = pd.Series(~base.index.hour.isin(cfg.strategy.excluded_hours), index=base.index)
macd_rising = base['macd_hist'] > base['macd_hist'].shift(1)
macd_falling = base['macd_hist'] < base['macd_hist'].shift(1)

gate_long = uptrend & trend_established & hour_ok
gate_short = downtrend & trend_established & hour_ok

prev_c = close.shift(1)
mid_bounce_up = (prev_c <= bb_mid.shift(1)) & (close > bb_mid)
mid_bounce_dn = (prev_c >= bb_mid.shift(1)) & (close < bb_mid)
band_bounce_up = (prev_c <= bb_low.shift(1)) & (close > bb_low)
band_bounce_dn = (prev_c >= bb_up.shift(1)) & (close < bb_up)
rsi_pb_up = (base['rsi'] > cfg.strategy.rsi_pullback_level) & (base['rsi'].shift(1) <= cfg.strategy.rsi_pullback_level)
rsi_pb_dn = (base['rsi'] < 100 - cfg.strategy.rsi_pullback_level) & (base['rsi'].shift(1) >= 100 - cfg.strategy.rsi_pullback_level)

VARIANTS = {
    'V1 mid-bounce':  (gate_long & mid_bounce_up, gate_short & mid_bounce_dn),
    'V2 mid+macd':    (gate_long & mid_bounce_up & macd_rising, gate_short & mid_bounce_dn & macd_falling),
    'V3 lower-band':  (gate_long & band_bounce_up, gate_short & band_bounce_dn),
    'V4 mid+rsi':     (gate_long & mid_bounce_up & rsi_pb_up, gate_short & mid_bounce_dn & rsi_pb_dn),
}

o = base['open'].to_numpy(); h = base['high'].to_numpy()
l = base['low'].to_numpy(); c = base['close'].to_numpy()
atr = base['atr'].to_numpy()
SPREAD = cfg.backtest.spread_points * 0.01
SLIP = cfg.backtest.slippage_points * 0.01
CONTRACT = 100.0
MIN_LOT, LOT_STEP = 0.01, 0.01
SL_M, TP_M = cfg.strategy.atr_sl_mult, cfg.strategy.atr_tp_mult  # shipped 5:3
n = len(c)
valid = ~np.isnan(atr)


def backtest(sig, lo, hi, capital=500.0, risk_pct=2.0):
    """lo/hi are integer bar bounds; entries only inside, exits may run past."""
    bal = capital; peak = capital; mdd = 0.0
    wins = losses = 0; gw = gl = 0.0
    i = lo
    while i < hi:
        if sig[i] == 0 or not valid[i]:
            i += 1; continue
        d = int(sig[i])
        entry = c[i] + d * (SPREAD / 2 + SLIP)
        sl_dist = SL_M * atr[i]; tp_dist = TP_M * atr[i]
        if sl_dist <= 0:
            i += 1; continue
        lot = max(MIN_LOT, round(((risk_pct / 100 * capital) / (sl_dist * CONTRACT)) / LOT_STEP) * LOT_STEP)
        sl_p = entry - d * sl_dist; tp_p = entry + d * tp_dist
        j = i + 1; ex = None
        while j < n:
            hit_sl = (l[j] <= sl_p) if d == 1 else (h[j] >= sl_p)
            hit_tp = (h[j] >= tp_p) if d == 1 else (l[j] <= tp_p)
            if hit_sl: ex = sl_p - d * SLIP; break
            if hit_tp: ex = tp_p - d * SLIP; break
            j += 1
        if ex is None: break
        pnl = d * (ex - entry) * CONTRACT * lot
        bal += pnl
        if pnl >= 0: wins += 1; gw += pnl
        else: losses += 1; gl -= pnl
        if bal > peak: peak = bal
        if (bal - peak) / peak < mdd: mdd = (bal - peak) / peak
        i = j + 1
    t = wins + losses
    return {'trades': t, 'win': 100 * wins / t if t else 0.0, 'net': bal - capital,
            'pf': (gw / gl) if gl > 0 else float('inf'), 'mdd': 100 * mdd}


SPLIT = pd.Timestamp('2023-01-01', tz='UTC')
split_i = int(base.index.searchsorted(SPLIT))
periods = [('TRAIN 2020-2022', 0, split_i), ('TEST 2023-2025', split_i, n)]

base_sig = base['signal'].to_numpy()

# V5/V6 keep the baseline trigger and use BB only as an extra FILTER on top
# - the one framing left where BB could add value without diluting the
# baseline's selectivity (56 trades in 3 years) that the variants above lose.
bb_pos_ok = np.where(base_sig == 1, (close > bb_mid).to_numpy(),
                     np.where(base_sig == -1, (close < bb_mid).to_numpy(), False))
bb_room_ok = np.where(base_sig == 1, (close < bb_up).to_numpy(),
                      np.where(base_sig == -1, (close > bb_low).to_numpy(), False))
VARIANTS['V5 baseline+BB side'] = None
VARIANTS['V6 baseline+BB room'] = None

rows = [('BASELINE (shipped RSI pullback)', base_sig)]
for name, v in VARIANTS.items():
    if v is None:
        continue
    lg, sh = v
    rows.append((name, np.where(lg.to_numpy(), 1, np.where(sh.to_numpy(), -1, 0)).astype(int)))
rows.append(('V5 baseline+BB side', np.where(bb_pos_ok, base_sig, 0)))
rows.append(('V6 baseline+BB room', np.where(bb_room_ok, base_sig, 0)))

print(f"XAUUSD M5 real data, {n} bars | costs: spread ${SPREAD:.2f}, slippage ${SLIP:.2f}/side, no commission")
print(f"SL {SL_M} ATR / TP {TP_M} ATR (shipped 5:3), $500 start, 2% fixed-capital risk\n")
for pname, lo, hi in periods:
    print(f"=== {pname} ===")
    print(f"{'variant':<32} {'trades':>7} {'win%':>7} {'net$':>10} {'PF':>7} {'maxDD%':>8}")
    for name, s in rows:
        r = backtest(s, lo, hi)
        pf = '    inf' if r['pf'] == float('inf') else f"{r['pf']:7.2f}"
        print(f"{name:<32} {r['trades']:>7} {r['win']:>7.1f} {r['net']:>+10.2f} {pf} {r['mdd']:>8.1f}")
    print()
