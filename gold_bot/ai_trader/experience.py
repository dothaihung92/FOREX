"""Historical 'experience': what happened after situations like this one.

Claude cannot be trained through the API. What can be done is to show it
past situations and their real outcomes. That is only honest if every
example it sees had already RESOLVED before the decision being made -
otherwise the model is shown the answer. Each example therefore carries its
resolution bar, and `ExperienceStore.query` refuses to use anything that had
not resolved by the cutoff.

Features are computed causally and expressed in the direction of the trade
(a short is the mirror image of a long), so one pool of examples serves both.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from ..indicators import atr, ema, macd, rsi

CONTRACT = 100.0
FEATURES = ["tr5", "tr15", "tr60", "tr240", "d50", "rsi5", "rsi60", "macd5", "macd_slope",
            "atr_rel", "hi50", "lo50", "hi200", "lo200", "ret12", "hour_s", "hour_c"]

# (rule, M5 bars per bar)
HTF = {"15": ("15min", 3), "60": ("1h", 12), "240": ("4h", 48)}


def _trend(close, e50, e100):
    return np.where((close > e50) & (e50 > e100), 1.0, np.where((close < e50) & (e50 < e100), -1.0, 0.0))


def _htf_aligned(m5: pd.DataFrame, rule: str, per: int):
    """Trend and RSI of COMPLETE higher-timeframe bars, available only after they close."""
    g = m5.resample(rule, label="left", closed="left")
    o = g.agg({"open": "first", "high": "max", "low": "min", "close": "last"})
    o["n"] = g["close"].count()
    o = o[o["n"] >= per].drop(columns="n").dropna()
    c = o["close"]
    e50, e100 = ema(c, 50), ema(c, 100)
    out = pd.DataFrame({"trend": _trend(c.values, e50.values, e100.values), "rsi": rsi(c, 14).values},
                       index=o.index + pd.Timedelta(rule))        # index = the moment the bar CLOSES
    return out


def compute_features(m5: pd.DataFrame) -> pd.DataFrame:
    """One row per M5 bar, in the LONG orientation. Uses only information up to and
    including that bar's close."""
    c, h, l = m5["close"], m5["high"], m5["low"]
    a = atr(m5, 14)
    e50, e100 = ema(c, 50), ema(c, 100)
    hist = macd(c)[2]
    f = pd.DataFrame(index=m5.index)
    f["tr5"] = _trend(c.values, e50.values, e100.values)
    f["d50"] = (c - e50) / a
    f["rsi5"] = rsi(c, 14)
    f["macd5"] = hist / a
    f["macd_slope"] = np.sign(hist.diff())
    f["atr_rel"] = a / a.rolling(1000, min_periods=200).median()
    f["hi50"] = (h.rolling(50).max() - c) / a          # room to the recent high, in ATRs
    f["lo50"] = (c - l.rolling(50).min()) / a
    f["hi200"] = (h.rolling(200).max() - c) / a
    f["lo200"] = (c - l.rolling(200).min()) / a
    f["ret12"] = (c - c.shift(12)) / a
    hr = m5.index.hour + m5.index.minute / 60.0
    f["hour_s"] = np.sin(2 * np.pi * hr / 24.0)
    f["hour_c"] = np.cos(2 * np.pi * hr / 24.0)

    close_time = pd.DataFrame({"t": m5.index + pd.Timedelta("5min")}, index=m5.index)
    for key, (rule, per) in HTF.items():
        htf = _htf_aligned(m5, rule, per)
        m = pd.merge_asof(close_time, htf.reset_index().rename(columns={"index": "end", "time": "end"}),
                          left_on="t", right_on="end", direction="backward")
        f[f"tr{key}"] = m["trend"].values
        if key == "60":
            f["rsi60"] = m["rsi"].values
    f["atr"] = a
    return f


def orient(f: pd.DataFrame, direction: int) -> np.ndarray:
    """Feature matrix as seen by a trade in `direction` (+1 long / -1 short)."""
    X = f[FEATURES].copy()
    if direction == -1:
        for k in ("tr5", "tr15", "tr60", "tr240", "d50", "macd5", "macd_slope", "ret12"):
            X[k] = -X[k]
        X["rsi5"], X["rsi60"] = 100 - X["rsi5"], 100 - X["rsi60"]
        X["hi50"], X["lo50"] = f["lo50"], f["hi50"]
        X["hi200"], X["lo200"] = f["lo200"], f["hi200"]
    return X.to_numpy(dtype=float)


def label_trades(m5: pd.DataFrame, idx: np.ndarray, direction: int, *, sl_atr=1.8, tp_atr=3.0,
                 spread=0.25, slip=0.05, horizon=576):
    """Simulate a trade entered at the close of each bar in `idx`.

    Same conventions as SimBroker: costs on both sides, exits triggered on the
    bid/ask that would actually trade, a bar touching both stop and target
    books the stop. Returns (R multiple net of costs, bar index of resolution).
    R is pnl in units of the planned stop distance.
    """
    o = m5["open"].to_numpy(); h = m5["high"].to_numpy(); l = m5["low"].to_numpy()
    c = m5["close"].to_numpy(); a = atr(m5, 14).to_numpy()
    n = len(c)
    half = spread / 2
    R = np.full(len(idx), np.nan)
    J = np.full(len(idx), -1, dtype=np.int64)
    for k, i in enumerate(idx):
        if not np.isfinite(a[i]) or a[i] <= 0 or i + 1 >= n:
            continue
        d = direction
        entry = c[i] + d * (half + slip)
        sl_d, tp_d = sl_atr * a[i], tp_atr * a[i]
        sl, tp = entry - d * sl_d, entry + d * tp_d
        exit_px, j = None, i
        for j in range(i + 1, min(i + 1 + horizon, n)):
            if d == 1:
                bo, bh, bl = o[j] - half, h[j] - half, l[j] - half
                if bo <= sl: exit_px = bo - slip; break
                if bo >= tp: exit_px = bo; break
                if bl <= sl: exit_px = sl - slip; break
                if bh >= tp: exit_px = tp - slip; break
            else:
                ao, ah, al = o[j] + half, h[j] + half, l[j] + half
                if ao >= sl: exit_px = ao + slip; break
                if ao <= tp: exit_px = ao; break
                if ah >= sl: exit_px = sl + slip; break
                if al <= tp: exit_px = tp + slip; break
        if exit_px is None:                                   # timed out: mark to market
            j = min(i + horizon, n - 1)
            exit_px = c[j] - d * (half + slip)
        R[k] = d * (exit_px - entry) / sl_d
        J[k] = j
    return R, J


def build_examples(m5: pd.DataFrame, *, every=3, hours=range(8, 20), **label_kw) -> pd.DataFrame:
    """All candidate situations (both directions) with their features, outcome and resolution bar."""
    f = compute_features(m5)
    ok = f[FEATURES].notna().all(axis=1) & f["atr"].notna()
    ok &= pd.Series(m5.index.hour, index=m5.index).isin(list(hours))
    ok &= np.arange(len(m5)) % every == 0
    idx = np.flatnonzero(ok.to_numpy())
    rows = []
    for d in (1, -1):
        R, J = label_trades(m5, idx, d, **label_kw)
        X = orient(f.iloc[idx], d)
        df = pd.DataFrame(X, columns=FEATURES)
        df["bar"], df["dir"], df["R"], df["resolved"] = idx, d, R, J
        rows.append(df)
    out = pd.concat(rows, ignore_index=True)
    out = out[np.isfinite(out["R"]) & (out["resolved"] >= 0)]
    return out.sort_values("bar").reset_index(drop=True)
