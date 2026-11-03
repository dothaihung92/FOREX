"""Run the AI trader: replay on history, paper-trade on live data, or trade live.

    # 1. Free: rules-only control on history (no API calls)
    python scripts/run_ai_trader.py replay --advisor rules --start 2024-01-01 --end 2024-06-30

    # 2. Costs money: Claude on a SMALL slice, hard-capped
    python scripts/run_ai_trader.py replay --advisor claude --start 2025-06-01 --end 2025-06-14 --max-consults 150

    # 3. Live data, virtual orders (Windows + MT5 terminal logged in to a DEMO account)
    python scripts/run_ai_trader.py paper --advisor claude

    # 4. Real orders - demo account only unless you add --allow-real-account
    python scripts/run_ai_trader.py live --advisor claude

Credentials come from the environment, never the command line:
    ANTHROPIC_API_KEY, MT5_LOGIN, MT5_PASSWORD, MT5_SERVER
"""
from __future__ import annotations

import argparse
import os
import sys
import time
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from gold_bot.ai_trader.advisors import DEFAULT_MODEL, ClaudeAdvisor, RuleAdvisor  # noqa: E402
from gold_bot.ai_trader.broker import MT5Broker, SimBroker  # noqa: E402
from gold_bot.ai_trader.engine import Engine, EngineConfig, replay, summarize  # noqa: E402
from gold_bot.ai_trader.guard import GuardConfig, RiskGuard  # noqa: E402

# Approximate Claude Opus 5.5 list prices, USD per million tokens.
PRICE_IN, PRICE_OUT = 4.0, 20.0


def build_guard(a) -> RiskGuard:
    return RiskGuard(GuardConfig(
        base_equity=a.base_equity, risk_per_trade_pct=a.risk_pct,
        max_trades_per_day=a.max_trades_per_day, max_daily_loss_pct=a.max_daily_loss_pct,
        kill_switch_path=a.kill_switch))


def build_advisor(a):
    if a.advisor == "rules":
        return RuleAdvisor()
    if not os.environ.get("ANTHROPIC_API_KEY"):
        sys.exit("ANTHROPIC_API_KEY is not set. Export it, or use --advisor rules.")
    return ClaudeAdvisor(model=a.model, effort=a.effort)


def report(engine, advisor, broker, start_balance):
    s = summarize(broker, start_balance)
    print("\n================ RESULT ================")
    for k, v in s.items():
        print(f"  {k:<16} {v}")
    st = engine.stats
    print(f"\n  advisor consulted : {st.consults} times ({st.skipped_blocked} skipped, entries blocked)")
    print(f"  proposed          : {dict(st.actions)}")
    print(f"  reached broker    : {dict(st.executed)}")
    if st.rejected:
        print("  guard refused     :")
        for why, n in st.rejected.most_common(8):
            print(f"      {n:>5}  {why}")
    if isinstance(advisor, ClaudeAdvisor):
        cost = advisor.input_tokens / 1e6 * PRICE_IN + advisor.output_tokens / 1e6 * PRICE_OUT
        print(f"\n  API calls {advisor.calls} (failed {advisor.failures}) | tokens in {advisor.input_tokens:,} "
              f"out {advisor.output_tokens:,} | approx cost ${cost:.2f}")


# ------------------------------------------------------------------------ replay
def cmd_replay(a):
    df = pd.read_csv(a.data)
    df["time"] = pd.to_datetime(df["time"], utc=True)
    df = df.set_index("time").sort_index()
    lo = df.index.searchsorted(pd.Timestamp(a.start, tz="UTC")) if a.start else 0
    hi = df.index.searchsorted(pd.Timestamp(a.end, tz="UTC")) if a.end else len(df)
    lo = max(lo, 2000)                                  # warm-up for the higher timeframes
    print(f"replaying {df.index[lo]:%Y-%m-%d} -> {df.index[min(hi, len(df)) - 1]:%Y-%m-%d} "
          f"({hi - lo:,} M5 bars) | advisor={a.advisor}")

    advisor = build_advisor(a)
    broker = SimBroker(a.base_equity, spread=a.spread, slippage=a.slippage)
    log = Path(a.log_dir); log.mkdir(parents=True, exist_ok=True)
    engine = Engine(advisor, broker, build_guard(a),
                    EngineConfig(log_path=str(log / "replay_decisions.jsonl")))
    (log / "replay_decisions.jsonl").write_text("")

    stop = None
    if isinstance(advisor, ClaudeAdvisor):
        cap = a.max_consults
        est = cap * (1500 * PRICE_IN + 600 * PRICE_OUT) / 1e6
        print(f"Claude advisor: hard cap {cap} calls, roughly ${est:.2f} at ~1.5k tokens in / ~600 out per call.")
        stop = lambda: advisor.calls + advisor.failures >= cap       # noqa: E731
    last = replay(df, engine, lo, min(hi, len(df)), should_stop=stop)
    if stop and stop():
        print(f"stopped early at {df.index[last]:%Y-%m-%d %H:%M} - call cap reached")
    report(engine, advisor, broker, a.base_equity)
    print(f"\n  audit log: {log / 'replay_decisions.jsonl'}")


