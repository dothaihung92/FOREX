"""Tests for the AI trader.

The claim worth testing is not that the model is smart - it is that no
model, however wrong, can get an unprotected, oversized or runaway
position through the guard. The fuzz test at the bottom is that claim.
"""
import json
import math
import random
from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest

from gold_bot.ai_trader.advisors import (DEFAULT_MODEL, ClaudeAdvisor, RuleAdvisor,
                                         ScriptedAdvisor)
from gold_bot.ai_trader.broker import CONTRACT, SimBroker
from gold_bot.ai_trader.context import build_context, find_pivots, resample_complete
from gold_bot.ai_trader.engine import Engine, EngineConfig, replay, summarize
from gold_bot.ai_trader.guard import GuardConfig, GuardState, RiskGuard, floor_to_step
from gold_bot.ai_trader.schema import (DECISION_JSON_SCHEMA, Decision, decision_from_dict,
                                       decision_from_json)


# =============================================================== helpers
def review(g, d, **over):
    kw = dict(price=2000.0, atr=2.0, hour=10, bar_index=100, equity=1000.0,
              spread=0.25, position=None)
    kw.update(over)
    return g.review(d, **kw)


def buy(sl=1996.0, tp=2008.0, conf=0.8):
    return Decision("OPEN_BUY", conf, sl, tp)


def sell(sl=2004.0, tp=1992.0, conf=0.8):
    return Decision("OPEN_SELL", conf, sl, tp)


def long_pos(sl=1996.0, tp=2008.0, entry=2000.0):
    return {"side": "BUY", "lots": 0.02, "entry": entry, "sl": sl, "tp": tp}


def short_pos(sl=2004.0, tp=1992.0, entry=2000.0):
    return {"side": "SELL", "lots": 0.02, "entry": entry, "sl": sl, "tp": tp}


def synthetic(n=3600, seed=1, step=0.6):
    rng = np.random.default_rng(seed)
    idx = pd.date_range("2024-01-01", periods=n, freq="5min", tz="UTC")
    drift = np.sin(np.arange(n) / 400.0) * 0.15
    close = 2000 + np.cumsum(rng.normal(drift, step))
    open_ = np.concatenate([[2000.0], close[:-1]])
    wick = np.abs(rng.normal(0, step * 0.4, n))
    return pd.DataFrame({"open": open_, "high": np.maximum(open_, close) + wick,
                         "low": np.minimum(open_, close) - wick, "close": close}, index=idx)


# =============================================================== schema
def test_garbage_model_output_becomes_a_safe_hold():
    assert decision_from_json("not json").action == "HOLD"
    assert decision_from_dict({"action": "LEVERAGE_UP_10X"}).action == "HOLD"
    assert decision_from_dict("a string").action == "HOLD"
    d = decision_from_dict({"action": "open_buy", "stop_loss": float("nan"),
                            "take_profit": "abc", "confidence": 7})
    assert d.action == "OPEN_BUY"
    assert d.stop_loss == 0.0 and d.take_profit == 0.0      # NaN / text -> 0, i.e. "no stop"
    assert d.confidence == 1.0                               # clamped


def test_json_schema_is_strict_and_complete():
    props = set(DECISION_JSON_SCHEMA["properties"])
    assert props == set(DECISION_JSON_SCHEMA["required"])
    assert DECISION_JSON_SCHEMA["additionalProperties"] is False


# =============================================================== lot maths
def test_floor_to_step_avoids_the_float_trap():
    # 0.01 + 6*0.01 is 0.06999999999999999, so a naive floor shaves a whole step
    # (this is exactly how the uploaded EA lost layers 7 and 10).
    raw = 0.01 + 6 * 0.01
    assert math.floor(raw / 0.01) == 6                       # the bug being avoided
    assert floor_to_step(raw, 0.01) == pytest.approx(0.07)
    assert math.floor((8.7 / 87.0) / 0.01) == 9              # the same trap in risk sizing
    assert floor_to_step(8.7 / 87.0, 0.01) == pytest.approx(0.10)
    assert floor_to_step(0.0299, 0.01) == pytest.approx(0.02)   # and it still floors, never rounds up


