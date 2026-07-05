import numpy as np
import pandas as pd

from gold_bot.indicators import adx, atr, bollinger_bands, ema, macd, rsi


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


def test_bollinger_bands_ordering(synthetic_ohlc):
    upper, mid, lower = bollinger_bands(synthetic_ohlc["close"], period=20, std_mult=2.0)
    valid = upper.notna() & mid.notna() & lower.notna()
    assert (upper[valid] >= mid[valid]).all()
    assert (mid[valid] >= lower[valid]).all()


def test_adx_bounded_and_high_for_strong_trend():
    s = pd.DataFrame(
        {
            "high": np.arange(1, 101, dtype=float) + 1,
            "low": np.arange(1, 101, dtype=float) - 1,
            "close": np.arange(1, 101, dtype=float),
        }
    )
    result = adx(s, 14)
    assert (result.dropna() >= 0).all()
    assert (result.dropna() <= 100).all()
    assert result.iloc[-1] > 30  # strong monotonic trend -> high ADX


def test_adx_low_for_flat_choppy_series():
    rng = np.random.default_rng(1)
    n = 200
    close = 100 + rng.normal(0, 0.3, n)  # no trend, pure noise around a flat level
    df = pd.DataFrame(
        {"high": close + 0.2, "low": close - 0.2, "close": close}
    )
    result = adx(df, 14)
    assert result.iloc[-1] < 25
