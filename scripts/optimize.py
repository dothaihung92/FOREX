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

GRID = {
    "ema_slow": [100, 200],
    "rsi_pullback_level": [40, 45, 50],
    "atr_sl_mult": [1.0, 1.5, 2.0],
    "atr_tp_mult": [2.0, 3.0, 4.0],
}

MIN_TRADES = 40  # discard combos with too few trades to trust the stats


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
    args = parser.parse_args()

    cfg = load_config(args.config)
    cfg_sessions = cfg.sessions
    df = load_ohlc_csv(args.data)
    split_ts = pd.Timestamp(args.split, tz="UTC")
    train, test = df[df.index < split_ts], df[df.index >= split_ts]
    print(f"Train: {len(train)} bars ({train.index.min()} -> {train.index.max()})")
    print(f"Test:  {len(test)} bars ({test.index.min()} -> {test.index.max()})")

    keys = list(GRID.keys())
    combos = list(itertools.product(*GRID.values()))
    print(f"Grid search: {len(combos)} combinations on train split...\n")

    results = []
    for i, values in enumerate(combos, 1):
        overrides = dict(zip(keys, values))
        strategy_cfg = replace(cfg.strategy, **overrides)
        summary = evaluate(train, strategy_cfg, cfg.risk, cfg.backtest)
        summary["params"] = overrides
        results.append(summary)
        print(f"[{i}/{len(combos)}] {overrides} -> trades={summary['trades']} "
              f"pf={summary['profit_factor']} ret={summary['total_return_pct']}% "
              f"dd={summary['max_drawdown_pct']}%")

    valid = [r for r in results if r["trades"] >= MIN_TRADES and isinstance(r["profit_factor"], (int, float))]
    valid.sort(key=lambda r: r["profit_factor"], reverse=True)

    print(f"\nTop {args.top} on TRAIN (min {MIN_TRADES} trades), ranked by profit factor:")
    for r in valid[: args.top]:
        print(r["params"], "->", {k: r[k] for k in ["trades", "win_rate_pct", "profit_factor", "total_return_pct", "max_drawdown_pct"]})

    print(f"\nValidating top {args.top} on held-out TEST split:")
    for r in valid[: args.top]:
        strategy_cfg = replace(cfg.strategy, **r["params"])
        test_summary = evaluate(test, strategy_cfg, cfg.risk, cfg.backtest)
        print(r["params"], "TRAIN pf=", r["profit_factor"], "-> TEST", {
            k: test_summary[k] for k in ["trades", "win_rate_pct", "profit_factor", "total_return_pct", "max_drawdown_pct"]
        })


if __name__ == "__main__":
    main()
