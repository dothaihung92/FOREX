"""Correctness tests for the two-sided grid simulator.

The first version of this module closed winning baskets at the bar's
extreme instead of at the price where the target was actually reached,
which inflated a losing design into a fictional six-figure profit. These
tests pin the arithmetic against hand calculations so that class of error
cannot come back unnoticed.
"""
import numpy as np
import pandas as pd
import pytest

from gold_bot.two_sided_grid import (
    CONTRACT,
    GridParams,
    TwoSidedGrid,
    basket_pnl,
    opening_cost,
    run_grid,
    total_ladder_lots,
)

FREE = GridParams(spread=0.0, slippage=0.0)          # frictionless, for clean maths


def bars(prices, freq="5min"):
    """Build an OHLC frame that trades exactly through `prices`."""
    idx = pd.date_range("2024-01-01", periods=len(prices), freq=freq, tz="UTC")
    p = np.asarray(prices, dtype=float)
    return pd.DataFrame({"open": p, "high": p, "low": p, "close": p}, index=idx)


def ohlc_bars(rows, freq="5min"):
    """Build a frame from explicit (open, high, low, close) tuples."""
    idx = pd.date_range("2024-01-01", periods=len(rows), freq=freq, tz="UTC")
    a = np.asarray(rows, dtype=float)
    return pd.DataFrame({"open": a[:, 0], "high": a[:, 1],
                         "low": a[:, 2], "close": a[:, 3]}, index=idx)


def test_ladder_lots_match_the_specified_progression():
    # 0.01 + 0.02 + ... + 0.10
    assert total_ladder_lots(GridParams()) == pytest.approx(0.55)
    assert total_ladder_lots(GridParams(levels=5)) == pytest.approx(0.15)
    assert total_ladder_lots(GridParams(lot_increment=0.0)) == pytest.approx(0.10)


def test_opening_cost_exceeds_the_target_by_design():
    """The headline problem, asserted rather than asserted-in-prose."""
    p = GridParams(spread=0.25, slippage=0.05)
    cost = opening_cost(p)
    # 0.55 lots/side x 2 sides x (0.25 + 0.10) x 100 oz
    assert cost == pytest.approx(0.55 * 2 * 0.35 * CONTRACT)
    assert cost > p.tp_usd * 20


def test_both_sides_open_at_the_start():
    g = TwoSidedGrid(FREE, 2000.0, 0)
    assert len(g.legs) == 2
    assert {leg.direction for leg in g.legs} == {1, -1}
    assert g.lots_per_side() == (0.01, 0.01)


def test_hedged_opening_basket_is_flat():
    """Equal and opposite at the same price nets to zero exposure."""
    g = TwoSidedGrid(FREE, 2000.0, 0)
    assert g.net_exposure() == pytest.approx(0.0)
    assert basket_pnl(g.legs, 2050.0) == pytest.approx(0.0)
    assert basket_pnl(g.legs, 1950.0) == pytest.approx(0.0)


def test_falling_price_fills_buy_levels_only():
    g = TwoSidedGrid(FREE, 2000.0, 0)
    g.trigger(1997.0)                      # 3 dollars down -> levels 1,2,3 on the buy side
    buy, sell = g.lots_per_side()
    assert buy == pytest.approx(0.01 + 0.02 + 0.03 + 0.04)
    assert sell == pytest.approx(0.01)


def test_rising_price_fills_sell_levels_only():
    g = TwoSidedGrid(FREE, 2000.0, 0)
    g.trigger(2002.0)
    buy, sell = g.lots_per_side()
    assert sell == pytest.approx(0.01 + 0.02 + 0.03)
    assert buy == pytest.approx(0.01)


def test_ladder_never_exceeds_the_configured_levels():
    g = TwoSidedGrid(FREE, 2000.0, 0)
    g.trigger(1000.0)                      # far beyond the bottom of the ladder
    buy, _ = g.lots_per_side()
    assert buy == pytest.approx(total_ladder_lots(FREE))
    assert g.pending_buy == {}


def test_price_for_pnl_is_exact():
    g = TwoSidedGrid(FREE, 2000.0, 0)
    g.trigger(1998.0)                      # net long after filling buy levels
    target = g.price_for_pnl(1.5)
    assert target is not None
    assert basket_pnl(g.legs, target) == pytest.approx(1.5)


