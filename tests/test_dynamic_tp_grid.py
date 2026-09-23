"""Verify the Python port matches GoldBot_DynamicTP_FastClose.mq5.

A backtest is only worth reading if the port is faithful, so the EA's
arithmetic is pinned here against hand calculations before any conclusion
is drawn from it.
"""
import numpy as np
import pandas as pd
import pytest

from gold_bot.dynamic_tp_grid import CONTRACT, EaParams, EaState, run_ea

FREE = EaParams(spread=0.0, slippage=0.0)


def ohlc(rows, freq="5min"):
    idx = pd.date_range("2024-01-01", periods=len(rows), freq=freq, tz="UTC")
    a = np.asarray(rows, dtype=float)
    return pd.DataFrame({"open": a[:, 0], "high": a[:, 1],
                         "low": a[:, 2], "close": a[:, 3]}, index=idx)


def ramp(start, end, bars):
    """A frame that walks steadily from `start` to `end`."""
    prices = np.linspace(start, end, bars)
    rows = [(prices[0], prices[0], prices[0], prices[0])]
    for a, b in zip(prices[:-1], prices[1:]):
        rows.append((a, max(a, b), min(a, b), b))
    return ohlc(rows)


# ---------------------------------------------------- lot progression
def test_next_lot_ramps_with_basket_size():
    """nextLot = InitialLot + positionCount * LotStep."""
    st = EaState(FREE)
    assert st.next_lot() == pytest.approx(0.01)
    for expected in (0.02, 0.03, 0.04):
        st._add(True, 0.01, 2000.0)
        assert st.next_lot() == pytest.approx(expected)


def test_lot_formula_silently_drops_a_step_on_floating_point():
    """Real defect in the EA, reproduced here rather than corrected.

    `MathFloor(rawLot / lotStep) * lotStep` is exact only when the division
    lands cleanly. At counts 6 and 9, 0.07/0.01 and 0.10/0.01 evaluate to
    6.9999... and 9.9999... in IEEE 754, so MathFloor drops a step and the
    EA opens 0.06 and 0.09 instead. MQL5 uses the same doubles, so this
    happens in the terminal too.
    """
    st = EaState(FREE)
    lots = []
    for _ in range(20):
        lot = st.next_lot()
        lots.append(lot)
        st._add(True, lot, 2000.0)
    assert lots[6] == pytest.approx(0.06)      # intended 0.07
    assert lots[9] == pytest.approx(0.09)      # intended 0.10
    # The intended total is 2.10; the bug makes it 2.08.
    assert st.volume() == pytest.approx(2.08)


# ------------------------------------------------------- dynamic TP
def test_required_tp_is_the_larger_of_floor_and_per_lot():
    st = EaState(FREE)
    st._add(True, 0.01, 2000.0)
    assert st.required_tp() == pytest.approx(2.0)          # floor wins
    st2 = EaState(FREE)
    for _ in range(20):
        st2._add(True, st2.next_lot(), 2000.0)
    assert st2.required_tp() == pytest.approx(20.8)        # 2.08 lots * $10


def test_dynamic_tp_target_is_tiny_against_the_exposure_it_carries():
    """The structural point: a full basket needs $21 while a $1 adverse
    move against 2.10 lots costs $210."""
    st = EaState(FREE)
    for _ in range(20):
        st._add(True, st.next_lot(), 2000.0)
    dollar_move = st.volume() * CONTRACT
    assert dollar_move == pytest.approx(208.0)
    assert st.required_tp() * 10 == pytest.approx(dollar_move)


# ------------------------------------------------ the empty-side bug
def test_empty_side_opens_instantly_because_last_price_is_zero():
    """`lastSellPrice == 0 || distance >= step` short-circuits, so the
    first trade on a fresh side skips the distance requirement entirely."""
    st = EaState(FREE)
    assert st.last_price(False) == 0.0
    st._add(True, 0.01, 2000.0)
    assert st.last_price(False) == 0.0          # still no sells
    assert st.last_price(True) == pytest.approx(2000.0)


