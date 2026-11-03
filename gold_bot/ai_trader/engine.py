"""The decision loop: snapshot -> advisor -> guard -> broker, with an audit log.

The same `Engine.step` runs in replay, paper and live; only the broker and
the source of bars differ. The advisor's output is never executed directly:
`RiskGuard.review` is the only door to the broker.
"""
from __future__ import annotations

import json
import os
from collections import Counter
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from .broker import CONTRACT
from .context import build_context
from .guard import GuardState, RiskGuard
from .schema import Decision


@dataclass
class EngineConfig:
    consult_every: int = 3        # flat: ask the advisor on every Nth M5 bar (= each M15)
    manage_every: int = 3         # in a position: ask this often
    lookback: int = 5000
    log_path: str = ""            # JSONL audit trail of every consultation
    state_path: str = ""          # guard state survives restarts when set


@dataclass
class Stats:
    consults: int = 0
    skipped_blocked: int = 0
    actions: Counter = field(default_factory=Counter)        # what the advisor proposed
    executed: Counter = field(default_factory=Counter)       # what actually reached the broker
    rejected: Counter = field(default_factory=Counter)       # why the guard said no


class Engine:
    def __init__(self, advisor, broker, guard: RiskGuard, cfg: EngineConfig | None = None):
        self.advisor = advisor
        self.broker = broker
        self.guard = guard
        self.cfg = cfg or EngineConfig()
        self.stats = Stats()
        self._open_risk = 0.0
        if self.cfg.state_path and os.path.exists(self.cfg.state_path):
            try:
                with open(self.cfg.state_path) as f:
                    self.guard.state = GuardState.from_dict(json.load(f))
            except (OSError, ValueError):
                pass

    # ------------------------------------------------------------------ helpers
    def _position_dict(self, mid: float, bar: int, spread: float) -> dict | None:
        p = self.broker.position
        if not p:
            return None
        risk = p.risk_usd or self._open_risk
        pnl = p.sign * ((mid - p.sign * spread / 2) - p.entry) * p.lots * CONTRACT
        return {
            "side": p.side, "lots": p.lots, "entry": round(p.entry, 2),
            "sl": round(p.sl, 2), "tp": round(p.tp, 2), "pnl": pnl,
            "r": pnl / risk if risk else 0.0,
            "bars_held": bar - p.opened_bar, "max_fav_r": p.max_fav_r,
            "sl_at_be": p.sign * (p.sl - p.entry) >= 0,
        }

    def _log(self, rec: dict) -> None:
        if not self.cfg.log_path:
            return
        with open(self.cfg.log_path, "a") as f:
            f.write(json.dumps(rec, default=str) + "\n")

    def _save_state(self) -> None:
        """Persist guard state whenever it changes, not only when the advisor is
        consulted - a restart right after a losing trade must not forget the streak."""
        if not self.cfg.state_path:
            return
        snap = self.guard.state.to_dict()
        if snap == getattr(self, "_last_saved", None):
            return
        with open(self.cfg.state_path, "w") as f:
            json.dump(snap, f)
        self._last_saved = snap

    # --------------------------------------------------------------------- step
    def step(self, closed: pd.DataFrame, bar: int) -> dict | None:
        """Run after bar `bar` has CLOSED. `closed` ends at that bar."""
        cfg, g = self.cfg, self.guard
        t = closed.index[-1]
        mid = float(closed["close"].iloc[-1])
        spread = self.broker.current_spread()
        equity = self.broker.equity(mid)

        for tr in self.broker.drain_closed():
            g.on_trade_closed(tr.pnl, bar)
        g.on_bar(t, bar, equity)
        self._save_state()

        pos = self._position_dict(mid, bar, spread)
        if pos:
            if (pos["bars_held"] % cfg.manage_every) != 0:
                return None
        else:
            if bar % cfg.consult_every != 0:
                return None
            blocked = g.entries_blocked(int(t.hour), bar, equity, spread, 0)
            if blocked:
                self.stats.skipped_blocked += 1          # no model call is spent on a dead end
                return None

        lim = g.cfg
        ctx = build_context(
            closed, bar_index=bar, spread=spread, position=pos, lookback=cfg.lookback,
            guard={"trades_today": g.state.trades_today, "max_trades_per_day": lim.max_trades_per_day,
                   "consecutive_losses": g.state.consecutive_losses,
                   "day_pnl": equity - g.state.day_start_equity},
            limits={"sl_min": 0.0, "sl_max": 0.0,        # filled in below, once ATR is known
                    "min_rr": lim.min_rr, "min_confidence": lim.min_confidence},
        )
        if ctx is None:
            return None
        ctx.limits["sl_min"] = lim.sl_atr_min * ctx.atr
        ctx.limits["sl_max"] = lim.sl_atr_max * ctx.atr

        self.stats.consults += 1
        try:
            d = self.advisor.decide(ctx)
        except Exception as exc:                          # noqa: BLE001 - an advisor must not stop the loop
            d = Decision(reasoning=f"advisor error: {type(exc).__name__}")
        self.stats.actions[d.action] += 1

        try:
            rv = g.review(d, price=ctx.price, atr=ctx.atr, hour=ctx.hour, bar_index=bar,
                          equity=equity, spread=spread, position=pos)
        except Exception as exc:                          # noqa: BLE001 - fail closed, never open on an error
            from .guard import Review
            rv = Review(False, "HOLD", f"guard error, refusing: {type(exc).__name__}")
        if not rv.ok and d.action != "HOLD":
            self.stats.rejected[rv.reason.split(" (")[0][:60]] += 1

        executed = "HOLD"
        if rv.ok:
            if rv.action in ("OPEN_BUY", "OPEN_SELL"):
                side = "BUY" if rv.action == "OPEN_BUY" else "SELL"
                self.broker.open(side, rv.lots, rv.stop_loss, rv.take_profit, mid, bar, t, rv.risk_usd)
                self._open_risk = rv.risk_usd
                g.on_trade_opened()
                executed = rv.action
            elif rv.action == "ADJUST":
                self.broker.modify(rv.stop_loss, rv.take_profit)
                executed = "ADJUST"
            elif rv.action == "CLOSE":
                self.broker.close_now(mid, bar, "advisor")
                executed = "CLOSE"
        if executed != "HOLD":
            self.stats.executed[executed] += 1

        rec = {"time": t, "bar": bar, "price": ctx.price, "equity": round(equity, 2),
               "proposed": d.to_dict(), "approved": rv.ok, "executed": executed,
               "guard": rv.reason, "lots": rv.lots, "risk_usd": rv.risk_usd, "meta": d.meta}
        self._log(rec)
        self._save_state()
        return rec