def test_price_for_pnl_is_undefined_when_perfectly_hedged():
    g = TwoSidedGrid(FREE, 2000.0, 0)
    assert g.price_for_pnl(1.5) is None


def test_worst_floating_is_tracked_not_forgotten():
    g = TwoSidedGrid(FREE, 2000.0, 0)
    g.trigger(1995.0)
    g.mark(1990.0)
    g.mark(2100.0)                         # a later good mark must not erase the low
    assert g.worst_floating < 0


def test_close_all_charges_costs_on_both_ends():
    p = GridParams(spread=0.25, slippage=0.05)
    g = TwoSidedGrid(p, 2000.0, 0)
    # Flat basket, price unchanged: the only outcome is the round-trip cost.
    realised = g.close_all(2000.0)
    expected = -2 * 0.01 * CONTRACT * (0.25 + 2 * 0.05)
    assert realised == pytest.approx(expected)


def test_winning_cycle_banks_the_target_not_the_bar_extreme():
    """The regression that matters: a basket that crosses $1.50 must book
    about $1.50, not whatever the bar's extreme happened to be worth.

    Bar 1 dips to 1997, filling buy levels 1-3. Bar 2 recovers to 1999,
    where the basket is worth about $11 at the high - but it passed $1.50
    on the way there, and that is where it must be booked.
    """
    p = GridParams(spread=0.0, slippage=0.0, tp_usd=1.5)
    r = run_grid(ohlc_bars([
        (2000.0, 2000.0, 2000.0, 2000.0),
        (2000.0, 2000.0, 1997.0, 1997.0),
        (1997.0, 1999.0, 1997.0, 1999.0),
    ]), p, start_balance=10_000.0)
    assert r["n_tp"] >= 1
    first = r["cycles"][0]
    assert first.closed_reason == "take_profit"
    assert first.realised_usd == pytest.approx(1.5, abs=0.01)


def test_a_big_move_either_way_loses_because_the_far_side_keeps_filling():
    """The structural flaw, pinned as a test.

    A violent move does not rescue the basket - it fills the whole ladder
    on the losing side. Up or down makes no difference, which is what
    "no stop-loss on either end" really buys you.
    """
    p = GridParams(spread=0.0, slippage=0.0, tp_usd=1.5)
    for direction in (+1, -1):
        rows = [(2000.0, 2000.0, 2000.0, 2000.0)]
        price = 2000.0
        for _ in range(60):
            nxt = price + direction * 10.0
            rows.append((price, max(price, nxt), min(price, nxt), nxt))
            price = nxt
        r = run_grid(ohlc_bars(rows), p, start_balance=10_000_000.0)
        assert r["worst_floating_usd"] < -5_000, direction


def test_no_stop_loss_means_losses_are_unbounded():
    """A one-way move with no reversal must never close a basket."""
    p = GridParams(spread=0.0, slippage=0.0, tp_usd=1.5)
    prices = [2000.0 - i for i in range(300)]        # straight down, no bounce
    r = run_grid(bars(prices), p, start_balance=10_000_000.0)
    assert r["n_tp"] == 0
    # 0.55 lots x 55 oz... the ladder is full and every further dollar costs $55
    assert r["max_lots_one_side"] == pytest.approx(0.55)
    assert r["worst_floating_usd"] < -10_000


def test_account_is_wiped_out_when_equity_hits_zero():
    p = GridParams(spread=0.0, slippage=0.0, tp_usd=1.5)
    prices = [2000.0 - i for i in range(300)]
    r = run_grid(bars(prices), p, start_balance=500.0)
    assert r["ruined_at"] is not None
    assert r["balance"] <= 0


def test_basket_loss_cap_prevents_ruin_on_the_same_data():
    p = GridParams(spread=0.0, slippage=0.0, tp_usd=1.5, max_basket_loss_usd=100.0)
    prices = [2000.0 - i for i in range(300)]
    r = run_grid(bars(prices), p, start_balance=10_000.0)
    assert r["ruined_at"] is None
    assert any(c.closed_reason == "guard_stop" for c in r["cycles"])


def test_flat_market_bleeds_cost_and_never_reaches_target():
    """Perfectly hedged and going nowhere: the spread is the only outcome."""
    p = GridParams(spread=0.25, slippage=0.05, tp_usd=1.5)
    r = run_grid(bars([2000.0] * 200), p, start_balance=10_000.0)
    assert r["n_tp"] == 0
    assert r["net"] <= 0
