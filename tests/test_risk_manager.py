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