def test_focus_mode_flips_to_the_flat_side_when_the_open_side_is_losing():
    """With no sells open, sellProfit is 0. A losing buy side therefore
    scores below it and the EA opens a SELL - immediately, per the bug
    above. This is how the basket becomes hedged."""
    st = EaState(FREE)
    for _ in range(6):
        st._add(True, st.next_lot(), 2000.0)
    buy_p, sell_p = st.side_profit(1990.0, 1990.0)    # buys under water
    assert buy_p < 0
    assert sell_p == 0.0
    assert not (buy_p >= sell_p)                      # -> EA takes the SELL branch


# ---------------------------------------------- breakout grid phase
def test_below_trigger_count_it_pyramids_in_both_directions():
    """Under InpTriggerPosCount the EA buys above the highest entry and
    sells below the lowest, so a swinging market opens both sides."""
    p = EaParams(spread=0.0, slippage=0.0, trigger_pos_count=6, max_layers=20)
    # A trend to seed the EMAs, then two-way swings wide enough to trip
    # both the "above the highest" and "below the lowest" rules.
    prices = list(np.linspace(1990.0, 2010.0, 150))
    prices += list(2000.0 + 12.0 * np.sin(2 * np.pi * np.arange(300) / 25))
    rows = [(prices[0],) * 4]
    for a, b in zip(prices[:-1], prices[1:]):
        rows.append((a, max(a, b), min(a, b), b))
    r = run_ea(ohlc(rows), p, start_balance=1_000_000.0)
    assert r["max_positions"] >= 5


# ------------------------------------------------------ no stop-loss
def test_a_balanced_hedged_basket_can_never_reach_its_target():
    """The EA's core trap, stated as arithmetic.

    The target scales with TOTAL lots, but a hedged basket's profit is
    driven by NET exposure. Perfectly hedged means zero net exposure and a
    non-zero target, so no price anywhere closes the basket. It is frozen
    for good, paying swap the whole time.
    """
    st = EaState(FREE)
    st._add(True, 0.50, 2000.0)
    st._add(False, 0.50, 2000.0)
    assert st.required_tp() == pytest.approx(10.0)
    for price in (1000.0, 2000.0, 3000.0, 5000.0):
        assert st.profit(price, price) == pytest.approx(0.0)


def test_target_grows_faster_than_net_exposure_as_the_basket_fills():
    """Each added layer raises the target by lots x $10 while a hedged
    basket's net exposure barely moves - the exit walks away from you."""
    st = EaState(FREE)
    needed = []
    for k in range(1, 21):
        st._add(k % 2 == 0, st.next_lot(), 2000.0)
        net = abs(sum((1 if q.is_buy else -1) * q.lots for q in st.positions)) * CONTRACT
        if net > 0:
            needed.append(st.required_tp() / net)
    assert needed[-1] > needed[3]


def test_a_basket_stuck_in_chop_never_closes_and_bleeds():
    """Small oscillation builds a hedged basket that never reaches target."""
    p = EaParams(spread=0.25, slippage=0.05)
    t = np.arange(1200)
    prices = 2000 + 3 * np.sin(2 * np.pi * t / 20)
    rows = [(prices[0],) * 4]
    for a, b in zip(prices[:-1], prices[1:]):
        rows.append((a, max(a, b), min(a, b), b))
    r = run_ea(ohlc(rows), p, start_balance=100_000.0)
    # 1,200 bars of oscillation produce almost no completed baskets, and
    # the account still ends down: the target is unreachable for most of
    # the run while costs accrue the whole time.
    assert r["n_cycles"] <= 10
    assert r["net"] < 0


# ------------------------------------------------------------ costs
def test_closing_immediately_costs_the_full_round_trip():
    p = EaParams(spread=0.25, slippage=0.05)
    st = EaState(p)
    st._add(True, 0.01, 2000.0)
    realised = st.close_all(2000.0 - 0.125, 2000.0 + 0.125)
    # entry at ask+slip, exit at bid-slip -> spread + 2x slippage
    assert realised == pytest.approx(-(0.25 + 2 * 0.05) * CONTRACT * 0.01)


def test_buy_is_marked_at_bid_and_sell_at_ask():
    """The spread is paid on the mark, not only on the fill - this is why
    a freshly opened hedged basket already shows a loss."""
    p = EaParams(spread=0.25, slippage=0.0)
    st = EaState(p)
    st._add(True, 0.01, 2000.0)
    st._add(False, 0.01, 2000.0)
    assert st.profit(1999.875, 2000.125) == pytest.approx(-0.25 * CONTRACT * 0.02)
