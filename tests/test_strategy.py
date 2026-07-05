from dataclasses import replace

import pandas as pd

from gold_bot.config import SessionWindow, StrategyConfig
from gold_bot.strategy import generate_signals, in_session

STRATEGY_CFG = StrategyConfig(
    ema_fast=50,
    ema_slow=200,
    rsi_period=14,
    rsi_oversold=30,
    rsi_overbought=70,
    rsi_pullback_level=45,
    atr_period=14,
    atr_sl_mult=1.5,
    atr_tp_mult=2.5,
    htf_timeframe="M15",
    htf_ema_period=100,
    adx_period=14,
    adx_threshold=20,
    bb_period=20,
    bb_std_mult=2.0,
    mr_atr_sl_mult=1.5,
    mr_atr_tp_mult=1.5,
)

ALL_DAY_SESSION = [SessionWindow(name="all", start="00:00", end="23:59")]
LONDON_ONLY = [SessionWindow(name="london", start="07:00", end="11:00")]


def test_in_session_basic():
    ts_in = pd.Timestamp("2024-01-01 08:00", tz="UTC")
    ts_out = pd.Timestamp("2024-01-01 20:00", tz="UTC")
    assert in_session(ts_in, LONDON_ONLY) is True
    assert in_session(ts_out, LONDON_ONLY) is False


def test_generate_signals_has_expected_columns(synthetic_ohlc):
    result = generate_signals(synthetic_ohlc, STRATEGY_CFG, ALL_DAY_SESSION)
    for col in ["ema_fast", "ema_slow", "rsi", "atr", "macd_hist", "htf_trend", "signal"]:
        assert col in result.columns
    assert set(result["signal"].unique()).issubset({-1, 0, 1})


def test_session_filter_blocks_all_signals_outside_window(synthetic_ohlc):
    # A session window that never matches any bar timestamp should produce
    # zero trade signals regardless of trend/momentum conditions.
    impossible_session = [SessionWindow(name="never", start="23:58", end="23:59")]
    result = generate_signals(synthetic_ohlc, STRATEGY_CFG, impossible_session)
    assert (result["signal"] == 0).all()


def test_trend_signals_only_fire_with_htf_trend_alignment(synthetic_ohlc):
    result = generate_signals(synthetic_ohlc, STRATEGY_CFG, ALL_DAY_SESSION)
    trend_rows = result[result["signal_type"] == "trend"]
    longs = trend_rows[trend_rows["signal"] == 1]
    shorts = trend_rows[trend_rows["signal"] == -1]
    if len(longs):
        assert (longs["htf_trend"] > 0).all()
    if len(shorts):
        assert (shorts["htf_trend"] < 0).all()


def test_mean_reversion_disabled_by_default(synthetic_ohlc):
    assert STRATEGY_CFG.enable_mean_reversion is False
    result = generate_signals(synthetic_ohlc, STRATEGY_CFG, ALL_DAY_SESSION)
    assert (result["signal_type"] != "mean_reversion").all()


def test_mean_reversion_signals_only_fire_in_ranging_regime_when_enabled(synthetic_ohlc):
    cfg = replace(STRATEGY_CFG, enable_mean_reversion=True)
    result = generate_signals(synthetic_ohlc, cfg, ALL_DAY_SESSION)
    mr_rows = result[result["signal_type"] == "mean_reversion"]
    if len(mr_rows):
        assert (mr_rows["adx"] < cfg.adx_threshold).all()


def test_trend_strength_filter_blocks_weak_trend_entries(synthetic_ohlc):
    # A very high threshold should eliminate all trend entries, since no
    # bar can have an EMA gap exceeding an absurdly large percentage.
    cfg = replace(STRATEGY_CFG, min_trend_strength_pct=1000.0)
    result = generate_signals(synthetic_ohlc, cfg, ALL_DAY_SESSION)
    assert (result["signal_type"] != "trend").all()


def test_trend_strength_filter_disabled_at_zero_threshold(synthetic_ohlc):
    cfg = replace(STRATEGY_CFG, min_trend_strength_pct=0.0)
    result = generate_signals(synthetic_ohlc, cfg, ALL_DAY_SESSION)
    trend_rows = result[result["signal_type"] == "trend"]
    if len(trend_rows):
        assert (trend_rows["trend_strength_pct"] > 0.0).all()
