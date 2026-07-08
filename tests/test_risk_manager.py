from dataclasses import replace
from datetime import date

from gold_bot.config import RiskConfig
from gold_bot.risk_manager import RiskManager

RISK_CFG = RiskConfig(
    risk_per_trade_pct=1.0,
    max_trades_per_day=2,
    max_daily_loss_pct=3.0,
    max_concurrent_trades=1,
    use_trailing_stop=True,
    trailing_atr_mult=1.2,
)


def make_manager(equity=10000.0):
    return RiskManager(cfg=RISK_CFG, equity=equity)


def test_position_size_risks_target_percent():
    mgr = make_manager(equity=10000.0)
    entry, stop = 2000.0, 1995.0  # 5.0 price distance
    lots = mgr.position_size_lots(entry, stop)
    risk_amount = lots * 5.0 * 100.0  # CONTRACT_SIZE=100
    assert abs(risk_amount - 100.0) < 1.0  # 1% of 10000


def test_position_size_zero_when_no_stop_distance():
    mgr = make_manager()
    assert mgr.position_size_lots(2000.0, 2000.0) == 0.0


def test_max_trades_per_day_blocks_further_trades():
    mgr = make_manager()
    today = date(2024, 1, 1)
    mgr.register_trade_opened(today)
    mgr.register_trade_opened(today)
    can_open, reason = mgr.can_open_trade(today, open_positions=0)
    assert can_open is False
    assert "max_trades_per_day" in reason


def test_depleted_equity_blocks_trading():
    mgr = make_manager(equity=0.0)
    today = date(2024, 1, 1)
    can_open, reason = mgr.can_open_trade(today, open_positions=0)
    assert can_open is False
    assert "equity depleted" in reason


def test_negative_equity_blocks_trading():
    mgr = make_manager(equity=-50.0)
    today = date(2024, 1, 1)
    can_open, reason = mgr.can_open_trade(today, open_positions=0)
    assert can_open is False
    assert "equity depleted" in reason


def test_daily_loss_limit_blocks_trading():
    mgr = make_manager(equity=10000.0)
    today = date(2024, 1, 1)
    mgr.register_fill_pnl(today, -350.0)  # 3.5% loss > 3.0% limit
    can_open, reason = mgr.can_open_trade(today, open_positions=0)
    assert can_open is False
    assert "max_daily_loss_pct" in reason


def test_daily_state_resets_on_new_day():
    mgr = make_manager()
    day1, day2 = date(2024, 1, 1), date(2024, 1, 2)
    mgr.register_trade_opened(day1)
    mgr.register_trade_opened(day1)
    can_open, _ = mgr.can_open_trade(day2, open_positions=0)
    assert can_open is True


def test_trailing_stop_moves_in_favor_of_long():
    mgr = make_manager()
    new_stop = mgr.trailing_stop(current_price=2010.0, atr_value=2.0, direction=1)
    assert new_stop == 2010.0 - 1.2 * 2.0


EQUITY_STEP_CFG = replace(
    RISK_CFG,
    sizing_mode="equity_step",
    base_equity=500.0,
    base_lot=0.01,
    lot_step=0.01,
    equity_step_usd=100.0,
    min_lot=0.01,
)


def test_equity_step_sizing_at_base_equity():
    mgr = RiskManager(cfg=EQUITY_STEP_CFG, equity=500.0)
    assert mgr.position_size_lots(2000.0, 1995.0) == 0.01


def test_equity_step_sizing_increases_with_profit():
    mgr = RiskManager(cfg=EQUITY_STEP_CFG, equity=700.0)  # +$200 -> +2 steps
    assert mgr.position_size_lots(2000.0, 1995.0) == 0.03


def test_equity_step_sizing_partial_step_rounds_down():
    mgr = RiskManager(cfg=EQUITY_STEP_CFG, equity=790.0)  # +$290 -> only 2 full steps
    assert mgr.position_size_lots(2000.0, 1995.0) == 0.03


def test_equity_step_sizing_decreases_with_loss():
    mgr = RiskManager(cfg=EQUITY_STEP_CFG, equity=300.0)  # -$200 -> -2 steps, still clamped to min_lot
    assert mgr.position_size_lots(2000.0, 1995.0) == 0.01


