"""GoldBot_FixedCapitalRisk.mq5 v2 must trade the strategy that was backtested.

MetaTrader is not available here, so the EA's own recurrences and entry
conditions are transcribed below line for line from the .mq5 source and
compared against gold_bot.indicators / gold_bot.strategy on real data.
If someone edits one side and not the other, this fails.
"""
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from gold_bot.config import load_config
from gold_bot.indicators import atr, ema, macd, rsi
from gold_bot.strategy import generate_signals, in_session

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data" / "XAUUSD_M5_real.csv"
pytestmark = pytest.mark.skipif(not DATA.exists(), reason="real data not present")


# ---- transcribed from the .mq5 (EmaSeries / WilderRsi / WilderAtr / LoadM5) ----
def ea_ema(x, period):
    a = 2.0 / (period + 1.0); out = np.empty(len(x)); out[0] = x[0]
    for i in range(1, len(x)):
        out[i] = a * x[i] + (1.0 - a) * out[i - 1]
    return out


def ea_rsi(c, period):
    a = 1.0 / period; out = np.empty(len(c)); out[0] = 50.0; ag = al = 0.0
    for i in range(1, len(c)):
        d = c[i] - c[i - 1]; g = max(d, 0.0); l = max(-d, 0.0)
        if i == 1:
            ag, al = g, l
        else:
            ag = a * g + (1 - a) * ag; al = a * l + (1 - a) * al
        out[i] = 100.0 if al == 0 else 100.0 - 100.0 / (1.0 + ag / al)
    return out


def ea_atr(h, l, c, period):
    a = 1.0 / period; out = np.empty(len(c)); out[0] = h[0] - l[0]
    for i in range(1, len(c)):
        tr = max(h[i] - l[i], abs(h[i] - c[i - 1]), abs(l[i] - c[i - 1]))
        out[i] = a * tr + (1 - a) * out[i - 1]
    return out


@pytest.fixture(scope="module")
def data():
    df = pd.read_csv(DATA, nrows=40000)
    df["time"] = pd.to_datetime(df["time"], utc=True)
    return df.set_index("time")


def test_ea_indicator_recurrences_match_python(data):
    w = data.iloc[10000:10600]                  # one EA window: 600 closed bars
    c, h, l = (w[k].to_numpy() for k in ("close", "high", "low"))
    full = data.iloc[:10600]                    # python computed on long history
    py_ema = ema(full["close"], 100).iloc[-1]
    py_rsi = rsi(full["close"], 14).iloc[-1]
    py_atr = atr(full, 14).iloc[-1]
    py_hist = macd(full["close"])[2].iloc[-1]
    e12, e26 = ea_ema(c, 12), ea_ema(c, 26)
    m = e12 - e26
    ea_hist = (m - ea_ema(m, 9))[-1]
    assert ea_ema(c, 100)[-1] == pytest.approx(py_ema, abs=1e-3)
    assert ea_rsi(c, 14)[-1] == pytest.approx(py_rsi, abs=1e-6)
    assert ea_atr(h, l, c, 14)[-1] == pytest.approx(py_atr, rel=1e-6)
    assert ea_hist == pytest.approx(py_hist, abs=1e-6)


def test_mt5_style_macd_would_not_match():
    """Why the EA stopped using iMACD: an SMA signal line gives a different histogram."""
    rng = np.random.default_rng(0)
    c = 2000 + np.cumsum(rng.normal(0, 1, 800))
    m = ea_ema(c, 12) - ea_ema(c, 26)
    ema_sig = m - ea_ema(m, 9)
    sma_sig = m - pd.Series(m).rolling(9).mean().to_numpy()
    assert np.nanmax(np.abs(ema_sig[100:] - sma_sig[100:])) > 0.05


def test_ea_entry_conditions_reproduce_the_strategy_signals(data):
    cfg = load_config(ROOT / "config" / "config.yaml")
    s = cfg.strategy
    sig = generate_signals(data.copy(), s, cfg.sessions)
    O, H, L, C = (data[k].to_numpy() for k in ("open", "high", "low", "close"))
    ef, es = ea_ema(C, s.ema_fast), ea_ema(C, s.ema_slow)
    r, a = ea_rsi(C, s.rsi_period), ea_atr(H, L, C, s.atr_period)
    m = ea_ema(C, 12) - ea_ema(C, 26); hist = m - ea_ema(m, 9)
    htf, htf2 = sig["htf_trend"].to_numpy(), sig["htf2_trend"].to_numpy()   # EA reads these via iMA
    excluded = set(s.excluded_hours)

    ea_signal = np.zeros(len(C), int)
    for k in range(700, len(C)):
        t = data.index[k]
        up = C[k] > es[k] and htf[k] > 0
        dn = C[k] < es[k] and htf[k] < 0
        os_ = any(r[i] <= s.rsi_oversold + 5 for i in range(k - 6, k))
        ob_ = any(r[i] >= s.rsi_overbought - 5 for i in range(k - 6, k))
        xu = r[k] > s.rsi_pullback_level and r[k - 1] <= s.rsi_pullback_level
        xd = r[k] < 100 - s.rsi_pullback_level and r[k - 1] >= 100 - s.rsi_pullback_level
        rise, fall = hist[k] > hist[k - 1], hist[k] < hist[k - 1]
        sess = in_session(t, cfg.sessions)
        hour_ok = t.hour not in excluded
        trend_ok = abs(ef[k] - es[k]) / C[k] * 100 > s.min_trend_strength_pct
        atr_ok = (not s.require_atr_expansion) or a[k] > a[k - s.atr_expansion_period + 1:k + 1].mean()
        rng_ = H[k] - L[k]
        pos = (C[k] - L[k]) / rng_ if rng_ > 0 else None
        cl_long = s.min_close_position <= 0 or (pos is not None and pos >= s.min_close_position)
        cl_short = s.min_close_position <= 0 or (pos is not None and 1 - pos >= s.min_close_position)
        h2l = (not s.require_htf2_confirmation) or htf2[k] > 0
        h2s = (not s.require_htf2_confirmation) or htf2[k] < 0
        if up and os_ and xu and rise and C[k] > ef[k] and sess and hour_ok and trend_ok and h2l and atr_ok and cl_long:
            ea_signal[k] = 1
        elif dn and ob_ and xd and fall and C[k] < ef[k] and sess and hour_ok and trend_ok and h2s and atr_ok and cl_short:
            ea_signal[k] = -1

    py = sig["signal"].to_numpy().copy(); py[:700] = 0
    assert (py != 0).sum() >= 5, "slice too quiet to be a meaningful check"
    assert np.array_equal(ea_signal, py), (
        f"EA fires on {np.flatnonzero(ea_signal).tolist()[:10]}, strategy on {np.flatnonzero(py).tolist()[:10]}")
