import numpy as np
import pandas as pd

from gold_bot.indicators import atr, ema, macd, rsi


def test_ema_converges_to_constant_series():
    s = pd.Series([100.0] * 50)
    result = ema(s, 10)
    assert abs(result.iloc[-1] - 100.0) < 1e-6


def test_rsi_is_bounded_0_100():
    rng = np.random.default_rng(0)
    s = pd.Series(100 + rng.normal(0, 1, 500).cumsum())
    result = rsi(s, 14)
    assert result.dropna().between(0, 100).all()


def test_rsi_high_for_monotonic_uptrend():
    s = pd.Series(np.arange(1, 60, dtype=float))
    result = rsi(s, 14)
    assert result.iloc[-1] > 90


def test_atr_nonnegative(synthetic_ohlc):
    result = atr(synthetic_ohlc, 14)
    assert (result.dropna() >= 0).all()


def test_macd_shapes_match(synthetic_ohlc):
    macd_line, signal_line, hist = macd(synthetic_ohlc["close"])
    assert len(macd_line) == len(signal_line) == len(hist) == len(synthetic_ohlc)
