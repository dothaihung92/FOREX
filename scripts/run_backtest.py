#!/usr/bin/env python3
"""Run a backtest of the gold M5 strategy over historical OHLC data.

Usage:
    python scripts/run_backtest.py --data data/XAUUSD_M5.csv --config config/config.yaml
    python scripts/run_backtest.py --data data/XAUUSD_M5.csv --plot equity.png

The CSV must have columns: time,open,high,low,close (time parseable as UTC).
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pandas as pd

from gold_bot.backtester import run_backtest, run_backtest_dca_grid
from gold_bot.config import load_config
from gold_bot.strategy import generate_signals


def load_ohlc_csv(path: str) -> pd.DataFrame:
    df = pd.read_csv(path)
    df["time"] = pd.to_datetime(df["time"], utc=True)
    df = df.set_index("time").sort_index()
    return df[["open", "high", "low", "close"]]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--data", required=True, help="Path to OHLC CSV file")
    parser.add_argument("--config", default="config/config.yaml")
    parser.add_argument("--plot", default=None, help="Optional path to save an equity curve PNG")
    args = parser.parse_args()

    cfg = load_config(args.config)
    df = load_ohlc_csv(args.data)
    signals = generate_signals(df, cfg.strategy, cfg.sessions)
    engine = run_backtest_dca_grid if cfg.risk.sizing_mode == "dca_grid" else run_backtest
    result = engine(signals, cfg.strategy, cfg.risk, cfg.backtest)

    summary = result.summary()
    print("Backtest summary")
    print("-----------------")
    for k, v in summary.items():
        print(f"{k:20s}: {v}")

    if args.plot:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt

        result.equity_curve.plot(title="Equity Curve")
        plt.xlabel("Time")
        plt.ylabel("Equity")
        plt.tight_layout()
        plt.savefig(args.plot)
        print(f"Saved equity curve to {args.plot}")


if __name__ == "__main__":
    main()
