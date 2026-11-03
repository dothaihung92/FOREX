"""Execution back ends: a simulator for replay/paper, and MT5 for live.

Both expose the same small surface so the engine cannot tell them apart.
"""
from __future__ import annotations

import sys
from dataclasses import dataclass

CONTRACT = 100.0


@dataclass
class Position:
    side: str                 # "BUY" | "SELL"
    lots: float
    entry: float
    sl: float
    tp: float
    opened_bar: int
    opened_time: object = None
    risk_usd: float = 0.0     # what the guard sized the trade to lose at the stop
    max_fav_r: float = 0.0
    ticket: int = 0

    @property
    def sign(self) -> int:
        return 1 if self.side == "BUY" else -1


@dataclass
class ClosedTrade:
    side: str
    lots: float
    entry: float
    exit: float
    pnl: float
    reason: str
    opened_bar: int
    closed_bar: int
    risk_usd: float

    @property
    def r(self) -> float:
        return self.pnl / self.risk_usd if self.risk_usd else 0.0


class SimBroker:
    """Bar-based simulator.

    Costs follow the project's Exness Standard model: half the spread on each
    side plus slippage on each side. Stops and targets are triggered on the
    side of the book that would actually trade (bid for exits of longs, ask
    for exits of shorts), and the bar is walked open -> adverse extreme ->
    favourable extreme so a bar that touches both SL and TP books the loss.
    """

    def __init__(self, start_balance: float = 1000.0, spread: float = 0.25, slippage: float = 0.05):
        self.balance = start_balance
        self.start_balance = start_balance
        self.spread = spread
        self.slippage = slippage
        self.position: Position | None = None
        self.closed: list[ClosedTrade] = []
        self._new_closed: list[ClosedTrade] = []

    # ----- queries
    def equity(self, mid: float) -> float:
        return self.balance + self._floating(mid)

    def _floating(self, mid: float) -> float:
        p = self.position
        if not p:
            return 0.0
        exit_px = mid - p.sign * self.spread / 2
        return p.sign * (exit_px - p.entry) * p.lots * CONTRACT

    def current_spread(self) -> float:
        return self.spread

    def drain_closed(self) -> list[ClosedTrade]:
        out, self._new_closed = self._new_closed, []
        return out

    # ----- actions
    def open(self, side: str, lots: float, sl: float, tp: float, mid: float,
             bar: int, time=None, risk_usd: float = 0.0) -> Position:
        if self.position:
            raise RuntimeError("SimBroker: position already open")
        sign = 1 if side == "BUY" else -1
        entry = mid + sign * (self.spread / 2 + self.slippage)
        self.position = Position(side, lots, entry, sl, tp, bar, time, risk_usd)
        return self.position

    def modify(self, sl: float, tp: float) -> None:
        if self.position:
            self.position.sl, self.position.tp = sl, tp

    def close_now(self, mid: float, bar: int, reason: str = "manual") -> ClosedTrade | None:
        p = self.position
        if not p:
            return None
        exit_px = mid - p.sign * (self.spread / 2 + self.slippage)
        return self._book(exit_px, bar, reason)

    def _book(self, exit_px: float, bar: int, reason: str) -> ClosedTrade:
        p = self.position
        pnl = p.sign * (exit_px - p.entry) * p.lots * CONTRACT
        self.balance += pnl
        t = ClosedTrade(p.side, p.lots, p.entry, exit_px, pnl, reason, p.opened_bar, bar, p.risk_usd)
        self.closed.append(t)
        self._new_closed.append(t)
        self.position = None
        return t

    # ----- time
    def on_bar(self, o: float, h: float, l: float, c: float, bar: int) -> ClosedTrade | None:
        """Advance the position through one finished bar."""
        p = self.position
        if not p:
            return None
        half = self.spread / 2
        slip = self.slippage
        if p.sign == 1:
            # exits trade on the bid = mid - half spread
            o_b, h_b, l_b = o - half, h - half, l - half
            if o_b <= p.sl:                       # gapped through the stop
                return self._book(o_b - slip, bar, "stop (gap)")
            if o_b >= p.tp:
                return self._book(o_b, bar, "target (gap)")
            if l_b <= p.sl:                       # adverse extreme first
                return self._book(p.sl - slip, bar, "stop")
            if h_b >= p.tp:
                return self._book(p.tp - slip, bar, "target")
            fav = h_b - p.entry
        else:
            o_a, h_a, l_a = o + half, h + half, l + half
            if o_a >= p.sl:
                return self._book(o_a + slip, bar, "stop (gap)")
            if o_a <= p.tp:
                return self._book(o_a, bar, "target (gap)")
            if h_a >= p.sl:
                return self._book(p.sl + slip, bar, "stop")
            if l_a <= p.tp:
                return self._book(p.tp + slip, bar, "target")
            fav = p.entry - l_a
        if p.risk_usd:
            r = fav * p.lots * CONTRACT / p.risk_usd
            p.max_fav_r = max(p.max_fav_r, r)
        return None


class MT5Broker:
    """Live execution through the MetaTrader5 package (Windows only).

    NOT exercised in the authoring environment: there is no Windows or MT5
    terminal there. Run it against a DEMO account first.
    """

    def __init__(self, connector, symbol: str = "XAUUSD", magic: int = 20240501, deviation: int = 30):
        if sys.platform != "win32":
            raise RuntimeError("MT5Broker needs Windows with a running, logged-in MT5 terminal")
        self.c = connector
        self.mt5 = connector.mt5
        self.symbol = symbol
        self.magic = magic
        self.deviation = deviation
        self._seen_deals: set[int] = set()

    def _positions(self):
        return [p for p in self.c.open_positions(self.symbol) if p.magic == self.magic]

    @property
    def position(self) -> Position | None:
        ps = self._positions()
        if not ps:
            return None
        p = ps[0]
        side = "BUY" if p.type == self.mt5.POSITION_TYPE_BUY else "SELL"
        return Position(side, p.volume, p.price_open, p.sl, p.tp, 0, None, 0.0, 0.0, p.ticket)

    def equity(self, mid: float = 0.0) -> float:
        return self.c.account_equity()

    def current_spread(self) -> float:
        tick = self.mt5.symbol_info_tick(self.symbol)
        return float(tick.ask - tick.bid)

    def open(self, side, lots, sl, tp, mid, bar, time=None, risk_usd=0.0):
        self.c.send_market_order(self.symbol, 1 if side == "BUY" else -1, lots, sl, tp,
                                 deviation=self.deviation, comment="ai_trader")
        return self.position

    def modify(self, sl, tp):
        p = self._positions()
        if p:
            self.c.modify_stop_loss(p[0].ticket, self.symbol, sl, tp)

    def close_now(self, mid, bar, reason="manual"):
        for p in self._positions():
            self.c.close_position(p, deviation=self.deviation)

    def on_bar(self, *a, **k):          # the broker server manages SL/TP itself
        return None

    def drain_closed(self) -> list[ClosedTrade]:
        """Closed deals since the last call, from the account history."""
        from datetime import datetime, timedelta, timezone
        now = datetime.now(timezone.utc)
        deals = self.mt5.history_deals_get(now - timedelta(days=2), now + timedelta(days=1)) or []
        out = []
        for d in deals:
            if d.magic != self.magic or d.symbol != self.symbol:
                continue
            if d.entry != self.mt5.DEAL_ENTRY_OUT or d.ticket in self._seen_deals:
                continue
            self._seen_deals.add(d.ticket)
            pnl = d.profit + d.commission + d.swap
            out.append(ClosedTrade("", d.volume, 0.0, d.price, pnl, "broker", 0, 0, 0.0))
        return out