def test_equity_step_sizing_ignores_stop_distance():
    mgr = RiskManager(cfg=EQUITY_STEP_CFG, equity=600.0)
    lots_tight_stop = mgr.position_size_lots(2000.0, 1999.0)
    lots_wide_stop = mgr.position_size_lots(2000.0, 1950.0)
    assert lots_tight_stop == lots_wide_stop == 0.02


FIXED_CAPITAL_CFG = replace(RISK_CFG, sizing_mode="fixed_capital_percent_risk", base_equity=500.0, risk_per_trade_pct=1.0)


def test_fixed_capital_sizing_ignores_current_equity_after_wins():
    # Equity grew to $2000 from wins, but lot size must stay anchored to the
    # $500 base - this is the whole point of the mode: a winning streak
    # must never inflate the position size that a losing streak then hits.
    mgr = RiskManager(cfg=FIXED_CAPITAL_CFG, equity=2000.0)
    entry, stop = 2000.0, 1995.0  # 5.0 price distance
    lots = mgr.position_size_lots(entry, stop)
    risk_amount = lots * 5.0 * 100.0
    assert abs(risk_amount - 5.0) < 0.5  # 1% of the fixed $500 base, not $2000


def test_fixed_capital_sizing_ignores_current_equity_after_losses():
    mgr = RiskManager(cfg=FIXED_CAPITAL_CFG, equity=100.0)
    entry, stop = 2000.0, 1995.0
    lots = mgr.position_size_lots(entry, stop)
    risk_amount = lots * 5.0 * 100.0
    assert abs(risk_amount - 5.0) < 0.5  # still 1% of $500, not $100


def test_fixed_capital_daily_loss_limit_uses_base_equity_not_live_equity():
    mgr = RiskManager(cfg=FIXED_CAPITAL_CFG, equity=5000.0)  # equity ballooned from wins
    today = date(2024, 1, 1)
    mgr.register_fill_pnl(today, -20.0)  # 4% of the $500 base, over the 3% limit
    can_open, reason = mgr.can_open_trade(today, open_positions=0)
    assert can_open is False
    assert "max_daily_loss_pct" in reason


FIXED_LOT_CFG = replace(RISK_CFG, sizing_mode="fixed_lot", base_lot=0.05)


def test_fixed_lot_sizing_is_constant_regardless_of_equity_or_stop_distance():
    mgr = RiskManager(cfg=FIXED_LOT_CFG, equity=50000.0)
    assert mgr.position_size_lots(2000.0, 1995.0) == 0.05
    assert mgr.position_size_lots(2000.0, 1900.0) == 0.05
    mgr2 = RiskManager(cfg=FIXED_LOT_CFG, equity=10.0)
    assert mgr2.position_size_lots(2000.0, 1995.0) == 0.05


DCA_GRID_CFG = replace(
    RISK_CFG,
    sizing_mode="dca_grid",
    base_equity=1000.0,
    dca_step_price=2.0,
    dca_leg_risk_pct=2.0,
    dca_max_legs=30,
    dca_hard_stop_pct=15.0,
)


def test_dca_leg_lots_risks_leg_risk_pct_of_fixed_base_equity():
    mgr = RiskManager(cfg=DCA_GRID_CFG, equity=1000.0)
    lots = mgr.dca_leg_lots()
    risk_amount = lots * DCA_GRID_CFG.dca_step_price * 100.0  # CONTRACT_SIZE=100
    assert abs(risk_amount - 20.0) < 0.5  # 2% of $1000


def test_dca_leg_lots_ignores_live_equity():
    # Equity ballooned from prior profit, but leg size must stay anchored
    # to the fixed base_equity - same principle as fixed_capital_percent_risk.
    mgr = RiskManager(cfg=DCA_GRID_CFG, equity=5000.0)
    assert mgr.dca_leg_lots() == RiskManager(cfg=DCA_GRID_CFG, equity=1000.0).dca_leg_lots()


def test_dca_hard_stop_usd_is_pct_of_fixed_base_equity():
    mgr = RiskManager(cfg=DCA_GRID_CFG, equity=1000.0)
    assert abs(mgr.dca_hard_stop_usd() - 150.0) < 1e-9  # 15% of $1000
