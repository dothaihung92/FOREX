"""Data/trading backends for the dashboard.

Two implementations behind one interface:

* `MT5Feed`   - live Exness (or any MT5 broker) account. Windows only,
                needs a running, logged-in MT5 terminal.
* `CsvFeed`   - replays the repo's historical CSVs. Runs anywhere, places
                no real orders. This is what makes the dashboard testable
                off Windows, and it is the default.

The split matters for safety as much as portability: nothing in the web
layer can place an order unless a real broker backend was explicitly
constructed and trading was explicitly enabled.
"""
from __future__ import annotations

import threading
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from pathlib import Path

import pandas as pd

DATA_DIR = Path(__file__).resolve().parents[2] / "data"

TIMEFRAME_MINUTES = {
    "M1": 1, "M5": 5, "M15": 15, "M30": 30, "H1": 60, "H4": 240, "D1": 1440,
}


class FeedError(RuntimeError):
    pass


@dataclass
class Position:
    ticket: int
    symbol: str
    direction: int          # 1 long, -1 short
    lots: float
    open_price: float
    sl: float
    tp: float
    profit: float
    opened_at: datetime

    def as_dict(self) -> dict:
        return {
            "ticket": self.ticket, "symbol": self.symbol,
            "side": "BUY" if self.direction == 1 else "SELL",
            "lots": self.lots, "open_price": self.open_price,
            "sl": self.sl, "tp": self.tp, "profit": round(self.profit, 2),
            "opened_at": self.opened_at.isoformat(),
        }


@dataclass
class AccountInfo:
    login: str
    server: str
    currency: str
    balance: float
    equity: float
    margin_free: float
    leverage: str
    live: bool

    def as_dict(self) -> dict:
        d = self.__dict__.copy()
        for k in ("balance", "equity", "margin_free"):
            d[k] = round(d[k], 2)
        return d


class BaseFeed:
    """Interface the web layer talks to. `can_trade` is the single gate."""

    can_trade = False

    def symbols(self) -> list[str]:
        raise NotImplementedError

    def candles(self, symbol: str, timeframe: str, n: int) -> pd.DataFrame:
        raise NotImplementedError

    def account(self) -> AccountInfo:
        raise NotImplementedError

    def positions(self) -> list[Position]:
        raise NotImplementedError

    def place_order(self, symbol: str, direction: int, lots: float,
                    sl: float, tp: float) -> dict:
        raise FeedError("this backend cannot place orders")

    def close_position(self, ticket: int) -> dict:
        raise FeedError("this backend cannot close positions")

    def close(self) -> None:
        pass


# --------------------------------------------------------------- CSV replay
class CsvFeed(BaseFeed):
    """Replays historical CSVs. Orders are simulated in memory only.

    Deliberately never touches a broker: this is the backend you develop
    and demo against, so a mistake in the UI cannot cost money.
    """

    can_trade = True          # simulated trading, clearly labelled as such

    _FILES = {
        "XAUUSD": ("XAUUSD_M5_real.csv", "time"),
        "EURUSD": ("EURUSD_m15.csv", "Date"),
        "AUDUSD": ("AUDUSD_m15.csv", "Date"),
        "USDCHF": ("USDCHF_m15.csv", "Date"),
        "GBPUSD": ("GBPUSD_m15.csv", "Date"),
        "USDJPY": ("USDJPY_m15.csv", "Date"),
        "USDCAD": ("USDCAD_m15.csv", "Date"),
    }
    # The FX CSVs store prices scaled by 1e5 (127435.0 == 1.27435).
    _SCALE = {s: (1.0 if s == "XAUUSD" else 1e-5) for s in _FILES}

    def __init__(self, start_balance: float = 500.0):
        self._cache: dict[str, pd.DataFrame] = {}
        self._positions: dict[int, Position] = {}
        self._next_ticket = 1
        self._balance = start_balance
        self._lock = threading.Lock()

    def symbols(self) -> list[str]:
        return [s for s in self._FILES if (DATA_DIR / self._FILES[s][0]).exists()]

    def _load(self, symbol: str) -> pd.DataFrame:
        if symbol in self._cache:
            return self._cache[symbol]
        if symbol not in self._FILES:
            raise FeedError(f"unknown symbol {symbol!r}")
        fname, tcol = self._FILES[symbol]
        path = DATA_DIR / fname
        if not path.exists():
            raise FeedError(f"no data file for {symbol} ({fname})")
        df = pd.read_csv(path)
        df[tcol] = pd.to_datetime(df[tcol], utc=True)
        df = df.set_index(tcol).sort_index()
        scale = self._SCALE[symbol]
        for c in ("open", "high", "low", "close"):
            df[c] = df[c].astype(float) * scale
        self._cache[symbol] = df[["open", "high", "low", "close"]]
        return self._cache[symbol]

    def candles(self, symbol: str, timeframe: str, n: int) -> pd.DataFrame:
        df = self._load(symbol)
        minutes = TIMEFRAME_MINUTES.get(timeframe)
        if minutes is None:
            raise FeedError(f"unknown timeframe {timeframe!r}")
        base = 5 if symbol == "XAUUSD" else 15
        if minutes > base:
            df = df.resample(f"{minutes}min").agg(
                {"open": "first", "high": "max", "low": "min", "close": "last"}
            ).dropna()
        return df.tail(n)

    def last_price(self, symbol: str) -> float:
        return float(self._load(symbol)["close"].iloc[-1])

    def account(self) -> AccountInfo:
        with self._lock:
            floating = sum(p.profit for p in self._positions.values())
            return AccountInfo(
                login="simulated", server="CSV replay", currency="USD",
                balance=self._balance, equity=self._balance + floating,
                margin_free=self._balance + floating, leverage="n/a", live=False,
            )

    def positions(self) -> list[Position]:
        with self._lock:
            out = []
            for p in self._positions.values():
                price = self.last_price(p.symbol)
                contract = 100.0 if p.symbol == "XAUUSD" else 100_000.0
                p.profit = p.direction * (price - p.open_price) * contract * p.lots
                out.append(p)
            return out

    def place_order(self, symbol: str, direction: int, lots: float,
                    sl: float, tp: float) -> dict:
        price = self.last_price(symbol)
        with self._lock:
            ticket = self._next_ticket
            self._next_ticket += 1
            self._positions[ticket] = Position(
                ticket=ticket, symbol=symbol, direction=direction, lots=lots,
                open_price=price, sl=sl, tp=tp, profit=0.0,
                opened_at=datetime.now(timezone.utc),
            )
        return {"ticket": ticket, "price": price, "simulated": True}

    def close_position(self, ticket: int) -> dict:
        with self._lock:
            pos = self._positions.pop(ticket, None)
            if pos is None:
                raise FeedError(f"no open position with ticket {ticket}")
            price = self.last_price(pos.symbol)
            contract = 100.0 if pos.symbol == "XAUUSD" else 100_000.0
            pnl = pos.direction * (price - pos.open_price) * contract * pos.lots
            self._balance += pnl
        return {"ticket": ticket, "close_price": price, "pnl": round(pnl, 2),
                "simulated": True}


