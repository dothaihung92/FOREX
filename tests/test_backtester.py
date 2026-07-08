from dataclasses import replace

import numpy as np
import pandas as pd

from gold_bot.config import RiskConfig, StrategyConfig
from gold_bot.backtester import run_backtest_dca_grid

STRATEGY_CFG = StrategyConfig(
    ema_fast=50, ema_slow=200, rsi_period=14, rsi_oversold=30, rsi_overbought=70,
    rsi_pullback_level=45, atr_period=14, atr_sl_mult=1.5, atr_tp_mult=2.5,
    htf_timeframe="M15", htf_ema_period=100, adx_period=14, adx_threshold=20,
    bb_period=20, bb_std_mult=2.0, mr_atr_sl_mult=1.5, mr_atr_tp_mult=1.5,
)

DCA_RISK_CFG = RiskConfig(
    risk_per_trade_pct=1.0, max_trades_per_day=50, max_daily_loss_pct=100.0,
    max_concurrent_trades=1, use_trailing_stop=False, trailing_atr_mult=1.0,
    sizing_mode="dca_grid", base_equity=1000.0,
    dca_step_price=2.0, dca_leg_risk_pct=2.0, dca_max_legs=30, dca_hard_stop_pct=15.0,
)


def make_df(n, close_path, uptrend_until=None):
    """Bars every 5min; ema_slow/htf_trend/htf2_trend/atr/signal set directly
    (no need to run the full strategy pipeline) so each test controls the
    exact price path and trend state."""
    idx = pd.date_range("2024-01-02 00:00", periods=n, freq="5min", tz="UTC")
    close = np.array(close_path, dtype=float)
    df = pd.DataFrame({
        "open": close, "high": close + 0.1, "low": close - 0.1, "close": close,
    }, index=idx)
    df["atr"] = 1.0
    df["ema_slow"] = close[0] - 100  # keep close permanently above ema_slow (uptrend side)
    df["htf_trend"] = 1
    df["htf2_trend"] = 1
    df["signal"] = 0
    if uptrend_until is not None:
        df.loc[df.index[uptrend_until:], ["htf_trend", "htf2_trend"]] = -1
        df.loc[df.index[uptrend_until:], "ema_slow"] = close[0] + 100  # flip below ema_slow too
    return df


def test_dca_grid_adds_leg_on_adverse_move():
    # Flat entry then drift down by exactly dca_step_price*2 without ever
    # reversing trend - expect 3 legs total (entry + 2 adds).
    n = 20
    close = [2000.0] * 3 + [1998.0] * 3 + [1996.0] * 14
    df = make_df(n, close)
    df.iloc[0, df.columns.get_loc("signal")] = 1
    bt_cfg = replace_backtest_defaults()
    res = run_backtest_dca_grid(df, STRATEGY_CFG, DCA_RISK_CFG, bt_cfg)
    open_legs = [t for t in res.trades if t.exit_price is None]
    closed_legs = [t for t in res.trades if t.exit_price is not None]
    assert len(open_legs) + len(closed_legs) == 3


def test_dca_grid_exits_on_trend_reversal():
    n = 10
    close = [2000.0] * n
    df = make_df(n, close, uptrend_until=5)
    df.iloc[0, df.columns.get_loc("signal")] = 1
    bt_cfg = replace_backtest_defaults()
    res = run_backtest_dca_grid(df, STRATEGY_CFG, DCA_RISK_CFG, bt_cfg)
    closed = [t for t in res.trades if t.exit_price is not None]
    assert len(closed) == 1
    assert closed[0].exit_reason == "trend_reversal"


def test_dca_grid_exits_on_hard_stop_before_trend_flips():
    # Trend never reverses, but price crashes far enough that the grid's
    # total unrealized loss breaches dca_hard_stop_pct (15% of $1000 = $150).
    n = 30
    close = [2000.0 - 2.0 * i for i in range(n)]  # steady $2/bar adverse drift, adding a leg each bar
    df = make_df(n, close)  # trend never flips (uptrend_until=None)
    df.iloc[0, df.columns.get_loc("signal")] = 1
    bt_cfg = replace_backtest_defaults()
    res = run_backtest_dca_grid(df, STRATEGY_CFG, DCA_RISK_CFG, bt_cfg)
    closed = [t for t in res.trades if t.exit_price is not None]
    assert len(closed) > 0
    assert all(t.exit_reason == "hard_stop" for t in closed)


def replace_backtest_defaults():
    from gold_bot.config import BacktestConfig
    return BacktestConfig(initial_balance=1000.0, spread_points=0.0, commission_per_lot=0.0, slippage_points=0.0)
