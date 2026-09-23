"""Faithful Python port of GoldBot_DynamicTP_FastClose.mq5 (the uploaded EA).

Ported so the EA can be measured on five years of real XAUUSD M5 data
instead of argued about. The logic below mirrors the .mq5 line for line;
where the original is ambiguous or buggy, the behaviour is reproduced as
written (not as intended) and the deviation is flagged in a comment, so
the backtest shows what the EA would actually do.

EA logic, as written:
  * Basket empty -> open 0.01 lot in the EMA50/EMA100 trend direction.
  * Fewer than InpTriggerPosCount positions -> breakout grid: BUY when
    price is a step above the highest entry, SELL when a step below the
    lowest. Note this pyramids in BOTH directions, so the basket becomes
    hedged.
  * At or above InpTriggerPosCount -> "focus" mode: add to whichever side
    currently has the higher floating profit, once price is `step` away
    from that side's most recent entry (MathAbs - either direction).
  * Next lot = InitialLot + positionCount * LotStep, so lots ramp
    0.01, 0.02, 0.03 ... with basket size.
  * Close everything when basket profit >= max(BaseTargetUSD,
    totalLots * ProfitPerLotUSD).
  * No stop-loss of any kind.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

CONTRACT = 100.0          # XAUUSD: 100 oz per lot


@dataclass
class EaParams:
    ema_fast: int = 50
    ema_slow: int = 100
    initial_lot: float = 0.01
    lot_step: float = 0.01
    step_price: float = 1.0
    trigger_pos_count: int = 6
    max_layers: int = 20
    base_target_usd: float = 2.0
    profit_per_lot_usd: float = 10.0
    # Execution costs (not in the EA - the EA simply pays whatever the
    # broker charges; the backtest has to model it explicitly).
    spread: float = 0.25
    slippage: float = 0.05
    min_lot: float = 0.01
    broker_max_lot: float = 100.0
    # Not part of the EA. Off by default so the default run measures the
    # EA as written; used only to answer "would a stop have saved it?".
    max_basket_loss_usd: float | None = None


@dataclass
class Pos:
    is_buy: bool
    lots: float
    open_price: float     # price actually paid, costs included
    seq: int              # opening order, stands in for POSITION_TIME


@dataclass
class Cycle:
    opened_bar: int
    closed_bar: int
    positions: int
    max_positions: int
    lots: float
    realised: float
    worst_floating: float
    reason: str


class EaState:
    """One basket. Mirrors the EA's per-tick decisions."""

    def __init__(self, p: EaParams):
        self.p = p
        self.positions: list[Pos] = []
        self.seq = 0
        self.worst_floating = 0.0
        self.max_positions = 0
        self.opened_bar = 0

    # ---- helpers mirroring the EA's accounting -----------------------
    def profit(self, bid: float, ask: float) -> float:
        """Floating P/L. A buy is valued at bid, a sell at ask - the same
        asymmetry the terminal applies, which is where the spread is paid
        a second time on exit."""
        total = 0.0
        for q in self.positions:
            mark = bid if q.is_buy else ask
            sign = 1.0 if q.is_buy else -1.0
            total += sign * (mark - q.open_price) * CONTRACT * q.lots
        return total

    def side_profit(self, bid: float, ask: float) -> tuple[float, float]:
        buy = sell = 0.0
        for q in self.positions:
            mark = bid if q.is_buy else ask
            sign = 1.0 if q.is_buy else -1.0
            pnl = sign * (mark - q.open_price) * CONTRACT * q.lots
            if q.is_buy:
                buy += pnl
            else:
                sell += pnl
        return buy, sell

    def volume(self) -> float:
        return round(sum(q.lots for q in self.positions), 2)

    def count(self) -> int:
        return len(self.positions)

    def last_price(self, is_buy: bool) -> float:
        """Most recently opened entry on that side, or 0.0 if none.

        0.0 matters: the EA tests `lastBuyPrice == 0 || distance >= step`,
        so an empty side opens immediately with no distance requirement.
        """
        best, price = -1, 0.0
        for q in self.positions:
            if q.is_buy == is_buy and q.seq > best:
                best, price = q.seq, q.open_price
        return price

    def extremes(self) -> tuple[float, float]:
        """(highestAll, lowestAll) over every entry price, reproducing the
        EA's zero-initialised min/max including its fallbacks."""
        if not self.positions:
            return 0.0, 0.0
        prices = [q.open_price for q in self.positions]
        return max(prices), min(prices)

    def next_lot(self) -> float:
        p = self.p
        raw = p.initial_lot + self.count() * p.lot_step
        stepped = np.floor(raw / p.min_lot) * p.min_lot
        stepped = min(max(stepped, p.min_lot), p.broker_max_lot)
        return round(stepped, 2)

    def required_tp(self) -> float:
        return max(self.p.base_target_usd, self.volume() * self.p.profit_per_lot_usd)

    # ---- actions ------------------------------------------------------
    def _add(self, is_buy: bool, lots: float, mid: float) -> None:
        p = self.p
        # Buy fills at ask + slippage, sell at bid - slippage.
        half = p.spread / 2.0
        price = mid + half + p.slippage if is_buy else mid - half - p.slippage
        self.positions.append(Pos(is_buy, lots, price, self.seq))
        self.seq += 1
        self.max_positions = max(self.max_positions, len(self.positions))

    def close_all(self, bid: float, ask: float) -> float:
        total = 0.0
        for q in self.positions:
            # Exit crosses the spread again and pays slippage.
            mark = (bid - self.p.slippage) if q.is_buy else (ask + self.p.slippage)
            sign = 1.0 if q.is_buy else -1.0
            total += sign * (mark - q.open_price) * CONTRACT * q.lots
        self.positions = []
        return total


