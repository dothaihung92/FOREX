"""Risk guard: the part of the system the AI cannot override.

Every rule here exists because an earlier experiment in this repository
showed what happens without it:

* A stop-loss is mandatory and can only ever be TIGHTENED. Holding a loser
  and hoping is the pattern that wiped out every grid/no-stop design tested.
* Lot size is computed here from the stop distance. The model never chooses
  it. A trade that would need less than the minimum lot is REJECTED, not
  rounded up (rounding up silently doubled the intended risk once already).
* Daily loss, trade-count, consecutive-loss and drawdown limits halt new
  entries. Open positions keep their broker-side stop regardless.
"""
from __future__ import annotations

import math
import os
from dataclasses import dataclass, field

from .schema import Decision

CONTRACT = 100.0          # oz per lot, XAUUSD


@dataclass
class GuardConfig:
    base_equity: float = 1000.0          # risk is a % of THIS, not of live equity
    risk_per_trade_pct: float = 1.0
    min_lot: float = 0.01
    lot_step: float = 0.01
    max_lot: float = 5.0

    sl_atr_min: float = 1.0              # stop distance bounds, in M5 ATRs
    sl_atr_max: float = 4.0
    min_rr: float = 1.5                  # net of costs
    min_confidence: float = 0.60
    be_buffer_atr: float = 0.10          # how close a tightened SL may sit to price

    max_open_positions: int = 1
    max_trades_per_day: int = 3
    max_daily_loss_pct: float = 3.0
    max_consecutive_losses: int = 3
    cooldown_bars: int = 24              # M5 bars paused after the streak limit
    max_drawdown_halt_pct: float = 15.0  # from base_equity; halts until reset

    allowed_hours_utc: tuple = tuple(range(7, 20))
    excluded_hours_utc: tuple = (7,)     # hour 7 tested worse in this project
    max_spread: float = 0.60
    spread: float = 0.25                 # cost model used for the net-RR check
    slippage: float = 0.05
    kill_switch_path: str = ""           # if this file exists, no new entries


@dataclass
class GuardState:
    day: str = ""
    day_start_equity: float = 0.0
    trades_today: int = 0
    consecutive_losses: int = 0
    cooldown_until_bar: int = -1
    peak_equity: float = 0.0
    halted: bool = False
    halt_reason: str = ""

    def to_dict(self) -> dict:
        return dict(self.__dict__)

    @classmethod
    def from_dict(cls, raw: dict) -> "GuardState":
        known = {k: v for k, v in raw.items() if k in cls().__dict__}
        return cls(**known)


@dataclass
class Review:
    ok: bool
    action: str                  # the action that may actually be executed
    reason: str
    lots: float = 0.0
    stop_loss: float = 0.0
    take_profit: float = 0.0
    risk_usd: float = 0.0


def floor_to_step(x: float, step: float) -> float:
    """Floor to a lot step without the IEEE-754 trap (0.07/0.01 = 6.999...)."""
    return math.floor(x / step + 1e-9) * step


