import pytest

from gold_bot.dashboard.orders import (
    MAX_RISK_PCT_PER_ORDER,
    OrderRejected,
    OrderRequest,
    contract_size,
    risk_usd,
    suggest_lots,
    validate,
)

SYMBOLS = ["XAUUSD", "EURUSD"]


def gold(**kw):
    base = dict(symbol="XAUUSD", direction=1, lots=0.02, sl=3350.0, tp=3400.0,
                price=3362.0)
    base.update(kw)
    return OrderRequest(**base)


def test_contract_size_distinguishes_metals_from_fx():
    assert contract_size("XAUUSD") == 100.0
    assert contract_size("EURUSD") == 100_000.0


def test_risk_is_unbounded_without_a_stop():
    assert risk_usd(gold(sl=0.0)) == float("inf")


def test_risk_matches_hand_calculation():
    # 12 points x 100 oz x 0.02 lots = $24
    assert risk_usd(gold(sl=3350.0, price=3362.0)) == pytest.approx(24.0)


def test_valid_order_passes():
    validate(gold(), equity=5000.0, symbols=SYMBOLS, min_lot=0.02)


def test_missing_stop_is_rejected_and_named_first():
    """The stop check must win over lot-size complaints, so the user sees
    the safety problem rather than a size nitpick that masks it."""
    with pytest.raises(OrderRejected, match="stop-loss is required"):
        validate(gold(sl=0.0, lots=0.001), equity=5000.0, symbols=SYMBOLS, min_lot=0.02)


def test_stop_on_wrong_side_rejected_both_directions():
    with pytest.raises(OrderRejected, match="below the current price"):
        validate(gold(direction=1, sl=3400.0), equity=5000.0, symbols=SYMBOLS, min_lot=0.02)
    with pytest.raises(OrderRejected, match="above the current price"):
        validate(gold(direction=-1, sl=3300.0, tp=3300.0), equity=5000.0,
                 symbols=SYMBOLS, min_lot=0.02)


def test_take_profit_on_wrong_side_rejected():
    with pytest.raises(OrderRejected, match="take-profit must be above"):
        validate(gold(tp=3300.0), equity=5000.0, symbols=SYMBOLS, min_lot=0.02)


def test_zero_take_profit_is_allowed():
    validate(gold(tp=0.0), equity=5000.0, symbols=SYMBOLS, min_lot=0.02)


def test_unknown_symbol_rejected():
    with pytest.raises(OrderRejected, match="unknown symbol"):
        validate(gold(symbol="DOGEUSD"), equity=5000.0, symbols=SYMBOLS, min_lot=0.02)


def test_bad_direction_rejected():
    with pytest.raises(OrderRejected, match="direction"):
        validate(gold(direction=0), equity=5000.0, symbols=SYMBOLS, min_lot=0.02)


def test_lot_bounds_and_step():
    with pytest.raises(OrderRejected, match="greater than zero"):
        validate(gold(lots=0.0), equity=5000.0, symbols=SYMBOLS, min_lot=0.02)
    with pytest.raises(OrderRejected, match="broker minimum"):
        validate(gold(lots=0.01), equity=5000.0, symbols=SYMBOLS, min_lot=0.02)
    with pytest.raises(OrderRejected, match="dashboard limit"):
        validate(gold(lots=50.0), equity=10_000_000.0, symbols=SYMBOLS, min_lot=0.02)
    with pytest.raises(OrderRejected, match="multiple of"):
        validate(gold(lots=0.025), equity=5000.0, symbols=SYMBOLS, min_lot=0.02)


def test_risk_cap_blocks_oversized_orders():
    # 0.02 lots risking $24 against $100 equity = 24%, far above the cap.
    with pytest.raises(OrderRejected, match="per-order limit"):
        validate(gold(), equity=100.0, symbols=SYMBOLS, min_lot=0.02)


def test_risk_cap_boundary_is_enforced_not_approximated():
    """An order at exactly the cap passes; a hair over does not."""
    req = gold(sl=3350.0, price=3362.0, lots=0.02)      # $24 risk
    equity_at_cap = 100 * risk_usd(req) / MAX_RISK_PCT_PER_ORDER
    validate(req, equity=equity_at_cap, symbols=SYMBOLS, min_lot=0.02)
    with pytest.raises(OrderRejected):
        validate(req, equity=equity_at_cap * 0.99, symbols=SYMBOLS, min_lot=0.02)


def test_zero_equity_rejected():
    with pytest.raises(OrderRejected, match="equity"):
        validate(gold(), equity=0.0, symbols=SYMBOLS, min_lot=0.02)


def test_suggest_lots_hits_the_requested_risk_when_it_can():
    lots, pct = suggest_lots("XAUUSD", price=3362.0, sl=3352.0, equity=10_000.0,
                             risk_pct=2.0, min_lot=0.01)
    # 2% of 10k = $200; 10 points x 100 oz = $1000/lot -> 0.20 lots
    assert lots == pytest.approx(0.20)
    assert pct == pytest.approx(2.0, abs=0.05)


def test_suggest_lots_reports_the_real_risk_when_min_lot_overshoots():
    """A $500 account cannot risk 2% on gold at min_lot 0.02 - the caller
    must be told the true figure rather than handed silent extra risk."""
    lots, pct = suggest_lots("XAUUSD", price=3362.0, sl=3352.0, equity=500.0,
                             risk_pct=2.0, min_lot=0.02)
    assert lots == 0.02
    assert pct == pytest.approx(4.0, abs=0.05)          # double what was asked
    assert pct > 2.0


def test_suggest_lots_handles_degenerate_input():
    lots, pct = suggest_lots("XAUUSD", price=3362.0, sl=3362.0, equity=500.0,
                             risk_pct=2.0, min_lot=0.02)
    assert lots == 0.02 and pct == float("inf")
