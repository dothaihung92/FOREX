"""Build the market snapshot the advisor sees.

Everything here is computed from CLOSED bars only:

* Higher-timeframe bars are used only once complete (a half-formed H1 bar
  would leak information that did not exist yet).
* A swing high/low is accepted as support/resistance only after `right`
  later bars have confirmed it, so a level never appears before it could
  have been known.

The model is handed numbers, not a screenshot: it is more reliable at
reading a table of prices and indicator values than at estimating prices
off pixels, and it costs far fewer tokens.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from ..indicators import atr, ema, macd, rsi

TF_RULES = {"M15": ("15min", 3), "H1": ("1h", 12), "H4": ("4h", 48)}   # rule, M5 bars per bar
CANDLES_SHOWN = {"M5": 24, "M15": 16, "H1": 12, "H4": 8}


@dataclass
class Context:
    time: pd.Timestamp
    bar_index: int
    price: float
    bid: float
    ask: float
    spread: float
    hour: int
    atr: float                          # M5 ATR(14): the unit for stops
    tf: dict = field(default_factory=dict)
    levels: dict = field(default_factory=dict)
    position: dict | None = None
    guard: dict = field(default_factory=dict)
    limits: dict = field(default_factory=dict)

    def to_prompt(self) -> str:
        return render_prompt(self)


# --------------------------------------------------------------------------- data
def resample_complete(m5: pd.DataFrame, rule: str, bars_per: int) -> pd.DataFrame:
    """Resample to a higher timeframe, keeping only bars that are fully formed."""
    g = m5.resample(rule, label="left", closed="left")
    out = g.agg({"open": "first", "high": "max", "low": "min", "close": "last"})
    out["n"] = g["close"].count()
    out = out[out["n"] >= bars_per].drop(columns="n")
    return out.dropna()


def _tf_summary(df: pd.DataFrame, name: str) -> dict | None:
    if len(df) < 30:
        return None
    close = df["close"]
    e50, e100 = ema(close, 50), ema(close, 100)
    a = atr(df, 14)
    hist = macd(close)[2]
    c, f, s = float(close.iloc[-1]), float(e50.iloc[-1]), float(e100.iloc[-1])
    ai = float(a.iloc[-1]) if not np.isnan(a.iloc[-1]) else 0.0
    if c > f > s:
        trend = "up"
    elif c < f < s:
        trend = "down"
    else:
        trend = "mixed"
    n = CANDLES_SHOWN[name]
    candles = [
        (str(ix)[5:16], round(float(r.open), 2), round(float(r.high), 2),
         round(float(r.low), 2), round(float(r.close), 2))
        for ix, r in df.tail(n).iterrows()
    ]
    return {
        "trend": trend, "close": round(c, 2), "ema50": round(f, 2), "ema100": round(s, 2),
        "rsi": round(float(rsi(close, 14).iloc[-1]), 1), "atr": round(ai, 2),
        "macd_hist": round(float(hist.iloc[-1]), 3),
        "macd_hist_prev": round(float(hist.iloc[-2]), 3),
        "dist_ema50_atr": round((c - f) / ai, 2) if ai else 0.0,
        "candles": candles,
    }


# --------------------------------------------------------------- support / resistance
def find_pivots(high: np.ndarray, low: np.ndarray, left: int = 3, right: int = 3):
    """Confirmed swing highs/lows as (index, price).

    A pivot at j needs `right` later bars, so the newest `right` bars can
    never be pivots yet - that is the no-lookahead guarantee.
    """
    n = len(high)
    highs, lows = [], []
    for j in range(left, n - right):
        wh = high[j - left: j + right + 1]
        wl = low[j - left: j + right + 1]
        if high[j] == wh.max() and (wh == high[j]).sum() == 1:
            highs.append((j, float(high[j])))
        if low[j] == wl.min() and (wl == low[j]).sum() == 1:
            lows.append((j, float(low[j])))
    return highs, lows


def cluster_levels(prices: list[float], tol: float) -> list[tuple[float, int]]:
    """Merge nearby swing prices into (level, touches)."""
    if not prices:
        return []
    prices = sorted(prices)
    groups, cur = [], [prices[0]]
    for p in prices[1:]:
        if p - np.mean(cur) <= tol:
            cur.append(p)
        else:
            groups.append(cur)
            cur = [p]
    groups.append(cur)
    return [(float(np.mean(g)), len(g)) for g in groups]


def support_resistance(tf_frames: dict, price: float, m5_atr: float) -> dict:
    swing_prices: list[float] = []
    h1_atr = 0.0
    for name in ("H1", "M15"):
        df = tf_frames.get(name)
        if df is None or len(df) < 20:
            continue
        tail = df.tail(300)
        hi, lo = find_pivots(tail["high"].to_numpy(), tail["low"].to_numpy())
        swing_prices += [p for _, p in hi] + [p for _, p in lo]
        if name == "H1":
            a = atr(df, 14).iloc[-1]
            h1_atr = float(a) if not np.isnan(a) else 0.0
    tol = max(0.5 * (h1_atr or 3 * m5_atr), 0.5)
    levels = cluster_levels(swing_prices, tol)
    unit = m5_atr if m5_atr > 0 else 1.0

    def pack(lv):
        p, t = lv
        return {"price": round(p, 2), "touches": t, "dist_atr": round(abs(p - price) / unit, 2)}

    above = sorted([lv for lv in levels if lv[0] > price], key=lambda x: x[0] - price)[:3]
    below = sorted([lv for lv in levels if lv[0] < price], key=lambda x: price - x[0])[:3]
    return {"resistance": [pack(x) for x in above], "support": [pack(x) for x in below]}


# ------------------------------------------------------------------------- builder
def build_context(m5: pd.DataFrame, *, bar_index: int, spread: float, position: dict | None,
                  guard: dict, limits: dict, lookback: int = 5000) -> Context | None:
    """`m5` must contain CLOSED bars only; its last row is the bar that just closed."""
    window = m5.tail(lookback)
    if len(window) < 200:
        return None
    a5 = atr(window, 14).iloc[-1]
    if np.isnan(a5) or a5 <= 0:
        return None
    price = float(window["close"].iloc[-1])
    frames = {"M5": window}
    for name, (rule, per) in TF_RULES.items():
        frames[name] = resample_complete(window, rule, per)
    tf = {}
    for name, df in frames.items():
        s = _tf_summary(df, name)
        if s:
            tf[name] = s
    t = window.index[-1]
    return Context(
        time=t, bar_index=bar_index, price=price,
        bid=price - spread / 2, ask=price + spread / 2, spread=spread,
        hour=int(t.hour), atr=float(a5), tf=tf,
        levels=support_resistance(frames, price, float(a5)),
        position=position, guard=guard, limits=limits,
    )


# ------------------------------------------------------------------------- prompt
def render_prompt(c: Context) -> str:
    L = [f"TIME {c.time:%Y-%m-%d %H:%M} UTC | XAUUSD | last closed M5 price {c.price:.2f} "
         f"| spread {c.spread:.2f} | M5 ATR(14) {c.atr:.2f}"]
    for name in ("H4", "H1", "M15", "M5"):
        s = c.tf.get(name)
        if not s:
            continue
        L.append(
            f"\n[{name}] trend={s['trend']} close={s['close']} EMA50={s['ema50']} EMA100={s['ema100']} "
            f"RSI={s['rsi']} ATR={s['atr']} MACDhist={s['macd_hist']} (prev {s['macd_hist_prev']}) "
            f"dist_from_EMA50={s['dist_ema50_atr']} M5-ATRs")
        L.append("  candles (UTC open high low close), oldest first:")
        L.extend(f"  {t} {o} {h} {l} {cl}" for t, o, h, l, cl in s["candles"])
    L.append("\nSUPPORT / RESISTANCE (confirmed swing levels; touches = how often tested):")
    for kind in ("resistance", "support"):
        for lv in c.levels.get(kind, []):
            L.append(f"  {kind}: {lv['price']}  touches={lv['touches']}  {lv['dist_atr']} ATR away")
    if not (c.levels.get("resistance") or c.levels.get("support")):
        L.append("  none found")
    p = c.position
    if p:
        L.append(
            f"\nOPEN POSITION: {p['side']} {p['lots']} lot, entry {p['entry']}, SL {p['sl']}, TP {p['tp']}, "
            f"floating {p['pnl']:+.2f} USD = {p['r']:+.2f}R, held {p['bars_held']} M5 bars, "
            f"best so far {p['max_fav_r']:+.2f}R, stop already at breakeven or better: {p['sl_at_be']}")
    else:
        L.append("\nNO OPEN POSITION.")
    g = c.guard
    L.append(f"\nACCOUNT STATE: trades today {g.get('trades_today')}/{g.get('max_trades_per_day')}, "
             f"consecutive losses {g.get('consecutive_losses')}, day P/L {g.get('day_pnl', 0):+.2f} USD")
    lim = c.limits
    L.append(f"HARD LIMITS (enforced by code; violating proposals are discarded): stop distance must be "
             f"{lim.get('sl_min'):.2f}-{lim.get('sl_max'):.2f} USD from price; net reward:risk >= "
             f"{lim.get('min_rr')}; confidence >= {lim.get('min_confidence')}.")
    return "\n".join(L)