class RiskGuard:
    def __init__(self, cfg: GuardConfig | None = None, state: GuardState | None = None):
        self.cfg = cfg or GuardConfig()
        self.state = state or GuardState()

    # ------------------------------------------------------------------ clock
    def on_bar(self, bar_time, bar_index: int, equity: float) -> None:
        s, c = self.state, self.cfg
        day = str(bar_time.date())
        if day != s.day:
            s.day, s.day_start_equity, s.trades_today = day, equity, 0
        if s.peak_equity == 0.0:
            s.peak_equity = equity
        s.peak_equity = max(s.peak_equity, equity)
        floor = c.base_equity * (1 - c.max_drawdown_halt_pct / 100.0)
        if equity <= floor and not s.halted:
            s.halted = True
            s.halt_reason = (f"equity {equity:.2f} fell to the {c.max_drawdown_halt_pct:.0f}% "
                             f"drawdown limit ({floor:.2f}); manual reset required")

    def on_trade_closed(self, pnl: float, bar_index: int) -> None:
        s, c = self.state, self.cfg
        if pnl < 0:
            s.consecutive_losses += 1
            if s.consecutive_losses >= c.max_consecutive_losses:
                s.cooldown_until_bar = bar_index + c.cooldown_bars
                s.consecutive_losses = 0
        else:
            s.consecutive_losses = 0

    def on_trade_opened(self) -> None:
        self.state.trades_today += 1

    def reset_halt(self) -> None:
        self.state.halted, self.state.halt_reason = False, ""

    # ----------------------------------------------------------- entry checks
    def entries_blocked(self, hour: int, bar_index: int, equity: float,
                        spread: float, open_positions: int) -> str:
        """Empty string when new entries are allowed, else the reason.

        Also used by the engine to avoid paying for a model call that could
        not result in a trade anyway.
        """
        c, s = self.cfg, self.state
        if c.kill_switch_path and os.path.exists(c.kill_switch_path):
            return "kill switch file present"
        if s.halted:
            return f"halted: {s.halt_reason}"
        if open_positions >= c.max_open_positions:
            return "a position is already open"
        if hour not in c.allowed_hours_utc or hour in c.excluded_hours_utc:
            return f"hour {hour} UTC is outside the trading window"
        if spread > c.max_spread:
            return f"spread {spread:.2f} above limit {c.max_spread:.2f}"
        if s.trades_today >= c.max_trades_per_day:
            return f"daily trade limit {c.max_trades_per_day} reached"
        day_loss = s.day_start_equity - equity
        if day_loss >= c.base_equity * c.max_daily_loss_pct / 100.0:
            return f"daily loss limit reached ({day_loss:.2f})"
        if bar_index < s.cooldown_until_bar:
            return "cooling down after consecutive losses"
        return ""

    # ----------------------------------------------------------------- review
    def review(self, d: Decision, *, price: float, atr: float, hour: int,
               bar_index: int, equity: float, spread: float,
               position: dict | None) -> Review:
        """Turn a proposal into something safe to execute, or refuse it."""
        c = self.cfg
        hold = lambda why: Review(False, "HOLD", why)   # noqa: E731

        if d.action == "HOLD":
            return Review(True, "HOLD", "advisor chose to hold")

        # Defence in depth: the parser already scrubs these, but the guard must
        # not rely on it. A NaN or infinity must be refused, never computed with.
        if not all(math.isfinite(x) for x in (d.confidence, d.stop_loss, d.take_profit)):
            return hold("non-finite number in proposal")
        if not all(math.isfinite(x) for x in (price, atr, equity, spread)):
            return hold("non-finite market data")

        # ---- management of an existing position ----------------------------
        if d.action == "CLOSE":
            return (Review(True, "CLOSE", "close requested")
                    if position else hold("CLOSE with no open position"))

        if d.action == "ADJUST":
            if not position:
                return hold("ADJUST with no open position")
            return self._review_adjust(d, position, price, atr)

        # ---- new entry ------------------------------------------------------
        if position:
            return hold("OPEN while a position is open (no averaging down, no pyramiding)")
        blocked = self.entries_blocked(hour, bar_index, equity, spread, 0)
        if blocked:
            return hold(blocked)
        if atr <= 0:
            return hold("ATR unavailable")
        if d.confidence < c.min_confidence:
            return hold(f"confidence {d.confidence:.2f} below {c.min_confidence:.2f}")

        buy = d.action == "OPEN_BUY"
        sign = 1 if buy else -1
        if d.stop_loss <= 0:
            return hold("no stop-loss supplied - a stop is mandatory")
        if d.take_profit <= 0:
            return hold("no take-profit supplied")
        sl_dist = sign * (price - d.stop_loss)
        tp_dist = sign * (d.take_profit - price)
        if sl_dist <= 0:
            return hold("stop-loss is on the wrong side of price")
        if tp_dist <= 0:
            return hold("take-profit is on the wrong side of price")
        if sl_dist < c.sl_atr_min * atr:
            return hold(f"stop {sl_dist:.2f} is tighter than {c.sl_atr_min} ATR ({c.sl_atr_min*atr:.2f})")
        if sl_dist > c.sl_atr_max * atr:
            return hold(f"stop {sl_dist:.2f} is wider than {c.sl_atr_max} ATR ({c.sl_atr_max*atr:.2f})")

        cost = spread + 2 * c.slippage
        net_rr = (tp_dist - cost) / (sl_dist + cost)
        if net_rr < c.min_rr:
            return hold(f"net reward:risk {net_rr:.2f} below {c.min_rr:.2f} after costs")

        risk_usd = c.base_equity * c.risk_per_trade_pct / 100.0
        loss_per_lot = (sl_dist + cost) * CONTRACT          # include costs in the loss
        lots = floor_to_step(risk_usd / loss_per_lot, c.lot_step)
        lots = min(lots, c.max_lot)
        if lots < c.min_lot - 1e-9:
            need = loss_per_lot * c.min_lot
            return hold(f"a {sl_dist:.2f} stop needs ${need:.2f} risk at the minimum lot, "
                        f"above the ${risk_usd:.2f} budget - rejected, not rounded up")
        lots = round(lots, 2)
        return Review(True, d.action, "approved", lots=lots,
                      stop_loss=d.stop_loss, take_profit=d.take_profit,
                      risk_usd=round(lots * loss_per_lot, 2))

    def _review_adjust(self, d: Decision, pos: dict, price: float, atr: float) -> Review:
        c = self.cfg
        sign = 1 if pos["side"] == "BUY" else -1
        entry, cur_sl, cur_tp = pos["entry"], pos["sl"], pos["tp"]
        new_sl = cur_sl
        new_tp = cur_tp
        notes = []

        if d.stop_loss > 0 and abs(d.stop_loss - cur_sl) > 1e-9:
            tighter = sign * (d.stop_loss - cur_sl) > 0
            if not tighter:
                return Review(False, "HOLD", "refused: a stop may only be tightened, never widened")
            if sign * (price - d.stop_loss) < c.be_buffer_atr * atr:
                return Review(False, "HOLD", "refused: new stop is too close to (or beyond) price")
            new_sl = d.stop_loss
            notes.append("stop tightened")

        if d.take_profit > 0 and abs(d.take_profit - cur_tp) > 1e-9:
            extends = sign * (d.take_profit - cur_tp) > 0
            if sign * (d.take_profit - price) <= c.be_buffer_atr * atr:
                return Review(False, "HOLD", "refused: take-profit too close to (or beyond) price")
            if extends and sign * (new_sl - entry) < 0:
                return Review(False, "HOLD",
                              "refused: the target may only be extended once the stop is at breakeven or better")
            new_tp = d.take_profit
            notes.append("target extended" if extends else "target pulled in")

        if not notes:
            return Review(True, "HOLD", "nothing to adjust")
        return Review(True, "ADJUST", "; ".join(notes), stop_loss=new_sl, take_profit=new_tp)
