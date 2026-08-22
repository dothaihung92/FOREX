"""Validation that every dashboard order passes before it reaches a broker.

Kept separate from the web and broker layers so it can be tested without a
terminal, and so there is exactly one place to read when asking "what can
this dashboard actually send to my account?".

The rules here are deliberately stricter than MT5's own. MT5 will happily
accept a 50-lot order with no stop on a $500 account; this project's whole
history says that is how accounts die, so the dashboard refuses.
"""
from __future__ import annotations

from dataclasses import dataclass

# A manual order still respects the project's core rule: never risk more of
# the account on one trade than the strategy would.
MAX_RISK_PCT_PER_ORDER = 5.0
MAX_LOTS_ABSOLUTE = 10.0


class OrderRejected(ValueError):
    """Raised instead of sending anything to the broker."""


@dataclass
class OrderRequest:
    symbol: str
    direction: int          # 1 buy, -1 sell
    lots: float
    sl: float               # absolute price, 0 = none
    tp: float               # absolute price, 0 = none
    price: float            # current market price, for validation


def contract_size(symbol: str) -> float:
    """Units per lot. Gold is 100 oz; FX majors are 100,000 units."""
    return 100.0 if symbol.upper().startswith("XAU") else 100_000.0


def risk_usd(req: OrderRequest) -> float:
    """Money at risk if the stop is hit. Zero stop means unbounded, which
    the validator rejects rather than reporting as 'no risk'."""
    if req.sl <= 0:
        return float("inf")
    return abs(req.price - req.sl) * contract_size(req.symbol) * req.lots


def validate(req: OrderRequest, equity: float, symbols: list[str],
             min_lot: float = 0.01, lot_step: float = 0.01,
             max_lot: float = MAX_LOTS_ABSOLUTE) -> None:
    """Raise OrderRejected if the order should not be sent. Returns None on
    success so callers cannot accidentally treat a rejection as a pass."""
    if req.symbol not in symbols:
        raise OrderRejected(f"unknown symbol {req.symbol!r}")
    if req.direction not in (1, -1):
        raise OrderRejected("direction must be 1 (buy) or -1 (sell)")
    if req.price <= 0:
        raise OrderRejected("no market price available for this symbol")

    # The stop is checked before anything else. Every blow-up documented in
    # this project traces back to an uncapped loss, so a missing stop must
    # be the error the user sees - not a lot-size complaint that happens to
    # fire first and hides it.
    if req.sl <= 0:
        raise OrderRejected(
            "a stop-loss is required - this dashboard does not send orders "
            "without a loss cap"
        )

    if req.lots <= 0:
        raise OrderRejected("lots must be greater than zero")
    if req.lots < min_lot:
        raise OrderRejected(f"lots below the broker minimum ({min_lot})")
    if req.lots > max_lot:
        raise OrderRejected(f"lots above the dashboard limit ({max_lot})")
    steps = req.lots / lot_step
    if abs(steps - round(steps)) > 1e-9:
        raise OrderRejected(f"lots must be a multiple of {lot_step}")

    if req.direction == 1 and req.sl >= req.price:
        raise OrderRejected("buy stop-loss must be below the current price")
    if req.direction == -1 and req.sl <= req.price:
        raise OrderRejected("sell stop-loss must be above the current price")
    if req.tp:
        if req.direction == 1 and req.tp <= req.price:
            raise OrderRejected("buy take-profit must be above the current price")
        if req.direction == -1 and req.tp >= req.price:
            raise OrderRejected("sell take-profit must be below the current price")

    if equity <= 0:
        raise OrderRejected("account equity is zero or negative")
    risk = risk_usd(req)
    pct = 100.0 * risk / equity
    if pct > MAX_RISK_PCT_PER_ORDER:
        raise OrderRejected(
            f"order risks ${risk:,.2f} = {pct:.1f}% of equity, above the "
            f"{MAX_RISK_PCT_PER_ORDER}% per-order limit"
        )


def suggest_lots(symbol: str, price: float, sl: float, equity: float,
                 risk_pct: float, min_lot: float = 0.01,
                 lot_step: float = 0.01) -> tuple[float, float]:
    """Lot size that risks `risk_pct` of equity given the chosen stop.

    Mirrors RiskManager's percent-risk maths so the manual panel and the
    bot size positions the same way.

    Returns (lots, actual_risk_pct). The second value is not decoration:
    the broker's minimum lot can be larger than the requested risk allows,
    and on a small account it routinely is. XAUUSD at min_lot 0.02 is 2 oz,
    so a $500 account cannot risk 2% on a normal stop - it is forced to
    roughly double that. Returning the real figure means the caller can
    surface it instead of silently handing the user twice the risk they
    asked for.
    """
    distance = abs(price - sl)
    if distance <= 0 or equity <= 0:
        return min_lot, float("inf")
    per_lot = distance * contract_size(symbol)
    raw = (risk_pct / 100.0 * equity) / per_lot
    stepped = round(raw / lot_step) * lot_step
    lots = max(min_lot, round(stepped, 2))
    actual_pct = 100.0 * lots * per_lot / equity
    return lots, actual_pct