# ----------------------------------------------------------------------- replay
def replay(df: pd.DataFrame, engine: Engine, start: int, end: int, should_stop=None) -> int:
    """Walk bar by bar. The position is resolved against bar i first, then the
    advisor decides using data up to and including bar i.

    `should_stop` lets a caller cap spending (paid model calls). Returns the
    index of the last bar processed.
    """
    o, h, l, c = (df[k].to_numpy() for k in ("open", "high", "low", "close"))
    lb = engine.cfg.lookback
    last = start
    for i in range(start, end):
        engine.broker.on_bar(o[i], h[i], l[i], c[i], i)
        engine.step(df.iloc[max(0, i - lb + 1): i + 1], i)
        last = i
        if should_stop and should_stop():
            break
    # Anything still open at the end is marked, not forgotten.
    if engine.broker.position:
        engine.broker.close_now(float(c[last]), last, "end of data")
    return last


def summarize(broker, start_balance: float) -> dict:
    trades = broker.closed
    if not trades:
        return {"trades": 0}
    pnl = np.array([t.pnl for t in trades])
    wins, losses = pnl[pnl > 0], pnl[pnl <= 0]
    curve = start_balance + np.cumsum(pnl)
    peak = np.maximum.accumulate(np.concatenate([[start_balance], curve]))[1:]
    return {
        "trades": len(trades),
        "win_pct": round(100 * len(wins) / len(trades), 1),
        "net": round(float(pnl.sum()), 2),
        "profit_factor": round(float(wins.sum() / -losses.sum()), 2) if losses.sum() < 0 else float("inf"),
        "avg_r": round(float(np.mean([t.r for t in trades])), 2),
        "worst_loss_usd": round(float(pnl.min()), 2),
        "max_dd_pct": round(float(100 * ((curve - peak) / peak).min()), 1),
        "final_balance": round(float(curve[-1]), 2),
    }
