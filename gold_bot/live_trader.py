"""Live/paper trading loop: poll MT5 for new closed M5 bars, evaluate the
strategy, and manage orders through the same RiskManager rules used in
backtesting.

Run only on Windows with a logged-in MT5 terminal (see mt5_connector.py).
"""
from __future__ import annotations

import logging
import time
from datetime import datetime, timezone

from gold_bot.config import BotConfig
from gold_bot.mt5_connector import MT5Connector
from gold_bot.risk_manager import RiskManager
from gold_bot.strategy import generate_signals

logger = logging.getLogger("gold_bot.live")

# Extra bars fetched beyond the slowest indicator's warmup period so EMA/ATR
# have converged before the newest (tradable) bar.
WARMUP_BARS = 400


class LiveTrader:
    def __init__(self, cfg: BotConfig):
        self.cfg = cfg
        self.conn = MT5Connector(
            login=cfg.mt5["login"],
            password=cfg.mt5["password"],
            server=cfg.mt5["server"],
            terminal_path=cfg.mt5.get("terminal_path", ""),
        )
        self.risk_mgr: RiskManager | None = None
        self._last_bar_time = None

    def start(self) -> None:
        self.conn.connect()
        equity = self.conn.account_equity()
        self.risk_mgr = RiskManager(cfg=self.cfg.risk, equity=equity)
        logger.info("Connected to MT5. Starting equity=%.2f", equity)

    def stop(self) -> None:
        self.conn.shutdown()

    def _poll_once(self) -> None:
        df = self.conn.get_rates(self.cfg.symbol, self.cfg.timeframe, WARMUP_BARS)
        signals = generate_signals(df, self.cfg.strategy, self.cfg.sessions)
        latest_closed = signals.iloc[-2]  # last fully closed bar (iloc[-1] may be forming)
        bar_time = signals.index[-2]

        if self._last_bar_time == bar_time:
            return  # already processed this bar
        self._last_bar_time = bar_time

        equity = self.conn.account_equity()
        self.risk_mgr.update_equity(equity)

        today = bar_time.date()
        open_positions = self.conn.open_positions(self.cfg.symbol)

        # Trail existing stop losses first.
        atr_value = latest_closed["atr"]
        for pos in open_positions:
            direction = 1 if pos.type == 0 else -1  # 0 == POSITION_TYPE_BUY
            if self.cfg.risk.use_trailing_stop and atr_value and atr_value > 0:
                new_sl = self.risk_mgr.trailing_stop(latest_closed["close"], atr_value, direction)
                better = (direction == 1 and new_sl > pos.sl) or (direction == -1 and new_sl < pos.sl)
                if better:
                    self.conn.modify_stop_loss(pos.ticket, self.cfg.symbol, new_sl, pos.tp)
                    logger.info("Trailed SL for ticket %s to %.2f", pos.ticket, new_sl)

        signal = int(latest_closed["signal"])
        can_open, reason = self.risk_mgr.can_open_trade(today, open_positions=len(open_positions))
        if signal == 0:
            return
        if not can_open:
            logger.info("Signal=%s but blocked: %s", signal, reason)
            return
        if not atr_value or atr_value <= 0:
            return

        entry_price = latest_closed["close"]
        stop_loss = self.risk_mgr.stop_loss(entry_price, atr_value, signal, self.cfg.strategy.atr_sl_mult)
        take_profit = self.risk_mgr.take_profit(entry_price, atr_value, signal, self.cfg.strategy.atr_tp_mult)
        lots = self.risk_mgr.position_size_lots(entry_price, stop_loss)
        if lots <= 0:
            return

        result = self.conn.send_market_order(self.cfg.symbol, signal, lots, stop_loss, take_profit)
        self.risk_mgr.register_trade_opened(today)
        logger.info(
            "Opened %s %.2f lots @ ~%.2f SL=%.2f TP=%.2f (result=%s)",
            "LONG" if signal == 1 else "SHORT",
            lots,
            entry_price,
            stop_loss,
            take_profit,
            result,
        )

    def run_forever(self, poll_seconds: int = 15) -> None:
        self.start()
        try:
            while True:
                try:
                    self._poll_once()
                except Exception:
                    logger.exception("Error during poll cycle")
                time.sleep(poll_seconds)
        finally:
            self.stop()
