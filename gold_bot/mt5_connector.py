"""Thin wrapper around the MetaTrader5 Python package.

NOTE: the `MetaTrader5` package only works on Windows, talking to a locally
running MT5 terminal that is logged into your broker account. It cannot be
installed or run on Linux/macOS. Backtesting (gold_bot.backtester) has no
such dependency and works everywhere; this module is only needed to fetch
live data / place real orders.
"""
from __future__ import annotations

import sys
from datetime import datetime

import pandas as pd

TIMEFRAME_MAP_NAMES = ["M1", "M5", "M15", "M30", "H1", "H4", "D1"]


class MT5Unavailable(RuntimeError):
    pass


def _import_mt5():
    if sys.platform != "win32":
        raise MT5Unavailable(
            "MetaTrader5 package only runs on Windows with a local MT5 terminal. "
            "Use gold_bot.backtester on this platform instead."
        )
    try:
        import MetaTrader5 as mt5  # type: ignore
    except ImportError as exc:
        raise MT5Unavailable(
            "MetaTrader5 package not installed. Run `pip install MetaTrader5` on Windows."
        ) from exc
    return mt5


class MT5Connector:
    def __init__(self, login: int, password: str, server: str, terminal_path: str = ""):
        self.mt5 = _import_mt5()
        self.login = login
        self.password = password
        self.server = server
        self.terminal_path = terminal_path
        self._connected = False

    def connect(self) -> None:
        kwargs = {}
        if self.terminal_path:
            kwargs["path"] = self.terminal_path
        if not self.mt5.initialize(**kwargs):
            raise MT5Unavailable(f"MT5 initialize() failed: {self.mt5.last_error()}")
        if self.login:
            authorized = self.mt5.login(self.login, password=self.password, server=self.server)
            if not authorized:
                raise MT5Unavailable(f"MT5 login() failed: {self.mt5.last_error()}")
        self._connected = True

    def shutdown(self) -> None:
        if self._connected:
            self.mt5.shutdown()
            self._connected = False

    def timeframe_constant(self, timeframe: str):
        return getattr(self.mt5, f"TIMEFRAME_{timeframe}")

    def get_rates(self, symbol: str, timeframe: str, n_bars: int = 1000) -> pd.DataFrame:
        rates = self.mt5.copy_rates_from_pos(symbol, self.timeframe_constant(timeframe), 0, n_bars)
        if rates is None:
            raise MT5Unavailable(f"copy_rates_from_pos failed: {self.mt5.last_error()}")
        df = pd.DataFrame(rates)
        df["time"] = pd.to_datetime(df["time"], unit="s", utc=True)
        df = df.set_index("time")
        df = df.rename(columns={"tick_volume": "volume"})
        return df[["open", "high", "low", "close", "volume"]]

    def account_equity(self) -> float:
        info = self.mt5.account_info()
        if info is None:
            raise MT5Unavailable(f"account_info failed: {self.mt5.last_error()}")
        return float(info.equity)

    def open_positions(self, symbol: str) -> list:
        positions = self.mt5.positions_get(symbol=symbol)
        return list(positions) if positions is not None else []

    def send_market_order(
        self,
        symbol: str,
        direction: int,
        lots: float,
        stop_loss: float,
        take_profit: float,
        deviation: int = 20,
        comment: str = "gold_bot",
    ):
        order_type = self.mt5.ORDER_TYPE_BUY if direction == 1 else self.mt5.ORDER_TYPE_SELL
        tick = self.mt5.symbol_info_tick(symbol)
        price = tick.ask if direction == 1 else tick.bid
        request = {
            "action": self.mt5.TRADE_ACTION_DEAL,
            "symbol": symbol,
            "volume": lots,
            "type": order_type,
            "price": price,
            "sl": stop_loss,
            "tp": take_profit,
            "deviation": deviation,
            "magic": 20240501,
            "comment": comment,
            "type_time": self.mt5.ORDER_TIME_GTC,
            "type_filling": self.mt5.ORDER_FILLING_IOC,
        }
        result = self.mt5.order_send(request)
        if result is None or result.retcode != self.mt5.TRADE_RETCODE_DONE:
            raise MT5Unavailable(f"order_send failed: {result}")
        return result

    def modify_stop_loss(self, ticket: int, symbol: str, new_sl: float, take_profit: float):
        request = {
            "action": self.mt5.TRADE_ACTION_SLTP,
            "symbol": symbol,
            "position": ticket,
            "sl": new_sl,
            "tp": take_profit,
        }
        result = self.mt5.order_send(request)
        if result is None or result.retcode != self.mt5.TRADE_RETCODE_DONE:
            raise MT5Unavailable(f"order_send (modify) failed: {result}")
        return result

    def close_position(self, position, deviation: int = 20):
        direction = 1 if position.type == self.mt5.POSITION_TYPE_BUY else -1
        order_type = self.mt5.ORDER_TYPE_SELL if direction == 1 else self.mt5.ORDER_TYPE_BUY
        tick = self.mt5.symbol_info_tick(position.symbol)
        price = tick.bid if direction == 1 else tick.ask
        request = {
            "action": self.mt5.TRADE_ACTION_DEAL,
            "symbol": position.symbol,
            "volume": position.volume,
            "type": order_type,
            "position": position.ticket,
            "price": price,
            "deviation": deviation,
            "magic": 20240501,
            "comment": "gold_bot_close",
            "type_time": self.mt5.ORDER_TIME_GTC,
            "type_filling": self.mt5.ORDER_FILLING_IOC,
        }
        result = self.mt5.order_send(request)
        if result is None or result.retcode != self.mt5.TRADE_RETCODE_DONE:
            raise MT5Unavailable(f"order_send (close) failed: {result}")
        return result