# ------------------------------------------------------------------- live MT5
class MT5Feed(BaseFeed):
    """Live broker account through the MT5 terminal (Exness included).

    `allow_trading` defaults to False. Charts, account state and open
    positions are readable without it; order placement and closing are not.
    """

    def __init__(self, connector, symbols: list[str], allow_trading: bool = False):
        self.conn = connector
        self._symbols = symbols
        self.can_trade = bool(allow_trading)

    def symbols(self) -> list[str]:
        return list(self._symbols)

    def candles(self, symbol: str, timeframe: str, n: int) -> pd.DataFrame:
        if timeframe not in TIMEFRAME_MINUTES:
            raise FeedError(f"unknown timeframe {timeframe!r}")
        df = self.conn.get_rates(symbol, timeframe, n)
        return df[["open", "high", "low", "close"]]

    def account(self) -> AccountInfo:
        info = self.conn.mt5.account_info()
        if info is None:
            raise FeedError(f"account_info failed: {self.conn.mt5.last_error()}")
        return AccountInfo(
            login=str(info.login), server=str(info.server), currency=str(info.currency),
            balance=float(info.balance), equity=float(info.equity),
            margin_free=float(info.margin_free), leverage=f"1:{info.leverage}",
            live=True,
        )

    def positions(self) -> list[Position]:
        raw = self.conn.mt5.positions_get()
        out = []
        for p in (raw or []):
            out.append(Position(
                ticket=int(p.ticket), symbol=str(p.symbol),
                direction=1 if p.type == self.conn.mt5.POSITION_TYPE_BUY else -1,
                lots=float(p.volume), open_price=float(p.price_open),
                sl=float(p.sl), tp=float(p.tp), profit=float(p.profit),
                opened_at=datetime.fromtimestamp(int(p.time), tz=timezone.utc),
            ))
        return out

    def place_order(self, symbol: str, direction: int, lots: float,
                    sl: float, tp: float) -> dict:
        if not self.can_trade:
            raise FeedError("trading is disabled on this session (start with --live-trading)")
        result = self.conn.send_market_order(symbol, direction, lots, sl, tp,
                                             comment="dashboard")
        return {"ticket": int(result.order), "price": float(result.price),
                "simulated": False}

    def close_position(self, ticket: int) -> dict:
        if not self.can_trade:
            raise FeedError("trading is disabled on this session (start with --live-trading)")
        for p in (self.conn.mt5.positions_get() or []):
            if int(p.ticket) == ticket:
                result = self.conn.close_position(p)
                return {"ticket": ticket, "close_price": float(result.price),
                        "simulated": False}
        raise FeedError(f"no open position with ticket {ticket}")

    def close(self) -> None:
        self.conn.shutdown()
