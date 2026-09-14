"""Two-sided martingale grid with a basket take-profit and no stop-loss.

Requested design, implemented exactly as specified:

  * Open a BUY and a SELL at the same time (both ends), 0.01 lot each.
  * Pre-place 10 levels per side, spaced `step` dollars apart. Each level
    down the ladder adds 0.01 lot (0.01, 0.02, ... 0.10).
  * No stop-loss anywhere.
  * When the basket's combined floating P/L reaches `tp_usd`, close every
    position and start a fresh cycle at the current price.

This is a martingale/grid design. The project's README documents why that
family is dangerous, and the earlier one-sided DCA test is the closest
relative. This module exists so the idea can be measured rather than
argued about - built faithfully, backtested on real data, and reported
with the worst case in plain numbers.

Bar-path convention: M5 OHLC hides the order in which the high and low
were touched. Each bar is walked in the conservative order - the extreme
that fills MORE grid levels is visited first, so adverse fills are always
booked before any take-profit check. Where the two extremes are equally
adverse, the direction of the bar decides. This under-states profit and
over-states exposure, which is the right way round for a design whose
whole risk lives in how deep the ladder gets.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

CONTRACT = 100.0          # XAUUSD: 100 oz per lot, so 0.01 lot = 1 oz = $1 per $1 move


@dataclass
class GridParams:
    step: float = 1.0             # dollars between levels
    levels: int = 10              # pending levels per side
    base_lot: float = 0.01        # first level
    lot_increment: float = 0.01   # added per level down the ladder
    tp_usd: float = 1.5           # basket profit that closes everything
    spread: float = 0.25          # round-turn cost per unit price
    slippage: float = 0.05        # per side
    # No stop-loss field exists on purpose: the design has none. The
    # optional guards below are off unless explicitly switched on, so the
    # default run measures the design as requested.
    max_basket_loss_usd: float | None = None
    margin_stop_out_equity: float | None = None


@dataclass
class Leg:
    direction: int        # +1 buy, -1 sell
    lots: float
    entry: float


@dataclass
class CycleResult:
    opened_at: int
    closed_at: int | None
    legs_filled: int
    realised_usd: float
    worst_floating_usd: float
    max_lots_one_side: float
    closed_reason: str


def _lot_for_level(p: GridParams, level: int) -> float:
    """Level 0 is the market entry; each further level adds one increment."""
    return round(p.base_lot + level * p.lot_increment, 2)


def total_ladder_lots(p: GridParams) -> float:
    return round(sum(_lot_for_level(p, i) for i in range(p.levels)), 2)


def basket_pnl(legs: list[Leg], price: float) -> float:
    return sum(leg.direction * (price - leg.entry) * CONTRACT * leg.lots for leg in legs)


def opening_cost(p: GridParams) -> float:
    """What it costs in spread+slippage to fill the whole ladder on BOTH
    sides. Worth computing before any backtest: if this exceeds tp_usd,
    the design is structurally unable to pay for itself once it is fully
    extended, regardless of what price does next."""
    per_side = total_ladder_lots(p)
    cost_per_lot = (p.spread + 2 * p.slippage) * CONTRACT
    return 2 * per_side * cost_per_lot


class TwoSidedGrid:
    """One cycle of the grid. Kept explicit so the backtest and any live
    port follow identical logic."""

    def __init__(self, params: GridParams, start_price: float, start_index: int):
        self.p = params
        self.start_price = start_price
        self.start_index = start_index
        self.legs: list[Leg] = []
        self.worst_floating = 0.0
        # Level 0 fills immediately on both sides, at market.
        self._fill(+1, 0, start_price)
        self._fill(-1, 0, start_price)
        # Levels 1..n-1 wait as pending orders. Buys sit below, sells above:
        # each side averages into its own losing direction, which is what
        # "add a level every dollar it goes against you" means.
        self.pending_buy = {
            lvl: start_price - lvl * self.p.step for lvl in range(1, self.p.levels)
        }
        self.pending_sell = {
            lvl: start_price + lvl * self.p.step for lvl in range(1, self.p.levels)
        }

    def _fill(self, direction: int, level: int, price: float) -> None:
        lots = _lot_for_level(self.p, level)
        # Pay half the spread plus slippage on entry, in the adverse direction.
        entry = price + direction * (self.p.spread / 2 + self.p.slippage)
        self.legs.append(Leg(direction=direction, lots=lots, entry=entry))

    def lots_per_side(self) -> tuple[float, float]:
        buy = sum(l.lots for l in self.legs if l.direction > 0)
        sell = sum(l.lots for l in self.legs if l.direction < 0)
        return round(buy, 2), round(sell, 2)

    def fills_at(self, price: float) -> int:
        """How many pending levels this price would trigger."""
        n = sum(1 for lvl, lp in self.pending_buy.items() if price <= lp)
        n += sum(1 for lvl, lp in self.pending_sell.items() if price >= lp)
        return n

    def trigger(self, price: float) -> None:
        for lvl in [l for l, lp in self.pending_buy.items() if price <= lp]:
            self._fill(+1, lvl, self.pending_buy.pop(lvl))
        for lvl in [l for l, lp in self.pending_sell.items() if price >= lp]:
            self._fill(-1, lvl, self.pending_sell.pop(lvl))

    def mark(self, price: float) -> float:
        pnl = basket_pnl(self.legs, price)
        if pnl < self.worst_floating:
            self.worst_floating = pnl
        return pnl

    def net_exposure(self) -> float:
        """Dollars of basket P/L per $1 of price movement."""
        return sum(leg.direction * CONTRACT * leg.lots for leg in self.legs)

    def price_for_pnl(self, target: float) -> float | None:
        """The exact price at which the basket is worth `target`.

        Basket P/L is linear in price, so this is solvable rather than
        approximated. It matters: a basket is closed the instant it crosses
        $1.50, not at whatever extreme the bar happened to reach. Marking
        the exit at the bar's high overstates every winning cycle - the
        first version of this module did exactly that and produced a
        fictional +$204k.
        """
        a = self.net_exposure()
        if abs(a) < 1e-12:
            return None
        b = sum(leg.direction * CONTRACT * leg.lots * leg.entry for leg in self.legs)
        return (target + b) / a

    def close_all(self, price: float) -> float:
        """Exit every leg, paying the other half of the spread + slippage."""
        total = 0.0
        for leg in self.legs:
            exit_price = price - leg.direction * (self.p.spread / 2 + self.p.slippage)
            total += leg.direction * (exit_price - leg.entry) * CONTRACT * leg.lots
        return total


def run_grid(df: pd.DataFrame, p: GridParams, start_balance: float = 500.0,
             max_cycles: int | None = None) -> dict:
    """Backtest continuous cycles: close a basket, immediately start another."""
    high = df["high"].to_numpy()
    low = df["low"].to_numpy()
    close = df["close"].to_numpy()
    open_ = df["open"].to_numpy()
    n = len(close)

    balance = start_balance
    peak = start_balance
    max_dd = 0.0
    cycles: list[CycleResult] = []
    worst_floating_ever = 0.0
    max_lots_ever = 0.0
    ruined_at: int | None = None

    grid = TwoSidedGrid(p, close[0], 0)
    i = 1
    while i < n:
        # Visit the bar's extremes in the order that fills the most levels
        # first - the pessimistic path for a design with no stop.
        fills_hi, fills_lo = grid.fills_at(high[i]), grid.fills_at(low[i])
        if fills_lo > fills_hi:
            path = (low[i], high[i])
        elif fills_hi > fills_lo:
            path = (high[i], low[i])
        else:
            path = (low[i], high[i]) if close[i] >= open_[i] else (high[i], low[i])

        prev_price = open_[i]
        for price in path:
            grid.trigger(price)
            pnl = grid.mark(price)
            buy_lots, sell_lots = grid.lots_per_side()
            max_lots_ever = max(max_lots_ever, buy_lots, sell_lots)

            equity = balance + pnl
            if equity > peak:
                peak = equity
            if peak > 0:
                dd = (equity - peak) / peak
                if dd < max_dd:
                    max_dd = dd

            # A real account is force-closed by the broker long before
            # equity reaches zero; model the hard floor at minimum.
            floor = p.margin_stop_out_equity if p.margin_stop_out_equity is not None else 0.0
            hit_floor = equity <= floor
            hit_guard = (p.max_basket_loss_usd is not None
                         and pnl <= -abs(p.max_basket_loss_usd))

            if pnl >= p.tp_usd or hit_guard or hit_floor:
                reason = ("take_profit" if pnl >= p.tp_usd
                          else "guard_stop" if hit_guard else "stop_out")
                # Exit where the threshold was actually crossed, not at the
                # bar's extreme. Only trim back toward prev_price - never
                # past it, which would be inventing a price the bar did not
                # trade through.
                exit_price = price
                if reason == "take_profit":
                    target_price = grid.price_for_pnl(p.tp_usd)
                    if target_price is not None:
                        lo_b, hi_b = min(prev_price, price), max(prev_price, price)
                        if lo_b <= target_price <= hi_b:
                            exit_price = target_price
                realised = grid.close_all(exit_price)
                balance += realised
                worst_floating_ever = min(worst_floating_ever, grid.worst_floating)
                b, s = grid.lots_per_side()
                cycles.append(CycleResult(
                    opened_at=grid.start_index, closed_at=i,
                    legs_filled=len(grid.legs), realised_usd=realised,
                    worst_floating_usd=grid.worst_floating,
                    max_lots_one_side=max(b, s), closed_reason=reason,
                ))
                if reason == "stop_out" or balance <= floor:
                    ruined_at = i
                    break
                grid = TwoSidedGrid(p, exit_price, i)
                break
            prev_price = price

        if ruined_at is not None:
            break
        i += 1

    if ruined_at is None:
        worst_floating_ever = min(worst_floating_ever, grid.worst_floating)
        final_float = basket_pnl(grid.legs, close[-1])
    else:
        final_float = 0.0

    wins = [c for c in cycles if c.closed_reason == "take_profit"]
    return {
        "cycles": cycles,
        "n_cycles": len(cycles),
        "n_tp": len(wins),
        "balance": balance,
        "final_floating": final_float,
        "equity": balance + final_float,
        "net": balance + final_float - start_balance,
        "max_dd_pct": 100 * max_dd,
        "worst_floating_usd": worst_floating_ever,
        "max_lots_one_side": max_lots_ever,
        "ruined_at": ruined_at,
        "ruined_time": df.index[ruined_at] if ruined_at is not None else None,
        "open_legs_at_end": 0 if ruined_at is not None else len(grid.legs),
    }
