"""Position sizing and trade-level risk controls.

Keeps risk decisions in one place so both the backtester and the live
trader apply exactly the same rules (no drift between simulated and real
risk behaviour).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

from gold_bot.config import RiskConfig

# XAUUSD contract conventions: 1 standard lot = 100 oz, price quoted per oz.
# 1 "point" as used in config.yaml = 0.01 price move (matches most brokers'
# point definition for XAUUSD, e.g. 1900.01 -> 1900.02 is 1 point).
CONTRACT_SIZE = 100.0
POINT = 0.01


@dataclass
class DailyState:
    day: date | None = None
    trades_opened: int = 0
    realized_pnl: float = 0.0

    def reset_if_new_day(self, current_day: date) -> None:
        if self.day != current_day:
            self.day = current_day
            self.trades_opened = 0
            self.realized_pnl = 0.0


@dataclass
class RiskManager:
    cfg: RiskConfig
    equity: float
    daily: DailyState = field(default_factory=DailyState)

    def update_equity(self, equity: float) -> None:
        self.equity = equity

    def register_fill_pnl(self, current_day: date, pnl: float) -> None:
        self.daily.reset_if_new_day(current_day)
        self.daily.realized_pnl += pnl

    def register_trade_opened(self, current_day: date) -> None:
        self.daily.reset_if_new_day(current_day)
        self.daily.trades_opened += 1

    def can_open_trade(self, current_day: date, open_positions: int) -> tuple[bool, str]:
        self.daily.reset_if_new_day(current_day)
        if self.equity <= 0:
            return False, "equity depleted"
        if open_positions >= self.cfg.max_concurrent_trades:
            return False, "max_concurrent_trades reached"
        if self.daily.trades_opened >= self.cfg.max_trades_per_day:
            return False, "max_trades_per_day reached"
        max_loss = -abs(self.cfg.max_daily_loss_pct) / 100.0 * self.equity
        if self.daily.realized_pnl <= max_loss:
            return False, "max_daily_loss_pct breached"
        return True, ""

    def position_size_lots(self, entry_price: float, stop_price: float) -> float:
        if self.cfg.sizing_mode == "equity_step":
            return self._position_size_lots_equity_step()
        return self._position_size_lots_percent_risk(entry_price, stop_price)

    def _position_size_lots_percent_risk(self, entry_price: float, stop_price: float) -> float:
        """Lots sized so that a stop-out risks exactly risk_per_trade_pct of equity."""
        stop_distance = abs(entry_price - stop_price)
        if stop_distance <= 0:
            return 0.0
        risk_amount = self.cfg.risk_per_trade_pct / 100.0 * self.equity
        loss_per_lot = stop_distance * CONTRACT_SIZE
        if loss_per_lot <= 0:
            return 0.0
        lots = risk_amount / loss_per_lot
        return max(0.0, round(lots, 2))

    def _position_size_lots_equity_step(self) -> float:
        """Step-based sizing some retail traders use instead of risk-percent
        sizing: start at base_lot for base_equity, then add lot_step for
        every equity_step_usd of profit above base_equity, and remove
        lot_step for every equity_step_usd of loss below it.

        Unlike percent-risk sizing, this does NOT normalize risk against
        the stop distance - the lot size is fixed by the equity milestone
        regardless of how far away the ATR-based stop is, so the dollar
        risk of a given trade varies with market volatility at entry time.
        """
        steps = (self.equity - self.cfg.base_equity) / self.cfg.equity_step_usd
        lots = self.cfg.base_lot + self.cfg.lot_step * int(steps)
        return round(max(self.cfg.min_lot, lots), 2)

    def stop_loss(self, entry_price: float, atr_value: float, direction: int, atr_sl_mult: float) -> float:
        return entry_price - direction * atr_sl_mult * atr_value

    def take_profit(self, entry_price: float, atr_value: float, direction: int, atr_tp_mult: float) -> float:
        return entry_price + direction * atr_tp_mult * atr_value

    def trailing_stop(self, current_price: float, atr_value: float, direction: int) -> float:
        return current_price - direction * self.cfg.trailing_atr_mult * atr_value
