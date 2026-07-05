import numpy as np
import pandas as pd
import pytest


@pytest.fixture
def synthetic_ohlc():
    """A few days of synthetic M5 XAUUSD-like OHLC data with a clear
    up-trend segment and a down-trend segment, so strategy tests have
    something concrete to detect."""
    rng = np.random.default_rng(42)
    n = 2000
    idx = pd.date_range("2024-01-02 00:00", periods=n, freq="5min", tz="UTC")

    trend = np.concatenate(
        [
            np.linspace(0, 30, n // 2),       # uptrend
            np.linspace(30, 0, n - n // 2),   # downtrend
        ]
    )
    noise = rng.normal(0, 0.6, n).cumsum() * 0.05
    close = 1900 + trend + noise
    open_ = np.roll(close, 1)
    open_[0] = close[0]
    high = np.maximum(open_, close) + rng.uniform(0.1, 0.8, n)
    low = np.minimum(open_, close) - rng.uniform(0.1, 0.8, n)

    return pd.DataFrame({"open": open_, "high": high, "low": low, "close": close}, index=idx)