def test_lot_is_sized_from_the_stop_and_never_exceeds_the_risk_budget():
    g = RiskGuard(GuardConfig(base_equity=1000.0, risk_per_trade_pct=1.0))
    r = review(g, buy())                                     # 4.00 stop, risk budget $10
    assert r.ok and r.action == "OPEN_BUY"
    assert r.lots == pytest.approx(0.02)
    assert r.risk_usd <= 10.0


def test_wider_stop_means_a_smaller_lot_at_the_same_risk():
    g = RiskGuard(GuardConfig(base_equity=5000.0, risk_per_trade_pct=1.0))
    narrow = review(g, buy(sl=1997.0, tp=2006.0))
    wide = review(g, buy(sl=1993.0, tp=2014.0))
    assert narrow.lots > wide.lots
    assert narrow.risk_usd <= 50.0 and wide.risk_usd <= 50.0


def test_a_trade_needing_less_than_the_minimum_lot_is_rejected_not_rounded_up():
    g = RiskGuard(GuardConfig(base_equity=100.0, risk_per_trade_pct=1.0))   # $1 budget
    r = review(g, buy())
    assert not r.ok and r.action == "HOLD"
    assert "not rounded up" in r.reason


# =============================================================== entry rules
@pytest.mark.parametrize("d,why", [
    (Decision("OPEN_BUY", 0.8, 0.0, 2008.0), "mandatory"),
    (Decision("OPEN_BUY", 0.8, 1996.0, 0.0), "take-profit"),
    (Decision("OPEN_BUY", 0.8, 2003.0, 2008.0), "wrong side"),       # stop above price on a buy
    (Decision("OPEN_SELL", 0.8, 1996.0, 1992.0), "wrong side"),
    (Decision("OPEN_BUY", 0.8, 1996.0, 1990.0), "wrong side"),       # target below price on a buy
    (buy(sl=1999.0), "tighter"),
    (buy(sl=1990.0, tp=2020.0), "wider"),
    (buy(tp=2005.0), "reward:risk"),
    (buy(conf=0.5), "confidence"),
])
def test_bad_entries_are_rejected(d, why):
    r = review(RiskGuard(), d)
    assert not r.ok and r.action == "HOLD"
    assert why in r.reason, r.reason


def test_good_entries_pass_both_ways():
    assert review(RiskGuard(), buy()).ok
    assert review(RiskGuard(), sell()).ok


@pytest.mark.parametrize("hour", [0, 3, 7, 20, 23])
def test_trading_window(hour):
    assert not review(RiskGuard(), buy(), hour=hour).ok


def test_wide_spread_blocks_entries():
    assert not review(RiskGuard(), buy(), spread=0.90).ok


def test_no_second_position_and_no_averaging_down():
    r = review(RiskGuard(), buy(), position=long_pos())
    assert not r.ok and "already open" in r.reason or "no averaging" in r.reason


def test_daily_trade_cap():
    g = RiskGuard()
    g.state.trades_today = g.cfg.max_trades_per_day
    assert "daily trade limit" in review(g, buy()).reason


def test_daily_loss_limit_stops_new_entries():
    g = RiskGuard(GuardConfig(base_equity=1000.0, max_daily_loss_pct=3.0))
    t = pd.Timestamp("2024-03-04 09:00", tz="UTC")
    g.on_bar(t, 100, 1000.0)
    g.on_bar(t + pd.Timedelta("5min"), 101, 965.0)           # -$35 > $30 limit
    assert "daily loss" in review(g, buy(), equity=965.0).reason


def test_a_new_day_resets_the_daily_counters():
    g = RiskGuard()
    g.on_bar(pd.Timestamp("2024-03-04 09:00", tz="UTC"), 1, 1000.0)
    g.on_trade_opened(); g.on_trade_opened()
    g.on_bar(pd.Timestamp("2024-03-05 09:00", tz="UTC"), 2, 1000.0)
    assert g.state.trades_today == 0


def test_consecutive_losses_trigger_a_cooldown_then_release():
    g = RiskGuard(GuardConfig(max_consecutive_losses=3, cooldown_bars=24))
    for _ in range(3):
        g.on_trade_closed(-8.0, 100)
    assert "cooling down" in review(g, buy(), bar_index=110).reason
    assert review(g, buy(), bar_index=125).ok


