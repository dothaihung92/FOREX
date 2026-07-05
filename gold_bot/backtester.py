"""Bar-by-bar backtest engine for the M5 gold strategy.

Deliberately simple/transparent rather than vectorized: risk management
(position sizing, trailing stops, daily loss cutoff) is path-dependent, so
simulating bar-by-bar is the only way to keep backtest and live logic
identical.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from gold_bot.config import BacktestConfig, RiskConfig, StrategyConfig
from gold_bot.risk_manager import POINT, RiskManager


@dataclass
class Trade:
    direction: int
    entry_time: pd.Timestamp
    entry_price: float
    stop_loss: float
    take_profit: float
    lots: float
    exit_time: pd.Timestamp | None = None
    exit_price: float | None = None
    exit_reason: str | None = None
    pnl: float = 0.0


@dataclass
class BacktestResult:
    trades: list[Trade]
    equity_curve: pd.Series
    initial_balance: float

    @property
    def final_equity(self) -> float:
        return float(self.equity_curve.iloc[-1]) if len(self.equity_curve) else self.initial_balance

    def summary(self) -> dict:
        closed = [t for t in self.trades if t.exit_price is not None]
        n = len(closed)
        wins = [t for t in closed if t.pnl > 0]
        losses = [t for t in closed if t.pnl <= 0]
        gross_profit = sum(t.pnl for t in wins)
        gross_loss = -sum(t.pnl for t in losses)
        profit_factor = gross_profit / gross_loss if gross_loss > 0 else float("inf") if gross_profit > 0 else 0.0

        running_max = self.equity_curve.cummax()
        drawdown = (self.equity_curve - running_max) / running_max
        max_dd_pct = float(drawdown.min() * 100) if len(drawdown) else 0.0

        total_return_pct = (self.final_equity / self.initial_balance - 1) * 100

        return {
            "trades": n,
            "wins": len(wins),
            "losses": len(losses),
            "win_rate_pct": round(len(wins) / n * 100, 2) if n else 0.0,
            "profit_factor": round(profit_factor, 2) if np.isfinite(profit_factor) else profit_factor,
            "total_return_pct": round(total_return_pct, 2),
            "max_drawdown_pct": round(max_dd_pct, 2),
            "final_equity": round(self.final_equity, 2),
        }


CONTRACT_SIZE = 100.0  # XAUUSD: 1 lot = 100 oz


def run_backtest(
    df: pd.DataFrame,
    strategy_cfg: StrategyConfig,
    risk_cfg: RiskConfig,
    backtest_cfg: BacktestConfig,
) -> BacktestResult:
    """df must already contain signal/atr columns from strategy.generate_signals."""
    balance = backtest_cfg.initial_balance
    equity_curve = pd.Series(index=df.index, dtype=float)

    risk_mgr = RiskManager(cfg=risk_cfg, equity=balance)
    spread = backtest_cfg.spread_points * POINT
    slippage = backtest_cfg.slippage_points * POINT

    open_trade: Trade | None = None
    trades: list[Trade] = []

    for ts, row in df.iterrows():
        day = ts.date()
        high, low, close = row["high"], row["low"], row["close"]
        atr_value = row["atr"]

        if open_trade is not None:
            direction = open_trade.direction
            exit_price = None
            exit_reason = None

            if direction == 1:
                if low <= open_trade.stop_loss:
                    exit_price, exit_reason = open_trade.stop_loss, "stop_loss"
                elif high >= open_trade.take_profit:
                    exit_price, exit_reason = open_trade.take_profit, "take_profit"
            else:
                if high >= open_trade.stop_loss:
                    exit_price, exit_reason = open_trade.stop_loss, "stop_loss"
                elif low <= open_trade.take_profit:
                    exit_price, exit_reason = open_trade.take_profit, "take_profit"

            if exit_price is not None:
                exit_price -= direction * slippage
                pnl = direction * (exit_price - open_trade.entry_price) * CONTRACT_SIZE * open_trade.lots
                open_trade.exit_time = ts
                open_trade.exit_price = exit_price
                open_trade.exit_reason = exit_reason
                open_trade.pnl = pnl
                balance += pnl
                risk_mgr.update_equity(balance)
                risk_mgr.register_fill_pnl(day, pnl)
                trades.append(open_trade)
                open_trade = None
            elif risk_cfg.use_trailing_stop and not pd.isna(atr_value):
                new_stop = risk_mgr.trailing_stop(close, atr_value, direction)
                if direction == 1:
                    open_trade.stop_loss = max(open_trade.stop_loss, new_stop)
                else:
                    open_trade.stop_loss = min(open_trade.stop_loss, new_stop)

        if open_trade is None:
            signal = row.get("signal", 0)
            can_open, _ = risk_mgr.can_open_trade(day, open_positions=0)
            if signal != 0 and can_open and not pd.isna(atr_value) and atr_value > 0:
                direction = int(signal)
                is_mean_reversion = row.get("signal_type") == "mean_reversion"
                sl_mult = strategy_cfg.mr_atr_sl_mult if is_mean_reversion else strategy_cfg.atr_sl_mult
                tp_mult = strategy_cfg.mr_atr_tp_mult if is_mean_reversion else strategy_cfg.atr_tp_mult
                entry_price = close + direction * (spread / 2 + slippage)
                stop_loss = risk_mgr.stop_loss(entry_price, atr_value, direction, sl_mult)
                take_profit = risk_mgr.take_profit(entry_price, atr_value, direction, tp_mult)
                lots = risk_mgr.position_size_lots(entry_price, stop_loss)
                if lots > 0:
                    open_trade = Trade(
                        direction=direction,
                        entry_time=ts,
                        entry_price=entry_price,
                        stop_loss=stop_loss,
                        take_profit=take_profit,
                        lots=lots,
                    )
                    risk_mgr.register_trade_opened(day)

        # Mark-to-market equity for drawdown tracking.
        unrealized = 0.0
        if open_trade is not None:
            unrealized = open_trade.direction * (close - open_trade.entry_price) * CONTRACT_SIZE * open_trade.lots
        equity_curve.loc[ts] = balance + unrealized

    if open_trade is not None:
        trades.append(open_trade)  # left open at end of data, pnl stays 0 (unrealized excluded from stats)

    return BacktestResult(trades=trades, equity_curve=equity_curve.ffill(), initial_balance=backtest_cfg.initial_balance)