def run_ea(df: pd.DataFrame, p: EaParams, start_balance: float = 500.0) -> dict:
    """Bar-path simulation of the EA over OHLC data.

    Each bar is walked open -> first extreme -> second extreme -> close.
    The extreme that is worse for the open basket is visited first, so
    adverse fills are always booked before any take-profit check. That is
    the pessimistic reading, which is the right one for a design with no
    stop-loss.
    """
    close = df["close"].to_numpy(dtype=float)
    high = df["high"].to_numpy(dtype=float)
    low = df["low"].to_numpy(dtype=float)
    open_ = df["open"].to_numpy(dtype=float)

    ema_f = df["close"].ewm(span=p.ema_fast, adjust=False).mean().to_numpy()
    ema_s = df["close"].ewm(span=p.ema_slow, adjust=False).mean().to_numpy()

    n = len(close)
    half = p.spread / 2.0

    balance = start_balance
    peak = start_balance
    max_dd = 0.0
    cycles: list[Cycle] = []
    worst_floating_ever = 0.0
    max_lots_ever = 0.0
    max_positions_ever = 0
    ruined_at: int | None = None

    st = EaState(p)
    warm = max(p.ema_fast, p.ema_slow)

    for i in range(warm, n):
        if st.count():
            b = st.profit(low[i] - half, low[i] + half)
            s = st.profit(high[i] - half, high[i] + half)
            path = (low[i], high[i]) if b <= s else (high[i], low[i])
        else:
            path = (low[i], high[i]) if close[i] >= open_[i] else (high[i], low[i])

        for mid in (open_[i], path[0], path[1], close[i]):
            bid, ask = mid - half, mid + half

            # The EA re-evaluates on every tick; a bar contains many. Loop
            # until the basket stops changing so a fast move can add more
            # than one layer inside a single bar, as it would live.
            for _ in range(p.max_layers + 2):
                cnt = st.count()

                if cnt:
                    pnl = st.profit(bid, ask)
                    if pnl < st.worst_floating:
                        st.worst_floating = pnl
                    equity = balance + pnl
                    peak = max(peak, equity)
                    if peak > 0:
                        max_dd = min(max_dd, (equity - peak) / peak)

                    hit_guard = (p.max_basket_loss_usd is not None
                                 and pnl <= -abs(p.max_basket_loss_usd))
                    # A real account is stopped out by the broker; model
                    # the floor at zero equity as the absolute limit.
                    hit_floor = equity <= 0.0

                    if pnl >= st.required_tp() or hit_guard or hit_floor:
                        reason = ("take_profit" if pnl >= st.required_tp()
                                  else "guard_stop" if hit_guard else "stop_out")
                        lots = st.volume()
                        realised = st.close_all(bid, ask)
                        balance += realised
                        worst_floating_ever = min(worst_floating_ever, st.worst_floating)
                        max_lots_ever = max(max_lots_ever, lots)
                        max_positions_ever = max(max_positions_ever, st.max_positions)
                        cycles.append(Cycle(
                            opened_bar=st.opened_bar, closed_bar=i, positions=cnt,
                            max_positions=st.max_positions, lots=lots,
                            realised=realised, worst_floating=st.worst_floating,
                            reason=reason,
                        ))
                        if reason == "stop_out" or balance <= 0.0:
                            ruined_at = i
                            break
                        st = EaState(p)
                        st.opened_bar = i
                        continue

                if st.count() == 0:
                    # Fresh cycle in the EMA trend direction.
                    if ema_f[i] > ema_s[i]:
                        st.opened_bar = i
                        st._add(True, p.initial_lot, mid)
                    elif ema_f[i] < ema_s[i]:
                        st.opened_bar = i
                        st._add(False, p.initial_lot, mid)
                    break

                if st.count() >= p.max_layers:
                    break

                lot = st.next_lot()
                if st.count() >= p.trigger_pos_count:
                    buy_p, sell_p = st.side_profit(bid, ask)
                    if buy_p >= sell_p:
                        last = st.last_price(True)
                        # NOTE: MathAbs in the original - distance in EITHER
                        # direction triggers another add on the winning side.
                        if last == 0.0 or abs(ask - last) >= p.step_price:
                            st._add(True, lot, mid)
                            continue
                    else:
                        last = st.last_price(False)
                        if last == 0.0 or abs(bid - last) >= p.step_price:
                            st._add(False, lot, mid)
                            continue
                    break
                else:
                    hi, lo = st.extremes()
                    if ask >= hi + p.step_price:
                        st._add(True, lot, mid)
                        continue
                    if lo > 0.0 and bid <= lo - p.step_price:
                        st._add(False, lot, mid)
                        continue
                    break
            if ruined_at is not None:
                break
        if ruined_at is not None:
            break

    if ruined_at is None and st.count():
        worst_floating_ever = min(worst_floating_ever, st.worst_floating)
        max_lots_ever = max(max_lots_ever, st.volume())
        max_positions_ever = max(max_positions_ever, st.max_positions)
        final_float = st.profit(close[-1] - half, close[-1] + half)
    else:
        final_float = 0.0

    return {
        "cycles": cycles,
        "n_cycles": len(cycles),
        "n_tp": sum(1 for c in cycles if c.reason == "take_profit"),
        "balance": balance,
        "final_floating": final_float,
        "net": balance + final_float - start_balance,
        "max_dd_pct": 100 * max_dd,
        "worst_floating_usd": worst_floating_ever,
        "max_lots": max_lots_ever,
        "max_positions": max_positions_ever,
        "ruined_at": ruined_at,
        "ruined_time": df.index[ruined_at] if ruined_at is not None else None,
    }