def test_a_win_resets_the_losing_streak():
    g = RiskGuard(GuardConfig(max_consecutive_losses=3))
    g.on_trade_closed(-1, 1); g.on_trade_closed(-1, 2); g.on_trade_closed(+5, 3)
    g.on_trade_closed(-1, 4); g.on_trade_closed(-1, 5)
    assert g.state.cooldown_until_bar == -1


def test_drawdown_halt_is_sticky_until_manually_reset():
    g = RiskGuard(GuardConfig(base_equity=1000.0, max_drawdown_halt_pct=15.0))
    t = pd.Timestamp("2024-03-04 09:00", tz="UTC")
    g.on_bar(t, 1, 840.0)
    assert g.state.halted
    g.on_bar(t + pd.Timedelta("5min"), 2, 1200.0)            # recovering does not un-halt it
    assert "halted" in review(g, buy(), equity=1200.0).reason
    g.reset_halt()
    assert review(g, buy(), equity=1200.0).ok


def test_kill_switch_file_blocks_entries(tmp_path):
    ks = tmp_path / "STOP"
    g = RiskGuard(GuardConfig(kill_switch_path=str(ks)))
    assert review(g, buy()).ok
    ks.write_text("x")
    assert "kill switch" in review(g, buy()).reason


# =============================================================== managing a position
def test_a_stop_can_be_tightened_but_never_widened_long():
    g = RiskGuard()
    widen = review(g, Decision("ADJUST", 1, stop_loss=1990.0), price=2003.0, position=long_pos())
    assert not widen.ok and "never widened" in widen.reason
    tighten = review(g, Decision("ADJUST", 1, stop_loss=2000.0), price=2003.0, position=long_pos())
    assert tighten.ok and tighten.action == "ADJUST" and tighten.stop_loss == 2000.0


def test_a_stop_can_be_tightened_but_never_widened_short():
    g = RiskGuard()
    widen = review(g, Decision("ADJUST", 1, stop_loss=2010.0), price=1997.0, position=short_pos())
    assert not widen.ok
    tighten = review(g, Decision("ADJUST", 1, stop_loss=2000.0), price=1997.0, position=short_pos())
    assert tighten.ok and tighten.stop_loss == 2000.0


def test_a_stop_cannot_be_moved_onto_or_past_price():
    r = review(RiskGuard(), Decision("ADJUST", 1, stop_loss=2003.0), price=2003.0, position=long_pos())
    assert not r.ok


def test_the_target_cannot_be_extended_until_the_stop_is_at_breakeven():
    g = RiskGuard()
    early = review(g, Decision("ADJUST", 1, take_profit=2015.0), price=2003.0, position=long_pos())
    assert not early.ok and "breakeven" in early.reason
    be = long_pos(sl=2000.0)
    later = review(g, Decision("ADJUST", 1, take_profit=2015.0), price=2003.0, position=be)
    assert later.ok and later.take_profit == 2015.0
    both = review(g, Decision("ADJUST", 1, stop_loss=2000.0, take_profit=2015.0),
                  price=2003.0, position=long_pos())
    assert both.ok and both.stop_loss == 2000.0 and both.take_profit == 2015.0


def test_close_is_always_allowed_with_a_position_and_refused_without():
    g = RiskGuard()
    assert review(g, Decision("CLOSE"), position=long_pos()).action == "CLOSE"
    assert not review(g, Decision("CLOSE")).ok


# =============================================================== broker
COSTS = 0.25 + 2 * 0.05


def test_round_trip_costs_are_charged():
    b = SimBroker(1000.0, spread=0.25, slippage=0.05)
    b.open("BUY", 0.10, 1990.0, 2010.0, 2000.0, 0, risk_usd=10)
    t = b.close_now(2000.0, 1)
    assert t.pnl == pytest.approx(-COSTS * 0.10 * CONTRACT)


def test_long_stop_loss_loses_stop_distance_plus_costs():
    b = SimBroker(1000.0, spread=0.25, slippage=0.05)
    p = b.open("BUY", 0.02, 1996.0, 2008.0, 2000.0, 0, risk_usd=8)
    t = b.on_bar(2000.0, 2000.5, 1990.0, 1995.0, 1)
    assert t.reason == "stop"
    assert t.pnl == pytest.approx(-((p.entry - 1996.0) + 0.05) * 0.02 * CONTRACT, abs=1e-6)


