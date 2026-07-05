#!/usr/bin/env python3
"""Run the live/paper MT5 trading loop.

Requires Windows + a running, logged-in MT5 terminal + `pip install
MetaTrader5`. Set credentials via config/config.yaml or the MT5_LOGIN /
MT5_PASSWORD / MT5_SERVER environment variables (preferred, keeps secrets
out of the repo).

Usage:
    python scripts/run_live.py --config config/config.yaml
"""
from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from gold_bot.config import load_config
from gold_bot.live_trader import LiveTrader


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", default="config/config.yaml")
    parser.add_argument("--poll-seconds", type=int, default=15)
    args = parser.parse_args()

    cfg = load_config(args.config)
    logging.basicConfig(
        level=getattr(logging, cfg.logging.get("level", "INFO")),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    trader = LiveTrader(cfg)
    trader.run_forever(poll_seconds=args.poll_seconds)


if __name__ == "__main__":
    main()
