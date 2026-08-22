"""Launch the market dashboard.

Safe by default. Two things must BOTH be true before the dashboard can
touch a real account:

  1. --broker mt5      connect to the live MT5/Exness terminal
  2. --live-trading    allow order placement (otherwise charts only)

With no flags it replays the repo's CSV data and simulates fills, which
runs on any OS and cannot cost money.

    # look around, no broker, no risk (works on Linux/macOS/Windows)
    python scripts/run_dashboard.py

    # real Exness charts and account, but read-only
    python scripts/run_dashboard.py --broker mt5

    # real account WITH order entry (Windows + running MT5 terminal)
    python scripts/run_dashboard.py --broker mt5 --live-trading

Credentials come from config/config.yaml or the MT5_LOGIN / MT5_PASSWORD /
MT5_SERVER environment variables - never from the command line, where they
would land in your shell history.
"""
from __future__ import annotations

import argparse
import logging
import os
import sys
import webbrowser
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from gold_bot.config import load_config
from gold_bot.dashboard.feeds import CsvFeed, MT5Feed
from gold_bot.dashboard.server import make_server

DEFAULT_SYMBOLS = ["XAUUSD", "EURUSD", "AUDUSD", "USDCHF", "GBPUSD", "USDJPY"]


def build_mt5_feed(cfg, symbols, allow_trading):
    from gold_bot.mt5_connector import MT5Connector, MT5Unavailable

    login = int(os.getenv("MT5_LOGIN") or cfg.raw.get("mt5", {}).get("login") or 0)
    password = os.getenv("MT5_PASSWORD") or cfg.raw.get("mt5", {}).get("password") or ""
    server = os.getenv("MT5_SERVER") or cfg.raw.get("mt5", {}).get("server") or ""
    path = cfg.raw.get("mt5", {}).get("terminal_path") or ""

    conn = MT5Connector(login=login, password=password, server=server, terminal_path=path)
    try:
        conn.connect()
    except MT5Unavailable as exc:
        raise SystemExit(
            f"Could not connect to MT5: {exc}\n"
            "The live backend needs Windows with a running, logged-in MT5 "
            "terminal. Run without --broker mt5 to use the CSV replay instead."
        ) from exc
    return MT5Feed(conn, symbols, allow_trading=allow_trading)


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--config", default="config/config.yaml")
    p.add_argument("--broker", choices=["csv", "mt5"], default="csv",
                   help="csv = offline replay (default), mt5 = live broker account")
    p.add_argument("--live-trading", action="store_true",
                   help="allow the dashboard to place and close orders")
    p.add_argument("--symbols", default=",".join(DEFAULT_SYMBOLS))
    p.add_argument("--port", type=int, default=8787)
    p.add_argument("--no-browser", action="store_true")
    args = p.parse_args()

    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    cfg = load_config(args.config)
    symbols = [s.strip().upper() for s in args.symbols.split(",") if s.strip()]

    if args.broker == "mt5":
        feed = build_mt5_feed(cfg, symbols, args.live_trading)
        mode = "LIVE TRADING" if args.live_trading else "live data, READ ONLY"
    else:
        feed = CsvFeed(start_balance=cfg.backtest.initial_balance)
        mode = "CSV replay, simulated fills"
        if args.live_trading:
            print("note: --live-trading has no effect with the csv backend; "
                  "fills stay simulated.")

    httpd = make_server(feed, cfg, host="127.0.0.1", port=args.port,
                        allow_trading=args.live_trading or args.broker == "csv")
    url = f"http://127.0.0.1:{args.port}/"

    print(f"\n  Gold Bot dashboard  ->  {url}")
    print(f"  backend: {args.broker}   mode: {mode}")
    if args.broker == "mt5" and args.live_trading:
        print("  *** ORDERS FROM THIS PAGE WILL HIT YOUR REAL ACCOUNT ***")
    print("  bound to localhost only; Ctrl+C to stop\n")

    if not args.no_browser:
        try:
            webbrowser.open(url)
        except Exception:                              # noqa: BLE001
            pass
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nshutting down")
    finally:
        httpd.server_close()
        feed.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