def test_short_target_pays_costs_too():
    b = SimBroker(1000.0, spread=0.25, slippage=0.05)
    b.open("SELL", 0.02, 2004.0, 1992.0, 2000.0, 0, risk_usd=8)
    t = b.on_bar(2000.0, 2000.2, 1990.0, 1991.0, 1)
    assert t.reason == "target" and 0 < t.pnl < 8 * 2


def test_a_bar_touching_both_stop_and_target_books_the_loss():
    b = SimBroker(1000.0, spread=0.0, slippage=0.0)
    b.open("BUY", 0.02, 1996.0, 2008.0, 2000.0, 0, risk_usd=8)
    t = b.on_bar(2000.0, 2010.0, 1990.0, 2005.0, 1)
    assert t.reason == "stop" and t.pnl < 0


def test_gap_through_the_stop_fills_at_the_worse_price():
    b = SimBroker(1000.0, spread=0.0, slippage=0.0)
    b.open("BUY", 0.02, 1996.0, 2008.0, 2000.0, 0, risk_usd=8)
    t = b.on_bar(1990.0, 1991.0, 1989.0, 1990.5, 1)
    assert t.reason == "stop (gap)" and t.exit == pytest.approx(1990.0)
    assert t.pnl < -0.02 * CONTRACT * 4                      # worse than the 4.00 planned


# =============================================================== context
def test_a_pivot_is_not_known_until_enough_bars_have_confirmed_it():
    high = np.array([1, 2, 3, 4, 9, 4, 3, 2, 1, 1, 1], dtype=float)
    low = high - 0.5
    k = 4
    hi_early, _ = find_pivots(high[: k + 3], low[: k + 3])      # only 2 bars after the peak
    hi_late, _ = find_pivots(high[: k + 4], low[: k + 4])       # 3 bars after: confirmed
    assert k not in [j for j, _ in hi_early]
    assert k in [j for j, _ in hi_late]


def test_incomplete_higher_timeframe_bars_are_dropped():
    idx = pd.date_range("2024-03-04 00:00", "2024-03-04 10:25", freq="5min", tz="UTC")
    df = pd.DataFrame({"open": 1.0, "high": 2.0, "low": 0.5, "close": 1.5}, index=idx)
    h1 = resample_complete(df, "1h", 12)
    assert h1.index[-1] == pd.Timestamp("2024-03-04 09:00", tz="UTC")    # 10:00 is only half formed


def test_context_needs_enough_history_and_renders():
    df = synthetic(3000)
    assert build_context(df.iloc[:100], bar_index=99, spread=0.25, position=None, guard={}, limits={}) is None
    ctx = build_context(df, bar_index=2999, spread=0.25, position=None,
                        guard={"trades_today": 0, "max_trades_per_day": 3, "consecutive_losses": 0, "day_pnl": 0},
                        limits={"sl_min": 2, "sl_max": 8, "min_rr": 1.5, "min_confidence": 0.6})
    text = ctx.to_prompt()
    assert "[H1]" in text and "[M5]" in text and "NO OPEN POSITION" in text and "HARD LIMITS" in text


# =============================================================== Claude advisor
class FakeMessages:
    def __init__(self, outcome):
        self.outcome, self.calls = outcome, []

    def create(self, **kw):
        self.calls.append(kw)
        if isinstance(self.outcome, Exception):
            raise self.outcome
        if callable(self.outcome):
            return self.outcome(kw)
        return self.outcome


def fake_client(outcome, beta_outcome=None):
    beta = FakeMessages(beta_outcome if beta_outcome is not None else outcome)
    return SimpleNamespace(messages=FakeMessages(outcome), beta=SimpleNamespace(messages=beta))


def reply(text, stop="end_turn"):
    return SimpleNamespace(content=[SimpleNamespace(type="thinking", text=""),
                                    SimpleNamespace(type="text", text=text)],
                           stop_reason=stop, model=DEFAULT_MODEL, stop_details=None,
                           usage=SimpleNamespace(input_tokens=1500, output_tokens=400))


@pytest.fixture
def ctx():
    return build_context(synthetic(3000), bar_index=2999, spread=0.25, position=None,
                         guard={"trades_today": 0, "max_trades_per_day": 3, "consecutive_losses": 0,
                                "day_pnl": 0},
                         limits={"sl_min": 2, "sl_max": 8, "min_rr": 1.5, "min_confidence": 0.6})


