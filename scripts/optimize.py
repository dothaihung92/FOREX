#!/usr/bin/env python3
"""Grid-search strategy parameters on a train split, then validate the top
candidates on a held-out test split so results aren't just overfit to the
whole dataset.

Usage:
    python scripts/optimize.py --data data/XAUUSD_M5_real.csv --split 2023-08-01
"""
from __future__ import annotations

import argparse
import itertools
import sys
from dataclasses import replace
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pandas as pd

from gold_bot.backtester import run_backtest
from gold_bot.config import load_config
from gold_bot.strategy import generate_signals

STRATEGY_GRID = {
    "atr_sl_mult": [2.0],
    "atr_tp_mult": [2.5, 3.0, 3.5],
    "rsi_pullback_level": [45, 50, 55],
    "rsi_oversold": [25, 30],
}

RISK_GRID = {
    "trailing_atr_mult": [1.0, 1.2, 1.5],
}

MIN_TRADES = 40  # discard combos with too few trades to trust the stats


def rsi_overbought_for(oversold: float) -> float:
    return 100 - oversold


def load_ohlc_csv(path: str) -> pd.DataFrame:
    df = pd.read_csv(path)
    df["time"] = pd.to_datetime(df["time"], utc=True)
    return df.set_index("time").sort_index()[["open", "high", "low", "close"]]


def evaluate(df, strategy_cfg, risk_cfg, backtest_cfg):
    signals = generate_signals(df, strategy_cfg, cfg_sessions)
    result = run_backtest(signals, strategy_cfg, risk_cfg, backtest_cfg)
    return result.summary()


def main():
    global cfg_sessions
    parser = argparse.ArgumentParser()
    parser.add_argument("--data", required=True)
    parser.add_argument("--config", default="config/config.yaml")
    parser.add_argument("--split", required=True, help="ISO date splitting train/test, e.g. 2023-08-01")
    parser.add_argument("--top", type=int, default=8)
    parser.add_argument(
        "--rank-by",
        choices=["profit_factor", "win_rate_pct"],
        default="profit_factor",
        help="Metric to rank TRAIN results by before validating on TEST",
    )
    args = parser.parse_args()

    cfg = load_config(args.config)
    cfg_sessions = cfg.sessions
    df = load_ohlc_csv(args.data)
    split_ts = pd.Timestamp(args.split, tz="UTC")
    train, test = df[df.index < split_ts], df[df.index >= split_ts]
    print(f"Train: {len(train)} bars ({train.index.min()} -> {train.index.max()})")
    print(f"Test:  {len(test)} bars ({test.index.min()} -> {test.index.max()})")

    strat_keys = list(STRATEGY_GRID.keys())
    risk_keys = list(RISK_GRID.keys())
    combos = list(itertools.product(*STRATEGY_GRID.values(), *RISK_GRID.values()))
    print(f"Grid search: {len(combos)} combinations on train split (ranking by {args.rank_by})...\n")

    results = []
    for i, values in enumerate(combos, 1):
        strat_values, risk_values = values[: len(strat_keys)], values[len(strat_keys) :]
        strat_overrides = dict(zip(strat_keys, strat_values))
        risk_overrides = dict(zip(risk_keys, risk_values))
        strat_overrides["rsi_overbought"] = rsi_overbought_for(strat_overrides["rsi_oversold"])

        strategy_cfg = replace(cfg.strategy, **strat_overrides)
        risk_cfg = replace(cfg.risk, **risk_overrides)
        summary = evaluate(train, strategy_cfg, risk_cfg, cfg.backtest)
        summary["strat_params"] = strat_overrides
        summary["risk_params"] = risk_overrides
        results.append(summary)
        print(f"[{i}/{len(combos)}] {strat_overrides} {risk_overrides} -> trades={summary['trades']} "
              f"win%={summary['win_rate_pct']} pf={summary['profit_factor']} "
              f"ret={summary['total_return_pct']}% dd={summary['max_drawdown_pct']}%")

    valid = [r for r in results if r["trades"] >= MIN_TRADES and isinstance(r["profit_factor"], (int, float))]
    valid.sort(key=lambda r: r[args.rank_by], reverse=True)

    print(f"\nTop {args.top} on TRAIN (min {MIN_TRADES} trades), ranked by {args.rank_by}:")
    for r in valid[: args.top]:
        print(r["strat_params"], r["risk_params"], "->",
              {k: r[k] for k in ["trades", "win_rate_pct", "profit_factor", "total_return_pct", "max_drawdown_pct"]})

    print(f"\nValidating top {args.top} on held-out TEST split:")
    for r in valid[: args.top]:
        strategy_cfg = replace(cfg.strategy, **r["strat_params"])
        risk_cfg = replace(cfg.risk, **r["risk_params"])
        test_summary = evaluate(test, strategy_cfg, risk_cfg, cfg.backtest)
        print(r["strat_params"], r["risk_params"], f"TRAIN {args.rank_by}=", r[args.rank_by], "-> TEST", {
            k: test_summary[k] for k in ["trades", "win_rate_pct", "profit_factor", "total_return_pct", "max_drawdown_pct"]
        })


if __name__ == "__main__":
    main()