# ------------------------------------------------------------------ paper / live
def cmd_live(a, live: bool):
    from gold_bot.mt5_connector import MT5Connector, MT5Unavailable
    try:
        conn = MT5Connector(int(os.environ.get("MT5_LOGIN", "0") or 0), os.environ.get("MT5_PASSWORD", ""),
                            os.environ.get("MT5_SERVER", ""), os.environ.get("MT5_PATH", ""))
        conn.connect()
    except MT5Unavailable as exc:
        sys.exit(f"MT5 not available: {exc}")

    info = conn.mt5.account_info()
    is_demo = info.trade_mode == conn.mt5.ACCOUNT_TRADE_MODE_DEMO
    print(f"account {info.login} on {info.server} | {'DEMO' if is_demo else 'REAL'} | equity {info.equity:.2f}")

    if live:
        if not is_demo and not a.allow_real_account:
            sys.exit("Refusing: this is a REAL account. Test on demo first, or pass --allow-real-account.")
        banner = "REAL MONEY" if not is_demo else "the demo account"
        print(f"\n  Orders will be sent to {banner}. The AI proposes; the risk guard approves; "
              f"every trade has a stop at the broker.\n  Stop anytime: create the file {a.kill_switch!r}.\n")
        if input("  Type LIVE to continue: ").strip() != "LIVE":
            sys.exit("cancelled; nothing was sent")
        broker = MT5Broker(conn, a.symbol)
    else:
        broker = SimBroker(a.base_equity, spread=a.spread, slippage=a.slippage)

    advisor = build_advisor(a)
    log = Path(a.log_dir); log.mkdir(parents=True, exist_ok=True)
    engine = Engine(advisor, broker, build_guard(a),
                    EngineConfig(log_path=str(log / ("live" if live else "paper") + "_decisions.jsonl"),
                                 state_path=str(log / ("live" if live else "paper") + "_state.json")))
    print(f"running ({'LIVE' if live else 'paper'}), advisor={a.advisor}. Ctrl+C to stop.\n")

    last_bar = None
    try:
        while True:
            df = conn.get_rates(a.symbol, "M5", 2500)
            closed = df.iloc[:-1]                         # the newest row is still forming
            t = closed.index[-1]
            if t != last_bar:
                bar = int(t.timestamp() // 300)           # stable across restarts
                if last_bar is not None and not live:
                    r = closed.iloc[-1]
                    broker.on_bar(r.open, r.high, r.low, r.close, bar)
                last_bar = t
                rec = engine.step(closed, bar)
                if rec:
                    print(f"{t:%m-%d %H:%M} {rec['proposed']['action']:<9} -> {rec['executed']:<9} {rec['guard']}")
            time.sleep(a.poll_seconds)
    except KeyboardInterrupt:
        print("\nstopped. Open positions keep their broker-side stop and target.")
    finally:
        report(engine, advisor, broker, a.base_equity)
        conn.shutdown()


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("mode", choices=["replay", "paper", "live"])
    p.add_argument("--advisor", choices=["claude", "rules"], default="rules")
    p.add_argument("--model", default=DEFAULT_MODEL)
    p.add_argument("--effort", default="medium", choices=["low", "medium", "high", "xhigh", "max"])
    p.add_argument("--data", default="data/XAUUSD_M5_real.csv")
    p.add_argument("--start"); p.add_argument("--end")
    p.add_argument("--symbol", default="XAUUSD")
    p.add_argument("--base-equity", type=float, default=1000.0, help="risk is a %% of this fixed amount")
    p.add_argument("--risk-pct", type=float, default=1.0)
    p.add_argument("--max-trades-per-day", type=int, default=3)
    p.add_argument("--max-daily-loss-pct", type=float, default=3.0)
    p.add_argument("--spread", type=float, default=0.25)
    p.add_argument("--slippage", type=float, default=0.05)
    p.add_argument("--max-consults", type=int, default=150, help="hard cap on paid Claude calls in replay")
    p.add_argument("--poll-seconds", type=int, default=10)
    p.add_argument("--log-dir", default="logs/ai_trader")
    p.add_argument("--kill-switch", default="STOP_TRADING")
    p.add_argument("--allow-real-account", action="store_true")
    a = p.parse_args()

    if a.mode == "replay":
        cmd_replay(a)
    else:
        cmd_live(a, live=(a.mode == "live"))


if __name__ == "__main__":
    main()