GOOD = json.dumps({"action": "OPEN_BUY", "confidence": 0.7, "stop_loss": 1996.0, "take_profit": 2008.0,
                   "trend": "up", "reversal_risk": "low", "key_support": 1995.0,
                   "key_resistance": 2010.0, "reasoning": "xu hướng tăng"})


def test_claude_advisor_parses_a_decision_and_tracks_usage(ctx):
    c = fake_client(reply(GOOD))
    a = ClaudeAdvisor(client=c)
    d = a.decide(ctx)
    assert d.action == "OPEN_BUY" and d.stop_loss == 1996.0
    assert a.calls == 1 and a.input_tokens == 1500 and a.output_tokens == 400


def test_the_request_avoids_everything_the_model_rejects(ctx):
    c = fake_client(reply(GOOD))
    ClaudeAdvisor(client=c).decide(ctx)
    kw = c.beta.messages.calls[0]
    assert kw["model"] == "claude-opus-5-5"
    for forbidden in ("temperature", "top_p", "top_k", "tool_choice", "budget_tokens"):
        assert forbidden not in kw
    assert "thinking" not in kw                                  # always on; steered by effort
    assert kw["messages"][-1]["role"] == "user"                  # no assistant prefill
    assert kw["output_config"]["format"]["type"] == "json_schema"
    assert kw["output_config"]["effort"] == "medium"
    assert kw["fallbacks"] == "default" and "server-side-fallback-2026-07-01" in kw["betas"]


@pytest.mark.parametrize("bad", [reply(GOOD, stop="refusal"), reply(GOOD, stop="max_tokens"),
                                 reply("{broken json")])
def test_unusable_replies_degrade_to_hold(ctx, bad):
    assert ClaudeAdvisor(client=fake_client(bad)).decide(ctx).action == "HOLD"


def test_an_api_failure_degrades_to_hold_and_is_counted(ctx):
    a = ClaudeAdvisor(client=fake_client(RuntimeError("network down")))
    d = a.decide(ctx)
    assert d.action == "HOLD" and "unavailable" in d.reasoning and a.failures == 1


def test_if_only_fallbacks_are_rejected_it_retries_without_them(ctx):
    beta_err = RuntimeError("invalid request: fallbacks is not supported")
    c = fake_client(reply(GOOD), beta_outcome=beta_err)
    a = ClaudeAdvisor(client=c)
    assert a.decide(ctx).action == "OPEN_BUY"
    assert a.use_fallbacks is False and len(c.messages.calls) == 1


# =============================================================== engine
def make_engine(advisor, tmp_path=None, **cfg_over):
    broker = SimBroker(1000.0)
    guard = RiskGuard(GuardConfig(base_equity=1000.0, **cfg_over.pop("guard", {})))
    ecfg = EngineConfig(lookback=1500, log_path=str(tmp_path / "log.jsonl") if tmp_path else "",
                        state_path=str(tmp_path / "state.json") if tmp_path else "", **cfg_over)
    return Engine(advisor, broker, guard, ecfg), broker, guard


def eager_buyer(ctx):
    return Decision("OPEN_BUY", 0.9, ctx.price - 2.5 * ctx.atr, ctx.price + 5 * ctx.atr)


def test_engine_trades_and_every_position_carries_a_stop(tmp_path):
    df = synthetic(3600, seed=3)
    eng, broker, guard = make_engine(ScriptedAdvisor(eager_buyer), tmp_path)
    replay(df, eng, 1600, len(df))
    assert eng.stats.executed["OPEN_BUY"] >= 3
    for t in broker.closed:
        assert t.risk_usd > 0
        assert t.pnl >= -1.6 * t.risk_usd, (t.pnl, t.risk_usd)   # costs + slippage, never a blow-through
    log = [json.loads(l) for l in (tmp_path / "log.jsonl").read_text().splitlines()]
    assert log and {"proposed", "approved", "executed", "guard"} <= set(log[0])


def test_no_model_call_is_spent_when_entries_are_blocked():
    df = synthetic(3600, seed=4)
    adv = ScriptedAdvisor(eager_buyer)
    eng, _, _ = make_engine(adv, guard={"allowed_hours_utc": (10,), "excluded_hours_utc": ()})
    replay(df, eng, 1600, len(df))
    assert adv.seen
    # Flat, the model is only consulted inside the window. With a position open it is
    # consulted at any hour on purpose - an open trade still has to be managed.
    assert all(c.hour == 10 for c in adv.seen if c.position is None)
    assert eng.stats.skipped_blocked > 0


def test_an_advisor_that_raises_does_not_stop_the_loop():
    def boom(ctx):
        raise ValueError("model exploded")
    eng, broker, _ = make_engine(ScriptedAdvisor(boom))
    replay(synthetic(2400, seed=5), eng, 1600, 2400)
    assert broker.closed == [] and eng.stats.consults > 0


def test_guard_state_survives_a_restart(tmp_path):
    df = synthetic(3600, seed=6)
    eng, _, guard = make_engine(ScriptedAdvisor(eager_buyer), tmp_path)
    replay(df, eng, 1600, 2600)
    opened = guard.state.trades_today
    eng2, _, guard2 = make_engine(ScriptedAdvisor([]), tmp_path)
    assert guard2.state.day == guard.state.day and guard2.state.trades_today == opened


def test_rule_advisor_runs_end_to_end():
    df = synthetic(3600, seed=7)
    eng, broker, _ = make_engine(RuleAdvisor())
    replay(df, eng, 1600, len(df))
    s = summarize(broker, 1000.0)
    assert s["trades"] == 0 or s["worst_loss_usd"] >= -16.0


# ---------------------------------------------------------------- the safety claim
class ChaosAdvisor:
    """A model having the worst possible day: random actions, random and
    often absurd prices, stops on the wrong side, missing stops, huge
    confidence, attempts to widen stops and to pyramid."""

    ACTIONS = ["HOLD", "OPEN_BUY", "OPEN_SELL", "ADJUST", "CLOSE"]

    def __init__(self, seed):
        self.rng = random.Random(seed)

    def decide(self, ctx):
        r = self.rng
        p = ctx.price
        pick = lambda: r.choice([0.0, p + r.uniform(-30, 30), p + r.uniform(-6, 6),   # noqa: E731
                                 p - 2 * ctx.atr, p + 2 * ctx.atr, float("nan"), -5.0, 1e9])
        return Decision(r.choice(self.ACTIONS), r.choice([0.0, 0.5, 0.99, 1.0, 5.0]),
                        pick(), pick())


@pytest.mark.parametrize("seed", [11, 12, 13])
def test_no_advisor_can_get_an_unprotected_or_oversized_position_through(seed):
    df = synthetic(3600, seed=seed)
    broker = SimBroker(1000.0)
    opened = []
    real_open = broker.open

    def spy(side, lots, sl, tp, mid, bar, time=None, risk_usd=0.0):
        assert broker.position is None, "a second position was opened"
        sign = 1 if side == "BUY" else -1
        assert sl > 0 and tp > 0, "position opened without a stop or target"
        assert sign * (mid - sl) > 0, "stop on the wrong side of price"
        assert sign * (tp - mid) > 0, "target on the wrong side of price"
        assert lots >= 0.01 and risk_usd <= 10.0 + 1e-6, "risk budget exceeded"
        opened.append((side, lots, sl, tp, bar))
        return real_open(side, lots, sl, tp, mid, bar, time, risk_usd)

    broker.open = spy
    real_modify = broker.modify

    def modify_spy(sl, tp):
        p = broker.position
        assert p.sign * (sl - p.sl) >= 0, "a stop was loosened"
        real_modify(sl, tp)

    broker.modify = modify_spy
    guard = RiskGuard(GuardConfig(base_equity=1000.0))
    eng = Engine(ChaosAdvisor(seed), broker, guard, EngineConfig(lookback=1500))
    replay(df, eng, 1600, len(df))

    per_day = pd.Series([df.index[b].date() for *_, b in opened]).value_counts()
    assert per_day.empty or per_day.max() <= guard.cfg.max_trades_per_day
    for t in broker.closed:
        assert t.pnl >= -2.0 * t.risk_usd, (t.pnl, t.risk_usd)
    assert eng.stats.consults > 50                                 # the test actually exercised it
    assert sum(eng.stats.rejected.values()) > 10                   # and the guard actually refused things
